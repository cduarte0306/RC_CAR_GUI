from car_controls.controller import Controller
from car_controls.VideoStreaming import VideoStreamer
import logging
import os
import configparser
from datetime import datetime
import signal

from utils.utilities import Emitter, Signal


def init_logger() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read("config/rc-car-viewer-config.ini")

    # Get the directory where run.py is located
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    # Main log file (always app.log inside logs/)
    log_file = os.path.join(log_dir, "app.log")

    logging.basicConfig(
        filename=log_file,
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    # Optional: timestamped extra log file every run
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    extra_file = os.path.join(log_dir, f"app_{timestamp}.log")
    file_handler = logging.FileHandler(extra_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logging.getLogger().addHandler(file_handler)

    # Optional: log to console too
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(console_handler)

    return parser


if __name__ == "__main__":
    parser = init_logger()
    logging.info(
        "RC Car Viewer started. Version: %s.%s.%s",
        parser["version"]["MAJOR"],
        parser["version"]["MINOR"],
        parser["version"]["PATCH"]
    )

    streamer = VideoStreamer(parser["settings"]["video_path"])
    ds = Controller()

    # ds.deviceFound.connect(lambda ip: streamer.startStream(ip))
    ds.deviceFound.connect(lambda ip: streamer.startStream(ip))
    ds.trianglePressed.connect(lambda state: streamer.startStreamOut(state))
    # ds.trianglePressed.connect(lambda state: pass)