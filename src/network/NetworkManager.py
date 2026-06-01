from .udp_client import UDP
from threading import Thread
from utils.utilities import Toolbox, CircularBuffer, Signal
import logging
import Defines

import psutil
import socket
from zeroconf import Zeroconf, ServiceBrowser, ServiceInfo

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from queue import Queue, Empty
from enum import Enum
import inspect
from dataclasses import dataclass


class IfaceId(Enum):
    EthIface  = 0
    WlanIface = 1
    
    
@dataclass
class SockReq:
    callback   : callable
    port       : int
    bufferSize : int
    sockDesc   : int


_socketPool : dict = {}
_registeredSockMap : dict  = {}
_socketList : list[tuple[Socket, Socket]] = []
_portToSocket : dict[int, list[tuple[Socket, Socket]]] = {}  # Maps socket descriptor to the socket name
_IfaceIps : dict[int, str] = {}  # Local interface IP 
_RemoteIps : dict[int, str] = {} # Remote interface IP
_IfaceIps[IfaceId.WlanIface.value] = None
_IfaceIps[IfaceId.EthIface.value] = None

_RemoteIps[IfaceId.WlanIface.value] = None
_RemoteIps[IfaceId.EthIface ] = None
_wlanSockOpenReqQueue = Queue(maxsize=50)
_ethSockOpenReqQueue = Queue(maxsize=50)


class Socket:
    def __init__(self, udpSocket=None, callback=None, recvBuffSize=4096) -> None:
        self.dataReceived = Signal()
        self.__udpSocket : UDP = udpSocket
        self.__callback = callback
        self.__thread = None
        self.__recvBuffSize : int = recvBuffSize
        self.__threadPool = ThreadPoolExecutor(max_workers=1)
        
        if callback is not None:
            self.__threadPool.submit(self.__receptionThread)
            
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
        
        
class NetworkErr(Exception):
    def __init__(self, message):
        super().__init__(message)

class NetworkManager:
    hostDiscovered = Signal(str)
    
    def __init__(self):
        self.__threadPool = ThreadPoolExecutor(max_workers=10)
        self.__searchHostWlanFuture = None
        self.__ethHandshakeFuture = None
        self.__openWlanQueueFuture = None
        self.__openEthQueueFuture = None
        self.__shutdownEvent       = Event()
        self.__wlanDiscoveredEvent = Event()
        self.__wlanQueueDrainedEvent = Event()
        self.__ethDicoveredEvent   = Event()
        
    def __openWlanSocketQueueHndlr(
        self
    ) -> None:
        """
        WLAN socket creation queue handler to open sockets while discovery is still in progress
        Returns:
            None
        """
        while not self.__shutdownEvent.is_set():
            # Wait until WLAN discovery is complete before processing queued requests.
            if not self.__wlanDiscoveredEvent.wait(timeout=0.5):
                continue

            try:
                req : SockReq = _wlanSockOpenReqQueue.get(timeout=0.25)
            except Empty:
                # Queue is drained for now.
                self.__wlanQueueDrainedEvent.set()
                continue
            except Exception:
                continue

            self.__wlanQueueDrainedEvent.clear()

            try:
                wlan_ip = _IfaceIps[IfaceId.WlanIface.value]
                remote_wlan_ip = _RemoteIps[IfaceId.WlanIface.value]
                if wlan_ip is None or remote_wlan_ip is None:
                    # Discovery state changed; put request back and retry later.
                    _wlanSockOpenReqQueue.put(req)
                    continue

                wlan = NetworkManager.openUDPAdapter(
                    (req.port, wlan_ip, remote_wlan_ip),
                    recvCallback=req.callback,
                    recvBuffSize=req.bufferSize,
                )

                if req.sockDesc >= len(_socketList):
                    logging.error("Invalid WLAN socket descriptor %s for port %s", req.sockDesc, req.port)
                    continue

                iface_list = _socketList[req.sockDesc]
                iface_list[IfaceId.WlanIface.value] = wlan
            except Exception as exc:
                logging.error("Failed to open queued WLAN UDP adapter on port %s: %s", req.port, exc)
            finally:
                try:
                    _wlanSockOpenReqQueue.task_done()
                except Exception:
                    pass
                
                
    def __openEthSocketQueueHndlr(
        self
    ) -> None:
        """
        ETH socket creation queue handler to open sockets while discovery is still in progress
        Returns:
            None
        """
        while not self.__shutdownEvent.is_set():
            self.__ethDicoveredEvent.wait()
            req : SockReq = _ethSockOpenReqQueue.get()

    def __searchHostWlan(self):
        """
        Host IP discovery service
        """
        ip = None
        
        adapters = NetworkManager.determineNicIp()
        wifi_info = adapters["wifi"]
        _IfaceIps[IfaceId.WlanIface.value] = wifi_info[0] if wifi_info is not None else None
        while ip is None:
            ip = NetworkManager.searchHostName()
        logging.info("IP found at %s", ip)
        
        _RemoteIps[IfaceId.WlanIface.value] = ip
        self.__wlanDiscoveredEvent.set()

        # Create queued WLAN sockets in this discovery thread before notifying listeners.
        while not self.__shutdownEvent.is_set():
            try:
                req : SockReq = _wlanSockOpenReqQueue.get_nowait()
            except Empty:
                break

            try:
                wlan_ip = _IfaceIps[IfaceId.WlanIface.value]
                remote_wlan_ip = _RemoteIps[IfaceId.WlanIface.value]
                if wlan_ip is None or remote_wlan_ip is None:
                    _wlanSockOpenReqQueue.put(req)
                    break

                wlan = NetworkManager.openUDPAdapter(
                    (req.port, wlan_ip, remote_wlan_ip),
                    recvCallback=req.callback,
                    recvBuffSize=req.bufferSize,
                )

                if req.sockDesc < len(_socketList):
                    iface_list = _socketList[req.sockDesc]
                    iface_list[IfaceId.WlanIface.value] = wlan
                else:
                    logging.error("Invalid WLAN socket descriptor %s for port %s", req.sockDesc, req.port)
            except Exception as exc:
                logging.error("Failed to open queued WLAN UDP adapter on port %s: %s", req.port, exc)
            finally:
                try:
                    _wlanSockOpenReqQueue.task_done()
                except Exception:
                    pass

        self.__wlanQueueDrainedEvent.set()
        
        self.hostDiscovered.emit(ip)

    def startDiscovery(self) -> None:
        """
        Start the host discovery service
        """
        if self.__searchHostWlanFuture is None or self.__searchHostWlanFuture.done():
            self.__wlanDiscoveredEvent.clear()
            self.__wlanQueueDrainedEvent.clear()
            # Add to thread pool and start the thread
            self.__searchHostWlanFuture = self.__threadPool.submit(self.__searchHostWlan)

        if self.__ethHandshakeFuture is None or self.__ethHandshakeFuture.done():
            self.__ethHandshakeFuture = self.__threadPool.submit(self.__ethHandshakeHandler)

        if self.__openEthQueueFuture is None or self.__openEthQueueFuture.done():
            self.__openEthQueueFuture = self.__threadPool.submit(self.__openEthSocketQueueHndlr)
            
        if self.__openWlanQueueFuture is None or self.__openWlanQueueFuture.done():
            self.__openWlanQueueFuture = self.__threadPool.submit(self.__openWlanSocketQueueHndlr)
            
    def StartConnection(self, hostIP : str, onDeviceConnected: callable = None) -> None:
        import ipaddress
        if not isinstance(hostIP, str):
            logging.error("Invalid host IP type: %s", type(hostIP).__name__)
            return
        
        # Check IP validity before starting the connection thread
        try:
            ipaddress.IPv4Address(hostIP)
        except ipaddress.AddressValueError:
            logging.error("Invalid host IP address: %s", hostIP)
            return
        self.__threadPool.submit(self.__wlanHandshakeHandler, hostIP, onDeviceConnected)
    
    def __wlanHandshakeHandler(self, hostIP: str, onDeviceConnected: callable = None) -> None:
        """
        WLAN handshake service
        
        Args:
            hostIP (str): The IP address of the host to perform the handshake with
        """
        logging.info("Starting WLAN handshake with host at %s", hostIP)
        replyReceivedEvent = Event()
        sock : UDP = None
        def handleHandshakeMessageRx(data: bytes):
            message = data.decode("utf-8").strip()
            replyingSrvrAddr = sock.getSrcAddr()

            if message != "HANDSHAKE_ACK":
                logging.debug("Ignoring non-ack handshake message '%s' from %s", message, replyingSrvrAddr)
                return
            logging.info("Received WLAN handshake ACK from %s", replyingSrvrAddr)
            replyReceivedEvent.set()
        
        # Open port at wlan IP
        sock = NetworkManager.openUDPAdapter(
            (Defines.HANDSHAKE_PORT, _IfaceIps[IfaceId.WlanIface.value], hostIP),
            recvBuffSize=1024,
            recvCallback=handleHandshakeMessageRx
        )
        
        sock.set_timeout(5.0)
        sock.set_broadcast(False)
        
        while not self.__shutdownEvent.is_set() and not replyReceivedEvent.is_set():
            # Transmit handshake
            sock.send("HANDSHAKE-SEND".encode("utf-8"), hostIP)
            # Wait a bit for ACK before sending the next probe.
            replyReceivedEvent.wait(1.0)
            
        sock.shutdown()
            
        logging.info("WLAN handshake with host at %s completed with ACK: %s", hostIP, replyReceivedEvent.is_set())
        if replyReceivedEvent.is_set() and onDeviceConnected is not None:
            onDeviceConnected(hostIP)
            
    def __ethHandshakeHandler(self):
        """
        Ethernet handshake service
        """
        ethIp : str = None
        sock : UDP = None
        replyingSrvrAddr : tuple[str, int] | None = None
        handshakeReceived = Event()
        
        def handleHandshakeMessageRx(data: bytes):
            nonlocal replyingSrvrAddr
            message = data.decode("utf-8").strip()
            replyingSrvrAddr = sock.getSrcAddr()
            if replyingSrvrAddr == ethIp:
                logging.debug("Received handshake message from self at %s; ignoring", replyingSrvrAddr)
                return
            if message != "HANDSHAKE_ACK":
                logging.debug("Ignoring non-ack handshake message '%s' from %s", message, replyingSrvrAddr)
                return
            logging.info("Received ETH handshake ACK from %s", replyingSrvrAddr)
            handshakeReceived.set()
            
        ethIp : str = None
        while ethIp is None and not self.__shutdownEvent.is_set():
            adapters = NetworkManager.determineNicIp()
            ethIp, _ = adapters["ethernet"]
            if ethIp is None:
                logging.info("Ethernet adapter not found; retrying in 5 seconds...")
                self.__shutdownEvent.wait(5)
                
        logging.info("Ethernet adapter found with IP %s; starting handshake listener", ethIp)
                
        # Extract first three octets of the Ethernet IP to determine the subnet (e.g. "192.168.1.")
        subnet = ".".join(ethIp.split(".")[:3]) + ".255"

        # Open adapter at ethernet
        sock = NetworkManager.openUDPAdapter(
            (Defines.HANDSHAKE_PORT, ethIp, subnet),
            recvCallback=handleHandshakeMessageRx,
            recvBuffSize=1024
        )
        
        sock.set_broadcast(True)
        sock.set_timeout(5.0)
    
        logging.info("Ethernet handshake listener started on %s:%s", ethIp, Defines.HANDSHAKE_PORT)
        
        while not self.__shutdownEvent.is_set() and not handshakeReceived.is_set():
            # Transmit handshake
            sock.send("HANDSHAKE-SEND".encode("utf-8"), subnet)
            # Wait a bit for ACK before sending the next probe.
            handshakeReceived.wait(1.0)

        if self.__shutdownEvent.is_set() and not handshakeReceived.is_set():
            logging.info("Ethernet handshake stopped due to shutdown before ACK")
            sock.shutdown()
            return

        if replyingSrvrAddr is None:
            logging.warning("Ethernet handshake ended without a valid ACK source")
            sock.shutdown()
            return
            
        
        # Handle eth queue requests
        _RemoteIps[IfaceId.EthIface.value] = replyingSrvrAddr[0]  # Store the replying server's IP as the Ethernet host IP for routing purposes
        self.__ethDiscoveredEvent.set()
        while not self.__shutdownEvent.is_set():
            req : SockReq = _ethSockOpenReqQueue.get()        
            if req == None:
                continue

            eth  = NetworkManager.openUDPAdapter(
                (req.port, ethIp, replyingSrvrAddr[0]),  
                recvBuffSize=req.bufferSize, 
                recvCallback=req.callback
            )
            
            # Retrieve the socket entry from the descriptor map
            iFaces : tuple = _socketList[req.sockDesc]
            iFaces[IfaceId.EthIface.value] = eth
            

    @staticmethod
    def isIpInSubnet(ip: str, subnet: str) -> bool:
        """
        Check if an IP address is in a given subnet

        Args:
            ip (str): The IP address to check
            subnet (str): The subnet in CIDR notation (e.g. "
        """
        import ipaddress
        try:
            ip_obj = ipaddress.IPv4Address(ip)
            subnet_obj = ipaddress.IPv4Network(subnet, strict=False)
            return ip_obj in subnet_obj
        except ValueError as e:
            logging.error("Invalid IP address or subnet: %s", e)
            return False

    @staticmethod
    def determineNicIp() -> dict:
        """Determine the IP address of the local Wi-Fi and Ethernet adapters

        Returns:
            dict: Adapter metadata containing a tuple per NIC type.
            Keys:
                - wifi
                - ethernet
            Values:
                - None if adapter type not found
                - (ip, netmask) when found
        """
        adapters = {
            "wifi": None,
            "ethernet": None,
        }
        try:
            adapters_info = psutil.net_if_addrs()
            adapters_stats = psutil.net_if_stats()
            for iface, addrs in adapters_info.items():
                if "vEthernet" in iface:             continue  # Skip Hyper-V virtual adapters
                if "Local Area Connection" in iface: continue  # Skip generic local area connections without clear type
                if "Bluetooth" in iface:             continue  # Skip Bluetooth adapters
                if "Loopback" in iface:              continue  # Skip loopback adapters
                if "vmware" in iface.lower():        continue  # Skip VMware virtual adapters
                iface_stats = adapters_stats.get(iface)
                for addr in addrs:
                    if "-" in addr.address or "::" in addr.address:  # Skip interfaces with hyphens (often virtual) or IPv6 addresses
                        continue
                    if addr.family == socket.AF_INET:
                        ip = addr.address
                        if "wi-fi" in iface.lower() or "wireless" in iface.lower():
                            adapters["wifi"] = (ip, addr.netmask)
                        elif "ethernet" in iface.lower():
                            # Only report Ethernet when link is up (cable connected).
                            if iface_stats is not None and iface_stats.isup:
                                adapters["ethernet"] = (ip, addr.netmask)
                            else:
                                logging.info("Ethernet adapter '%s' is down or disconnected", iface)
        except Exception as exc:
            logging.warning("Failed to determine local adapters: %s", exc)
        
        wifi_ip, wifi_mask = adapters["wifi"] if adapters["wifi"] is not None else (None, None)
        eth_ip, eth_mask = adapters["ethernet"] if adapters["ethernet"] is not None else (None, None)
        logging.info(
            "Determined local adapters: Wi-Fi=%s/%s, Ethernet=%s/%s",
            wifi_ip,
            wifi_mask,
            eth_ip,
            eth_mask,
        )
        return adapters
        
    
    @staticmethod
    def searchHostName() -> str | None:
        try:
            results = socket.getaddrinfo(
                "rc-car-machine.local", None,
                family=socket.AF_INET  # IPv4 only
            )
            ips = list({r[4][0] for r in results if r and r[4] and r[4][0]})
            if not ips:
                return None
            # Deterministic pick so downstream signals receive a single string.
            return sorted(ips)[0]
        except Exception as exc:
            logging.debug("searchHostName failed: %s", exc)
            return None

    @staticmethod
    def openUDPAdapter(adapterInfo : tuple, recvCallback=None, recvBuffSize=4096) -> UDP:
        """
        Opens an adapter to the specified IP

        Args:
            ip (str): _description_
            adapter (tuple): _description_

        Returns:
            bool: _description_
        """
        # NetworkManager.__validateRecvCallback(recvCallback)

        dstPort = 0
        ipHost = ""
        ipLocal = ""

        if len(adapterInfo) != 3:
            raise NetworkErr("Invalid adapter info tuple; expected (port, host_ip, local_bind_ip)")
        
        # Pos 1: Destination port
        # Pos 2: Local IP
        # Pos 3: Host IP
        dstPort, localIp, hostIP = adapterInfo
        if dstPort is None:
            raise NetworkErr("Port number must be specified for UDP adapter opening")

        # Determine requested adapter family based on the selected local bind IP.
        adapter_kind = "unknown"
        if localIp and localIp == _IfaceIps[IfaceId.EthIface.value]:
            adapter_kind = "eth"
        elif localIp and localIp == _IfaceIps[IfaceId.WlanIface.value]:
            adapter_kind = "wlan"
        elif localIp in (None, "", "0.0.0.0"):
            adapter_kind = "auto"

        logging.info(
            "Opening UDP adapter: kind=%s, port=%s, localIp=%s, hostIp=%s",
            adapter_kind,
            dstPort,
            localIp,
            hostIP,
        )
        
        # Create the underlying UDP adapter
        udp_adapter = UDP(dstPort, ipHost)

        # If no remote IP was provided, this adapter is intended for receiving
        # so bind it to the local port so recvfrom() will receive packets.
        try:
            udp_adapter.bindSocket(0, localIp)  # Use kernel assigned src port
            logging.info("Bound UDP adapter (%s) to port %s for receiving", adapter_kind, dstPort)

        except Exception as e:
            logging.error("Failed to bind UDP adapter %s", e)

        # Create a Socket wrapper that runs the receive thread
        socket_wrapper = Socket(udp_adapter, recvCallback, recvBuffSize)

        # If a callback was provided, connect the Socket signal to it
        if recvCallback is not None:
            socket_wrapper.dataReceived.connect(recvCallback)

        # Return the underlying UDP adapter (caller expects UDP)
        return udp_adapter

    @staticmethod
    def getUDPAdapter(
        port : int,
        recvBuffSize : int = 4096,
        OnRx : callable = None
    ) -> int :
        """
        Get the UDP adapter by name. Create if none available

        Args:
            port (int) : Destination port
            OnRx (callable) : Reception callback

        Returns:
            int: Adapter descriptor if found, -1 otherwise
        """
        if not isinstance(port, int):
            raise NetworkErr(
                "Unrecognized port type: %s (value=%r)" % (type(port).__name__, port)
            )
        desc : int = -1
        if port in _portToSocket:
            entry = _portToSocket.get(port)
            if not isinstance(entry, (list, tuple)) or len(entry) < 1:
                raise NetworkErr(f"Corrupted adapter mapping for port {port}: {entry!r}")

            desc = entry[0]
            if not isinstance(desc, int) or desc < 0:
                raise NetworkErr(f"Invalid adapter descriptor for port {port}: {desc!r}")

            # Descriptor must always index into _socketList.
            if desc >= len(_socketList):
                raise NetworkErr(
                    f"Descriptor out of range for port {port}: desc={desc}, socket_count={len(_socketList)}"
                )

            # Self-heal mapping payload if tuple/list payload is missing.
            if len(entry) == 1:
                _portToSocket[port] = [desc, _socketList[desc]]

            logging.info("Reusing existing UDP adapter descriptor %s for port %s", desc, port)
        else:
            # Descriptor is the index into _socketList.
            desc = len(_socketList)
            req = SockReq(
                callback=OnRx,
                port=port,
                bufferSize=recvBuffSize,
                sockDesc=desc,
            )
            # Request to wlan and eth
            _wlanSockOpenReqQueue.put(req)
            
            # Only submit to the wlan socket queue if the discovery queue is live
            
            _ethSockOpenReqQueue.put(req)
            
            _registeredSockMap[port] = desc
            # Mark as registered immediately so repeated calls can reuse the descriptor.
            pair = [None, None]
            _socketList.append(pair)
            _portToSocket[port] = [desc, pair]
            
            
        return desc
            
    @staticmethod
    def write2Port(
        desc : int,   # Descriptor
        data : bytes  # Data to be sent
    ) -> bool:
        """
        Write data to opened port

        Args:
            desc (int): _description_

        Returns:
            bool: _description_
        """
        if desc < 0 or desc >= len(_socketList):
            logging.error("Unrecognized socket descriptor")
            return False
        
        dstIp = ""
        ifaceList : tuple = _socketList[desc]
        ethAdapter : UDP  = ifaceList[IfaceId.EthIface.value]
        if ethAdapter != None:  # We prefer Ethernet when available, so send through it if possible
            dstIp = _RemoteIps[IfaceId.EthIface.value]
            ethAdapter.send(data, dstIp)
            return True

        # No Ethernet available, send through Wi-Fi if possible
        wlanAdapter : UDP = ifaceList[IfaceId.WlanIface.value]
        dstIp = _RemoteIps[IfaceId.WlanIface.value]
        if wlanAdapter is None: raise NetworkErr("No valid adapter found for writing")
        return wlanAdapter.send(data, dstIp)
