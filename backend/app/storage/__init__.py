"""Pluggable blob storage for uploads + generated XML artefacts.

Backends (``TM_STORAGE_BACKEND``): ``local`` (default, filesystem), ``memory`` (tests),
``s3`` (boto3, prod / MinIO). Keys are tenant-prefixed (``org/{org}/jobs/{job}/...``) so a bucket
policy can isolate tenants. The DB stores only the key, never the bytes.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.core.config import get_settings


class StorageError(Exception):
    pass


class ObjectNotFound(StorageError):
    pass


@runtime_checkable
class Storage(Protocol):
    def put(self, key: str, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class InMemoryStorage:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def put(self, key: str, data: bytes) -> str:
        with self._lock:
            self._data[key] = bytes(data)
        return key

    def get(self, key: str) -> bytes:
        with self._lock:
            if key not in self._data:
                raise ObjectNotFound(key)
            return self._data[key]

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def exists(self, key: str) -> bool:
        with self._lock:
            return key in self._data


class LocalStorage:
    def __init__(self, base_dir: str) -> None:
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # keys are forward-slash separated and tenant-scoped; never absolute
        return self.base.joinpath(*key.split("/"))

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise ObjectNotFound(key)
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Storage:
    def __init__(self, bucket: str, **client_kwargs) -> None:
        import boto3  # optional dependency

        self._s3 = boto3.client("s3", **{k: v for k, v in client_kwargs.items() if v})
        self.bucket = bucket

    def put(self, key: str, data: bytes) -> str:
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def get(self, key: str) -> bytes:
        try:
            return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self._s3.exceptions.NoSuchKey as exc:  # type: ignore[attr-defined]
            raise ObjectNotFound(key) from exc

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        backend = s.storage_backend.lower()
        if backend == "memory":
            _storage = InMemoryStorage()
        elif backend == "s3":
            _storage = S3Storage(
                s.s3_bucket,
                endpoint_url=s.s3_endpoint,
                region_name=s.s3_region,
                aws_access_key_id=s.s3_access_key,
                aws_secret_access_key=s.s3_secret_key,
            )
        else:
            _storage = LocalStorage(s.storage_local_dir)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None


def upload_key(org_id: str, job_id: str) -> str:
    return f"org/{org_id}/jobs/{job_id}/upload.bin"


def artifact_key(org_id: str, job_id: str) -> str:
    return f"org/{org_id}/jobs/{job_id}/artifact.xml"
