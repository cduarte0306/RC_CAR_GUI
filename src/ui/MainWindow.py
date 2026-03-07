import json
from PyQt6.QtCore import (
    QSize, QPropertyAnimation, QRect, QRectF, QEasingCurve, Qt, QTimer, pyqtSignal, QPoint
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton, QGraphicsOpacityEffect,
    QLabel, QFrame, QGraphicsBlurEffect, QHBoxLayout, QFileDialog, QMessageBox, QGridLayout,
    QSizePolicy, QToolTip, QToolButton, QMenu
)
from PyQt6.QtGui import QIcon, QFont, QPixmap, QPainter, QColor, QPen, QCursor, QAction

from ui.TelemetryWindow import VehicleTelemetryWindow
from ui.VideoStreamingWindow import VideoStreamingWindow
from ui.UIConsumer import BackendIface
from ui.FirmwareUpdateWindow import FirmwareUpdateWindow
from ui.theme import make_card
from network.interfaces import list_ipv4_interfaces, NetworkInterfaceOption
import logging


class GlowButton(QPushButton):
    """Modern flat button with hover glow."""

    def __init__(self, text):
        super().__init__(text)
        self._full_text = text
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
            QPushButton[compact="true"] {
                padding-left: 0px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: rgba(0,210,255,0.16);
                border: 1px solid rgba(0,210,255,0.35);
            }
            QPushButton:pressed {
                background-color: rgba(0,210,255,0.24);
            }
        """)
        self.setCompactMode(False)

    def setCompactMode(self, compact: bool) -> None:
        if self.property("compact") == compact:
            return
        self.setProperty("compact", compact)
        self.setText("" if compact else self._full_text)
        self.style().unpolish(self)
        self.style().polish(self)


class SidePanel(QFrame):
    EXPANDED_WIDTH = 210
    COLLAPSED_WIDTH = 64

    # Signals
    showWelcome     = pyqtSignal()  # Show welcome
    showTlm         = pyqtSignal()  # Show telemetry signal
    showVideoStream = pyqtSignal()  # Show video stream

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setMinimumWidth(self.COLLAPSED_WIDTH)
        self.setMaximumWidth(self.EXPANDED_WIDTH)
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
        self._expanded = False

        # ----------------------
        # Layout & Widgets
        # ----------------------
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.setLayout(layout)

        # Header
        self._header = QLabel("Quick Nav")
        self._header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._header.setStyleSheet("color: #e8ecf3; font-size: 18px; font-weight: 700;")
        layout.addWidget(self._header)

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

        self.btnGPS = GlowButton(" GPS")
        self.btnGPS.setIcon(QIcon("icons/gps.svg"))
        self.btnGPS.setToolTip("GPS Position")

        layout.addWidget(self.btnWelcome)
        layout.addWidget(self.btnTelem)
        layout.addWidget(self.btnVideo)
        layout.addWidget(self.btnFw)
        layout.addWidget(self.btnGPS)

        layout.addStretch()

        self._buttons = [
            self.btnWelcome,
            self.btnTelem,
            self.btnVideo,
            self.btnFw,
            self.btnGPS,
        ]

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
        self._applyCompactMode(True)
        
        # Connect all signals
        self.__connectSignals()
        

    def __connectSignals(self) -> None:
        """
        Handles the connection of signals
        """
        self.btnWelcome.clicked.connect(lambda: self.showWelcome.emit())
        self.btnTelem.clicked.connect(lambda: self.showTlm.emit())
        self.btnVideo.clicked.connect(lambda: self.showVideoStream.emit())

    def _applyCompactMode(self, compact: bool) -> None:
        self._header.setVisible(not compact)
        for btn in self._buttons:
            btn.setCompactMode(compact)
        if hasattr(self, "pinButton"):
            self.pinButton.setVisible(False)
        layout = self.layout()
        if layout is not None:
            if compact:
                layout.setContentsMargins(8, 12, 8, 12)
                layout.setSpacing(8)
            else:
                layout.setContentsMargins(12, 12, 12, 12)
                layout.setSpacing(10)

    def _animateWidth(self, target_width: int) -> None:
        h = self.parent().height() if self.parent() else self.height()
        self.__anim.stop()
        self.__anim.setStartValue(QRect(0, 0, self.width(), h))
        self.__anim.setEndValue(QRect(0, 0, target_width, h))
        self.__anim.start()


    # ----------------------------
    # Slide Animations
    # ----------------------------
    def slideIn(self):
        if self._expanded:
            return
        self._expanded = True
        self.hidden = False
        self._applyCompactMode(False)
        self._animateWidth(self.EXPANDED_WIDTH)


    def slideOut(self):
        if not self._expanded or self.pinned:
            return
        self._expanded = False
        self.hidden = True
        self._applyCompactMode(True)
        self._animateWidth(self.COLLAPSED_WIDTH)

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

            
    def enterEvent(self, event):  # noqa: N802
        self.autoHideTimer.stop()
        self.slideIn()
        super().enterEvent(event)


    def leaveEvent(self, event):  # noqa: N802
        self.startAutoHide()
        super().leaveEvent(event)

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


class DeviceTile(QWidget):
    """Clickable device icon tile with an embedded NIC/adaptor selector badge."""

    def __init__(
        self,
        pixmap: QPixmap,
        tooltip: str = "",
        connect_callback=None,
        adapter_provider=None,
        selected_adapter_ip: str = "0.0.0.0",
        adapter_selected_callback=None,
        parent=None,
    ):
        super().__init__(parent)
        self._adapter_provider = adapter_provider
        self._selected_adapter_ip = (selected_adapter_ip or "0.0.0.0").strip() or "0.0.0.0"
        self._adapter_selected_callback = adapter_selected_callback

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(80, 80)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.iconLabel = ClickableLabel(pixmap, tooltip=tooltip, callback=connect_callback, parent=self)
        self.iconLabel.setFixedSize(72, 72)
        self.iconLabel.setScaledContents(True)
        layout.addWidget(self.iconLabel, 0, 0, Qt.AlignmentFlag.AlignCenter)

        self._adapterMenu = QMenu(self)
        self._adapterMenu.setStyleSheet(
            """
            QMenu {
                background-color: #0b111c;
                color: #e8ecf3;
                border: 1px solid rgba(0,210,255,0.35);
                border-radius: 10px;
                padding: 6px;
            }
            QMenu::item {
                padding: 7px 18px 7px 28px;
                border-radius: 8px;
                background: transparent;
            }
            QMenu::item:selected {
                background: rgba(0,210,255,0.18);
            }
            QMenu::item:disabled {
                color: rgba(232, 236, 243, 120);
            }
            QMenu::separator {
                height: 1px;
                background: rgba(255,255,255,0.08);
                margin: 6px 0px;
            }
            QMenu::indicator {
                width: 14px;
                height: 14px;
                left: 7px;
            }
            QMenu::indicator:checked {
                image: none;
                border: 1px solid rgba(0,210,255,0.75);
                background: rgba(0,210,255,0.30);
                border-radius: 3px;
            }
            QMenu::indicator:unchecked {
                image: none;
                border: 1px solid rgba(255,255,255,0.18);
                background: rgba(255,255,255,0.06);
                border-radius: 3px;
            }
            """
        )
        self._adapterMenu.aboutToShow.connect(self._rebuildAdapterMenu)
        self._adapterMenu.triggered.connect(self._onAdapterActionTriggered)

        self.adapterButton = QToolButton(self)
        self.adapterButton.setCursor(Qt.CursorShape.PointingHandCursor)
        self.adapterButton.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.adapterButton.setMenu(self._adapterMenu)
        self.adapterButton.setToolTip("Select video network adapter (Ethernet/NIC)")
        self.adapterButton.setStyleSheet(
            """
            QToolButton {
                background: rgba(11, 17, 28, 220);
                color: #e8ecf3;
                border: 1px solid rgba(0,210,255,0.38);
                border-radius: 8px;
                padding: 2px 6px;
                font-size: 10px;
                font-weight: 700;
            }
            QToolButton:hover {
                background: rgba(0,210,255,0.16);
                border: 1px solid rgba(0,210,255,0.55);
            }
            QToolButton::menu-indicator {
                image: none;
                width: 0px;
            }
            """
        )
        self.adapterButton.setFixedHeight(18)
        self.adapterButton.setMaximumWidth(70)
        layout.addWidget(
            self.adapterButton,
            0,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
        )

        self._updateAdapterButtonText()


    def setSelectedAdapterIp(self, adapter_ip: str) -> None:
        self._selected_adapter_ip = (adapter_ip or "0.0.0.0").strip() or "0.0.0.0"
        self._updateAdapterButtonText()


    def _formatAdapterBadge(self, adapter_ip: str, options: list[NetworkInterfaceOption]) -> tuple[str, str]:
        def short(text: str, max_len: int = 10) -> str:
            text = text or ""
            if len(text) <= max_len:
                return text
            return text[: max_len - 1] + "…"

        if not adapter_ip or adapter_ip == "0.0.0.0":
            return "Auto", "Auto (system routing)"

        match = next((opt for opt in options if opt.ip == adapter_ip), None)
        if match is None:
            return short(adapter_ip), f"Adapter IP: {adapter_ip}"

        return short(match.name), f"{match.name} ({match.ip})"


    def _updateAdapterButtonText(self) -> None:
        options: list[NetworkInterfaceOption] = []
        if callable(self._adapter_provider):
            try:
                options = self._adapter_provider()
            except Exception:
                options = []
        badge, tooltip = self._formatAdapterBadge(self._selected_adapter_ip, options)
        self.adapterButton.setText(badge)
        self.adapterButton.setToolTip(f"Video adapter: {tooltip}\nClick to change")


    def _rebuildAdapterMenu(self) -> None:
        self._adapterMenu.clear()

        options: list[NetworkInterfaceOption] = []
        if callable(self._adapter_provider):
            try:
                options = self._adapter_provider()
            except Exception:
                options = []

        auto_action = QAction("Auto (system routing)", self._adapterMenu)
        auto_action.setCheckable(True)
        auto_action.setChecked(self._selected_adapter_ip in ("", "0.0.0.0"))
        auto_action.setData("0.0.0.0")
        self._adapterMenu.addAction(auto_action)
        self._adapterMenu.addSeparator()

        if not options:
            disabled = QAction("No adapters found", self._adapterMenu)
            disabled.setEnabled(False)
            self._adapterMenu.addAction(disabled)
            return

        for opt in options:
            label = f"{opt.name} ({opt.ip})"
            action = QAction(label, self._adapterMenu)
            action.setCheckable(True)
            action.setChecked(opt.ip == self._selected_adapter_ip)
            action.setData(opt.ip)
            self._adapterMenu.addAction(action)


    def _onAdapterActionTriggered(self, action: QAction) -> None:
        try:
            adapter_ip = str(action.data() or "0.0.0.0")
        except Exception:
            adapter_ip = "0.0.0.0"

        self.setSelectedAdapterIp(adapter_ip)
        if callable(self._adapter_selected_callback):
            try:
                self._adapter_selected_callback(adapter_ip)
            except Exception:
                pass


class WelcomeWindow(QWidget):

    startRequested = pyqtSignal()  # emitted when user clicks Start

    def __init__(self, parent=None, flags=Qt.WindowType.Widget):
        super().__init__(parent, flags)
        self.setObjectName("welcome-panel")
        self._device_tiles: list[DeviceTile] = []
        self._adapter_provider = None
        self._selected_adapter_ip: str = "0.0.0.0"
        self._adapter_selected_callback = None

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


    def configureAdapterPicker(self, adapter_provider, selected_adapter_ip: str, adapter_selected_callback=None) -> None:
        self._adapter_provider = adapter_provider
        self._adapter_selected_callback = adapter_selected_callback
        self.setSelectedAdapterIp(selected_adapter_ip)


    def setSelectedAdapterIp(self, adapter_ip: str) -> None:
        self._selected_adapter_ip = (adapter_ip or "0.0.0.0").strip() or "0.0.0.0"
        for tile in self._device_tiles:
            try:
                tile.setSelectedAdapterIp(self._selected_adapter_ip)
            except Exception:
                pass

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
            if w and getattr(w, "_deviceTooltip", None) == deviceTooltip:
                return

        # Use a ClickableLabel to avoid button chrome and background artifacts
        try:
            icon = QPixmap(icon_path)
        except Exception:
            icon = QPixmap()

        tile = DeviceTile(
            icon,
            tooltip=f"{deviceTooltip}\nClick to connect",
            connect_callback=connect_callback,
            adapter_provider=self._adapter_provider,
            selected_adapter_ip=self._selected_adapter_ip,
            adapter_selected_callback=self._onAdapterSelected,
            parent=self,
        )
        tile._deviceTooltip = deviceTooltip
        tile._device_ip = deviceTooltip
        self._device_tiles.append(tile)
        self._devices_layout.addWidget(tile)


    def _onAdapterSelected(self, adapter_ip: str) -> None:
        self.setSelectedAdapterIp(adapter_ip)
        if callable(self._adapter_selected_callback):
            try:
                self._adapter_selected_callback(adapter_ip)
            except Exception:
                pass


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
        self.__firstShow = True

        # Welcome
        self.__welcomeWindow = WelcomeWindow(self)

        # Central UI
        self.central = QWidget()
        centralLayout = QVBoxLayout()
        centralLayout.setContentsMargins(24 + SidePanel.COLLAPSED_WIDTH, 24, 24, 24)
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
        self.contentFrame.setProperty("role", "main")
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

        # Side panel
        self.side = SidePanel(self)
        self.side.setGeometry(0, 0, SidePanel.COLLAPSED_WIDTH, self.height())
        self.side.raise_()
        
        # import UI consumer
        self.__consumer = BackendIface()
        self.__adapter_ip = self.__consumer.getVideoOutAdapterIp()
        self.__welcomeWindow.configureAdapterPicker(
            self.__listAdapterOptions,
            self.__adapter_ip,
            self.__onAdapterIpSelected,
        )

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


    def __listAdapterOptions(self) -> list[NetworkInterfaceOption]:
        try:
            return list_ipv4_interfaces(include_down=False)
        except Exception:
            return []


    def __onAdapterIpSelected(self, adapter_ip: str) -> None:
        self.__adapter_ip = (adapter_ip or "0.0.0.0").strip() or "0.0.0.0"
        try:
            self.__consumer.setVideoOutAdapterIp(self.__adapter_ip)
        except Exception as exc:
            logging.warning("Failed to apply adapter selection: %s", exc)


    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_MainWindow__firstShow", False):
            return
        self.__firstShow = False

        screen = self.screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return

        avail = screen.availableGeometry()

        # Size to something "big" by default, but never larger than the usable screen.
        target_w = min(avail.width(), max(self.minimumWidth(), int(avail.width() * 0.92)))
        target_h = min(avail.height(), max(self.minimumHeight(), int(avail.height() * 0.92)))
        self.resize(target_w, target_h)

        # Place slightly above center so it doesn't feel "low".
        x = avail.left() + max(0, (avail.width() - self.width()) // 2)
        y = avail.top() + max(20, int(avail.height() * 0.05))
        if y + self.height() > avail.bottom():
            y = max(avail.top() + 12, avail.bottom() - self.height() - 12)

        self.move(int(x), int(y))
        self.raise_()
        self.activateWindow()


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
        
        self.__consumer.videoBufferSignal.connect(lambda left_frame, right_frame: self.__streamWindow.updateFrame(left_frame))
        self.__consumer.videoBufferSignalStereo.connect(lambda left_frame, right_frame: self.__streamWindow.updateStereoFrame(left_frame, right_frame))
        self.__consumer.videoBufferSignalStereoMono.connect(lambda frame, gyroData: self.__streamWindow.updateFrame(frame, gyroData))
        self.__consumer.telemetryReceived.connect(lambda raw_payload : self.__routeTlm(raw_payload))
        self.__consumer.videoUploadProgress.connect(self.__updateVideoUploadProgress)
        self.__consumer.videoUploadFinished.connect(self.__streamWindow.finishUploadProgress)
        self.__consumer.notifyDisconnect.connect(self.__handleDisconnect)
        self.__consumer.controllerConnected.connect(self.__onControllerConnected)
        self.__consumer.controllerBatteryLevel.connect(self.__onControllerBatteryLevel)
        self.__consumer.controllerDisconnected.connect(self.__onControllerDisconnected)
        self.__consumer.failedToStoreVideoOnDevice.connect(self.__streamWindow.showErrorMessage)
        self.__consumer.videoStoredToDevice.connect(self.__streamWindow.showVideoSavedMessage)
        self.__consumer.videoListLoaded.connect(self.__streamWindow.updateDeviceVideoList)
        self.__consumer.paramsLoaded.connect(self.__streamWindow.updateSettingsFromParams)
        
        self.__streamWindow.stereoMonoModeChanged.connect(self.__consumer.setStereoMonoMode)
        self.__streamWindow.uploadVideoClicked.connect(self.__consumer.uploadVideoFile)
        self.__streamWindow.cameraSourceSelected.connect(self.__consumer.setCameraSource)
        self.__streamWindow.simulationSourceSelected.connect(self.__consumer.setSimulationSource)
        self.__streamWindow.fpsChanged.connect(self.__consumer.setFrameRate)
        self.__streamWindow.qualityChanged.connect(self.__consumer.setVideoQuality)
        self.__streamWindow.minDisparitiesChanged.connect(self.__consumer.setMinDisparities)
        self.__streamWindow.maxDisparitiesChanged.connect(self.__consumer.setMaxDisparities)
        self.__streamWindow.confidenceThresholdChanged.connect(self.__consumer.setConfidenceThreshold)
        self.__streamWindow.p1Changed.connect(self.__consumer.setP1)
        self.__streamWindow.p2Changed.connect(self.__consumer.setP2)
        self.__streamWindow.uniquenessRatioChanged.connect(self.__consumer.setUniquenessRatio)
        self.__streamWindow.zMaxChanged.connect(self.__consumer.setZMax)
        self.__streamWindow.zMinChanged.connect(self.__consumer.setZMin)

        self.__streamWindow.streamOutRequested.connect(self.__consumer.startVideoStream)
        self.__streamWindow.stereoCalibrationApplyRequested.connect(self.__consumer.setStereoCalibrationParams)
        self.__streamWindow.calibrationCaptureRequested.connect(self.__consumer.captureCalibrationSample)
        self.__streamWindow.calibrationPauseToggled.connect(self.__consumer.setCalibrationPaused)
        self.__streamWindow.calibrationAbortRequested.connect(self.__consumer.abortCalibrationSession)
        self.__streamWindow.calibrationResetRequested.connect(self.__consumer.resetCalibrationSamples)
        self.__streamWindow.calibrationStoreRequested.connect(self.__consumer.storeCalibrationResult)
        self.__streamWindow.saveVideoOnDevice.connect(self.__consumer.setSaveVideoOnDevice)
        self.__streamWindow.recordingStateChanged.connect(self.__consumer.setRecordingState)
        self.__streamWindow.disparityRenderModeChanged.connect(self.__consumer.setDisparityRenderMode)
        self.__streamWindow.deviceVideoLoadRequested.connect(self.__consumer.loadDeviceVideo)
        self.__streamWindow.deviceVideoDeleteRequested.connect(self.__consumer.deleteDeviceVideo)
        self.side.btnFw.clicked.connect(lambda: self.__showFirmware())


    def __routeTlm(self, raw_payload : bytes) -> None:
        """
        Route telemetry data to appropriate windows
        """
        
        text = raw_payload.decode("utf-8") if isinstance(raw_payload, (bytes, bytearray)) else str(raw_payload)
        try:
            outer = json.loads(text)
        except json.JSONDecodeError:
            self.__tlmWindow.updateTelemetry(raw_payload)
            return
        if not isinstance(outer, dict):
            self.__tlmWindow.updateTelemetry(raw_payload)
            return

        if outer.get("source") == "CamController":
            payload = outer.get("payload", "")
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode("utf-8", errors="ignore")
            data = payload
            if isinstance(payload, str):
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    data = {"status": payload}
            count_text = ""
            status_text = ""
            if isinstance(data, dict):
                count_text = data.get("count", "")
                status_text = data.get("status", "")
            self.__streamWindow.updateCalibrationStats(count_text, status_text)
            return

        self.__tlmWindow.updateTelemetry(raw_payload)


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
        self.__streamWindow.autoStartStreamOut()
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



