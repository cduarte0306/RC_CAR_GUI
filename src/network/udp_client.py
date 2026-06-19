import socket
import struct
from threading import Lock, Event
import logging
import select

import asyncio
import dataclasses
import itertools

from utils.utilities import Signal


class UDP:

    def __init__(
        self,
        port: int,
        timeout: float | None = None,
        log_timeouts: bool = False,
        enable_broadcast: bool = False,
    ) -> None:
        self.__socket_mutex = Lock()
        self.__socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.__socket.setblocking(False)
        self.__replyingSrvAddr: tuple[str, int] | None = None

        # Runtime-configurable timeout behavior for sync/async receive methods.
        self.__timeout: float | None = timeout
        self.__log_timeouts: bool = log_timeouts

        # Try to increase the OS receive buffer to reduce chance of ENOBUFS/10040
        try:
            desired_buf = 1280 * 720 *4 * 3  # 256 KiB
            self.__socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, desired_buf)
            logging.info("Set UDP socket SO_RCVBUF to %d", desired_buf)
        except Exception:
            logging.debug("Could not set SO_RCVBUF on UDP socket; continuing with defaults")

        # Enable broadcast only when explicitly requested.
        self.__broadcast_enabled = False
        self.set_broadcast(enable_broadcast)

        self.__shutdown_event = Event()

        # Exposed signals
        self.deviceFound = Signal()
        
        self.__dstPort = port
        
    def __del__(self):
        self.shutdown()


    def set_broadcast(self, enabled: bool) -> bool:
        """Enable or disable UDP broadcast sending on this socket."""
        try:
            self.__socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1 if enabled else 0)
            self.__broadcast_enabled = enabled
            logging.info("UDP broadcast %s", "enabled" if enabled else "disabled")
            return True
        except Exception as e:
            logging.error("Failed to set UDP broadcast mode: %s", e)
            return False
        
    def getSrcAddr(self) -> tuple[str, int] | None:
        """ Get the address of the server this client is communicating with """
        return self.__replyingSrvAddr


    def bindSocket(self, srcPort: int, ip: str = "0.0.0.0") -> bool:
        """
        Bind the socket

        Args:
            ip (str): IP address to bind to
            srcPort (int): Source port number to bind to (use 0 for ephemeral port assignment)

        Returns:
            bool: True if binding was successful, False otherwise
        """
        try:
            self.__socket.bind((ip, srcPort))
            # Get the actual assigned port, especially important for ephemeral (port 0)
            assigned_ip, assigned_port = self.__socket.getsockname()
            logging.info("UDP socket bound to %s:%d; Dest port: %d", assigned_ip, assigned_port, self.__dstPort)
            return True
        except Exception as e:
            logging.error("Failed to bind UDP socket: %s", e)
            return False


    def set_timeout(self, timeout: float) -> None:
        """
        Set a timeout for the socket operations.

        Args:
            timeout (float): Timeout in seconds. Use None for blocking mode.
        """
        self.__timeout = timeout
    

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
        dest_ip = ip
        if not dest_ip:
            logging.debug("Server IP not set. Cannot send data.")
            return False

        try:
            self.__socket.sendto(data, (dest_ip, self.__dstPort))
            if self.__broadcast_enabled:
                self.__replyingSrvAddr = (dest_ip, self.__dstPort)
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
            ready, _, _ = select.select([self.__socket], [], [], self.__timeout)
            if not ready:
                if self.__log_timeouts:
                    logging.warning("UDP.receive_data timeout")
                else:
                    logging.debug("UDP.receive_data timeout (suppressed)")
                return None
            data, addr = self.__socket.recvfrom(recv_size)  # no flags on Windows
            # Always record the source address so callers can identify the sender
            # (e.g. to reject self-originated datagrams on a shared TX/RX port).
            self.__replyingSrvAddr = addr
            return data
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
