import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def get_logger(log_path: Path, name: str="asgl_a", level: str="INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(str(log_path), maxBytes=2_000_000, backupCount=5)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    # also stderr
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.propagate = False
    return logger
