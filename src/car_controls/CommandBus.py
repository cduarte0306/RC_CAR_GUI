import ctypes
import logging
import queue
from dataclasses import dataclass
from enum import Enum, auto
from threading import Event, Thread, Lock

from network.udp_client import UDP
from utils.utilities import Signal


class commands(Enum):
    CMD_NOOP = 0
    CMD_FWD_DIR = 1
    CMD_STEER = 2
    CMD_CAMERA_MODULE = 3


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
    
    
class ReplyPayload(ctypes.Structure):
    _pack_ = 1
    _fields_ = [    
        ("data", val_type_t),
        ("status", ctypes.c_uint8),
        ("payloadLen", ctypes.c_uint32),
    ]


class CameraCommand(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("command", ctypes.c_uint8),
        ("data", val_type_t),
        ("payloadLen", ctypes.c_uint32),
    ]

class CamCommands(Enum):
    CmdStartStream          = 0
    CmdStopStream           = auto()
    CmdSelCameraStream      = auto()
    CmdSetFps               = auto()
    CmdSetQuality           = auto()
    CmdSetNumDisparities    = auto()
    
    CmdSetPreFilterType     = auto()
    CmdSetPreFilterSize     = auto()
    CmdSetPreFilterCap      = auto()
    CmdSetTextureThreshold  = auto()
    CmdSetUniquenessRatio   = auto()
    
    CmdSetBlockSize         = auto()
    CmdRdParams             = auto()
    CmdClrVideoRec          = auto()
    CmdSaveVideo            = auto()
    CmdLoadStoredVideos     = auto()
    CmdLoadSelectedVideo    = auto()
    CmdDeleteVideo          = auto()
    CmdCalibrationSetState  = auto()
    CmdCalibrationWrtParams = auto()
    CmdCalibrationSave      = auto()
    

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


@dataclass
class Command:
    command_id: int
    value: int | float
    payload : bytes = b''
    replyCallback : callable = None  # Optional callback for replies
    signalCallback : Signal = None  # Optional signal for replies
    
    
class Reply:
    def __init__(self, status: int, data_i: int, data_f: float, payload: bytes = b'') -> None:
        self.__status = status
        self.__data_i = data_i
        self.__data_f = data_f
        self.__payload = payload


    def status(self) -> int:
        return self.__status


    def data_i(self) -> int:
        return self.__data_i
    

    def data_f(self) -> float:
        return self.__data_f
    
    
    def payload(self) -> bytes:
        return self.__payload


class CommandBus:
    """Single dispatch thread + queue for controller commands."""
    enqueueSignal = Signal()
    replyReceived = Signal(ReplyPayload)
    
    def __init__(self, udp: UDP, start_immediately: bool = True) -> None:
        self._udp = udp
        self._queue: queue.Queue[Command] = queue.Queue()
        self._lock = Lock()
        self._shutdown = Event()
        self._seq_id = 0
        self._thread: Thread | None = None
        self.__commandSentBank : dict = {}

        self.enqueueSignal.connect(self.submit)

        if start_immediately:
            self.start()


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
        """Clear the sent command bank."""
        with self._lock:
            self.__commandSentBank.clear()


    def submit(self, cmd: Command | tuple[int, int | float]) -> None:
        """Enqueue a command. Accepts Command or (cmd_id, value)."""
        if isinstance(cmd, tuple):
            cmd = Command(command_id=cmd[0], value=cmd[1])
        self._queue.put(cmd)


    def processReply(self, data: bytes) -> None:
        """
        Process a reply from the car

        Args:
            data (bytes): Reply data
        """
        if not data:
            logging.warning("Empty reply received from controller")
            return

        base_header_len = ctypes.sizeof(ctypes.c_uint16) + ctypes.sizeof(ctypes.c_uint16)
        min_frame  = base_header_len + ctypes.sizeof(ReplyPayload)

        if len(data) < min_frame:
            logging.warning("Reply too short (%d bytes). Expected at least %d", len(data), min_frame)
            return

        try:
            # Parse sequence and message length (little-endian)
            seq_id = int.from_bytes(data[0:2], byteorder="little")
            msg_len = int.from_bytes(data[2:4], byteorder="little")

            # Validate overall length against msg_len field
            expected_len = base_header_len + msg_len
            if len(data) < expected_len:
                logging.warning("Reply truncated: have %d bytes, expect %d (msg_len=%d)", len(data), expected_len, msg_len)
                return

            combined_payload = data[4:4 + msg_len]

            base_size = ctypes.sizeof(ReplyPayload)
            if msg_len < base_size:
                logging.warning("Reply payload too small (%d bytes) for header (%d bytes)", msg_len, base_size)
                return

            base_payload = ReplyPayload.from_buffer_copy(combined_payload[:base_size])
            extra_payload = combined_payload[base_size:]

            if base_payload.payloadLen != len(extra_payload):
                logging.debug(
                    "Reply payload length mismatch (field=%d, actual=%d) seq=%d",
                    base_payload.payloadLen,
                    len(extra_payload),
                    seq_id,
                )

            # Log the reply contents for now; adapt as needed when UI consumes replies
            logging.debug(
                "Reply seq=%d status=%d data_i=%d data_f=%.3f extra_len=%d",
                seq_id,
                base_payload.status,
                base_payload.data.i,
                base_payload.data.f32,
                len(extra_payload),
            )

            # Unpack reply payload
            reply = ReplyPayload.from_buffer_copy(combined_payload[:ctypes.sizeof(ReplyPayload)])

            with self._lock:
                # Extract and remove the command sent from the bank
                cmd: Command = self.__commandSentBank.pop(seq_id, None)
                if cmd:
                    # Assign the reply callback and signal if provided
                    if cmd.signalCallback is not None:
                        # DEBUG: Printing command fields and reply for tracing
                        hostReplyPacket = Reply(
                            status=reply.status,
                            data_i=reply.data.i,
                            data_f=reply.data.f32,
                            payload=extra_payload
                        )
                        cmd.signalCallback.emit(hostReplyPacket)
                        
                else:
                    logging.warning("No matching command found for reply seq=%d", seq_id)
            
            self.replyReceived.emit(reply)
            
        except Exception as exc:
            logging.error("Failed to process reply: %s", exc)


    def _build_packet(self, cmd: Command, seq_id: int) -> bytes:
        """
        Build the command packet to send over UDP
        Args:
            cmd (Command): Command to send
        """
        base_payload = payload()
        base_payload.command_id = int(cmd.command_id)
        base_payload.payloadLen = len(cmd.payload) if cmd.payload else 0

        if isinstance(cmd.value, float):
            base_payload.data.f32 = cmd.value
        else:
            base_payload.data.i = int(cmd.value)

        base_payload_bytes = ctypes.string_at(ctypes.addressof(base_payload), ctypes.sizeof(base_payload))
        extra_payload = cmd.payload if cmd.payload else b""
        combined_payload = base_payload_bytes + extra_payload

        seq = ctypes.c_uint16(seq_id)
        msg_len = ctypes.c_uint16(len(combined_payload))
        
        requestPayload = clientReq()
        requestPayload.sequence_id = seq.value
        requestPayload.msg_length = msg_len.value
        requestPayload.payload = base_payload

        # Serialize the fixed-size struct (header + base payload), then append any extra payload bytes
        request_bytes = ctypes.string_at(ctypes.addressof(requestPayload), ctypes.sizeof(requestPayload))
        packet_bytes = request_bytes + extra_payload

        return packet_bytes


    def _worker(self) -> None:
        """
        Command dispatch thread
        """
        while not self._shutdown.is_set():
            try:
                cmd = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue

            ok = False
            seq_id = None
            try:
                with self._lock:
                    seq_id = self._seq_id & 0xFFFF
                    self._seq_id = (seq_id + 1) & 0xFFFF
                    # Track all commands so replies can be matched by sequence id
                    self.__commandSentBank[seq_id] = cmd
                    packet_bytes = self._build_packet(cmd, seq_id)
                ok = self._udp.send(packet_bytes)
                if not ok:
                    logging.error("CommandBus failed to send command %s", cmd)
            except Exception as e:
                logging.error("CommandBus error while sending %s: %s", cmd, e)
            finally:
                if not ok and seq_id is not None:
                    with self._lock:
                        self.__commandSentBank.pop(seq_id, None)


    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())
