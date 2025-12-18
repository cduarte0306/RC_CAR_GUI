from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog, QLineEdit, QButtonGroup
from PyQt6.QtGui import QImage, QPixmap, QColor, QPainter, QFont, QIcon
from PyQt6.QtCore import Qt, QMutex, QElapsedTimer, pyqtSignal, QSize, QSettings
import numpy as np
import os

os.environ["QT_LOGGING_RULES"] = "*.debug=false; *.warning=false"

import logging


class IconButton(QPushButton):
    """QPushButton that swaps icons on hover/press and slightly shrinks on press.

    Usage: IconButton(normal_icon, hover_icon=None, pressed_icon=None, icon_size=QSize(24,24))
    If hover/pressed icons are not provided the normal icon is reused.
    """
    def __init__(self, normal: QIcon, hover: QIcon | None = None, pressed: QIcon | None = None,
                 icon_size: QSize = QSize(24, 24), parent=None):
        super().__init__(parent)
        self._icon_normal = normal
        self._icon_hover = hover or normal
        self._icon_pressed = pressed or self._icon_hover
        self._base_icon_size = icon_size

        self.setIcon(self._icon_normal)
        self.setIconSize(self._base_icon_size)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, ev):
        self.setIcon(self._icon_hover)
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self.setIcon(self._icon_normal)
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        # set pressed icon and slightly reduce icon size for tactile feedback
        self.setIcon(self._icon_pressed)
        smaller = QSize(max(1, int(self._base_icon_size.width() * 0.9)),
                        max(1, int(self._base_icon_size.height() * 0.9)))
        self.setIconSize(smaller)
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        # restore hover/normal depending on cursor position
        if self.underMouse():
            self.setIcon(self._icon_hover)
        else:
            self.setIcon(self._icon_normal)
        self.setIconSize(self._base_icon_size)
        super().mouseReleaseEvent(ev)


    def setAllIcons(self, icon: QIcon) -> None:
        """Update normal/hover/pressed icons together so external toggles persist."""
        self._icon_normal = icon
        self._icon_hover = icon
        self._icon_pressed = icon
        self.setIcon(icon)
        self.setIconSize(self._base_icon_size)


class VideoStreamingWindow(QWidget):

    startStreamOut = pyqtSignal(bool, str)  # File selected signals
    viewModeChanged = pyqtSignal(str)       # "regular" or "depth"
    
    def __init__(self, parent=None):
        super().__init__(parent)

        # Persist user-selected file path across app restarts
        self.__settings = QSettings("RC_CAR_GUI", "VideoStreaming")

        # Ensure the viewport has space before first frame arrives
        self.setMinimumSize(640, 480)

        self.setLayout(QVBoxLayout())
        videoOutcontrolerLayout = QHBoxLayout()
        self.layout().setContentsMargins(18, 18, 18, 18)
        self.layout().setSpacing(12)

        # --- Video display area ---
        header = QHBoxLayout()
        title = QLabel("Video Stream")
        title.setObjectName("card-title")
        header.addWidget(title)
        header.addStretch()

        self.__viewMode = "regular"
        self.__modeButtons: dict[str, QPushButton] = {}
        self.__modeGroup = QButtonGroup(self)
        self.__modeGroup.setExclusive(True)

        modeRow = QHBoxLayout()
        modeRow.setSpacing(6)
        for mode, label in (("regular", "Regular"), ("depth", "Depth")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                """
                QPushButton {
                    background-color: rgba(255,255,255,0.06);
                    color: #e8ecf3;
                    border: 1px solid rgba(255,255,255,0.12);
                    border-radius: 10px;
                    padding: 8px 14px;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background-color: rgba(0,210,255,0.14);
                    border: 1px solid rgba(0,210,255,0.32);
                }
                QPushButton:checked {
                    background-color: rgba(0,210,255,0.18);
                    border: 1px solid rgba(0,210,255,0.65);
                    color: #0fd3ff;
                }
                """
            )
            btn.clicked.connect(lambda checked, m=mode: self.__setViewMode(m))
            self.__modeGroup.addButton(btn)
            self.__modeButtons[mode] = btn
            modeRow.addWidget(btn)

        self.__modeButtons[self.__viewMode].setChecked(True)
        header.addLayout(modeRow)
        self.layout().addLayout(header)

        self.__videoLabel = QLabel()
        self.__videoLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.__videoLabel.setStyleSheet(
            "background-color: #05080d; border: 1px solid rgba(255,255,255,0.08); border-radius: 14px;"
        )

        # FIX: initialize with non-null pixmap
        placeholder = QPixmap(1, 1)
        placeholder.fill(Qt.GlobalColor.black)
        self.__videoLabel.setPixmap(placeholder)

        self.layout().addWidget(self.__videoLabel)
        self.layout().addLayout(videoOutcontrolerLayout)

        # Put FPS overlay in top-left corner
        self.__fileLineEdit      = QLineEdit()
        self.__fileLineEdit.setPlaceholderText("Select a .mov file to stream out")
        last_path = self.__settings.value("lastFilePath", "", str)
        if last_path:
            self.__fileLineEdit.setText(last_path)
        # Use IconButton for hover/press feedback
        self.__browseButton      = IconButton(QIcon("icons/file-manager-icon.svg"),
                                              icon_size=QSize(40, 40))

        self.__startIcon = QIcon("icons/play-icon.svg")
        self.__pauseIcon = QIcon("icons/pause-icon.svg")
        # Fallback if pause asset is missing
        if self.__pauseIcon.isNull():
            self.__pauseIcon = QIcon("icons/red-square-shape-icon.svg")

        # create play/stop IconButton (pressed/hover icons not provided -> fall back to normal)
        self.__startStreamOutBtn = IconButton(self.__startIcon, icon_size=QSize(40, 40))
        self.__startStreamOutBtn.setToolTip("Start outbound stream")

        self.__fileLineEdit.setMinimumWidth(360)
        self.__fileLineEdit.setMinimumHeight(32)
        self.__fileLineEdit.setStyleSheet("padding: 6px 10px; font-size: 13px;")
        self.__browseButton.setIconSize(QSize(50, 50))
        # Keep the same stylesheet for background hover/pressed visuals
        self.__browseButton.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,255,255,0.1);
                color: white;
                font-size: 15px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.25);
                padding: 8px 15px; 
            }
            QPushButton:pressed {
                background-color: rgba(255,255,255,0.35);
            }
        """)

        overlay = QHBoxLayout()
        # overlay.addWidget(self.__fileLineEdit, alignment=Qt.AlignmentFlag.AlignLeft)
        overlay.addStretch()
        overlay.addWidget(self.__fileLineEdit, stretch=1)
        overlay.addWidget(self.__browseButton)
        self.layout().addLayout(overlay)
        
        self.layout().addWidget(self.__startStreamOutBtn, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # Icon playig flag
        self.__isPlaying = False

        # FPS tracking
        self.__fpsSmooth = 0.0
        self.__mutex = QMutex()
        
        self.left = 100
        self.top = 100
        self.fwidth = 640
        self.fheight = 480
        self.padding = 10

        # Connect signals
        self.__browseButton.clicked.connect(self.__openFileDialog)
        self.__startStreamOutBtn.clicked.connect(self.__startStreamOut)
        # Default view mode
        self.__setViewMode(self.__viewMode, emit_signal=False)
        
        
    def __startStreamOut(self) -> None:
        text = self.__fileLineEdit.text()
        if text == "" or not os.path.isfile(text) or not text.lower().endswith(".mov"):
            logging.info("No valid .MOV file selected")
            return

        self.__settings.setValue("lastFilePath", text)

        if not self.__isPlaying:
            self.__startStreamOutBtn.setAllIcons(self.__pauseIcon)
            self.__startStreamOutBtn.setToolTip("Pause outbound stream")
            self.__isPlaying = True
        else:
            self.__startStreamOutBtn.setAllIcons(self.__startIcon)
            self.__startStreamOutBtn.setToolTip("Start outbound stream")
            self.__isPlaying = False
        self.startStreamOut.emit(self.__isPlaying, self.__fileLineEdit.text())


    def __openFileDialog(self) -> None:
        dialog = QFileDialog(self)
        dialog.setDirectory(r'C:')
        dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        dialog.setNameFilter("Images (*.MOV)")
        
        # Get the file name
        if dialog.exec():
            filenames = dialog.selectedFiles()
            if len(filenames) > 1:
                logging.info("Incorrect number of files selected")
                return

            self.__fileLineEdit.setText(filenames[0])
            self.__settings.setValue("lastFilePath", filenames[0])


    # ------------------------------------------------------------------
    # Call this when a new frame arrives (numpy array BGR)
    # ------------------------------------------------------------------
    def updateFrame(self, frame: np.ndarray):
        if frame is None:
            return

        rgbImage = frame   # Already RGB

        self.__mutex.lock()
        try:
            # ------------------------------------------------------
            # Guard size but still render first frame
            # ------------------------------------------------------
            imWidth  = max(1, (self.width()  - 2*self.padding) if self.width()  > 1 else self.fwidth)
            imHeight = max(1, (self.height() - 2*self.padding) if self.height() > 1 else self.fheight)

            # Convert ndarray → QImage
            h, w, ch = rgbImage.shape
            bytesPerLine = ch * w

            qImg = QImage(rgbImage.data, w, h, bytesPerLine, QImage.Format.Format_RGB888)
            if qImg.isNull():
                logging.info("QImage is null")
                return

            # Step 1 – scale to base size
            baseScaled = qImg.scaled(
                self.fwidth,
                self.fheight,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )

            # Step 2 – scale to window (or fallback size)
            finalScaled = baseScaled.scaled(
                imWidth,
                imHeight,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )

            pixmap = QPixmap.fromImage(finalScaled)
            if not pixmap.isNull():
                self.__videoLabel.setPixmap(pixmap)
        finally:
            self.__mutex.unlock()


    def __setViewMode(self, mode: str, emit_signal: bool = True) -> None:
        if mode == self.__viewMode:
            return
        if mode not in self.__modeButtons:
            return
        self.__viewMode = mode
        for key, btn in self.__modeButtons.items():
            btn.setChecked(key == mode)
        if emit_signal:
            self.viewModeChanged.emit(mode)

