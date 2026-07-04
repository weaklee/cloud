import logging
import os
import sys
from datetime import datetime

from .config import DATA_DIR

_LOG_DIR = os.path.join(DATA_DIR, "logs")
_initialized = False


def setup_logging():
    global _initialized
    if _initialized:
        return
    _initialized = True

    os.makedirs(_LOG_DIR, exist_ok=True)

    log_file = os.path.join(
        _LOG_DIR, f"ssh_{datetime.now().strftime('%Y%m%d')}.log"
    )

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-5s %(name)s:%(lineno)d — %(message)s",
        datefmt="%H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)
    root.addHandler(ch)

    logging.getLogger("paramiko").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
