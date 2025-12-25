import ctypes
import logging
import queue
from dataclasses import dataclass
from enum import Enum, auto
from threading import Event, Thread

from network.udp_client import UDP
from utils.utilities import Signal


class commands(Enum):
    CMD_NOOP = 0
    CMD_FWD_DIR = 1
    CMD_STEER = 2
    CMD_CAMERA_SET_MODE = 3


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
    
    
class replyPayload(ctypes.Structure):
    _pack_ = 1
    _fields_ = [    
        ("data", val_type_t),
        ("status", ctypes.c_uint8),
    ]


class CameraCommand(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("command", ctypes.c_uint8),
        ("data", val_type_t),
    ]
    
class CamCommands(Enum):
    CmdSetFrameRate = 0
    CmdStartStream  = auto()
    CmdStopStream   = auto()
    CmdStreamMode   = auto()
    CmdSelMode      = auto()
    CmdClrVideoRec  = auto()
    

class CamStreamModes(Enum):
    StreamCamera = 0
    StreamSim    = auto()
    


class clientReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("sequence_id", ctypes.c_uint32),
        ("msg_length", ctypes.c_uint16),
        ("payload", payload),
    ]


@dataclass
class Command:
    command_id: int
    value: int | float
    payload : bytes = b''
    replyCallback: callable = None


class CommandBus:
    """Single dispatch thread + queue for controller commands."""
    enqueueSignal = Signal()
    replyReceived = Signal(replyPayload)
    
    def __init__(self, udp: UDP, start_immediately: bool = True) -> None:
        self._udp = udp
        self._queue: queue.Queue[Command] = queue.Queue()
        self._shutdown = Event()
        self._seq_id = 0
        self._thread: Thread | None = None

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

        base_header_len = ctypes.sizeof(ctypes.c_uint32) + ctypes.sizeof(ctypes.c_uint16)
        min_frame  = base_header_len + ctypes.sizeof(payload)

        if len(data) < min_frame:
            logging.warning("Reply too short (%d bytes). Expected at least %d", len(data), min_frame)
            return

        try:
            # Parse sequence and message length (little-endian)
            seq_id = int.from_bytes(data[0:4], byteorder="little")
            msg_len = int.from_bytes(data[4:6], byteorder="little")

            # Validate overall length against msg_len field
            expected_len = base_header_len + msg_len
            if len(data) < expected_len:
                logging.warning("Reply truncated: have %d bytes, expect %d (msg_len=%d)", len(data), expected_len, msg_len)
                return

            combined_payload = data[6:6 + msg_len]

            base_size = ctypes.sizeof(payload)
            if msg_len < base_size:
                logging.warning("Reply payload too small (%d bytes) for header (%d bytes)", msg_len, base_size)
                return

            base_payload = payload.from_buffer_copy(combined_payload[:base_size])
            extra_payload = combined_payload[base_size:]

            if base_payload.payloadLen != len(extra_payload):
                logging.debug(
                    "Reply payload length mismatch (field=%d, actual=%d) seq=%d cmd=%d",
                    base_payload.payloadLen,
                    len(extra_payload),
                    seq_id,
                    base_payload.command_id,
                )

            # Log the reply contents for now; adapt as needed when UI consumes replies
            logging.debug(
                "Reply seq=%d cmd=%d data_i=%d data_f=%.3f extra_len=%d",
                seq_id,
                base_payload.command_id,
                base_payload.data.i,
                base_payload.data.f32,
                len(extra_payload),
            )
            
            # Unpack reply payload
            reply = replyPayload.from_buffer_copy(combined_payload[:ctypes.sizeof(replyPayload)])
            self.replyReceived.emit(reply)
        except Exception as exc:
            logging.error("Failed to process reply: %s", exc)


    def _build_packet(self, cmd: Command) -> bytes:
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

        seq = ctypes.c_uint32(self._seq_id)
        msg_len = ctypes.c_uint16(len(combined_payload))
        
        requestPayload = clientReq()
        requestPayload.sequence_id = seq.value
        requestPayload.msg_length = msg_len.value
        requestPayload.payload = base_payload

        # Serialize the fixed-size struct (header + base payload), then append any extra payload bytes
        request_bytes = ctypes.string_at(ctypes.addressof(requestPayload), ctypes.sizeof(requestPayload))
        packet_bytes = request_bytes + extra_payload

        self._seq_id += 1
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

            try:
                packet_bytes = self._build_packet(cmd)
                ok = self._udp.send(packet_bytes)
                if not ok:
                    logging.error("CommandBus failed to send command %s", cmd)
            except Exception as e:
                logging.error("CommandBus error while sending %s: %s", cmd, e)


    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())
