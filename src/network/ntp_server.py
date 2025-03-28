import socket
import struct
import ctypes
import time
from threading import Lock, Thread



class NTPPacket(ctypes.Structure):
    _fields_ = [
        ( "li_vn_mode"         , ctypes.c_uint8  ),
        ( "stratum"            , ctypes.c_uint8  ),
        ( "poll"               , ctypes.c_uint8  ),
        ( "precision"          , ctypes.c_int8   ),
        ( "root_delay"         , ctypes.c_int32  ),
        ( "root_dispersion"    , ctypes.c_int32  ),
        ( "ref_id"             , ctypes.c_uint32 ),
        ( "ref_timestamp_secs" , ctypes.c_uint32 ),
        ( "ref_timestamp_fraq" , ctypes.c_uint32 ),
        ( "orig_timestamp_secs", ctypes.c_uint32 ),
        ( "orig_timestamp_fraq", ctypes.c_uint32 ),
        ( "recv_timestamp_secs", ctypes.c_uint32 ),
        ( "recv_timestamp_fraq", ctypes.c_uint32 ),
        ( "tx_timestamp_secs"  , ctypes.c_uint32 ),
        ( "tx_timestamp_fraq"  , ctypes.c_uint32 ),
    ]


class NTPServer:

    NTP_EPOCH = 2208988800  # NTP epoch starts on Jan 1, 1900

    
    def __init__(self) -> None:
        self.__server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.__server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.__server.bind( ("", 123) )  # NTP port

        # Here, we start the server thread
        self.__server_thread = Thread(target=self.__server_thread)
        self.__server_thread.start()


    def __server_thread(self) -> None:
        """
        Main server thread that listens for incoming NTP requests and sends responses.
        """

        while True:
            data, addr = self.__receive_data()
            if data == b'':
                continue

            # Grab the reception time
            reception_timestamp : int = int(time.time())
            
            try:
                ntp_packet : NTPPacket = self.__parse_packet(data, reception_timestamp)
            except Exception as e:
                print(e)
                
            self.__send_data(ntp_packet, addr[0], addr[1])


    def __htonl_ctypes(self, val: ctypes.c_uint32) -> ctypes.c_uint32:
        v = val.value
        swapped = ((v & 0x000000FF) << 24) | \
                  ((v & 0x0000FF00) << 8)  | \
                  ((v & 0x00FF0000) >> 8)  | \
                  ((v & 0xFF000000) >> 24)
        return ctypes.c_uint32(swapped)


    def __ntohl_ctypes(self, val: ctypes.c_uint32) -> ctypes.c_uint32:
        v = val.value
        swapped = ((v & 0x000000FF) << 24) | \
                  ((v & 0x0000FF00) << 8)  | \
                  ((v & 0x00FF0000) >> 8)  | \
                  ((v & 0xFF000000) >> 24)
        return ctypes.c_uint32(swapped)


    def __parse_packet(self, data: bytes, reception_timeout : int) -> NTPPacket:
        """
        Parse an incoming NTP packet.

        Args:
            data (bytes): Raw data from the client
            reception_timeout (int): Reception time for the packet

        Returns:
            NTPPacket: Parsed NTP packet
        """
        ntp_packet_in: NTPPacket = NTPPacket.from_buffer_copy(data)

        ntp_packet_out: NTPPacket = NTPPacket()

        # Current time for all time-related fields
        recv_secs, recv_fraq = self.__time_to_ntp_ts(reception_timeout)
        
        # Header fields
        ntp_packet_out.li_vn_mode = (0b00 << 6) | (0b100 << 3) | 0b100  # LI=0, VN=4, Mode=3 (server)

        ntp_packet_out.stratum = 1
        ntp_packet_out.poll = 4
        ntp_packet_out.precision = -6  # ~15.625 ms

        # Reference information
        ntp_packet_out.root_delay = 0
        ntp_packet_out.root_dispersion = 0
        ntp_packet_out.ref_id = self.__ntohl_ctypes(ctypes.c_uint32(0x47505300))  # "GPS\0" as an example ref ID for a stratum 1 clock

        # Timestamps
        ntp_packet_out.ref_timestamp_secs = self.__ntohl_ctypes(recv_secs)
        ntp_packet_out.ref_timestamp_fraq = self.__ntohl_ctypes(recv_fraq)

        ntp_packet_out.orig_timestamp_secs = self.__ntohl_ctypes(ctypes.c_uint32(ntp_packet_in.tx_timestamp_secs))
        ntp_packet_out.orig_timestamp_fraq = self.__ntohl_ctypes(ctypes.c_uint32(ntp_packet_in.tx_timestamp_fraq))

        ntp_packet_out.recv_timestamp_secs = self.__ntohl_ctypes(recv_secs)
        ntp_packet_out.recv_timestamp_fraq = self.__ntohl_ctypes(recv_secs)

        tx_secs, tx_fraq = self.__time_to_ntp_ts(time.time())  # Recompute slightly later for TX
        ntp_packet_out.tx_timestamp_secs = self.__ntohl_ctypes(tx_secs)
        ntp_packet_out.tx_timestamp_fraq = self.__ntohl_ctypes(tx_fraq)

        return ntp_packet_out


    def __time_to_ntp_ts(self, t: int) -> tuple[ctypes.c_uint32, ctypes.c_uint32]:
        """
        Convert a UNIX timestamp (seconds since 1970) to NTP timestamp format.
        
        Returns:
            Tuple of (seconds, fraction) as ctypes.c_uint32
        """
        ntp_sec = int(t + self.NTP_EPOCH)
        ntp_frac = int((t % 1) * (2**32))  # Fractional part if t is float, but t is int so this is 0

        return ctypes.c_uint32(ntp_sec), ctypes.c_uint32(ntp_frac)


    def __receive_data(self) -> bytes:
        data: bytes = self.__server.recvfrom(1024)
        if data == None:
            return b''
        
        return data


    def __send_data(self, data: bytes, addr: str, port: int) -> bool:
        try:
            self.__server.sendto( data, (addr, port) )
        except:
            return False
        
        return True
    

    def close(self) -> None:
        self.__server.close()

