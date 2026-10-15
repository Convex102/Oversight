from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import boto3

from app.config import settings


def _parse_s3_uri(uri: str) -> Tuple[str, str]:
    # s3://bucket/key...
    u = uri.replace("s3://", "", 1)
    parts = u.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("Invalid s3 uri")
    return parts[0], parts[1]


@dataclass
class PresignedUrl:
    method: str
    url: str
    expires_in: int


class Storage:
    """Minimal storage abstraction for local filesystem or S3.

    - For local backend: returns file:// URIs.
    - For s3 backend: returns s3:// URIs.
    """

    def __init__(self):
        self.backend = (settings.STORAGE_BACKEND or "local").lower().strip()
        self.local_root = Path(settings.LOCAL_STORAGE_PATH).resolve()
        self.s3 = None
        self.bucket = None

        if self.backend == "s3":
            self.s3 = boto3.client("s3", region_name=settings.AWS_REGION or None)
            self.bucket = settings.S3_BUCKET
            if not self.bucket:
                raise RuntimeError("S3_BUCKET must be set when STORAGE_BACKEND=s3")

    def _local_path(self, key: str) -> Path:
        # prevent path traversal
        key = key.lstrip("/").replace("..", "")
        p = (self.local_root / key).resolve()
        try:
            p.relative_to(self.local_root)
        except ValueError:
            raise ValueError("Invalid storage key")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        if self.backend == "s3":
            assert self.s3 is not None and self.bucket is not None
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
            return f"s3://{self.bucket}/{key}"

        path = self._local_path(key)
        path.write_bytes(data)
        return "file://" + str(path)

    def get(self, uri: str) -> bytes:
        if uri.startswith("file://"):
            path = Path(uri.replace("file://", "", 1))
            return path.read_bytes()

        if uri.startswith("s3://"):
            assert self.s3 is not None
            bucket, key = _parse_s3_uri(uri)
            obj = self.s3.get_object(Bucket=bucket, Key=key)
            return obj["Body"].read()

        # allow passing plain storage key for local backend
        if self.backend == "local":
            path = self._local_path(uri)
            if not path.exists():
                raise FileNotFoundError("Artifact not found")
            return path.read_bytes()

        raise ValueError("Unsupported storage URI")

    def delete(self, uri: str) -> bool:
        """Delete one stored object. Only objects inside this storage (the local root or the configured bucket) can be deleted.
        Returns True when something was removed, False when it did not exist."""
        if uri.startswith("s3://"):
            if self.backend != "s3":
                raise ValueError("Refusing to delete an s3 object on a local backend")
            assert self.s3 is not None and self.bucket is not None
            bucket, key = _parse_s3_uri(uri)
            if bucket != self.bucket:
                raise ValueError("Refusing to delete outside the configured bucket")
            self.s3.delete_object(Bucket=bucket, Key=key)
            return True
        if uri.startswith("file://"):
            path = Path(uri.replace("file://", "", 1)).resolve()
            try:
                path.relative_to(self.local_root)
            except ValueError:
                raise ValueError("Refusing to delete outside the storage root")
            if not path.is_file():
                return False
            path.unlink()
            return True
        raise ValueError("Unsupported storage URI")

    def presigned_get(self, uri: str, expires_in: int = 3600) -> Optional[PresignedUrl]:
        if not uri.startswith("s3://"):
            return None
        assert self.s3 is not None
        bucket, key = _parse_s3_uri(uri)
        url = self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=int(expires_in),
        )
        return PresignedUrl(method="GET", url=url, expires_in=int(expires_in))

    def list_uris(self, prefix: str) -> list[str]:
        """List objects under a prefix.

        - Local backend: walks LOCAL_STORAGE_PATH/prefix recursively.
        - S3 backend: uses ListObjectsV2 with the given prefix.

        Returns a list of URIs (file://... or s3://...).
        """
        prefix = (prefix or "").lstrip("/")

        if self.backend == "s3":
            assert self.s3 is not None and self.bucket is not None
            uris: list[str] = []
            continuation = None
            while True:
                kwargs = {"Bucket": self.bucket, "Prefix": prefix}
                if continuation:
                    kwargs["ContinuationToken"] = continuation
                resp = self.s3.list_objects_v2(**kwargs)
                for obj in resp.get("Contents", []) or []:
                    key = obj.get("Key")
                    if key:
                        uris.append(f"s3://{self.bucket}/{key}")
                if resp.get("IsTruncated"):
                    continuation = resp.get("NextContinuationToken")
                else:
                    break
            return uris

        # local
        base = self._local_path(prefix)
        if base.is_file():
            return ["file://" + str(base)]
        if not base.exists():
            return []
        uris: list[str] = []
        for p in base.rglob("*"):
            if p.is_file():
                uris.append("file://" + str(p))
        return uris

    def presigned_put(self, key: str, expires_in: int = 3600, content_type: str = "application/octet-stream") -> Optional[PresignedUrl]:
        if self.backend != "s3":
            return None
        assert self.s3 is not None and self.bucket is not None
        url = self.s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=int(expires_in),
        )
        return PresignedUrl(method="PUT", url=url, expires_in=int(expires_in))

    # ── key based helpers used by services (telemetry, active learning, vector store ...) ──
    def read(self, key: str) -> Optional[bytes]:
        """Return the bytes stored under a key, or None when it does not exist."""
        if self.backend == "s3":
            assert self.s3 is not None and self.bucket is not None
            try:
                return self.s3.get_object(Bucket=self.bucket, Key=key.lstrip("/"))["Body"].read()
            except Exception as exc:  # NoSuchKey and friends
                if "NoSuchKey" in type(exc).__name__ or "NoSuchKey" in str(exc) or "404" in str(exc):
                    return None
                raise
        path = self._local_path(key)
        return path.read_bytes() if path.exists() else None

    def write(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        return self.put(key, data, content_type)

    def read_json(self, key: str):
        import json
        raw = self.read(key)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def write_json(self, key: str, obj) -> str:
        import json
        return self.put(key, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json")

    def append_jsonl(self, key: str, obj) -> str:
        import json
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        return self.put(key, (self.read(key) or b"") + line, "application/x-ndjson")


storage = Storage()
