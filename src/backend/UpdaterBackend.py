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
    UpdateStepFinalize        = 6
    HandleStepWaitForReboot   = 7
    UpdateStepVerifyInstall   = 8
    UpdateStepCleanup         = 9

class UpdaterBackend:
    MAX_ATTEMPTS = 5
    # For TCP transfers, use smaller chunks to match network/receiver bandwidth
    # Larger chunks cause backpressure; smaller chunks flow better over slow connections
    SAFE_UDP_CHUNK_SIZE = 1200

    def __init__(self):        
        self.__threadPool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="update-fsm")

        self._ReplyReceived  : Event  = Event()
        self._stopEvent      : Event  = Event()
        self._stopWaitEvent  : Event  = Event()
        self._writeAckdEvent : Event  = Event()
        self._replyQueue     : Queue  = Queue(10)
        self._updateFileName : str    = None
        self._updateFileVer  : str    = None
        self._updateThread   : Thread = None
        self._bytesWritten   : int = 0
        self._fsm            : FSM = FSM()
        
        self._attemptCtr : int = 0

        self.updateDone       : Signal = Signal(str)
        self.updateProgress   : Signal = Signal(int, int)
        self.updateError      : Signal = Signal()
        self.rebootInProgress : Signal = Signal()
        self.installStarted   : Signal = Signal()
        self.firmwareAborted  : Signal = Signal()
        
        self._tcpPort = None
        self._remotePort : int = None
        
        self._threadFuture = None
        self._hashFuture   = None
        
        self._aborted : bool = False
        self._ipAddr : str = None

        # Register steps
        self._fsm.registerStep(UpdateSteps.UpdateStepInit.value,            transition=UpdateSteps.UpdateEstablishConnection.value, callback=self._HandleInit)
        self._fsm.registerStep(UpdateSteps.UpdateEstablishConnection.value, transition=UpdateSteps.UpdateStepWrite.value,           callback=self._HandleEstablishConnection)
        self._fsm.registerStep(UpdateSteps.UpdateStepWrite.value,           transition=UpdateSteps.UpdateStepCommandInstall.value,  callback=self._HandleFileWrite)
        self._fsm.registerStep(UpdateSteps.UpdateStepCommandInstall.value,  transition=UpdateSteps.UpdateStepFinalize.value,        callback=self._HandleInstall)
        self._fsm.registerStep(UpdateSteps.UpdateStepFinalize.value,        transition=UpdateSteps.HandleStepWaitForReboot.value,   callback=self._HandleFinalize)
        self._fsm.registerStep(UpdateSteps.HandleStepWaitForReboot.value,   transition=UpdateSteps.UpdateStepVerifyInstall.value,   callback=self._HandleWaitForReboot)
        self._fsm.registerStep(UpdateSteps.UpdateStepVerifyInstall.value,   transition=None,                                        callback=self._HandleVerifyInstall)
        self._fsm.finally_(self._HandleCleanup)

    def StartUpdate(self, filename : str):
        self._updateFileName = filename
        self._updateFileVer = os.path.basename(filename).replace("rc-car-update-rc-car-machine-", "").replace(".swu", "")
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
        self._aborted = True
        # self._fsm.trigger(UpdateSteps.UpdateStepCleanup.value)

    def _HandleInit(self) -> bool | None:
        # Calculate sha256 sum of file
        print("[FSM] init")

        # Request firmware revision from target to ensure compatibility
        logging.info("Requesting firmware revision from target")
        payload = self._xfer(UpdaterCommand.CmdRequestFirmwareRev, replyCallback=self._OnReply)
        rev : str = payload.decode('utf-8') if payload else "Unknown"
        logging.info("Target firmware revision: %s", rev)

        payload = self._xfer(UpdaterCommand.CmdInitUpdate, replyCallback=self._OnReply)
        if payload is None:
            logging.warning("Reply timeout detected")
            return False

        if len(payload) < ctypes.sizeof(ctypes.c_int):
            logging.error("Invalid TCP port payload in init reply (len=%s)", len(payload))
            return False
        
        # Read the TCP port from the reply
        tcpPort : int = ctypes.c_int.from_buffer_copy(payload).value
        
        if tcpPort <= 0:
            logging.error("Invalid TCP port received: %s", tcpPort)
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
            return False
        except Exception as e:
            logging.error("Unexpected error opening update TCP adapter: %s", e)
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

                if not sent and not self._stopEvent.is_set():
                    logging.error(
                        "Failed to send firmware chunk after retries. chunk=%s, size=%s",
                        writeCount,
                        len(chunk),
                    )
                    return False
                elif self._stopEvent.is_set():
                    logging.warning("File write interrupted by stop event")
                    return False

                writeCount += 1
                writeCount %= WRITE_COUNT_BEFORE_CHECK
                
                bytesWritten += len(chunk)
                progress = (bytesWritten / fileSize) * 100
                self.updateProgress.emit("Uploading...", progress)

        if self._stopEvent.is_set():
            self.firmwareAborted.emit()
            return False

        # Update is done
        logging.info("File write finalized")
        self.updateProgress.emit("Uploading...", 100)
        
        self._closeTCPPort()
        return True

    def _HandleInstall(self) -> None:
        print("[FSM] Install")
        self._xfer(UpdaterCommand.CmdInstallUpdate, replyCallback=self._OnReply)
        self.installStarted.emit()

        logging.info("Waiting for install update to complete...")
        progress : int = 0
        while progress < 100 and not self._stopEvent.is_set():
            payload = self._xfer(UpdaterCommand.CmdQueryUpdateStatus, replyCallback=self._OnReply)
            progress = ctypes.c_int.from_buffer_copy(payload).value
            self.updateProgress.emit("Installing...", progress)
            time.sleep(1)
        logging.info("Install update completed with progress: %s", progress)
        return True

    def _HandleFinalize(self) -> None:
        print("[FSM] Finalize")
        # Send the reboot command to the target
        logging.info("Sending reboot command to target")
        self.rebootInProgress.emit()
        self._xfer(UpdaterCommand.CmdUpdaterFinalize, replyCallback=self._OnReply)
        time.sleep(5)  # Give time for the CPU to reboot
        return True

    def _HandleWaitForReboot(self) -> None:
        # Now we wait for the unit to come back online. This may take some time
        print("[FSM] Wait for Reboot")
        logging.info("Waiting for target to become reachable after reboot...")
        counter : int = 0
        MAX_WAIT_TIME : int = 72  # 6 minutes (72 * 5 seconds = 360 seconds)
        ping_acked : bool = False

        def ping_reply():
            nonlocal ping_acked
            ping_acked = True

        while counter < MAX_WAIT_TIME and not self._stopEvent.is_set():
            time.sleep(5)
            counter += 1
            RcCommands().ping(replyCallback=ping_reply)
            if ping_acked:
                logging.info("Target is reachable after reboot")
                break
        return True

    def _HandleVerifyInstall(self) -> None:
        print("[FSM] Verify Install")
        # After reboot, verify that the update was successful
        payload = self._xfer(UpdaterCommand.CmdRequestFirmwareRev, replyCallback=self._OnReply)
        rev : str = payload.decode('utf-8') if payload else "Unknown"
        logging.info("Target firmware revision after update: %s", rev)
        if rev != self._updateFileVer:
            logging.error("Firmware revision mismatch after update: expected=%s, got=%s", self._updateFileVer, rev)
            self.updateError.emit()
            return False
        logging.info("Firmware update verified successfully -> V%s", rev)
        return True

    def _HandleCleanup(self) -> None:
        print("[FSM] Cleanup")
        self._stopEvent.set()
        if self._tcpPort is not None:
            try:
                self._tcpPort.shutdown()
            except Exception as e:
                logging.warning("Failed shutting down update TCP adapter: %s", e)
            finally:
                self._closeTCPPort()
        CameraCommand().ModuleStartStream()
        
        try:
            self._xfer(UpdaterCommand.CmdCleanUpdater, replyCallback=self._OnReply)
        except FSM.FSMException:
            logging.warning("Unable to command target to cleanup state")

        if self._aborted:
            logging.info("Update process was aborted by user.")
            self.firmwareAborted.emit()
        else:
            self.updateDone.emit(self._ipAddr)
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
    
    def _closeTCPPort(self) -> None:
        if self._tcpPort is not None:
            try:
                self._tcpPort.shutdown()
                logging.info("TCP update adapter shutdown successfully")
            except Exception as e:
                logging.warning("Failed shutting down update TCP adapter: %s", e)
            finally:
                self._tcpPort = None

    def _xfer(self, cmd : int, payload : bytes = None, replyCallback = None) -> bytes | None:
        """
        Send a command to the updater module with optional payload and reply callback.

        Args:
            cmd (int): Command ID to send
            payload (bytes, optional): Optional payload data to send with the command
            replyCallback (callable, optional): Optional callback function to handle the reply

        Returns:
            bytes | None: The reply data if available, None otherwise
        """
        MAX_TRIES = 5
        tries : int = 0
        
        while tries < MAX_TRIES:
            match cmd:
                case UpdaterCommand.CmdRequestFirmwareRev:
                    UpdaterCommand().ModuleRequestRevision(replyCallback=replyCallback)
                case UpdaterCommand.CmdInitUpdate:
                    UpdaterCommand().ModuleInitUpdate(replyCallback=replyCallback, fileName=self._updateFileName)
                case UpdaterCommand.CmdWriteFileData:
                    UpdaterCommand().ModuleWriteFileData(payload, replyCallback=replyCallback)
                case UpdaterCommand.CmdInstallUpdate:
                    UpdaterCommand().ModuleApplyUpdate(replyCallback=replyCallback)
                case UpdaterCommand.CmdQueryUpdateStatus:
                    UpdaterCommand().ModuleQueryUpdateStatus(replyCallback=replyCallback)
                case UpdaterCommand.CmdUpdaterFinalize:
                    UpdaterCommand().ModuleFinalize(replyCallback=replyCallback)
                case UpdaterCommand.CmdCleanUpdater:
                    UpdaterCommand().ModuleCleanUpdater(replyCallback=replyCallback)
                case _:
                    logging.error("Unknown updater command ID: %s", cmd)
                    raise FSM.FSMException(f"Unknown updater command ID: {cmd}")
            reply = self._synchReply(timeout=1.0, expected_command_id=cmd)
            if reply == None:
                logging.warning("Reply timeout detected for command ID: %s", cmd)
                tries += 1
                continue
            return reply

        raise FSM.FSMException(f"Failed to receive reply after max number of retries")

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
                if reply.status() != 1:
                    logging.error("Updater reply indicates error: commandID=%s, status=%s", reply.commandID(), reply.status())
                    raise FSM.FSMException(f"Updater reply indicates error: commandID={reply.commandID()}, status={reply.status()}")
                return reply.payload() if reply.payload() else reply

            logging.debug(
                "Discarding unrelated updater reply commandID=%s while waiting for commandID=%s",
                reply.commandID(),
                expected_command_id,
            )
        