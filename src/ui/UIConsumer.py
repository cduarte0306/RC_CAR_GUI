from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot
from utils.utilities import CircularBuffer

from car_controls.VideoStreaming import VideoStreamer
from car_controls.controller import Controller
from car_controls.CommandBus import CamCommands, CamStreamModes, CommandBus, Command, commands, CameraCommand
from network.NetworkManager import NetworkManager
from network.udp_client import UDP

import os
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
    telemetryReceived  = pyqtSignal(bytes)  # Telemetry data received
    videoUploadProgress = pyqtSignal(int, int)  # Emitted during video file upload (sent bytes, total bytes)
    commandReplyReceived = pyqtSignal(bytes)  # Replies from command/ctrl socket

    CONTROLLER_PORT = 65000
    STREAM_PORT     = 5000
    TELEMETRY_PORT  = 6000

    def __init__(self):
        super().__init__()
        
        # Create the network manager
        self.__networkManager : NetworkManager = NetworkManager()

        # Create the adapters
        # Controller adapter now listens for replies via callback
        self.__controllerAdapter       : UDP = self.__networkManager.openAdapter(
            "controller", (BackendIface.CONTROLLER_PORT, ""), self.__controllerReplyCallback
        )
        # Create outbound adapter first (no receive callback)
        self.__videoStreamerOutAdapter : UDP = self.__networkManager.openAdapter("streamOut" , (BackendIface.STREAM_PORT, "192.168.1.10"), recvBuffSize=65507)

        # Create buffers and streamer before wiring the inbound adapter callback
        self.__videoBuffer    : CircularBuffer = CircularBuffer(100)
        self.__tlmBuffer      : CircularBuffer = CircularBuffer(100)
        
        # Create VideoStreamer without inbound adapter; inbound frames will be fed via callback
        self.__videoStreamer  : VideoStreamer  = VideoStreamer(None, self.__videoStreamerOutAdapter)

        # Now open the inbound adapter and pass our callback (starts receive thread)
        self.__videoStreamerInAdapter  : UDP = self.__networkManager.openAdapter("streamerIn", (BackendIface.STREAM_PORT, ""), self.__videoReceivedCallback, 65507)
        self.__commandBus     : CommandBus     = CommandBus(self.__controllerAdapter)
        self.__controller     : Controller     = Controller(self.__commandBus)

        # Open adapter for motor telemetry at port 6000
        self.__telemetryAdapter : UDP = self.__networkManager.openAdapter("telemetry", (BackendIface.TELEMETRY_PORT, ""), self.__telemetryReceivedCallback)

        # controllerAdapter.deviceFound.connect(self.__deviceFound)
        self.__devicesPool : list  = []
        self.__connected_ip : str = ""
        self.__mac_cache : dict = {}

        # Connect signals
        # Forward discovered host IPs to the UI with the IP string
        self.__networkManager.hostDiscovered.connect(lambda ip: self.__on_host_discovered(ip))
        self.__videoStreamer.sendFrameSignal.connect(lambda pkt: self.__videoStreamOutThread(pkt))
        self.__videoStreamer.frameSentSignal.connect(self.__frameSentCallback)
        self.__videoStreamer.startingVideoTransmission.connect(self.__startingVideoTransmission)
        self.__videoStreamer.endingVideoTransmission.connect(self.__endingVideoTransmission)
        self.__commandBus.replyReceived.connect(lambda reply: self.commandReplyReceived.emit(ctypes.string_at(ctypes.addressof(reply), ctypes.sizeof(reply))))

        # Default to regular camera streaming mode
        self.setStreamMode(False)
        
        # Ping thread
        self.__ping_thread = Thread(target=self.__ping_loop, daemon=True)


    def __controllerReplyCallback(self, data: bytes) -> None:
        """Handle async replies arriving on the controller socket."""
        try:
            self.__commandBus.processReply(data)
        except Exception as exc:
            logging.error("Failed to emit controller reply: %s", exc)


    def __on_host_discovered(self, ip: str) -> None:
        # remember device and notify UI
        if ip not in self.__devicesPool:
            self.__devicesPool.append(ip)
        self.deviceDiscovered.emit(ip)


    def __telemetryReceivedCallback(self, data : bytes) -> None:
        """
        Telemetry reception callback

        Args:
            data (bytes): Telemetry data
        """
        # For now, just log telemetry size
        logging.debug("Received telemetry data (%d bytes)", len(data))
        self.__tlmBuffer.push(data)
        
        
    def __frameSentCallback(self, sent: int, total: int) -> None:
        """
        Frame sent callback

        Args:
            sent (int): Sent bytes
            total (int): Total bytes
        """
        self.videoUploadProgress.emit(sent, total)


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
        
        
    def __startingVideoTransmission(self) -> None:
        """
        Video transmission starting
        """
        logging.info("Video transmission starting")
        
        # Command the camera to clear the buffer before starting
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdClrVideoRec.value  # CmdSelMode on host
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        
        # Emit camera mode command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_SET_MODE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue camera clear buffer command: %s", exc)
    
    
    def __endingVideoTransmission(self) -> None:
        """
        Video transmission ended
        """
        logging.info("Video transmission ended")


    def startStreamOut(self, state : bool, fileName:str) -> None:
        print(state, fileName)
        logging.info(f"Start stream out called with state={state}, fileName={fileName}")
        streamStateMap = {
            "streaminoff"  : 0,
            "streaminon"   : 1,
            "streamsim"    : 2,
            "streamcamera" : 3
        }
        
        val : int = streamStateMap["streamsim"]  if state else streamStateMap["streaminoff"]
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdSelMode.value  # CmdSelMode on host
        cam_cmd.data.u8 = val
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))

        # Emit camera mode command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_SET_MODE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue camera mode command: %s", exc)
        

    @pyqtSlot(str)
    def uploadVideoFile(self, fileName: str) -> None:
        """Upload a video file to the car's storage."""
        if fileName == "" or not os.path.isfile(fileName) or not fileName.lower().endswith(".mov"):
            logging.info("No valid .MOV file selected for upload")
            return
        
        self.__videoStreamer.setVideoSource(fileName)
        self.__videoStreamer.startStreamOut(True)
        logging.info(f"Uploading video file to car: {fileName}")
        # Here you would implement the actual upload logic
        # For now, just log the action


    @pyqtSlot(bool)
    def setStreamMode(self, enabled: bool) -> None:
        """Enable or disable video streaming to the car."""
        logging.info("Setting stream mode to: %s", "ENABLED" if enabled else "DISABLED")
        self.startStreamOut(enabled, "")
        
        # Build nested CameraCommand payload
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdStreamMode.value  # CmdSelMode on host
        cam_cmd.data.u8 = CamStreamModes.StreamSim.value if enabled else CamStreamModes.StreamCamera.value
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))

        # Emit camera mode command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_SET_MODE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue camera mode command: %s", exc)


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
        cam_cmd.command = CamCommands.CmdSelMode.value  # CmdSelMode on host
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
        
        # Start the constant ping thread to keep connection alive
        self.__ping_thread.start()


    def getDevices(self) -> list:
        """
        Return the list of devices

        Returns:
            list: List of devices
        """
        return self.__devicesPool
    
    
    def __ping_loop(self):
        while True:
            if self.__connected_ip:
                try:
                    self.__commandBus.submit(Command(commands.CMD_NOOP.value, 0))
                except Exception as exc:
                    logging.error("Failed to enqueue ping command: %s", exc)
            time.sleep(2)


    def run(self):
        """
        Main UI interface thread
        """
        while True:
            frame = self.__videoStreamer.getFrameIn()
            if frame is not None:
                self.videoBufferSignal.emit(frame)
                
            if self.__tlmBuffer.empty() == False:
                tlm = self.__tlmBuffer.read()
                self.telemetryReceived.emit(tlm)
                
            self.usleep(100)