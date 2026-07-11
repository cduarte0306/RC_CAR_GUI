import socket
from threading import Lock, Event
import logging
import select
import time

from utils.utilities import Signal

class TCP:
    def __init__(
        self,
        port: int,
        timeout: float | None = None,
        log_timeouts: bool = False,
    ) -> None:
        self.__socket_mutex = Lock()
        self.__socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.__socket.setblocking(False)
        self.__connected_addr: tuple[str, int] | None = None
        self.__local_bind: tuple[str, int] | None = None

        # Runtime-configurable timeout behavior for sync/async receive methods.
        self.__timeout: float | None = timeout
        self.__log_timeouts: bool = log_timeouts

        # Increase OS buffer sizes to reduce blocking during large transfers
        try:
            desired_buf = 16 * 1024 * 1024  # 16 MiB for both send and receive
            self.__socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, desired_buf)
            self.__socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, desired_buf)
            logging.info("Set TCP socket SO_RCVBUF and SO_SNDBUF to %d bytes", desired_buf)
        except Exception:
            logging.debug("Could not set TCP socket buffers; continuing with defaults")

        self.__shutdown_event = Event()

        # Exposed signals
        self.deviceFound = Signal()
        
        self.__dstPort = port
        
    def __del__(self):
        self.shutdown()


    def getSrcAddr(self) -> tuple[str, int] | None:
        """Get the address of the connected server endpoint."""
        return self.__connected_addr


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
            self.__local_bind = (assigned_ip, assigned_port)
            logging.info("TCP socket bound to %s:%d; Dest port: %d", assigned_ip, assigned_port, self.__dstPort)
            return True
        except Exception as e:
            logging.error("Failed to bind TCP socket: %s", e)
            return False

    def bind(self, ip: str = "0.0.0.0", srcPort: int = 0) -> bool:
        """Explicit bind API. Use srcPort=0 for ephemeral local port."""
        return self.bindSocket(srcPort=srcPort, ip=ip)


    def set_timeout(self, timeout: float) -> None:
        """
        Set a timeout for the socket operations.

        Args:
            timeout (float): Timeout in seconds. Use None for blocking mode.
        """
        self.__timeout = timeout
    

    def _ensure_connected(self, ip: str) -> bool:
        """Ensure there is an active TCP connection to the destination."""
        target = (ip, self.__dstPort)

        if self.__connected_addr == target:
            return True

        # Tear down any previous endpoint if target changed.
        if self.__connected_addr is not None and self.__connected_addr != target:
            try:
                self.__socket.close()
            except Exception:
                pass
            self.__socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.__socket.setblocking(True)
            if self.__timeout is not None:
                self.__socket.settimeout(self.__timeout)
            if self.__local_bind is not None:
                try:
                    self.__socket.bind(self.__local_bind)
                except Exception as e:
                    logging.error("Failed rebinding TCP socket to %s:%s: %s", self.__local_bind[0], self.__local_bind[1], e)
                    return False

        try:
            self.__socket.connect(target)
        except BlockingIOError:
            pass
        except OSError as e:
            # WSAEISCONN (10056): already connected
            if getattr(e, "winerror", None) != 10056:
                logging.error("Failed to connect TCP socket to %s:%s: %s", target[0], target[1], e)
                return False

        timeout = self.__timeout if self.__timeout is not None else 2.0
        _, writable, exceptional = select.select([], [self.__socket], [self.__socket], timeout)
        if exceptional or not writable:
            logging.error("TCP connect timeout to %s:%s", target[0], target[1])
            return False

        try:
            self.__socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.__socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except Exception:
            logging.debug("Could not set TCP options (TCP_NODELAY, SO_KEEPALIVE)")

        self.__connected_addr = target
        return True

    def connect(self, ip: str, port: int | None = None) -> bool:
        """Explicit connect API that also sets/overrides destination port when provided."""
        if port is not None:
            if not isinstance(port, int) or port <= 0:
                logging.error("Invalid TCP destination port: %s", port)
                return False
            self.__dstPort = port
        return self._ensure_connected(ip)


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
        # Allow callers to omit `ip` and use current connected endpoint if available.
        dest_ip = ip
        if not dest_ip:
            if self.__connected_addr is None:
                logging.debug("Server IP not set. Cannot send data.")
                return False
            dest_ip = self.__connected_addr[0]

        if not self._ensure_connected(dest_ip):
            return False

        if not data:
            return True

        payload = memoryview(data)
        total_sent = 0
        would_block_count = 0
        deadline = time.monotonic() + 120.0  # 2-minute timeout for large transfers

        while total_sent < len(payload):
            try:
                sent = self.__socket.send(payload[total_sent:])
                if sent == 0:
                    logging.error("TCP socket closed while sending data")
                    self.__connected_addr = None
                    return False
                total_sent += sent
                would_block_count = 0
                continue
            except BlockingIOError:
                pass
            except OSError as e:
                win_err = getattr(e, "winerror", None)
                errno_val = getattr(e, "errno", None)
                is_would_block = (
                    win_err == 10035
                    or errno_val in (socket.EWOULDBLOCK, getattr(socket, "WSAEWOULDBLOCK", 10035))
                )
                if not is_would_block:
                    logging.error("Failed to send TCP data: %s", e)
                    return False
            except Exception as e:
                logging.error("Failed to send TCP data: %s", e)
                return False

            would_block_count += 1
            if time.monotonic() >= deadline:
                logging.error(
                    "Failed to send TCP data after waiting for writable socket (sent %s/%s bytes, would-block retries=%s)",
                    total_sent,
                    len(payload),
                    would_block_count,
                )
                return False

            wait_s = min(0.005 * (2 ** min(would_block_count, 6)), 0.2)
            _, writable, exceptional = select.select([], [self.__socket], [self.__socket], wait_s)
            if exceptional:
                logging.error("TCP socket entered exceptional state while sending")
                self.__connected_addr = None
                return False
            if writable:
                if would_block_count > 10:
                    logging.debug("TCP send buffer was full; waited for %d retries", would_block_count)
            else:
                if would_block_count > 20:
                    logging.warning("TCP socket send buffer still full after %d retries (%.1f sec)",
                                  would_block_count, time.monotonic() - (deadline - 30.0))

        return True


    def receive_data(self, size : int = 65507) -> bytes | None:
        """
        Receive data from the socket. Returns None if socket is closed or timeout occurs.
        
        Returns:
            bytes | None: Received data or None if no data or socket error
        """
        try:
            # Clamp requested size to a sensible upper bound
            recv_size = min(size, 65535)
            ready, _, _ = select.select([self.__socket], [], [], self.__timeout)
            if not ready:
                if self.__log_timeouts:
                    logging.warning("TCP.receive_data timeout")
                else:
                    logging.debug("TCP.receive_data timeout (suppressed)")
                return None
            data = self.__socket.recv(recv_size)
            if data == b"":
                # Peer closed connection.
                self.__connected_addr = None
                return None
            return data
        except OSError as e:
            if self.__shutdown_event.is_set():
                return None
            logging.warning("TCP.receive_data exception: %s", e)
            return None
        except Exception as e:
            logging.error("TCP.receive_data unexpected exception: %s", e)
            return None

    def shutdown(self) -> None:
        """
        Gracefully shutdown the TCP client and close the socket
        """
        logging.info("Shutting down TCP client...")
        self.__shutdown_event.set()
        try:
            try:
                self.__socket.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            self.__socket.close()
        except Exception as e:
            logging.error("Error closing socket: %s", e)

    def is_shutdown(self) -> bool:
        """Return True if shutdown has been initiated."""
        return self.__shutdown_event.is_set()
