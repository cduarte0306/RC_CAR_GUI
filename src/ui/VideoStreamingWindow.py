from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog, QLineEdit,
    QButtonGroup, QProgressDialog, QTabWidget, QFrame, QComboBox
)
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

    startStreamOut     = pyqtSignal(bool, str) # File selected signals
    viewModeChanged    = pyqtSignal(str)       # "regular" or "depth"
    uploadVideoClicked = pyqtSignal(str)       # File selected signal
    
    def __init__(self, parent=None):
        super().__init__(parent)

        # Persist user-selected file path across app restarts
        self.__settings = QSettings("RC_CAR_GUI", "VideoStreaming")

        # Ensure the viewport has space before first frame arrives
        self.setMinimumSize(640, 480)

        root = QVBoxLayout()
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        self.setLayout(root)

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
        root.addLayout(header)

        # Tabs
        self.__tabs = QTabWidget()
        self.__tabs.setTabBarAutoHide(False)
        self.__tabs.setStyleSheet(
            """
            QTabWidget::pane {
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
                background: #0b111c;
            }
            QTabBar::tab {
                background: rgba(255,255,255,0.04);
                color: #e8ecf3;
                padding: 8px 16px;
                border: 1px solid rgba(255,255,255,0.08);
                border-bottom-color: rgba(255,255,255,0.04);
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: rgba(0,210,255,0.18);
                color: #0fd3ff;
                border: 1px solid rgba(0,210,255,0.55);
                border-bottom-color: #0b111c;
            }
            QTabBar::tab:hover {
                background: rgba(0,210,255,0.12);
                color: #e8ecf3;
            }
            QTabBar::tab:!selected {
                margin-top: 4px;
            }
            """
        )
        root.addWidget(self.__tabs)

        # Viewer tab
        viewerTab = QWidget()
        viewerLayout = QVBoxLayout()
        viewerLayout.setContentsMargins(0, 0, 0, 0)
        viewerLayout.setSpacing(10)
        viewerTab.setLayout(viewerLayout)

        self.__videoLabel = QLabel()
        self.__videoLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.__videoLabel.setStyleSheet(
            "background-color: #05080d; border: 1px solid rgba(255,255,255,0.08); border-radius: 14px;"
        )

        # FIX: initialize with non-null pixmap
        placeholder = QPixmap(1, 1)
        placeholder.fill(Qt.GlobalColor.black)
        self.__videoLabel.setPixmap(placeholder)

        viewerLayout.addWidget(self.__videoLabel)
        self.__tabs.addTab(viewerTab, "Viewer")

        # Controls tab
        controlsTab = QWidget()
        controlsLayout = QVBoxLayout()
        controlsLayout.setContentsMargins(8, 8, 8, 8)
        controlsLayout.setSpacing(12)
        controlsTab.setLayout(controlsLayout)

        # Mode buttons (Regular/Depth)
        modeCard = QFrame()
        modeCard.setStyleSheet("border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; background: rgba(255,255,255,0.03);")
        modeCardLayout = QVBoxLayout(modeCard)
        modeCardLayout.setContentsMargins(12, 12, 12, 12)
        modeCardLayout.setSpacing(8)
        modeTitle = QLabel("View Mode")
        modeTitle.setObjectName("card-title")
        modeCardLayout.addWidget(modeTitle)

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
        modeRow.addStretch()
        modeCardLayout.addLayout(modeRow)
        controlsLayout.addWidget(modeCard)

        # FPS control
        fpsCard = QFrame()
        fpsCard.setStyleSheet("border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; background: rgba(255,255,255,0.03);")
        fpsLayout = QHBoxLayout(fpsCard)
        fpsLayout.setContentsMargins(12, 12, 12, 12)
        fpsLayout.setSpacing(10)
        fpsLabel = QLabel("FPS")
        fpsLabel.setObjectName("card-title")
        fpsLabel.setMaximumWidth(60)
        fpsLabel.setMaximumHeight(32)

        self.__fpsSel = QComboBox()
        self.__fpsSel.addItems(["5", "10", "15", "20", "25", "30", "45", "60"])
        self.__fpsSel.setCurrentText("30")
        self.__fpsSel.setStyleSheet(
            """
            QComboBox {
                background-color: rgba(255,255,255,0.1);
                color: #e8ecf3;
                border: 1px solid rgba(255,255,255,0.18);
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
                padding-right: 28px; /* leave room for arrow */
            }
            QComboBox:hover {
                background-color: rgba(0,210,255,0.16);
                border: 1px solid rgba(0,210,255,0.35);
            }
            QComboBox::drop-down {
                border: none;
                width: 26px;
                subcontrol-origin: padding;
                subcontrol-position: top right;
            }
            """
        )
        
        self.__fpsSel.setFixedWidth(80)
        
        self.__startIcon = QIcon("icons/play-icon.svg")
        self.__pauseIcon = QIcon("icons/pause-icon.svg")
        if self.__pauseIcon.isNull():
            self.__pauseIcon = QIcon("icons/red-square-shape-icon.svg")
        
        self.__startStreamOutBtn = IconButton(self.__startIcon, icon_size=QSize(16, 16))
        self.__startStreamOutBtn.setFixedSize(40, 40)
        self.__startStreamOutBtn.setToolTip("Start outbound stream")

        fpsLayout.addWidget(fpsLabel)
        fpsLayout.addWidget(self.__fpsSel)
        fpsLayout.addWidget(self.__startStreamOutBtn)
        fpsLayout.setAlignment(self.__startStreamOutBtn, Qt.AlignmentFlag.AlignLeft)
        fpsLayout.setAlignment(fpsLabel, Qt.AlignmentFlag.AlignLeft)
        fpsLayout.addStretch()
        fpsLayout.setAlignment(self.__fpsSel, Qt.AlignmentFlag.AlignLeft)
        controlsLayout.addWidget(fpsCard)

        # Upload controls
        self.__uploadVideo       = QPushButton("Upload Video")
        self.__fileLineEdit      = QLineEdit()
        self.__fileLineEdit.setPlaceholderText("Select a .mov file to stream out")
        last_path = self.__settings.value("lastFilePath", "", str)
        if last_path:
            self.__fileLineEdit.setText(last_path)
        self.__browseButton      = IconButton(QIcon("icons/file-manager-icon.svg"), icon_size=QSize(32, 32))

        self.__fileLineEdit.setMinimumWidth(360)
        self.__fileLineEdit.setMinimumHeight(32)
        self.__fileLineEdit.setStyleSheet("padding: 6px 10px; font-size: 13px;")
        self.__browseButton.setIconSize(QSize(32, 32))
        self.__browseButton.setStyleSheet(
            """
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
            """
        )

        uploadRow = QHBoxLayout()
        uploadRow.setSpacing(8)
        uploadRow.addWidget(self.__fileLineEdit, stretch=1)
        uploadRow.addWidget(self.__browseButton)
        controlsLayout.addLayout(uploadRow)

        self.__uploadVideo.setFixedHeight(36)
        self.__uploadVideo.setStyleSheet(
            """
            QPushButton {
                background-color: rgba(0,210,255,0.16);
                color: #e8ecf3;
                border: 1px solid rgba(0,210,255,0.35);
                border-radius: 10px;
                padding: 8px 14px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(0,210,255,0.24);
            }
            """
        )
        controlsLayout.addWidget(self.__uploadVideo)
        # controlsLayout.addWidget(self.__startStreamOutBtn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.__tabs.addTab(controlsTab, "Stream Controls")
        
        # Icon playig flag
        self.__isPlaying = False
        self.__uploadProgress = None

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
        self.__uploadVideo.clicked.connect(self.__uploadVideoClicked)
        
        # Default view mode
        self.__setViewMode(self.__viewMode, emit_signal=False)
    
    
    def __uploadVideoClicked(self) -> None:
        text = self.__fileLineEdit.text()
        if text == "" or not os.path.isfile(text) or not text.lower().endswith(".mov"):
            logging.info("No valid .MOV file selected for upload")
            return

        self.__uploadVideo.setEnabled(False)
        self.__settings.setValue("lastFilePath", text)
        logging.info(f"Uploading video file: {text}")
        self.__showUploadProgress()
        self.uploadVideoClicked.emit(text)
        # TODO: wire real upload and call updateUploadProgress()/finishUploadProgress()
        
        
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


    def updateStereoFrame(self, left_frame: np.ndarray, right_frame: np.ndarray):
        """Render stereo frames side-by-side."""
        if left_frame is None and right_frame is None:
            return
        if right_frame is None:
            # Already stitched; render as-is
            self.updateFrame(left_frame)
            return

        if left_frame.shape[0] != right_frame.shape[0]:
            logging.warning("Stereo frames have mismatched heights; skipping render")
            return

        try:
            stacked = np.hstack((left_frame, right_frame))
        except Exception as exc:
            logging.warning("Failed to stack stereo frames: %s", exc)
            return

        self.updateFrame(stacked)


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


    def __showUploadProgress(self):
        if self.__uploadProgress is None:
            dlg = QProgressDialog("Uploading video...", None, 0, 0, self)
            dlg.setWindowTitle("Upload")
            dlg.setCancelButton(None)
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
            dlg.setMinimumWidth(360)
            dlg.setMinimumDuration(0)
            dlg.setStyleSheet(
                """
                QProgressDialog {
                    background-color: #0f1624;
                    color: #e8ecf3;
                    border: 1px solid rgba(0,210,255,0.35);
                    border-radius: 10px;
                }
                QProgressDialog QLabel {
                    color: #e8ecf3;
                    font-weight: 600;
                }
                QProgressBar {
                    background-color: rgba(255,255,255,0.08);
                    border: 1px solid rgba(255,255,255,0.18);
                    border-radius: 8px;
                    text-align: center;
                    color: #e8ecf3;
                    padding: 3px;
                }
                QProgressBar::chunk {
                    background-color: #00d2ff;
                    border-radius: 8px;
                }
                """
            )
            dlg.show()
            self.__uploadProgress = dlg
        else:
            self.__uploadProgress.setLabelText("Uploading video...")
            self.__uploadProgress.setRange(0, 0)
            self.__uploadProgress.show()


    def updateUploadProgress(self, percent: int) -> None:
        """Call this from backend when bytes uploaded are known."""
        if self.__uploadProgress is None:
            return
        clamped = max(0, min(100, int(percent)))
        self.__uploadProgress.setRange(0, 100)
        self.__uploadProgress.setValue(clamped)


    def finishUploadProgress(self, success: bool = True) -> None:
        self.__uploadVideo.setEnabled(True)
        if self.__uploadProgress is None:
            return
        self.__uploadProgress.reset()
        self.__uploadProgress = None

