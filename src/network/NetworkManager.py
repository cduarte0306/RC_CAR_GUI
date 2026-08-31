import json

from .udp_client import UDP
from .tcp_client import TCP
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
    bufferSize : int
    sockDesc   : int
    dstPort    : int = 0
    srcPort    : int = 0
    name       : str = ""

_socketPool : dict = {}
_registeredSockMap : dict  = {}
_socketList : list[tuple[Socket, Socket]] = []
_dstPortToSocket : dict[int, list[tuple[Socket, Socket]]] = {}  # Maps socket descriptor to the socket name
_srcPortToSocket : dict[int, list[tuple[Socket, Socket]]] = {}  # Maps socket descriptor to the socket name
_IfaceIps : dict[int, str] = {}  # Local interface IP 
_RemoteIps : dict[int, str] = {} # Remote interface IP
_IfaceIps[IfaceId.WlanIface.value] = None
_IfaceIps[IfaceId.EthIface.value] = None

_RemoteIps[IfaceId.WlanIface.value] = None
_RemoteIps[IfaceId.EthIface.value] = None
_wlanSockOpenReqQueue = Queue(maxsize=50)
_ethSockOpenReqQueue = Queue(maxsize=50)

# Maximum UDP payload size for IPv4: 65535 - 20 byte IP header - 8 byte UDP header.
MAX_UDP = 65507


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
    dataReceived = Signal()

    def __init__(self):
        self.__threadPool = ThreadPoolExecutor(max_workers=10)
        self.__searchHostWlanFuture = None
        self.__shutdownEvent         = Event()
        self.__wlanDiscoveredEvent   = Event()
        self.__wlanQueueDrainedEvent = Event()
        self.__ethDiscoveredEvent    = Event()         
        self.__ethCreated : bool = False

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
        logging.info("RC Car found at %s", ip)
        _RemoteIps[IfaceId.WlanIface.value] = ip
        self.__wlanDiscoveredEvent.set()
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

    @staticmethod
    def getRemoteHostIP(prefer_ethernet: bool = True) -> str | None:
        """Return the discovered remote host IP, preferring Ethernet when available."""
        if prefer_ethernet:
            return _RemoteIps[IfaceId.EthIface.value] or _RemoteIps[IfaceId.WlanIface.value]
        return _RemoteIps[IfaceId.WlanIface.value] or _RemoteIps[IfaceId.EthIface.value]

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
        import json
        logging.info("Starting WLAN handshake with host at %s", hostIP)
        replyReceivedEvent = Event()
        sock : UDP = None
        ethIp : str = None
        hostNetMask : str = None
        devVersion : str = None
        ethInfo : tuple[str, str] = None
        def handleHandshakeMessageRx(data: bytes):
            self.dataReceived.emit()
            nonlocal ethInfo, devVersion
            message = data.decode("utf-8").strip()
            message = json.loads(message.strip())
            # Decode json handshake message and extract host IP if present
            replyingSrvrAddr = sock.getSrcAddr()

            if message["message"] != "HANDSHAKE_ACK":
                logging.debug("Ignoring non-ack handshake message '%s' from %s", message, replyingSrvrAddr)
                return


            # Decode the ethernet IP
            ethInfo = message
            devVersion = message.get("version", None)
            logging.info("Received WLAN handshake ACK from %s:\r\n%s", replyingSrvrAddr, json.dumps(ethInfo, indent=4))
            replyReceivedEvent.set()

        # Open port at wlan IP
        sock = NetworkManager.openUDPAdapter(
            (_IfaceIps[IfaceId.WlanIface.value], Defines.HANDSHAKE_PORT),
            recvCallback=handleHandshakeMessageRx
        )

        sock.set_timeout(5.0)
        sock.set_broadcast(False)

        while not self.__shutdownEvent.is_set() and not replyReceivedEvent.is_set():
            # Transmit handshake
            sock.send("HANDSHAKE-SEND".encode("utf-8"), hostIP)
            # Wait a bit for ACK before sending the next probe.
            replyReceivedEvent.wait(1.0)

        wlanAckReceived = replyReceivedEvent.is_set()
        sock.shutdown()
        replyReceivedEvent.clear()  
        self.__shutdownEvent.clear()
        self.__ethHandshakeHandler(ethInfo)

        logging.info("WLAN handshake with host at %s completed with ACK: %s", hostIP, wlanAckReceived)
        if wlanAckReceived and onDeviceConnected is not None:
            onDeviceConnected((hostIP, devVersion or ""))

    def __ethHandshakeHandler(self, ethInfo) -> None:
        """
        Ethernet handshake service
        """
        if self.__ethCreated:
            logging.info("Ethernet handshake already completed; skipping...")
            return
        sock : UDP = None
        replyingSrvrAddr : tuple[str, int] | None = None
        handshakeReceived = Event()

        def handleHandshakeMessageRx(data: bytes):
            self.dataReceived.emit()
            nonlocal replyingSrvrAddr
            message = data.decode("utf-8").strip()
            replyingSrvrAddr = sock.getSrcAddr()
            if replyingSrvrAddr is None:
                logging.error("Failed to get source address from handshake message; ignoring")
                return
            if message != "HANDSHAKE_ACK":
                logging.debug("Ignoring non-ack handshake message '%s' from %s", message, replyingSrvrAddr)
                return
            logging.info("Received ETH handshake ACK from %s", replyingSrvrAddr)
            handshakeReceived.set()
        logging.info("Starting Ethernet handshake with host IP %s, netmask %s", ethInfo["eth_ip"], ethInfo.get("net_mask"))

        # Check if host adapter is compatible with local Ethernet adapter before starting the handshake
        ethIp : str = None
        netmask : str = None
        while ethIp is None and not self.__shutdownEvent.is_set():
            adapters = NetworkManager.determineNicIp()
            adapterEntry = adapters["ethernet"]
            if adapterEntry is None:
                # No ethernet adapter found, just exit
                logging.warning("No ethernet adapter found")
                return
            else:
                ethIp, netmask = adapterEntry
            if ethIp is None:
                logging.info("Ethernet adapter not found; retrying in 5 seconds...")
                self.__shutdownEvent.wait(5)

        # Check if netmask of local eth adapter is compatible with host IP subnet before starting the handshake
        while not NetworkManager.isIpInSubnet(ethInfo["eth_ip"], f"{ethIp}/{netmask}") and not self.__shutdownEvent.is_set():
            logging.info("Host Ethernet IP %s is not in the same subnet as local Ethernet adapter IP %s with netmask %s; retrying in 5 seconds...", ethInfo["eth_ip"], ethIp, netmask)
            self.__shutdownEvent.wait(5)

        _IfaceIps[IfaceId.EthIface.value] = ethIp
        logging.info("Ethernet adapter found with IP %s, netmask %s; starting handshake listener", ethIp, netmask)

        # Open adapter at ethernet
        sock = NetworkManager.openUDPAdapter(
            (ethIp, Defines.HANDSHAKE_PORT),
            recvCallback=handleHandshakeMessageRx,
            recvBuffSize=1024
        )

        sock.set_timeout(5.0)
        logging.info("Ethernet handshake listener started on %s:%s", ethIp, Defines.HANDSHAKE_PORT)

        ethHostIp = ethInfo.get("eth_ip") if ethInfo is not None else None
        if ethHostIp is None:
            logging.info("Ethernet handshake stopped due to shutdown before host IP was set")
            sock.shutdown()
            return

        while not self.__shutdownEvent.is_set() and not handshakeReceived.is_set():
            # Transmit handshake
            sock.send("HANDSHAKE-SEND".encode("utf-8"), ethHostIp)
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

        # Persist the discovered peer, then retire the handshake listener so it
        # does not keep consuming and logging additional ACK packets.
        _RemoteIps[IfaceId.EthIface.value] = replyingSrvrAddr[0]  # Store the replying server's IP as the Ethernet host IP for routing purposes
        sock.shutdown()
        self.__ethDiscoveredEvent.set()
        while not self.__shutdownEvent.is_set() and not _ethSockOpenReqQueue.empty():
            req : SockReq = _ethSockOpenReqQueue.get()        
            if req == None:
                continue

            eth  = NetworkManager.openUDPAdapter(
                (ethIp, req.dstPort, req.srcPort),
                recvBuffSize=req.bufferSize, 
                recvCallback=req.callback
            )

            logging.info("Opened Ethernet UDP adapter for port %s with descriptor %s", req.dstPort, req.sockDesc)
            # Retrieve the socket entry from the descriptor map
            iFaces : tuple = _socketList[req.sockDesc]
            iFaces[IfaceId.EthIface.value] = eth
            
        self.__ethCreated = True

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
    def openNetworkAdapter(adapterInfo : tuple, protocol: str = "udp", recvCallback=None, recvBuffSize=4096):
        """
        Open a network adapter for the selected transport protocol.

        Args:
            adapterInfo (tuple): Adapter information tuple (local_bind_ip, port) or (local_bind_ip, port, src_port)
            protocol (str): Transport protocol ("udp" or "tcp")
            recvCallback (callable, optional): Callback function for received data
            recvBuffSize (int, optional): Receive buffer size for the socket wrapper. Defaults to 4096.

        Returns:
            UDP | TCP: The created network adapter
        """
        proto = (protocol or "udp").strip().lower()
        if proto == "udp":
            return NetworkManager.openUDPAdapter(adapterInfo, recvCallback=recvCallback, recvBuffSize=recvBuffSize)
        if proto == "tcp":
            return NetworkManager.openTCPAdapter(adapterInfo, recvCallback=recvCallback, recvBuffSize=recvBuffSize)
        raise NetworkErr(f"Unsupported protocol '{protocol}'. Expected 'udp' or 'tcp'.")

    @staticmethod
    def openUDPAdapter(adapterInfo : tuple, recvCallback=None, recvBuffSize=4096) -> UDP:
        """
        Opens an adapter to the specified IP

        Args:
            adapterInfo (tuple): Adapter information tuple (local_bind_ip, port) or (local_bind_ip, port, src_port)
            recvCallback (callable, optional): Callback function for received data
            recvBuffSize (int, optional): Receive buffer size for the UDP socket. Defaults to 4096.

        Returns:
            UDP: The created UDP adapter
        """
        # NetworkManager.__validateRecvCallback(recvCallback)

        dstPort = 0
        ipHost = ""
        ipLocal = ""
        
        # Pos 1: Local IP
        # Pos 2: Destination port
        srcPort : int = 0
        if len(adapterInfo) == 2:
            localIp, dstPort = adapterInfo
        elif len(adapterInfo) == 3:
            localIp, dstPort, srcPort = adapterInfo
        else:
            raise NetworkErr("Invalid adapter info tuple; expected (local_bind_ip, port)")

        # When callers pass a 3-tuple for inbound adapters but omit srcPort,
        # bind to dstPort so host-pushed telemetry/stream packets can arrive.
        if srcPort is None:
            srcPort = dstPort
        
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
            "Opening UDP adapter: kind=%s, port=%s, localIp=%s",
            adapter_kind,
            dstPort,
            localIp
        )

        # Create the underlying UDP adapter
        udp_adapter = UDP(dstPort)

        # If no remote IP was provided, this adapter is intended for receiving
        # so bind it to the local port so recvfrom() will receive packets.
        try:
            bind_ok = udp_adapter.bindSocket(srcPort, localIp)
            if not bind_ok:
                raise NetworkErr(
                    f"Failed to bind UDP adapter ({adapter_kind}) localIp={localIp} srcPort={srcPort} dstPort={dstPort}"
                )
            logging.info(
                "Bound UDP adapter (%s) to local %s:%s (dest port %s)",
                adapter_kind,
                localIp,
                srcPort,
                dstPort,
            )
        except Exception as e:
            logging.error("Failed to bind UDP adapter %s", e)
            raise

        # Create a Socket wrapper that runs the receive thread
        socket_wrapper = Socket(udp_adapter, recvCallback, recvBuffSize)

        # If a callback was provided, connect the Socket signal to it
        if recvCallback is not None:
            socket_wrapper.dataReceived.connect(recvCallback)

        # Return the underlying UDP adapter (caller expects UDP)
        return udp_adapter

    @staticmethod
    def openTCPAdapter(adapterInfo : tuple, recvCallback=None, recvBuffSize=4096) -> TCP:
        """
        Opens a TCP adapter to the specified port.

        Args:
            adapterInfo (tuple): Adapter information tuple (local_bind_ip, port) or (local_bind_ip, port, src_port)
            recvCallback (callable, optional): Callback function for received data
            recvBuffSize (int, optional): Receive buffer size for the socket wrapper. Defaults to 4096.

        Returns:
            TCP: The created TCP adapter
        """
        srcPort : int = 0
        if len(adapterInfo) == 2:
            localIp, dstPort = adapterInfo
        elif len(adapterInfo) == 3:
            localIp, dstPort, srcPort = adapterInfo
        else:
            raise NetworkErr("Invalid adapter info tuple; expected (local_bind_ip, port)")

        if dstPort is None:
            raise NetworkErr("Port number must be specified for TCP adapter opening")

        if srcPort is None:
            srcPort = 0

        adapter_kind = "unknown"
        if localIp and localIp == _IfaceIps[IfaceId.EthIface.value]:
            adapter_kind = "eth"
        elif localIp and localIp == _IfaceIps[IfaceId.WlanIface.value]:
            adapter_kind = "wlan"
        elif localIp in (None, "", "0.0.0.0"):
            adapter_kind = "auto"

        logging.info(
            "Opening TCP adapter: kind=%s, port=%s, localIp=%s",
            adapter_kind,
            dstPort,
            localIp
        )

        tcp_adapter = TCP(dstPort)
        try:
            bind_ok = tcp_adapter.bindSocket(srcPort, localIp)
            if not bind_ok:
                raise NetworkErr(
                    f"Failed to bind TCP adapter ({adapter_kind}) localIp={localIp} srcPort={srcPort} dstPort={dstPort}"
                )
            logging.info(
                "Bound TCP adapter (%s) to local %s:%s (dest port %s)",
                adapter_kind,
                localIp,
                srcPort,
                dstPort,
            )
        except Exception as e:
            logging.error("Failed to bind TCP adapter %s", e)
            raise

        socket_wrapper = Socket(tcp_adapter, recvCallback, recvBuffSize)
        if recvCallback is not None:
            socket_wrapper.dataReceived.connect(recvCallback)

        return tcp_adapter
    
    @staticmethod
    def isRemoteHostReachable() -> bool:
        """
        Check if the remote host is reachable via either Ethernet or Wi-Fi.

        Returns:
            bool: True if reachable, False otherwise
        """
        replyReceivedEvent = Event()
        sock : UDP = None

    @staticmethod
    def getRemoteHostIp() -> str | None:
        """
        Get the remote host IP address, preferring Ethernet when available.

        Returns:
            str | None: The remote host IP address if reachable, None otherwise
        """
        ip = None
        while ip is None:
            ip = NetworkManager.searchHostName()
        return ip

    @staticmethod
    def getUDPAdapter(
        dstPort : int = None,
        srcPort : int = None,
        recvBuffSize : int = 4096,
        OnRx : callable = None,
        name : str = ""
    ) -> int :
        """
        Get the UDP adapter by name. Create if none available

        Args:
            dstPort (int) : Destination port
            srcPort (int) : Source port (optional, use 0 for kernel-assigned ephemeral port)
            recvBuffSize (int) : Receive buffer size for the UDP socket
            OnRx (callable) : Reception callback

        Returns:
            int: Adapter descriptor if found, -1 otherwise
        """
        if not isinstance(dstPort, int):
            raise NetworkErr(
                "Unrecognized port type: %s (value=%r)" % (type(dstPort).__name__, dstPort)
            )
            
        if (dstPort is None) and (srcPort is None):
            raise NetworkErr("At least one of dstPort or srcPort must be non-zero for UDP adapter creation")

        desc : int = -1
        if dstPort in _dstPortToSocket:
            entry = _dstPortToSocket.get(dstPort)
            if not isinstance(entry, (list, tuple)) or len(entry) < 1:
                raise NetworkErr(f"Corrupted adapter mapping for port {dstPort}: {entry!r}")

            desc = entry[0]
            if not isinstance(desc, int) or desc < 0:
                raise NetworkErr(f"Invalid adapter descriptor for port {dstPort}: {desc!r}")

            # Descriptor must always index into _socketList.
            if desc >= len(_socketList):
                raise NetworkErr(
                    f"Descriptor out of range for port {dstPort}: desc={desc}, socket_count={len(_socketList)}"
                )

            # Self-heal mapping payload if tuple/list payload is missing.
            if len(entry) == 1:
                _dstPortToSocket[dstPort] = [desc, _socketList[desc]]

            logging.info("Reusing existing UDP adapter descriptor %s for port %s", desc, dstPort)
        elif srcPort in _srcPortToSocket:
            entry = _srcPortToSocket.get(srcPort)
            if not isinstance(entry, (list, tuple)) or len(entry) < 1:
                raise NetworkErr(f"Corrupted adapter mapping for source port {srcPort}: {entry!r}")

            desc = entry[0]
            if not isinstance(desc, int) or desc < 0:
                raise NetworkErr(f"Invalid adapter descriptor for source port {srcPort}: {desc!r}")

            # Descriptor must always index into _socketList.
            if desc >= len(_socketList):
                raise NetworkErr(
                    f"Descriptor out of range for source port {srcPort}: desc={desc}, socket_count={len(_socketList)}"
                )

            # Self-heal mapping payload if tuple/list payload is missing.
            if len(entry) == 1:
                _srcPortToSocket[srcPort] = [desc, _socketList[desc]]

            logging.info("Reusing existing UDP adapter descriptor %s for source port %s", desc, srcPort)
        elif dstPort is not None or srcPort is not None:
            # Descriptor is the index into _socketList.
            desc = len(_socketList)
            req = SockReq(
                callback=OnRx,
                dstPort=dstPort,
                srcPort=srcPort,
                bufferSize=recvBuffSize,
                sockDesc=desc,
                name=name
            )

            # Only submit to the wlan socket queue if the discovery queue is live
            _ethSockOpenReqQueue.put(req)
            
            _registeredSockMap[dstPort] = desc
            # Mark as registered immediately so repeated calls can reuse the descriptor.
            pair = [None, None]
            _socketList.append(pair)
            if dstPort is not None: _dstPortToSocket[dstPort] = [desc, pair]
            if srcPort is not None: _srcPortToSocket[srcPort] = [desc, pair]

            adapters = NetworkManager.determineNicIp()
            wifi_info = adapters["wifi"]
            _IfaceIps[IfaceId.WlanIface.value] = wifi_info[0] if wifi_info is not None else None

            wlan = NetworkManager.openUDPAdapter(
                (_IfaceIps[IfaceId.WlanIface.value], dstPort, srcPort), 
                recvCallback=req.callback,
                recvBuffSize=req.bufferSize,
            )
            
            iface_list = _socketList[req.sockDesc]
            iface_list[IfaceId.WlanIface.value] = wlan            
            logging.info("Registered new UDP adapter descriptor %s for port %s", desc, dstPort)
            
        return desc
    
    @staticmethod
    def write2PortSynch(
        desc : int,   # Descriptor
        data : bytes,  # Data to be sent
        timeout : int = -1
    ) -> bytes:
        """
        Write data to opened port and block until reply

        Args:
            desc (int): _description_

        Returns:
            bytes: Reply in bytes
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

    @staticmethod
    def write2Port(
        desc : int,   # Descriptor
        data : bytes,  # Data to be sent
        timeout : int = -1
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