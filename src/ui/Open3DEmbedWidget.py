"""Embed the Open3D point-cloud window inside the Qt UI, driven on the GUI thread.

The legacy Open3D ``Visualizer`` owns a GLFW window with its own event loop.
Embedding it reliably on Windows requires that the window be **created and pumped
on the same thread that owns the Qt UI** — otherwise ``SetParent`` attaches the
input queues of two threads and any stall freezes the whole GUI (and GLFW keeps
popping the window back out).

So this widget:
  1. calls ``renderer.start_window()`` on the GUI thread (window owned by it),
  2. ``renderer.embed_into(host.winId())`` to reparent it as a WS_CHILD,
  3. ticks ``renderer.pump()`` from a ``QTimer`` on the GUI thread.

The backend keeps pushing point data into the renderer from its own thread; only
the window lifecycle lives here.

Usage:
    view3d = Open3DEmbedWidget(backend.getRenderer3D)
    layout.addWidget(view3d)
    # show 3D:  view3d.start()
    # hide 3D:  view3d.stop()
"""
from __future__ import annotations

import logging
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


class Open3DEmbedWidget(QWidget):
    """Hosts the Open3D window as a native child, pumped on the GUI thread."""

    def __init__(self, renderer_provider, parent: QWidget | None = None, fps: int = 60) -> None:
        """
        Args:
            renderer_provider: zero-arg callable returning the C++ Renderer3D
                object (or None if the native module is unavailable).
            fps: render pump rate (frames per second).
        """
        super().__init__(parent)
        self.__provider = renderer_provider
        self.__renderer = None
        self.__embedded = False

        self.__layout = QVBoxLayout(self)
        self.__layout.setContentsMargins(0, 0, 0, 0)

        # Native host widget the Open3D child window reparents into. Forcing a
        # native window gives it a real HWND to pass to embed_into().
        self.__host = QWidget(self)
        self.__host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.__host.setStyleSheet("background-color: #05080d; border-radius: 14px;")
        self.__host.setMinimumHeight(360)
        self.__host.hide()
        self.__layout.addWidget(self.__host)

        self.__placeholder = QLabel("3D view will appear here once streaming starts…", self)
        self.__placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.__placeholder.setWordWrap(True)
        self.__placeholder.setStyleSheet(
            "background-color: #05080d; border: 1px solid rgba(255,255,255,0.08);"
            " border-radius: 14px; color: rgba(232,236,243,0.6);"
        )
        self.__placeholder.setMinimumHeight(360)
        self.__layout.addWidget(self.__placeholder)

        self.__timer = QTimer(self)
        self.__timer.setInterval(max(1, int(1000 / max(1, fps))))
        self.__timer.timeout.connect(self.__pump)

    def start(self) -> None:
        """Create + embed the Open3D window and begin pumping it (GUI thread)."""
        if sys.platform != "win32":
            logging.warning("3D window embedding is only supported on Windows")
            return
        if self.__embedded:
            self.__timer.start()
            return

        try:
            renderer = self.__provider()
        except Exception as exc:
            logging.error("3D renderer provider failed: %s", exc)
            renderer = None
        if renderer is None:
            logging.warning("3D renderer unavailable (native module not built?)")
            return

        try:
            if not renderer.start_window():
                logging.error("Failed to create Open3D window")
                return
            renderer.embed_into(int(self.__host.winId()))
        except Exception as exc:
            logging.error("Failed to start/embed Open3D window: %s", exc)
            try:
                renderer.stop_window()
            except Exception:
                pass
            return

        self.__renderer = renderer
        self.__embedded = True
        self.__placeholder.hide()
        self.__host.show()
        self.__timer.start()
        logging.info("Open3D window embedded and pumping")

    def stop(self) -> None:
        """Stop pumping and destroy the Open3D window (GUI thread)."""
        self.__timer.stop()
        if self.__renderer is not None and self.__embedded:
            try:
                self.__renderer.stop_window()
            except Exception as exc:
                logging.debug("stop_window failed: %s", exc)
        self.__embedded = False
        self.__renderer = None
        self.__host.hide()
        self.__placeholder.show()

    def __pump(self) -> None:
        if self.__renderer is None:
            self.__timer.stop()
            return
        try:
            alive = self.__renderer.pump()
        except Exception as exc:
            logging.error("Open3D pump failed: %s", exc)
            alive = False
        if not alive:
            # Window was closed/destroyed; tear down cleanly.
            self.stop()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Re-fill the host area so the child window tracks the container size.
        if self.__renderer is not None and self.__embedded:
            try:
                self.__renderer.embed_into(int(self.__host.winId()))
            except Exception:
                pass
