import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logger(name: str = "asgla_compare", out_dir: str = ".", debug: bool = False) -> logging.Logger:
    os.makedirs(os.path.join(out_dir, "logs"), exist_ok=True)
    log_path = os.path.join(out_dir, "logs", "asgla_compare.log")

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    # Prevent duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    fh = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG if debug else logging.INFO)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG if debug else logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.propagate = False
    logger.info("Logger initialised. debug=%s, log_path=%s", debug, log_path)
    return logger
