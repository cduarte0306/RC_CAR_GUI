from pydualsense import pydualsense, TriggerModes
from pydualsense.enums import ConnectionType

from threading import Event

import time
import logging
import types

from utils.utilities import Signal
from .BaseClass import BaseClass
from .CommandBus import CommandBus, Command, commands


class Controller(BaseClass):
    controllerDetected = Signal(str)  # Emitted when a controller is detected
    controllerBatteryLevel = Signal(int)  # Emitted when battery level changes
    controllerDisconnected = Signal()  # Emitted when controller is disconnected
    
    def __init__( self, CommandBus: CommandBus ) -> None:
        super().__init__()  # Initialize parent class

        self.__ds = self.__create_dualsense()

        self.__last_joystick_x : int = 0
        self.__last_joystick_y : int = 0

        self.__controllerConnected = False

        self.__event_loop_started = False

        # Shutdown event for graceful thread termination
        self.__shutdownEvent = Event()
        self.__shutdownEvent.clear()

        # Command dispatch
        self.__bus = CommandBus

        # Signals
        self.controllerFound = Signal()  # Emitted when a controller is found
        self.deviceFound     = Signal()  # Emitted when a device is found
        self.trianglePressed = Signal()  # Emitted when triangle button is pressed

        # Connect the signals
        # self.__udpClient.deviceFound.connect(lambda ip: self.deviceFound.emit(ip))

        # Start the controller detection thread
        self.createThread("controller-discover", self.__controllerConnectionManager)


    def __controllerConnectionManager(self) -> None:
        """
        Controller scanning thread
        """
        while not self.__shutdownEvent.is_set():
            # Connected mode
            if self.__controllerConnected:
                level : int = self.__ds.battery.Level
                self.controllerBatteryLevel.emit(level)
                
                if level >= 60:
                    self.__ds.light.setColorI(0, 255, 0)  # Green
                elif level < 60 and level >= 20:
                    self.__ds.light.setColorI(255, 255, 0)  # Yellow
                else:
                    self.__ds.light.setColorI(255, 0, 0)  # Red
                
                time.sleep(0.1)
                if not self.__ds.connected:
                    logging.warning("Controller disconnected")
                    self.__mark_disconnected()
                    self.__controllerConnected = False
                continue

            try:
                self.__ds.init()

                # Set left trigger to resistance mode
                self.__ds.triggerL.setMode(TriggerModes.Rigid)
                self.__ds.triggerL.setForce(0, 255)

                # Subscribe to cross button press event
                self.__ds.cross_pressed          += self.__cross_pressed
                self.__ds.square_pressed         += self.__square_pressed
                self.__ds.left_joystick_changed  += self.__l_joystick
                self.__ds.right_joystick_changed += self.__r_joystick
                
                self.__controllerConnected = True
                logging.info("Controller initialized")
                
                connectionType : str = "USB" if self.__ds.determineConnectionType() == ConnectionType.USB else "Bluetooth"
                logging.info("Controller connected via %s", connectionType)
                self.controllerDetected.emit(connectionType)
            except Exception as e:
                self.__controllerConnected = False
            time.sleep(0.1)


    def __create_dualsense(self) -> pydualsense:
        """
        Create a DualSense instance with a non-blocking connection-type probe
        so reconnects don't hang on device reads.
        """
        ds = pydualsense()

        def _determine_connection_type(self) -> ConnectionType:
            # Try a few short reads so init doesn't block forever after reconnects.
            for _ in range(10):
                try:
                    dummy_report = self.device.read(100, timeout_ms=200)
                except Exception:
                    return ConnectionType.ERROR

                if dummy_report:
                    input_report_length = len(dummy_report)
                    if input_report_length == 64:
                        self.input_report_length = 64
                        self.output_report_length = 64
                        return ConnectionType.USB
                    elif input_report_length == 78:
                        self.input_report_length = 78
                        self.output_report_length = 78
                        return ConnectionType.BT

                time.sleep(0.05)

            return ConnectionType.ERROR

        if hasattr(ds, "determineConnectionType"):
            ds.determineConnectionType = types.MethodType(_determine_connection_type, ds)

        return ds


    def shutdown(self) -> None:
        """
        Gracefully shutdown all threads and clean up resources
        """
        logging.info("Shutting down controller threads...")
        self.__shutdownEvent.set()
        
        # Clear the controller connection flag
        self.__controllerConnected = False
        
        # Stop event loop and close device
        try:
            self.__ds.close()
        except Exception:
            pass

        logging.info("Controller threads shutdown complete")


    def StartComms(self) -> None:
        # Command bus owns transmission; just ensure it is running
        self.__bus.start()


    def __cross_pressed( self, state : bool ) -> None:
        """
        Cross pressing handler

        Args:
            state (bool): Button press state
        """
        # Placeholder: route button presses if needed
        _ = state


    def __square_pressed( self, state : bool ) -> None:
        """
        Square pressing handler

        Args:
            state (bool): Button press state
        """
        _ = state


    def __l_joystick( self, x : int, y : int ) -> None:
        """
        Left joystick handler
        """
        if abs(self.__last_joystick_x - x) > 2:
            self.__last_joystick_x = x

        self.__bus.submit(Command(commands.CMD_STEER.value, x))
        self.__bus.submit(Command(commands.CMD_FWD_DIR.value, y))


    def __r_joystick( self, x : int, y : int ) -> None:
        """
        Left joystick handler
        """
        # Extend to emit right-joystick commands if needed
        _ = (x, y)


    def dataReceived(self, data : bytes) -> None:
        if data == None:
            logging.error("Invalid data received")


    def __mark_disconnected(self) -> None:
        """Reset flags and close device so discovery can restart after loss."""
        was_connected = self.__controllerConnected
        try:
            self.__ds.close()
        except Exception as e:
            logging.error("Error closing controller device: %s", e)
        
        # Recreate the device wrapper to avoid stale handles after reconnects.
        self.__ds = self.__create_dualsense()
        
        logging.info("Controller marked disconnected")
        self.__controllerConnected = False
        self.__event_loop_started = False
        if was_connected:
            self.controllerDisconnected.emit()
