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
    UpdateStepAbort           = 0
    UpdateStepInit            = 1
    UpdateEstablishConnection = 2
    UpdateStepWrite           = 3
    UpdateStepVerifyWrite     = 4
    UpdateStepCommandInstall  = 5
    UpdateStepWaitForInstall  = 6
    UpdateStepCommandReboot   = 7
    UpdateStepCleanup         = 8

class UpdaterBackend:
    MAX_ATTEMPTS = 5
    # For TCP transfers, use smaller chunks to match network/receiver bandwidth
    # Larger chunks cause backpressure; smaller chunks flow better over slow connections
    SAFE_UDP_CHUNK_SIZE = 1200

    def __init__(self):        
        self.__threadPool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="update-fsm")
        
        steps : list = [
            []
        ]

        self._ReplyReceived  : Event  = Event()
        self._stopEvent      : Event  = Event()
        self._stopWaitEvent  : Event  = Event()
        self._writeAckdEvent : Event  = Event()
        self._replyQueue     : Queue  = Queue(10)
        self._updateFileName : str    = None
        self._updateThread   : Thread = None
        self._bytesWritten   : int = 0
        self._fsm            : FSM = FSM()
        
        self._attemptCtr : int = 0

        self.updateDone      : Signal = Signal(bool)
        self.updateProgress  : Signal = Signal(int, int)
        self.updateError     : Signal = Signal()
        self.firmwareAborted : Signal = Signal()
        
        self._tcpPort = None
        self._remotePort : int = None
        
        self._threadFuture = None
        self._hashFuture   = None

        # Register steps
        self._fsm.registerStep(UpdateSteps.UpdateStepInit.value,            transition=UpdateSteps.UpdateEstablishConnection.value, callback=self._HandleInit)
        self._fsm.registerStep(UpdateSteps.UpdateEstablishConnection.value, transition=UpdateSteps.UpdateStepWrite.value,           callback=self._HandleEstablishConnection)
        self._fsm.registerStep(UpdateSteps.UpdateStepWrite.value,           transition=UpdateSteps.UpdateStepCommandInstall.value, callback=self._HandleFileWrite)
        self._fsm.registerStep(UpdateSteps.UpdateStepCommandInstall.value,  transition=UpdateSteps.UpdateStepCommandReboot.value,  callback=self._HandleInstall)
        self._fsm.registerStep(UpdateSteps.UpdateStepCommandReboot.value,   transition=None,                                        callback=self._HandleReboot)
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
        UpdaterCommand().ModuleCleanUpdater(replyCallback=self._OnReply)
        reply : Reply = self._synchReply(5.0, expected_command_id=UpdaterCommand.CmdCleanUpdater)
        if reply is None:  # Reply timeout detected for clean updater
            logging.warning("Reply timeout detected for clean updater")
            return False

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
            self._remotePort = tcpPort
            remote_ip = NetworkManager.getRemoteHostIP(prefer_ethernet=True)
            if not remote_ip:
                raise NetworkErr("Remote host IP is unknown; cannot establish TCP update connection")
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
        logging.info("FW update initialized on target")
        return True
    
    def _HandleEstablishConnection(self) -> bool:
        print("[FSM] establish connection")
        connection_established : bool = False
        
        remote_ip = NetworkManager.getRemoteHostIP(prefer_ethernet=True)
        tcpPort = self._remotePort
        attempts = 0
        MAX_ATTEMPTS = 5
        
        while not connection_established and attempts < MAX_ATTEMPTS:
            if not self._tcpPort.connect(remote_ip):
                attempts += 1
                logging.warning("Failed connecting TCP update client to %s:%d (attempt %d/%d)", remote_ip, tcpPort, attempts, MAX_ATTEMPTS)
                continue
            logging.info("TCP update connection established: local=%s, remote=%s:%d",
                        self._tcpPort.getSrcAddr(), remote_ip, tcpPort)
            connection_established = True
            break
        if not connection_established:
            raise NetworkErr(f"Failed connecting TCP update client to {remote_ip}:{tcpPort} after {MAX_ATTEMPTS} attempts")
        return True

    def _HandleFileWrite(self) -> bool | None:
        print("[FSM] write")
        # return True
        fileSize : int = 0
        logging.info(f"Starting file write: {self._updateFileName}")
        bytesWritten : int = 0
        fileSize : int = 0
        writeCount : int = 0
        MAX_TRIES = 5
        WRITE_COUNT_BEFORE_CHECK = 32
        chunk_size = UpdaterCommand.GetMaxPayload()
        if chunk_size <= 0:
            logging.error("Invalid updater chunk size: %s", chunk_size)
            self._fsm.trigger(UpdateSteps.UpdateStepCleanup.value)
            return False

        logging.info(
            "Using firmware chunk size %s bytes (capped from %s, max payload %s)",
            chunk_size,
            chunk_size,
            UpdaterCommand.GetMaxPayload(),
        )

        with open (self._updateFileName, "rb") as file:
            fileSize = os.path.getsize(self._updateFileName)
            while not self._stopEvent.is_set():
                chunk = file.read(chunk_size)
                if not chunk:
                    break

                tries = 0
                sent = False
                tries = 0
                while tries < MAX_TRIES and not self._stopEvent.is_set():
                    if self._tcpPort.send(chunk):
                        sent = True
                        break
                    tries += 1
                    logging.warning(
                        "TCP chunk send failed. retry=%s/%s, chunk=%s, size=%s",
                        tries,
                        MAX_TRIES,
                        writeCount,
                        len(chunk),
                    )
                    time.sleep(min(0.02 * tries, 0.2))

                if not sent:
                    logging.error(
                        "Failed to send firmware chunk after retries. chunk=%s, size=%s",
                        writeCount,
                        len(chunk),
                    )
                    self._fsm.trigger(UpdateSteps.UpdateStepCleanup.value)
                    return False

                writeCount += 1
                writeCount %= WRITE_COUNT_BEFORE_CHECK
                
                bytesWritten += len(chunk)
                progress = (bytesWritten / fileSize) * 100
                self.updateProgress.emit(0, progress)

        if self._stopEvent.is_set():
            self.updateDone.emit()
            self.firmwareAborted.emit()
            logging.warning("File write interrupted by stop event")
            self._fsm.trigger(UpdateSteps.UpdateStepCleanup.value)
            return False

        # Update is done
        logging.info("File write finalized")
        self.updateProgress.emit(0, 100)
        return True

    def _HandleInstall(self) -> None:
        print("[FSM] Install")
        UpdaterCommand().ModuleApplyUpdate(replyCallback=self._OnReply)
        reply = self._synchReply(timeout=5.0, expected_command_id=UpdaterCommand.CmdInstallUpdate)
        if reply is None:
            logging.error("No reply received for install update command")
        elif reply.status() != 1:
            logging.error("Install update failed with status: %s", reply.status())

        logging.info("Waiting for install update to complete...")
        progress : int = 0
        while progress < 100 and not self._stopEvent.is_set():
            UpdaterCommand().ModuleQueryUpdateStatus(replyCallback=self._OnReply)
            reply = self._synchReply(timeout=5.0, expected_command_id=UpdaterCommand.CmdQueryUpdateStatus)
            if reply is None:
                logging.warning("No reply received for install update progress")
                continue
            if reply.status() != 1:
                logging.error("Install update progress failed with status: %s", reply.status())
                break
            payload = reply.payload()
            if payload is None or len(payload) < ctypes.sizeof(ctypes.c_int):
                logging.error("Invalid payload in install update progress reply (len=%s)", 0 if payload is None else len(payload))
                break
            progress = ctypes.c_int.from_buffer_copy(payload).value
            print(f"[FSM] Install progress: {progress}%")
            self.updateProgress.emit(1, progress)
            time.sleep(1)
        logging.info("Install update completed with progress: %s", progress)
        return True

    def _HandleReboot(self) -> None:
        print("[FSM] Reboot")
        # Send the reboot command to the target
        logging.info("Sending reboot command to target")
        CommandBus().ModuleFinalize(replyCallback=self._OnReply)
        reply = self._synchReply(timeout=5.0, expected_command_id=UpdaterCommand.CmdUpdaterFinalize)
        if reply is None:
            logging.error("No reply received for reboot command")
        return True

    def _HandleIdle(self) -> bool | None:
        TIMEOUT    = 5
        timeoutCtr = 0

        while timeoutCtr < TIMEOUT and not self._stopWaitEvent.is_set():
            timeoutCtr += 1
            time.sleep(1)
            
        self._stopWaitEvent.clear()
        return True

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
        