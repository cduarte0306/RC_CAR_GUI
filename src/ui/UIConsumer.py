from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot
from utils.utilities import CircularBuffer

from car_controls.VideoStreaming import VideoStreamer
from car_controls.controller import Controller
from car_controls.CommandBus import CommandBus, Command, commands, CameraCommand
from network.NetworkManager import NetworkManager
from network.udp_client import UDP

import numpy as np
import subprocess
import re
import ctypes

from threading import Thread
import time
import logging


class BackendIface(QThread):
    videoBufferSignal = pyqtSignal(object)  # Frame received signal
    deviceDiscovered  = pyqtSignal(str)  # Device discovered signal (emits IP)
    deviceConnected   = pyqtSignal(str)  # Device connected (emits IP)
    deviceMacResolved = pyqtSignal(str, str)  # Emits (ip, mac)
    videoModeRequested = pyqtSignal(str)  # Emits requested camera mode (regular/depth)

    CONTROLLER_PORT = 65000
    STREAM_PORT     = 5000
    TELEMETRY_PORT  = 65001

    def __init__(self):
        super().__init__()
        
        # Create the network manager
        self.__networkManager : NetworkManager = NetworkManager()

        # Create the adapters
        self.__controllerAdapter       : UDP = self.__networkManager.openAdapter("controller", (BackendIface.CONTROLLER_PORT, ""))
        # Create outbound adapter first (no receive callback)
        self.__videoStreamerOutAdapter : UDP = self.__networkManager.openAdapter("streamOut" , (BackendIface.STREAM_PORT, "192.168.1.10"), recvBuffSize=65507)

        # Create buffers and streamer before wiring the inbound adapter callback
        self.__videoBuffer    : CircularBuffer = CircularBuffer(100)
        # Create VideoStreamer without inbound adapter; inbound frames will be fed via callback
        self.__videoStreamer  : VideoStreamer  = VideoStreamer(None, self.__videoStreamerOutAdapter)

        # Now open the inbound adapter and pass our callback (starts receive thread)
        self.__videoStreamerInAdapter  : UDP = self.__networkManager.openAdapter("streamerIn", (BackendIface.STREAM_PORT, ""), self.__videoReceivedCallback, 65507)
        self.__commandBus     : CommandBus     = CommandBus(self.__controllerAdapter)
        self.__controller     : Controller     = Controller(self.__commandBus)

        # Open adapter for motor telemetry at port 5001
        self.__telemetryAdapter : UDP = self.__networkManager.openAdapter("telemetry", (BackendIface.TELEMETRY_PORT, ""))

        # controllerAdapter.deviceFound.connect(self.__deviceFound)
        self.__devicesPool : list  = []
        self.__connected_ip : str = ""
        self.__mac_cache : dict = {}

        # Connect signals
        # Forward discovered host IPs to the UI with the IP string
        self.__networkManager.hostDiscovered.connect(lambda ip: self.__on_host_discovered(ip))
        self.__videoStreamer.sendFrameSignal.connect(lambda pkt: self.__videoStreamOutThread(pkt))


    def __on_host_discovered(self, ip: str) -> None:
        # remember device and notify UI
        if ip not in self.__devicesPool:
            self.__devicesPool.append(ip)
        self.deviceDiscovered.emit(ip)


    def __videoReceivedCallback(self, data : bytes) -> None:
        """
        Video frame reception callback

        Args:
            data (bytes): Video frame data
        """
        self.__videoStreamer.setFrame(data)


    def __videoStreamOutThread(self, packet:bytes) -> None:
        """
        Video stream out thread
        """
        ret = self.__videoStreamerOutAdapter.send(packet)
        if not ret:
            logging.error("Failed to transmit frame over UDP")


    def __resolve_mac(self, ip: str) -> str:
        """Attempt to resolve MAC address for the given IP using the ARP cache."""
        try:
            # Windows/macOS friendly ARP query
            result = subprocess.run(["arp", "-a", ip], capture_output=True, text=True, check=False)
            output = result.stdout + result.stderr
            match = re.search(r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", output)
            return match.group(0) if match else ""
        except Exception as e:
            logging.debug("MAC resolution failed for %s: %s", ip, e)
            return ""


    def startDiscovery(self) -> None:
        """
        Start the device discovery service
        """
        self.__networkManager.startDiscovery()


    def startStreamOut(self, state : bool, fileName:str) -> None:
        print(state, fileName)
        self.__videoStreamer.setVideoSource(fileName)
        self.__videoStreamer.startStreamOut(state)


    @pyqtSlot(str)
    def setVideoMode(self, mode: str) -> None:
        """Switch camera rendering mode (regular/depth/training) and send to car."""
        mode_lc = (mode or "").lower()
        mode_map = {
            "regular": 0,  # CamModeNormal
            "normal": 0,
            "depth": 1,    # CamModeDepth
            "training": 2  # CamModeTraining
        }

        if mode_lc not in mode_map:
            logging.warning("Unknown camera mode requested: %s", mode)
            return

        value = mode_map[mode_lc]
        logging.info("Requested video mode: %s (%d)", mode_lc, value)
        self.videoModeRequested.emit(mode_lc)

        # Build nested CameraCommand payload
        cam_cmd = CameraCommand()
        cam_cmd.command = 3  # CmdSelMode on host
        cam_cmd.data.u8 = value
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))

        # Emit camera mode command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_SET_MODE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue camera mode command: %s", exc)


    def connectToDevice(self, ip: str) -> None:
        """Attempt to connect to the RC car at the given IP. This sets the
        selected device and emits `deviceConnected` for the UI to react.

        Note: actual transport-level controller binding may be performed elsewhere;
        here we record the selection and notify listeners."""
        self.__connected_ip = ip
        # Potential place to reconfigure adapters or start sessions
        self.__controllerAdapter.setServerIP(ip)

        # Kick off host unicast video by sending a NOOP command
        self.__commandBus.submit(Command(commands.CMD_NOOP.value, 0))

        self.__controller.StartComms()
        self.deviceConnected.emit(ip)

        # Resolve MAC once and emit
        mac = self.__mac_cache.get(ip) or self.__resolve_mac(ip)
        if mac:
            self.__mac_cache[ip] = mac
        self.deviceMacResolved.emit(ip, mac if mac else "")


    def getDevices(self) -> list:
        """
        Return the list of devices

        Returns:
            list: List of devices
        """
        return self.__devicesPool


    def run(self):
        """
        Main UI interface thread
        """
        while True:
            frame = self.__videoStreamer.getFrameIn()
            if frame is not None:
                self.videoBufferSignal.emit(frame)
            
            self.msleep(1)