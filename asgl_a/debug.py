import json
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict

def save_debug(debug_dir: Path, payload: Dict[str, Any], prefix: str="asgl_a") -> Path:
    debug_dir.mkdir(parents=True, exist_ok=True)
    p = debug_dir / f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex}.json"
    try:
        p.write_text(json.dumps(payload, indent=2, default=str))
    except Exception:
        pass
    return p
