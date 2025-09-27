from pydualsense import pydualsense, TriggerModes

from threading import Thread, Lock
import queue

import time
import ctypes
import asyncio
import logging

from network.udp_client import UDPCLient
from enum import Enum, auto

from utils.utilities import Toolbox


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
    
class Controller:

    def __init__( self ) -> None:
        self.__ds = pydualsense()

        try:
            self.__ds.init()
        except Exception as e:
            logging.error("Failed to initialize the controller: %s", e)
            return
        
        self.__msg_id : int = 0
        
        self.__udp_client = UDPCLient()
        
        self.__last_joystick_x : int = 0
        self.__last_joystick_y : int = 0

        self.__mutex = Lock()
        self.__queue = queue.Queue()
        
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

        self.__transmission_structure = dataOut()

        # Stat the UDP transmission thread
        self.__controller_thread = Thread(target=self.__transmission_thread)
        self.__controller_thread.start()

        self.__controller_thread.join()
        self.__reception_thread.join()


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
        msg  : payload    = payload()
        data : val_type_t = val_type_t()

        if abs( self.__last_joystick_x - x ) > 2:
            self.__last_joystick_x = x

        data.i = x

        msg.command_id = commands.CMD_STEER.value
        msg.data       = data

        self.__queue.put(msg)


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
    

    def __transmission_thread(self) -> None:
        """
        Main transmission thread
        """
        packet: clientReq = clientReq()
        thread_pool: list = []
        receive_thread : Thread

        def data_reception_thread(pool: list) -> None:
            """
            Main reception thread
            """
            data: bytes = self.__udp_client.receive_data()
            pool.pop()

        while True:
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

            if len(thread_pool) > 99:
                continue

            recv_thread = Thread(
                target=data_reception_thread,
                daemon=True,
                args=(thread_pool,)  # ✅ fixed
            )

            thread_pool.append(recv_thread)
            recv_thread.start()

