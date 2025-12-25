from PyQt6.QtCore import (
    QSize, QPropertyAnimation, QRect, QEasingCurve, Qt, QTimer, QRectF, pyqtProperty
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton,
    QLabel, QFrame, QGraphicsBlurEffect, QHBoxLayout, QTabWidget
)
from PyQt6.QtGui import QIcon, QPainter, QColor, QPixmap, QPen, QFont
from ui.theme import make_card

import math
import json


class ProximityVisualizer(QWidget):
    def __init__(self, car_image_path="icons/car_top.png"):
        super().__init__()

        # distances in cm — set these from your sensor data
        self.front_dist = 100
        self.back_dist = 100
        self.left_dist = 100
        self.right_dist = 100

        self.max_dist = 120   # cm max arc length
        self.arc_width = 28   # thickness of arcs

        self.car_img = QPixmap(car_image_path)
        self.setMinimumSize(350, 350)


    def setDistances(self, front, back, left, right):
        self.front_dist = front
        self.back_dist = back
        self.left_dist = left
        self.right_dist = right
        self.update()


    def distanceColor(self, dist):
        """Green → Yellow → Red depending on distance."""
        if dist > 80:
            return QColor(0, 255, 0, 160)
        elif dist > 40:
            return QColor(255, 255, 0, 180)
        else:
            return QColor(255, 0, 0, 200)


    def drawArc(self, painter, cx, cy, angle_deg, dist):
        """Draw a parking-style curved arc in the given direction."""
        color = self.distanceColor(dist)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)

        # scale arc length
        length = max(0, min(dist, self.max_dist))
        arc_radius = 60 + (self.max_dist - length)  # shorter distance = closer arc

        # arc sector shape
        arc_rect = QRectF(cx - arc_radius, cy - arc_radius,
                          arc_radius * 2, arc_radius * 2)

        span_angle = 60 * 16   # 60° arc
        start_angle = (angle_deg - 30) * 16  # center on angle

        painter.drawPie(arc_rect, start_angle, span_angle)


    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2

        # ------------------------------
        # Draw car image in center
        # ------------------------------
        car_scaled = self.car_img.scaled(
            120, 240,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )

        car_rect = QRectF(
            cx - car_scaled.width() / 2,
            cy - car_scaled.height() / 2,
            car_scaled.width(),
            car_scaled.height()
        )

        # FIX: QRectF → QRect
        p.drawPixmap(car_rect.toRect(), car_scaled, car_scaled.rect())

        # ------------------------------
        # Draw parking-style arcs
        # ------------------------------

        # front sensor arc (angle_deg=90)
        self.drawArc(p, cx, car_rect.top(), angle_deg=90, dist=self.front_dist)

        # back arc (angle_deg=-90)
        self.drawArc(p, cx, car_rect.bottom(), angle_deg=-90, dist=self.back_dist)

        # left side arc (angle_deg=180)
        self.drawArc(p, car_rect.left(), cy, angle_deg=180, dist=self.left_dist)

        # right arc (angle_deg=0)
        self.drawArc(p, car_rect.right(), cy, angle_deg=0, dist=self.right_dist)


class OdometerGauge(QWidget):
    def __init__(self):
        super().__init__()
        self._speed = 0          # current needle value
        self.max_speed = 50      # adjust for your RC car
        self.setMinimumSize(200, 200)

        # Smooth animation object
        self.anim = QPropertyAnimation(self, b"speed")
        self.anim.setDuration(350)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

    # property for animation
    def getSpeed(self): return self._speed


    def setSpeed(self, value):
        self._speed = value
        self.update()

    speed = pyqtProperty(int, fget=getSpeed, fset=setSpeed)


    def animateTo(self, value):
        self.anim.stop()
        self.anim.setStartValue(self._speed)
        self.anim.setEndValue(value)
        self.anim.start()


    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        r = min(w, h) // 2 - 10
        cx, cy = w//2, h//2

        # Draw outer arc
        arc_pen = QPen(QColor(200, 200, 200), 6)
        p.setPen(arc_pen)
        p.drawArc(cx - r, cy - r, r*2, r*2, 45*16, 270*16)

        # Ticks
        p.setPen(QPen(QColor(180, 180, 180), 2))
        for i in range(0, self.max_speed+1, 5):
            angle = 45 + (i / self.max_speed) * 270
            rad = math.radians(angle)
            x1 = cx + (r - 10) * math.cos(rad)
            y1 = cy - (r - 10) * math.sin(rad)
            x2 = cx + (r - 22) * math.cos(rad)
            y2 = cy - (r - 22) * math.sin(rad)
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Needle
        angle = 45 + (self._speed / self.max_speed) * 270
        rad = math.radians(angle)
        x = cx + (r - 30) * math.cos(rad)
        y = cy - (r - 30) * math.sin(rad)

        p.setPen(QPen(Qt.GlobalColor.red, 4))
        p.drawLine(cx, cy, int(x), int(y))

        # Speed label
        p.setPen(QColor(255,255,255))
        p.setFont(QFont("Arial", 20))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"{int(self._speed)}")



class AccelVisualizer(QWidget):
    """2D accelerometer vector display (X/Y) with smooth animation."""
    def __init__(self):
        super().__init__()
        self._cur_x = 0.0
        self._cur_y = 0.0
        self._tgt_x = 0.0
        self._tgt_y = 0.0
        self._cur_z = 0.0
        self._tgt_z = 0.0
        self.setMinimumSize(220, 220)

        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._step)
        self._timer.start()

        self._smoothing = 0.15

    def setAccel(self, ax, ay, az=0.0):
        """Call with acceleration in g or m/s^2 depending on your feed."""
        self._tgt_x = float(ax)
        self._tgt_y = float(ay)
        self._tgt_z = float(az)

    def _step(self):
        # simple exponential smoothing towards target
        self._cur_x += (self._tgt_x - self._cur_x) * self._smoothing
        self._cur_y += (self._tgt_y - self._cur_y) * self._smoothing
        self._cur_z += (self._tgt_z - self._cur_z) * self._smoothing
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 12

        # background
        p.fillRect(self.rect(), QColor(12, 17, 23))

        # outer ring
        p.setPen(QPen(QColor(80, 160, 255), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # grid lines
        p.setPen(QPen(QColor(38, 64, 92), 1))
        p.drawLine(cx - r, cy, cx + r, cy)
        p.drawLine(cx, cy - r, cx, cy + r)

        # acceleration vector
        # scale display so that +/-2 (g) fits near the edge
        scale = r / 2.5
        ax = max(-2.0, min(2.0, self._cur_x))
        ay = max(-2.0, min(2.0, self._cur_y))

        vx = int(cx + ax * scale)
        vy = int(cy - ay * scale)

        # arrow
        p.setPen(QPen(QColor(0, 210, 255), 4))
        p.drawLine(cx, cy, vx, vy)

        # head
        p.setBrush(QColor(0, 210, 255))
        p.drawEllipse(vx - 6, vy - 6, 12, 12)

        # magnitude label
        mag = math.sqrt(self._cur_x ** 2 + self._cur_y ** 2 + self._cur_z ** 2)
        p.setPen(QColor(220, 220, 220))
        p.setFont(QFont("Segoe UI", 10))
        p.drawText(self.rect().adjusted(8, 8, -8, -8), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                   f"Accel: {mag:.2f}")


class GyroVisualizer(QWidget):
    """Simple gyroscope / orientation display. Shows yaw compass and roll/pitch bars."""
    def __init__(self):
        super().__init__()
        self._roll = 0.0
        self._pitch = 0.0
        self._yaw = 0.0

        self._troll = 0.0
        self._tpitch = 0.0
        self._tyaw = 0.0

        self.setMinimumSize(220, 220)

        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._step)
        self._timer.start()

        self._smoothing = 0.12


    def setGyro(self, roll, pitch, yaw):
        self._troll = float(roll)
        self._tpitch = float(pitch)
        self._tyaw = float(yaw)


    def _step(self):
        self._roll += (self._troll - self._roll) * self._smoothing
        self._pitch += (self._tpitch - self._pitch) * self._smoothing
        # yaw wrap-safe lerp
        dy = (self._tyaw - self._yaw + 180) % 360 - 180
        self._yaw = (self._yaw + dy * self._smoothing) % 360
        self.update()


    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 12

        # background
        p.fillRect(self.rect(), QColor(12, 17, 23))

        # yaw compass
        p.setPen(QPen(QColor(80, 160, 255), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # draw yaw needle
        angle = -self._yaw + 90
        rad = math.radians(angle)
        nx = int(cx + (r - 18) * math.cos(rad))
        ny = int(cy - (r - 18) * math.sin(rad))

        p.setPen(QPen(QColor(255, 180, 60), 4))
        p.drawLine(cx, cy, nx, ny)
        p.setBrush(QColor(255, 180, 60))
        p.drawEllipse(nx - 6, ny - 6, 12, 12)

        # roll / pitch bars
        bar_w = r * 1.2
        # roll (horizontal bar)
        rx = cx - bar_w // 2
        ry = cy + r + 8
        p.setPen(QPen(QColor(50, 80, 110), 2))
        p.drawRect(int(rx), int(ry), int(bar_w), 10)

        roll_pos = int((self._roll / 180.0) * (bar_w - 4))
        p.fillRect(int(rx + 2 + roll_pos), int(ry + 1), 6, 8, QColor(0, 210, 255))

        # pitch (vertical bar)
        px = cx + r + 8
        py = cy - bar_w // 2
        p.drawRect(int(px), int(py), 10, int(bar_w))
        pitch_pos = int((self._pitch / 90.0) * (bar_w - 4))
        p.fillRect(int(px + 1), int(py + 2 + pitch_pos), 8, 6, QColor(54, 224, 184))

        # numeric readouts
        p.setPen(QColor(220, 220, 220))
        p.setFont(QFont("Segoe UI", 10))
        p.drawText(8, 18, f"Roll: {self._roll:.1f}°")
        p.drawText(8, 34, f"Pitch: {self._pitch:.1f}°")
        p.drawText(8, 50, f"Yaw: {self._yaw:.1f}°")


class TrajectoryMap(QWidget):
    """Simple top-down traversal plot relative to session origin."""

    def __init__(self):
        super().__init__()
        self.points = [(0.0, 0.0)]
        self.heading_deg = 0.0
        self.setMinimumSize(360, 360)
        self._max_points = 800
        self._margin = 32

    def resetOrigin(self):
        """Clear the path and reset origin to (0,0)."""
        self.points = [(0.0, 0.0)]
        self.heading_deg = 0.0
        self.update()

    def setPose(self, x: float, y: float, heading_deg: float = 0.0):
        """Append an absolute pose in meters relative to origin."""
        self.points.append((float(x), float(y)))
        self.heading_deg = float(heading_deg)
        if len(self.points) > self._max_points:
            self.points = self.points[-self._max_points :]
        self.update()

    def addDisplacement(self, dx: float, dy: float, heading_deg: float | None = None):
        """Append a delta step (dx, dy) from the last point."""
        last_x, last_y = self.points[-1] if self.points else (0.0, 0.0)
        hdg = self.heading_deg if heading_deg is None else float(heading_deg)
        self.setPose(last_x + float(dx), last_y + float(dy), hdg)

    def _computeTransform(self, w: int, h: int):
        if not self.points:
            return 1.0, 0.0, 0.0

        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        span = max(max_x - min_x, max_y - min_y, 1e-3)
        usable = min(w, h) - self._margin * 2
        scale = usable / span if span > 0 else 1.0

        mid_x = (min_x + max_x) / 2.0
        mid_y = (min_y + max_y) / 2.0
        return scale, mid_x, mid_y

    def _mapPoint(self, x: float, y: float, w: int, h: int, scale: float, mid_x: float, mid_y: float):
        cx, cy = w / 2.0, h / 2.0
        px = cx + (x - mid_x) * scale
        py = cy - (y - mid_y) * scale
        return px, py

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(12, 17, 23))

        scale, mid_x, mid_y = self._computeTransform(w, h)
        origin_px, origin_py = self._mapPoint(0.0, 0.0, w, h, scale, mid_x, mid_y)

        # grid
        p.setPen(QPen(QColor(30, 44, 60), 1))
        step_px = 60
        x = origin_px % step_px
        while x < w:
            p.drawLine(int(x), 0, int(x), h)
            x += step_px
        y = origin_py % step_px
        while y < h:
            p.drawLine(0, int(y), w, int(y))
            y += step_px

        # axes
        p.setPen(QPen(QColor(0, 210, 255), 2))
        p.drawLine(0, int(origin_py), w, int(origin_py))
        p.drawLine(int(origin_px), 0, int(origin_px), h)

        if len(self.points) >= 2:
            path_pen = QPen(QColor(84, 196, 255), 3)
            path_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            path_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(path_pen)
            for i in range(1, len(self.points)):
                x1, y1 = self._mapPoint(*self.points[i - 1], w, h, scale, mid_x, mid_y)
                x2, y2 = self._mapPoint(*self.points[i], w, h, scale, mid_x, mid_y)
                p.drawLine(int(x1), int(y1), int(x2), int(y2))

        # current position marker
        cur_x, cur_y = self.points[-1]
        px, py = self._mapPoint(cur_x, cur_y, w, h, scale, mid_x, mid_y)
        p.setBrush(QColor(255, 180, 60))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(px) - 7, int(py) - 7, 14, 14)

        # heading arrow
        arrow_len = 28
        rad = math.radians(self.heading_deg)
        hx = px + arrow_len * math.cos(rad)
        hy = py - arrow_len * math.sin(rad)
        p.setPen(QPen(QColor(255, 180, 60), 3))
        p.drawLine(int(px), int(py), int(hx), int(hy))

        # legend
        p.setPen(QColor(220, 220, 220))
        p.setFont(QFont("Segoe UI", 10))
        p.drawText(10, 20, f"Pose: ({cur_x:.2f}, {cur_y:.2f}) m")
        p.drawText(10, 36, f"Heading: {self.heading_deg:.1f}°")


class VehicleTelemetryWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        root = QVBoxLayout()
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        self.setLayout(root)

        header = QHBoxLayout()
        title = QLabel("Telemetry")
        title.setObjectName("card-title")
        subtitle = QLabel("HUD view · Live sensors")
        subtitle.setStyleSheet("color: #9ba7b4; font-size: 12px;")
        titleColumn = QVBoxLayout()
        titleColumn.addWidget(title)
        titleColumn.addWidget(subtitle)
        header.addLayout(titleColumn)
        header.addStretch()

        self.statusChip = QLabel("Idle")
        self.statusChip.setObjectName("status-chip")
        self.statusChip.setProperty("state", "idle")
        header.addWidget(self.statusChip)

        root.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setTabBarAutoHide(False)
        root.addWidget(self.tabs)

        dashboardTab = QWidget()
        body = QHBoxLayout()
        body.setSpacing(12)
        dashboardTab.setLayout(body)
        self.tabs.addTab(dashboardTab, "Dashboard")

        # Left: proximity HUD card
        proxCard = QFrame()
        make_card(proxCard)
        proxLayout = QVBoxLayout()
        proxLayout.setContentsMargins(16, 16, 16, 16)
        proxLayout.setSpacing(8)
        proxTitle = QLabel("Surround")
        proxTitle.setObjectName("card-title")
        proxSubtitle = QLabel("Ultrasonic range")
        proxSubtitle.setStyleSheet("color: #9ba7b4; font-size: 12px;")
        proxLayout.addWidget(proxTitle)
        proxLayout.addWidget(proxSubtitle)
        self.proximity = ProximityVisualizer("icons/car_top.png")
        proxLayout.addWidget(self.proximity, stretch=1)
        proxCard.setLayout(proxLayout)
        body.addWidget(proxCard, stretch=3)

        # Right column: speed + sensors
        rightCol = QVBoxLayout()
        rightCol.setSpacing(12)

        speedCard = QFrame()
        make_card(speedCard)
        speedLayout = QVBoxLayout()
        speedLayout.setContentsMargins(16, 16, 16, 16)
        speedLayout.setSpacing(8)
        speedTitle = QLabel("Velocity")
        speedTitle.setObjectName("card-title")
        speedRow = QHBoxLayout()
        speedRow.addWidget(speedTitle)
        speedRow.addStretch()
        self.speedLabel = QLabel("0 km/h")
        self.speedLabel.setStyleSheet("color: #e8ecf3; font-size: 16px; font-weight: 700;")
        speedRow.addWidget(self.speedLabel)
        speedLayout.addLayout(speedRow)
        self.odometer = OdometerGauge()
        speedLayout.addWidget(self.odometer, stretch=1)
        speedCard.setLayout(speedLayout)
        rightCol.addWidget(speedCard, stretch=1)

        sensorsCard = QFrame()
        make_card(sensorsCard)
        sensorsLayout = QVBoxLayout()
        sensorsLayout.setContentsMargins(16, 16, 16, 16)
        sensorsLayout.setSpacing(10)
        sensorsHeader = QHBoxLayout()
        sensorsTitle = QLabel("Dynamics")
        sensorsTitle.setObjectName("card-title")
        sensorsHeader.addWidget(sensorsTitle)
        sensorsHeader.addStretch()
        sensorsLayout.addLayout(sensorsHeader)

        sensorsRow = QHBoxLayout()
        sensorsRow.setSpacing(10)

        self.accelVis = None
        self.gyroVis = None
        try:
            self.accelVis = AccelVisualizer()
            self.gyroVis = GyroVisualizer()
        except NameError:
            pass

        if self.accelVis is not None:
            sensorsRow.addWidget(self.accelVis, stretch=1)
        if self.gyroVis is not None:
            sensorsRow.addWidget(self.gyroVis, stretch=1)
        sensorsLayout.addLayout(sensorsRow)

        # status row under sensors
        self.info_label = QLabel("Live telemetry ready")
        self.info_label.setStyleSheet("color: #9ba7b4; font-size: 12px;")
        sensorsLayout.addWidget(self.info_label)

        sensorsCard.setLayout(sensorsLayout)
        rightCol.addWidget(sensorsCard, stretch=1)

        body.addLayout(rightCol, stretch=2)

        # Trajectory tab
        mapTab = QWidget()
        mapLayout = QVBoxLayout()
        mapLayout.setContentsMargins(12, 12, 12, 12)
        mapLayout.setSpacing(12)

        mapCard = QFrame()
        make_card(mapCard)
        mapCardLayout = QVBoxLayout()
        mapCardLayout.setContentsMargins(16, 16, 16, 16)
        mapCardLayout.setSpacing(8)

        mapTitleRow = QHBoxLayout()
        mapTitle = QLabel("Trajectory")
        mapTitle.setObjectName("card-title")
        mapSubtitle = QLabel("Zeroed at session start; path updates from IMU")
        mapSubtitle.setStyleSheet("color: #9ba7b4; font-size: 12px;")
        mapTitleRow.addWidget(mapTitle)
        mapTitleRow.addStretch()
        mapCardLayout.addLayout(mapTitleRow)
        mapCardLayout.addWidget(mapSubtitle)

        self.traversal = TrajectoryMap()
        mapCardLayout.addWidget(self.traversal, stretch=1)

        mapCard.setLayout(mapCardLayout)
        mapLayout.addWidget(mapCard, stretch=1)

        mapTab.setLayout(mapLayout)
        self.tabs.addTab(mapTab, "Trajectory")


    def updateTelemetry(self, raw_payload):
        """Decode telemetry bytes/str into UI elements."""
        try:
            text = raw_payload.decode("utf-8") if isinstance(raw_payload, (bytes, bytearray)) else str(raw_payload)
            outer = json.loads(text)
            payload_str = outer.get("payload", text)
            data = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
        except Exception:
            return

        try:
            front = float(data.get("frontDistance", self.proximity.front_dist))
            left = float(data.get("leftDistance", self.proximity.left_dist))
            right = float(data.get("rightDistance", self.proximity.right_dist))
            back = float(data.get("backDistance", self.proximity.back_dist if hasattr(self.proximity, "back_dist") else front))
            self.proximity.setDistances(front, back, left, right)
        except Exception:
            pass

        try:
            spd = float(data.get("speed", 0.0))
            self.speedLabel.setText(f"{spd:.0f} km/h")
            if hasattr(self, "odometer"):
                self.odometer.animateTo(int(spd))
        except Exception:
            pass

        try:
            vb = data.get("version_build")
            vmaj = data.get("version_major")
            vmin = data.get("version_minor")
            if vb is not None and vmaj is not None and vmin is not None:
                self.info_label.setText(f"Telemetry v{vmaj}.{vmin}.{vb}")
        except Exception:
            pass

    def setTraversalPose(self, x: float, y: float, heading_deg: float = 0.0):
        """Update trajectory plot with an absolute pose."""
        self.traversal.setPose(x, y, heading_deg)

    def addTraversalDelta(self, dx: float, dy: float, heading_deg: float | None = None):
        """Update trajectory plot with a delta step."""
        self.traversal.addDisplacement(dx, dy, heading_deg)

    def resetTraversal(self):
        """Reset traversal to origin."""
        self.traversal.resetOrigin()
        
