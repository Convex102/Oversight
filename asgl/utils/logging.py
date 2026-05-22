import logging, os, sys, json
from datetime import datetime

def setup_logging(name="asgl"):
    level = os.getenv("ASGL_LOG_LEVEL", "INFO").upper()
    debug = os.getenv("ASGL_DEBUG", "0") == "1"
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)

    class JsonFormatter(logging.Formatter):
        def format(self, record):
            payload = {
                "ts": datetime.utcnow().isoformat()+"Z",
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info:
                payload["exc"] = self.formatException(record.exc_info)
            extra = getattr(record, "extra", None)
            if isinstance(extra, dict):
                payload.update(extra)
            return json.dumps(payload)

    h = logging.StreamHandler(sys.stdout)
    h.setLevel(level)
    h.setFormatter(JsonFormatter() if debug else logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(h)
    logger.propagate = False
    return logger
