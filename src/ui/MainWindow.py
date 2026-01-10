from PyQt6.QtCore import (
    QSize, QPropertyAnimation, QRect, QRectF, QEasingCurve, Qt, QTimer, pyqtSignal, QPoint
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton, QGraphicsOpacityEffect,
    QLabel, QFrame, QGraphicsBlurEffect, QHBoxLayout, QFileDialog, QMessageBox, QGridLayout,
    QSizePolicy, QToolTip
)
from PyQt6.QtGui import QIcon, QFont, QPixmap, QPainter, QColor, QPen, QCursor

from ui.TelemetryWindow import VehicleTelemetryWindow
from ui.VideoStreamingWindow import VideoStreamingWindow
from ui.VisualizationWindow import VisualizationWindow
from ui.UIConsumer import BackendIface
from ui.FirmwareUpdateWindow import FirmwareUpdateWindow
from ui.theme import make_card
import logging


class GlowButton(QPushButton):
    """Modern flat button with hover glow."""

    def __init__(self, text):
        super().__init__(text)
        self.setFixedHeight(46)
        self.setIconSize(QSize(22, 22))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,255,255,0.04);
                color: #e8ecf3;
                font-size: 15px;
                font-weight: 600;
                border-radius: 10px;
                border: 1px solid rgba(255,255,255,0.08);
                padding-left: 14px;
                text-align: left;
            }
            QPushButton:hover {
                background-color: rgba(0,210,255,0.16);
                border: 1px solid rgba(0,210,255,0.35);
            }
            QPushButton:pressed {
                background-color: rgba(0,210,255,0.24);
            }
        """)


class SidePanel(QFrame):
    PANEL_WIDTH = 210
    PEEK = 12    # show a subtle grab area when closed

    # Signals
    showWelcome     = pyqtSignal()  # Show welcome
    showTlm         = pyqtSignal()  # Show telemetry signal
    showVideoStream = pyqtSignal()  # Show video stream
    showVisualizer  = pyqtSignal()  # Show 3D visualizer

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setFixedWidth(self.PANEL_WIDTH)
        self.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #0f1624,
                    stop:1 #0b111c);
                border-right: 2px solid rgba(0,210,255,0.55);
            }
        """)

        # Animation
        self.__anim = QPropertyAnimation(self, b"geometry")
        self.__anim.setDuration(300)
        self.__anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # Auto-hide timer
        self.autoHideTimer = QTimer()
        self.autoHideTimer.setInterval(400)  # 0.4s after no mouse → hide
        self.autoHideTimer.timeout.connect(self.__autoHideCheck)

        self.hidden = True
        self.pinned = False

        # ----------------------
        # Layout & Widgets
        # ----------------------
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.setLayout(layout)

        # Header
        header = QLabel("Quick Nav")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStyleSheet("color: #e8ecf3; font-size: 18px; font-weight: 700;")
        layout.addWidget(header)

        # Buttons
        # Welcome window button
        self.btnWelcome = GlowButton(" Home")
        self.btnWelcome.setIcon(QIcon("icons/home-icon.svg"))
        self.btnWelcome.setToolTip("Go to home window")
        
        self.btnTelem = GlowButton(" Telemetry")
        self.btnTelem.setIcon(QIcon("icons/activity.svg"))
        self.btnTelem.setToolTip("See vehicle telemetry")

        self.btnVideo = GlowButton(" Video")
        self.btnVideo.setToolTip("Open video stream")
        self.btnVideo.setIcon(QIcon("icons/video.svg"))

        self.btn3d = GlowButton(" 3D View")
        self.btn3d.setToolTip("Open 3D visualization")
        self.btn3d.setIcon(QIcon("icons/rc-car.png"))

        self.btnFw    = GlowButton(" Firmware")
        self.btnFw.setIcon(QIcon("icons/upgrade.svg"))
        self.btnFw.setToolTip("Upload Firmware")
        self.btnFw.setIconSize(QSize(24, 24))

        self.btnGPS = GlowButton("GPS")
        self.btnGPS.setToolTip("GPS Position")

        layout.addWidget(self.btnWelcome)
        layout.addWidget(self.btnTelem)
        layout.addWidget(self.btnVideo)
        layout.addWidget(self.btn3d)
        layout.addWidget(self.btnFw)
        layout.addWidget(self.btnGPS)

        layout.addStretch()

        # Pin button row
        pinRow = QHBoxLayout()
        layout.addLayout(pinRow)

        self.pinButton = QPushButton("📌 Pin")
        self.pinButton.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.1);
                color: white;
                border-radius: 6px;
                font-size: 15px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.25);
            }
        """)
        self.pinButton.setFixedHeight(36)
        self.pinButton.clicked.connect(self.togglePin)

        pinRow.addWidget(self.pinButton)
        self.setMouseTracking(True)
        
        # Connect all signals
        self.__connectSignals()
        

    def __connectSignals(self) -> None:
        """
        Handles the connection of signals
        """
        self.btnWelcome.clicked.connect(lambda: self.showWelcome.emit())
        self.btnTelem.clicked.connect(lambda: self.showTlm.emit())
        self.btnVideo.clicked.connect(lambda: self.showVideoStream.emit())
        self.btn3d.clicked.connect(lambda: self.showVisualizer.emit())


    # ----------------------------
    # Slide Animations
    # ----------------------------
    def slideIn(self):
        if not self.hidden:
            return
        self.hidden = False
        h = self.parent().height()

        self.__anim.stop()
        self.__anim.setStartValue(QRect(-self.PANEL_WIDTH + self.PEEK, 0,
                                        self.PANEL_WIDTH, h))
        self.__anim.setEndValue(QRect(0, 0, self.PANEL_WIDTH, h))
        self.__anim.start()


    def slideOut(self):
        if self.hidden or self.pinned:
            return
        self.hidden = True
        h = self.parent().height()

        self.__anim.stop()
        self.__anim.setStartValue(QRect(0, 0, self.PANEL_WIDTH, h))
        self.__anim.setEndValue(QRect(-self.PANEL_WIDTH + self.PEEK, 0,
                        self.PANEL_WIDTH, h))
        self.__anim.start() 

    # ----------------------------
    # Pinning logic
    # ----------------------------
    def togglePin(self):
        self.pinned = not self.pinned
        self.pinButton.setText("📌 Unpin" if self.pinned else "📌 Pin")

        if self.pinned:
            self.slideIn()
        else:
            self.autoHideTimer.start()

    # ----------------------------
    # Auto-hide after timeout
    # ----------------------------
    def startAutoHide(self):
        if not self.pinned:
            self.autoHideTimer.start()


    def __autoHideCheck(self):
        if not self.underMouse():
            self.slideOut()
            self.autoHideTimer.stop()

            
class ClickableLabel(QLabel):
    """A QLabel that behaves like a transparent icon button (clickable)."""
    def __init__(self, pixmap, tooltip:str = "", callback=None, parent=None):
        super().__init__(parent)
        if isinstance(pixmap, QIcon):
            pm = pixmap.pixmap(pixmap.availableSizes()[0] if pixmap.availableSizes() else QSize(64,64))
        elif isinstance(pixmap, QPixmap):
            pm = pixmap
        else:
            pm = QPixmap(pixmap)

        self.setPixmap(pm)
        self.setToolTip(tooltip)
        self._callback = callback
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if callable(self._callback):
            try:
                self._callback()
            except Exception:
                pass
        super().mousePressEvent(event)


class WelcomeWindow(QWidget):

    startRequested = pyqtSignal()  # emitted when user clicks Start

    def __init__(self, parent=None, flags=Qt.WindowType.Widget):
        super().__init__(parent, flags)
        self.setObjectName("welcome-panel")

        # Make panel look premium
        self.setStyleSheet("""
            QWidget#welcome-panel {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(18, 24, 34, 235),
                    stop:1 rgba(11, 16, 24, 230));
                border-radius: 20px;
                border: 1px solid rgba(255,255,255,0.10);
            }
            QLabel {
                color: #e8ecf3;
                background: transparent;
                border: none;
            }
            QLabel#title {
                color: #e8ecf3;
                font-weight: 700;
                letter-spacing: 1px;
                text-transform: uppercase;
            }
            QPushButton {
                background-color: #00d2ff;
                color: #0a1116;
                border-radius: 14px;
                padding: 13px 26px;
                font-size: 16px;
                font-weight: 700;
                border: none;
            }
            QPushButton:hover {
                background-color: #26deff;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # App title
        title = QLabel("RC CAR CONTROL SUITE")
        title.setFont(QFont("Segoe UI", 24, QFont.Weight.Bold))
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Subtitle
        subtitle = QLabel("Autonomous Vehicle / Telemetry Platform")
        subtitle.setFont(QFont("Segoe UI", 14))
        subtitle.setStyleSheet("color: #9ba7b4;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Optional logo
        logo = QLabel()
        logo.setPixmap(QIcon("icons/car.svg").pixmap(128, 128))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Welcome text
        welcome = QLabel("Welcome! Connect your RC vehicle to begin.")
        welcome.setFont(QFont("Segoe UI", 13))
        welcome.setStyleSheet("color: #c4ccd8;")
        welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Start button
        self.startBtn = QPushButton("Start Session")
        self.startBtn.clicked.connect(self.startRequested.emit)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(15)
        layout.addWidget(logo)
        layout.addSpacing(10)
        layout.addWidget(welcome)
        # Discovered devices area (icons will be added here)
        self._devices_container = QWidget()
        self._devices_layout = QHBoxLayout()
        self._devices_layout.setSpacing(12)
        self._devices_container.setLayout(self._devices_layout)
        layout.addWidget(self._devices_container)
        layout.addSpacing(15)
        layout.addWidget(self.startBtn)

        # Fade-in animation
        self._fadeEffect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fadeEffect)

        self._fadeAnim = QPropertyAnimation(self._fadeEffect, b"opacity")
        self._fadeAnim.setDuration(900)
        self._fadeAnim.setStartValue(0.0)
        self._fadeAnim.setEndValue(1.0)
        self._fadeAnim.setEasingCurve(QEasingCurve.Type.InOutQuad)

    def setStartButtonState(self, discovering: bool) -> None:
        """Enable/disable the start button with visual feedback."""
        if discovering:
            self.startBtn.setEnabled(False)
            self.startBtn.setText("Discovering…")
        else:
            self.startBtn.setEnabled(True)
            self.startBtn.setText("Start Session")

    def fadeIn(self):
        """Call this when adding the widget to your main UI."""
        self._fadeAnim.start()


    def addDevice(self, icon_path: str, deviceTooltip: str = "", connect_callback=None):
        """Add a discovered device icon to the welcome panel.

        - `ip` is the device IP shown on hover
        - `icon_path` is the path to the image to display
        - `connect_callback` is called when the user clicks the icon
        """
        # Avoid duplicates
        for i in range(self._devices_layout.count()):
            w = self._devices_layout.itemAt(i).widget()
            if w and getattr(w, "deviceTooltip", None) == deviceTooltip:
                return

        # Use a ClickableLabel to avoid button chrome and background artifacts
        try:
            icon = QPixmap(icon_path)
        except Exception:
            icon = QPixmap()

        lbl = ClickableLabel(icon, tooltip=f"{deviceTooltip}\nClick to connect", callback=connect_callback)
        lbl._deviceTooltip = deviceTooltip
        lbl.setFixedSize(72, 72)
        lbl.setScaledContents(True)
        self._devices_layout.addWidget(lbl)


class BatteryIndicator(QWidget):
    """Compact controller + battery icon with percentage text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = -1
        self._controller_icon = QIcon("icons/dualsense.svg")
        self.setFixedHeight(35)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setToolTip("Controller battery")

    def setLevel(self, level: int | None) -> None:
        if level is None:
            level_int = -1
        else:
            try:
                level_int = max(0, min(100, int(level)))
            except Exception:
                level_int = -1

        if level_int != self._level:
            self._level = level_int
            self.update()

    def _level_color(self) -> QColor:
        if self._level < 0:
            return QColor(155, 167, 180)
        if self._level <= 20:
            return QColor(231, 76, 60)
        if self._level <= 50:
            return QColor(255, 200, 87)
        return QColor(54, 224, 184)

    def paintEvent(self, event):  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        padding = 4
        icon_w = 24
        icon_h = 10
        cap_w = 3
        cap_h = 6
        y = (rect.height() - icon_h) / 2

        border_rect = rect.adjusted(0, 0, -1, -1)
        painter.setPen(QPen(QColor(255, 255, 255, 160), 1))
        painter.setBrush(QColor(255, 255, 255, 10))
        painter.drawRoundedRect(border_rect, 8, 8)

        controller_size = 30
        controller_x = padding + 2
        controller_y = (rect.height() - controller_size) / 2
        controller_pix = self._controller_icon.pixmap(controller_size, controller_size)
        if not controller_pix.isNull():
            tinted = QPixmap(controller_pix.size())
            tinted.fill(Qt.GlobalColor.transparent)
            tint_painter = QPainter(tinted)
            tint_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            tint_painter.drawPixmap(0, 0, controller_pix)
            tint_painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            tint_painter.fillRect(tinted.rect(), QColor(155, 167, 180))
            tint_painter.end()
            painter.drawPixmap(int(controller_x), int(controller_y), tinted)

        battery_x = controller_x + controller_size + 8
        body = QRectF(battery_x, y, icon_w, icon_h)
        cap = QRectF(battery_x + icon_w, y + (icon_h - cap_h) / 2, cap_w, cap_h)

        outline = QColor(255, 255, 255, 120)
        painter.setPen(QPen(outline, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(body, 2.5, 2.5)
        painter.drawRoundedRect(cap, 1.5, 1.5)

        if self._level >= 0:
            inset = 2
            fill_w = max(2, (icon_w - inset * 2) * (self._level / 100))
            fill_rect = QRectF(
                battery_x + inset,
                y + inset,
                fill_w,
                icon_h - inset * 2,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._level_color())
            painter.drawRoundedRect(fill_rect, 2, 2)

        text = "--%" if self._level < 0 else f"{self._level}%"
        painter.setPen(QColor(232, 236, 243))
        font = QFont("Segoe UI", 8, QFont.Weight.DemiBold)
        painter.setFont(font)
        text_x = battery_x + icon_w + cap_w + 6
        text_rect = QRectF(text_x, 0, rect.width() - text_x, rect.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("RC Control Studio")
        self.setMinimumSize(QSize(1080, 720))
        self.setWindowIcon(QIcon("icons/car.svg"))

        # Welcome
        self.__welcomeWindow = WelcomeWindow(self)

        # Central UI
        self.central = QWidget()
        centralLayout = QVBoxLayout()
        centralLayout.setContentsMargins(24, 24, 24, 24)
        centralLayout.setSpacing(14)

        headerRow = QGridLayout()
        headerRow.setHorizontalSpacing(10)
        headerRow.setColumnStretch(0, 1)
        headerRow.setColumnStretch(1, 0)
        headerRow.setColumnStretch(2, 1)
        headerRow.setContentsMargins(0, 0, 0, 0)

        self.__titleLabel = QLabel("RC Control Studio")
        self.__titleLabel.setObjectName("header-title")
        self.__titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.__statusChip = QLabel("Idle")
        self.__statusChip.setObjectName("status-chip")
        self.__statusChip.setProperty("state", "idle")
        self.__statusChip.setWordWrap(False)
        self.__statusChip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.__statusChip.setFixedHeight(36)
        self.__statusChip.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.__setStatusChip("Idle", "idle")

        self.__batteryIndicator = BatteryIndicator(self)
        self.__batteryIndicator.setVisible(False)

        chipWrap = QWidget()
        chipLayout = QHBoxLayout(chipWrap)
        chipLayout.setContentsMargins(0, 0, 0, 0)
        chipLayout.setSpacing(8)
        chipLayout.addWidget(self.__statusChip)
        chipLayout.addWidget(self.__batteryIndicator)

        headerRow.addWidget(self.__titleLabel, 0, 1, Qt.AlignmentFlag.AlignCenter)
        headerRow.addWidget(
            chipWrap,
            0,
            2,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        self.contentFrame = QFrame()
        make_card(self.contentFrame)
        self.__contentLayout = QVBoxLayout()
        self.__contentLayout.setContentsMargins(18, 18, 18, 18)
        self.__contentLayout.setSpacing(12)
        self.contentFrame.setLayout(self.__contentLayout)

        self.__setContent(self.__welcomeWindow)
        self.__welcomeWindow.fadeIn()

        centralLayout.addLayout(headerRow)
        centralLayout.addWidget(self.contentFrame)
        centralLayout.setStretch(0, 0)
        centralLayout.setStretch(1, 1)
        self.central.setLayout(centralLayout)
        self.setCentralWidget(self.central)
        
        # Telemetry window
        self.__tlmWindow    = VehicleTelemetryWindow(self)
        self.__streamWindow = VideoStreamingWindow()
        self.__fwWindow     = FirmwareUpdateWindow(self)
        self.__vizWindow    = VisualizationWindow(self)

        # Side panel
        self.side = SidePanel(self)
        self.side.setGeometry(-SidePanel.PANEL_WIDTH + SidePanel.PEEK, 0,
                    SidePanel.PANEL_WIDTH, self.height())
        self.side.raise_()
        self.side.hidden = True
        
        # import UI consumer
        self.__consumer = BackendIface()

        # Disable side buttons until a device is connected
        self.side.btnTelem.setEnabled(False)
        self.side.btnVideo.setEnabled(False)
        self.side.btnFw.setEnabled(False)
        self.side.btnGPS.setEnabled(False)

        # Mouse tracking
        self.setMouseTracking(True)
        self.central.setMouseTracking(True)
        self.side.setMouseTracking(True)
        
        # Connect signals
        self.__connectSignals()
        self.__consumer.start()


    def __connectSignals(self):
        """
        Handles connection of UI signals
        """
        self.side.showWelcome.connect(self.__showWelcome)
        self.side.showTlm.connect(self.__showTlm)
        self.side.showVideoStream.connect(self.__showVideo)
        self.side.showVisualizer.connect(self.__showVisualizer)
        self.__welcomeWindow.startRequested.connect(self.__onDiscoveryStart)
        # When a device is discovered, show it on the welcome window
        self.__consumer.deviceDiscovered.connect(self.__onDeviceDiscovered)
        # When UI requests connection to a device, backend will emit deviceConnected
        self.__consumer.deviceConnected.connect(self.__onDeviceConnected)
        self.__consumer.deviceMacResolved.connect(self.__onDeviceMacResolved)
        
        self.__consumer.videoBufferSignal.connect(lambda left_frame, right_frame: self.__streamWindow.updateFrame(left_frame))
        self.__consumer.videoBufferSignalStereo.connect(lambda left_frame, right_frame: self.__streamWindow.updateStereoFrame(left_frame, right_frame))
        self.__consumer.telemetryReceived.connect(lambda tlm : self.__tlmWindow.updateTelemetry(tlm))
        self.__consumer.videoUploadProgress.connect(self.__updateVideoUploadProgress)
        self.__consumer.videoUploadFinished.connect(self.__streamWindow.finishUploadProgress)
        self.__consumer.notifyDisconnect.connect(self.__handleDisconnect)
        self.__consumer.controllerConnected.connect(self.__onControllerConnected)
        self.__consumer.controllerBatteryLevel.connect(self.__onControllerBatteryLevel)
        self.__consumer.controllerDisconnected.connect(self.__onControllerDisconnected)
        
        self.__streamWindow.startStreamOut.connect(lambda state, fileName : self.__consumer.setStreamMode(state))
        self.__streamWindow.viewModeChanged.connect(self.__consumer.setVideoMode)
        self.__streamWindow.uploadVideoClicked.connect(self.__consumer.uploadVideoFile)
        self.side.btnFw.clicked.connect(lambda: self.__showFirmware())


    def __handleDisconnect(self) -> None:
        """
        Handle device disconnection
        """
        self.side.btnTelem.setEnabled(False)
        self.side.btnVideo.setEnabled(False)
        self.side.btnFw.setEnabled(False)
        self.side.btnGPS.setEnabled(False)
        self.__setStatusChip("Disconnected", "idle")
        self.__batteryIndicator.setLevel(None)
        self.__disconnectWindowShow()
        self.__showWelcome()


    def __disconnectWindowShow(self) -> None:
        try:
            msg = QMessageBox(self)
            msg.setWindowTitle("Device Disconnected")
            msg.setText("Connection to the RC car was lost. Check power/network and reconnect.")
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.setStyleSheet(
                """
                QMessageBox {
                    background-color: #0b111c;
                    color: #e8ecf3;
                    border: 1px solid rgba(0,210,255,0.35);
                    border-radius: 10px;
                    padding: 12px;
                }
                QMessageBox QLabel {
                    color: #e8ecf3;
                    font-size: 14px;
                }
                QMessageBox QPushButton {
                    background-color: rgba(0,210,255,0.16);
                    color: #e8ecf3;
                    border: 1px solid rgba(0,210,255,0.35);
                    border-radius: 8px;
                    padding: 6px 14px;
                    min-width: 80px;
                }
                QMessageBox QPushButton:hover {
                    background-color: rgba(0,210,255,0.24);
                }
                """
            )
            msg.exec()
        except Exception:
            pass


    def __updateVideoUploadProgress(self, sent: int, total: int) -> None:
        """
        Update video upload progress bar in the streaming window

        Args:
            sent (int): _sent bytes
            total (int): _total bytes
        """
        self.__streamWindow.updateUploadProgress(sent, total)


    def __onControllerConnected(self, connType: str) -> None:
        """Handle controller connection events."""
        self.__batteryIndicator.setVisible(True)
        tooltip_text = f"Controller connected via {connType}"
        self.__batteryIndicator.setToolTip(tooltip_text)
        anchor = self.__batteryIndicator.mapToGlobal(self.__batteryIndicator.rect().bottomRight())
        QToolTip.showText(anchor + QPoint(6, 6), tooltip_text, self.__batteryIndicator)


    def __onControllerBatteryLevel(self, level: int) -> None:
        """Update controller battery level in the header."""
        if not self.__batteryIndicator.isVisible():
            self.__batteryIndicator.setVisible(True)
        self.__batteryIndicator.setLevel(level)


    def __onControllerDisconnected(self) -> None:
        """Hide controller battery indicator when connection is lost."""
        self.__batteryIndicator.setLevel(None)
        self.__batteryIndicator.setVisible(False)


    def __onDeviceDiscovered(self, ip: str) -> None:
        """Add a discovered device to the welcome window with hover tooltip and click-to-connect."""
        # Use a car icon from icons/ folder
        icon_path = "icons/rc-car.png"
        self.__welcomeWindow.addDevice(icon_path, ip, connect_callback=lambda: self.__consumer.connectToDevice(ip))
        self.__setStatusChip("Discovering devices", "discovering")
        self.__welcomeWindow.setStartButtonState(False)


    def __onDeviceConnected(self, ip: str) -> None:
        """Enable the side panel buttons once a connection to the device is initiated."""
        self.side.btnTelem.setEnabled(True)
        self.side.btnVideo.setEnabled(True)
        self.side.btnFw.setEnabled(True)
        self.side.btnGPS.setEnabled(True)
        self.__setStatusChip(f"Connected - {ip}", "connected")
        self.__welcomeWindow.setStartButtonState(False)
        # Visually mark the connected device in the welcome panel
        if hasattr(self.__welcomeWindow, "_devices_layout"):
            for i in range(self.__welcomeWindow._devices_layout.count()):
                w = self.__welcomeWindow._devices_layout.itemAt(i).widget()
                if w and getattr(w, "_device_ip", None) == ip:
                    w.setStyleSheet("border: 2px solid #00aaff; border-radius:8px; background: rgba(0,170,255,0.06);")
                elif w:
                    w.setStyleSheet("background: transparent;")


    def __onDeviceMacResolved(self, ip: str, mac: str) -> None:
        """Update UI with resolved MAC address when available."""
        if mac:
            mac = mac.replace("-", ":")
            # Keep chip single-line; show MAC via tooltip to avoid clipping
            self.__setStatusChip(f"Connected - {ip}", "connected")
            self.__statusChip.setToolTip(f"MAC address: {mac}")


    def __updateVideoUploadProgress(self, sent: int, total: int) -> None:
        if total <= 0:
            return
        percent = int((sent / total) * 100)
        self.__streamWindow.updateUploadProgress(percent)
        if sent >= total:
            self.__streamWindow.finishUploadProgress(True)


    def __startStreamOut(self, state : bool, fileName : str) -> None:
        self.__consumer.setStreamMode(state)


    def __onDiscoveryStart(self) -> None:
        """Begin discovery and reflect state in the UI header chip."""
        self.__setStatusChip("Discovering devices", "discovering")
        self.__welcomeWindow.setStartButtonState(True)
        self.__consumer.startDiscovery()


    def __setContent(self, widget: QWidget) -> None:
        """Swap the central card contents with the requested widget."""
        self.__clearLayout(self.__contentLayout)
        self.__contentLayout.addWidget(widget)


    def __setStatusChip(self, text: str, state: str) -> None:
        """Update header status chip text and state styling."""
        self.__statusChip.setText(text)
        self.__statusChip.setProperty("state", state)
        self.__statusChip.style().unpolish(self.__statusChip)
        self.__statusChip.style().polish(self.__statusChip)



    def __showWelcome(self) -> None:
        """
        Shows welcome window
        """
        self.__setContent(self.__welcomeWindow)



    def __showTlm(self) -> None:
        """
        Shows the telemetry
        """
        self.__setContent(self.__tlmWindow)



    def __showVideo(self) -> None:
        self.__setContent(self.__streamWindow)


    def __showVisualizer(self) -> None:
        """Show the 3D visualization window."""
        self.__setContent(self.__vizWindow)



    def __showFirmware(self) -> None:
        """Show the firmware update panel in the central area."""
        self.__setContent(self.__fwWindow)
        
        

    def __clearLayout(self, layout):
        if layout is None:
            return

        while layout.count():
            item = layout.takeAt(0)

            widget = item.widget()
            if widget is not None:
                widget.setParent(None)   # DO NOT DELETE
                continue

            sublayout = item.layout()
            if sublayout is not None:
                self.__clearLayout(sublayout)



    def mouseMoveEvent(self, event):
        x = event.position().x()

        if x < 20:
            self.side.slideIn()
        elif x > SidePanel.PANEL_WIDTH + 40:
            self.side.startAutoHide()
