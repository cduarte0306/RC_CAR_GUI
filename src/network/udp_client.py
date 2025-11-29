import socket
import struct
from threading import Lock, Event

from threading import Thread
import time
from time import sleep

import logging

from utils.utilities import Signal


class UDP:

    def __init__(self, port : int, host:str="") -> None:
        self.__socket_mutex = Lock()
        self.__socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.__server_ip : str = host if host else ""
        self.__timeout : int = 0  # 5 seconds default timeout
        self.__shutdown_event = Event()

        # Exposed signals
        self.deviceFound = Signal()
        
        self.__port = port

        # Only start hostname search if no host was provided
        if not len(host):
            thread = Thread(target=self.__search_hostname, daemon=True)
            thread.start()


    def __search_hostname(self) -> None:
        """
        Hostname search service
        """
        while not self.__shutdown_event.is_set():
            try:
                ip = socket.gethostbyname("rc-car-machine.local")
                if len(ip) > 0:
                    self.__server_ip = ip
                    logging.info("Found RC car at address %s", self.__server_ip)
                    self.deviceFound.emit(ip)  # emit the Device found signal
                    break
            except:
                pass
            
            # Use wait instead of sleep for graceful shutdown
            self.__shutdown_event.wait(timeout=5)


    def bindSocket(self, port) -> bool:
        """
        Bind the socket

        Args:
            ip (_type_): _description_
            port (_type_): _description_

        Returns:
            bool: _description_
        """
        self.__socket.bind(("0.0.0.0", self.__port))


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
        if not self.__server_ip:
            logging.error("Server IP not set. Cannot send data.")
            return False
            
        try:
            self.__socket.sendto( data, (self.__server_ip, self.__port ) )
        except Exception as e:
            logging.error("Failed to send UDP data: %s", e)
            return False
        
        return True


    def receive_data(self, size : int = 4096) -> bytes | None:
        """
        Receive data from the socket. Returns None if socket is closed or timeout occurs.
        
        Returns:
            bytes | None: Received data or None if no data or socket error
        """
        try:
            data, addr = self.__socket.recvfrom(size)  # no flags on Windows
            return data
        except (TimeoutError, OSError, socket.error) as e:
            # OSError 10022 occurs when socket is closed during receive
            # Return None to signal thread to exit gracefully
            print(e)
            return None


    def shutdown(self) -> None:
        """
        Gracefully shutdown the UDP client and close the socket
        """
        logging.info("Shutting down UDP client...")
        self.__shutdown_event.set()
        
        try:
            self.__socket.close()
        except Exception as e:
            logging.error("Error closing socket: %s", e)