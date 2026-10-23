import logging
import os
from datetime import datetime
from pathlib import Path


def setup_logger(name: str = __name__) -> logging.Logger:
    logs_dir = Path("./logs")
    logs_dir.mkdir(exist_ok=True)
    dt = datetime.strftime(datetime.now(), "%m_%d_%y %H_%M_%S ")

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # File handler
    fh = logging.FileHandler(logs_dir / f"{dt}applyJobs.log", mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    ffmt = logging.Formatter('%(asctime)s::%(name)s::%(levelname)s::%(message)s')
    fh.setFormatter(ffmt)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    cfmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S')
    ch.setFormatter(cfmt)

    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(ch)

    # Avoid double logging via root
    logger.propagate = False

    return logger
