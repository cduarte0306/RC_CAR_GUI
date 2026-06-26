"""Embed the native Open3D visualizer window inside the Qt UI.

The Open3D legacy Visualizer (``open3d::visualization::Visualizer``) owns its own
top-level GLFW window. On Windows that window has a native ``HWND`` which can be
reparented into a Qt widget via ``QWindow.fromWinId()`` +
``QWidget.createWindowContainer()``.

Usage:

    view3d = Open3DEmbedWidget(backend.getRenderer3DWindowId)
    someLayout.addWidget(view3d)
    backend.renderer3DWindowOpened.connect(view3d.start)   # begin polling
    # ... and view3d.stop() when the renderer window closes.

The handle is created asynchronously after the first point-cloud frame, so this
widget polls ``window_id_provider`` on a timer until it returns a non-zero handle,
then performs the embed once.
"""
from __future__ import annotations

import logging
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QWindow
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


class Open3DEmbedWidget(QWidget):
    """Hosts the Open3D native window once its handle becomes available."""

    def __init__(self, window_id_provider, parent: QWidget | None = None, poll_interval_ms: int = 200) -> None:
        """
        Args:
            window_id_provider: zero-arg callable returning the native window
                handle (int). Returns 0 until the window exists.
            poll_interval_ms: how often to poll for the handle.
        """
        super().__init__(parent)
        self.__provider = window_id_provider
        self.__container: QWidget | None = None
        self.__foreign: QWindow | None = None
        self.__embedded_id: int = 0

        self.__layout = QVBoxLayout(self)
        self.__layout.setContentsMargins(0, 0, 0, 0)

        self.__placeholder = QLabel("3D view will appear here once streaming starts…", self)
        self.__placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.__placeholder.setWordWrap(True)
        self.__layout.addWidget(self.__placeholder)

        self.__timer = QTimer(self)
        self.__timer.setInterval(poll_interval_ms)
        self.__timer.timeout.connect(self.__tryEmbed)

    def start(self) -> None:
        """Begin polling for the native window handle and embed when ready."""
        if not self.__timer.isActive():
            self.__timer.start()

    def stop(self) -> None:
        """Stop polling and release the embedded window (without destroying it)."""
        self.__timer.stop()
        self.__clearContainer()
        self.__placeholder.show()

    def __tryEmbed(self) -> None:
        if sys.platform != "win32":
            # createWindowContainer + fromWinId reparenting is implemented for
            # the Win32 HWND path only; other platforms need their own handle type.
            self.__timer.stop()
            logging.warning("Open3D window embedding is only supported on Windows")
            return

        try:
            win_id = int(self.__provider() or 0)
        except Exception as exc:
            logging.debug("Open3D window id provider raised: %s", exc)
            return

        if win_id == 0 or win_id == self.__embedded_id:
            return  # not ready yet, or already embedded this handle

        # A reopened renderer yields a new HWND, so drop any stale container first.
        self.__clearContainer()

        foreign = QWindow.fromWinId(win_id)
        if foreign is None:
            logging.warning("QWindow.fromWinId returned None for handle %s", win_id)
            return

        container = QWidget.createWindowContainer(foreign, self)
        container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.__foreign = foreign
        self.__container = container
        self.__embedded_id = win_id
        self.__placeholder.hide()
        self.__layout.addWidget(container)
        logging.info("Embedded Open3D window (HWND=%s)", win_id)

    def __clearContainer(self) -> None:
        if self.__container is not None:
            self.__layout.removeWidget(self.__container)
            self.__container.deleteLater()
            self.__container = None
        self.__foreign = None
        self.__embedded_id = 0
