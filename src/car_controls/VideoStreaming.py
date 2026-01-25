import cv2
from threading import Thread
import logging

from network.udp_client import UDP
from utils.utilities import CircularBuffer
import configparser
import numpy as np
import time
import os
import ctypes

from utils.utilities import Signal
from enum import Enum, auto


MAX_UDP_PACKET_SIZE = 65507


class FrameMetadata(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("videoNameID",  ctypes.c_uint8 * 128),
        ("sequenceID",  ctypes.c_uint32),
        ("segmentID",   ctypes.c_uint8),
        ("numSegments", ctypes.c_uint8),
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
        ("rows", ctypes.c_double * 16),  # Rectification matrix values
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

    _PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

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
        self.__disparityDebugLast: float = 0.0

        self.__receiveThread = Thread(target=self.__streamThread, daemon=True)
        self.__sendThread = None  # Create on demand

        # Circular buffer
        self.__frameBufferMono = CircularBuffer(100)
        self.__frameBufferStereo = CircularBuffer(100)
        self.__frameBufferStereoMono = CircularBuffer(100)
        self.__frameBufferDisparity = CircularBuffer(100)
        self.__streamInBuff = CircularBuffer(200)
        self.__streamOutBuff = CircularBuffer(100)
        self.__recordPath: str = ""
        self.__isRecording: bool = False
        self.__recordWriter = None
        self.__recordFilename: str = ""
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


    def setVideoSource(self, filePath: str) -> None:
        videoName = os.path.basename(filePath)
        if len(videoName) > 128:
            raise ValueError("Source file name exceeds maximum length of 128 characters")
        self.__srcFile = filePath


    def startStream(self, ip: str) -> bool:
        return True


    def setFrame(self, data: bytes) -> None:
        self.__streamInBuff.push(data)
        
    
    def setRecordingPath(self, path: str) -> None:
        """
        Set the path where recorded videos will be saved.

        Args:
            path (str): The directory path for saving recorded videos.
        """
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(path, f"recording_{timestamp}")
        self.__recordPath = path
        
    
    def setRecordingState(self, isRecording: bool) -> None:
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


    def setDisparityRenderMode(self, mode: str) -> None:
        """Set how disparity frames should be visualized ("depth" or "disparity")."""
        if mode not in ("depth", "disparity"):
            logging.warning("Unknown disparity render mode: %s", mode)
            return
        self.__disparityRenderMode = mode
        
        
    def __process_disparity_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        Process a disparity frame for visualization based on the selected render mode.

        Args:
            frame (np.ndarray): Input disparity frame.

        Returns:
            np.ndarray: Processed frame for visualization.
        """
        if frame is None:
            return frame

        disp = frame
        if disp.ndim == 3:
            if disp.shape[2] == 4:
                disp = cv2.cvtColor(disp, cv2.COLOR_BGRA2GRAY)
            else:
                disp = cv2.cvtColor(disp, cv2.COLOR_BGR2GRAY)
        if disp.ndim != 2:
            return frame

        disp32f = np.maximum(disp.astype(np.float32), 0.0)
        disp8U = np.zeros_like(disp32f, dtype=np.uint8)
        valid_mask = (disp32f > 0.0).astype(np.uint8)
        if valid_mask.ndim != 2:
            valid_mask = valid_mask[:, :, 0]
        if valid_mask.dtype != np.uint8:
            valid_mask = valid_mask.astype(np.uint8)
        valid_count = cv2.countNonZero(valid_mask)
        if valid_count > 0:
            valid_vals = disp32f[valid_mask > 0]
            if valid_vals.size > 0:
                vmin = float(np.percentile(valid_vals, 5))
                vmax = float(np.percentile(valid_vals, 95))
            else:
                vmin, vmax, _, _ = cv2.minMaxLoc(disp32f, mask=valid_mask)
            if vmax > vmin:
                scale = 255.0 / (vmax - vmin)
                shift = -vmin * scale
                scaled = cv2.convertScaleAbs(disp32f, alpha=scale, beta=shift)
                np.copyto(disp8U, scaled, where=(valid_mask > 0))
        else:
            vmin, vmax = 0.0, 0.0

        now = time.time()
        if now - self.__disparityDebugLast >= 2.0:
            total = valid_mask.size
            ratio = (valid_count / total) if total else 0.0
            logging.info(
                "Disparity debug: mode=%s valid=%.2f%% min=%.2f max=%.2f",
                self.__disparityRenderMode,
                ratio * 100.0,
                vmin,
                vmax,
            )
            self.__disparityDebugLast = now

        if self.__disparityRenderMode == "depth":
            colored = cv2.applyColorMap(disp8U, cv2.COLORMAP_JET)
            return cv2.cvtColor(colored, cv2.COLOR_BGR2RGB)

        return cv2.cvtColor(disp8U, cv2.COLOR_GRAY2RGB)


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
            while self.__streamInBuff.empty():
                time.sleep(0.0001)
                continue

            data = self.__streamInBuff.read()
            if data is None:
                continue

            header_size = ctypes.sizeof(FrameHeader)
            if len(data) < header_size:
                logging.warning("Received frame chunk too small for header (%d bytes)", len(data))
                continue

            frameHdr = FrameHeader.from_buffer_copy(data[:header_size])
            frameType = frameHdr.frameHeader.frameType
            frameSide = frameHdr.frameHeader.frameSide
            
            if frameType == VideoStreamer.FrameTypes.Mono.value:
                ret = self.assembleMonoFrame(data, frameHdr)
            elif frameType == VideoStreamer.FrameTypes.Stereo.value:
                ret = self.assembleStereoFrame(data, frameHdr, frameSide)
            elif frameType == VideoStreamer.FrameTypes.Disparity.value:
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

        if len(self.__receivedFrameBuff) < totalLen:
            self.__receivedFrameBuff = bytearray(totalLen)

        if (self.__recvFrameIDStereoMono is not None and
                seqID != self.__recvFrameIDStereoMono and
                (len(self.__segmentMapStereoMono) > 0)):
            logging.debug(f"Dropping incomplete stereo frame ID {self.__recvFrameIDStereoMono}")
            self.__segmentMapStereoMono.clear()
            out_of_order = True

        if self.__recvFrameIDStereoMono != seqID:
            self.__recvFrameIDStereoMono = seqID
            self.__expectedSegmentsStereoMono = numSegs
            self.__segmentMapStereoMono.clear()

        payload_start = header_size
        payload_end = header_size + segLen
        if payload_end > len(data):
            logging.warning("Stereo segment payload truncated (seq=%d seg=%d expected=%d have=%d)",
                            seqID, segID, segLen, len(data) - header_size)
            return VideoStreamer.Decodestatus.DecodingError
        self.__segmentMapStereoMono[segID] = bytes(data[payload_start:payload_end])

        if len(self.__segmentMapStereoMono) == self.__expectedSegmentsStereoMono:
            imgBytes = bytearray()
            for i in range(self.__expectedSegmentsStereoMono):
                if i not in self.__segmentMapStereoMono:
                    logging.warning(f"Missing segment {i} for stereo frame {self.__recvFrameIDStereoMono}")
                    self.__segmentMapStereoMono.clear()
                    self.__recvFrameIDStereoMono = None
                    return VideoStreamer.Decodestatus.DecodingError
                imgBytes.extend(self.__segmentMapStereoMono[i])
            gyro_data = StereoData.from_buffer_copy(imgBytes)
            imgBytes_ = imgBytes[ctypes.sizeof(StereoData):]
            img = cv2.imdecode(np.frombuffer(imgBytes_, dtype=np.uint8), cv2.IMREAD_UNCHANGED)

            if img is None:
                logging.warning("Failed to decode stereo-mono image for frame %s", self.__recvFrameIDStereoMono)
                self.__segmentMapStereoMono.clear()
                self.__recvFrameIDStereoMono = None
                return VideoStreamer.Decodestatus.DecodingError

            fps_text = self._update_fps()
            if fps_text:
                cv2.putText(img, fps_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)

            self.__segmentMapStereoMono.clear()
            self.__recvFrameIDStereoMono = None

            if self.__isRecording and self.__recordPath:
                overlay_img = img.copy()
                gyro_text = f"GyroX: {gyro_data.gyroX}, GyroY: {gyro_data.gyroY}, GyroZ: {gyro_data.gyroZ}"
                cv2.putText(overlay_img, gyro_text, (10, img.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
                self.__record_frame(overlay_img, "stereo_mono")

            # rgbImage = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            rgbImage = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            # rgbImage = self.__process_disparity_frame(img)
            # rgbImage = img
            print(img.dtype, img.shape)

            self.__frameBufferStereoMono.push((rgbImage, (gyro_data.gyroX, gyro_data.gyroY, gyro_data.gyroZ)))
            return (VideoStreamer.Decodestatus.DecodingError
                    if out_of_order
                    else VideoStreamer.Decodestatus.DecodingOK)
        return (VideoStreamer.Decodestatus.DecodingError
                if out_of_order
                else VideoStreamer.Decodestatus.DecodingIncomplete)


    def __decodePointCloudFrame(self, segmentMap : dict, frameId: int) -> bool:
        """Decode a point cloud frame from received data."""
        imgBytes = bytearray()
        for i in range(self.__expectedSegmentsStereoMono):
            if i not in segmentMap:
                logging.warning(f"Missing segment {i} for stereo frame {self.__recvFrameIDStereoMono}")
                segmentMap.clear()
                self.__recvFrameIDStereoMono = None
                return VideoStreamer.Decodestatus.DecodingError
            imgBytes.extend(segmentMap[i])
        stereoDatas = StereoData.from_buffer_copy(imgBytes)
        imgBytes_ = imgBytes[ctypes.sizeof(StereoData):]
        
        # Reconstruct the frame
        print(f"Resolution: {stereoDatas.cols}x{stereoDatas.rows}", )
        
