from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget, QFrame
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

from ui.theme import make_card
from utils import cpp_extensions


class _CubeView(QOpenGLWidget):
    """
    Lightweight QOpenGLWidget that delegates drawing to the C++ Renderer3D.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._renderer = cpp_extensions.create_renderer3d((0.05, 0.09, 0.14, 1.0))
        self._angle_x = 18.0
        self._angle_y = 32.0
        self._distance = 4.2
        self._spin_enabled = True

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def toggle_spin(self):
        self._spin_enabled = not self._spin_enabled
        if self._spin_enabled:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def is_spinning(self) -> bool:
        return self._spin_enabled

    def _tick(self):
        self._angle_y += 0.8
        self._angle_x += 0.35
        self.update()

    def paintGL(self):  # noqa: N802
        if self._renderer is None:
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(10, 17, 28))
            painter.setPen(QColor(200, 210, 220))
            painter.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "C++ renderer unavailable")
            painter.end()
            return

        self._renderer.render_cube(
            self._angle_x,
            self._angle_y,
            self._distance,
            self.width(),
            self.height(),
        )

    def resizeGL(self, width, height):  # noqa: N802
        # Trigger a repaint at the new size
        _ = (width, height)
        self.update()


class VisualizationWindow(QWidget):
    """
    Simple container that hosts the OpenGL view plus small controls.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        header = QLabel("3D Vehicle View")
        header.setObjectName("header-title")
        header.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStyleSheet("padding-left: 6px;")

        desc = QLabel(
            "Rendered via the C++ OpenGL backend. A simple cube stands in for the vehicle model."
        )
        desc.setStyleSheet("color: #9ba7b4;")
        desc.setWordWrap(True)

        card = QFrame()
        make_card(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(10)

        self._view = _CubeView(card)
        self._view.setMinimumHeight(360)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)

        self._status = QLabel()
        self._status.setStyleSheet("color: #c4ccd8;")
        self._update_status()

        self._toggle_btn = QPushButton("Pause rotation")
        self._toggle_btn.setFixedHeight(34)
        self._toggle_btn.clicked.connect(self._toggle_spin)

        controls.addWidget(self._status, 1)
        controls.addWidget(self._toggle_btn, 0, Qt.AlignmentFlag.AlignRight)

        card_layout.addWidget(self._view, 1)
        card_layout.addLayout(controls)

        outer.addWidget(header)
        outer.addWidget(desc)
        outer.addWidget(card, 1)

    def _toggle_spin(self):
        self._view.toggle_spin()
        self._toggle_btn.setText("Resume rotation" if not self._view.is_spinning() else "Pause rotation")

    def _update_status(self):
        if cpp_extensions.is_cpp_available():
            self._status.setText("C++ renderer active (OpenGL)")
        else:
            self._status.setText("C++ renderer unavailable - showing fallback view")
