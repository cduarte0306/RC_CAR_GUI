from pydualsense import pydualsense, TriggerModes

from threading import Thread, Lock, Event
import queue

import time
import ctypes
import asyncio
import logging

from network.udp_client import UDP
from enum import Enum, auto

from utils.utilities import Toolbox, CircularBuffer, Signal

import numpy


class commands(Enum):
        CMD_NOOP = 0x00
        CMD_FWD_DIR = auto()
        CMD_STEER = auto()

class val_type_t(ctypes.Union):
    _fields_ = [
        ("i", ctypes.c_int),
        ("f32", ctypes.c_float),
        ("u32", ctypes.c_uint),
        ("u16", ctypes.c_uint16),
        ("u8", ctypes.c_uint8),
    ]

class payload(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("command_id", ctypes.c_uint8),  # Command ID
        ("data", val_type_t),         # Directly reference __val_type_t
    ]

class dataOut(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("l_joystick_x", ctypes.c_float),
        ("l_joystick_y", ctypes.c_float),
        ("r_joystick_x", ctypes.c_float),
        ("r_joystick_y", ctypes.c_float),
        ("square", ctypes.c_uint8),
        ("cross", ctypes.c_uint8),
    ]

class clientReq(ctypes.Structure):
    _pack_ = 1  # Pack the struct for no padding
    _fields_ = [
        ("sequence_id", ctypes.c_uint32),
        ("msg_length",  ctypes.c_uint16),
        ("payload",     payload),        # Directly reference __payload
        ("crc32", ctypes.c_uint32),
    ]

class clientReply(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("data",  val_type_t),
        ("state", ctypes.c_bool)
    ]
    

class Controller:
    PORT = 65000

    def __init__( self ) -> None:
        self.__ds = pydualsense()
        
        self.__msg_id : int = 0
        self.__udp_client = UDP(Controller.PORT)
        
        self.__last_joystick_x : int = 0
        self.__last_joystick_y : int = 0

        self.__triangleToggled = False

        self.__mutex = Lock()
        self.__queue = queue.Queue()
        
        # Shutdown event for graceful thread termination
        self.__shutdown_event = Event()
        
        # Signals
        self.controllerFound = Signal()
        self.deviceFound     = Signal()
        self.trianglePressed = Signal()

        # Connect the signals
        self.__udp_client.deviceFound.connect(lambda ip: self.deviceFound.emit(ip))

        self.__transmission_structure = dataOut()

        try:
            self.__ds.init()
        except Exception as e:
            logging.error("Failed to initialize the controller: %s", e)
            return

        # Set touchpad color to red
        self.__ds.light.setColorI(255, 255, 0)

        # Set left trigger to resistance mode
        self.__ds.triggerL.setMode(TriggerModes.Rigid)
        self.__ds.triggerL.setForce(0, 255)

        # Subscribe to cross button press event
        self.__ds.cross_pressed          += self.__cross_pressed
        self.__ds.square_pressed         += self.__square_pressed
        self.__ds.left_joystick_changed  += self.__l_joystick
        self.__ds.right_joystick_changed += self.__r_joystick
        self.__ds.triangle_pressed       += self.__trianglePressed


        # Start the UDP transmission thread (do NOT join here - it's infinite)
        self.__controller_thread = Thread(target=self.__transmission_thread, daemon=False)
        self.__controller_thread.start()


    def __cross_pressed( self, state : bool ) -> None:
        """
        Cross pressing handler

        Args:
            state (bool): Button press state
        """
        with self.__mutex:
            self.__transmission_structure.cross = state

        
    def __square_pressed( self, state : bool ) -> None:
        """
        Square pressing handler

        Args:
            state (bool): Button press state
        """
        with self.__mutex:
            self.__transmission_structure.cross = state


    def __l_joystick( self, x : int, y : int ) -> None:
        """
        Left joystick handler
        """
        msg_1  : payload    = payload()
        msg_2  : payload    = payload()
        data : val_type_t = val_type_t()

        if abs( self.__last_joystick_x - x ) > 2:
            self.__last_joystick_x = x

        data.i = x

        msg_1.command_id = commands.CMD_STEER.value
        msg_1.data       = data

        self.__queue.put(msg_1)
        data.i = y
        msg_2.command_id = commands.CMD_FWD_DIR.value
        msg_2.data       = data
        self.__queue.put(msg_2)


    def __trianglePressed(self, input:bool) -> None:
        """
        Triangle toggled handler

        Args:
            input (bool): button state
        """
        if not input: return
        self.__triangleToggled = not self.__triangleToggled
        logging.info("Triangle pressed: %s", "ON" if self.__triangleToggled else "OFF")

        if self.__triangleToggled:
            self.trianglePressed.emit(True)
        else:
            self.trianglePressed.emit(False)
        

    def __r_joystick( self, x : int, y : int ) -> None:
        """
        Left joystick handler
        """
        with self.__mutex:
            self.__transmission_structure.r_joystick_x = x
            self.__transmission_structure.r_joystick_y = y


    def connect_controller( self ) -> bool:
        """
        Establioshes the network connection and the connection to the dualsense

        Returns:
            bool: _description_
        """
        try:
            self.__ds.init()
        except Exception as e:
            return False
        
        return True
    

    def shutdown(self) -> None:
        """
        Gracefully shutdown all threads and clean up resources
        """
        logging.info("Shutting down controller threads...")
        self.__shutdown_event.set()
        
        # Shutdown UDP client to close socket
        self.__udp_client.shutdown()
        
        # Wait for transmission thread to finish
        if self.__controller_thread.is_alive():
            self.__controller_thread.join(timeout=5)
            if self.__controller_thread.is_alive():
                logging.warning("Transmission thread did not finish within timeout")
        
        logging.info("Controller threads shutdown complete")


    def __transmission_thread(self) -> None:
        """
        Main transmission thread
        """
        packet: clientReq = clientReq()
        thread_pool: list = []
        buffer : CircularBuffer = CircularBuffer(100)

        def data_reception_thread(pool: list, buff:CircularBuffer) -> None:
            """
            Main reception thread - receives data until shutdown is signaled
            """
            _buffer : CircularBuffer = buff
            reply = clientReply()
            
            while not self.__shutdown_event.is_set():
                if _buffer.empty():
                    continue

                data: bytes = self.__udp_client.receive_data()
                if data is None:
                    continue

                ctypes.memmove(
                    ctypes.addressof(reply),  # Destination address
                    data,                     # Source data
                    ctypes.sizeof(reply)      # Number of bytes to copy
                )
                

        recv_thread = Thread(
            target=data_reception_thread,
            daemon=True,
            args=(thread_pool, buffer,)
        )
        recv_thread.start()

        while not self.__shutdown_event.is_set():
            try:
                req = self.__queue.get(timeout=1)
                self.__queue.task_done()
            except queue.Empty:
                continue

            packet.sequence_id = self.__msg_id
            packet.payload     = req
            packet.msg_length  = ctypes.sizeof(payload)

            payload_bytes      = ctypes.string_at(ctypes.addressof(packet.payload),
                                                ctypes.sizeof(packet.payload))
            self.__msg_id     += 1
            self.__udp_client.send(
                ctypes.string_at(ctypes.addressof(packet), ctypes.sizeof(packet))
            )

            # Send this request to the circular buffer so that the receive thread knows what should be expected
            buffer.push(req)
        recv_thread.join()