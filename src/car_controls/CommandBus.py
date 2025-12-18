import ctypes
import logging
import queue
from dataclasses import dataclass
from enum import Enum, auto
from threading import Event, Thread

from network.udp_client import UDP
from utils.utilities import Signal, Toolbox


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


class CameraCommand(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("command", ctypes.c_uint8),
        ("data", val_type_t),
    ]


class clientReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("sequence_id", ctypes.c_uint32),
        ("msg_length", ctypes.c_uint16),
        ("payload", payload),
        ("crc32", ctypes.c_uint32),
    ]


@dataclass
class Command:
    command_id: int
    value: int | float
    payload : bytes = b''


class CommandBus:
    """Single dispatch thread + queue for controller commands."""

    def __init__(self, udp: UDP, start_immediately: bool = True) -> None:
        self._udp = udp
        self._queue: queue.Queue[Command] = queue.Queue()
        self._shutdown = Event()
        self._seq_id = 0
        self._thread: Thread | None = None

        # Allow producers to emit into the bus via a signal
        self.enqueueSignal = Signal()
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


    def _build_packet(self, cmd: Command) -> bytes:
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
        crc = ctypes.c_uint32(Toolbox.crc32(combined_payload))

        packet_bytes = (
            ctypes.string_at(ctypes.addressof(seq), ctypes.sizeof(seq))
            + ctypes.string_at(ctypes.addressof(msg_len), ctypes.sizeof(msg_len))
            + combined_payload
            + ctypes.string_at(ctypes.addressof(crc), ctypes.sizeof(crc))
        )

        self._seq_id += 1
        return packet_bytes


    def _worker(self) -> None:
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
