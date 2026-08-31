import ctypes
import logging
import queue
from dataclasses import dataclass
from enum import Enum, auto
from threading import Event, Thread, Lock

import Defines
from network.udp_client import UDP
from utils.utilities import Signal

from network.NetworkManager import MAX_UDP, NetworkManager
import inspect
import asyncio

# class Command:
#     command_id: int
#     value: int | float
#     payload : bytes = b''
#     replyCallback : callable = None  # Optional callback for replies
#     signalCallback : Signal = None  # Optional signal for replies

#     def __init__(self, command_id: int, value: int | float, payload: bytes = b'', replyCallback: callable = None, signalCallback: Signal = None):
#         self.command_id = command_id
#         self.value = value
#         self.payload = payload
#         self.replyCallback = replyCallback
#         self.signalCallback = signalCallback


class ModuleIDs(Enum):
    NullModule  = 0
    MotorControllerModule  = auto()
    CameraControllerModule = auto()
    UpdaterModule          = auto()
    CommsModule            = auto()
    CliModule              = auto()
    TelemetryModule        = auto()
        
class val_type_t(ctypes.Union):
    _fields_ = [
        ("i", ctypes.c_int),
        ("f32", ctypes.c_float),
        ("u32", ctypes.c_uint),
        ("u16", ctypes.c_uint16),
        ("u8", ctypes.c_uint8),
    ]


class payload(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("command_id", ctypes.c_uint8),
        ("data", val_type_t),
        ("payloadLen", ctypes.c_uint32),
    ]

    
'''
struct ModMsgHdr {
        uint8_t command;
        val_type_t data;
        uint64_t payloadLen;
    } __attribute__((__packed__));
'''

'''
Layer 1 Ack: Replied by the top level system
'''
@dataclass
class MessageAck(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("moduleID",   ctypes.c_uint8),
        ("sequenceID", ctypes.c_uint16)
    ]
    
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        
    @staticmethod
    def getHeader(data: bytes) -> tuple:
        """Extract the header fields from the given data bytes."""
        if len(data) < ctypes.sizeof(MessageAck):
            logging.warning("Data too short to extract Command header: %d bytes", len(data))
            return ()
        ack = MessageAck.from_buffer_copy(data[:ctypes.sizeof(MessageAck)])
        return (ack.moduleID, ack.sequenceID)

'''
Layer 2 Ack: Replied by the receiving module
'''
@dataclass
class ModuleAck(MessageAck):
    _pack_ = 1
    _fields_ = [
        ("status",     ctypes.c_uint8),
        ("data",       val_type_t),
        ("payloadLen", ctypes.c_uint32),
    ]


@dataclass
class ReplyWireHeader(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("seqID", ctypes.c_uint16),
        ("commandID", ctypes.c_int32),
        ("status", ctypes.c_uint8),
        ("payloadSize", ctypes.c_uint32),
    ]


class Reply:
    """Host reply decoded from EthernetPipePacket::WireHeader + payload."""

    def __init__(self, seq_id: int, command_id: int, status: int, payload: bytes = b"") -> None:
        self._seq_id = int(seq_id)
        self._command_id = int(command_id)
        self._status = int(status)
        self._payload = bytes(payload)

    @staticmethod
    def headerSize() -> int:
        return ctypes.sizeof(ReplyWireHeader)

    @classmethod
    def fromWire(cls, raw: bytes) -> 'Reply | None':
        header_size = cls.headerSize()
        if len(raw) < header_size:
            logging.warning("Reply too short to extract wire header: %d bytes", len(raw))
            return None

        header = ReplyWireHeader.from_buffer_copy(raw[:header_size])
        payload_end = header_size + header.payloadSize
        if len(raw) < payload_end:
            logging.warning(
                "Reply payload truncated: have %d bytes, expect at least %d",
                len(raw),
                payload_end,
            )
            return None

        payload = raw[header_size:payload_end] if header.payloadSize > 0 else b""
        return cls(header.seqID, header.commandID, header.status, payload)

    def sequenceID(self) -> int:
        return self._seq_id

    def commandID(self) -> int:
        return self._command_id

    def status(self) -> int:
        return self._status

    def payload(self) -> bytes:
        return self._payload

    def __repr__(self) -> str:
        return (
            f"Reply(seqID={self._seq_id}, commandID={self._command_id}, "
            f"status={self._status}, payloadLen={len(self._payload)})"
        )

@dataclass
class Command(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("moduleId",   ctypes.c_uint8),  # Module ID for command destination
        ("sequenceId", ctypes.c_uint16), # Sequence ID for matching replies
        ("ack",        ctypes.c_uint8),  # Does this message expect an ack?
    ]
    
    def __init__(self, *args, socket=None, timeout:int=-1, **kwargs):
        super().__init__(*args, **kwargs)
        self._payload : bytes = b''
        self._replyCallback : callable = None # Optional callback for replies
        self._signalCallback : Signal = None # Optional signal for replies
        self._replyCallbackMode : str | None = None
        self._sequence_id : int = None  # To track the sequence ID for matching replies
        # Take reference to socket if needed for sending commands directly from the command instance
        self._socket = socket
        self._timeout = timeout

    def __str__(self) -> str:
        return self.__class__.__name__

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Ensure the structure fields are defined for each subclass
        if not hasattr(cls, "_fields_"):
            raise NotImplementedError(f"{cls.__name__} must define _fields_ for ctypes.Structure")
        
    def registerReplyCallback(self, callback: callable) -> None:
        """Register a callback to be invoked when a reply is received for this command."""
        sig = inspect.signature(callback)
        params = list(sig.parameters.values())

        if len(params) == 1:
            param = params[0]
            if param.annotation not in (inspect.Parameter.empty, Reply):
                annotation_name = getattr(param.annotation, "__name__", str(param.annotation))
                raise TypeError(
                    "Reply callback with one argument must accept Reply, "
                    f"got '{annotation_name}'"
                )
            self._replyCallbackMode = "reply"
        else:
            expected = [val_type_t, bytes]
            if len(params) < len(expected):
                raise TypeError(
                    f"Reply callback must accept Reply or {len(expected)} arguments "
                    f"(val_type_t, bytes), got {len(params)}"
                )

            for i, (param, expected_type) in enumerate(zip(params, expected)):
                if param.annotation not in (inspect.Parameter.empty, expected_type):
                    annotation_name = getattr(param.annotation, "__name__", str(param.annotation))
                    raise TypeError(
                        f"Callback argument {i} should be '{expected_type.__name__}', "
                        f"got '{annotation_name}'"
                    )
            self._replyCallbackMode = "legacy"

        self._replyCallback = callback
        self._signalCallback = Signal()         # Create a new signal for this command's replies
        self._signalCallback.connect(callback)  # Connect the callback to the signal so it will be emitted when a reply is received

    def getReplyCallback(self) -> callable:
        """Get the registered reply callback, if any."""
        return getattr(self, "_replyCallback", None)
    
    def emitReplyReceived(self, reply: Reply) -> None:
        if not isinstance(reply, Reply):
            raise TypeError("Expecting reply type to be Reply")
        if self._signalCallback is not None:
            if self._replyCallbackMode == "reply":
                self._signalCallback.emit(reply)
                return

            data = val_type_t()
            data.i = int(reply.status())
            self._signalCallback.emit(data, reply.payload())
        
    def setPayload(self, payload: bytes) -> None:
        """Set the payload for this command."""
        self._payload = payload
        
    def setSequenceId(self, seq_id: int) -> None:
        """Set the sequence ID for this command, used for matching replies."""
        self._sequence_id = seq_id
        self.sequenceId = seq_id
        
    def sequenceId(self) -> int:
        """Get the sequence ID for this command."""
        return self._sequence_id
        
    def getPayload(self) -> bytes:
        """Get the payload for this command."""
        return self._payload

    def serialize(self) -> bytes:
        """Serialize this structure instance to bytes for network transmission."""
        return ctypes.string_at(ctypes.addressof(self), ctypes.sizeof(self))

    def unpackReply(self, data : bytes):
        """Get the reply for this command, if available. This is a placeholder and should be implemented to return the actual reply data."""
        # Unpack the top layer mesasge and extract the module payload
        ack = MessageAck()
        ret = ack.get(data)
        if ret["status"] == 0:
            logging.debug("Command %s received reply: %s", self, ret)
            return ret

        data = self._deserialize(ack.get(data))

        # We unpack and fire the callback
        if data is not None and self._signalCallback is not None:
            self._signalCallback.emit(data)
    
    def _deserialize(self, data: bytes) -> None:
        """Deserialize bytes back into this structure instance. This is a placeholder and should be implemented to populate the structure fields from the given data."""
        # In a real implementation, this would likely involve parsing the bytes according to the expected structure format and populating the fields of this instance.
        pass

    def getBytes(self) -> bytes:
        """Serialize the command to bytes for sending over UDP."""
        return ctypes.string_at(ctypes.addressof(self), ctypes.sizeof(self)) + self.getPayload()

    def dispatchCommand(self, cmd : int, value: int | float, payload: bytes = b'', replyCallback: callable = None) -> None:
        """Configure this command instance and enqueue it on the command bus."""
        if not isinstance(payload, bytes):
            raise TypeError("Expecting payload type to be bytes")

        # Most module command payloads share these fields.
        if hasattr(self, "command"):
            self.command = int(cmd)
        if hasattr(self, "data"):
            if isinstance(value, float):
                self.data.f32 = float(value)
            else:
                self.data.i = int(value)
        if hasattr(self, "payloadLen"):
            self.payloadLen = len(payload)

        # ACK policy: only request ACK/reply tracking when an explicit callback is provided.
        if hasattr(self, "ack"):
            self.ack = 1 if replyCallback is not None else 0

        self.setPayload(payload)

        if replyCallback is not None:
            self.registerReplyCallback(replyCallback)
        else:
            # Ensure old callback state from previous dispatches does not leak into this send.
            self._replyCallback = None
            self._signalCallback = None
            self._replyCallbackMode = None

        CommandBus.getInstance().submit(self)

class RcCommands(Command):
    """
    Base RC Car command

    Args:
        Command (type): The base command class for RC Car commands.

    Returns:
        type: The initialized RC Car command instance.
    """
    def __init__(self, *args, socket=None, **kwargs):
        super().__init__(*args, socket=socket, **kwargs)
        
        # Initialize module fields
        self.moduleId = ModuleIDs.NullModule.value

    def ping(self, replyCallback: callable = None) -> None:
        """Example ping command to test connectivity and reply handling."""
        logging.debug("Sending ping command")
        self.dispatchCommand(cmd=0, value=0, payload=b'', replyCallback=replyCallback)

class CameraCommand(Command):
    # IDs pinned to host enum order in `.vscode/test2`.
    CmdStartStream            = 0
    CmdStopStream             = 1
    CmdSelCameraStream        = 2
    CmdSetFps                 = 3
    CmdSetQuality             = 4
    CmdSetMinDisparities      = 5
    CmdSetMaxDisparities      = 6
    CmdSetConfidenceThreshold = 7
    CmdSetUniquenessRatio     = 8
    CmdSetP1                  = 9
    CmdSetP2                  = 10
    CmdSetZMax                = 11
    CmdSetZMin                = 12
    CmdSetDepthThreshold      = 13
    CmdSetMinAgreeingPixels   = 14
    CmdSetColorThreshold      = 15
    CmdRdParams               = 16
    CmdClrVideoRec            = 17
    CmdSaveVideo              = 18
    CmdLoadStoredVideos       = 19
    CmdLoadSelectedVideo      = 20
    CmdDeleteVideo            = 21
    CmdCalibrationSetState    = 22
    CmdCalibrationWrtParams   = 23
    CmdCalibrationReset       = 24
    CmdCalibrationSave        = 25

    _pack_ = 1
    _fields_ = [
        ("command", ctypes.c_uint8),
        ("data", val_type_t),
        ("payloadLen", ctypes.c_uint32),
    ]

    def __init__(self, *args, socket=None, **kwargs):
        super().__init__(*args, socket=socket, **kwargs)
        
        # Initialize module fields
        self.moduleId = ModuleIDs.CameraControllerModule.value
        
    def ModuleStartStream(self, replyCallback: callable = None) -> None:
        logging.info("Sending ModuleStartStream command")
        self.dispatchCommand(self.CmdStartStream, value=1, replyCallback=replyCallback)
    
    def ModuleStopStream(self, replyCallback: callable = None) -> None:
        logging.info("Sending ModuleStopStream command")
        self.dispatchCommand(self.CmdStopStream, value=0, replyCallback=replyCallback)
    
    def ModuleSelectCameraStream(self, stream_mode: CamStreamSelectionModes, payload: bytes = b'', replyCallback: callable = None) -> None:
        logging.info("Sending ModuleSelectCameraStream command")
        self.dispatchCommand(self.CmdSelCameraStream, value=stream_mode.value, payload=payload, replyCallback=replyCallback)
    
    def ModuleSetFps(self, fps: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleSetFps command with fps={fps}")
        self.dispatchCommand(self.CmdSetFps, value=fps, replyCallback=replyCallback)
    
    def ModuleSetQuality(self, quality: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleSetQuality command with quality={quality}")
        self.dispatchCommand(self.CmdSetQuality, value=quality, replyCallback=replyCallback)
    
    def ModuleSetMinDisparities(self, min_disp: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleSetMinDisparities command with min_disp={min_disp}")
        self.dispatchCommand(self.CmdSetMinDisparities, value=min_disp, replyCallback=replyCallback)
    
    def ModuleSetMaxDisparities(self, max_disp: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleSetMaxDisparities command with max_disp={max_disp}")
        self.dispatchCommand(self.CmdSetMaxDisparities, value=max_disp, replyCallback=replyCallback)
    
    def ModuleSetConfidenceThreshold(self, conf_thresh: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleSetConfidenceThreshold command with conf_thresh={conf_thresh}")
        self.dispatchCommand(self.CmdSetConfidenceThreshold, value=conf_thresh, replyCallback=replyCallback)
    
    def ModuleSetUniquenessRatio(self, uniq_ratio: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleSetUniquenessRatio command with uniq_ratio={uniq_ratio}")
        self.dispatchCommand(self.CmdSetUniquenessRatio, value=uniq_ratio, replyCallback=replyCallback)
    
    def ModuleSetP1(self, p1: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleSetP1 command with p1={p1}")
        self.dispatchCommand(self.CmdSetP1, value=p1, replyCallback=replyCallback)
    
    def ModuleSetP2(self, p2: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleSetP2 command with p2={p2}")
        self.dispatchCommand(self.CmdSetP2, value=p2, replyCallback=replyCallback)
    
    def ModuleSetZMax(self, zmax: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleSetZMax command with zmax={zmax}")
        self.dispatchCommand(self.CmdSetZMax, value=zmax, replyCallback=replyCallback)
    
    def ModuleSetZMin(self, zmin: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleSetZMin command with zmin={zmin}")
        self.dispatchCommand(self.CmdSetZMin, value=zmin, replyCallback=replyCallback)
    
    def ModuleSetDepthThreshold(self, depth_thresh: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleSetDepthThreshold command with depth_thresh={depth_thresh}")
        self.dispatchCommand(self.CmdSetDepthThreshold, value=depth_thresh, replyCallback=replyCallback)
    
    def ModuleSetMinAgreeingPixels(self, min_agree: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleSetMinAgreeingPixels command with min_agree={min_agree}")
        self.dispatchCommand(self.CmdSetMinAgreeingPixels, value=min_agree, replyCallback=replyCallback)
    
    def ModuleSetColorThreshold(self, color_thresh: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleSetColorThreshold command with color_thresh={color_thresh}")
        self.dispatchCommand(self.CmdSetColorThreshold, value=color_thresh, replyCallback=replyCallback)
    
    def ModuleReadParams(self, replyCallback: callable = None) -> None:
        logging.info("Sending ModuleReadParams command")
        self.dispatchCommand(self.CmdRdParams, value=0, replyCallback=replyCallback)
    
    def ModuleClearVideoRecordings(self, replyCallback: callable = None) -> None:
        logging.info("Sending ModuleClearVideoRecordings command")
        self.dispatchCommand(self.CmdClrVideoRec, value=0, replyCallback=replyCallback)
    
    def ModuleSaveVideo(self, payload: bytes = b'', replyCallback: callable = None) -> None:
        logging.info("Sending ModuleSaveVideo command")
        self.dispatchCommand(self.CmdSaveVideo, value=0, payload=payload, replyCallback=replyCallback)
    
    def ModuleLoadStoredVideos(self, replyCallback: callable = None) -> None:
        logging.info("Sending ModuleLoadStoredVideos command")
        self.dispatchCommand(self.CmdLoadStoredVideos, value=0, replyCallback=replyCallback)

    def ModuleLoadSelectedVideo(self, video_id: int | str, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleLoadSelectedVideo command with video_id={video_id}")
        if isinstance(video_id, str):
            self.dispatchCommand(self.CmdLoadSelectedVideo, value=0, payload=video_id.encode("utf-8"), replyCallback=replyCallback)
            return
        self.dispatchCommand(self.CmdLoadSelectedVideo, value=video_id, replyCallback=replyCallback)

    def ModuleDeleteVideo(self, video_id: int | str, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleDeleteVideo command with video_id={video_id}")
        if isinstance(video_id, str):
            self.dispatchCommand(self.CmdDeleteVideo, value=0, payload=video_id.encode("utf-8"), replyCallback=replyCallback)
            return
        self.dispatchCommand(self.CmdDeleteVideo, value=video_id, replyCallback=replyCallback)

    def ModuleCalibrationSetState(self, state: int, replyCallback: callable = None) -> None:
        logging.info(f"Sending ModuleCalibrationSetState command with state={state}")
        self.dispatchCommand(self.CmdCalibrationSetState, value=state, replyCallback=replyCallback)
    
    def ModuleCalibrationWriteParams(self, payload: bytes = b'', replyCallback: callable = None) -> None:
        logging.info("Sending ModuleCalibrationWriteParams command")
        self.dispatchCommand(self.CmdCalibrationWrtParams, value=0, payload=payload, replyCallback=replyCallback)
    
    def ModuleCalibrationReset(self, replyCallback: callable = None) -> None:
        logging.info("Sending ModuleCalibrationReset command")
        self.dispatchCommand(self.CmdCalibrationReset, value=0, replyCallback=replyCallback)

    def ModuleCalibrationSave(self, payload: bytes = b'', replyCallback: callable = None) -> None:
        self.dispatchCommand(self.CmdCalibrationSave, value=0, payload=payload, replyCallback=replyCallback)

    def getBytes(self) -> bytes:
        return ctypes.string_at(ctypes.addressof(self), ctypes.sizeof(self)) + self.getPayload()  # Serialize the structure fields and append module payload if set

    def _deserialize(self, data: bytes) -> CameraCommand:
        """Unpack a reply from bytes to a MessageAck structure."""
        data = MessageAck().get(data)  # Camera module replies with a MessageAck format, so unpack that first to extract the status and payload
        self.status = data.get("status") 

class UpdaterCommand(Command):
    CmdRequestFirmwareRev = 1
    CmdInitUpdate         = 2
    CmdWriteFileData      = 3
    CmdInstallUpdate      = 4
    CmdQueryUpdateStatus  = 5
    CmdCleanUpdater       = 6
    CmdUpdaterFinalize    = 7

    _pack_ = 1
    _fields_ = [
        ("command", ctypes.c_uint8),
        ("data", val_type_t),
        ("payloadLen", ctypes.c_uint32),
    ]
    
    def __init__(self, *args, socket=None, blocking=None, **kwargs):
        super().__init__(*args, socket=socket, **kwargs)
        self._blocking = blocking
        self.moduleId = ModuleIDs.UpdaterModule.value

    @staticmethod
    def GetMaxPayload() -> int:
        return MAX_UDP - ctypes.sizeof(UpdaterCommand)
        # return 32768 - ctypes.sizeof(UpdaterCommand)

    def ModuleRequestRevision(self, replyCallback: callable = None, blocking=False) -> None:
        logging.info("Requesting firmware revision")
        self.dispatchCommand(self.CmdRequestFirmwareRev, 0, replyCallback=replyCallback)

    def ModuleInitUpdate(self, replyCallback: callable = None, fileName : str = "") -> None:
        payload = fileName.encode('utf-8')
        logging.debug("Initializing update with file: %s", fileName)
        self.dispatchCommand(self.CmdInitUpdate, 0, payload=payload, replyCallback=replyCallback)

    def ModuleWriteFileData(self, file_data: bytes, replyCallback: callable = None, blocking=False) -> None:
        # logging.debug("Writing file data of length: %d", len(file_data))
        self.dispatchCommand(self.CmdWriteFileData, 0, payload=file_data, replyCallback=replyCallback)

    def ModuleApplyUpdate(self, replyCallback: callable = None, blocking=False) -> None:
        logging.debug("Applying update")
        self.dispatchCommand(self.CmdInstallUpdate, 0, replyCallback=replyCallback)

    def ModuleQueryUpdateStatus(self, replyCallback: callable = None, blocking=False) -> None:
        logging.debug("Querying update status")
        self.dispatchCommand(self.CmdQueryUpdateStatus, 0, replyCallback=replyCallback)

    def ModuleFinalize(self, replyCallback: callable = None, blocking=False) -> None:
        logging.debug("Rebooting system")
        self.dispatchCommand(self.CmdUpdaterFinalize, 0, replyCallback=replyCallback)

    def ModuleCleanUpdater(self, replyCallback: callable = None, blocking=False) -> None:
        logging.debug("Cleaning updater")
        self.dispatchCommand(self.CmdCleanUpdater, 0, replyCallback=replyCallback)

    def getBytes(self) -> bytes:
        # Implement serialization logic specific to updater commands if needed
        return super().getBytes()  # Or provide custom serialization
    
class MotorCommands(Command):
    CmdSteer  = 0
    CmdFwdDir = 1
    CmdStop   = 2
    
    _pack_ = 1
    _fields_ = [
        ("command", ctypes.c_uint8),    
        ("data", val_type_t),
        ("payloadLen", ctypes.c_uint32),
    ]
    
    def __init__(self, *args, socket=None, **kwargs):
        super().__init__(*args, socket=socket, **kwargs)
        self.moduleId = ModuleIDs.MotorControllerModule.value
        
    def ModuleSteer(self, angle: float, replyCallback: callable = None) -> None:
        self.dispatchCommand(self.CmdSteer, value=angle, replyCallback=replyCallback)
    
    def ModuleFwdDir(self, speed: float, replyCallback: callable = None) -> None:
        self.dispatchCommand(self.CmdFwdDir, value=speed, replyCallback=replyCallback)
    
    def ModuleStop(self, replyCallback: callable = None) -> None:
        self.dispatchCommand(self.CmdStop, value=0, replyCallback=replyCallback)
    
    def getBytes(self) -> bytes:
        # Implement serialization logic specific to motor commands if needed
        return super().getBytes()  # Or provide custom serialization

class CamStreamSelectionModes(Enum):
    StreamCameraSource = 0
    StreamSimSource    = auto()

class clientReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("sequence_id", ctypes.c_uint16),
        ("msg_length", ctypes.c_uint16),
        ("payload", payload),
    ]

class CommandBus:
    """Single dispatch thread + queue for controller commands."""
    enqueueSignal = Signal()

    def __init__(self) -> None:
        self._cmdPipeSockFd = NetworkManager.getUDPAdapter(
            Defines.CONTROLLER_PORT,
            OnRx=self._processReply,
            recvBuffSize=1024
        )
        self._queue: queue.Queue[Command] = queue.Queue()
        self._lock = Lock()
        self._shutdown = Event()
        self._seq_id = 0
        self._expectedReplyPool : list[Command] = [None] * 65535
        self._lastAckedReplyID : int = 0

        self.enqueueSignal.connect(self.submit)
        self._thread = Thread(target=self._worker, name="command-bus", daemon=True)
        self._thread.start()
            
            
    @staticmethod
    def getInstance() -> 'CommandBus':
        """
        Singleton access method for CommandBus. Initializes the instance on first call.

        Returns:
            CommandBus: The singleton CommandBus instance
        """
        global _CommandBus
        if _CommandBus is None:
            _CommandBus = CommandBus()
        return _CommandBus


    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = Thread(target=self._worker, name="command-bus", daemon=True)
        self._thread.start()


    def shutdown(self) -> None:
        self._shutdown.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)


    def flushReplyCache(self) -> None:
        """Clear pending reply expectations."""
        with self._lock:
            for i in range(len(self._expectedReplyPool)):
                self._expectedReplyPool[i] = None


    def submit(self, cmd: Command) -> None:
        """Enqueue a command. Accepts Command or (cmd_id, value)."""
        # if isinstance(cmd, tuple):
        self._queue.put(cmd)

    def xmit(self, cmd : Command, timeout : int = 0) -> None:
        """ Blocks until reply """
        ok = NetworkManager.write2Port(
            self._cmdPipeSockFd,
            cmd.getBytes()
        )
        if not ok:
            raise Exception(f"Failed to transmit command: {cmd.command}")

    def _processReply(self, raw: bytes) -> None:
        """
        Process a reply callback from command socket

        Args:
            data (bytes): CmdAck data
        """
        if not raw:
            logging.warning("Empty CmdAck received from controller")
            return

        reply = Reply.fromWire(raw)
        if reply is None:
            return

        seqID = reply.sequenceID()
        logging.debug(
            "CmdAck received for command %d with sequence ID %d",
            reply.commandID(),
            seqID,
        )

        # Check if the message is not associated with a command
        if seqID == 0xFFFF:
            
            return

        # Is the command on the bank?
        with self._lock:
            if self._expectedReplyPool[seqID] is None:
                logging.debug("Command not found in the pool: %d", seqID)
                return

            # Search in expecting reply bank
            cmd : Command = self._expectedReplyPool[seqID]
            if cmd == None:
                logging.debug("Reply ID %d was not recognized", seqID)
                return

        logging.debug(
            "Received reply for command with sequence ID %d: commandID=%d, status=%d, payloadLen=%d",
            seqID,
            reply.commandID(),
            reply.status(),
            len(reply.payload()),
        )
        # Fire the command's reply callback signal
        cmd.emitReplyReceived(reply)
        self._lastAckedReplyID = seqID

        # Delete the object and remove from the bank to free memory and prevent stale matches
        with self._lock:
            self._expectedReplyPool[seqID] = None

        del cmd  # Free the command object after processing the reply

    def _worker(self) -> None:
        """
        Command dispatch thread
        """
        while not self._shutdown.is_set():
            try:
                cmd : Command = self._queue.get(timeout=5.0)
            except queue.Empty:
                continue

            ok = False
            seq_id = None
            try:
                with self._lock:
                    seq_id = self._seq_id & 0xFFFF
                    self._seq_id = (seq_id + 1) & 0xFFFF
                    # Track all commands so replies can be matched by sequence id
                    if cmd == None:
                        logging.debug("CommandBus: cmd is None!")

                    cmd.setSequenceId(seq_id)
                    packet_bytes : bytes = cmd.getBytes()
                ok = NetworkManager.write2Port(
                    self._cmdPipeSockFd,
                    packet_bytes
                )
                if not ok:
                    logging.error("CommandBus failed to send command %s", cmd)
                else:
                    if cmd.getReplyCallback() is not None and cmd.ack == 1:
                        self._expectedReplyPool[seq_id] = cmd
            except Exception as e:
                logging.error("CommandBus error while sending %s: %s", cmd, e)
            finally:
                if not ok and seq_id is not None:
                    with self._lock:
                        self._expectedReplyPool[seq_id] = None


    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

_CommandBus : CommandBus | None = CommandBus()