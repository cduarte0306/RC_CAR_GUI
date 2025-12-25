from pydualsense import pydualsense, TriggerModes

from threading import Event

import time
import logging

from utils.utilities import Signal
from .BaseClass import BaseClass
from .CommandBus import CommandBus, Command, commands


class Controller(BaseClass):
    def __init__( self, CommandBus: CommandBus ) -> None:
        super().__init__()  # Initialize parent class

        self.__ds = pydualsense()

        self.__last_joystick_x : int = 0
        self.__last_joystick_y : int = 0

        self.__controllerConnected = False

        self.__event_loop_started = False

        # Shutdown event for graceful thread termination
        self.__shutdownEvent = Event()

        # Command dispatch
        self.__bus = CommandBus

        # Signals
        self.controllerFound = Signal()  # Emitted when a controller is found
        self.deviceFound     = Signal()  # Emitted when a device is found
        self.trianglePressed = Signal()  # Emitted when triangle button is pressed

        # Connect the signals
        # self.__udpClient.deviceFound.connect(lambda ip: self.deviceFound.emit(ip))

        # Start the controller detection thread
        self.createThread("controller-discover", self.__findController)


    def __findController(self) -> None:
        """
        Controller scanning thread
        """
        while self.threadCanRun:
            # Find controller
            if self.__controllerConnected:
                time.sleep(1)
                continue

            try:
                self.__ds.init()
                
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
                
                self.__controllerConnected = True
                logging.info("Controller initialized")

                # Start a dedicated listener so pydualsense can dispatch callbacks
                # (callbacks will not fire unless listen()/poll is running)
                if not self.__event_loop_started:
                    self.createThread("controller-events", self.__eventLoop)
                    self.__event_loop_started = True
            except Exception as e:
                self.__controllerConnected = False
            time.sleep(0.1)


    def __eventLoop(self) -> None:
        """Pump pydualsense events so registered callbacks fire."""
        while not self.__shutdownEvent.is_set():
            try:
                if hasattr(self.__ds, "listen"):
                    # listen() blocks until close() is called
                    self.__ds.listen()
                    break
                elif hasattr(self.__ds, "update"):
                    self.__ds.update()
                else:
                    logging.error("pydualsense has no listen/update method; cannot pump events")
                    break
            except Exception as e:
                logging.error("Controller event loop error: %s", e)
                time.sleep(0.2)
        logging.info("Controller event loop stopped")

        # Mark disconnected so the discover thread can try to re-init
        self.__mark_disconnected()


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
        try:
            self.__ds.close()
        except Exception:
            pass

        self.__controllerConnected = False
        self.__event_loop_started = False
