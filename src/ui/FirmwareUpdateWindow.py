from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QFileDialog,
    QLineEdit,
    QProgressBar,
    QTextEdit,
)

from PyQt6.QtGui import QIcon, QDragEnterEvent, QDropEvent

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize


class FirmwareUpdateWindow(QWidget):
    """Front-end only firmware upgrade panel.

    Intended to be embedded in the main window (for example added to a
    stacked layout or shown in a dialog). No backend logic is included —
    the UI emits `installRequested` when the user triggers an install.
    """

    installRequested = pyqtSignal(str)


    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._timer = QTimer(self)
        self._timer.setInterval(120)

        self._file_path = ""

        self.setAcceptDrops(True)
        self.setMinimumWidth(640)

        self._build_ui()

        self._connect_signals()


    def _build_ui(self) -> None:
        """Create widgets and layout."""

        self.setStyleSheet(
            """
            QWidget#panel {
                background: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
            }

            QLabel#header {
                font-size: 20px;
                font-weight: 700;
                color: #e8ecf3;
            }

            QPushButton.primary {
                background-color: #00d2ff;
                color: #0a1116;
                padding: 10px 16px;
                border-radius: 10px;
                font-weight: 700;
                border: none;
            }

            QPushButton.secondary {
                background-color: rgba(255,255,255,0.04);
                color: #e8ecf3;
                padding: 8px 14px;
                border-radius: 10px;
                border: 1px solid rgba(255,255,255,0.12);
            }

            QProgressBar {
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 10px;
                text-align: center;
                background: rgba(255,255,255,0.06);
                color: #e8ecf3;
            }

            QProgressBar::chunk {
                background-color: #00d2ff;
                border-radius: 8px;
            }
            """
        )

        self.setObjectName("panel")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        header = QLabel("Firmware Update")
        header.setObjectName("header")

        subtitle = QLabel("Upload a firmware package and apply it to the device.")
        subtitle.setStyleSheet("color: #9ba7b4;")

        root.addWidget(header)
        root.addWidget(subtitle)

        # File selection row
        file_row = QHBoxLayout()

        self._file_edit = QLineEdit()
        self._file_edit.setPlaceholderText("Select firmware file (.bin, .hex, .uf2, .zip)")
        self._file_edit.setReadOnly(True)

        self._browse_btn = QPushButton("Browse")
        self._browse_btn.setObjectName("browse")
        self._browse_btn.setProperty("class", "secondary")
        self._browse_btn.setFixedHeight(30)

        file_row.addWidget(self._file_edit)
        file_row.addWidget(self._browse_btn)

        root.addLayout(file_row)

        # Drag/drop hint
        self._drop_hint = QLabel("Or drag and drop a firmware file here")
        self._drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop_hint.setStyleSheet("color: #9ba7b4; padding: 8px;")

        root.addWidget(self._drop_hint)

        # Changelog / notes area
        notes_label = QLabel("Release Notes")
        notes_label.setStyleSheet("font-weight: 650; color: #e8ecf3;")

        self._notes = QTextEdit()
        self._notes.setReadOnly(True)
        self._notes.setPlaceholderText("No release notes available for this package.")
        self._notes.setFixedHeight(120)

        root.addWidget(notes_label)
        root.addWidget(self._notes)

        # Progress and controls
        progress_row = QHBoxLayout()

        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFixedHeight(22)

        controls = QVBoxLayout()

        btn_row = QHBoxLayout()

        self._start_btn = QPushButton("Start Update")
        self._start_btn.setProperty("class", "primary")
        self._start_btn.setEnabled(False)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setProperty("class", "secondary")
        self._cancel_btn.setEnabled(False)

        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._cancel_btn)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #9ba7b4;")

        controls.addLayout(btn_row)
        controls.addWidget(self._status_label)

        progress_row.addWidget(self._progress, stretch=1)
        progress_row.addLayout(controls)

        root.addLayout(progress_row)


    def _connect_signals(self) -> None:
        """Wire widget signals to handlers."""

        self._browse_btn.clicked.connect(self._on_browse)
        self._start_btn.clicked.connect(self._on_start)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._timer.timeout.connect(self._on_timer)


    def _on_browse(self) -> None:
        """Open a file dialog and accept a firmware file."""

        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setNameFilter("Firmware Files (*.bin *.hex *.uf2 *.zip)")

        if dialog.exec():
            files = dialog.selectedFiles()
            if files:
                self._set_file(files[0])


    def _set_file(self, path: str) -> None:
        """Record selected file and enable the start control."""

        self._file_path = path
        self._file_edit.setText(path)
        self._start_btn.setEnabled(True)
        # Clear previous notes — front-end only; real notes would be parsed from package
        self._notes.setPlainText("Release notes: (preview not available")


    def _on_start(self) -> None:
        """Begin a simulated firmware install (frontend-only)."""

        if not self._file_path:
            self._set_status("No firmware selected.")
            return

        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._progress.setValue(0)
        self._set_status("Preparing update…")
        self._timer.start()


    def _on_cancel(self) -> None:
        """Cancel a running simulated install."""

        if self._timer.isActive():
            self._timer.stop()

        self._progress.setValue(0)
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._set_status("Update cancelled.")


    def _on_timer(self) -> None:
        """Timer tick: advance simulated progress and finish when complete."""

        value = self._progress.value() + 3
        if value >= 100:
            self._timer.stop()
            self._progress.setValue(100)
            self._cancel_btn.setEnabled(False)
            self._set_status("Update completed successfully.")
            # Emit installRequested so host can hook a backend if desired
            self.installRequested.emit(self._file_path)
            return

        self._progress.setValue(value)
        self._set_status(f"Updating… {value}%")


    def _set_status(self, text: str) -> None:
        """Update the status label text."""

        self._status_label.setText(text)


    def dragEnterEvent(self, ev: QDragEnterEvent) -> None:
        """Accept drag if it contains a supported filename."""

        urls = ev.mimeData().urls()
        if not urls:
            ev.ignore()
            return

        path = urls[0].toLocalFile()
        if path.lower().endswith((".bin", ".hex", ".uf2", ".zip")):
            ev.acceptProposedAction()
        else:
            ev.ignore()


    def dropEvent(self, ev: QDropEvent) -> None:
        """Handle drop of a firmware file."""

        urls = ev.mimeData().urls()
        if not urls:
            return

        path = urls[0].toLocalFile()
        if path:
            self._set_file(path)
