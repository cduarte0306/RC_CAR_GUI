import datetime
import socket
import struct
import time
import threading
import select
import queue

# --- Helper Functions ---

def system_to_ntp_time(timestamp):
    return timestamp + NTP.NTP_DELTA

def _to_int(timestamp):
    return int(timestamp)

def _to_frac(timestamp, n=32):
    return int(abs(timestamp - _to_int(timestamp)) * 2**n)

def _to_time(integ, frac, n=32):
    return integ + float(frac)/2**n


# --- Exception ---

class NTPException(Exception):
    pass


# --- NTP Constants ---

class NTP:
    _SYSTEM_EPOCH = datetime.date(*time.gmtime(0)[0:3])
    _NTP_EPOCH = datetime.date(1900, 1, 1)
    NTP_DELTA = (_SYSTEM_EPOCH - _NTP_EPOCH).days * 24 * 3600

    REF_ID_TABLE = {
        'DNC': "DNC routing protocol",
        'NIST': "NIST public modem",
        'TSP': "TSP time protocol",
        'DTS': "Digital Time Service",
        'ATOM': "Atomic clock (calibrated)",
        'VLF': "VLF radio (OMEGA, etc)",
        'callsign': "Generic radio",
        'LORC': "LORAN-C radionavidation",
        'GOES': "GOES UHF environment satellite",
        'GPS': "GPS UHF satellite positioning",
    }

    STRATUM_TABLE = {
        0: "unspecified",
        1: "primary reference",
    }

    MODE_TABLE = {
        0: "unspecified",
        1: "symmetric active",
        2: "symmetric passive",
        3: "client",
        4: "server",
        5: "broadcast",
        6: "reserved for NTP control messages",
        7: "reserved for private use",
    }

    LEAP_TABLE = {
        0: "no warning",
        1: "last minute has 61 seconds",
        2: "last minute has 59 seconds",
        3: "alarm condition (clock not synchronized)",
    }


# --- NTP Packet Structure ---

class NTPPacket:
    _PACKET_FORMAT = "!B B B b 11I"

    def __init__(self, version=2, mode=3, tx_timestamp=0):
        self.leap = 0
        self.version = version
        self.mode = mode
        self.stratum = 0
        self.poll = 0
        self.precision = 0
        self.root_delay = 0
        self.root_dispersion = 0
        self.ref_id = 0
        self.ref_timestamp = 0
        self.orig_timestamp = 0
        self.orig_timestamp_high = 0
        self.orig_timestamp_low = 0
        self.recv_timestamp = 0
        self.tx_timestamp = tx_timestamp
        self.tx_timestamp_high = 0
        self.tx_timestamp_low = 0

    def to_data(self):
        try:
            packed = struct.pack(NTPPacket._PACKET_FORMAT,
                (self.leap << 6 | self.version << 3 | self.mode),
                self.stratum,
                self.poll,
                self.precision,
                _to_int(self.root_delay) << 16 | _to_frac(self.root_delay, 16),
                _to_int(self.root_dispersion) << 16 | _to_frac(self.root_dispersion, 16),
                self.ref_id,
                _to_int(self.ref_timestamp),
                _to_frac(self.ref_timestamp),
                self.orig_timestamp_high,
                self.orig_timestamp_low,
                _to_int(self.recv_timestamp),
                _to_frac(self.recv_timestamp),
                _to_int(self.tx_timestamp),
                _to_frac(self.tx_timestamp))
        except struct.error:
            raise NTPException("Invalid NTP packet fields.")
        return packed

    def from_data(self, data):
        try:
            unpacked = struct.unpack(NTPPacket._PACKET_FORMAT,
                    data[0:struct.calcsize(NTPPacket._PACKET_FORMAT)])
        except struct.error:
            raise NTPException("Invalid NTP packet.")

        self.leap = unpacked[0] >> 6 & 0x3
        self.version = unpacked[0] >> 3 & 0x7
        self.mode = unpacked[0] & 0x7
        self.stratum = unpacked[1]
        self.poll = unpacked[2]
        self.precision = unpacked[3]
        self.root_delay = float(unpacked[4]) / 2**16
        self.root_dispersion = float(unpacked[5]) / 2**16
        self.ref_id = unpacked[6]
        self.ref_timestamp = _to_time(unpacked[7], unpacked[8])
        self.orig_timestamp = _to_time(unpacked[9], unpacked[10])
        self.orig_timestamp_high = unpacked[9]
        self.orig_timestamp_low = unpacked[10]
        self.recv_timestamp = _to_time(unpacked[11], unpacked[12])
        self.tx_timestamp = _to_time(unpacked[13], unpacked[14])
        self.tx_timestamp_high = unpacked[13]
        self.tx_timestamp_low = unpacked[14]

    def GetTxTimeStamp(self):
        return (self.tx_timestamp_high, self.tx_timestamp_low)

    def SetOriginTimeStamp(self, high, low):
        self.orig_timestamp_high = high
        self.orig_timestamp_low = low


# --- NTP Server Class ---

class NTPServer:
    def __init__(self, ip="0.0.0.0", port=123, version=3, stratum=2):
        self.ip = ip
        self.port = port
        self.version = version
        self.stratum = stratum
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("", self.port))
        self.task_queue = queue.Queue()
        self._stop_event = threading.Event()
        self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.work_thread = threading.Thread(target=self._work_loop, daemon=True)

        self.recv_thread.start()
        self.work_thread.start()


    def stop(self):
        print("Stopping NTP server...")
        self._stop_event.set()
        self.recv_thread.join()
        self.work_thread.join()
        self.socket.close()
        print("NTP server stopped.")

    def _recv_loop(self):
        while not self._stop_event.is_set():
            rlist, _, _ = select.select([self.socket], [], [], 1)
            for sock in rlist:
                try:
                    data, addr = sock.recvfrom(1024)
                    recv_timestamp = system_to_ntp_time(time.time())
                    self.task_queue.put((data, addr, recv_timestamp))
                except socket.error as e:
                    print("Socket error:", e)

    def _work_loop(self):
        while not self._stop_event.is_set():
            try:
                data, addr, recv_timestamp = self.task_queue.get(timeout=1)
                recv_packet = NTPPacket()
                recv_packet.from_data(data)

                tx_high, tx_low = recv_packet.GetTxTimeStamp()
                send_packet = NTPPacket(version=self.version, mode=4)
                send_packet.stratum = self.stratum
                send_packet.poll = 10
                send_packet.ref_timestamp = recv_timestamp - 5
                send_packet.SetOriginTimeStamp(tx_high, tx_low)
                send_packet.recv_timestamp = recv_timestamp
                send_packet.tx_timestamp = system_to_ntp_time(time.time())

                self.socket.sendto(send_packet.to_data(), addr)
                print(f"Responded to {addr[0]}:{addr[1]}")
            except queue.Empty:
                continue

