import os, json, uuid
from datetime import datetime
from typing import Any


class IOManager:
    """Per-tenant IO, logging and debug snapshot manager."""

    def __init__(self, tenant_id: str = "default", version: str = "v12"):
        self.tenant_id = tenant_id or "default"
        self.version = version
        root = os.getenv("ASGL_OUTPUT_ROOT", "/data/asgl_outputs")
        self.output_root = os.path.join(root, self.tenant_id)
        self.log_dir = os.path.join(self.output_root, "logs")
        self.debug_dir = os.path.join(self.output_root, "debug")
        self.artifact_dir = os.path.join(self.output_root, "artifacts")
        self.adapter_dir = os.path.join(self.output_root, "adapters")
        for d in (self.output_root, self.log_dir, self.debug_dir, self.artifact_dir, self.adapter_dir):
            os.makedirs(d, exist_ok=True)

    def log(self, msg: str, level: str = "INFO") -> None:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{self.version}] [{self.tenant_id}] [{level}] {msg}\n"
        try:
            with open(os.path.join(self.log_dir, f"{self.version}.log"), "a") as f:
                f.write(line)
        except Exception:
            # Fallback to stdout if filesystem unavailable
            print(line, flush=True)

    def save_debug(self, obj: Any, prefix: str = "debug") -> str:
        path = os.path.join(self.debug_dir, f"{prefix}_{uuid.uuid4().hex}.json")
        try:
            with open(path, "w") as f:
                json.dump(obj, f, indent=2, default=str)
        except Exception as e:
            self.log(f"Failed to save debug snapshot: {e}", level="WARN")
        return path

    def adapter_path(self) -> str:
        return os.path.join(self.adapter_dir, "adapters.json")
