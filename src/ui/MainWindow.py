from PyQt6.QtCore import (
    QSize, QPropertyAnimation, QRect, QEasingCurve, Qt, QTimer, pyqtSignal
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton, QGraphicsOpacityEffect,
    QLabel, QFrame, QGraphicsBlurEffect, QHBoxLayout
)
from PyQt6.QtGui import QIcon, QFont, QPixmap

from ui.TelemetryWindow import VehicleTelemetryWindow
from ui.VideoStreamingWindow import VideoStreamingWindow
from ui.UIConsumer import BackendIface
from ui.FirmwareUpdateWindow import FirmwareUpdateWindow
from ui.theme import make_card


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
        self.autoHideTimer.setInterval(2000)  # 2s after no mouse → hide
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

        self.btnFw    = GlowButton(" Firmware")
        self.btnFw.setIcon(QIcon("icons/upgrade.svg"))
        self.btnFw.setToolTip("Upload Firmware")
        self.btnFw.setIconSize(QSize(24, 24))

        self.btnGPS = GlowButton("GPS")
        self.btnGPS.setToolTip("GPS Position")

        layout.addWidget(self.btnWelcome)
        layout.addWidget(self.btnTelem)
        layout.addWidget(self.btnVideo)
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

        # Make panel look premium
        self.setStyleSheet("""
            QWidget {
                background-color: rgba(12, 17, 23, 210);
                border-radius: 18px;
                border: 1px solid rgba(255,255,255,0.08);
            }
            QLabel {
                color: #e8ecf3;
                background: transparent;
            }
            QLabel#title {
                color: #e8ecf3;
                font-weight: 700;
            }
            QPushButton {
                background-color: #00d2ff;
                color: #0a1116;
                border-radius: 12px;
                padding: 12px 22px;
                font-size: 16px;
                font-weight: 700;
                border: none;
            }
            QPushButton:hover {
                background-color: #24dcff;
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


    def addDevice(self, ip: str, icon_path: str, connect_callback=None):
        """Add a discovered device icon to the welcome panel.

        - `ip` is the device IP shown on hover
        - `icon_path` is the path to the image to display
        - `connect_callback` is called when the user clicks the icon
        """
        # Avoid duplicates
        for i in range(self._devices_layout.count()):
            w = self._devices_layout.itemAt(i).widget()
            if w and getattr(w, "_device_ip", None) == ip:
                return

        # Use a ClickableLabel to avoid button chrome and background artifacts
        try:
            icon = QPixmap(icon_path)
        except Exception:
            icon = QPixmap()

        lbl = ClickableLabel(icon, tooltip=f"{ip}\nClick to connect", callback=connect_callback)
        lbl._device_ip = ip
        lbl.setFixedSize(72, 72)
        lbl.setScaledContents(True)
        self._devices_layout.addWidget(lbl)


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

        headerRow = QHBoxLayout()
        headerRow.setSpacing(10)

        self.__titleLabel = QLabel("RC Control Studio")
        self.__titleLabel.setObjectName("header-title")

        self.__statusChip = QLabel("Idle")
        self.__statusChip.setObjectName("status-chip")
        self.__statusChip.setProperty("state", "idle")
        self.__statusChip.setWordWrap(True)
        self.__setStatusChip("Idle", "idle")

        headerRow.addWidget(self.__titleLabel)
        headerRow.addStretch()
        headerRow.addWidget(self.__statusChip)

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
        self.central.setLayout(centralLayout)
        self.setCentralWidget(self.central)
        
        # Telemetry window
        self.__tlmWindow    = VehicleTelemetryWindow(self)
        self.__streamWindow = VideoStreamingWindow()
        self.__fwWindow     = FirmwareUpdateWindow(self)

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
        self.__welcomeWindow.startRequested.connect(self.__onDiscoveryStart)
        # When a device is discovered, show it on the welcome window
        self.__consumer.deviceDiscovered.connect(self.__onDeviceDiscovered)
        # When UI requests connection to a device, backend will emit deviceConnected
        self.__consumer.deviceConnected.connect(self.__onDeviceConnected)
        self.__consumer.deviceMacResolved.connect(self.__onDeviceMacResolved)
        
        self.__consumer.videoBufferSignal.connect(lambda frame : self.__streamWindow.updateFrame(frame))
        self.__consumer.telemetryReceived.connect(lambda tlm : self.__tlmWindow.updateTelemetry(tlm))
        self.__consumer.videoUploadProgress.connect(self.__updateVideoUploadProgress)
        
        self.__streamWindow.startStreamOut.connect(lambda state, fileName : self.__consumer.setStreamMode(state))
        self.__streamWindow.viewModeChanged.connect(self.__consumer.setVideoMode)
        self.__streamWindow.uploadVideoClicked.connect(self.__consumer.uploadVideoFile)
        self.side.btnFw.clicked.connect(lambda: self.__showFirmware())


    def __updateVideoUploadProgress(self, sent: int, total: int) -> None:
        """
        Update video upload progress bar in the streaming window

        Args:
            sent (int): _sent bytes
            total (int): _total bytes
        """
        print("Hello")
        self.__streamWindow.updateUploadProgress(sent, total)


    def __onDeviceDiscovered(self, ip: str) -> None:
        """Add a discovered device to the welcome window with hover tooltip and click-to-connect."""
        # Use a car icon from icons/ folder
        icon_path = "icons/rc-car.png"
        self.__welcomeWindow.addDevice(ip, icon_path, connect_callback=lambda: self.__consumer.connectToDevice(ip))
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