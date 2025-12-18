from .udp_client import UDP
from threading import Thread
from utils.utilities import Toolbox, CircularBuffer, Signal
import logging


class Socket:
    def __init__(self, udpSocket=None, callback=None, recvBuffSize=4096) -> None:
        self.dataReceived = Signal()
        self.__udpSocket : UDP = udpSocket
        self.__callback = callback
        self.__thread = None
        self.__recvBuffSize : int = recvBuffSize
        if callback is not None:
            self.__thread = Thread(target=self.__receptionThread, daemon=True)
            self.__thread.start()


    def __receptionThread(self) -> None:
        """
        Socket reception thread
        """
        # Loop until the underlying UDP socket is shut down
        while not self.__udpSocket.is_shutdown():
            data = self.__udpSocket.receive_data()
            if data is not None:
                self.dataReceived.emit(data)

        logging.info("Reception thread for socket exiting due to shutdown")


class NetworkManager:
    hostDiscovered = Signal()
    
    def __init__(self):
        self.__socketPool : dict = {}
        self.__searchHostThread : Thread | None = None


    def __searchHost(self):
        """
        Host IP discovery service
        """
        ip = None
        while ip is None:
            ip = UDP.searchHostName()
        logging.info("IP found at %s", ip)
        self.hostDiscovered.emit(ip)


    def startDiscovery(self) -> None:
        """
        Start the host discovery service
        """
        if self.__searchHostThread is None or not self.__searchHostThread.is_alive():
            self.__searchHostThread = Thread(target=self.__searchHost, daemon=True)
            self.__searchHostThread.start()


    def openAdapter(self, name : str, port : tuple, recvCallback=None, recvBuffSize=4096) -> UDP:
        """
        Opens an adapter to the specified IP

        Args:
            ip (str): _description_
            port (int): _description_

        Returns:
            bool: _description_
        """
        port, ip = port
        # Create the underlying UDP adapter
        udp_adapter = UDP(port, ip)

        # If no remote IP was provided, this adapter is intended for receiving
        # so bind it to the local port so recvfrom() will receive packets.
        try:
            if not ip:
                udp_adapter.bindSocket(port)
                logging.info("Bound UDP adapter '%s' to port %s for receiving", name, port)
        except Exception as e:
            logging.error("Failed to bind UDP adapter '%s': %s", name, e)

        # Create a Socket wrapper that runs the receive thread
        socket_wrapper = Socket(udp_adapter, recvCallback, recvBuffSize)

        # If a callback was provided, connect the Socket signal to it
        if recvCallback is not None:
            socket_wrapper.dataReceived.connect(recvCallback)

        self.__socketPool[name] = socket_wrapper

        # Return the underlying UDP adapter (caller expects UDP)
        return udp_adapter
    

    def getAdapterNames(self) -> list:
        """
        Return all registered adapter names

        Returns:
            list: _description_
        """
        return self.__socketPool.keys()