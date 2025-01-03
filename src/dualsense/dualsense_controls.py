from pydualsense import pydualsense, TriggerModes

from threading import Thread, Lock

import time
import ctypes

from network.udp_client import UDPCLient


class DualSense:

    class __dataOut(ctypes.Structure):
        _pack_ = 1  # Pack the struct for no padding
        _fields_ = [
            ("l_joystick_x", ctypes.c_float),
            ("l_joystick_y", ctypes.c_float),
            ("r_joystick_x", ctypes.c_float),
            ("r_joystick_y", ctypes.c_float),
            ("square",     ctypes.c_uint8),
            ("cross",      ctypes.c_uint8),
        ]


    def __init__( self ) -> None:
        self.__ds = pydualsense()

        try:
            self.__ds.init()
        except Exception as e:
            print(e)
            return
        
        self.__udp_client = UDPCLient()
        
        self.__last_joystick_x : int = 0
        self.__last_joystick_y : int = 0

        self.__mutex = Lock()
        
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

        self.__transmission_structure = DualSense.__dataOut()

        # Stat the UDP transmission thread
        self.__controller_thread = Thread(target=self.__transmission_thread)
        self.__controller_thread.start()
        self.__controller_thread.join()


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
        with self.__mutex:
            self.__transmission_structure.l_joystick_x = x
            self.__transmission_structure.l_joystick_y = y


    def __r_joystick( self, x : int, y : int ) -> None:
        """
        Left joystick handler
        """
        with self.__mutex:
            self.__transmission_structure.r_joystick_x = x
            self.__transmission_structure.r_joystick_y = y


    def connect_controller( self, ip_address : str = "" ) -> bool:
        """
        Establioshes the network connection and the connection to the dualsense

        Returns:
            bool: _description_
        """
        ret : bool
        ret = self.__udp_client.open_connection( ip_address )
        if not ret: return False

        try:
            self.__ds.init()
        except Exception as e:
            return False
        
        return True
    

    def __transmission_thread( self ) -> None:
        """
        Main transmission thread
        """
        while True:
            
            self.__mutex.acquire()
            self.__udp_client.send(bytes(self.__transmission_structure))
            self.__mutex.release()
            time.sleep(0.00001)
