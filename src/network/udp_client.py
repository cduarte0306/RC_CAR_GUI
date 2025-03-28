import socket
import struct
from threading import Lock

from network.ntp_server import NTPServer


class UDPCLient:

    CAR_STATIC_IP = "192.168.1.10"
    PORT = 57345

    def __init__(self) -> None:
        self.__socket_mutex = Lock()
        self.__socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.__server_ip : str = ""
        self.__timeout : int = 5  # 5 seconds default timeout
        self.__ntp_server = NTPServer()


    def set_timeout(self, timeout: float) -> None:
        """
        Set a timeout for the socket operations.

        Args:
            timeout (float): Timeout in seconds. Use None for blocking mode.
        """
        self.__timeout = timeout
        self.__socket.settimeout(self.__timeout)
    

    def send( self, data: bytes ) -> bool:
        """
        Transmit data to the server (RC Car)

        Args:
            data (bytes): Data to transmit

        Returns:
            bool: 
                - TRUE: Transmitted data succesfully
                - FALSE: Failed to transmit data
        """
        try:
            self.__socket.sendto( data, (UDPCLient.CAR_STATIC_IP, UDPCLient.PORT ) )
        except:
            return False
        
        return True


    def receive_data( self ) -> bytes:
        data: bytes = self.__socket.recvfrom(4096)
        if data == None:
            return b''
        
        return data

