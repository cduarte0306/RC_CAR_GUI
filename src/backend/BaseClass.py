import logging
from threading import Thread, Lock, Event


class BaseClass:
    def __init__(self):
        self.__threadPool : dict = {}
        self.threadCanRun : bool = True


    def createThread(self, name : str, callback, autoStart : bool = True) -> None:
        if self.__threadPool.get(name) is not None:
            logging.warning("Thread with name %s already exists", name)
            return

        thread : Thread = Thread(target=callback, daemon=True)
        self.__threadPool[name] = thread
        
        if autoStart:
            if not self.__threadPool[name].is_alive():
                self.__threadPool[name].start()

