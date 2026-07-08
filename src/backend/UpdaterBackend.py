import os
from threading import Thread, Event
from queue import Queue, Empty, Full
from concurrent.futures import ThreadPoolExecutor
import hashlib
import time
import logging
import ctypes
import struct

from utils.utilities import Emitter, Signal, FSM

from backend.CommandBus import (CamStreamSelectionModes,
                                     CommandBus, Command, CameraCommand, RcCommands,
                                     Reply,
                                     MotorCommands, UpdaterCommand, val_type_t)
from backend.CommandBus import UpdaterCommand, CameraCommand
from network.NetworkManager import NetworkManager, NetworkErr
from enum import Enum
from dataclasses import dataclass
import ctypes

MAX_UDP_PACKET_SIZE = 65507

@dataclass
class FileChunks:
    index: int
    size: int
    chunkSize: int
    data: bytes

    HEADER_FMT = "<QQQ"
    HEADER_SIZE = struct.calcsize(HEADER_FMT)

    def to_bytes(self) -> bytes:
        header = struct.pack(self.HEADER_FMT, self.index, self.size, self.chunkSize)
        return header + self.data

class UpdateSteps(Enum):
    UpdateStepAbort          = 0
    UpdateStepInit           = 1
    UpdateStepWrite          = 2
    UpdateStepVerifyWrite    = 3
    UpdateStateValidate      = 4
    UpdateStepCommandInstall = 5
    UpdateStepWaitForInstall = 6
    UpdateStateCommandReboot = 7
    UpdateStepCleanup        = 8

class UpdaterBackend:
    MAX_ATTEMPTS = 5
    # Keep firmware chunks near MTU to avoid heavy IP fragmentation over UDP.
    SAFE_UDP_CHUNK_SIZE = 1200

    def __init__(self):        
        self.__threadPool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="update-fsm")

        self._ReplyReceived  : Event  = Event()
        self._stopEvent      : Event  = Event()
        self._stopWaitEvent  : Event  = Event()
        self._writeAckdEvent : Event  = Event()
        self._replyQueue     : Queue  = Queue(10)
        self._updateFileName : str    = None
        self._updateThread   : Thread = None
        self._bytesWritten   : int = 0
        self._fileHash       : int = 0
        self._fsm            : FSM = FSM()
        
        self._attemptCtr : int = 0

        self.updateDone      : Signal = Signal(bool)
        self.updateProgress  : Signal = Signal()
        self.updateError     : Signal = Signal()
        self.firmwareAborted : Signal = Signal()
        
        self._tcpPort = None
        
        self._threadFuture = None
        self._hashFuture   = None

        # Register steps
        self._fsm.registerStep(UpdateSteps.UpdateStepInit.value,           transition=UpdateSteps.UpdateStepWrite.value,           callback=self._HandleInit)
        self._fsm.registerStep(UpdateSteps.UpdateStepWrite.value,          transition=UpdateSteps.UpdateStateValidate.value,       callback=self._HandleFileWrite)
        self._fsm.registerStep(UpdateSteps.UpdateStateValidate.value,      transition=UpdateSteps.UpdateStepCommandInstall.value,  callback=self._HandleVerify)
        self._fsm.registerStep(UpdateSteps.UpdateStepCommandInstall.value, transition=UpdateSteps.UpdateStateCommandReboot.value,  callback=self._HandleInstall)
        self._fsm.registerStep(UpdateSteps.UpdateStateCommandReboot.value, transition=None,                                        callback=self._HandleReboot)
        self._fsm.finally_(self._HandleCleanupOnError)

    def StartUpdate(self, filename : str):
        self._updateFileName = filename
        self._bytesWritten = 0
        self._stopEvent.clear()
        self._replyQueue.queue.clear()
        while not self._replyQueue.empty():
            try:
                self._replyQueue.get_nowait()
            except Empty:
                break

        if self._threadFuture is not None and not self._threadFuture.done():
            logging.error("Update still in progress")
            self.updateError.emit()
            return

        if self._threadFuture is None or self._threadFuture.done():
            self._threadFuture = self.__threadPool.submit(lambda: self._fsm.trigger(UpdateSteps.UpdateStepInit.value))

    def AbortUpdate(self) -> None:
        logging.info("Abort update requested")
        self._stopEvent.set()
        self._fsm.kill()
        self._fsm.trigger(UpdateSteps.UpdateStepCleanup.value)
        
    def _VerifyWrite(self) -> bool | None:
        pass

    def _HandleInit(self) -> bool | None:
        # Calculate sha256 sum of file
        print("[FSM] init")
        logging.debug(f"Initializing update with file: {self._updateFileName}")
        UpdaterCommand().ModuleInitUpdate(replyCallback=self._OnReply, fileName=self._updateFileName)
        reply : Reply = self._synchReply(5.0, expected_command_id=UpdaterCommand.CmdInitUpdate)
        if reply is None:
            logging.warning("Reply timeout detected")
            return False

        if reply.status() != 1:
            logging.error("Target replied with error. Aborting update")
            self._fsm.trigger(UpdateSteps.UpdateStepCleanup.value)
            return False

        payload = reply.payload()
        if payload is None or len(payload) < ctypes.sizeof(ctypes.c_int):
            logging.error("Invalid TCP port payload in init reply (len=%s)", 0 if payload is None else len(payload))
            self._fsm.trigger(UpdateSteps.UpdateStepCleanup.value)
            return False
        
        # Read the TCP port from the reply
        tcpPort : int = ctypes.c_int.from_buffer_copy(payload).value
        
        if tcpPort <= 0:
            logging.error("Invalid TCP port received: %s", tcpPort)
            self._fsm.trigger(UpdateSteps.UpdateStepCleanup.value)
            return False

        try:
            # Open a TCP client bound to an ephemeral local source port (0),
            # targeting the destination port provided by the host init reply.
            self._tcpPort = NetworkManager.openNetworkAdapter(("0.0.0.0", tcpPort, 0), protocol="tcp")
            logging.info("Opened update TCP adapter on ephemeral source port for destination port %s", tcpPort)
        except NetworkErr as e:
            logging.error("Failed opening update TCP adapter on port %s: %s", tcpPort, e)
            self._fsm.trigger(UpdateSteps.UpdateStepCleanup.value)
            return False
        except Exception as e:
            logging.error("Unexpected error opening update TCP adapter: %s", e)
            self._fsm.trigger(UpdateSteps.UpdateStepCleanup.value)
            return False
            
        # Stop camera streaming to prevent conflicts during firmware update
        CameraCommand().ModuleStopStream()

        # with open(self._updateFileName, "rb") as f:
        #     digest = hashlib.file_digest(f, "sha256")
        # self._fileHash = digest.hexdigest()
        logging.info("FW update initialized on target")
        return True

    def _HandleFileWrite(self) -> bool | None:
        print("[FSM] write")
        fileSize : int = 0
        logging.info(f"Starting file write: {self._updateFileName}")
        bytesWritten : int = 0
        fileSize : int = 0
        writeCount : int = 0
        MAX_TRIES = 5
        WRITE_COUNT_BEFORE_CHECK = 32
        chunk_size = UpdaterCommand.GetMaxPayload() - FileChunks.HEADER_SIZE
        if chunk_size <= 0:
            logging.error("Invalid updater chunk size: %s", chunk_size)
            self._fsm.trigger(UpdateSteps.UpdateStepCleanup.value)
            return False

        logging.info(
            "Using firmware chunk size %s bytes (max payload %s)",
            chunk_size,
            UpdaterCommand.GetMaxPayload(),
        )

        with open (self._updateFileName, "rb") as file:
            fileSize = os.path.getsize(self._updateFileName)
            numChunks : int = (fileSize + chunk_size - 1) // chunk_size
            chunkID : int = 0
            needAck : bool = False
            while not self._stopEvent.is_set():
                chunk = file.read(chunk_size)
                if not chunk:
                    break

                tries = 0
                dataOut : FileChunks = FileChunks(
                    index=chunkID,
                    size=fileSize,
                    chunkSize=len(chunk),
                    data=chunk,
                )
                chunkID += 1
                UpdaterCommand().ModuleWriteFileData(dataOut.to_bytes(), replyCallback=self._OnReply)
                writeCount += 1
                writeCount %= WRITE_COUNT_BEFORE_CHECK
                
                bytesWritten += len(chunk)
                progress = (bytesWritten / fileSize) * 100
                self.updateProgress.emit(progress)
                print(f"\rProgress: {progress:.2f}% ({bytesWritten}/{fileSize} bytes)", end='')
                # if not needAck:
                #     continue
                reply : Reply = self._synchReply(1.0, expected_command_id=UpdaterCommand.CmdWriteFileData)

                if reply is None:
                    tries += 1
                    logging.warning(
                        "Failed to write file chunk (timeout). retry=%s/%s, offset=%s, size=%s",
                        tries,
                        MAX_TRIES,
                        bytesWritten,
                        len(chunk),
                    )
                    # Brief backoff to avoid overwhelming the target after a timeout.
                    time.sleep(min(0.1 * tries, 0.5))
                    continue

                if reply.status() != 1:
                    tries += 1
                    logging.warning(
                        "Target rejected chunk with status=%s. retry=%s/%s, offset=%s, size=%s",
                        reply.status(),
                        tries,
                        MAX_TRIES,
                        bytesWritten,
                        len(chunk),
                    )
                    time.sleep(min(0.1 * tries, 0.5))
                    continue

                # Check whether there are any missing segments
                fmt = f'<{len(reply.payload()) // 8}Q'
                segmentsList : list[int] = list(struct.unpack(fmt, reply.payload()))
        if self._stopEvent.is_set():
            self.updateDone.emit()
            self.firmwareAborted.emit()
            logging.warning("File write interrupted by stop event")
            self._fsm.trigger(UpdateSteps.UpdateStepCleanup.value)
            return False

        # Update is done
        logging.info("File write finalized")
        self.updateProgress.emit(100)
        return True

    def _HandleVerify(self) -> bool | None:
        print("[FSM] Verify")
        return True
        # Send the checksum and request from the device
        UpdaterCommand().ModuleVerifyFile(ctypes.c_uint64(self._fileHash), self._HandleVerify)
        status = self._replyQueue.get()
        if status is None:
            logging.error("Target reported CRC mismatch. Aborting update...")
            self._fsm.kill()

        return True
    
    def _HandleInstall(self) -> None:
        print("[FSM] Install")
        return True
        pass
    
    def _HandleReboot(self) -> None:
        print("[FSM] Reboot")
        return True
        pass

    def _HandleIdle(self) -> bool | None:
        TIMEOUT    = 5
        timeoutCtr = 0

        while timeoutCtr < TIMEOUT and not self._stopWaitEvent.is_set():
            timeoutCtr += 1
            time.sleep(1)
            
        self._stopWaitEvent.clear()

    def _HandleCleanupOnError(self) -> None:
        print("[FSM] Cleanup on Error")
        if self._tcpPort is not None:
            try:
                self._tcpPort.shutdown()
            except Exception as e:
                logging.warning("Failed shutting down update TCP adapter: %s", e)
            finally:
                self._tcpPort = None
        CameraCommand().ModuleStartStream()
        logging.error("An error occurred during the update process. Performing cleanup.")
        
    def _OnReply(self, reply : Reply) -> None:
        self._ReplyReceived.set()
        try:
            self._replyQueue.put_nowait(reply)
        except Full:
            logging.warning("Updater reply queue full; dropping oldest reply")
            try:
                _ = self._replyQueue.get_nowait()
                self._replyQueue.put_nowait(reply)
            except Empty:
                pass

    def _synchReply(self, timeout : float = 0, expected_command_id: int | None = None) -> Reply | None:
        end_time = time.monotonic() + timeout if timeout and timeout > 0 else None

        while True:
            remaining = None
            if end_time is not None:
                remaining = end_time - time.monotonic()
                if remaining <= 0:
                    return None

            try:
                reply = self._replyQueue.get(timeout=remaining)
            except Empty:
                return None

            if expected_command_id is None or reply.commandID() == expected_command_id:
                return reply

            logging.debug(
                "Discarding unrelated updater reply commandID=%s while waiting for commandID=%s",
                reply.commandID(),
                expected_command_id,
            )
        