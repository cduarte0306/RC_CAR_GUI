import socket
import struct
from threading import Lock


class UDPCLient:

    PORT = 5555

    def __init__(self) -> None:
        self.__socket_mutex = Lock()
        self.__socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.__server_ip : str = ""


    def open_connection( self, ip : str ) -> bool:
        """
        Handles the openning of the connection

        Args:
            ip (str): IP address to connect to

        Returns:
            bool:
                - TRUE:     Connection succesful
                - FALSE: Failed to connect
        """
        if len( ip ) == 0:
            return False
        
        self.__server_ip = ip
        return True
    

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
        if not len(self.__server_ip):
            return False
        
        self.__socket.sendto( data, (self.__server_ip, UDPCLient.PORT ) )


        
