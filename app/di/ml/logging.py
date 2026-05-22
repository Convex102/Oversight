from __future__ import annotations

import logging
import os


def get_logger(name: str) -> logging.Logger:
    level = os.getenv("ASGL_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO))
    return logging.getLogger(name)
