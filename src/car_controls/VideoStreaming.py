import cv2
from threading import Thread, Lock
import logging

from network.udp_client import UDP
from utils.utilities import CircularBuffer
import configparser
import numpy as np
import time
import os
import ctypes
import json
from utils import utilities

from utils.utilities import Signal
from enum import Enum, auto

import sys
# Add paths for rc_car_cpp module and its Open3D dependencies
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_module_path = os.path.join(_project_root, 'python_modules')
_build_path = os.path.join(_project_root, 'build')  # Contains tbb12.dll and other deps

sys.path.insert(0, _module_path)

# Windows requires DLL directories to be added explicitly (Python 3.8+)
if sys.platform == 'win32' and hasattr(os, 'add_dll_directory'):
    # Build directory has tbb12.dll and other Open3D dependencies
    if os.path.isdir(_build_path):
        os.add_dll_directory(_build_path)
    # CUDA runtime DLLs
    cuda_bin = r'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin'
    if os.path.isdir(cuda_bin):
        os.add_dll_directory(cuda_bin)

import rc_car_cpp  # C++ bindings module


MAX_UDP_PACKET_SIZE = 65507


class FrameMetadata(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("videoNameID",  ctypes.c_uint8 * 128),
        ("sequenceID",  ctypes.c_uint32),
        ("segmentID",   ctypes.c_uint16),
        ("numSegments", ctypes.c_uint16),
        ("totalLength", ctypes.c_uint32),
        ("length",      ctypes.c_uint16),
    ]


class FragmentHeader(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("frameType",   ctypes.c_uint8),  # 0: Mono, 1: Stereo, 2: Disparity
        ("frameSide",   ctypes.c_uint8),  # 0: Left, 1: Right (for stereo)
    ]


class Frame(ctypes.Structure):
    MAX_SIZE = MAX_UDP_PACKET_SIZE - (ctypes.sizeof(FrameMetadata) + ctypes.sizeof(FragmentHeader))
    _pack_ = 1
    _fields_ = [
        ("frameHeader", FragmentHeader),
        ("metadata",    FrameMetadata),
        ("payload",     ctypes.c_uint8 * MAX_SIZE),
    ]


class FrameHeader(ctypes.Structure):
    """Lightweight header view for receive parsing (no giant payload)."""
    _pack_ = 1
    _fields_ = [
        ("frameHeader", FragmentHeader),
        ("metadata",    FrameMetadata),
    ]
    

"""
int16_t gx;  // Gyro X-axis
int16_t gy;  // Gyro Y-axis
int16_t gz;  // Gyro Z-axis
int16_t ax;  // Accel X-axis
int16_t ay;  // Accel Y-axis
int16_t az;  // Accel Z-axis
int rows;    // Number of rowss on frame
int cols;    // Number of columns on frame
uint8_t  type;                // OpenCV type enum (e.g. CV_16S = 3)
uint8_t  channels;            // normally 1 for disparity
uint16_t elemSize;            // Element sizes
double Q[QSize];  // Reprojection matrixs
"""

class StereoData(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("gyroX", ctypes.c_int16),
        ("gyroY", ctypes.c_int16),
        ("gyroZ", ctypes.c_int16),
        ("accelX", ctypes.c_int16),
        ("accelY", ctypes.c_int16),
        ("accelZ", ctypes.c_int16),
        ("rows", ctypes.c_int),
        ("cols", ctypes.c_int),
        ("type", ctypes.c_uint8),
        ("channels", ctypes.c_uint8),    # Number of channels
        ("elemSize", ctypes.c_uint16),   # Frame element sizess
        ("Q", ctypes.c_double * 16),  # Rectification matrix values
    ]
    
    
class _3DPoints(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("X", ctypes.c_float),
        ("Y", ctypes.c_float),
        ("Z", ctypes.c_float)
    ]
    
class PointCloudHdr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("Magic", ctypes.c_uint8 * 10),
        ("Length", ctypes.c_uint64)
    ]


class VideoStreamer:
    """
    Fixed receiver for RC-car video packets.

    Key fix vs the previous stereo-pair implementation:
      - Left and Right JPEGs often have DIFFERENT numSegments.
      - Track expected segment counts PER SIDE, not one shared expectedSegmentsStereo.
    """
    PORT = 5000

    frameSentSignal           = Signal(int, int) # Emitted when a frame is sent out
    startingVideoTransmission = Signal()         # Emitted when video transmission is starting
    endingVideoTransmission   = Signal()         # Emitted when video transmission is ending
    requestVideoSettings      = Signal(int, int) # Emitted to request video settings from GUI
    
    class Decodestatus(Enum):
        DecodingOK = 0
        DecodingIncomplete = auto()
        DecodingError = auto()
        
    class FrameTypes (Enum):
        Mono = 0
        Stereo = 1
        Disparity = 2
        
    class RecordingType(Enum):
        RecordVideo      = 0
        RecordPointCloud = auto()


    def __init__(self, streamInAdapter: UDP = None, streamOutAdapter: UDP = None, path: str = ""):
        self.__cap = None

        self.running = True
        self.__streamOutCanRun = False
        self.__sendFrameID = 0
        self.__segmentMapMono: dict[int, bytes] = {}
        self.__segmentMapL: dict[int, bytes] = {}  # Left frame segments
        self.__segmentMapR: dict[int, bytes] = {}  # Right frame segments
        self.__segmentMapStereoMono: dict[int, bytes] = {}
        self.__recvFrameIDMono: int | None = None
        self.__recvFrameIDStereo: int | None = None
        self.__recvFrameIDStereoMono: int | None = None
        self.__expectedSegmentsMono: int = 0
        self.__expectedSegmentsStereoL: int = 0
        self.__expectedSegmentsStereoR: int = 0
        self.__expectedSegmentsStereoMono: int = 0
        self.__receivedFrameBuff = bytearray()
        self.__lastRecvFrameTime: float | None = None
        self.__lastFpsTime: float | None = None
        self.__disparityRenderMode: str = "depth"
        self.__depthDebugLast: float = 0.0

        self.__receiveThread = Thread(target=self.__streamThread, daemon=True)
        self.__sendThread = None  # Create on demand

        # Circular buffer
        self.__frameBufferMono = CircularBuffer(100)
        self.__frameBufferStereo = CircularBuffer(100)
        self.__frameBufferStereoMono = CircularBuffer(100)
        self.__frameBufferDisparity = CircularBuffer(100)
        self.__streamInBuff = CircularBuffer(100)
        self.__streamInEthBuff = CircularBuffer(100)
        self.__streamOutBuff = CircularBuffer(100)
        
        self.__recordPath: str = ""
        self.__isRecording: bool = False
        self.__recordingMode : VideoStreamer.RecordingType = VideoStreamer.RecordingType.RecordVideo
        self.__recordWriter = None
        self.__recordSize: tuple[int, int] | None = None
        self.__recordFps: float = 30.0
        self.__recordFourcc = cv2.VideoWriter_fourcc(*"mp4v")
        # Signals
        self.sendFrameSignal = Signal()  # Emitted when a frame is ready to be sent out

        # Open receive socket (will do hostname lookup)
        self.__streamSocket: UDP = streamInAdapter

        # Outbound socket (direct IP - no hostname lookup needed)
        self.__streamOutSocket = streamOutAdapter
        self.__srcFile: str = path

        self.__fpsDelta: float

        self.__receiveThread.start()
        
        self.__lastSegId = 0

        # 3D point cloud visualizer (Open3D). The C++ side owns a dedicated render thread so
        # VideoStreamer can safely push point data from its worker thread.
        self.__renderer = None
        try:
            self.__renderer = rc_car_cpp.Renderer3D()
            # Match the PyQt dark theme (#0b111c)
            self.__renderer.set_clear_color(0.043, 0.067, 0.110, 1.0)
            self.__renderer.enable_visualizer_window(True)
        except Exception as e:
            logging.warning(f"3D visualizer disabled (failed to start): {e}")
            self.__renderer = None


    def setVideoSource(self, filePath: str) -> None:
        videoName = os.path.basename(filePath)
        if len(videoName) > 128:
            raise ValueError("Source file name exceeds maximum length of 128 characters")
        self.__srcFile = filePath


    def startStream(self, ip: str) -> bool:
        return True


    def setFrame(self, data: bytes) -> None:
        self.__streamInBuff.push(data)


    def setFrameEth(self, data: bytes) -> None:
        self.__streamInEthBuff.push(data)
        
    
    def setRecordingPath(self, path: str) -> None:
        """
        Set the path where recorded videos will be saved.

        Args:
            path (str): The directory path for saving recorded videos.
        """
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(path, f"recording_{timestamp}")
        self.__recordPath = path
        
    
    def setRecordingState(self, isRecording: bool, state : int) -> None:
        """
        Enable or disable recording of incoming video frames.

        Args:
            isRecording (bool): True to enable recording, False to disable.
        """
        if self.__isRecording and not isRecording:
            self.__close_record_writer()
        if not self.__isRecording and isRecording:
            self.__close_record_writer()
        self.__isRecording = isRecording
        self.__recordingMode = VideoStreamer.RecordingType(state)


    def setDisparityRenderMode(self, mode: str) -> None:
        """Set how disparity frames should be visualized ("depth" or "disparity")."""
        if mode not in ("depth", "disparity"):
            logging.warning("Unknown disparity render mode: %s", mode)
            return
        self.__disparityRenderMode = mode


    def __close_record_writer(self) -> None:
        if self.__recordWriter is not None:
            self.__recordWriter.release()
        self.__recordWriter = None
        self.__recordFilename = ""
        self.__recordSize = None


    def __record_fps(self) -> float:
        if hasattr(self, "_VideoStreamer__fpsDelta") and self.__fpsDelta:
            if self.__fpsDelta > 0:
                fps_val = 1.0 / self.__fpsDelta
                return max(1.0, min(120.0, fps_val))
        return self.__recordFps


    def __ensure_record_writer(self, size: tuple[int, int], tag: str) -> bool:
        if not (self.__isRecording and self.__recordPath):
            return False
        if self.__recordWriter is not None and self.__recordSize == size:
            return True
        self.__close_record_writer()
        try:
            os.makedirs(self.__recordPath, exist_ok=True)
        except OSError:
            logging.error("Failed to create record path: %s", self.__recordPath)
            return False
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{tag}_{timestamp}.mp4"
        filepath = os.path.join(self.__recordPath, filename)
        writer = cv2.VideoWriter(filepath, self.__recordFourcc, self.__record_fps(), size)
        if not writer.isOpened():
            logging.error("Failed to open video writer for: %s", filepath)
            return False
        self.__recordWriter = writer
        self.__recordFilename = filepath
        self.__recordSize = size
        return True


    def __normalize_record_frame(self, frame_bgr: np.ndarray) -> np.ndarray | None:
        if frame_bgr is None:
            return None
        frame = frame_bgr
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.ndim == 3 and frame.shape[2] == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        if frame.dtype != np.uint8:
            frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = np.ascontiguousarray(frame)
        height, width = frame.shape[:2]
        if width % 2 != 0 or height % 2 != 0:
            new_w = width - (width % 2)
            new_h = height - (height % 2)
            if new_w <= 0 or new_h <= 0:
                return None
            frame = cv2.resize(frame, (new_w, new_h))
        return frame


    def __record_frame(self, frame_bgr: np.ndarray, tag: str) -> None:
        frame = self.__normalize_record_frame(frame_bgr)
        if frame is None:
            return
        size = (frame.shape[1], frame.shape[0])
        if not self.__ensure_record_writer(size, tag):
            return
        try:
            self.__recordWriter.write(frame)
        except cv2.error as exc:
            logging.error("VideoWriter write failed: %s", exc)
            self.__close_record_writer()


    def __record_rgb_frame_with_q(self, frame_rgb: np.ndarray, q_values, tag: str = "mono") -> None:
        if not (self.__isRecording and self.__recordPath):
            return

        if self.__recordingMode == VideoStreamer.RecordingType.RecordVideo:
            try:
                self.__record_frame(cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR), tag)
            except cv2.error as exc:
                logging.error("Failed to convert RGB frame for recording: %s", exc)

        try:
            os.makedirs(self.__recordPath, exist_ok=True)
            q_path = os.path.join(self.__recordPath, "RgbFrames_Q.json")

            # Q is constant for the recording; write it once per folder.
            if os.path.exists(q_path):
                return

            q_flat = np.asarray(q_values, dtype=np.float64).reshape(-1)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

            q_data = {
                "timestamp": timestamp,
                "tag": tag,
                "q": [float(v) for v in q_flat],
            }

            with open(q_path, "w", encoding="utf-8") as q_file:
                json.dump(q_data, q_file, indent=2)
        except Exception as exc:
            logging.error("Failed to write RGB Q metadata: %s", exc)
            
            
    def __recordDisparity(self, mat) -> None:
        if mat is None:
            return

        # Ensure mat is CV_16U (uint16)
        if mat.dtype != np.uint16:
            if np.issubdtype(mat.dtype, np.floating):
                mat = np.clip(mat, 0, 65535).astype(np.uint16)
            else:
                mat = mat.astype(np.uint16)

        mat = np.ascontiguousarray(mat)

        try:
            os.makedirs(self.__recordPath, exist_ok=True)
        except OSError as exc:
            logging.error("Cannot create record directory: %s", exc)
            return

        # Record as raw binary: [DISPARITY magic(10)] [rows(4)] [cols(4)] [dtype(1)] [pixel data]
        # so downstream agents can reconstruct the exact CV_16U matrix.
        path = os.path.join(self.__recordPath, "Disparity.bin")
        rows, cols = mat.shape[:2]
        magic = b"DISPARITY\x00"
        header = magic + rows.to_bytes(4, 'little') + cols.to_bytes(4, 'little') + mat.dtype.itemsize.to_bytes(1, 'little')

        try:
            with open(path, "ab") as f:
                f.write(header)
                f.write(mat.tobytes())
        except Exception as exc:
            logging.error("Failed to write disparity data: %s", exc)
            
        self.__isRecording = False


    def __recordPointCloud(self, mat) -> None:
        if mat is None:
            return
        if self.__recordPath == "":
            logging.warning("Point cloud record path is not set")
            return
        try:
            mat = np.ascontiguousarray(mat)
        except Exception as exc:
            logging.error("Failed to prepare point cloud buffer: %s", exc)
            return
        
        pts_xyz  = mat
        pts_xyz = np.ascontiguousarray(pts_xyz, dtype=np.float32)

        hdr = PointCloudHdr()
        hdr.Magic = (ctypes.c_uint8 * 10)(*b"POINTCLOUD")
        hdr.Length = int(pts_xyz.nbytes)
        bytesOut: bytes = ctypes.string_at(ctypes.byref(hdr), ctypes.sizeof(hdr))
        bytesOut += pts_xyz.tobytes()

        # Write to file
        # Append to a single file for the whole recording session.
        # The file contains repeated [PointCloudHdr][payload] chunks.
        filename = "PointCloud.pcl"
        try:
            os.makedirs(self.__recordPath, exist_ok=True)
        except OSError as exc:
            logging.error("Failed to create point cloud record path %s: %s", self.__recordPath, exc)
            return
        path = os.path.join(self.__recordPath, filename)

        try:
            with open(path, "ab") as f:
                f.write(bytesOut)
            logging.info("Appended point cloud chunk (%d points) to %s", len(pts_xyz), path)
        except Exception as e:
            logging.error("Failed to save point cloud to %s: %s", path, e)


    def startStreamOut(self, state: bool) -> None:
        logging.info("Setting stream out to: %s", "ON" if state else "OFF")

        self.__streamOutCanRun = state
        if self.__streamOutCanRun:
            logging.info("Starting stream out thread")
            self.__sendThread = Thread(target=self.__streamOutThread, daemon=True)
            self.__sendThread.start()


    def getFrameOut(self) -> None | np.ndarray:
        if self.__streamOutBuff.empty():
            return None
        return self.__streamOutBuff.read()


    def getFrameIn(self) -> None | np.ndarray:
        if not self.__frameBufferMono.empty():
            return self.__frameBufferMono.read()
        return None


    def getFrameBufferInStereo(self) -> None | np.ndarray:
        if not self.__frameBufferStereo.empty():
            return self.__frameBufferStereo.read()
        return None


    def getFrameBufferInStereoMono(self) -> None | tuple[np.ndarray, int, int, int]:
        if not self.__frameBufferStereoMono.empty():
            return self.__frameBufferStereoMono.read()
        return None
    
    
    def getFrameBufferInDisparity(self) -> None | tuple[np.ndarray, int, int, int]:
        if not self.__frameBufferDisparity.empty():
            return self.__frameBufferDisparity.read()
        return None


    def __sendFrame(self, data: bytes, frameType: int = 0, frameSide: int = 0, videoName: str = "") -> None:
        frameMeta = FrameMetadata()
        frameMeta.videoNameID[:len(videoName)] = (ctypes.c_uint8 * len(videoName))(*bytearray(videoName, 'utf-8'))
        frameMeta.videoNameID[len(videoName)] = 0  # Null-terminate
        frame = Frame()
        frame.frameHeader.frameType = frameType
        frame.frameHeader.frameSide = frameSide

        bytesRemaining = len(data)
        offset = 0
        frameSequence = 0

        while bytesRemaining > 0:
            frameMeta.sequenceID = self.__sendFrameID
            frameMeta.totalLength = len(data)
            frameMeta.segmentID = frameSequence
            frameMeta.numSegments = (len(data) + Frame.MAX_SIZE - 1) // Frame.MAX_SIZE
            frameSequence += 1

            bytesToSend = min(Frame.MAX_SIZE, bytesRemaining)
            frameMeta.length = bytesToSend

            dataSeg = data[offset: offset + bytesToSend]

            ctypes.memmove(
                ctypes.addressof(frame.payload),
                dataSeg,
                bytesToSend
            )

            if bytesToSend < Frame.MAX_SIZE:
                ctypes.memset(
                    ctypes.addressof(frame.payload) + bytesToSend,
                    0,
                    Frame.MAX_SIZE - bytesToSend
                )

            frame.metadata = frameMeta

            offset += bytesToSend
            bytesRemaining -= bytesToSend

            packet = ctypes.string_at(
                ctypes.addressof(frame),
                ctypes.sizeof(frame)
            )

            self.sendFrameSignal.emit(packet)

        self.__sendFrameID += 1

    def __streamOutThread(self) -> None:
        self.startingVideoTransmission.emit()

        videoFileName = os.path.basename(self.__srcFile)
        fileSize: int = os.path.getsize(self.__srcFile)
        bytes_sent: int = 0
        total_estimate: int = fileSize
        try:
            cap = cv2.VideoCapture(self.__srcFile)
        except Exception:
            self.__streamOutCanRun = False
            return

        while self.__streamOutCanRun:
            ret, frame = cap.read()
            if not ret:
                cap.release()
                break

            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 35]
            result, img_encoded = cv2.imencode('.jpg', frame, encode_param)
            data = img_encoded.tobytes()

            size = len(data)
            self.__sendFrame(data, videoName=videoFileName)
            bytes_sent += size
            total_estimate = max(total_estimate, bytes_sent)
            self.frameSentSignal.emit(bytes_sent, total_estimate)

        final_total = max(total_estimate, bytes_sent, fileSize)
        self.frameSentSignal.emit(final_total, final_total)
        logging.info("Upload bytes sent=%d, file_size=%d, total_estimate=%d", bytes_sent, fileSize, total_estimate)
        self.endingVideoTransmission.emit()
        cap.release()
        del cap


    def __streamThread(self) -> None:
        ret : VideoStreamer.Decodestatus
        frameOkCounter = 0
        frameErrCounter = 0
        qualitySetting = 100
        fpsSetting = 30
        receivedFrames = 0

        while True:
            while self.__streamInBuff.empty() and self.__streamInEthBuff.empty():
                time.sleep(0.001)
                continue

            data = self.__streamInBuff.read()
            dataEth = self.__streamInEthBuff.read()
            if data is None and dataEth is None:
                continue
            
            data = data if data is not None else dataEth
            header_size = ctypes.sizeof(FrameHeader)
            if len(data) < header_size:
                logging.warning("Received frame chunk too small for header (%d bytes)", len(data))
                continue

            frameHdr = FrameHeader.from_buffer_copy(data[:header_size])
            frameType = frameHdr.frameHeader.frameType
            frameSide = frameHdr.frameHeader.frameSide
            
            # if frameType == VideoStreamer.FrameTypes.Mono.value:
            #     ret = self.assembleMonoFrame(data, frameHdr)
            # elif frameType == VideoStreamer.FrameTypes.Stereo.value:
            #     ret = self.assembleStereoFrame(data, frameHdr, frameSide)
            # elif frameType == VideoStreamer.FrameTypes.Disparity.value:
            #     ret = self.assembleStereoMonoFrame(data, frameHdr)
            ret = self.assembleStereoMonoFrame(data, frameHdr)

            if ret == VideoStreamer.Decodestatus.DecodingOK:
                frameOkCounter += 1
            elif ret == VideoStreamer.Decodestatus.DecodingError:
                frameErrCounter += 1
                
            receivedFrames = frameOkCounter + frameErrCounter

            # If < 50% ratio of errors, request to lower JPEG encoding quality
            if frameErrCounter == 0:
                continue

            logging.debug(f"Frame OK: {frameOkCounter}, Frame Errors: {frameErrCounter}")


    def _update_fps(self) -> str:
        now = time.time()
        fps_text = ""
        if self.__lastRecvFrameTime is not None:
            if self.__lastFpsTime is None or now - self.__lastFpsTime >= 1.0:
                self.__lastFpsTime = now
                self.__fpsDelta = now - self.__lastRecvFrameTime
            if self.__fpsDelta > 0:
                fps_val = 1.0 / self.__fpsDelta
                fps_text = f"FPS: {fps_val:.1f}"
        self.__lastRecvFrameTime = now
        return fps_text


    def assembleMonoFrame(self, data: bytes, header: FrameHeader) -> "VideoStreamer.Decodestatus":
        header_size = ctypes.sizeof(FrameHeader)
        seqID = header.metadata.sequenceID
        segID = header.metadata.segmentID
        numSegs = header.metadata.numSegments
        totalLen = header.metadata.totalLength
        segLen = header.metadata.length
        out_of_order = False

        if len(self.__receivedFrameBuff) < totalLen:
            self.__receivedFrameBuff = bytearray(totalLen)

        if (self.__recvFrameIDMono is not None and
                seqID != self.__recvFrameIDMono and
                len(self.__segmentMapMono) > 0 and
                len(self.__segmentMapMono) < self.__expectedSegmentsMono):
            logging.warning(f"Dropping incomplete mono frame ID {self.__recvFrameIDMono}")
            self.__segmentMapMono.clear()
            out_of_order = True

        if self.__recvFrameIDMono != seqID:
            self.__recvFrameIDMono = seqID
            self.__expectedSegmentsMono = numSegs
            self.__segmentMapMono.clear()

        payload_start = header_size
        payload_end = header_size + segLen
        if payload_end > len(data):
            logging.warning("Mono segment payload truncated (seq=%d seg=%d expected=%d have=%d)",
                            seqID, segID, segLen, len(data) - header_size)
            return VideoStreamer.Decodestatus.DecodingError
        self.__segmentMapMono[segID] = bytes(data[payload_start:payload_end])

        if len(self.__segmentMapMono) == self.__expectedSegmentsMono:
            jpeg_bytes = bytearray()
            for i in range(self.__expectedSegmentsMono):
                if i not in self.__segmentMapMono:
                    logging.warning(f"Missing segment {i} for mono frame {self.__recvFrameIDMono}")
                    self.__segmentMapMono.clear()
                    self.__recvFrameIDMono = None
                    return VideoStreamer.Decodestatus.DecodingError
                jpeg_bytes.extend(self.__segmentMapMono[i])

            npbuf = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(npbuf, cv2.IMREAD_COLOR)

            fps_text = self._update_fps()
            if fps_text and frame is not None:
                cv2.putText(frame, fps_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)

            self.__segmentMapMono.clear()
            self.__recvFrameIDMono = None

            if frame is None:
                return VideoStreamer.Decodestatus.DecodingError
            if self.__isRecording and self.__recordPath:
                self.__record_frame(frame, "mono")
            rgbImage = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            self.__frameBufferMono.push(rgbImage)
            return (VideoStreamer.Decodestatus.DecodingError
                    if out_of_order
                    else VideoStreamer.Decodestatus.DecodingOK)

        return (VideoStreamer.Decodestatus.DecodingError
                if out_of_order
                else VideoStreamer.Decodestatus.DecodingIncomplete)


    def assembleStereoFrame(self, data: bytes, header: FrameHeader, side: int) -> "VideoStreamer.Decodestatus":
        """
        Assemble a stereo frame pair from received segments.

        IMPORTANT: left/right can have different numSegments because each is its own JPEG.
        """
        header_size = ctypes.sizeof(FrameHeader)
        seqID = header.metadata.sequenceID
        segID = header.metadata.segmentID
        numSegs = header.metadata.numSegments
        totalLen = header.metadata.totalLength
        segLen = header.metadata.length
        out_of_order = False

        if (self.__recvFrameIDStereo is not None and
                seqID != self.__recvFrameIDStereo and
                (len(self.__segmentMapL) > 0 or len(self.__segmentMapR) > 0)):
            logging.debug(f"Dropping incomplete stereo frame ID {self.__recvFrameIDStereo}")
            self.__segmentMapL.clear()
            self.__segmentMapR.clear()
            self.__expectedSegmentsStereoL = 0
            self.__expectedSegmentsStereoR = 0
            out_of_order = True

        if self.__recvFrameIDStereo != seqID:
            self.__recvFrameIDStereo = seqID
            self.__segmentMapL.clear()
            self.__segmentMapR.clear()
            self.__expectedSegmentsStereoL = 0
            self.__expectedSegmentsStereoR = 0

        if side == 0:
            segmentMap = self.__segmentMapL
            if self.__expectedSegmentsStereoL in (0, numSegs):
                self.__expectedSegmentsStereoL = numSegs
            else:
                logging.warning("Stereo L numSegments changed mid-frame (seq=%d old=%d new=%d); resetting",
                                seqID, self.__expectedSegmentsStereoL, numSegs)
                self.__segmentMapL.clear()
                self.__expectedSegmentsStereoL = numSegs
                out_of_order = True
        else:
            segmentMap = self.__segmentMapR
            if self.__expectedSegmentsStereoR in (0, numSegs):
                self.__expectedSegmentsStereoR = numSegs
            else:
                logging.warning("Stereo R numSegments changed mid-frame (seq=%d old=%d new=%d); resetting",
                                seqID, self.__expectedSegmentsStereoR, numSegs)
                self.__segmentMapR.clear()
                self.__expectedSegmentsStereoR = numSegs
                out_of_order = True

        payload_start = header_size
        payload_end = header_size + segLen
        if payload_end > len(data):
            logging.warning("Stereo segment payload truncated (seq=%d seg=%d expected=%d have=%d)",
                            seqID, segID, segLen, len(data) - header_size)
            return VideoStreamer.Decodestatus.DecodingError
        segmentMap[segID] = bytes(data[payload_start:payload_end])

        # Need both expected counts known and both sides complete.
        if self.__expectedSegmentsStereoL <= 0 or self.__expectedSegmentsStereoR <= 0:
            return (VideoStreamer.Decodestatus.DecodingError
                    if out_of_order
                    else VideoStreamer.Decodestatus.DecodingIncomplete)
        if len(self.__segmentMapL) != self.__expectedSegmentsStereoL:
            return (VideoStreamer.Decodestatus.DecodingError
                    if out_of_order
                    else VideoStreamer.Decodestatus.DecodingIncomplete)
        if len(self.__segmentMapR) != self.__expectedSegmentsStereoR:
            return (VideoStreamer.Decodestatus.DecodingError
                    if out_of_order
                    else VideoStreamer.Decodestatus.DecodingIncomplete)

        left_bytes = bytearray()
        right_bytes = bytearray()

        for i in range(self.__expectedSegmentsStereoL):
            if i not in self.__segmentMapL:
                logging.warning(f"Missing segment {i} for stereo LEFT frame {self.__recvFrameIDStereo}")
                self.__segmentMapL.clear()
                self.__segmentMapR.clear()
                self.__recvFrameIDStereo = None
                self.__expectedSegmentsStereoL = 0
                self.__expectedSegmentsStereoR = 0
                return VideoStreamer.Decodestatus.DecodingError
            left_bytes.extend(self.__segmentMapL[i])

        for i in range(self.__expectedSegmentsStereoR):
            if i not in self.__segmentMapR:
                logging.warning(f"Missing segment {i} for stereo RIGHT frame {self.__recvFrameIDStereo}")
                self.__segmentMapL.clear()
                self.__segmentMapR.clear()
                self.__recvFrameIDStereo = None
                self.__expectedSegmentsStereoL = 0
                self.__expectedSegmentsStereoR = 0
                return VideoStreamer.Decodestatus.DecodingError
            right_bytes.extend(self.__segmentMapR[i])

        left_img = cv2.imdecode(np.frombuffer(left_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        right_img = cv2.imdecode(np.frombuffer(right_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)

        if left_img is None or right_img is None:
            logging.warning("Failed to decode stereo images for frame %s", self.__recvFrameIDStereo)
            self.__segmentMapL.clear()
            self.__segmentMapR.clear()
            self.__recvFrameIDStereo = None
            self.__expectedSegmentsStereoL = 0
            self.__expectedSegmentsStereoR = 0
            return VideoStreamer.Decodestatus.DecodingError

        fps_text = self._update_fps()
        if fps_text:
            for img in (left_img, right_img):
                cv2.putText(img, fps_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)

        try:
            stitched = cv2.hconcat([left_img, right_img])
        except Exception:
            stitched = left_img

        self.__segmentMapL.clear()
        self.__segmentMapR.clear()
        self.__recvFrameIDStereo = None
        self.__expectedSegmentsStereoL = 0
        self.__expectedSegmentsStereoR = 0

        if self.__isRecording and self.__recordPath:
            self.__record_frame(stitched, "stereo")
        rgbImage = cv2.cvtColor(stitched, cv2.COLOR_BGR2RGB)
        self.__frameBufferStereo.push(rgbImage)
        return (VideoStreamer.Decodestatus.DecodingError
                if out_of_order
                else VideoStreamer.Decodestatus.DecodingOK)


    def assembleStereoMonoFrame(self, data: bytes, header: FrameHeader) -> "VideoStreamer.Decodestatus":
        header_size = ctypes.sizeof(FrameHeader)
        seqID = header.metadata.sequenceID
        segID = header.metadata.segmentID
        numSegs = header.metadata.numSegments
        totalLen = header.metadata.totalLength
        segLen = header.metadata.length
        out_of_order = False
        
        # print("seg ID:", segID, "numSegs:", numSegs, "totalLen:", totalLen, "segLen:", segLen, "frameID:", seqID)
        
        # return

        if len(self.__receivedFrameBuff) < totalLen:
            self.__receivedFrameBuff = bytearray(totalLen)

        if (self.__recvFrameIDStereoMono is not None and
                seqID != self.__recvFrameIDStereoMono and
                (len(self.__segmentMapStereoMono) > 0)):
            logging.debug(f"Dropping incomplete stereo frame ID {self.__recvFrameIDStereoMono}. Last segment ID received: {self.__lastSegId}"
                         ""f" Expected segment number: {numSegs}. Segment map length: {len(self.__segmentMapStereoMono)}")
            self.__segmentMapStereoMono.clear()
            out_of_order = True

        if self.__recvFrameIDStereoMono != seqID:
            self.__recvFrameIDStereoMono = seqID
            self.__expectedSegmentsStereoMono = numSegs
            self.__segmentMapStereoMono.clear()
            self.__lastSegId = -1
            
        self.__lastSegId = segID

        payload_start = header_size
        payload_end = header_size + segLen
        if payload_end > len(data):
            logging.warning("Stereo segment payload truncated (seq=%d seg=%d expected=%d have=%d)",
                            seqID, segID, segLen, len(data) - header_size)
            return VideoStreamer.Decodestatus.DecodingError
        self.__segmentMapStereoMono[segID] = bytes(data[payload_start:payload_end])

        # we may now decode
        if len(self.__segmentMapStereoMono) == (self.__expectedSegmentsStereoMono):
            decodeRet = self.__decodePointCloudFrame(self.__segmentMapStereoMono)
            self.__segmentMapStereoMono.clear()
            self.__recvFrameIDStereoMono = None
            self.__expectedSegmentsStereoMono = 0
            return (VideoStreamer.Decodestatus.DecodingError
                    if not decodeRet
                    else VideoStreamer.Decodestatus.DecodingOK)
        return (VideoStreamer.Decodestatus.DecodingError
            if out_of_order
            else VideoStreamer.Decodestatus.DecodingIncomplete)


    def __decodePointCloudFrame(self, segmentMap : dict) -> bool:
        """Decode either disparity+Q payloads or direct CV_32FC3 point-cloud payloads."""
        imgBytes = bytearray()
        for i in range(self.__expectedSegmentsStereoMono):
            if i not in segmentMap:
                logging.warning(f"Missing segment {i} for stereo frame {self.__recvFrameIDStereoMono}")
                segmentMap.clear()
                self.__recvFrameIDStereoMono = None
                return False
            imgBytes.extend(segmentMap[i])

        if len(imgBytes) < ctypes.sizeof(StereoData):
            logging.warning("Point-cloud payload too small (%d bytes)", len(imgBytes))
            return False

        stereoDatas = StereoData.from_buffer_copy(imgBytes)
        imgBytes_ = imgBytes[ctypes.sizeof(StereoData):]
        Matx44d = (ctypes.c_double * 4) * 4

        q_mat = Matx44d()
        for idx, val in enumerate(stereoDatas.Q):
            q_mat[idx // 4][idx % 4] = val

        dtype_map = {
            0: np.uint8,
            1: np.int8,
            2: np.uint16,
            3: np.int16,
            4: np.int32,
            5: np.float32,
            6: np.float64,
        }
        depth = stereoDatas.type & 7
        if depth not in dtype_map:
            logging.warning("Unsupported OpenCV depth type in stereo payload: %s", depth)
            return False
        dtype = dtype_map[depth]

        channels = stereoDatas.channels
        rows = stereoDatas.rows
        cols = stereoDatas.cols

        # If it's an image payload (RGB/BGR/BGRA), push directly to display and skip depth/pointcloud.
        # Some senders misuse `channels`/`cols` (e.g., channels=1 with cols == bytes_per_row for RGB).
        is_u8 = depth == 0
        elem_size = int(getattr(stereoDatas, "elemSize", 0) or 0)
        payload_u8 = np.frombuffer(imgBytes_, dtype=np.uint8)
        looks_like_image = is_u8 and (
            channels in (3, 4)
            or elem_size in (3, 4)
            or payload_u8.size == (rows * cols * 3)
            or payload_u8.size == (rows * cols * 4)
            or (channels == 1 and cols > 0 and (cols % 3 == 0) and payload_u8.size == (rows * cols))
        )

        if looks_like_image:
            # Try and decompresss. If it fails, we treat is as uncompressed frame
            try:
                img_decoded = cv2.imdecode(np.frombuffer(imgBytes_, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img_decoded is not None and img_decoded.shape[0] == rows and img_decoded.shape[1] == cols:
                    frame_rgb = cv2.cvtColor(img_decoded, cv2.COLOR_BGR2RGB)
                    self.__record_rgb_frame_with_q(frame_rgb, stereoDatas.Q)
                    self.__frameBufferMono.push(frame_rgb)
                    return True
            except Exception as exc:
                logging.debug("JPEG decode failed for RGB frame; falling back to raw RGB decode: %s", exc)

            # If not compressed (or JPEG decode returned invalid output), expect raw RGB data.
            if rows <= 0 or cols <= 0:
                logging.warning("Invalid RGB frame geometry rows=%d cols=%d", rows, cols)
                return False

            raw_buf = payload_u8

            # Case A: Correct metadata for packed 3-channel.
            # Sender converts BGR/BGRA -> RGB for raw Ethernet frames, so treat this as RGB already.
            if raw_buf.size == rows * cols * 3:
                frame_rgb = raw_buf.reshape(rows, cols, 3)
                self.__record_rgb_frame_with_q(frame_rgb, stereoDatas.Q)
                self.__frameBufferMono.push(frame_rgb)
                return True

            # Case B: Correct metadata for packed 4-channel (BGRA/RGBA). Assume BGRA from OpenCV.
            if raw_buf.size == rows * cols * 4:
                frame_bgra = raw_buf.reshape(rows, cols, 4)
                frame_rgb = cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2RGB)
                self.__record_rgb_frame_with_q(frame_rgb, stereoDatas.Q)
                self.__frameBufferMono.push(frame_rgb)
                return True

            # Case C: Misreported as 1-channel with cols == bytes_per_row (packed RGB bytes).
            # This yields a "3 tiled grayscale copies" look in the UI if not corrected.
            if channels == 1 and (cols % 3 == 0) and raw_buf.size == rows * cols:
                pix_w = cols // 3
                frame_rgb = raw_buf.reshape(rows, pix_w, 3)
                self.__record_rgb_frame_with_q(frame_rgb, stereoDatas.Q)
                self.__frameBufferMono.push(frame_rgb)
                return True

            # Unknown raw image layout.
            logging.warning(
                "Unsupported raw image payload layout rows=%d cols=%d channels=%d elemSize=%d payload_bytes=%d",
                rows, cols, channels, elem_size, int(raw_buf.size)
            )
            return False
            

        if rows <= 0 or cols <= 0 or channels <= 0:
            logging.warning("Invalid frame geometry rows=%d cols=%d channels=%d", rows, cols, channels)
            return False

        buf = np.frombuffer(imgBytes_, dtype=dtype)
        expected_vals = rows * cols * channels
        if buf.size < expected_vals:
            logging.warning("Point-cloud payload truncated: expected elements=%d got=%d", expected_vals, buf.size)
            return False
        if buf.size > expected_vals:
            buf = buf[:expected_vals]

        if channels == 1:
            frame = buf.reshape(rows, cols)
        else:
            frame = buf.reshape(rows, cols, channels)

        is_direct_point_cloud = (depth == 5 and (channels == 3 or channels == 6))
        has_color = (channels == 6)
        Q = None

        if is_direct_point_cloud:
            points3d = np.zeros((rows, cols, 3), dtype=np.float32)
            color3d = np.zeros((rows, cols, 3), dtype=np.float32)
            if channels == 3:
                points3d = frame.astype(np.float32, copy=False)
            elif channels == 6:
                points3dColor = frame.astype(np.float32, copy=False)

                # Extract the color and the coordinates
                points3d = points3dColor[:, :, :3]
                color3d = points3dColor[:, :, 3:]

            Z = points3d[:, :, 2]
            display_source = Z
            mode_name = "pc32f"
            base_valid = np.isfinite(Z) & (Z > 0.0)
        else:
            if channels != 1:
                logging.warning(
                    "Unsupported stereo payload layout for disparity decode: depth=%d channels=%d",
                    depth, channels
                )
                return False

            disp = frame
            if disp.dtype == np.float32:
                disp32f = disp
            elif disp.dtype in (np.uint8, np.int16, np.int32):
                disp32f = disp.astype(np.float32, copy=False) / 16.0
            else:
                disp32f = disp.astype(np.float32) / 16.0

            Q = np.array(q_mat, dtype=np.float32)
            points3d = cv2.reprojectImageTo3D(disp32f, Q)
            Z = points3d[:, :, 2]
            display_source = disp32f
            mode_name = "disparity"
            base_valid = (disp32f > 0) & np.isfinite(Z)

        # base_valid already incorporates np.isfinite(Z), so no need to re-check.
        valid = base_valid

        z_min_abs, z_max_abs = 0.2, 30.0
        Z_valid_flat = Z[valid]
        if Z_valid_flat.size > 0:
            # Subsample for percentile: statistically equivalent for display, ~8x faster.
            Z_sample = Z_valid_flat[::8] if Z_valid_flat.size > 8 else Z_valid_flat
            z_lo = float(np.percentile(Z_sample, 5))
            z_hi = float(np.percentile(Z_sample, 95))
            if not np.isfinite(z_lo) or not np.isfinite(z_hi) or z_hi <= z_lo:
                z_lo, z_hi = z_min_abs, z_max_abs
        else:
            z_lo, z_hi = z_min_abs, z_max_abs

        z_lo = max(z_min_abs, min(z_lo, z_max_abs))
        z_hi = max(z_lo + 1e-6, min(z_hi, z_max_abs))

        # Build Z_norm without nan intermediates: only operate on valid pixels,
        # leaving invalid pixels as 0. This avoids 3 full-array temporaries.
        Z_norm = np.zeros(Z.shape, dtype=np.uint8)
        if Z_valid_flat.size > 0:
            Z_v = np.clip(Z_valid_flat, z_lo, z_hi)
            Z_norm[valid] = np.clip(255.0 * (z_hi - Z_v) / (z_hi - z_lo), 0.0, 255.0).astype(np.uint8)

        Z_color = cv2.applyColorMap(Z_norm, cv2.COLORMAP_TURBO)
        Z_color[~valid] = (0, 0, 0)
        Z_color = cv2.cvtColor(Z_color, cv2.COLOR_BGR2RGB)

        display_frame = Z_color if self.__disparityRenderMode == "depth" else display_source

        if self.__isRecording:
            if self.__recordingMode == VideoStreamer.RecordingType.RecordVideo:
                def _to_bgr(frame_in: np.ndarray | None) -> np.ndarray | None:
                    if frame_in is None:
                        return None
                    if frame_in.ndim == 2:
                        finite = np.isfinite(frame_in)
                        if not np.any(finite):
                            return None
                        min_v = float(np.min(frame_in[finite]))
                        max_v = float(np.max(frame_in[finite]))
                        if max_v <= min_v:
                            norm = np.zeros_like(frame_in, dtype=np.uint8)
                        else:
                            norm = ((frame_in - min_v) * (255.0 / (max_v - min_v))).astype(np.uint8)
                        return cv2.cvtColor(norm, cv2.COLOR_GRAY2BGR)
                    if frame_in.ndim == 3 and frame_in.shape[2] == 1:
                        return cv2.cvtColor(frame_in[:, :, 0], cv2.COLOR_GRAY2BGR)
                    if frame_in.ndim == 3 and frame_in.shape[2] == 3:
                        return cv2.cvtColor(frame_in, cv2.COLOR_RGB2BGR)
                    return None

                bgr_frame = _to_bgr(display_frame)
                if bgr_frame is not None:
                    self.__record_frame(bgr_frame, "")
            elif self.__recordingMode == VideoStreamer.RecordingType.RecordPointCloud:
                record_mask = base_valid & (Z > 0.2) & (Z < 30.0)
                # if is_direct_point_cloud:
                self.__recordPointCloud(points3d[record_mask])
                # else:
                #     self.__recordDisparity(frame)

        if self.__renderer is not None:
            # points3d = _points3d
            x = points3d[:, :, 0]
            y = points3d[:, :, 1]
            finite_xyz = np.isfinite(x) & np.isfinite(y) & np.isfinite(Z)
            pc_mask = (
                base_valid
                & finite_xyz
                & (Z > 0.01)
                & (Z < 5.0)
                # & (dist < 25.0)
                & (np.abs(x) < 12.0)
                & (np.abs(y) < 8.0)
            )
            pts_xyz = points3d[pc_mask]
            if pts_xyz.size == 0:
                pc_mask = base_valid & finite_xyz
                pts_xyz = points3d[pc_mask]

            pts_xyz = pts_xyz.astype(np.float32, copy=False)
            pts_xyz = np.ascontiguousarray(pts_xyz)

            # Extract matching color data if available
            pts_rgb = None
            if has_color:
                pts_rgb = color3d[pc_mask]

            n_points = int(pts_xyz.shape[0])

            max_points = 200_000
            if n_points > max_points:
                step = max(1, n_points // max_points)
                pts_xyz = pts_xyz[::step]
                if pts_rgb is not None:
                    pts_rgb = pts_rgb[::step]
                n_points = int(pts_xyz.shape[0])

            if n_points > 0:
                pc_bytes = pts_xyz.view(np.uint8)
                if pts_rgb is not None:
                    # Convert float32 RGB (0-255) to uint32 to match C++ PointRGB struct
                    pts_rgb = np.clip(pts_rgb, 0, 255).astype(np.uint32)
                    pts_rgb = np.ascontiguousarray(pts_rgb)
                    rgb_bytes = pts_rgb.view(np.uint8)
                    self.__renderer.setPointCloudColorData(pc_bytes, rgb_bytes, n_points)
                else:
                    self.__renderer.setPointCloudData(pc_bytes, n_points)
            else:
                # If no point cloud frames received in over a period of time  
                pass

        self.__frameBufferStereoMono.push((display_frame, (stereoDatas.gyroX, stereoDatas.gyroY, stereoDatas.gyroZ)))
        return True
