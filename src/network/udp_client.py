import socket
import struct
from threading import Lock, Event
import logging

from utils.utilities import Signal


class UDP:

    def __init__(self, port: int, host: str = "", timeout: float | None = None, log_timeouts: bool = False) -> None:
        self.__socket_mutex = Lock()
        self.__socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.__server_ip: str = host if host else ""

        # Runtime-configurable timeout behavior; default is blocking (no timeouts/log spam)
        self.__timeout: float | None = timeout
        self.__log_timeouts: bool = log_timeouts
        self.__socket.settimeout(self.__timeout)

        # Try to increase the OS receive buffer to reduce chance of ENOBUFS/10040
        try:
            desired_buf = 262144  # 256 KiB
            self.__socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, desired_buf)
            logging.info("Set UDP socket SO_RCVBUF to %d", desired_buf)
        except Exception:
            logging.debug("Could not set SO_RCVBUF on UDP socket; continuing with defaults")

        self.__shutdown_event = Event()

        # Exposed signals
        self.deviceFound = Signal()
        
        self.__port = port

    
    @staticmethod
    def searchHostName() -> str | None:
        """
        Hostname search service
        """
        try:
            ip = socket.gethostbyname("rc-car-machine.local")
            if len(ip) > 0:
                return ip
        except:
            return None
        
        return None
    
    
    def setServerIP(self, ip: str) -> None:
        """
        Set the server IP address

        Args:
            ip (str): Server IP address
        """
        self.__server_ip = ip


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
    

    def send(self, data: bytes, ip: str = None) -> bool:
        """
        Transmit data to the server (RC Car)

        Args:
            data (bytes): Data to transmit

        Returns:
            bool: 
                - TRUE: Transmitted data succesfully
                - FALSE: Failed to transmit data
        """
        # Allow callers to omit `ip` and use configured server IP from constructor
        dest_ip = ip if ip else self.__server_ip
        if not dest_ip:
            logging.debug("Server IP not set. Cannot send data.")
            return True

        try:
            self.__socket.sendto(data, (dest_ip, self.__port))
        except Exception as e:
            logging.error("Failed to send UDP data: %s", e)
            return False
        
        return True


    def receive_data(self, size : int = 65507) -> bytes | None:
        """
        Receive data from the socket. Returns None if socket is closed or timeout occurs.
        
        Returns:
            bytes | None: Received data or None if no data or socket error
        """
        try:
            # Clamp requested size to a sensible UDP maximum
            recv_size = min(size, 65535)
            data, addr = self.__socket.recvfrom(recv_size)  # no flags on Windows
            return data
        except socket.timeout as e:
            if self.__log_timeouts:
                logging.warning("UDP.receive_data timeout: %s", e)
            else:
                logging.debug("UDP.receive_data timeout (suppressed)")
            return None
        except OSError as e:
            if self.__shutdown_event.is_set():
                return None
            # OSError 10040 occurs when the incoming datagram is larger than the
            # receive buffer. Log a warning and return None so callers can handle it.
            logging.warning("UDP.receive_data exception: %s", e)
            return None
        except Exception as e:
            logging.error("UDP.receive_data unexpected exception: %s", e)
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

    def is_shutdown(self) -> bool:
        """Return True if shutdown has been initiated."""
        return self.__shutdown_event.is_set()