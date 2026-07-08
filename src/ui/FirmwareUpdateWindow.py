from urllib import request

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
    QDialog
)

from PyQt6.QtGui import QIcon, QDragEnterEvent, QDropEvent

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize, QThread

import utils.utilities as utils

from threading import Event
import logging
from enum import Enum, auto
import configparser
import os

class FirmwareUpdateWindow(QWidget):
    
    class ReplyId(Enum):
        VERIFY_RESULT   = auto()
        PROGRESS_UPDATE = auto()
        INSTALL_RESULT  = auto()
    
    """Front-end only firmware upgrade panel.

    Intended to be embedded in the main window (for example added to a
    stacked layout or shown in a dialog). No backend logic is included —
    the UI emits `initUpdate` when the user triggers an install.
    """
    initUpdate          = pyqtSignal(str)    # Initiate firmware update with given file path
    sendFileChunk       = pyqtSignal(int, bytes)  # Emit a chunk of the firmware file for transfer to the device
    verifyFile          = pyqtSignal(int, object) # Verify the firmware file before starting the update (emits file path, size, and hash for verification by backend)
    cmdInstall          = pyqtSignal(int, )       # Command to start the installation process after file transfer is complete
    requestInstallState = pyqtSignal(int, )       # Request current installation state (for UI sync on startup or after reconnecting to device)
    requestCancel       = pyqtSignal()       # Request to cancel the ongoing firmware update
   
    class ReplyWorker(QThread):      
        doNext = pyqtSignal(int) # Signal to trigger processing of the next reply in the buffer (used to wake the thread when a new reply arrives)
          
        def __init__(self, replyCircBuff : utils.CircularBuffer =None) -> None:
            super().__init__()
            self._replyCircBuff = replyCircBuff
            self._canRun = True

        def kill(self):
            self._canRun = False
            self._replyCircBuff.flush() # Unblock the thread if it's waiting on an empty buffer

        def OnVerifyResult(self, data):
            logging.info(f"Received VERIFY_RESULT reply: {data}")

        def OnProgressUpdate(self, data):
            logging.info(f"Received PROGRESS_UPDATE reply: {data}")
            pass
        
        def OnInstallResult(self, data):
            logging.info(f"Received INSTALL_RESULT reply: {data}")
            
        def run(self) -> None:
            logging.info(f"Starting ReplyWorker thread...")
            while self._canRun:
                reply = self._replyCircBuff.read()
                
                if reply is None:
                    continue
                
                id, data = reply
                
                match id:
                    case FirmwareUpdateWindow.ReplyId.VERIFY_RESULT: self.OnVerifyResult(data)
                        # Handle verification result (e.g. update UI, show error if verification failed)
                
            logging.info(f"Exiting ReplyWorker thread...")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._file_path = ""
        self._config_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "config", "rc-car-viewer-config.ini")
        )

        self.setAcceptDrops(True)
        self.setMinimumWidth(640)

        self._build_ui()

        self._connect_signals()
        
        self._replyCircBuff = utils.CircularBuffer(10) # Buffer for incoming device replies related to firmware update process
        
        self._procReplyThread = self.ReplyWorker(self._replyCircBuff)
        self._restore_persisted_file_path()


    def _restore_persisted_file_path(self) -> None:
        """Restore previously selected firmware file path from config."""
        parser = configparser.ConfigParser()
        parser.read(self._config_path)
        last_path = parser.get("settings", "firmware_path", fallback="").strip()
        if last_path:
            self._set_file(last_path)


    def _persist_file_path(self, path: str) -> None:
        """Persist selected firmware file path to config."""
        parser = configparser.ConfigParser()
        parser.read(self._config_path)
        if not parser.has_section("settings"):
            parser.add_section("settings")
        parser.set("settings", "firmware_path", path)
        with open(self._config_path, "w", encoding="utf-8") as cfg:
            parser.write(cfg)


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
        self._file_edit.setPlaceholderText("Select firmware file (.swu)")
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

    def _on_browse(self) -> None:
        """Open a file dialog and accept a firmware file."""

        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setNameFilter("Firmware Files (*.swu)")

        if dialog.exec():
            files = dialog.selectedFiles()
            if files:
                self._set_file(files[0])


    def _set_file(self, path: str) -> None:
        """Record selected file and enable the start control."""

        self._file_path = path
        self._file_edit.setText(path)
        self._start_btn.setEnabled(True)
        self._persist_file_path(path)
        # Clear previous notes — front-end only; real notes would be parsed from package
        self._notes.setPlainText("Release notes: (preview not available")


    def _on_start(self) -> None:
        """Begin a simulated firmware install (frontend-only)."""

        if not self._file_path:
            self._set_status("No firmware selected.")
            return
        self._cancel_btn.setEnabled(True)
        self._progress.setValue(0)
        self._set_status("Preparing update…")
        logging.info(f"Emitting initUpdate signal with file path: {self._file_path}")
        
        # Persist the selected file path for the backend to access
        self.initUpdate.emit(self._file_path) # Emit signal to trigger backend update process (


    def _on_cancel(self) -> None:
        """Cancel a running simulated install."""

        self._progress.setValue(0)
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self.requestCancel.emit()


    def _set_status(self, text: str) -> None:
        """Update the status label text."""

        self._status_label.setText(text)
        
        
    def onDeviceReply(self, id : ReplyId, reply: dict) -> None:
        """
        Device reply state machine handling for firmware update process. This should be connected to the backend signal that emits device replies related to firmware update commands, allowing the UI to react to progress updates, verification results, and installation outcomes.

        Args:
            reply (dict): _description_
        """
        self._replyCircBuff.push((id, reply))
        pass


    def dragEnterEvent(self, ev: QDragEnterEvent) -> None:
        """Accept drag if it contains a supported filename."""

        urls = ev.mimeData().urls()
        if not urls:
            ev.ignore()
            return

        path = urls[0].toLocalFile()
        if path.lower().endswith((".swu",)):
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


    def setProgress(self, prog : float) -> None:
        self._set_status("Downloading firmware...")
        self._progress.setValue(int(prog))

    def OnFwError(self) -> None:
        logging.error("Detected error during firmware update")    
    
    def OnFwFinished(self) -> None:
        logging.info("Firmware update finished!")

    def OnFwAborted(self) -> None:
        logging.info("Firmware update aborted.")
        self._progress.setValue(0)
        self._set_status("Update cancelled.")