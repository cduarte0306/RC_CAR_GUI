from html import parser
from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot, QTimer
from utils.utilities import CircularBuffer

from backend.VideoStreaming import VideoStreamer, FrameHeader
from backend.controller import Controller
from backend.CommandBus import (CamStreamSelectionModes,
                                     CommandBus, Command, CameraCommand, RcCommands,
                                     Reply,
                                     MotorCommands, UpdaterCommand, val_type_t)
from network.NetworkManager import NetworkManager
from network.udp_client import UDP

from backend.UpdaterBackend import UpdaterBackend

import os
import numpy as np
import subprocess
import re
import ctypes
import socket

from threading import Event
from concurrent.futures import ThreadPoolExecutor
import json
import time
import logging

from utils.utilities import Signal
import configparser

import Defines


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
    paramsLoaded                = pyqtSignal(dict)           # Emitted when calibration parameters are loaded
    
    updaterError                = pyqtSignal()               # Updater error
    updaterProgress             = pyqtSignal(float)          # Update progress signal
    updaterInstallProgress             = pyqtSignal(float)          # Update progress signal
    updaterFinished             = pyqtSignal()               # Updater finished signal
    updaterAborted              = pyqtSignal()               # Updater aborted signal
    
    # Firmware update signals
    firmwareUpdateState         = pyqtSignal(bool)           # To be called by the backend to notify the UI of the return state of the latest firmware update command (success/failure)

    # Status signals
    videoListLoaded             = pyqtSignal(str, list)      # Emitted when video list is loaded from device along with the loaded video
    videoStoredToDevice         = pyqtSignal()               # Emitted when video is successfully stored on device
    
    # Error signals
    failedToStoreVideoOnDevice  = pyqtSignal(str)  # Emitted when saving video on device fails


    def __init__(self):
        super().__init__()
        
        parser = configparser.ConfigParser()
        self.__config_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "config", "rc-car-viewer-config.ini")
        )
        parser.read(self.__config_path)
        # Video stream-out local adapter (Ethernet) selection.
        # Backwards-compatible fallback to the old `adapter_ip` key.

        # Create buffers and streamer before wiring the inbound adapter callback
        self.__tlmBuffer      : CircularBuffer = CircularBuffer(100)

        # Create VideoStreamer without inbound adapter; inbound frames will be fed via callback
        self.__videoStreamer  : VideoStreamer  = VideoStreamer()

        # Now open the inbound adapter and pass our callback (starts receive thread)
        self.__commandBus     : CommandBus = CommandBus.getInstance()
        self.__controller     : Controller = Controller()
        
        self.__networkMgr = NetworkManager()
        self.__updaterBacked = UpdaterBackend()

        # Telemetry is inbound-only, so listen on all local interfaces rather
        # than tying reception to whichever NIC discovery selected.
        self._tlmSockFd = NetworkManager.getUDPAdapter(
            Defines.TELEMETRY_PORT,
            OnRx=self.__telemetryReceivedCallback,
            recvBuffSize=1024,
            name="Telemetry"
        )

        # controllerAdapter.deviceFound.connect(self.__deviceFound)
        self.__devicesPool : list  = []
        self.__connected_ip : str = ""
        self.__mac_cache : dict = {}
        self.__streamQuality: int = 75
        self.__streamFps: int = 30
        self.__maxDisparityCurrent: int = 64

        # Connect signals
        # Forward discovered host IPs to the UI with the IP string
        # self.__networkMgr.hostDiscovered.connect(lambda ip: self.deviceConnected.emit(ip))
        self.__networkMgr.dataReceived.connect(lambda: self.__clearTimers)
        self.__networkMgr.hostDiscovered.connect(lambda ip: self.deviceDiscovered.emit(ip))
        self.__videoStreamer.sendFrameSignal.connect(lambda pkt: self.__videoStreamOutThread(pkt))
        self.__videoStreamer.frameSentSignal.connect(self.__frameSentCallback)
        self.__videoStreamer.startingVideoTransmission.connect(self.__startingVideoTransmission)
        self.__videoStreamer.endingVideoTransmission.connect(self.__endingVideoTransmission)
        self.__controller.controllerDetected.connect(lambda connType: self.controllerConnected.emit(connType))
        self.__controller.controllerBatteryLevel.connect(lambda level: self.controllerBatteryLevel.emit(level))
        self.__controller.controllerDisconnected.connect(lambda: self.controllerDisconnected.emit())
        self.__controller.controllerBatteryLevel.connect(lambda level: self.controllerBatteryLevel.emit(level))

        self.__updaterBacked.updateProgress.connect(self.__updaterProgressCallback)
        self.__updaterBacked.updateDone.connect(lambda: self.updaterFinished.emit())
        self.__updaterBacked.updateError.connect(lambda: self.updaterError.emit())
        self.__updaterBacked.firmwareAborted.connect(lambda: self.updaterAborted.emit())
    
        # Default to disparity (normal) stereo streaming mode
        # self.setCameraSource(False)
        # self.setStereoMonoMode("disparity")
        self.__disconnectTimer : int = 0  # Disconnect timer counter
        
        # Background workers for ping and disconnect watchdog
        self.__threadPool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="backend-iface")
        self.__pingFuture = None
        self.__disconnectFuture = None
        
        self.__pingShutdownEvent = Event()

        # Connect to reply callbacks
        # self.__videoSavedOnDeviceSignal.connect(self.__handleVideoSavedOnDeviceReply)
        # self.__loadVideoNamesSignal.connect(self.__handleStoredVideoListReply)
        # self.__loadParamsSignal.connect(self.__handleParamsReply)
        
        self.setDisparityRenderMode("depth")
        
        self.__threadCanRun = True
        
        
    def __del__(self):
        
        # This will be called when the object is garbage collected
        self.__threadCanRun = False
        self.__pingShutdownEvent.set()
        if self.__threadPool is not None:
            self.__threadPool.shutdown(wait=False, cancel_futures=True)


    def getVideoOutAdapterIp(self) -> str:
        return self.__video_out_adapter_ip


    def getRenderer3D(self):
        """Return the C++ Renderer3D object (or None) for GUI-thread 3D embedding.

        The Qt GUI thread drives its window lifecycle (start_window/pump/
        stop_window/embed_into); the streamer only feeds it point data.
        """
        return self.__videoStreamer.getRenderer()


    @pyqtSlot(str)
    def setVideoOutAdapterIp(self, adapter_ip: str) -> None:
        """Select the local adapter used ONLY for the Ethernet video stream-out socket.

        Main control/telemetry comms stay on the default OS routing (WLAN).
        """
        adapter_ip = (adapter_ip or "0.0.0.0").strip() or "0.0.0.0"

        if adapter_ip != "0.0.0.0":
            try:
                socket.inet_aton(adapter_ip)
            except OSError:
                logging.warning("Ignoring invalid video_out_adapter_ip selection: %s", adapter_ip)
                return

        if adapter_ip == self.__video_out_adapter_ip:
            return

        self.__video_out_adapter_ip = adapter_ip
        self.__persist_video_out_adapter_ip(adapter_ip)

        # Rebind only the outbound video socket. Use port 0 (ephemeral) to avoid conflicts with streamerIn.
        try:
            if adapter_ip != "0.0.0.0":
                self.__videoStreameEthAdapter.rebind(adapter_ip, 0)
            else:
                self.__videoStreameEthAdapter.rebind("0.0.0.0", 0)
        except Exception as exc:
            logging.error("Failed to apply video_out_adapter_ip=%s: %s", adapter_ip, exc)


    def __persist_video_out_adapter_ip(self, adapter_ip: str) -> None:
        try:
            parser = configparser.ConfigParser()
            parser.read(self.__config_path)
            if "settings" not in parser:
                parser["settings"] = {}
            parser["settings"]["video_out_adapter_ip"] = adapter_ip
            with open(self.__config_path, "w", encoding="utf-8") as f:
                parser.write(f)
        except Exception as exc:
            logging.warning("Failed to persist video_out_adapter_ip to config: %s", exc)

    
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
            if self.__disconnectTimer >= 2:  # 2 seconds timeout
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


    def __controllerReplyCallbackEth(self, data: bytes) -> None:
        """Handle async replies arriving on the Ethernet controller socket."""
        try:
            self.__commandBus.processReply(data)
        except Exception as exc:
            logging.error("Failed to emit ethernet controller reply: %s", exc)


    def __telemetryReceivedCallback(self, data : bytes) -> None:
        """
        Telemetry reception callback

        Args:
            data (bytes): Telemetry data
        """
        # Port 6000 is reserved for telemetry; do not attempt to reinterpret
        # these datagrams as video fragments.
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
        
        
    def __videoReceivedEthCallback(self, data : bytes) -> None:
        """
        Video frame reception callback over Ethernet

        Args:
            data (bytes): Video frame data
        """
        self.__videoStreamer.setFrameEth(data)


    def __videoStreamOutThread(self, packet:bytes) -> None:
        """
        Video stream out thread
        """
        ret = self.__videoStreameEthAdapter.send(packet)
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
        self.__networkMgr.startDiscovery()


    def __startingVideoTransmission(self) -> None:
        """
        Video transmission starting
        """
        logging.info("Video transmission starting")
        
        # Command the camera to clear the buffer before starting
        cam_cmd = CameraCommand()
        
        # Emit camera mode command to the controller bus, with appended payload
        try:
            cam_cmd.ModuleClearVideoRecordings()
        except Exception as exc:
            logging.error("Failed to enqueue camera clear buffer command: %s", exc)
            
            
    def __updaterProgressCallback(self, type : int, progress: float) -> None:
        if type == 0:  # Assuming 0 represents general progress
            self.updaterProgress.emit(progress)
        elif type == 1:  # Assuming 1 represents install progress
            self.updaterInstallProgress.emit(progress)
    
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

        logging.info("Loaded streaming parameters")
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
        nameList  = ""
        for name in videoNames:
            nameList += name.split(".")[-1] + "\r\n"
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
        
        # Emit camera mode command to the controller bus, with appended payload
        try:
            if enable:
                cam_cmd.ModuleStartStream()
            else:
                cam_cmd.ModuleStopStream()
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
        
        # Emit camera mode command to the controller bus, with appended payload
        try:
            CameraCommand().ModuleSelectCameraStream(CamStreamSelectionModes.StreamCameraSource, payload=extraCommandPayload)
        except Exception as exc:
            logging.error("Failed to enqueue camera mode command: %s", exc)


    @pyqtSlot()
    def setSimulationSource(self) -> None:
        """Set the camera source to either simulation or physical camera."""
        logging.info("Requested simulation source mode")
        
        # Emit camera mode command to the controller bus, with appended payload
        try:
            CameraCommand().ModuleSelectCameraStream(CamStreamSelectionModes.StreamSimSource)
        except Exception as exc:
            logging.error("Failed to enqueue camera mode command: %s", exc)


    @pyqtSlot(dict)
    def setStereoCalibrationParams(self, params: dict) -> None:
        """Apply stereo calibration parameters to the camera (UI stub)."""
        if not params:
            return
        logging.info("Stereo calibration params requested: %s", params)
        params_payload = json.dumps(params).encode("utf-8")
        # Emit camera save video command to the controller bus, with appended payload
        try:
            CameraCommand().ModuleCalibrationWriteParams(params_payload)
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
        # Emit camera save video command to the controller bus, with appended payload
        try:
            if mode == "normal":
                CameraCommand().ModuleSelectCameraStream(CamStreamSelectionModes.StreamCameraSource)
            else:
                CameraCommand().ModuleSelectCameraStream(CamStreamSelectionModes.StreamSimSource)
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
        try:
            cam_cmd.ModuleCalibrationSave(payload)
        except Exception as exc:
            logging.error("Failed to enqueue calibration store command: %s", exc)


    def setSaveVideoOnDevice(self, videoName: str) -> None:
        """Send command to save video on the device with the given name."""        
        logging.info(f"Commanding video save")
        
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleSaveVideo(videoName.encode("utf-8"), self.__handleVideoSavedOnDeviceReply)
        except Exception as exc:
            logging.error("Failed to enqueue save video command: %s", exc)


    def setRecordingState(self, enabled: bool, path: str, mode : int) -> None:
        """Set local recording state and path for incoming video frames."""
        if path:
            logging.info("Setting recording path to: %s", path)
            self.__videoStreamer.setRecordingPath(path)
        logging.info("Recording %s", "enabled" if enabled else "disabled")
        self.__videoStreamer.setRecordingState(enabled, mode)


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

        try:
            if mode == "normal":
                cam_cmd.ModuleSelectCameraStream(CamStreamSelectionModes.StreamCameraSource)
            else:
                cam_cmd.ModuleSelectCameraStream(CamStreamSelectionModes.StreamSimSource)
        except Exception as exc:
            logging.error("Failed to enqueue stereo-mono mode command: %s", exc)

    def __OnDeviceConnected(self, ip: str) -> None:
        """Handle device connection event.

        Args:
            ip (str): The IP address of the connected device.
        """
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        logging.info(f"Detected device connection with IP {ip} at {time_str}")

        # Trigger on device connection signal
        self.deviceConnected.emit(ip)
        
        self.__loadStoredVideoList()
        self.__loadParams()
        self.startVideoStream(True)
        self.__controller.setDeviceConnected(True)
        pass

    def connectToDevice(self, ip: str) -> None:
        """Attempt to connect to the RC car at the given IP. This sets the
        selected device and emits `deviceConnected` for the UI to react.

        Note: actual transport-level controller binding may be performed elsewhere;
        here we record the selection and notify listeners."""
        self.__connected_ip = ip
        logging.info(f"Connecting to device at {ip}")
        self.__networkMgr.StartConnection(ip, self.__OnDeviceConnected)
        
        # Start disconnect timer
        if self.__disconnectFuture is None or self.__disconnectFuture.done():
            self.__disconnectFuture = self.__threadPool.submit(self.__check_disconnect)
            
        if self.__pingFuture is None or self.__pingFuture.done():
            self.__pingFuture = self.__threadPool.submit(self.__ping_loop)
        return
        # Potential place to reconfigure adapters or start sessions
        self.__videoStreameEthAdapter.setServerIP(ip)

        self.__controller.StartComms()
        self.deviceConnected.emit(ip)

        # Resolve MAC once and emit
        mac = self.__mac_cache.get(ip) or self.__resolve_mac(ip)
        if mac:
            self.__mac_cache[ip] = mac
        self.deviceMacResolved.emit(ip, mac if mac else "")
        
        # Start the constant ping thread to keep connection alive
        if self.__pingFuture is None or self.__pingFuture.done():
            self.__pingFuture = self.__threadPool.submit(self.__ping_loop)
        
        # Start disconnect timer
        if self.__disconnectFuture is None or self.__disconnectFuture.done():
            self.__disconnectFuture = self.__threadPool.submit(self.__check_disconnect)
        
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

        cam_cmd = CameraCommand()
        
        try:
            cam_cmd.ModuleSetFps(fps)
        except Exception as exc:
            logging.error("Failed to enqueue camera clear buffer command: %s", exc)
            
            
    def setVideoQuality(self, quality: int) -> None:
        """
        Set video settings on the camera

        Args:
            quality (int): _description_
        """
        logging.info("Setting video quality to %d", quality)
    
        cam_cmd = CameraCommand()
        
        try:
            cam_cmd.ModuleSetQuality(quality)
        except Exception as exc:
            logging.error("Failed to enqueue camera clear buffer command: %s", exc)


    def setMaxDisparities(self, value: int) -> None:
        """Set the maximum disparity for matching search (VPI: maxDisparity)."""
        step = 16
        value = int(value)
        if value < 16:
            value = 16
        if value > 256:
            value = 256
        value = int(round(value / step) * step)
        value = max(16, min(256, value))
        self.__maxDisparityCurrent = value
        logging.info("Setting max disparity to %d", value)
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleSetMaxDisparities(value)
        except Exception as exc:
            logging.error("Failed to enqueue max disparity command: %s", exc)


    def setConfidenceThreshold(self, value: int) -> None:
        """Set VPI confidence threshold (0-65535, default 32767)."""
        value = int(value)
        value = max(0, min(65535, value))
        logging.info("Setting confidence threshold to %d", value)
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleSetConfidenceThreshold(value)
        except Exception as exc:
            logging.error("Failed to enqueue confidence threshold command: %s", exc)


    def setUniquenessRatio(self, value: int) -> None:
        """Set the VPI uniqueness ratio on the camera (slider 0-100 → float 0.0-1.0)."""
        value = int(value)
        value = max(0, min(100, value))
        fval = value / 100.0
        logging.info("Setting uniqueness ratio to %.2f", fval)
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleSetUniquenessRatio(fval)
        except Exception as exc:
            logging.error("Failed to enqueue uniqueness ratio command: %s", exc)


    def setMinDisparities(self, value: int) -> None:
        """Set minimum disparity (VPI: minDisparity, 0 to maxDisparity)."""
        value = int(value)
        max_disp = max(0, int(self.__maxDisparityCurrent))
        value = max(0, min(max_disp, value))
        logging.info("Setting min disparity to %d", value)
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleSetMinDisparities(value)
        except Exception as exc:
            logging.error("Failed to enqueue min disparity command: %s", exc)


    @pyqtSlot(int)
    def setP1(self, value: int) -> None:
        """Set SGBM P1 penalty."""
        value = int(value)
        value = max(1, min(255, value))
        logging.info("Setting P1 to %d", value)
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleSetP1(value)
        except Exception as exc:
            logging.error("Failed to enqueue P1 command: %s", exc)


    @pyqtSlot(int)
    def setP2(self, value: int) -> None:
        """Set SGBM P2 penalty (must be > P1, enforced on device too)."""
        value = int(value)
        value = max(1, min(255, value))
        logging.info("Setting P2 to %d", value)
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleSetP2(value)
        except Exception as exc:
            logging.error("Failed to enqueue P2 command: %s", exc)


    @pyqtSlot(int)
    def setZMax(self, value: int) -> None:
        """Set maximum distance to be displayed (slider int 0-5000 → float 0.00-50.00)."""
        fval = int(value) / 100.0
        fval = max(0.0, min(50.0, fval))
        logging.info("Setting Z Max to %.2f", fval)
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleSetZMax(fval)
        except Exception as exc:
            logging.error("Failed to enqueue Z Max command: %s", exc)


    @pyqtSlot(int)
    def setZMin(self, value: int) -> None:
        """Set minimum distance to be displayed (slider int 0-5000 → float 0.00-50.00)."""
        fval = int(value) / 100.0
        fval = max(0.0, min(50.0, fval))
        logging.info("Setting Z Min to %.2f", fval)
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleSetZMin(fval)
        except Exception as exc:
            logging.error("Failed to enqueue Z Min command: %s", exc)


    @pyqtSlot(int)
    def setDepthThreshold(self, value: int) -> None:
        """Set depth threshold (slider int 0-1000 → float 0.00-10.00)."""
        fval = int(value) / 100.0
        fval = max(0.0, min(10.0, fval))
        logging.info("Setting depth threshold to %.2f", fval)
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleSetDepthThreshold(fval)
        except Exception as exc:
            logging.error("Failed to enqueue depth threshold command: %s", exc)


    @pyqtSlot(int)
    def setMinAgreeingPixels(self, value: int) -> None:
        """Set minimum number of agreeing pixels."""
        value = max(0, min(50, int(value)))
        logging.info("Setting min agreeing pixels to %d", value)
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleSetMinAgreeingPixels(value)
        except Exception as exc:
            logging.error("Failed to enqueue min agreeing pixels command: %s", exc)


    @pyqtSlot(int)
    def setColorThreshold(self, value: int) -> None:
        """Set color threshold (slider int 0-1000 → float 0.00-10.00)."""
        fval = int(value) / 100.0
        fval = max(0.0, min(10.0, fval))
        logging.info("Setting color threshold to %.2f", fval)
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleSetColorThreshold(fval)
        except Exception as exc:
            logging.error("Failed to enqueue color threshold command: %s", exc)
            
            
    def __loadStoredVideoList(self) -> None:
        """Load the list of stored videos from the device."""
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleLoadStoredVideos(self.__handleStoredVideoListReply)
        except Exception as exc:
            logging.error("Failed to enqueue load stored videos command: %s", exc)
            
            
    def __loadParams(self) -> None:
        """Load calibration parameters from the device."""
        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleReadParams(self.__handleParamsReply)
        except Exception as exc:
            logging.error("Failed to enqueue load calibration parameters command: %s", exc)


    @pyqtSlot(str)
    def loadDeviceVideo(self, videoName: str) -> None:
        """Request the device to load a stored video by name."""
        if not videoName:
            logging.warning("No device video name provided")
            return

        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleLoadSelectedVideo(videoName)
        except Exception as exc:
            logging.error("Failed to enqueue load selected video command: %s", exc)
            
            
    @pyqtSlot(str)
    def deleteDeviceVideo(self, videoName: str) -> None:
        """Request the device to delete a stored video by name."""
        if not videoName:
            logging.warning("No device video name provided")
            return

        cam_cmd = CameraCommand()
        try:
            cam_cmd.ModuleDeleteVideo(videoName)
            self.__loadStoredVideoList()
        except Exception as exc:
            logging.error("Failed to enqueue delete video command: %s", exc)


    @pyqtSlot(str)
    def initUpdate(self, fileName : str) -> None:
        self.__updaterBacked.StartUpdate(fileName)

    def getDevices(self) -> list:
        """
        Return the list of devices

        Returns:
            list: List of devices
        """
        return self.__devicesPool

    @pyqtSlot()
    def abortUpdate(self) -> None:
        self.__updaterBacked.AbortUpdate()

    def __ping_loop(self):
        while not self.__pingShutdownEvent.is_set():
            if self.__connected_ip:
                try:
                    # Ping over both command buses
                    rcCommands = RcCommands()
                    rcCommands.ping()
                except Exception as exc:
                    logging.error("Failed to enqueue ping command: %s", exc)
            time.sleep(2)
            
        self.__pingShutdownEvent.clear()
        logging.info("Ping thread exiting")


    def run(self):
        """
        Main UI interface thread
        """
        while self.__threadCanRun:
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
                
            self.usleep(5000)
