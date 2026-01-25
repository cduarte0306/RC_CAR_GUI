from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot, QTimer
from utils.utilities import CircularBuffer

from car_controls.VideoStreaming import VideoStreamer, FrameHeader
from car_controls.controller import Controller
from car_controls.CommandBus import (CamCommands, CamStreamSelectionModes,
                                     CommandBus, Command, commands, CameraCommand, ReplyPayload, Reply)
from network.NetworkManager import NetworkManager
from network.udp_client import UDP

import os
import numpy as np
import subprocess
import re
import ctypes

from threading import Thread, Event
import json
import time
import logging

from utils.utilities import Signal



class BackendIface(QThread):
    videoBufferSignal           = pyqtSignal(object, object) # Frame received signal (left and right frames)
    videoBufferSignalStereo     = pyqtSignal(object, object) # Stereo frame received signal (left and right frames)
    videoBufferSignalStereoMono = pyqtSignal(object, object) # Stereo mono frame received signal (left frame and right frame as int)
    videoBufferSignalDisparity  = pyqtSignal(object, object) # Disparity frame received signal (left frame and right frame as int)

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
    paramsLoaded                = pyqtSignal(dict)          # Emitted when calibration parameters are loaded
    
    # Status signals
    videoListLoaded             = pyqtSignal(str, list)      # Emitted when video list is loaded from device along with the loaded video   
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
        self.__streamQuality: int = 75
        self.__streamFps: int = 30

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
    
        # Default to disparity (normal) stereo streaming mode
        self.setCameraSource(False)
        self.setStereoMonoMode("disparity")
        self.__disconnectTimer : int = 0  # Disconnect timer counter
        
        # Ping thread
        self.__ping_thread = Thread(target=self.__ping_loop, daemon=True)
        self.__disconnectTimerObj = Thread(target=self.__check_disconnect, daemon=True)
        
        self.__pingShutdownEvent = Event()
        
        # Callback signals
        self.__videoSavedOnDeviceSignal = Signal(Reply)
        self.__loadVideoNamesSignal     = Signal(Reply)
        self.__loadParamsSignal         = Signal(Reply)

        # Connect to reply callbacks
        self.__videoSavedOnDeviceSignal.connect(self.__handleVideoSavedOnDeviceReply)
        self.__loadVideoNamesSignal.connect(self.__handleStoredVideoListReply)
        self.__loadParamsSignal.connect(self.__handleParamsReply)

    
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
        
        while self.__pingShutdownEvent.is_set() == False:
            time.sleep(1)
            self.__disconnectTimer += 1
            if self.__disconnectTimer >= 5:  # 5 seconds timeout
                logging.warning("No communication from device %s; assuming disconnected", self.__connected_ip)
                self.__connected_ip = ""
                self.__commandBus.flushReplyCache()
                self.notifyDisconnect.emit()
                self.__disconnectTimer = 0
                self.__pingShutdownEvent.set()
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
        cam_cmd.command = CamCommands.CmdClrVideoRec.value  # CmdSelCameraStream on host
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
        self.__loadStoredVideoList()
        
    
    def __handleParamsReply(self, reply : Reply):
        """
        Handles replies from the load streaming parameters command
        Args:
            reply (Reply): Reply from host
        """
        status = reply.status()
        if not status:
            logging.error("Failed to load calibration parameters; status=%d", status)
            return
        
        if not reply.payload():
            logging.error("Failed to load calibration parameters; empty payload")
            return

        try:
            paramsJson = reply.payload().decode("utf-8").rstrip("\x00")
            params = json.loads(paramsJson)
        except Exception as exc:
            logging.error("Failed to parse calibration parameters payload: %s", exc)
            return

        logging.info("Loaded streaming parameters: %s", params)
        # Here you would typically emit a signal or store the params for UI consumption
        self.paramsLoaded.emit(params)
        
        
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
        
        if not reply.payload():
            logging.error("Failed to load stored video list; empty payload")
            return

        try:
            videoListJson = reply.payload().decode("utf-8").rstrip("\x00")
            videoListJson = json.loads(videoListJson)
        except Exception as exc:
            logging.error("Failed to parse stored video list payload: %s", exc)
            return

        loadedVideoName = videoListJson.get("loaded-video", "")
        videoList = videoListJson.get("video-list", [])
        if isinstance(videoList, str):
            videoNames = [name for name in videoList.split(";") if name]
        elif isinstance(videoList, list):
            videoNames = [name for name in videoList if name]
        else:
            videoNames = []
        logging.info("Loaded stored video list: %s", videoNames)
        self.videoListLoaded.emit(loadedVideoName, videoNames)
        
        
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
        self.__loadStoredVideoList()
        
        
    @pyqtSlot(bool)
    def startVideoStream(self, enable: bool) -> None:
        """Start or stop video streaming to the car."""
        logging.info("Video transmission starting")
        
        # Command the camera to clear the buffer before starting
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdStartStream.value if enable else CamCommands.CmdStopStream.value  # CmdSelCameraStream on host
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        
        # Emit camera mode command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue camera clear buffer command: %s", exc)


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
    def setCameraSource(self, calMode:bool) -> None:
        """Set the camera source to either simulation or physical camera."""
        logging.info("Requested camera source mode. calMode=%s", calMode)
        extraCommand : dict = {
            "calibration-mode": calMode
        }
        
        extraCommandPayload = json.dumps(extraCommand).encode("utf-8")
        
        # Build nested CameraCommand payload
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdSelCameraStream.value  # CmdSelCameraStream on host
        cam_cmd.data.u8 = CamStreamSelectionModes.StreamCameraSource.value
        cam_cmd.payloadLen = len(extraCommandPayload)
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd)) + extraCommandPayload
        # Emit camera mode command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue camera mode command: %s", exc)


    @pyqtSlot()
    def setSimulationSource(self) -> None:
        """Set the camera source to either simulation or physical camera."""
        logging.info("Requested simulation source mode")
        
        # Build nested CameraCommand payload
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdSelCameraStream.value  # CmdSelCameraStream on host
        cam_cmd.data.u8 = CamStreamSelectionModes.StreamSimSource.value
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        # Emit camera mode command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue camera mode command: %s", exc)


    @pyqtSlot(dict)
    def setStereoCalibrationParams(self, params: dict) -> None:
        """Apply stereo calibration parameters to the camera (UI stub)."""
        if not params:
            return
        logging.info("Stereo calibration params requested: %s", params)
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdCalibrationWrtParams.value
        cam_cmd.payloadLen = len(json.dumps(params).encode("utf-8"))
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        cam_payload = cam_payload + json.dumps(params).encode("utf-8")
        # Emit camera save video command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue save video command: %s", exc)


    @pyqtSlot(bool)
    def setCalibrationMode(self, active: bool) -> None:
        """Start/stop calibration capture mode (UI stub)."""
        logging.info("Calibration mode toggled: %s", "on" if active else "off")
        mode_map = {
            "normal": 0,      # CamModeNormal
            "disparity": 1,   # CamModeDisparity
            "calibration": 2  # CamModeCalibration
        }

        mode = "calibration"
        logging.info("Setting camera mode to: %s", mode)
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdSelCameraStream.value
        cam_cmd.data.u8 = mode_map[mode]
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        # Emit camera save video command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue save video command: %s", exc)


    @pyqtSlot()
    def captureCalibrationSample(self) -> None:
        """Request a calibration sample capture (UI stub)."""
        logging.info("Calibration capture requested")

    @pyqtSlot(bool)
    def setCalibrationPaused(self, paused: bool) -> None:
        """Pause/resume calibration capture (UI stub)."""
        logging.info("Calibration paused: %s", "on" if paused else "off")

    @pyqtSlot()
    def abortCalibrationSession(self) -> None:
        """Abort the current calibration session (UI stub)."""
        logging.info("Calibration session aborted")

    @pyqtSlot()
    def resetCalibrationSamples(self) -> None:
        """Reset captured calibration samples (UI stub)."""
        logging.info("Calibration samples reset")


    @pyqtSlot()
    def storeCalibrationResult(self) -> None:
        """Ask the host to persist the latest calibration results."""
        logging.info("Calibration result store requested")
        payload = json.dumps({"action": "store"}).encode("utf-8")
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdCalibrationSave.value
        cam_cmd.payloadLen = len(payload)
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd)) + payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue calibration store command: %s", exc)


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


    def setRecordingState(self, enabled: bool, path: str) -> None:
        """Set local recording state and path for incoming video frames."""
        if path:
            logging.info("Setting recording path to: %s", path)
            self.__videoStreamer.setRecordingPath(path)
        logging.info("Recording %s", "enabled" if enabled else "disabled")
        self.__videoStreamer.setRecordingState(enabled)


    def setDisparityRenderMode(self, mode: str) -> None:
        """Control how disparity frames are visualized on the host."""
        self.__videoStreamer.setDisparityRenderMode(mode)


    def setStereoMonoMode(self, mode: str) -> None:
        """Set stereo-mono render mode (normal/disparity) on the camera."""
        mode_map = {
            "normal": 0,     # CamModeNormal
            "disparity": 1,  # CamModeDisparity
        }
        if mode not in mode_map:
            logging.warning("Unknown stereo-mono mode requested: %s", mode)
            return

        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdSelCameraStream.value
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
        if not self.__ping_thread.is_alive():
            self.__ping_thread = Thread(target=self.__ping_loop, daemon=True)
            self.__ping_thread.start()
        
        # Start disconnect timer
        if not self.__disconnectTimerObj.is_alive():
            self.__disconnectTimerObj = Thread(target=self.__check_disconnect, daemon=True)
            self.__disconnectTimerObj.start()
        
        # Load the list of stored videos from the device
        self.__loadStoredVideoList()
        self.__loadParams()
        self.startVideoStream(True)
        
    
    def setFrameRate(self, fps: int) -> None:
        """
        Set frame rate on the camera

        Args:
            fps (int): Frames per second
        """
        logging.info("Setting frame rate to %d fps", fps)

        # Command the camera to clear the buffer before starting
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdSetFps.value  # CmdSelCameraStream on host
        cam_cmd.data.u8 = fps
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        
        # Emit camera mode command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue camera clear buffer command: %s", exc)
            
            
    def setVideoQuality(self, quality: int) -> None:
        """
        Set video settings on the camera

        Args:
            quality (int): _description_
        """
        logging.info("Setting video quality to %d", quality)
    
        # Command the camera to clear the buffer before starting
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdSetQuality.value  # CmdSelCameraStream on host
        cam_cmd.data.u8 = quality
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        
        # Emit camera mode command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue camera clear buffer command: %s", exc)


    def setNumDisparities(self, value: int) -> None:
        """Set the number of disparities on the camera (must be multiple of 8)."""
        step = 8
        value = (value // step) * step
        if value <= 0:
            return
        logging.info("Setting num disparities to %d", value)
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdSetNumDisparities.value
        cam_cmd.data.u16 = value
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue num disparities command: %s", exc)


    def setBlockSize(self, value: int) -> None:
        """Set the block size on the camera (must be odd, typically >= 5)."""
        if value % 2 == 0:
            value -= 1
        if value < 5:
            value = 5
        if value <= 0:
            return
        logging.info("Setting block size to %d", value)
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdSetBlockSize.value
        cam_cmd.data.u8 = value
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue block size command: %s", exc)
    
    
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
            
            
    def __loadParams(self) -> None:
        """Load calibration parameters from the device."""
        # Build nested CameraCommand payload
        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdRdParams.value  # hypothetical command
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        # Emit camera load stored videos command to the controller bus, with appended payload
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload, signalCallback=self.__loadParamsSignal))
        except Exception as exc:
            logging.error("Failed to enqueue load calibration parameters command: %s", exc)


    @pyqtSlot(str)
    def loadDeviceVideo(self, videoName: str) -> None:
        """Request the device to load a stored video by name."""
        if not videoName:
            logging.warning("No device video name provided")
            return

        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdLoadSelectedVideo.value
        cam_cmd.payloadLen = len(videoName)
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        cam_payload = cam_payload + videoName.encode("utf-8")
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload))
        except Exception as exc:
            logging.error("Failed to enqueue load selected video command: %s", exc)
            
            
    @pyqtSlot(str)
    def deleteDeviceVideo(self, videoName: str) -> None:
        """Request the device to delete a stored video by name."""
        if not videoName:
            logging.warning("No device video name provided")
            return

        cam_cmd = CameraCommand()
        cam_cmd.command = CamCommands.CmdDeleteVideo.value
        cam_cmd.payloadLen = len(videoName)
        cam_payload = ctypes.string_at(ctypes.addressof(cam_cmd), ctypes.sizeof(cam_cmd))
        cam_payload = cam_payload + videoName.encode("utf-8")
        try:
            self.__commandBus.submit(Command(commands.CMD_CAMERA_MODULE.value, 0, payload=cam_payload, signalCallback=self.__loadVideoNamesSignal))
            self.__loadStoredVideoList()
        except Exception as exc:
            logging.error("Failed to enqueue delete video command: %s", exc)


    def getDevices(self) -> list:
        """
        Return the list of devices

        Returns:
            list: List of devices
        """
        return self.__devicesPool
    
    
    def __ping_loop(self):
        while not self.__pingShutdownEvent.is_set():
            if self.__connected_ip:
                try:
                    self.__commandBus.submit(Command(commands.CMD_NOOP.value, 0))
                except Exception as exc:
                    logging.error("Failed to enqueue ping command: %s", exc)
            time.sleep(2)
            
        self.__pingShutdownEvent.clear()
        logging.info("Ping thread exiting")


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
