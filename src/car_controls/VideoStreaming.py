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
        ("sequenceID",  ctypes.c_uint32),
        ("segmentID",   ctypes.c_uint8),
        ("numSegments", ctypes.c_uint8),
        ("totalLength", ctypes.c_uint32),
        ("length",      ctypes.c_uint16),
    ]

class Frame(ctypes.Structure):
    MAX_SIZE = MAX_UDP_PACKET_SIZE - ctypes.sizeof(FrameMetadata)

    _pack_ = 1
    _fields_ = [
        ("metadata",  FrameMetadata),
        ("payload",     ctypes.c_uint8 * MAX_SIZE),
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
        self.__segmentMap : dict = {}
        self.__recvFrameID = None
        self.__receivedFrameBuff = bytearray()
        self.__lastRecvFrameTime : float | None = None

        self.__receiveThread = Thread(target=self.__streamThread, daemon=True)
        # self.__dispThread    = Thread(target=self.__imShowThread, daemon=True)
        self.__sendThread    = None  # Create on demand
        
        # Circular buffer
        self.__frameBuffer   = CircularBuffer(100)
        self.__streamInBuff  = CircularBuffer(100)
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
        
        self.__receiveThread.start()


    def setVideoSource(self, srcFile:str) -> None:
        """
        Set the video source file for streaming out

        Args:
            srcFile (str): Source file path
        """
        self.__srcFile = srcFile


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
        """
        Handles the reading of the frame

        Returns:
            None | np.ndarray: _description_
        """
        if self.__frameBuffer.empty():
            return None
        
        return self.__frameBuffer.read()


    def __sendFrame(self, data: bytes) -> None:
        ret = False
        frameMeta = FrameMetadata()
        frame = Frame()

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
            self.__sendFrame(data)
            bytes_sent += size
            total_estimate = max(total_estimate, bytes_sent)
            # Emit cumulative progress (sent so far, current total estimate)
            self.frameSentSignal.emit(bytes_sent, total_estimate)
        # Final emission to ensure UI hits 100%
        final_total = max(total_estimate, bytes_sent, fileSize)
        self.frameSentSignal.emit(final_total, total_estimate)
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
            frameSeg = Frame.from_buffer_copy(data)
            seqID      = frameSeg.metadata.sequenceID
            segID      = frameSeg.metadata.segmentID
            numSegs    = frameSeg.metadata.numSegments
            totalLen   = frameSeg.metadata.totalLength
            segLen     = frameSeg.metadata.length

            # Resize received frame buffer if needed (not strictly required)
            if len(self.__receivedFrameBuff) < totalLen:
                self.__receivedFrameBuff = bytearray(totalLen)

            # If new frame arrives before old is complete → drop old frame
            if (self.__recvFrameID is not None and
                seqID != self.__recvFrameID and
                len(self.__segmentMap) > 0 and
                len(self.__segmentMap) < self.__expectedSegments):

                logging.warning(f"Dropping incomplete frame ID {self.__recvFrameID}")
                self.__segmentMap.clear()

            # If starting a new frame, record segment count + ID
            if self.__recvFrameID != seqID:
                self.__recvFrameID = seqID
                self.__expectedSegments = numSegs
                self.__segmentMap.clear()

            # Store segment
            self.__segmentMap[segID] = bytes(frameSeg.payload[:segLen])

            # Completed frame?
            if len(self.__segmentMap) == self.__expectedSegments:
                jpeg_bytes = bytearray()

                for i in range(self.__expectedSegments):
                    if i not in self.__segmentMap:
                        logging.warning(
                            f"Missing segment {i} for frame {self.__recvFrameID}"
                        )
                        self.__segmentMap.clear()
                        self.__recvFrameID = None
                        return
                    jpeg_bytes.extend(self.__segmentMap[i])

                npbuf = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                frame = cv2.imdecode(npbuf, cv2.IMREAD_COLOR)

                # Compute and overlay receive FPS
                now = time.time()
                fps_text = ""  # default when unknown
                if self.__lastRecvFrameTime is not None:
                    delta = now - self.__lastRecvFrameTime
                    if delta > 0:
                        fps_val = 1.0 / delta
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

                self.__segmentMap.clear()
                self.__recvFrameID = None
                
                rgbImage = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                self.__frameBuffer.push(rgbImage)


    def __imShowThread(self):
        logging.info("Video receiving thread started.")
        prev_frame_time = 0
        new_frame_time = 0

        while self.running:
            if self.__frameBuffer.empty():
                continue

            frame = self.__frameBuffer.read()
            if frame is None:
                continue

            new_frame_time = time.time()
            try:
                fps = 1 / (new_frame_time - prev_frame_time)
            except ZeroDivisionError:
                pass
            prev_frame_time = new_frame_time

            # Convert FPS to string and round to an integer for display
            fps_text = str(int(fps))

            # Overlay FPS on the frame
            cv2.putText(frame, f"FPS: {fps_text}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2, cv2.LINE_AA)

        # self.__cap.release()
        cv2.destroyAllWindows()
        logging.info("Video receiving thread stopped.")
