from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog, QLineEdit,
    QButtonGroup, QProgressDialog, QTabWidget, QFrame, QComboBox, QDialog, QTabBar, QMessageBox,
    QApplication, QListWidget, QListWidgetItem, QMenu, QSpinBox, QDoubleSpinBox,
    QFormLayout, QScrollArea, QCheckBox, QSlider, QToolButton
)
from PyQt6.QtGui import QImage, QPixmap, QColor, QPainter, QFont, QIcon, QDrag, QCursor, QAction
from PyQt6.QtCore import Qt, QMutex, QElapsedTimer, pyqtSignal, QSize, QSettings, QPoint, QMimeData, QPropertyAnimation, QEasingCurve
import numpy as np
import os
import json
import cv2

os.environ["QT_LOGGING_RULES"] = "*.debug=false; *.warning=false"

import logging


class ButtonDropDown(QPushButton, QSpinBox):
    selectionChanged = pyqtSignal(str)

    def __init__(self, options: list[str] | None = None, default: str | None = None, parent=None):
        QPushButton.__init__(self, parent)
        self._menu = QMenu(self)
        self._actions: dict[str, QAction] = {}
        self._current: str | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._showMenu)
        self.setOptions(options or [])
        if default:
            self.setCurrent(default)
        elif options:
            self.setCurrent(options[0])

    def setOptions(self, options: list[str]) -> None:
        self._menu.clear()
        self._actions.clear()
        for item in options:
            action = self._menu.addAction(str(item))
            action.triggered.connect(lambda checked=False, v=str(item): self.setCurrent(v))
            self._actions[str(item)] = action

    def setCurrent(self, value: str) -> None:
        if value not in self._actions:
            action = self._menu.addAction(str(value))
            action.triggered.connect(lambda checked=False, v=str(value): self.setCurrent(v))
            self._actions[str(value)] = action
        if self._current == value:
            return
        self._current = value
        self.setText(value)
        self.setProperty("buttonType", value)
        self.style().unpolish(self)
        self.style().polish(self)
        self.selectionChanged.emit(value)

    def current(self) -> str | None:
        return self._current

    def _showMenu(self) -> None:
        if not self._actions:
            return
        self._menu.exec(self.mapToGlobal(QPoint(0, self.height())))


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


class CollapsibleSection(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        content: QWidget,
        expanded: bool = False,
        max_expanded_height: int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._expanded = bool(expanded)
        self._max_expanded_height = max_expanded_height

        self._toggle = QToolButton(self)
        self._toggle.setObjectName("collapsibleToggle")
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(self._expanded)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if self._expanded else Qt.ArrowType.RightArrow)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setAutoRaise(False)
        self._toggle.clicked.connect(self._onToggleClicked)
        self._toggle.setStyleSheet(
            """
            QToolButton#collapsibleToggle {
                background: rgba(255,255,255,0.04);
                color: #e8ecf3;
                border: 1px solid rgba(255,255,255,0.10);
                border-left: 4px solid rgba(118,185,0,0.9);
                border-radius: 12px;
                padding: 10px 12px;
                font-size: 13px;
                font-weight: 700;
                text-align: left;
            }
            QToolButton#collapsibleToggle:hover {
                background: rgba(0,210,255,0.10);
                border: 1px solid rgba(0,210,255,0.35);
                border-left: 4px solid rgba(118,185,0,1.0);
            }
            QToolButton#collapsibleToggle:checked {
                background: rgba(0,210,255,0.12);
                border: 1px solid rgba(0,210,255,0.45);
                border-left: 4px solid rgba(118,185,0,1.0);
            }
            """
        )

        self._content = content
        self._content.setObjectName("collapsibleContent")
        self._content.setVisible(self._expanded)
        self._content.setMaximumHeight(16777215 if self._expanded else 0)

        self._anim = QPropertyAnimation(self._content, b"maximumHeight", self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.finished.connect(self._onAnimFinished)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(self._toggle)
        root.addWidget(self._content)

    def isExpanded(self) -> bool:
        return self._expanded

    def setExpanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded

        self._toggle.blockSignals(True)
        self._toggle.setChecked(self._expanded)
        self._toggle.blockSignals(False)
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if self._expanded else Qt.ArrowType.RightArrow)

        self._anim.stop()
        start_h = int(self._content.height()) if self._content.isVisible() else 0
        if self._expanded:
            self._content.setVisible(True)
            self._content.setMaximumHeight(0)
            self._content.adjustSize()
            target_h = int(self._content.sizeHint().height())
            if self._max_expanded_height is not None:
                target_h = min(target_h, int(self._max_expanded_height))
        else:
            target_h = 0
        self._anim.setStartValue(start_h)
        self._anim.setEndValue(target_h)
        self._anim.start()

        self.toggled.emit(self._expanded)

    def _onToggleClicked(self) -> None:
        self.setExpanded(self._toggle.isChecked())

    def _onAnimFinished(self) -> None:
        if self._expanded:
            self._content.setMaximumHeight(
                int(self._max_expanded_height) if self._max_expanded_height is not None else 16777215
            )
        else:
            self._content.setVisible(False)


class TearOffTabBar(QTabBar):
    tearOffRequested = pyqtSignal(int, QPoint)
    dockRequested = pyqtSignal(str)

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
            tab_id = self.tabData(self._drag_index)
            if not tab_id:
                super().mouseMoveEvent(event)
                return
            distance = (event.pos() - self._drag_start).manhattanLength()
            if distance >= QApplication.startDragDistance():
                drag = QDrag(self)
                mime = QMimeData()
                mime.setData("application/x-rc-controls-tab", str(tab_id).encode("utf-8"))
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
            raw = bytes(event.mimeData().data("application/x-rc-controls-tab"))
            tab_id = raw.decode("utf-8") if raw else ""
            self.dockRequested.emit(tab_id)
            return
        super().dropEvent(event)


class DockHandle(QFrame):
    """Draggable tab handle to dock the torn-off controls back into the tab bar."""

    def __init__(self, label: str, tab_id: str, parent=None):
        super().__init__(parent)
        self._label = QLabel(label, self)
        self._label.setObjectName("dock-handle-title")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drag_start: QPoint | None = None
        self._tab_id = tab_id

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
                mime.setData("application/x-rc-controls-tab", self._tab_id.encode("utf-8"))
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

    startStreamOut                  = pyqtSignal(bool, str)        # File selected signals
    streamOutRequested              = pyqtSignal(bool, str)        # Start/stop outbound stream request
    fpsChanged                      = pyqtSignal(int)              # Emitted when FPS selection changes
    qualityChanged                  = pyqtSignal(int)              # Emitted when quality slider is released
    cameraSourceSelected            = pyqtSignal(bool)             # calibration mode on/off for camera source
    simulationSourceSelected        = pyqtSignal()                 # request simulation source
    stereoMonoModeChanged           = pyqtSignal(str)              # "normal", "disparity"
    disparityRenderModeChanged      = pyqtSignal(str)              # "depth", "disparity"
    numDisparitiesChanged           = pyqtSignal(int)              # Emitted when num disparities slider is released
    
    preFilterTypeChanged            = pyqtSignal(int)              # Emitted when pre-filter type slider is released
    preFilterSizeChanged            = pyqtSignal(int)              # Emitted when pre-filter size slider is released
    textureThresholdChanged         = pyqtSignal(int)              # Emitted when texture threshold slider is released
    uniquenessRatioChanged          = pyqtSignal(int)              # Emitted when uniqueness ratio slider is released
    preFilterCapChanged             = pyqtSignal(int)              # Emitted when pre-filter cap slider is released
    
    blockSizeChanged                = pyqtSignal(int)              # Emitted when block size slider is released
    uploadVideoClicked              = pyqtSignal(str)              # File selected signal
    deviceVideoLoadRequested        = pyqtSignal(str)              # Device video selection signal
    deviceVideoDeleteRequested      = pyqtSignal(str)              # Device video deletion signal
    setNormalMode                   = pyqtSignal()
    setDisparityMode                = pyqtSignal()
    saveVideoOnDevice               = pyqtSignal(str)              # Signal to save video on device
    recordingStateChanged           = pyqtSignal(bool, str, int)   # Local recording state + path
    stereoCalibrationApplyRequested = pyqtSignal(dict)             # Apply calibration parameters to camera
    calibrationModeToggled          = pyqtSignal(bool)             # Start/stop calibration mode
    calibrationCaptureRequested     = pyqtSignal()                 # Capture a calibration sample
    calibrationPauseToggled         = pyqtSignal(bool)             # Pause/resume calibration capture
    calibrationAbortRequested       = pyqtSignal()                 # Abort calibration session
    calibrationResetRequested       = pyqtSignal()                 # Reset captured samples
    calibrationStoreRequested       = pyqtSignal()                 # Store current calibration results on host
    
    def __init__(self, parent=None):
        super().__init__(parent)

        # Persist user-selected file path across app restarts
        self.__settings = QSettings("RC_CAR_GUI", "VideoStreaming")
        self.__recordPath = self.__settings.value("recordPath", "", str)

        # Ensure the viewport has space before first frame arrives
        self.setMinimumSize(900, 650)

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
        
        self.__buttonSel = ButtonDropDown(["Record Video", "Record Point Cloud"])

        self.__recordBtn = QPushButton("REC")
        self.__recordBtn.setFixedHeight(28)
        self.__recordBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.__recordBtn.setToolTip("Start recording to disk")
        self.__recordBtn.setStyleSheet(
            """
            QPushButton {
                background-color: rgba(210, 45, 45, 0.85);
                color: #fff;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 8px;
                padding: 4px 10px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }
            QPushButton:hover {
                background-color: rgba(230, 65, 65, 0.95);
            }
            QPushButton:pressed {
                background-color: rgba(190, 35, 35, 0.95);
            }
            """
        )
        header.addWidget(self.__recordBtn)
        header.addWidget(self.__buttonSel)

        self.__streamMode = "stereo"
        self.__streamModeButtons: dict[str, QPushButton] = {}
        self.__streamModeGroup = QButtonGroup(self)
        self.__streamModeGroup.setExclusive(True)
        self.__stereoMonoMode = "normal"
        self.__stereoMonoButtons: dict[str, QPushButton] = {}
        self.__stereoMonoGroup = QButtonGroup(self)
        self.__stereoMonoGroup.setExclusive(True)
        self.__disparityRenderMode = "depth"
        root.addLayout(header)

        # Tabs
        self.__tabs = QTabWidget()
        self.__tabBar = TearOffTabBar(self.__tabs)
        self.__tabBar.tearOffRequested.connect(self.__onTearOffRequested)
        self.__tabBar.dockRequested.connect(self.__dockFromHandle)
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
        self.__videoLabel.setMinimumHeight(360)

        viewerLayout.addWidget(self.__videoLabel, stretch=1)

        viewer_settings_expanded = self.__settings.value("viewerSettingsExpanded", False, bool)

        scroll_style = """
        QScrollArea { border: none; background-color: transparent; }
        QScrollArea::viewport { background-color: transparent; }
        QScrollBar:vertical {
            background-color: rgba(255,255,255,0.04);
            width: 10px;
            margin: 2px 0 2px 0;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background-color: rgba(0,210,255,0.22);
            border: 1px solid rgba(0,210,255,0.25);
            min-height: 22px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: rgba(0,210,255,0.32);
            border: 1px solid rgba(0,210,255,0.35);
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
            subcontrol-origin: margin;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background-color: transparent;
        }
        """

        viewerSettingsBody = QWidget()
        viewerSettingsBody.setStyleSheet("background-color: transparent;")
        viewerSettingsLayout = QVBoxLayout(viewerSettingsBody)
        viewerSettingsLayout.setContentsMargins(0, 0, 0, 0)
        viewerSettingsLayout.setSpacing(10)

        recordPathCard = QFrame()
        recordPathCard.setStyleSheet(
            "border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; background: rgba(255,255,255,0.03);"
        )
        recordPathRow = QHBoxLayout(recordPathCard)
        recordPathRow.setContentsMargins(12, 10, 12, 10)
        recordPathRow.setSpacing(8)
        recordPathLabel = QLabel("Record path")
        recordPathLabel.setStyleSheet("color: #9ba7b4; font-size: 12px; font-weight: 600;")
        self.__recordPathEdit = QLineEdit()
        self.__recordPathEdit.setPlaceholderText("Select a folder for recordings")
        if self.__recordPath:
            self.__recordPathEdit.setText(self.__recordPath)
        self.__recordPathEdit.setMinimumHeight(30)
        self.__recordPathEdit.setStyleSheet(
            """
            QLineEdit {
                background-color: rgba(255,255,255,0.06);
                color: #e8ecf3;
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 8px;
                padding: 6px 10px;
                font-size: 12px;
            }
            QLineEdit:hover {
                background-color: rgba(0,210,255,0.12);
                border: 1px solid rgba(0,210,255,0.35);
            }
            QLineEdit:focus {
                border: 1px solid rgba(0,210,255,0.65);
            }
            """
        )
        self.__recordBrowseBtn = IconButton(QIcon("icons/file-manager-icon.svg"), icon_size=QSize(20, 20))
        self.__recordBrowseBtn.setFixedSize(30, 30)
        self.__recordBrowseBtn.setStyleSheet(
            """
            QPushButton {
                background-color: rgba(255,255,255,0.08);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: rgba(0,210,255,0.16);
                border: 1px solid rgba(0,210,255,0.35);
            }
            """
        )
        recordPathRow.addWidget(recordPathLabel)
        recordPathRow.addWidget(self.__recordPathEdit, stretch=1)
        recordPathRow.addWidget(self.__recordBrowseBtn)
        viewerSettingsLayout.addWidget(recordPathCard)

        videoControlCard = QFrame()
        videoControlCard.setStyleSheet(
            "border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; background: rgba(255,255,255,0.03);"
        )
        videoControlLayout = QVBoxLayout(videoControlCard)
        videoControlLayout.setContentsMargins(12, 6, 12, 6)
        videoControlLayout.setSpacing(6)

        fpsTitle = QLabel("FPS")
        fpsTitle.setStyleSheet("color: #9ba7b4; font-size: 12px; font-weight: 600;")
        self.__fpsCombo = QComboBox()
        self.__fpsCombo.addItems(["5", "10", "15", "20", "25", "30", "45", "60"])
        self.__fpsCombo.setCurrentText("30")
        self.__fpsCombo.setFixedWidth(80)
        self.__fpsCombo.setStyleSheet(
            """
            QComboBox {
                background-color: rgba(255,255,255,0.1);
                color: #e8ecf3;
                border: 1px solid rgba(255,255,255,0.18);
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
                padding-right: 24px;
            }
            QComboBox:hover {
                background-color: rgba(0,210,255,0.16);
                border: 1px solid rgba(0,210,255,0.35);
            }
            QComboBox::drop-down {
                border: none;
                width: 22px;
                subcontrol-origin: padding;
                subcontrol-position: top right;
            }
            """
        )

        qualityLabel = QLabel("Quality")
        qualityLabel.setStyleSheet("color: #9ba7b4; font-size: 12px; font-weight: 600;")
        self.__qualitySlider = QSlider(Qt.Orientation.Horizontal)
        self.__qualitySlider.setRange(0, 100)
        self.__qualitySlider.setValue(75)
        self.__qualitySlider.setSingleStep(1)
        self.__qualitySlider.setPageStep(5)
        self.__qualitySlider.setStyleSheet(
            """
            QSlider::groove:horizontal {
                border: 1px solid rgba(255,255,255,0.15);
                height: 6px;
                background: rgba(255,255,255,0.08);
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: rgba(0,210,255,0.7);
                border: 1px solid rgba(0,210,255,0.9);
                width: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }
            QSlider::sub-page:horizontal {
                background: rgba(0,210,255,0.3);
                border-radius: 4px;
            }
            """
        )
        self.__qualityValueLabel = QLabel(str(self.__qualitySlider.value()))
        self.__qualityValueLabel.setStyleSheet("color: #e8ecf3; font-size: 12px; font-weight: 600;")

        disparitiesLabel = QLabel("Disparities")
        disparitiesLabel.setStyleSheet("color: #9ba7b4; font-size: 12px; font-weight: 600;")
        self.__disparitiesSlider = QSlider(Qt.Orientation.Horizontal)
        self.__disparitiesSlider.setRange(8, 128)
        self.__disparitiesSlider.setValue(64)
        self.__disparitiesSlider.setSingleStep(1)
        self.__disparitiesSlider.setPageStep(8)
        self.__disparitiesSlider.setStyleSheet(self.__qualitySlider.styleSheet())
        self.__disparitiesValueLabel = QLabel(str(self.__disparitiesSlider.value()))
        self.__disparitiesValueLabel.setStyleSheet("color: #e8ecf3; font-size: 12px; font-weight: 600;")

        blockLabel = QLabel("Block Size")
        blockLabel.setStyleSheet("color: #9ba7b4; font-size: 12px; font-weight: 600;")
        self.__blockSlider = QSlider(Qt.Orientation.Horizontal)
        self.__blockSlider.setRange(5, 21)
        self.__blockSlider.setValue(5)
        self.__blockSlider.setSingleStep(1)
        self.__blockSlider.setPageStep(2)
        self.__blockSlider.setStyleSheet(self.__qualitySlider.styleSheet())
        self.__blockValueLabel = QLabel(str(self.__blockSlider.value()))
        self.__blockValueLabel.setStyleSheet("color: #e8ecf3; font-size: 12px; font-weight: 600;")
        
        prefilterCapLabel = QLabel("Prefilter Cap")
        prefilterCapLabel.setStyleSheet("color: #9ba7b4; font-size: 12px; font-weight: 600;")
        self.__preFilterCapSlider = QSlider(Qt.Orientation.Horizontal)
        self.__preFilterCapSlider.setRange(1, 63)
        self.__preFilterCapSlider.setValue(31)
        self.__preFilterCapSlider.setSingleStep(1)
        self.__preFilterCapSlider.setPageStep(2)
        self.__preFilterCapSlider.setStyleSheet(self.__qualitySlider.styleSheet())
        self.__preFilterCapValueLabel = QLabel(str(self.__preFilterCapSlider.value()))
        self.__preFilterCapValueLabel.setStyleSheet("color: #e8ecf3; font-size: 12px; font-weight: 600;")
        
        """
        int preFilterSize;
        int preFilterCap;
        int textureThreshold;
        int uniquenessRatio;
        """
        
        prefilterSizeLabel = QLabel("Prefilter Size")
        prefilterSizeLabel.setStyleSheet("color: #9ba7b4; font-size: 12px; font-weight: 600;")
        self.__preFilterSizeSlider = QSlider(Qt.Orientation.Horizontal)
        self.__preFilterSizeSlider.setRange(5, 255)
        self.__preFilterSizeSlider.setValue(5)
        self.__preFilterSizeSlider.setSingleStep(2)
        self.__preFilterSizeSlider.setPageStep(10)
        self.__preFilterSizeSlider.setStyleSheet(self.__qualitySlider.styleSheet())
        self.__preFilterSizeValueLabel = QLabel(str(self.__preFilterSizeSlider.value()))
        self.__preFilterSizeValueLabel.setStyleSheet("color: #e8ecf3; font-size: 12px; font-weight: 600;")
        
        textureThresholdLabel = QLabel("Texture Threshold")
        textureThresholdLabel.setStyleSheet("color: #9ba7b4; font-size: 12px; font-weight: 600;")
        self.__textureThresholdSlider = QSlider(Qt.Orientation.Horizontal)
        self.__textureThresholdSlider.setRange(0, 100)
        self.__textureThresholdSlider.setValue(10)
        self.__textureThresholdSlider.setSingleStep(1)
        self.__textureThresholdSlider.setPageStep(5)
        self.__textureThresholdSlider.setStyleSheet(self.__qualitySlider.styleSheet())
        self.__textureThresholdValueLabel = QLabel(str(self.__textureThresholdSlider.value()))
        self.__textureThresholdValueLabel.setStyleSheet("color: #e8ecf3; font-size: 12px; font-weight: 600;")

        uniquenessRatioLabel = QLabel("Uniqueness Ratio")
        uniquenessRatioLabel.setStyleSheet("color: #9ba7b4; font-size: 12px; font-weight: 600;")
        self.__uniquenessRatioSlider = QSlider(Qt.Orientation.Horizontal)
        self.__uniquenessRatioSlider.setRange(0, 100)
        self.__uniquenessRatioSlider.setValue(15)
        self.__uniquenessRatioSlider.setSingleStep(1)
        self.__uniquenessRatioSlider.setPageStep(5)
        self.__uniquenessRatioSlider.setStyleSheet(self.__qualitySlider.styleSheet())
        self.__uniquenessRatioValueLabel = QLabel(str(self.__uniquenessRatioSlider.value()))
        self.__uniquenessRatioValueLabel.setStyleSheet("color: #e8ecf3; font-size: 12px; font-weight: 600;")

        renderLabel = QLabel("RGB Depth")
        renderLabel.setStyleSheet("color: #9ba7b4; font-size: 12px; font-weight: 600;")
        self.__disparityRenderButtons: dict[str, QPushButton] = {}
        self.__disparityRenderGroup = QButtonGroup(self)
        self.__disparityRenderGroup.setExclusive(True)
        for mode, label in (
            ("depth", "On"),
            ("disparity", "Off"),
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
                    border-radius: 8px;
                    padding: 4px 10px;
                    font-size: 11px;
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
            btn.clicked.connect(lambda checked, m=mode: self.__setDisparityRenderMode(m))
            self.__disparityRenderGroup.addButton(btn)
            self.__disparityRenderButtons[mode] = btn
        self.__disparityRenderButtons[self.__disparityRenderMode].setChecked(True)

        for widget in (
            fpsTitle,
            self.__fpsCombo,
            qualityLabel,
            self.__qualityValueLabel,
            disparitiesLabel,
            self.__disparitiesValueLabel,
            blockLabel,
            self.__blockValueLabel,
            prefilterCapLabel,
            self.__preFilterCapValueLabel,
            prefilterSizeLabel,
            self.__preFilterSizeValueLabel,
            textureThresholdLabel,
            self.__textureThresholdValueLabel,
            uniquenessRatioLabel,
            self.__uniquenessRatioValueLabel,
            renderLabel,
        ):
            widget.setFixedHeight(18)
        for slider in (
            self.__qualitySlider,
            self.__disparitiesSlider,
            self.__blockSlider,
            self.__preFilterCapSlider,
            self.__preFilterSizeSlider,
            self.__textureThresholdSlider,
            self.__uniquenessRatioSlider,
        ):
            slider.setFixedHeight(18)
        for value_label in (
            self.__qualityValueLabel,
            self.__disparitiesValueLabel,
            self.__blockValueLabel,
            self.__preFilterCapValueLabel,
            self.__preFilterSizeValueLabel,
            self.__textureThresholdValueLabel,
            self.__uniquenessRatioValueLabel,
        ):
            value_label.setFixedWidth(36)

        fpsRow = QHBoxLayout()
        fpsRow.setSpacing(8)
        fpsRow.addWidget(fpsTitle)
        fpsRow.addWidget(self.__fpsCombo)
        fpsRow.addStretch()

        qualityRow = QHBoxLayout()
        qualityRow.setSpacing(8)
        qualityRow.addWidget(qualityLabel)
        qualityRow.addWidget(self.__qualitySlider, stretch=1)
        qualityRow.addWidget(self.__qualityValueLabel)

        disparitiesRow = QHBoxLayout()
        disparitiesRow.setSpacing(8)
        disparitiesRow.addWidget(disparitiesLabel)
        disparitiesRow.addWidget(self.__disparitiesSlider, stretch=1)
        disparitiesRow.addWidget(self.__disparitiesValueLabel)

        blockRow = QHBoxLayout()
        blockRow.setSpacing(8)
        blockRow.addWidget(blockLabel)
        blockRow.addWidget(self.__blockSlider, stretch=1)
        blockRow.addWidget(self.__blockValueLabel)

        prefilterCapRow = QHBoxLayout()
        prefilterCapRow.setSpacing(8)
        prefilterCapRow.addWidget(prefilterCapLabel)
        prefilterCapRow.addWidget(self.__preFilterCapSlider, stretch=1)
        prefilterCapRow.addWidget(self.__preFilterCapValueLabel)

        prefilterSizeRow = QHBoxLayout()
        prefilterSizeRow.setSpacing(8)
        prefilterSizeRow.addWidget(prefilterSizeLabel)
        prefilterSizeRow.addWidget(self.__preFilterSizeSlider, stretch=1)
        prefilterSizeRow.addWidget(self.__preFilterSizeValueLabel)

        textureThresholdRow = QHBoxLayout()
        textureThresholdRow.setSpacing(8)
        textureThresholdRow.addWidget(textureThresholdLabel)
        textureThresholdRow.addWidget(self.__textureThresholdSlider, stretch=1)
        textureThresholdRow.addWidget(self.__textureThresholdValueLabel)

        uniquenessRatioRow = QHBoxLayout()
        uniquenessRatioRow.setSpacing(8)
        uniquenessRatioRow.addWidget(uniquenessRatioLabel)
        uniquenessRatioRow.addWidget(self.__uniquenessRatioSlider, stretch=1)
        uniquenessRatioRow.addWidget(self.__uniquenessRatioValueLabel)

        renderRow = QHBoxLayout()
        renderRow.setSpacing(8)
        renderRow.addWidget(renderLabel)
        renderRow.addStretch()
        renderRow.addWidget(self.__disparityRenderButtons["depth"])
        renderRow.addWidget(self.__disparityRenderButtons["disparity"])

        videoControlLayout.addLayout(fpsRow)
        videoControlLayout.addLayout(qualityRow)
        videoControlLayout.addLayout(disparitiesRow)
        videoControlLayout.addLayout(blockRow)
        videoControlLayout.addLayout(prefilterCapRow)
        videoControlLayout.addLayout(prefilterSizeRow)
        videoControlLayout.addLayout(textureThresholdRow)
        videoControlLayout.addLayout(uniquenessRatioRow)
        videoControlLayout.addLayout(renderRow)
        viewerSettingsLayout.addWidget(videoControlCard)

        viewerSettingsScroll = QScrollArea()
        viewerSettingsScroll.setWidgetResizable(True)
        viewerSettingsScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        viewerSettingsScroll.setFrameShape(QFrame.Shape.NoFrame)
        viewerSettingsScroll.setStyleSheet(scroll_style)
        viewerSettingsScroll.setWidget(viewerSettingsBody)

        self.__viewerSettingsSection = CollapsibleSection(
            "Settings", viewerSettingsScroll, expanded=viewer_settings_expanded, max_expanded_height=300
        )
        self.__viewerSettingsSection.toggled.connect(lambda v: self.__settings.setValue("viewerSettingsExpanded", v))
        viewerLayout.addWidget(self.__viewerSettingsSection)
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

        controls_settings_expanded = self.__settings.value("controlsSettingsExpanded", False, bool)
        controlsBody = QWidget()
        controlsBody.setStyleSheet("background-color: transparent;")
        controlsBodyLayout = QVBoxLayout(controlsBody)
        controlsBodyLayout.setContentsMargins(0, 0, 0, 0)
        controlsBodyLayout.setSpacing(12)
        controlsScroll = QScrollArea()
        controlsScroll.setWidgetResizable(True)
        controlsScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controlsScroll.setFrameShape(QFrame.Shape.NoFrame)
        controlsScroll.setStyleSheet(scroll_style)
        controlsScroll.setWidget(controlsBody)

        self.__controlsSettingsSection = CollapsibleSection(
            "Settings", controlsScroll, expanded=controls_settings_expanded, max_expanded_height=520
        )
        self.__controlsSettingsSection.toggled.connect(
            lambda v: (self.__settings.setValue("controlsSettingsExpanded", v), self.__resizeControlsPopout())
        )
        controlsLayout.addWidget(self.__controlsSettingsSection)

        # Stream mode buttons (Stereo/Training)
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
            ("stereo", "Stereo"),
            ("training", "Training"),
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
        controlsBodyLayout.addWidget(streamCard)

        # Stereo sub-modes
        stereoMonoCard = QFrame()
        stereoMonoCard.setStyleSheet("border: none; border-radius: 10px; background: rgba(255,255,255,0.03);")
        stereoMonoCardLayout = QVBoxLayout(stereoMonoCard)
        stereoMonoCardLayout.setContentsMargins(12, 12, 12, 12)
        stereoMonoCardLayout.setSpacing(8)
        stereoMonoTitle = QLabel("Stereo Mode")
        stereoMonoTitle.setObjectName("card-title")
        stereoMonoCardLayout.addWidget(stereoMonoTitle)

        stereoMonoRow = QHBoxLayout()
        stereoMonoRow.setSpacing(6)
        for mode, label in (
            ("normal", "Normal"),
            ("calibration", "Calibration"),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if mode == "normal":
                btn.setToolTip("Default disparity view")
            else:
                btn.setToolTip("Calibration workflow")
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

        controlsBodyLayout.addWidget(stereoMonoCard)
        self.__stereoMonoCard = stereoMonoCard
        self.__stereoMonoCard.setVisible(False)

        # Training library panel (device videos + local upload controls)
        simulationCard = QFrame()
        simulationCard.setStyleSheet("border: none; border-radius: 10px; background: rgba(255,255,255,0.03);")
        simulationLayout = QVBoxLayout(simulationCard)
        simulationLayout.setContentsMargins(12, 12, 12, 12)
        simulationLayout.setSpacing(10)
        simulationTitle = QLabel("Training Library")
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
        self.__deviceVideoList.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.__deviceVideoList.customContextMenuRequested.connect(self.__showDeviceVideoMenu)
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

        self.__activeFileLabel = QLabel("Active file: none")
        self.__activeFileLabel.setObjectName("muted")
        self.__activeFileLabel.setStyleSheet("font-size: 12px;")
        localLayout.addWidget(self.__activeFileLabel)

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
        controlsBodyLayout.addWidget(simulationCard)

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
        self.__fpsSel.currentTextChanged.connect(self.__syncFpsCombos)
        self.__fpsCombo.currentTextChanged.connect(self.__syncFpsCombos)
        
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
        self.__tabs.addTab(self.__controlsTab, self.__controlsTabLabel)
        self.__controlsTabIndex = self.__tabs.indexOf(self.__controlsTab)
        if self.__controlsTabIndex != -1:
            self.__tabBar.setTabData(self.__controlsTabIndex, "controls")

        # Calibration controls (shown when stereo sub-mode is calibration)
        input_style = """
        QSpinBox, QDoubleSpinBox {
            background-color: rgba(255,255,255,0.1);
            color: #e8ecf3;
            border: 1px solid rgba(255,255,255,0.18);
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 12px;
        }
        QSpinBox:hover, QDoubleSpinBox:hover {
            background-color: rgba(0,210,255,0.16);
            border: 1px solid rgba(0,210,255,0.35);
        }
        """
        combo_style = """
        QComboBox {
            background-color: rgba(255,255,255,0.1);
            color: #e8ecf3;
            border: 1px solid rgba(255,255,255,0.18);
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 12px;
            padding-right: 26px;
        }
        QComboBox:hover {
            background-color: rgba(0,210,255,0.16);
            border: 1px solid rgba(0,210,255,0.35);
        }
        QComboBox::drop-down {
            border: none;
            width: 22px;
            subcontrol-origin: padding;
            subcontrol-position: top right;
        }
        """
        line_style = """
        QLineEdit {
            background-color: rgba(255,255,255,0.1);
            color: #e8ecf3;
            border: 1px solid rgba(255,255,255,0.18);
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 12px;
        }
        QLineEdit:hover {
            background-color: rgba(0,210,255,0.16);
            border: 1px solid rgba(0,210,255,0.35);
        }
        """
        button_style = """
        QPushButton {
            background-color: rgba(255,255,255,0.06);
            color: #e8ecf3;
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 10px;
            padding: 8px 14px;
            font-size: 12px;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: rgba(0,210,255,0.16);
            border: 1px solid rgba(0,210,255,0.35);
        }
        """
        primary_button_style = """
        QPushButton {
            background-color: rgba(0,210,255,0.18);
            color: #e8ecf3;
            border: 1px solid rgba(0,210,255,0.55);
            border-radius: 10px;
            padding: 8px 14px;
            font-size: 12px;
            font-weight: 700;
        }
        QPushButton:hover {
            background-color: rgba(0,210,255,0.28);
        }
        """
        checkbox_style = """
        QCheckBox {
            color: #e8ecf3;
            spacing: 8px;
            font-size: 12px;
        }
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border: 1px solid rgba(255,255,255,0.18);
            border-radius: 4px;
            background-color: rgba(255,255,255,0.06);
        }
        QCheckBox::indicator:checked {
            background-color: rgba(0,210,255,0.35);
            border: 1px solid rgba(0,210,255,0.65);
        }
        """
        card_style = "border: none; border-radius: 10px; background: rgba(255,255,255,0.03);"
        label_style = "color: #9ba7b4; font-size: 12px;"

        def make_double(min_val, max_val, step, decimals=3, suffix=""):
            spin = QDoubleSpinBox()
            spin.setRange(min_val, max_val)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
            if suffix:
                spin.setSuffix(suffix)
            spin.setStyleSheet(input_style)
            spin.setMinimumWidth(120)
            return spin

        def make_int(min_val, max_val, step=1, suffix=""):
            spin = QSpinBox()
            spin.setRange(min_val, max_val)
            spin.setSingleStep(step)
            if suffix:
                spin.setSuffix(suffix)
            spin.setStyleSheet(input_style)
            spin.setMinimumWidth(120)
            return spin

        self.__calibProfiles: dict[str, dict] = {}
        self.__calibFields: dict[str, QWidget] = {}

        profileCard = QFrame()
        profileCard.setStyleSheet(card_style)
        profileLayout = QVBoxLayout(profileCard)
        profileLayout.setContentsMargins(12, 12, 12, 12)
        profileLayout.setSpacing(8)
        profileTitle = QLabel("Calibration Profiles")
        profileTitle.setObjectName("card-title")
        profileLayout.addWidget(profileTitle)

        profileRow = QHBoxLayout()
        profileRow.setSpacing(8)
        profileLabel = QLabel("Saved")
        profileLabel.setStyleSheet(label_style)
        self.__calibProfileCombo = QComboBox()
        self.__calibProfileCombo.setStyleSheet(combo_style)
        self.__calibProfileCombo.setMinimumWidth(180)
        self.__calibLoadBtn = QPushButton("Load")
        self.__calibLoadBtn.setStyleSheet(button_style)
        self.__calibDeleteBtn = QPushButton("Delete")
        self.__calibDeleteBtn.setStyleSheet(button_style)
        profileRow.addWidget(profileLabel)
        profileRow.addWidget(self.__calibProfileCombo, stretch=1)
        profileRow.addWidget(self.__calibLoadBtn)
        profileRow.addWidget(self.__calibDeleteBtn)
        profileLayout.addLayout(profileRow)

        profileNameRow = QHBoxLayout()
        profileNameRow.setSpacing(8)
        nameLabel = QLabel("Name")
        nameLabel.setStyleSheet(label_style)
        self.__calibProfileName = QLineEdit()
        self.__calibProfileName.setPlaceholderText("Profile name")
        self.__calibProfileName.setStyleSheet(line_style)
        self.__calibSaveBtn = QPushButton("Save")
        self.__calibSaveBtn.setStyleSheet(button_style)
        self.__calibSaveBtn.setToolTip("Save profile locally")
        profileNameRow.addWidget(nameLabel)
        profileNameRow.addWidget(self.__calibProfileName, stretch=1)
        profileNameRow.addWidget(self.__calibSaveBtn)
        profileLayout.addLayout(profileNameRow)

        self.__calibStartIcon = QIcon("icons/play-icon.svg")
        self.__calibStopIcon = QIcon("icons/red-square-shape-icon.svg")
        self.__calibModeBtn = QPushButton("Start Calibration")
        self.__calibModeBtn.setCheckable(True)
        self.__calibModeBtn.setIcon(self.__calibStartIcon)
        self.__calibModeBtn.setIconSize(QSize(16, 16))
        self.__calibModeBtn.setToolTip("Begin capturing calibration samples")
        self.__calibModeBtn.setStyleSheet(
            """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(0,210,255,0.18),
                    stop:1 rgba(0,180,230,0.28));
                color: #e8ecf3;
                border: 1px solid rgba(0,210,255,0.55);
                border-radius: 12px;
                padding: 9px 16px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(0,210,255,0.28),
                    stop:1 rgba(0,180,230,0.38));
            }
            QPushButton:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(231,76,60,0.22),
                    stop:1 rgba(231,76,60,0.38));
                border: 1px solid rgba(231,76,60,0.6);
                color: #ffd1cc;
            }
            """
        )
        self.__calibModeBtn.setVisible(False)
        self.__calibCaptureBtn = QPushButton("Perform Capture")
        self.__calibCaptureBtn.setStyleSheet(
            """
            QPushButton {
                background-color: rgba(255,255,255,0.08);
                color: #e8ecf3;
                border: 1px solid rgba(255,255,255,0.18);
                border-radius: 10px;
                padding: 8px 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(0,210,255,0.16);
                border: 1px solid rgba(0,210,255,0.35);
            }
            """
        )
        self.__calibCaptureBtn.setToolTip("Capture a calibration sample")
        self.__calibCaptureBtn.setVisible(False)
        self.__calibPauseBtn = QPushButton("Pause")
        self.__calibPauseBtn.setCheckable(True)
        self.__calibPauseBtn.setStyleSheet(button_style)
        self.__calibPauseBtn.setToolTip("Pause calibration capture")
        self.__calibPauseBtn.setEnabled(False)
        self.__calibPauseBtn.setVisible(False)

        self.__calibSendParamsBtn = QPushButton("Send Settings")
        self.__calibSendParamsBtn.setStyleSheet(button_style)
        self.__calibSendParamsBtn.setToolTip("Send calibration settings to device")

        self.__calibAbortBtn = QPushButton("Abort")
        self.__calibAbortBtn.setStyleSheet(
            """
            QPushButton {
                background-color: rgba(231,76,60,0.14);
                color: #ffd1cc;
                border: 1px solid rgba(231,76,60,0.45);
                border-radius: 10px;
                padding: 8px 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(231,76,60,0.24);
                border: 1px solid rgba(231,76,60,0.65);
            }
            """
        )
        self.__calibAbortBtn.setToolTip("Abort calibration session")
        self.__calibAbortBtn.setEnabled(False)
        self.__calibAbortBtn.setVisible(False)

        self.__calibResetBtn = QPushButton("Reset Samples")
        self.__calibResetBtn.setStyleSheet(button_style)
        self.__calibResetBtn.setToolTip("Clear captured samples")
        self.__calibResetBtn.setEnabled(False)
        self.__calibStoreBtn = QPushButton("Store Host Result")
        self.__calibStoreBtn.setStyleSheet(button_style)
        self.__calibStoreBtn.setToolTip("Ask host to persist its latest calibration results")

        calibrationCard = QFrame()
        calibrationCard.setStyleSheet(card_style)
        calibrationLayout = QVBoxLayout(calibrationCard)
        calibrationLayout.setContentsMargins(12, 12, 12, 12)
        calibrationLayout.setSpacing(12)
        calibrationTitle = QLabel("Stereo Calibration")
        calibrationTitle.setObjectName("card-title")
        calibrationLayout.addWidget(calibrationTitle)

        calibrationLayout.addWidget(profileCard)

        sessionCard = QFrame()
        sessionCard.setStyleSheet(card_style)
        sessionLayout = QVBoxLayout(sessionCard)
        sessionLayout.setContentsMargins(12, 12, 12, 12)
        sessionLayout.setSpacing(8)
        sessionTitle = QLabel("Session Controls")
        sessionTitle.setObjectName("card-title")
        sessionLayout.addWidget(sessionTitle)

        sessionRow = QHBoxLayout()
        sessionRow.setSpacing(8)
        sessionRow.addWidget(self.__calibModeBtn)
        sessionRow.addWidget(self.__calibPauseBtn)
        sessionRow.addWidget(self.__calibSendParamsBtn)
        sessionRow.addWidget(self.__calibCaptureBtn)
        sessionRow.addWidget(self.__calibResetBtn)
        sessionRow.addWidget(self.__calibAbortBtn)
        sessionRow.addStretch()
        sessionLayout.addLayout(sessionRow)

        statsRow = QHBoxLayout()
        statsRow.setSpacing(10)
        self.__calibCountLabel = QLabel("Samples: 0/0")
        self.__calibCountLabel.setStyleSheet("color: #9ba7b4; font-size: 12px; font-weight: 600;")
        self.__calibStatusLabel = QLabel("CALIB: --")
        self.__calibStatusLabel.setStyleSheet("color: #e8ecf3; font-size: 12px; font-weight: 600;")
        self.__calibStatusLabel.setWordWrap(True)
        statsRow.addWidget(self.__calibCountLabel)
        statsRow.addWidget(self.__calibStatusLabel, stretch=1)
        sessionLayout.addLayout(statsRow)

        storeRow = QHBoxLayout()
        storeRow.setSpacing(8)
        storeRow.addWidget(self.__calibStoreBtn)
        storeRow.addStretch()
        sessionLayout.addLayout(storeRow)

        calibrationLayout.addWidget(sessionCard)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            """
            QScrollArea { border: none; background: transparent; }
            QScrollArea::viewport { background: transparent; }
            """
        )
        calibBody = QWidget()
        calibBody.setObjectName("calibration-body")
        calibBody.setStyleSheet("background: transparent;")
        scroll.setWidget(calibBody)
        calibrationLayout.addWidget(scroll, stretch=1)

        bodyLayout = QVBoxLayout(calibBody)
        bodyLayout.setContentsMargins(0, 0, 0, 0)
        bodyLayout.setSpacing(12)

        def make_label(text: str) -> QLabel:
            label = QLabel(text)
            label.setStyleSheet(label_style)
            return label

        targetCard = QFrame()
        targetCard.setStyleSheet(card_style)
        targetLayout = QVBoxLayout(targetCard)
        targetLayout.setContentsMargins(12, 12, 12, 12)
        targetLayout.setSpacing(8)
        targetTitle = QLabel("Target & Pattern")
        targetTitle.setObjectName("card-title")
        targetLayout.addWidget(targetTitle)
        targetForm = QFormLayout()
        targetForm.setHorizontalSpacing(14)
        targetForm.setVerticalSpacing(8)
        targetLayout.addLayout(targetForm)

        targetType = QComboBox()
        targetType.addItems(["Chessboard", "Charuco", "Circles Grid"])
        targetType.setStyleSheet(combo_style)
        patternCols = make_int(2, 64, 1)
        patternCols.setValue(9)
        patternRows = make_int(2, 64, 1)
        patternRows.setValue(6)
        squareSize = make_double(0.0, 1000.0, 0.1, 3, " mm")
        squareSize.setValue(25.0)
        markerSize = make_double(0.0, 1000.0, 0.1, 3, " mm")
        markerSize.setValue(15.0)
        markerSize.setEnabled(False)
        unitsCombo = QComboBox()
        unitsCombo.addItems(["mm", "m"])
        unitsCombo.setStyleSheet(combo_style)

        targetForm.addRow(make_label("Target type"), targetType)
        targetForm.addRow(make_label("Pattern cols"), patternCols)
        targetForm.addRow(make_label("Pattern rows"), patternRows)
        targetForm.addRow(make_label("Square size"), squareSize)
        targetForm.addRow(make_label("Marker size"), markerSize)
        targetForm.addRow(make_label("Units"), unitsCombo)

        self.__calibFields["target_type"] = targetType
        self.__calibFields["pattern_cols"] = patternCols
        self.__calibFields["pattern_rows"] = patternRows
        self.__calibFields["square_size"] = squareSize
        self.__calibFields["marker_size"] = markerSize
        self.__calibFields["square_units"] = unitsCombo

        def update_marker_state(text: str) -> None:
            markerSize.setEnabled(text.strip().lower() == "charuco")

        def update_unit_suffix(unit: str) -> None:
            suffix = f" {unit}"
            squareSize.setSuffix(suffix)
            markerSize.setSuffix(suffix)

        targetType.currentTextChanged.connect(update_marker_state)
        unitsCombo.currentTextChanged.connect(update_unit_suffix)
        update_unit_suffix(unitsCombo.currentText())

        bodyLayout.addWidget(targetCard)

        captureCard = QFrame()
        captureCard.setStyleSheet(card_style)
        captureLayout = QVBoxLayout(captureCard)
        captureLayout.setContentsMargins(12, 12, 12, 12)
        captureLayout.setSpacing(8)
        captureTitle = QLabel("Capture Policy")
        captureTitle.setObjectName("card-title")
        captureLayout.addWidget(captureTitle)
        captureForm = QFormLayout()
        captureForm.setHorizontalSpacing(14)
        captureForm.setVerticalSpacing(8)
        captureLayout.addLayout(captureForm)

        captureMode = QComboBox()
        captureMode.addItems(["Manual", "Auto"])
        captureMode.setStyleSheet(combo_style)
        requiredSamples = make_int(5, 500, 1)
        requiredSamples.setValue(25)
        stableFrames = make_int(0, 120, 1)
        stableFrames.setValue(3)
        minInterval = make_double(0.0, 10.0, 0.1, 2, " s")
        minInterval.setValue(0.5)

        captureForm.addRow(make_label("Capture mode"), captureMode)
        captureForm.addRow(make_label("Required samples"), requiredSamples)
        captureForm.addRow(make_label("Stable frames"), stableFrames)
        captureForm.addRow(make_label("Min interval"), minInterval)

        self.__calibFields["capture_mode"] = captureMode
        self.__calibFields["required_samples"] = requiredSamples
        self.__calibFields["stable_frames"] = stableFrames
        self.__calibFields["min_interval_s"] = minInterval

        def update_capture_button(text: str) -> None:
            self.__calibCaptureBtn.setEnabled(text.strip().lower() == "manual")

        captureMode.currentTextChanged.connect(update_capture_button)
        update_capture_button(captureMode.currentText())

        bodyLayout.addWidget(captureCard)

        qualityCard = QFrame()
        qualityCard.setStyleSheet(card_style)
        qualityLayout = QVBoxLayout(qualityCard)
        qualityLayout.setContentsMargins(12, 12, 12, 12)
        qualityLayout.setSpacing(8)
        qualityTitle = QLabel("Quality Thresholds")
        qualityTitle.setObjectName("card-title")
        qualityLayout.addWidget(qualityTitle)
        qualityForm = QFormLayout()
        qualityForm.setHorizontalSpacing(14)
        qualityForm.setVerticalSpacing(8)
        qualityLayout.addLayout(qualityForm)

        minCorners = make_int(0, 2000, 1)
        minCorners.setValue(0)
        blurThreshold = make_double(0.0, 5000.0, 1.0, 1)
        blurThreshold.setValue(0.0)
        edgeMargin = make_int(0, 200, 1, " px")
        edgeMargin.setValue(0)
        minTargetSize = make_int(0, 100, 1, " %")
        minTargetSize.setValue(0)
        maxTargetSize = make_int(0, 100, 1, " %")
        maxTargetSize.setValue(0)
        maxReproj = make_double(0.0, 20.0, 0.01, 3, " px")
        maxReproj.setValue(0.0)

        qualityForm.addRow(make_label("Min corners"), minCorners)
        qualityForm.addRow(make_label("Blur threshold"), blurThreshold)
        qualityForm.addRow(make_label("Edge margin"), edgeMargin)
        qualityForm.addRow(make_label("Min target size"), minTargetSize)
        qualityForm.addRow(make_label("Max target size"), maxTargetSize)
        qualityForm.addRow(make_label("Max reprojection error"), maxReproj)

        self.__calibFields["min_corners"] = minCorners
        self.__calibFields["blur_threshold"] = blurThreshold
        self.__calibFields["edge_margin_px"] = edgeMargin
        self.__calibFields["min_target_size_pct"] = minTargetSize
        self.__calibFields["max_target_size_pct"] = maxTargetSize
        self.__calibFields["max_reproj_error_px"] = maxReproj

        bodyLayout.addWidget(qualityCard)

        outputCard = QFrame()
        outputCard.setStyleSheet(card_style)
        outputLayout = QVBoxLayout(outputCard)
        outputLayout.setContentsMargins(12, 12, 12, 12)
        outputLayout.setSpacing(8)
        outputTitle = QLabel("Output View")
        outputTitle.setObjectName("card-title")
        outputLayout.addWidget(outputTitle)
        outputForm = QFormLayout()
        outputForm.setHorizontalSpacing(14)
        outputForm.setVerticalSpacing(8)
        outputLayout.addLayout(outputForm)

        showOverlays = QCheckBox("Show overlays (corners/axes)")
        showOverlays.setStyleSheet(checkbox_style)
        showStats = QCheckBox("Show stats (reprojection error)")
        showStats.setStyleSheet(checkbox_style)
        streamView = QComboBox()
        streamView.addItems(["Combined", "Left", "Right"])
        streamView.setStyleSheet(combo_style)

        outputForm.addRow(showOverlays)
        outputForm.addRow(showStats)
        outputForm.addRow(make_label("Stream view"), streamView)

        self.__calibFields["show_overlays"] = showOverlays
        self.__calibFields["show_stats"] = showStats
        self.__calibFields["stream_view"] = streamView

        bodyLayout.addWidget(outputCard)

        computeCard = QFrame()
        computeCard.setStyleSheet(card_style)
        computeLayout = QVBoxLayout(computeCard)
        computeLayout.setContentsMargins(12, 12, 12, 12)
        computeLayout.setSpacing(8)
        computeTitle = QLabel("Compute")
        computeTitle.setObjectName("card-title")
        computeLayout.addWidget(computeTitle)
        computeRow = QHBoxLayout()
        computeRow.setSpacing(8)
        self.__calibRecomputeRectify = QCheckBox("Recompute rectification")
        self.__calibRecomputeRectify.setStyleSheet(checkbox_style)
        self.__calibApplyBtn = QPushButton("Run Calibration")
        self.__calibApplyBtn.setStyleSheet(primary_button_style)
        self.__calibApplyBtn.setToolTip("Run calibration with current settings")
        computeRow.addWidget(self.__calibRecomputeRectify)
        computeRow.addStretch()
        computeRow.addWidget(self.__calibApplyBtn)
        computeLayout.addLayout(computeRow)

        self.__calibFields["recompute_rectification"] = self.__calibRecomputeRectify

        bodyLayout.addWidget(computeCard)

        self.__calibrationCard = calibrationCard
        self.__calibrationCard.setVisible(False)
        controlsBodyLayout.addWidget(self.__calibrationCard)
        controlsBodyLayout.addWidget(fpsCard)
        
        # Icon playig flag
        self.__isPlaying = False
        self.__isRecording = False
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
        self.__recordBtn.clicked.connect(self.__toggleRecording)
        self.__recordBrowseBtn.clicked.connect(self.__selectRecordPath)
        self.__recordPathEdit.editingFinished.connect(self.__storeRecordPathFromEdit)
        self.__fileLineEdit.textChanged.connect(self.__updateActiveFileLabel)
        self.__calibSaveBtn.clicked.connect(self.__saveCalibrationProfile)
        self.__calibLoadBtn.clicked.connect(self.__loadCalibrationProfile)
        self.__calibDeleteBtn.clicked.connect(self.__deleteCalibrationProfile)
        self.__calibApplyBtn.clicked.connect(self.__applyCalibrationToCamera)
        self.__calibModeBtn.clicked.connect(self.__toggleCalibrationMode)
        self.__calibCaptureBtn.clicked.connect(self.__performCalibrationCapture)
        self.__calibPauseBtn.clicked.connect(self.__toggleCalibrationPause)
        self.__calibSendParamsBtn.clicked.connect(self.__sendCalibrationSettings)
        self.__calibAbortBtn.clicked.connect(self.__abortCalibrationSession)
        self.__calibResetBtn.clicked.connect(self.__resetCalibrationSamples)
        self.__calibStoreBtn.clicked.connect(self.__storeCalibrationResult)
        self.__calibProfileCombo.currentTextChanged.connect(self.__syncCalibrationProfileName)
        self.__qualitySlider.valueChanged.connect(self.__updateQualityLabel)
        self.__qualitySlider.sliderReleased.connect(self.__emitQualityChanged)
        self.__disparitiesSlider.valueChanged.connect(self.__updateDisparitiesLabel)
        self.__disparitiesSlider.sliderReleased.connect(self.__emitDisparitiesChanged)
        self.__blockSlider.valueChanged.connect(self.__updateBlockLabel)
        self.__blockSlider.sliderReleased.connect(self.__emitBlockChanged)
        self.__preFilterCapSlider.valueChanged.connect(self.__updatePreFilterCapLabel)
        self.__preFilterSizeSlider.valueChanged.connect(self.__updatePreFilterSizeLabel)
        self.__textureThresholdSlider.valueChanged.connect(self.__updateTextureThresholdLabel)
        self.__uniquenessRatioSlider.valueChanged.connect(self.__updateUniquenessRatioLabel)
        
        # Default stream mode
        self.__setStreamMode(self.__streamMode, emit_signal=False)
        self.__updateActiveFileLabel(self.__fileLineEdit.text())
        self.__loadCalibrationProfiles()


    def __updateActiveFileLabel(self, path: str) -> None:
        name = os.path.basename(path) if path else ""
        if not name:
            self.__activeFileLabel.setText("Active file: none")
        else:
            self.__activeFileLabel.setText(f"Active file: {name}")

    def updateFpsDisplay(self, fps_text: str) -> None:
        if hasattr(self, "_VideoStreamingWindow__fpsCombo") and str(fps_text).isdigit():
            if self.__fpsCombo.findText(str(fps_text)) != -1:
                self.__fpsCombo.blockSignals(True)
                self.__fpsSel.blockSignals(True)
                self.__fpsCombo.setCurrentText(str(fps_text))
                self.__fpsSel.setCurrentText(str(fps_text))
                self.__fpsSel.blockSignals(False)
                self.__fpsCombo.blockSignals(False)

    def __updateQualityLabel(self, value: int) -> None:
        self.__qualityValueLabel.setText(str(value))

    def __emitQualityChanged(self) -> None:
        self.qualityChanged.emit(int(self.__qualitySlider.value()))

    def __updateDisparitiesLabel(self, value: int) -> None:
        step = 8
        snapped = (value // step) * step
        if snapped < self.__disparitiesSlider.minimum():
            snapped = self.__disparitiesSlider.minimum()
        if snapped != value:
            self.__disparitiesSlider.blockSignals(True)
            self.__disparitiesSlider.setValue(snapped)
            self.__disparitiesSlider.blockSignals(False)
        self.__disparitiesValueLabel.setText(str(snapped))

    def __emitDisparitiesChanged(self) -> None:
        value = int(self.__disparitiesSlider.value())
        step = 8
        snapped = (value // step) * step
        if snapped < self.__disparitiesSlider.minimum():
            snapped = self.__disparitiesSlider.minimum()
        self.numDisparitiesChanged.emit(snapped)

    def __updateBlockLabel(self, value: int) -> None:
        odd_value = value if value % 2 == 1 else value - 1
        if odd_value < self.__blockSlider.minimum():
            odd_value = self.__blockSlider.minimum()
        if odd_value != value:
            self.__blockSlider.blockSignals(True)
            self.__blockSlider.setValue(odd_value)
            self.__blockSlider.blockSignals(False)
        self.__blockValueLabel.setText(str(odd_value))

    def __emitBlockChanged(self) -> None:
        value = int(self.__blockSlider.value())
        if value % 2 == 0:
            value -= 1
        self.blockSizeChanged.emit(max(self.__blockSlider.minimum(), value))

    def __updatePreFilterCapLabel(self, value: int) -> None:
        self.__preFilterCapValueLabel.setText(str(int(value)))
        self.preFilterCapChanged.emit(int(value))

    def __updatePreFilterSizeLabel(self, value: int) -> None:
        odd_value = value if value % 2 == 1 else value - 1
        if odd_value < self.__preFilterSizeSlider.minimum():
            odd_value = self.__preFilterSizeSlider.minimum()
        if odd_value != value:
            self.__preFilterSizeSlider.blockSignals(True)
            self.__preFilterSizeSlider.setValue(odd_value)
            self.__preFilterSizeSlider.blockSignals(False)
        self.__preFilterSizeValueLabel.setText(str(odd_value))
        self.preFilterSizeChanged.emit(odd_value)

    def __updateTextureThresholdLabel(self, value: int) -> None:
        self.__textureThresholdValueLabel.setText(str(int(value)))
        self.textureThresholdChanged.emit(int(value))

    def __updateUniquenessRatioLabel(self, value: int) -> None:
        self.__uniquenessRatioValueLabel.setText(str(int(value)))
        self.uniquenessRatioChanged.emit(int(value))

    def __syncFpsCombos(self, value: str) -> None:
        if not hasattr(self, "_VideoStreamingWindow__fpsCombo") or not hasattr(self, "_VideoStreamingWindow__fpsSel"):
            return
        sender = self.sender()
        target = self.__fpsSel if sender is self.__fpsCombo else self.__fpsCombo
        if target.currentText() == value:
            return
        target.blockSignals(True)
        target.setCurrentText(value)
        target.blockSignals(False)
        try:
            self.fpsChanged.emit(int(value))
        except ValueError:
            return

    def updateCalibrationStats(self, count_text: str | None, status_text: str | None) -> None:
        if hasattr(self, "_VideoStreamingWindow__calibCountLabel") and count_text:
            self.__calibCountLabel.setText(str(count_text))
        if hasattr(self, "_VideoStreamingWindow__calibStatusLabel") and status_text:
            self.__calibStatusLabel.setText(str(status_text))
        self.__resizeControlsPopout()


    def __syncCalibrationProfileName(self, name: str) -> None:
        if not name:
            return
        self.__calibProfileName.setText(name)

    def __toggleCalibrationMode(self, checked: bool | None = None, emit_signal: bool = True) -> None:
        active = self.__calibModeBtn.isChecked()
        if active:
            self.__calibModeBtn.setText("Stop Calibration")
            self.__calibModeBtn.setIcon(self.__calibStopIcon)
            self.__calibModeBtn.setToolTip("Stop capturing calibration samples")
        else:
            self.__calibModeBtn.setText("Start Calibration")
            self.__calibModeBtn.setIcon(self.__calibStartIcon)
            self.__calibModeBtn.setToolTip("Begin capturing calibration samples")
            if self.__calibPauseBtn.isChecked():
                self.__calibPauseBtn.setChecked(False)
                self.__toggleCalibrationPause()
        self.__calibCaptureBtn.setVisible(False)
        self.__calibPauseBtn.setEnabled(False)
        self.__calibResetBtn.setEnabled(active)
        self.__calibAbortBtn.setEnabled(False)
        if emit_signal:
            self.calibrationModeToggled.emit(active)
        self.__resizeControlsPopout()

    def __performCalibrationCapture(self) -> None:
        self.calibrationCaptureRequested.emit()

    def __sendCalibrationSettings(self) -> None:
        if not self.__calibModeBtn.isChecked():
            self.__calibModeBtn.setChecked(True)
            self.__toggleCalibrationMode()
        else:
            self.calibrationModeToggled.emit(True)
        self.__applyCalibrationToCamera()

    def __toggleCalibrationPause(self) -> None:
        paused = self.__calibPauseBtn.isChecked()
        if paused:
            self.__calibPauseBtn.setText("Resume")
            self.__calibPauseBtn.setToolTip("Resume calibration capture")
        else:
            self.__calibPauseBtn.setText("Pause")
            self.__calibPauseBtn.setToolTip("Pause calibration capture")
        self.calibrationPauseToggled.emit(paused)

    def __abortCalibrationSession(self) -> None:
        self.calibrationAbortRequested.emit()

    def __resetCalibrationSamples(self) -> None:
        self.calibrationResetRequested.emit()


    def __collectCalibrationParams(self) -> dict:
        profile_name = self.__calibProfileName.text().strip() or self.__calibProfileCombo.currentText().strip()
        target_type = self.__calibFields.get("target_type")
        pattern_cols = self.__calibFields.get("pattern_cols")
        pattern_rows = self.__calibFields.get("pattern_rows")
        square_size = self.__calibFields.get("square_size")
        square_units = self.__calibFields.get("square_units")
        marker_size = self.__calibFields.get("marker_size")
        capture_mode = self.__calibFields.get("capture_mode")
        required_samples = self.__calibFields.get("required_samples")
        stable_frames = self.__calibFields.get("stable_frames")
        min_interval = self.__calibFields.get("min_interval_s")
        min_corners = self.__calibFields.get("min_corners")
        blur_threshold = self.__calibFields.get("blur_threshold")
        edge_margin = self.__calibFields.get("edge_margin_px")
        min_target_size = self.__calibFields.get("min_target_size_pct")
        max_target_size = self.__calibFields.get("max_target_size_pct")
        max_reproj = self.__calibFields.get("max_reproj_error_px")
        show_overlays = self.__calibFields.get("show_overlays")
        show_stats = self.__calibFields.get("show_stats")
        stream_view = self.__calibFields.get("stream_view")
        recompute_rectify = self.__calibFields.get("recompute_rectification")

        return {
            "profile_name": profile_name,
            "target": {
                "type": target_type.currentText().lower() if isinstance(target_type, QComboBox) else "chessboard",
                "pattern": {
                    "cols": int(pattern_cols.value()) if isinstance(pattern_cols, QSpinBox) else 0,
                    "rows": int(pattern_rows.value()) if isinstance(pattern_rows, QSpinBox) else 0,
                },
                "square_size": {
                    "value": float(square_size.value()) if isinstance(square_size, QDoubleSpinBox) else 0.0,
                    "units": square_units.currentText() if isinstance(square_units, QComboBox) else "mm",
                },
                "marker_size": {
                    "value": float(marker_size.value()) if isinstance(marker_size, QDoubleSpinBox) else 0.0,
                    "units": square_units.currentText() if isinstance(square_units, QComboBox) else "mm",
                },
            },
            "capture": {
                "mode": capture_mode.currentText().lower() if isinstance(capture_mode, QComboBox) else "manual",
                "required_samples": int(required_samples.value()) if isinstance(required_samples, QSpinBox) else 0,
                "stable_frames": int(stable_frames.value()) if isinstance(stable_frames, QSpinBox) else 0,
                "min_interval_s": float(min_interval.value()) if isinstance(min_interval, QDoubleSpinBox) else 0.0,
            },
            "quality": {
                "min_corners": int(min_corners.value()) if isinstance(min_corners, QSpinBox) else 0,
                "blur_threshold": float(blur_threshold.value()) if isinstance(blur_threshold, QDoubleSpinBox) else 0.0,
                "edge_margin_px": int(edge_margin.value()) if isinstance(edge_margin, QSpinBox) else 0,
                "min_target_size_pct": int(min_target_size.value()) if isinstance(min_target_size, QSpinBox) else 0,
                "max_target_size_pct": int(max_target_size.value()) if isinstance(max_target_size, QSpinBox) else 0,
                "max_reproj_error_px": float(max_reproj.value()) if isinstance(max_reproj, QDoubleSpinBox) else 0.0,
            },
            "compute": {
                "recompute_rectification": bool(recompute_rectify.isChecked()) if isinstance(recompute_rectify, QCheckBox) else False,
            },
            "output_view": {
                "show_overlays": bool(show_overlays.isChecked()) if isinstance(show_overlays, QCheckBox) else False,
                "show_stats": bool(show_stats.isChecked()) if isinstance(show_stats, QCheckBox) else False,
                "stream_view": stream_view.currentText().lower() if isinstance(stream_view, QComboBox) else "combined",
            },
        }


    def __applyCalibrationParams(self, params: dict) -> None:
        if not isinstance(params, dict):
            return
        profile_name = params.get("profile_name")
        if profile_name:
            self.__calibProfileName.setText(str(profile_name))

        target = params.get("target", {})
        if isinstance(target, dict):
            target_type = self.__calibFields.get("target_type")
            if isinstance(target_type, QComboBox):
                target_value = str(target.get("type", "")).title()
                if target_value and target_type.findText(target_value) == -1:
                    target_type.addItem(target_value)
                if target_value:
                    target_type.setCurrentText(target_value)
            pattern = target.get("pattern", {})
            if isinstance(pattern, dict):
                pattern_cols = self.__calibFields.get("pattern_cols")
                if isinstance(pattern_cols, QSpinBox):
                    pattern_cols.setValue(int(pattern.get("cols", pattern_cols.value())))
                pattern_rows = self.__calibFields.get("pattern_rows")
                if isinstance(pattern_rows, QSpinBox):
                    pattern_rows.setValue(int(pattern.get("rows", pattern_rows.value())))
            square = target.get("square_size", {})
            if isinstance(square, dict):
                square_size = self.__calibFields.get("square_size")
                if isinstance(square_size, QDoubleSpinBox):
                    square_size.setValue(float(square.get("value", square_size.value())))
                square_units = self.__calibFields.get("square_units")
                if isinstance(square_units, QComboBox):
                    units_value = str(square.get("units", square_units.currentText()))
                    if square_units.findText(units_value) == -1:
                        square_units.addItem(units_value)
                    square_units.setCurrentText(units_value)
            marker = target.get("marker_size", {})
            if isinstance(marker, dict):
                marker_size = self.__calibFields.get("marker_size")
                if isinstance(marker_size, QDoubleSpinBox):
                    marker_size.setValue(float(marker.get("value", marker_size.value())))

        capture = params.get("capture", {})
        if isinstance(capture, dict):
            capture_mode = self.__calibFields.get("capture_mode")
            if isinstance(capture_mode, QComboBox):
                mode_value = str(capture.get("mode", "")).capitalize()
                if mode_value and capture_mode.findText(mode_value) == -1:
                    capture_mode.addItem(mode_value)
                if mode_value:
                    capture_mode.setCurrentText(mode_value)
            required_samples = self.__calibFields.get("required_samples")
            if isinstance(required_samples, QSpinBox):
                required_samples.setValue(int(capture.get("required_samples", required_samples.value())))
            stable_frames = self.__calibFields.get("stable_frames")
            if isinstance(stable_frames, QSpinBox):
                stable_frames.setValue(int(capture.get("stable_frames", stable_frames.value())))
            min_interval = self.__calibFields.get("min_interval_s")
            if isinstance(min_interval, QDoubleSpinBox):
                min_interval.setValue(float(capture.get("min_interval_s", min_interval.value())))

        quality = params.get("quality", {})
        if isinstance(quality, dict):
            min_corners = self.__calibFields.get("min_corners")
            if isinstance(min_corners, QSpinBox):
                min_corners.setValue(int(quality.get("min_corners", min_corners.value())))
            blur_threshold = self.__calibFields.get("blur_threshold")
            if isinstance(blur_threshold, QDoubleSpinBox):
                blur_threshold.setValue(float(quality.get("blur_threshold", blur_threshold.value())))
            edge_margin = self.__calibFields.get("edge_margin_px")
            if isinstance(edge_margin, QSpinBox):
                edge_margin.setValue(int(quality.get("edge_margin_px", edge_margin.value())))
            min_target = self.__calibFields.get("min_target_size_pct")
            if isinstance(min_target, QSpinBox):
                min_target.setValue(int(quality.get("min_target_size_pct", min_target.value())))
            max_target = self.__calibFields.get("max_target_size_pct")
            if isinstance(max_target, QSpinBox):
                max_target.setValue(int(quality.get("max_target_size_pct", max_target.value())))
            max_reproj = self.__calibFields.get("max_reproj_error_px")
            if isinstance(max_reproj, QDoubleSpinBox):
                max_reproj.setValue(float(quality.get("max_reproj_error_px", max_reproj.value())))

        output_view = params.get("output_view", {})
        if isinstance(output_view, dict):
            show_overlays = self.__calibFields.get("show_overlays")
            if isinstance(show_overlays, QCheckBox):
                show_overlays.setChecked(bool(output_view.get("show_overlays", show_overlays.isChecked())))
            show_stats = self.__calibFields.get("show_stats")
            if isinstance(show_stats, QCheckBox):
                show_stats.setChecked(bool(output_view.get("show_stats", show_stats.isChecked())))
            stream_view = self.__calibFields.get("stream_view")
            if isinstance(stream_view, QComboBox):
                view_value = str(output_view.get("stream_view", "")).capitalize()
                if view_value and stream_view.findText(view_value) == -1:
                    stream_view.addItem(view_value)
                if view_value:
                    stream_view.setCurrentText(view_value)

        compute = params.get("compute", {})
        if isinstance(compute, dict):
            recompute = self.__calibFields.get("recompute_rectification")
            if isinstance(recompute, QCheckBox):
                recompute.setChecked(bool(compute.get("recompute_rectification", recompute.isChecked())))


    def __persistCalibrationProfiles(self) -> None:
        try:
            raw = json.dumps(self.__calibProfiles)
        except Exception as exc:
            logging.error("Failed to serialize calibration profiles: %s", exc)
            return
        self.__settings.setValue("calibrationProfiles", raw)


    def __loadCalibrationProfiles(self) -> None:
        raw = self.__settings.value("calibrationProfiles", "", str)
        profiles: dict[str, dict] = {}
        if raw:
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    profiles = loaded
            except Exception as exc:
                logging.error("Failed to load calibration profiles: %s", exc)
        self.__calibProfiles = profiles
        self.__calibProfileCombo.blockSignals(True)
        self.__calibProfileCombo.clear()
        for name in sorted(self.__calibProfiles.keys()):
            self.__calibProfileCombo.addItem(name)
        self.__calibProfileCombo.blockSignals(False)

        last_profile = self.__settings.value("calibrationLastProfile", "", str)
        if last_profile and last_profile in self.__calibProfiles:
            self.__calibProfileCombo.setCurrentText(last_profile)
            self.__applyCalibrationParams(self.__calibProfiles[last_profile])


    def __saveCalibrationProfile(self) -> None:
        name = self.__calibProfileName.text().strip() or self.__calibProfileCombo.currentText().strip()
        if not name:
            self.showErrorMessage("Provide a profile name before saving.")
            return
        self.__calibProfiles[name] = self.__collectCalibrationParams()
        self.__persistCalibrationProfiles()
        if self.__calibProfileCombo.findText(name) == -1:
            self.__calibProfileCombo.addItem(name)
        self.__calibProfileCombo.setCurrentText(name)
        self.__settings.setValue("calibrationLastProfile", name)


    def __loadCalibrationProfile(self) -> None:
        name = self.__calibProfileName.text().strip() or self.__calibProfileCombo.currentText().strip()
        if not name:
            self.showErrorMessage("Select or enter a profile name to load.")
            return
        if name not in self.__calibProfiles:
            self.showErrorMessage(f"No saved profile named '{name}'.")
            return
        self.__applyCalibrationParams(self.__calibProfiles[name])
        self.__settings.setValue("calibrationLastProfile", name)


    def __deleteCalibrationProfile(self) -> None:
        name = self.__calibProfileCombo.currentText().strip()
        if not name or name not in self.__calibProfiles:
            return
        self.__calibProfiles.pop(name, None)
        self.__persistCalibrationProfiles()
        index = self.__calibProfileCombo.findText(name)
        if index != -1:
            self.__calibProfileCombo.removeItem(index)
        self.__calibProfileName.clear()


    def __storeCalibrationResult(self) -> None:
        self.calibrationStoreRequested.emit()


    def __applyCalibrationToCamera(self) -> None:
        params = self.__collectCalibrationParams()
        self.stereoCalibrationApplyRequested.emit(params)


    def __showDeviceVideoMenu(self, pos) -> None:
        item = self.__deviceVideoList.itemAt(pos)
        if item is None:
            return
        if not item.flags() & Qt.ItemFlag.ItemIsEnabled:
            return
        self.__deviceVideoList.setCurrentItem(item)
        self.__deviceVideoList.setFocus()
        self.__deviceVideoList.scrollToItem(item)
        menu = QMenu(self)
        load_action = menu.addAction("Load video")
        delete_action = menu.addAction("Delete video")
        action = menu.exec(self.__deviceVideoList.mapToGlobal(pos))
        if action == load_action:
            name = item.text().strip()
            if name:
                self.deviceVideoLoadRequested.emit(name)
        elif action == delete_action:
            name = item.text().strip()
            if name:
                self.deviceVideoDeleteRequested.emit(name)
            return

    def __dockFromHandle(self, tab_id: str) -> None:
        if tab_id == "controls":
            self.__dockControls()
            return
        if self.__controlsPopout is not None:
            self.__dockControls()

    def __onTearOffRequested(self, index: int, global_pos: QPoint) -> None:
        tab = self.__tabs.widget(index)
        if tab is self.__controlsTab:
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

        popoutLayout = QVBoxLayout(self.__controlsPopout)
        popoutLayout.setContentsMargins(12, 12, 12, 12)
        popoutLayout.setSpacing(10)

        handle = DockHandle(self.__controlsTabLabel, "controls", self.__controlsPopout)
        popoutLayout.addWidget(handle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.__controlsTab.setParent(self.__controlsPopout)
        popoutLayout.addWidget(self.__controlsTab)
        self.__controlsTab.show()
        self.__controlsPopout.finished.connect(lambda _=None: self.__dockControls())
        self.__controlsPopout.show()
        self.__resizeControlsPopout()
        self.__placeDialogOnScreen(self.__controlsPopout, global_pos)
        self.__controlsPopout.raise_()
        self.__controlsPopout.activateWindow()


    def __resizeControlsPopout(self) -> None:
        if self.__controlsPopout is None or not self.__controlsPopout.isVisible():
            return
        self.__controlsTab.adjustSize()
        self.__controlsPopout.adjustSize()
        self.__controlsPopout.resize(self.__controlsPopout.sizeHint())
        self.__controlsPopout.updateGeometry()


    def __placeDialogOnScreen(self, dlg: QDialog, anchor_pos: QPoint | None) -> None:
        if dlg is None:
            return
        if anchor_pos is None:
            anchor_pos = QCursor.pos()

        screen = QApplication.screenAt(anchor_pos)
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return

        avail = screen.availableGeometry()
        w = dlg.width() if dlg.width() > 0 else dlg.sizeHint().width()
        h = dlg.height() if dlg.height() > 0 else dlg.sizeHint().height()
        w = max(w, dlg.minimumWidth())
        h = max(h, dlg.minimumHeight())

        # Prefer positioning ABOVE the click point so it "comes up" (higher).
        desired_x = anchor_pos.x() - (w // 2)
        desired_y = anchor_pos.y() - h - 24

        margin = 12
        x_min = avail.left() + margin
        y_min = avail.top() + margin
        x_max = avail.right() - w - margin
        y_max = avail.bottom() - h - margin

        x = max(x_min, min(desired_x, x_max))
        y = max(y_min, min(desired_y, y_max))
        dlg.move(QPoint(int(x), int(y)))


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
            self.__tabBar.setTabData(new_index, "controls")
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

    def __selectRecordPath(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Recording Folder")
        if not path:
            return
        self.__recordPathEdit.setText(path)
        self.__storeRecordPath(path)


    def __storeRecordPathFromEdit(self) -> None:
        self.__storeRecordPath(self.__recordPathEdit.text().strip())


    def __storeRecordPath(self, path: str) -> None:
        if not path:
            return
        self.__recordPath = path
        self.__settings.setValue("recordPath", path)


    def __toggleRecording(self) -> None:
        if not self.__isRecording:
            path = self.__recordPathEdit.text().strip()
            if not path:
                self.__selectRecordPath()
                path = self.__recordPathEdit.text().strip()
            if not path:
                logging.info("Recording path not set; recording cancelled")
                return
            self.__storeRecordPath(path)
            self.__recordBtn.setText("REC ON")
            self.__recordBtn.setToolTip("Stop recording")
            self.__isRecording = True
        else:
            self.__recordBtn.setText("REC")
            self.__recordBtn.setToolTip("Start recording to disk")
            self.__isRecording = False
        
        setting : int = -1
        if self.__buttonSel.current() == "Record Video":
            setting = 0
        elif self.__buttonSel.current() == "Record Point Cloud":
            setting = 1
        self.recordingStateChanged.emit(self.__isRecording, self.__recordPath, setting)
    

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
        self.streamOutRequested.emit(self.__isPlaying, self.__fileLineEdit.text())

    def autoStartStreamOut(self) -> None:
        if self.__isPlaying:
            return
        text = self.__fileLineEdit.text()
        if text == "" or not os.path.isfile(text) or not text.lower().endswith(".mov"):
            logging.info("No valid .MOV file selected for auto-start")
            return
        self.__settings.setValue("lastFilePath", text)
        self.__startStreamOutBtn.setAllIcons(self.__pauseIcon)
        self.__startStreamOutBtn.setToolTip("Pause outbound stream")
        self.__isPlaying = True
        self.startStreamOut.emit(True, self.__fileLineEdit.text())
        self.streamOutRequested.emit(True, self.__fileLineEdit.text())


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
        dlg.setText("Video saved to device.")
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
        
        
    def updateDeviceVideoList(self, loadedVideoName: str | list[str], video_list: list[str] | None = None) -> None:
        """Update the device video list and preselect the loaded video when provided."""
        if video_list is None and isinstance(loadedVideoName, list):
            loaded_name = ""
            video_names = loadedVideoName
        else:
            loaded_name = loadedVideoName or ""
            video_names = video_list or []

        self.__deviceVideoList.clear()
        if not video_names:
            placeholder = QListWidgetItem("No device videos loaded yet")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.__deviceVideoList.addItem(placeholder)
            return

        selected_item = None
        for video_name in video_names:
            if not video_name:
                continue
            item = QListWidgetItem(video_name)
            self.__deviceVideoList.addItem(item)
            if loaded_name and video_name == loaded_name:
                selected_item = item
        if selected_item is not None:
            self.__deviceVideoList.setCurrentItem(selected_item)
            
            
    def updateSettingsFromParams(self, params: dict) -> None:
        """Update UI settings based on provided parameters dictionary."""
        quality = params.get("quality")
        if isinstance(quality, int):
            self.__qualitySlider.setValue(quality)
        num_disparities = params.get("num_disparities", params.get("disparities"))
        if isinstance(num_disparities, int) and hasattr(self, "_VideoStreamingWindow__disparitiesSlider"):
            self.__disparitiesSlider.setValue(num_disparities)
        block_size = params.get("block_size", params.get("blocks"))
        if isinstance(block_size, int) and hasattr(self, "_VideoStreamingWindow__blockSlider"):
            self.__blockSlider.setValue(block_size)

        pre_filter_cap = params.get("pre_filter_cap", params.get("preFilterCap"))
        if isinstance(pre_filter_cap, int) and hasattr(self, "_VideoStreamingWindow__preFilterCapSlider"):
            self.__preFilterCapSlider.setValue(pre_filter_cap)

        pre_filter_size = params.get("pre_filter_size", params.get("preFilterSize"))
        if isinstance(pre_filter_size, int) and hasattr(self, "_VideoStreamingWindow__preFilterSizeSlider"):
            self.__preFilterSizeSlider.setValue(pre_filter_size)

        texture_threshold = params.get("texture_threshold", params.get("textureThreshold"))
        if isinstance(texture_threshold, int) and hasattr(self, "_VideoStreamingWindow__textureThresholdSlider"):
            self.__textureThresholdSlider.setValue(texture_threshold)

        uniqueness_ratio = params.get("uniqueness_ratio", params.get("uniquenessRatio"))
        if isinstance(uniqueness_ratio, int) and hasattr(self, "_VideoStreamingWindow__uniquenessRatioSlider"):
            self.__uniquenessRatioSlider.setValue(uniqueness_ratio)
        fps = params.get("fps")
        if isinstance(fps, int):
            self.updateFpsDisplay(str(fps))
        stream_mode = params.get("stream_mode")
        if isinstance(stream_mode, str):
            self.__setStreamMode(stream_mode)
        stereo_mono_mode = params.get("stereo_mono_mode")
        if isinstance(stereo_mono_mode, str):
            self.__setStereoMonoMode(stereo_mono_mode)
        render_mode = params.get("disparity_render_mode")
        if isinstance(render_mode, str) and render_mode in ("depth", "disparity"):
            self.__setDisparityRenderMode(render_mode, emit_signal=False)


    # ------------------------------------------------------------------
    # Call this when a new frame arrives (disparity frame)
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


    def updateDisparityFrame(self, frame: np.ndarray) -> None:
        """Render disparity frame (already RGB)."""
        if frame is None:
            return
        self.updateFrame(frame)


    def __setStreamMode(self, mode: str, emit_signal: bool = True) -> None:
        if mode not in self.__streamModeButtons:
            return
        same_mode = mode == self.__streamMode
        if same_mode and emit_signal:
            return
        self.__streamMode = mode
        for key, btn in self.__streamModeButtons.items():
            btn.setChecked(key == mode)
        if hasattr(self, "_VideoStreamingWindow__stereoMonoCard"):
            self.__stereoMonoCard.setVisible(mode == "stereo")
            if mode == "stereo":
                self.__setStereoMonoMode(self.__stereoMonoMode, emit_signal=emit_signal)
        if hasattr(self, "_VideoStreamingWindow__simulationCard"):
            self.__simulationCard.setVisible(mode == "training")
        if hasattr(self, "_VideoStreamingWindow__calibrationCard") and mode != "stereo":
            self.__calibrationCard.setVisible(False)
        if mode != "stereo" and hasattr(self, "_VideoStreamingWindow__calibModeBtn"):
            if self.__calibModeBtn.isChecked():
                self.__calibModeBtn.setChecked(False)
                self.__toggleCalibrationMode(emit_signal=emit_signal)
        if emit_signal and mode == "training":
            self.simulationSourceSelected.emit()
        self.__resizeControlsPopout()

    def __setDisparityRenderMode(self, mode: str, emit_signal: bool = True) -> None:
        if mode not in self.__disparityRenderButtons:
            return
        self.__disparityRenderMode = mode
        for key, btn in self.__disparityRenderButtons.items():
            btn.setChecked(key == mode)
        if emit_signal:
            self.disparityRenderModeChanged.emit(mode)
        self.__resizeControlsPopout()


    def __setStereoMonoMode(self, mode: str, emit_signal: bool = True) -> None:
        if mode not in self.__stereoMonoButtons:
            return
        self.__stereoMonoMode = mode
        for key, btn in self.__stereoMonoButtons.items():
            btn.setChecked(key == mode)
        if hasattr(self, "_VideoStreamingWindow__calibrationCard"):
            self.__calibrationCard.setVisible(mode == "calibration")
        if mode == "calibration":
            if hasattr(self, "_VideoStreamingWindow__calibModeBtn"):
                if not self.__calibModeBtn.isChecked():
                    self.__calibModeBtn.setChecked(True)
                    self.__toggleCalibrationMode(emit_signal=emit_signal)
            if emit_signal:
                self.__applyCalibrationToCamera()
        else:
            if hasattr(self, "_VideoStreamingWindow__calibModeBtn"):
                if self.__calibModeBtn.isChecked():
                    self.__calibModeBtn.setChecked(False)
                    self.__toggleCalibrationMode(emit_signal=emit_signal)
            if emit_signal:
                self.stereoMonoModeChanged.emit("disparity")
        if emit_signal:
            self.cameraSourceSelected.emit(mode == "calibration")
        self.__resizeControlsPopout()


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




