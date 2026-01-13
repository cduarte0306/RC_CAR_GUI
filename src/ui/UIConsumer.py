from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot, QTimer
from utils.utilities import CircularBuffer

from car_controls.VideoStreaming import VideoStreamer, FrameHeader
from car_controls.controller import Controller
from car_controls.CommandBus import (CamCommands, CamStreamModes, 
                                     CommandBus, Command, commands, CameraCommand, ReplyPayload, Reply)
from network.NetworkManager import NetworkManager
from network.udp_client import UDP

import os
import numpy as np
import subprocess
import re
import ctypes

from threading import Thread, Event
import time
import logging

from utils.utilities import Signal



class BackendIface(QThread):
    videoBufferSignal           = pyqtSignal(object, object) # Frame received signal (left and right frames)
    videoBufferSignalStereo     = pyqtSignal(object, object) # Stereo frame received signal (left and right frames)
    videoBufferSignalStereoMono = pyqtSignal(object, object) # Stereo mono frame received signal (left frame and right frame as int)
    deviceDiscovered            = pyqtSignal(str)            # Device discovered signal (emits IP)
    deviceConnected             = pyqtSignal(str)            # Device connected (emits IP)
    deviceMacResolved           = pyqtSignal(str, str)       # Emits (ip, mac)
    videoModeRequested          = pyqtSignal(str)            # Emits requested camera mode (regular/depth)
    telemetryReceived           = pyqtSignal(bytes)          # Telemetry data received
    videoUploadProgress         = pyqtSignal(int, int)       # Emitted during video file upload (sent bytes, total bytes)
    videoUploadFinished         = pyqtSignal()               # Emitted when video upload is finished
    commandReplyReceived        = pyqtSignal(bytes)          # Replies from command/ctrl socket
    notifyDisconnect            = pyqtSignal()               # Notify UI of disconnection
    controllerConnected         = pyqtSignal(str)            # Notify UI of controller connection
    controllerBatteryLevel      = pyqtSignal(int)            # Notify UI of controller battery level
    controllerDisconnected      = pyqtSignal()               # Notify UI of controller disconnection
    
    # Status signals
    videoListLoaded             = pyqtSignal(list)           # Emitted when video list is loaded from device   
    videoStoredToDevice         = pyqtSignal()               # Emitted when video is successfully stored on device
    
    # Error signals
    failedToStoreVideoOnDevice  = pyqtSignal(str)  # Emitted when saving video on device fails

    CONTROLLER_PORT = 65000
    STREAM_PORT     = 5005
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
        self.__controller.controllerDetected.connect(lambda connType: self.controllerConnected.emit(connType))
        self.__controller.controllerBatteryLevel.connect(lambda level: self.controllerBatteryLevel.emit(level))
        self.__controller.controllerDisconnected.connect(lambda: self.controllerDisconnected.emit())
        self.__controller.controllerBatteryLevel.connect(lambda level: self.controllerBatteryLevel.emit(level))
    
        # Default to regular camera streaming mode
        self.setStreamMode("stereo_pairs")
        
        self.__disconnectTimer : int = 0  # Disconnect timer counter
        
        # Ping thread
        self.__ping_thread = Thread(target=self.__ping_loop, daemon=True)
        self.__disconnectTimerObj = Thread(target=self.__check_disconnect, daemon=True)
        
        # Callback signals
        self.__videoSavedOnDeviceSignal = Signal(Reply)
        self.__loadVideoNamesSignal     = Signal(Reply)

        # Connect to reply callbacks
        self.__videoSavedOnDeviceSignal.connect(self.__handleVideoSavedOnDeviceReply)
        self.__loadVideoNamesSignal.connect(self.__handleStoredVideoListReply)
    
    def __clearTimers(self) -> None:
        """
        Clear disconnect timers
        """
        self.__disconnectTimer = 0
        

    def __check_disconnect(self) -> None:
        """
        Check for device disconnection
        """
        if self.__connected_ip == "":
            return
        
        while True:
            time.sleep(1)
            self.__disconnectTimer += 1
            if self.__disconnectTimer >= 5:  # 5 seconds timeout
                logging.warning("No communication from device %s; assuming disconnected", self.__connected_ip)
                self.__connected_ip = ""
                self.__commandBus.flushReplyCache()
                self.notifyDisconnect.emit()
                self.__disconnectTimer = 0
                break


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
        if self.__looks_like_video_packet(data):
            self.__videoReceivedCallback(data)
            return
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


    def __looks_like_video_packet(self, data: bytes) -> bool:
        header_size = ctypes.sizeof(FrameHeader)
        if len(data) < header_size:
            return False

        try:
            frame_hdr = FrameHeader.from_buffer_copy(data[:header_size])
        except Exception:
            return False

        frame_type = frame_hdr.frameHeader.frameType
        frame_side = frame_hdr.frameHeader.frameSide
        seg_id = frame_hdr.metadata.segmentID
        num_segs = frame_hdr.metadata.numSegments
        total_len = frame_hdr.metadata.totalLength
        seg_len = frame_hdr.metadata.length

        if frame_type not in (0, 1):
            return False
        if frame_side not in (0, 1):
            return False
        if num_segs <= 0 or seg_id >= num_segs:
            return False
        if seg_len <= 0:
            return False
        if total_len < seg_len:
            return False
        if seg_len > len(data) - header_size:
            return False
        if total_len > 10 * 1024 * 1024:
            return False

        return True


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
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue camera clear buffer command: %s", exc)
    
    
    def __endingVideoTransmission(self) -> None:
        """
        Video transmission ended
        """
        logging.info("Video transmission ended")
        self.videoUploadFinished.emit()
        
        
    def __handleStoredVideoListReply(self, reply : Reply):
        """
        Handles replies from the load stored video names command

        Args:
            reply (Reply): Reply from host
        """
        status = reply.status()
        if not status:
            logging.error("Failed to load stored video list; status=%d", status)
            return
        
        print(reply.payload())
        
        # # Parse video names from payload
        # payload_size = ctypes.sizeof(reply) - ctypes.sizeof(ReplyPayload)
        # num_videos = payload_size // 64  # assuming each name is 64 bytes
        # video_names = []
        
        # for i in range(num_videos):
        #     offset = ctypes.sizeof(ReplyPayload) + i * 64
        #     name_bytes = (ctypes.c_char * 64).from_buffer_copy(ctypes.string_at(ctypes.addressof(reply) + offset, 64))
        #     name_str = name_bytes.value.decode('utf-8').rstrip('\x00')
        #     if name_str:
        #         video_names.append(name_str)
        
        # logging.info(f"Loaded stored video list: {video_names}")
        # self.videoListLoaded.emit(video_names)
        
        
    def __handleVideoSavedOnDeviceReply(self, reply : Reply):
        """
        Handles replies from the video saved on device command

        Args:
            reply (Reply): Reply from host
        """
        status = reply.status()
        if not status:
            logging.error("Failed to save video on device; status=%d", status)
            self.failedToStoreVideoOnDevice.emit(f"Failed to save video on device; status={status}")
            return
        
        logging.info(f"Video successfully saved on device")
        self.videoStoredToDevice.emit()
        

    def __handleVideoSetNameReply(self, reply : ReplyPayload):
        """
        Handles replies from the video name setting command

        Args:
            reply (ReplyPayload): Reply from host
        """
        status = reply.status
        if not status:
            logging.error("Failed to set video name on device; status=%d", status)
            return
        
        logging.info(f"Commanding video save")
        
        # Build nested CameraCommand payload
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdSaveVideo.value  # hypothetical command
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        # Emit camera save video command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload, signalCallback=self.__videoSavedOnDeviceSignal))
        except Exception as exc:
            logging.error("Failed to enqueue save video command: %s", exc)


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
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
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


    @pyqtSlot(str)
    def setStreamMode(self, streamMode:str) -> None:
        """Enable or disable video streaming to the car."""
        modeMap = {
            "stereo_pairs": CamStreamModes.StreamStereoPairs.value,
            "stereo_mono": CamStreamModes.StreamStereoMono.value,
            "simulation": CamStreamModes.StreamSim.value,
        }
        
        if modeMap.get(streamMode) is None:
            logging.warning("Unknown stream mode requested: %s", streamMode)
            return
        
        
        logging.info("Requested stream mode: %s", streamMode)
        
        # Build nested CameraCommand payload
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdStreamMode.value  # CmdSelMode on host
        cam_cmd.data.u8 = modeMap[streamMode]
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))

        # Emit camera mode command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue camera mode command: %s", exc)


    @pyqtSlot(str)
    def setStereoMonoMode(self, mode: str) -> None:
        """Set stereo-mono render mode (normal/disparity) on the camera."""
        mode_map = {
            "normal": 0,     # CamModeNormal
            "depth": 1,      # CamModeDepth
            "disparity": 2,  # CamModeDisparity
        }
        if mode not in mode_map:
            logging.warning("Unknown stereo-mono mode requested: %s", mode)
            return

        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdSelMode.value
        cam_cmd.data.u8 = mode_map[mode]
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))

        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue stereo-mono mode command: %s", exc)


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
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue camera mode command: %s", exc)


    def setSaveVideoOnDevice(self, videoName: str) -> None:
        """Send command to save video on the device with the given name."""        
        logging.info(f"Commanding video save")
        
        # Build nested CameraCommand payload
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdSaveVideo.value  # hypothetical command
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        # Emit camera save video command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload, signalCallback=self.__videoSavedOnDeviceSignal))
        except Exception as exc:
            logging.error("Failed to enqueue save video command: %s", exc)



    def setStereoMonoMode(self, mode: str) -> None:
        """Set stereo-mono render mode (normal/disparity) on the camera."""
        mode_map = {
            "normal": 0,     # CamModeNormal
            "depth": 1,      # CamModeDepth
            "disparity": 2,  # CamModeDisparity
        }
        if mode not in mode_map:
            logging.warning("Unknown stereo-mono mode requested: %s", mode)
            return

        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdSelMode.value
        cam_cmd.data.u8 = mode_map[mode]
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))

        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue stereo-mono mode command: %s", exc)


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
        
        # Start disconnect timer
        self.__disconnectTimerObj.start()
        
        # Load the list of stored videos from the device
        # self.__loadStoredVideoList()
        
    
    def __loadStoredVideoList(self) -> None:
        """Load the list of stored videos from the device."""
        # Build nested CameraCommand payload
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdLoadStoredVideos.value  # hypothetical command
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        # Emit camera load stored videos command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload, signalCallback=self.__loadVideoNamesSignal))
        except Exception as exc:
            logging.error("Failed to enqueue load stored videos command: %s", exc)


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
                self.__clearTimers()
                self.videoBufferSignal.emit(frame, None)

            frameStereo = self.__videoStreamer.getFrameBufferInStereo()
            if frameStereo is not None:
                self.__clearTimers()
                self.videoBufferSignalStereo.emit(frameStereo, None)  # stacked stereo frame
                
            frameStereoMono = self.__videoStreamer.getFrameBufferInStereoMono()
            if frameStereoMono is not None:
                self.__clearTimers()
                self.videoBufferSignalStereoMono.emit(frameStereoMono[0], frameStereoMono[1])  # left frame and right frame as int

            if self.__tlmBuffer.empty() == False:
                self.__clearTimers()
                tlm = self.__tlmBuffer.read()
                self.telemetryReceived.emit(tlm)
                
            self.usleep(100)
