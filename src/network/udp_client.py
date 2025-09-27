import socket
import struct
from threading import Lock

from threading import Thread
import time
from time import sleep

import logging


class UDPCLient:

    CAR_STATIC_IP = "192.168.1.10"
    PORT = 65000

    def __init__(self) -> None:
        self.__socket_mutex = Lock()
        self.__socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.__server_ip : str = ""
        self.__timeout : int = 5  # 5 seconds default timeout
        thread = Thread(target=self.__search_hostname)
        thread.daemon = True
        thread.start()


    def __search_hostname(self) -> None:
        """
        Hostname search service
        """
        while True:
            ip = socket.gethostbyname("rc-car-machine.local")
            if len(ip) > 0:
                self.__server_ip = ip
                logging.info("Found RC car at address %s", self.__server_ip)
                break
            
            time.sleep(5)


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
            self.__socket.sendto( data, (self.__server_ip, UDPCLient.PORT ) )
        except:
            return False
        
        return True


    def receive_data(self) -> bytes | None:
        try:
            data, addr = self.__socket.recvfrom(4096)  # no flags on Windows
            return data
        except TimeoutError:
            return None