from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog, QLineEdit,
    QButtonGroup, QProgressDialog, QTabWidget, QFrame, QComboBox, QDialog, QTabBar, QMessageBox,
    QApplication, QListWidget, QListWidgetItem
)
from PyQt6.QtGui import QImage, QPixmap, QColor, QPainter, QFont, QIcon, QDrag, QCursor
from PyQt6.QtCore import Qt, QMutex, QElapsedTimer, pyqtSignal, QSize, QSettings, QPoint, QMimeData
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


class TearOffTabBar(QTabBar):
    tearOffRequested = pyqtSignal(int, QPoint)
    dockRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_start = None
        self._drag_index = -1
        self.setAcceptDrops(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
            self._drag_index = self.tabAt(self._drag_start)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_start is not None
            and self._drag_index != -1
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            if self.tabData(self._drag_index) != "tearoff":
                super().mouseMoveEvent(event)
                return
            distance = (event.pos() - self._drag_start).manhattanLength()
            if distance >= QApplication.startDragDistance():
                drag = QDrag(self)
                mime = QMimeData()
                mime.setData("application/x-rc-controls-tab", b"controls")
                drag.setMimeData(mime)

                tab_rect = self.tabRect(self._drag_index)
                pix = self.grab(tab_rect)
                if not pix.isNull():
                    drag.setPixmap(pix)
                    drag.setHotSpot(event.pos() - tab_rect.topLeft())

                result = drag.exec(Qt.DropAction.MoveAction)
                if result == Qt.DropAction.IgnoreAction:
                    self.tearOffRequested.emit(self._drag_index, QCursor.pos())
                    if self.window():
                        self.window().activateWindow()
                        self.window().raise_()
                self._drag_start = None
                self._drag_index = -1
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        self._drag_index = -1
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-rc-controls-tab"):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-rc-controls-tab"):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-rc-controls-tab"):
            event.acceptProposedAction()
            self.dockRequested.emit()
            return
        super().dropEvent(event)


class DockHandle(QFrame):
    """Draggable tab handle to dock the torn-off controls back into the tab bar."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._label = QLabel(label, self)
        self._label.setObjectName("dock-handle-title")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drag_start: QPoint | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.addWidget(self._label)

        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet(
            """
            QFrame {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
            }
            QLabel#dock-handle-title {
                color: #e8ecf3;
                font-size: 12px;
                font-weight: 600;
            }
            """
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_start is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            distance = (event.pos() - self._drag_start).manhattanLength()
            if distance >= QApplication.startDragDistance():
                drag = QDrag(self)
                mime = QMimeData()
                mime.setData("application/x-rc-controls-tab", b"controls")
                drag.setMimeData(mime)
                drag.setHotSpot(event.pos())

                pix = self.grab()
                if not pix.isNull():
                    drag.setPixmap(pix)
                drag.exec(Qt.DropAction.MoveAction)
                self._drag_start = None
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)


class VideoStreamingWindow(QWidget):

    startStreamOut        = pyqtSignal(bool, str) # File selected signals
    streamModeChanged     = pyqtSignal(str)       # "stereo_pairs", "stereo_mono", "simulation"
    stereoMonoModeChanged = pyqtSignal(str)       # "normal", "disparity"
    uploadVideoClicked    = pyqtSignal(str)       # File selected signal
    setNormalMode         = pyqtSignal()
    setDisparityMode      = pyqtSignal()
    saveVideoOnDevice     = pyqtSignal(str)       # Signal to save video on device
    
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

        self.__streamMode = "stereo_pairs"
        self.__streamModeButtons: dict[str, QPushButton] = {}
        self.__streamModeGroup = QButtonGroup(self)
        self.__streamModeGroup.setExclusive(True)
        self.__stereoMonoMode = "normal"
        self.__stereoMonoButtons: dict[str, QPushButton] = {}
        self.__stereoMonoGroup = QButtonGroup(self)
        self.__stereoMonoGroup.setExclusive(True)
        root.addLayout(header)

        # Tabs
        self.__tabs = QTabWidget()
        self.__tabBar = TearOffTabBar(self.__tabs)
        self.__tabBar.tearOffRequested.connect(self.__onTearOffRequested)
        self.__tabBar.dockRequested.connect(self.__dockControls)
        self.__tabs.setTabBar(self.__tabBar)
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
        self.__controlsTab = controlsTab
        self.__controlsTabLabel = "Stream Controls"
        self.__controlsPopout: QDialog | None = None
        self.__controlsTabIndex: int | None = None
        controlsLayout = QVBoxLayout()
        controlsLayout.setContentsMargins(8, 8, 8, 8)
        controlsLayout.setSpacing(12)
        controlsTab.setLayout(controlsLayout)

        # Stream mode buttons (Stereo Pairs/Stereo-Mono/Simulation)
        streamCard = QFrame()
        streamCard.setStyleSheet("border: none; border-radius: 10px; background: rgba(255,255,255,0.03);")
        streamCardLayout = QVBoxLayout(streamCard)
        streamCardLayout.setContentsMargins(12, 12, 12, 12)
        streamCardLayout.setSpacing(8)
        streamTitle = QLabel("Stream Mode")
        streamTitle.setObjectName("card-title")
        streamCardLayout.addWidget(streamTitle)

        streamRow = QHBoxLayout()
        streamRow.setSpacing(6)
        for mode, label in (
            ("stereo_pairs", "Stereo Pairs"),
            ("stereo_mono", "Stereo-Mono"),
            ("simulation", "Simulation"),
        ):
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
            btn.clicked.connect(lambda checked, m=mode: self.__setStreamMode(m))
            self.__streamModeGroup.addButton(btn)
            self.__streamModeButtons[mode] = btn
            streamRow.addWidget(btn)
        self.__streamModeButtons[self.__streamMode].setChecked(True)
        streamRow.addStretch()
        streamCardLayout.addLayout(streamRow)
        controlsLayout.addWidget(streamCard)

        # Stereo-mono sub-modes
        stereoMonoCard = QFrame()
        stereoMonoCard.setStyleSheet("border: none; border-radius: 10px; background: rgba(255,255,255,0.03);")
        stereoMonoCardLayout = QVBoxLayout(stereoMonoCard)
        stereoMonoCardLayout.setContentsMargins(12, 12, 12, 12)
        stereoMonoCardLayout.setSpacing(8)
        stereoMonoTitle = QLabel("Stereo-Mono Mode")
        stereoMonoTitle.setObjectName("card-title")
        stereoMonoCardLayout.addWidget(stereoMonoTitle)

        stereoMonoRow = QHBoxLayout()
        stereoMonoRow.setSpacing(6)
        for mode, label in (
            ("normal", "Normal"),
            ("disparity", "Disparity"),
        ):
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
            btn.clicked.connect(lambda checked, m=mode: self.__setStereoMonoMode(m))
            self.__stereoMonoGroup.addButton(btn)
            self.__stereoMonoButtons[mode] = btn
            stereoMonoRow.addWidget(btn)
        self.__stereoMonoButtons[self.__stereoMonoMode].setChecked(True)
        stereoMonoRow.addStretch()
        stereoMonoCardLayout.addLayout(stereoMonoRow)
        controlsLayout.addWidget(stereoMonoCard)
        self.__stereoMonoCard = stereoMonoCard
        self.__stereoMonoCard.setVisible(False)

        # Simulation library panel (device videos + local upload controls)
        simulationCard = QFrame()
        simulationCard.setStyleSheet("border: none; border-radius: 10px; background: rgba(255,255,255,0.03);")
        simulationLayout = QVBoxLayout(simulationCard)
        simulationLayout.setContentsMargins(12, 12, 12, 12)
        simulationLayout.setSpacing(10)
        simulationTitle = QLabel("Simulation Library")
        simulationTitle.setObjectName("card-title")
        simulationLayout.addWidget(simulationTitle)

        simulationRow = QHBoxLayout()
        simulationRow.setSpacing(10)
        simulationLayout.addLayout(simulationRow)

        deviceCard = QFrame()
        deviceCard.setStyleSheet(
            "border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; background: rgba(255,255,255,0.02);"
        )
        deviceLayout = QVBoxLayout(deviceCard)
        deviceLayout.setContentsMargins(10, 10, 10, 10)
        deviceLayout.setSpacing(8)
        deviceTitle = QLabel("Device Videos")
        deviceTitle.setObjectName("card-title")
        deviceLayout.addWidget(deviceTitle)

        self.__deviceVideoList = QListWidget()
        self.__deviceVideoList.setMinimumHeight(160)
        self.__deviceVideoList.setStyleSheet(
            """
            QListWidget {
                background-color: rgba(5, 8, 13, 0.8);
                color: #e8ecf3;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                padding: 6px;
            }
            QListWidget::item {
                padding: 6px;
            }
            QListWidget::item:selected {
                background-color: rgba(0,210,255,0.18);
                color: #0fd3ff;
            }
            """
        )
        self.updateDeviceVideoList([])
        deviceLayout.addWidget(self.__deviceVideoList)
        simulationRow.addWidget(deviceCard, stretch=1)

        localCard = QFrame()
        localCard.setStyleSheet(
            "border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; background: rgba(255,255,255,0.02);"
        )
        localLayout = QVBoxLayout(localCard)
        localLayout.setContentsMargins(10, 10, 10, 10)
        localLayout.setSpacing(8)
        localTitle = QLabel("Local Upload")
        localTitle.setObjectName("card-title")
        localLayout.addWidget(localTitle)

        # Upload controls
        self.__uploadVideo       = QPushButton("Upload Video")
        self.__saveIcon          = QIcon("icons/save-icon.svg")
        if self.__saveIcon.isNull():
            self.__saveIcon = QIcon("icons/file-manager-icon.svg")
        self.__saveSnapshotBtn   = IconButton(self.__saveIcon, icon_size=QSize(18, 18))
        self.__fileLineEdit      = QLineEdit()
        self.__fileLineEdit.setPlaceholderText("Select a .mov file to stream out")
        last_path = self.__settings.value("lastFilePath", "", str)
        if last_path:
            self.__fileLineEdit.setText(last_path)
        self.__browseButton      = IconButton(QIcon("icons/file-manager-icon.svg"), icon_size=QSize(32, 32))

        self.__fileLineEdit.setMinimumWidth(240)
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
        localLayout.addLayout(uploadRow)

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
        self.__saveSnapshotBtn.setFixedSize(36, 36)
        self.__saveSnapshotBtn.setToolTip("Write to device")
        self.__saveSnapshotBtn.setStyleSheet(
            """
            QPushButton {
                background-color: rgba(255,255,255,0.06);
                color: #e8ecf3;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 10px;
            }
            QPushButton:hover {
                background-color: rgba(0,210,255,0.16);
                border: 1px solid rgba(0,210,255,0.35);
            }
            """
        )

        uploadButtons = QHBoxLayout()
        uploadButtons.setSpacing(8)
        uploadButtons.addWidget(self.__uploadVideo)
        uploadButtons.addWidget(self.__saveSnapshotBtn)
        uploadButtons.addStretch()
        localLayout.addLayout(uploadButtons)

        simulationRow.addWidget(localCard, stretch=1)

        self.__simulationCard = simulationCard
        self.__simulationCard.setVisible(False)
        controlsLayout.addWidget(simulationCard)

        # FPS control
        fpsCard = QFrame()
        fpsCard.setStyleSheet("border: none; border-radius: 10px; background: rgba(255,255,255,0.03);")
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

        self.__tabs.addTab(self.__controlsTab, self.__controlsTabLabel)
        self.__controlsTabIndex = self.__tabs.indexOf(self.__controlsTab)
        if self.__controlsTabIndex != -1:
            self.__tabBar.setTabData(self.__controlsTabIndex, "tearoff")
        
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
        self.__saveSnapshotBtn.clicked.connect(self.__saveVideoOnDevice)
        
        # Default stream mode
        self.__setStreamMode(self.__streamMode, emit_signal=False)
        self.__setStereoMonoMode(self.__stereoMonoMode, emit_signal=False)


    def __onTearOffRequested(self, index: int, global_pos: QPoint) -> None:
        if self.__controlsPopout is not None:
            return
        if self.__tabs.widget(index) is not self.__controlsTab:
            return
        self.__tearOffControls(global_pos)


    def __tearOffControls(self, global_pos: QPoint | None = None) -> None:
        if self.__controlsPopout is not None:
            self.__controlsPopout.raise_()
            self.__controlsPopout.activateWindow()
            return

        if self.__controlsTabIndex is None:
            self.__controlsTabIndex = self.__tabs.indexOf(self.__controlsTab)
        if self.__controlsTabIndex != -1:
            self.__tabs.removeTab(self.__controlsTabIndex)

        self.__controlsPopout = QDialog(self)
        self.__controlsPopout.setWindowTitle(self.__controlsTabLabel)
        self.__controlsPopout.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.__controlsPopout.setMinimumSize(520, 360)
        self.__controlsPopout.setStyleSheet(
            """
            QDialog {
                background-color: #0b111c;
                color: #e8ecf3;
            }
            """
        )
        if global_pos is not None:
            self.__controlsPopout.move(global_pos - QPoint(80, 20))

        popoutLayout = QVBoxLayout(self.__controlsPopout)
        popoutLayout.setContentsMargins(12, 12, 12, 12)
        popoutLayout.setSpacing(10)

        handle = DockHandle(self.__controlsTabLabel, self.__controlsPopout)
        popoutLayout.addWidget(handle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.__controlsTab.setParent(self.__controlsPopout)
        popoutLayout.addWidget(self.__controlsTab)
        self.__controlsTab.show()
        self.__controlsPopout.finished.connect(lambda _=None: self.__dockControls())
        self.__controlsPopout.show()


    def __dockControls(self) -> None:
        if self.__controlsPopout is None:
            return

        popout = self.__controlsPopout
        self.__controlsPopout = None

        layout = popout.layout()
        if layout is not None:
            layout.removeWidget(self.__controlsTab)
        self.__controlsTab.setParent(None)

        if popout.isVisible():
            popout.close()

        insert_at = self.__controlsTabIndex if self.__controlsTabIndex is not None else self.__tabs.count()
        if self.__tabs.indexOf(self.__controlsTab) == -1:
            self.__tabs.insertTab(insert_at, self.__controlsTab, self.__controlsTabLabel)
        new_index = self.__tabs.indexOf(self.__controlsTab)
        if new_index != -1:
            self.__tabBar.setTabData(new_index, "tearoff")
        self.__tabs.setCurrentWidget(self.__controlsTab)
    
    
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


    def __saveVideoOnDevice(self) -> None:
        """Save the current frame as an image file."""
        # Extract the name from the path (name only)
        videoName : str = os.path.basename(self.__fileLineEdit.text())
        logging.info(f"Saving video on device: {videoName}")
        self.saveVideoOnDevice.emit(videoName)
    

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


    def showErrorMessage(self, message: str) -> None:
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Error")
        dlg.setIcon(QMessageBox.Icon.Critical)
        dlg.setText(message)
        dlg.setStyleSheet(
            """
            QMessageBox {
                background-color: #0b111c;
                color: #e8ecf3;
            }
            QMessageBox QLabel {
                color: #e8ecf3;
            }
            """
        )
        dlg.exec()
        
        
    def showVideoSavedMessage(self) -> None:
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Video Saved")
        dlg.setIcon(QMessageBox.Icon.Information)
        dlg.setText("Video saved to device:\n")
        dlg.setStyleSheet(
            """
            QMessageBox {
                background-color: #0b111c;
                color: #e8ecf3;
            }
            QMessageBox QLabel {
                color: #e8ecf3;
            }
            """
        )
        dlg.exec()


    # ------------------------------------------------------------------
    # Call this when a new frame arrives (numpy array BGR)
    # ------------------------------------------------------------------
    def updateFrame(self, frame: np.ndarray, GyroData:tuple[int, int, int]=None):
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


    def __setStreamMode(self, mode: str, emit_signal: bool = True) -> None:
        if mode == self.__streamMode:
            return
        if mode not in self.__streamModeButtons:
            return
        self.__streamMode = mode
        for key, btn in self.__streamModeButtons.items():
            btn.setChecked(key == mode)
        if hasattr(self, "_VideoStreamingWindow__stereoMonoCard"):
            self.__stereoMonoCard.setVisible(mode == "stereo_mono")
            if mode == "stereo_mono":
                self.__setStereoMonoMode(self.__stereoMonoMode, emit_signal=True)
        if hasattr(self, "_VideoStreamingWindow__simulationCard"):
            self.__simulationCard.setVisible(mode == "simulation")
        if emit_signal:
            self.streamModeChanged.emit(mode)


    def __setStereoMonoMode(self, mode: str, emit_signal: bool = True) -> None:
        if mode == self.__stereoMonoMode:
            if emit_signal:
                self.stereoMonoModeChanged.emit(mode)
            return
        if mode not in self.__stereoMonoButtons:
            return
        self.__stereoMonoMode = mode
        for key, btn in self.__stereoMonoButtons.items():
            btn.setChecked(key == mode)
        if emit_signal:
            self.stereoMonoModeChanged.emit(mode)


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


    def updateDeviceVideoList(self, video_names: list[str]) -> None:
        """Update the device video list display."""
        self.__deviceVideoList.clear()
        if not video_names:
            placeholder = QListWidgetItem("No device videos loaded yet")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.__deviceVideoList.addItem(placeholder)
            return
        for name in video_names:
            if name:
                self.__deviceVideoList.addItem(name)

