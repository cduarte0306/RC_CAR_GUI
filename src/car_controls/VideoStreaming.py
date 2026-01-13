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
        ("frameType",   ctypes.c_uint8),  # 0: Mono, 1: Stereo
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

class StereoFrameMonoHeader(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("xPos",   ctypes.c_int16),
        ("yPos",   ctypes.c_int16),
        ("width",  ctypes.c_uint16),
        ("frame",  ctypes.c_char_p)
    ]
    
class GyroData(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("gyroX", ctypes.c_int16),
        ("gyroY", ctypes.c_int16),
        ("gyroZ", ctypes.c_int16),
        ("accelX", ctypes.c_int16),
        ("accelY", ctypes.c_int16),
        ("accelZ", ctypes.c_int16),
    ]
    
class VideoStreamer:
    PORT = 5000
    
    frameSentSignal = Signal(int, int)    # Emitted when a frame is sent out
    startingVideoTransmission = Signal()  # Emitted when video transmission is starting
    endingVideoTransmission   = Signal()  # Emitted when video transmission is ending

    def __init__(self, streamInAdapter : UDP = None, streamOutAdapter : UDP = None, path:str = ""):
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
        self.__expectedSegmentsStereo: int = 0
        self.__expectedSegmentsStereoMono: int = 0
        self.__receivedFrameBuff = bytearray()
        self.__lastRecvFrameTime : float | None = None
        self.__lastFpsTime : float | None = None

        self.__receiveThread = Thread(target=self.__streamThread, daemon=True)
        self.__sendThread    = None  # Create on demand
        
        # Circular buffer
        self.__frameBufferMono   = CircularBuffer(100)
        self.__frameBufferStereo = CircularBuffer(100)
        self.__frameBufferStereoMono = CircularBuffer(100)
        self.__streamInBuff  = CircularBuffer(200)
        self.__streamOutBuff = CircularBuffer(100)
        
        # Signals
        self.sendFrameSignal = Signal()  # Emitted when a frame is ready to be sent out

        # Open receive socket (will do hostname lookup)
        self.__streamSocket : UDP = streamInAdapter
        # self.__streamSocket = UDP(VideoStreamer.PORT)
        
        # Outbound socket (direct IP - no hostname lookup needed)
        # self.__streamOutSocket = UDP(VideoStreamer.PORT, host="192.168.1.10")
        self.__streamOutSocket = streamOutAdapter
        self.__srcFile:str = path
        
        self.__fpsDelta : float
        
        self.__receiveThread.start()


    def setVideoSource(self, filePath:str) -> None:
        """
        Set the video source file for streaming out

        Args:
            filePath (str): Source file path
        """
        # Check if the name of the video file itself does not exceed max length
        videoName = os.path.basename(filePath)
        if len(videoName) > 128:
            raise ValueError("Source file name exceeds maximum length of 128 characters")
        self.__srcFile = filePath


    def startStream(self, ip: str) -> bool:
        # self.__streamSocket.bindSocket(VideoStreamer.PORT)

        # Jetson sends RTP/JPEG, so ffmpeg needs params
        # self.__receiveThread.start()
        # self.__dispThread.start()

        return True
    
    
    def setFrame(self, data: bytes) -> None:
        """
        Set the frame to be sent out

        Args:
            data (bytes): Frame data
        """
        self.__streamInBuff.push(data)


    def startStreamOut(self, state : bool) -> None:
        """
        Start the out stream to the car

        Args:
            state (bool): True to start streaming, False to stop
        """
        logging.info("Setting stream out to: %s", "ON" if state else "OFF")

        self.__streamOutCanRun = state
        if self.__streamOutCanRun:
            logging.info("Starting stream out thread")
            # Create a new thread each time (threads can only be started once)
            self.__sendThread = Thread(target=self.__streamOutThread, daemon=True)
            self.__sendThread.start()


    def getFrameOut(self) -> None | np.ndarray:
        """
        Handles the reading of the frame to be sent out

        Returns:
            None | np.ndarray: _description_
        """
        if self.__streamOutBuff.empty():
            return None
        
        return self.__streamOutBuff.read()


    def getFrameIn(self) -> None | np.ndarray:
        """Return next available frame. Prefers stereo if present, else mono."""
        if not self.__frameBufferMono.empty():
            return self.__frameBufferMono.read()
        return None
    
    
    def getFrameBufferInStereo(self) -> None | np.ndarray:
        """Return the stereo frame buffer."""
        if not self.__frameBufferStereo.empty():
            return self.__frameBufferStereo.read()
        return None
    
    
    def getFrameBufferInStereoMono(self) -> None | tuple[np.ndarray, int, int, int]:
        """Return the stereo mono frame buffer."""
        if not self.__frameBufferStereoMono.empty():
            return self.__frameBufferStereoMono.read()
        return None


    def __sendFrame(self, data: bytes, frameType: int = 0, frameSide: int = 0, videoName : str = "") -> None:
        ret = False
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
            frameMeta.sequenceID  = self.__sendFrameID
            frameMeta.totalLength = len(data)
            frameMeta.segmentID   = frameSequence
            frameMeta.numSegments = (len(data) + Frame.MAX_SIZE - 1) // Frame.MAX_SIZE
            frameSequence += 1

            bytesToSend = min(Frame.MAX_SIZE, bytesRemaining)
            frameMeta.length = bytesToSend

            # Extract segment
            dataSeg = data[offset : offset + bytesToSend]

            # Copy valid payload bytes
            ctypes.memmove(
                ctypes.addressof(frame.payload),
                dataSeg,
                bytesToSend
            )

            # Zero-fill remainder (CRITICAL FIX)
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

            # self.__streamOutBuff.push(packet)
            self.sendFrameSignal.emit(packet)

        self.__sendFrameID += 1


    def __streamOutThread(self) -> None:
        """
        Handles transmitting of frames over network
        """
        frameSequence : int = 0
        
        # Fire starting signal
        self.startingVideoTransmission.emit()

        videoFileName = os.path.basename(self.__srcFile)
        cap : cv2.VideoCapture
        fileSize: int = os.path.getsize(self.__srcFile)
        bytes_sent: int = 0
        total_estimate: int = fileSize  # start with file size; grow if compressed frames exceed it
        try:
            cap = cv2.VideoCapture(self.__srcFile)
        except Exception as e:
            self.__streamOutCanRun = False
            return

        while self.__streamOutCanRun:
            ret, frame = cap.read()
            if not ret:
                cap.release()
                break

            # Transmit the frame to the RC car
            # Encode frame as JPEG to reduce size significantly
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 35] # Set JPEG quality (0-100)
            result, img_encoded = cv2.imencode('.jpg', frame, encode_param)
            data = img_encoded.tobytes()

            # Get the size of the encoded data
            size = len(data)

            # Send frame immediately (no FPS cap)
            self.__sendFrame(data, videoName=videoFileName)
            bytes_sent += size
            total_estimate = max(total_estimate, bytes_sent)
            # Emit cumulative progress (sent so far, current total estimate)
            self.frameSentSignal.emit(bytes_sent, total_estimate)
        # Final emission to ensure UI hits 100%
        final_total = max(total_estimate, bytes_sent, fileSize)
        self.frameSentSignal.emit(final_total, final_total)
        logging.info("Upload bytes sent=%d, file_size=%d, total_estimate=%d", bytes_sent, fileSize, total_estimate)
        # Fire ending signal
        self.endingVideoTransmission.emit()
        cap.release()
        del cap


    def __streamThread(self) -> None:
        while True:
            # data: bytes = self.__streamSocket.receive_data(65507)
            # if data is None:
            #     continue
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
            frameType  = frameHdr.frameHeader.frameType
            frameSide  = frameHdr.frameHeader.frameSide

            if frameType == 0:
                self.assembleMonoFrame(data, frameHdr)
            elif frameType == 1:
                self.assembleStereoFrame(data, frameHdr, frameSide)
            elif frameType == 2:
                self.assembleStereoMonoFrame(data, frameHdr)


    def assembleMonoFrame(self, data : bytes, header: FrameHeader) -> None:
        """
        Assemble a mono frame from received segments

        Args:
            data (bytes): Frame data
        """
        header_size = ctypes.sizeof(FrameHeader)
        seqID      = header.metadata.sequenceID
        segID      = header.metadata.segmentID
        numSegs    = header.metadata.numSegments
        totalLen   = header.metadata.totalLength
        segLen     = header.metadata.length
        
        # Resize received frame buffer if needed (not strictly required)
        if len(self.__receivedFrameBuff) < totalLen:
            self.__receivedFrameBuff = bytearray(totalLen)

        # If new frame arrives before old is complete → drop old frame
        if (self.__recvFrameIDMono is not None and
            seqID != self.__recvFrameIDMono and
            len(self.__segmentMapMono) > 0 and
            len(self.__segmentMapMono) < self.__expectedSegmentsMono):

            logging.warning(f"Dropping incomplete mono frame ID {self.__recvFrameIDMono}")
            self.__segmentMapMono.clear()

        # If starting a new frame, record segment count + ID
        if self.__recvFrameIDMono != seqID:
            self.__recvFrameIDMono = seqID
            self.__expectedSegmentsMono = numSegs
            self.__segmentMapMono.clear()

        # Store segment
        payload_start = header_size
        payload_end = header_size + segLen
        if payload_end > len(data):
            logging.warning("Mono segment payload truncated (seq=%d seg=%d expected=%d have=%d)", seqID, segID, segLen, len(data) - header_size)
            return
        self.__segmentMapMono[segID] = bytes(data[payload_start:payload_end])

        # Completed frame?
        if len(self.__segmentMapMono) == self.__expectedSegmentsMono:
            jpeg_bytes = bytearray()

            for i in range(self.__expectedSegmentsMono):
                if i not in self.__segmentMapMono:
                    logging.warning(
                        f"Missing segment {i} for mono frame {self.__recvFrameIDMono}"
                    )
                    self.__segmentMapMono.clear()
                    self.__recvFrameIDMono = None
                    return
                jpeg_bytes.extend(self.__segmentMapMono[i])

            npbuf = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(npbuf, cv2.IMREAD_COLOR)

            # Compute and overlay receive FPS
            now = time.time()
            fps_text = ""  # default when unknown
            if self.__lastRecvFrameTime is not None:
                if self.__lastFpsTime is None or now - self.__lastFpsTime >= 1.0:
                    self.__lastFpsTime = now
                    self.__fpsDelta = now - self.__lastRecvFrameTime
                if self.__fpsDelta > 0:
                    fps_val = 1.0 / self.__fpsDelta
                    fps_text = f"FPS: {fps_val:.1f}"
            self.__lastRecvFrameTime = now

            if fps_text:
                cv2.putText(
                    frame,
                    fps_text,
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )

            self.__segmentMapMono.clear()
            self.__recvFrameIDMono = None
            rgbImage = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            self.__frameBufferMono.push(rgbImage)

    
    def assembleStereoFrame(self, data : bytes, header: FrameHeader, side : int) -> None:
        """
        Assemble a stereo frame from received segments

        Args:
            data (bytes): Frame data
            side (int): Side of the stereo frame (e.g., left or right)
        """
        header_size = ctypes.sizeof(FrameHeader)
        seqID    = header.metadata.sequenceID
        segID    = header.metadata.segmentID
        numSegs  = header.metadata.numSegments
        totalLen = header.metadata.totalLength
        segLen   = header.metadata.length

        segmentMap : dict[int, bytes] = self.__segmentMapL if side == 0 else self.__segmentMapR

        # Resize received frame buffer if needed (not strictly required)
        if len(self.__receivedFrameBuff) < totalLen:
            self.__receivedFrameBuff = bytearray(totalLen)

        # If new frame arrives before old is complete → drop old frame
        if (self.__recvFrameIDStereo is not None and
            seqID != self.__recvFrameIDStereo and
            (len(self.__segmentMapL) > 0 or len(self.__segmentMapR) > 0)):

            logging.warning(f"Dropping incomplete stereo frame ID {self.__recvFrameIDStereo}")
            self.__segmentMapL.clear()
            self.__segmentMapR.clear()

        # If starting a new frame, record segment count + ID
        if self.__recvFrameIDStereo != seqID:
            self.__recvFrameIDStereo = seqID
            self.__expectedSegmentsStereo = numSegs
            self.__segmentMapL.clear()
            self.__segmentMapR.clear()

        # Store segment
        payload_start = header_size
        payload_end = header_size + segLen
        if payload_end > len(data):
            logging.warning("Stereo segment payload truncated (seq=%d seg=%d expected=%d have=%d)", seqID, segID, segLen, len(data) - header_size)
            return
        segmentMap[segID] = bytes(data[payload_start:payload_end])

        # Completed frame?
        if (len(self.__segmentMapR) == self.__expectedSegmentsStereo) and \
           (len(self.__segmentMapL) == self.__expectedSegmentsStereo):

            # Build left and right JPEGs separately using the full maps
            left_bytes = bytearray()
            right_bytes = bytearray()

            for i in range(self.__expectedSegmentsStereo):
                if i not in self.__segmentMapL or i not in self.__segmentMapR:
                    logging.warning(
                        f"Missing segment {i} for stereo frame {self.__recvFrameIDStereo}"
                    )
                    self.__segmentMapL.clear()
                    self.__segmentMapR.clear()
                    self.__recvFrameIDStereo = None
                    return
                left_bytes.extend(self.__segmentMapL[i])
                right_bytes.extend(self.__segmentMapR[i])

            left_img = cv2.imdecode(np.frombuffer(left_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
            right_img = cv2.imdecode(np.frombuffer(right_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)

            if left_img is None or right_img is None:
                logging.warning("Failed to decode stereo images for frame %s", self.__recvFrameIDStereo)
                self.__segmentMapL.clear()
                self.__segmentMapR.clear()
                self.__recvFrameIDStereo = None
                return

            # Compute and overlay receive FPS
            now = time.time()
            fps_text = ""  # default when unknown
            if self.__lastRecvFrameTime is not None:
                if self.__lastFpsTime is None or now - self.__lastFpsTime >= 1.0:
                    self.__lastFpsTime = now
                    self.__fpsDelta = now - self.__lastRecvFrameTime
                if self.__fpsDelta > 0:
                    fps_val = 1.0 / self.__fpsDelta
                    fps_text = f"FPS: {fps_val:.1f}"
            self.__lastRecvFrameTime = now

            if fps_text:
                for img in (left_img, right_img):
                    cv2.putText(
                        img,
                        fps_text,
                        (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (0, 255, 0),
                        2,
                        cv2.LINE_AA,
                    )

            # Stitch side-by-side so existing UI can render a single frame
            try:
                stitched = cv2.hconcat([left_img, right_img])
            except Exception:
                stitched = left_img

            self.__segmentMapL.clear()
            self.__segmentMapR.clear()
            self.__recvFrameIDStereo = None
            rgbImage = cv2.cvtColor(stitched, cv2.COLOR_BGR2RGB)

            self.__frameBufferStereo.push(rgbImage)
            
            
    def assembleStereoMonoFrame(self, data : bytes, header: FrameHeader) -> None:
        """
        Assemble a stereo frame from received segments

        Args:
            data (bytes): Frame data
            header (FrameHeader): Frame header information
        """
        header_size = ctypes.sizeof(FrameHeader)
        seqID    = header.metadata.sequenceID
        segID    = header.metadata.segmentID
        numSegs  = header.metadata.numSegments
        totalLen = header.metadata.totalLength
        segLen   = header.metadata.length

        # Resize received frame buffer if needed (not strictly required)
        if len(self.__receivedFrameBuff) < totalLen:
            self.__receivedFrameBuff = bytearray(totalLen)

        # If new frame arrives before old is complete → drop old frame
        if (self.__recvFrameIDStereoMono is not None and
            seqID != self.__recvFrameIDStereoMono and
            (len(self.__segmentMapStereoMono) > 0)):

            logging.warning(f"Dropping incomplete stereo frame ID {self.__recvFrameIDStereoMono}")
            self.__segmentMapStereoMono.clear()

        # If starting a new frame, record segment count + ID
        if self.__recvFrameIDStereoMono != seqID:
            self.__recvFrameIDStereoMono = seqID
            self.__expectedSegmentsStereoMono = numSegs
            self.__segmentMapStereoMono.clear()

        # Store segment
        payload_start = header_size
        payload_end = header_size + segLen
        if payload_end > len(data):
            logging.warning("Stereo segment payload truncated (seq=%d seg=%d expected=%d have=%d)", seqID, segID, segLen, len(data) - header_size)
            return
        self.__segmentMapStereoMono[segID] = bytes(data[payload_start:payload_end])

        # Completed frame?
        if (len(self.__segmentMapStereoMono) == self.__expectedSegmentsStereoMono):

            # Build left and right JPEGs separately using the full maps
            imgBytes = bytearray()

            for i in range(self.__expectedSegmentsStereoMono):
                if i not in self.__segmentMapStereoMono:
                    logging.warning(
                        f"Missing segment {i} for stereo frame {self.__recvFrameIDStereoMono}"
                    )
                    self.__segmentMapStereoMono.clear()
                    self.__recvFrameIDStereoMono = None
                    return
                imgBytes.extend(self.__segmentMapStereoMono[i])
                
            # Extract the gyroscope data before decoding the image
            gyro_data = GyroData.from_buffer_copy(imgBytes)
            logging.debug("Gyro: x=%s y=%s z=%s", gyro_data.gyroX, gyro_data.gyroY, gyro_data.gyroZ)
            imgBytes_ = imgBytes[ctypes.sizeof(GyroData):]
            img = cv2.imdecode(np.frombuffer(imgBytes_, dtype=np.uint8), cv2.IMREAD_COLOR)

            if img is None:
                logging.warning("Failed to decode stereo images for frame %s", self.__recvFrameIDStereoMono)
                self.__segmentMapStereoMono.clear()
                self.__recvFrameIDStereoMono = None
                return

            # Compute and overlay receive FPS
            now = time.time()
            fps_text = ""  # default when unknown
            if self.__lastRecvFrameTime is not None:
                if self.__lastFpsTime is None or now - self.__lastFpsTime >= 1.0:
                    self.__lastFpsTime = now
                    self.__fpsDelta = now - self.__lastRecvFrameTime
                if self.__fpsDelta > 0:
                    fps_val = 1.0 / self.__fpsDelta
                    fps_text = f"FPS: {fps_val:.1f}"
            self.__lastRecvFrameTime = now

            if fps_text:
                cv2.putText(
                    img,
                    fps_text,
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (0, 255, 0),
                        2,
                        cv2.LINE_AA,
                    )

            self.__segmentMapStereoMono.clear()
            self.__recvFrameIDStereoMono = None
            rgbImage = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            data : tuple[np.ndarray, int, int, int] = (rgbImage, gyro_data.gyroX, gyro_data.gyroY, gyro_data.gyroZ) 
            self.__frameBufferStereoMono.push((rgbImage, (gyro_data.gyroX, gyro_data.gyroY, gyro_data.gyroZ)))
        
