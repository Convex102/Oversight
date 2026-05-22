import os
from pathlib import Path

OUTPUT_ROOT = Path(os.getenv("ASGL_OUTPUT_ROOT", "./asgl_outputs")).resolve()
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

DEBUG = os.getenv("ASGL_DEBUG", "0") == "1"
API_KEYS = set([k.strip() for k in os.getenv("ASGL_API_KEYS","").split(",") if k.strip()])
