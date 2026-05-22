import logging
from logging.handlers import RotatingFileHandler

def get_logger(name: str, log_file):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5)
        formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s :: %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger
