"""
Storage Abstraction Layer for EDVEXA Platform.
Provides unified, interchangeable storage interfaces for:
- Local Filesystem (development, tests, Windows local)
- Cloudflare R2 S3-Compatible Object Storage (production persistent uploads)
"""

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger("edvexa.storage")


class StorageBackend(ABC):
    """Abstract base class for file and object storage operations."""

    @abstractmethod
    def save(self, key: str, content: bytes, content_type: str = "text/csv") -> str:
        """Store bytes content under the given key. Returns the storage identifier."""
        pass

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Retrieve stored file bytes by key. Raises FileNotFoundError if missing."""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete object by key. Returns True if deleted or did not exist, False on failure."""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if an object exists under the given key."""
        pass

    @abstractmethod
    def list_keys(self, prefix: str = "") -> list[str]:
        """List all object keys matching the prefix."""
        pass

    @abstractmethod
    def clear_all(self) -> int:
        """Clear all stored upload artifacts. Returns number of removed items."""
        pass


class LocalStorageBackend(StorageBackend):
    """Local filesystem storage using settings.UPLOADS_DIR."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = (base_dir or settings.UPLOADS_DIR).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_key_path(self, key: str) -> Path:
        """Resolve path and verify against path-traversal attacks."""
        sanitized_key = Path(key).name  # Strips any directory traversals
        target = (self.base_dir / sanitized_key).resolve()
        if not target.is_relative_to(self.base_dir):
            raise ValueError(f"Path traversal detected: {key}")
        return target

    def save(self, key: str, content: bytes, content_type: str = "text/csv") -> str:
        target = self._resolve_key_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as f:
            f.write(content)
        logger.debug(f"[LocalStorage] Saved {len(content)} bytes to {target}")
        return target.name

    def get(self, key: str) -> bytes:
        target = self._resolve_key_path(key)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"File '{key}' not found in local storage.")
        with open(target, "rb") as f:
            return f.read()

    def delete(self, key: str) -> bool:
        try:
            target = self._resolve_key_path(key)
            if target.exists() and target.is_file():
                target.unlink()
                logger.debug(f"[LocalStorage] Deleted {target}")
                return True
            return True
        except Exception as e:
            logger.error(f"[LocalStorage] Error deleting {key}: {e}")
            return False

    def exists(self, key: str) -> bool:
        try:
            target = self._resolve_key_path(key)
            return target.exists() and target.is_file()
        except Exception:
            return False

    def list_keys(self, prefix: str = "") -> list[str]:
        if not self.base_dir.exists():
            return []
        keys = []
        for item in self.base_dir.iterdir():
            if item.is_file() and item.name.startswith(prefix):
                keys.append(item.name)
        return keys

    def clear_all(self) -> int:
        removed = 0
        if not self.base_dir.exists():
            return 0
        for item in self.base_dir.iterdir():
            if item.is_file():
                try:
                    item.unlink()
                    removed += 1
                except Exception as e:
                    logger.warning(f"[LocalStorage] Could not delete {item.name}: {e}")
        return removed


class R2StorageBackend(StorageBackend):
    """Cloudflare R2 S3-compatible persistent object storage."""

    def __init__(
        self,
        account_id: str | None = None,
        bucket_name: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        endpoint_url: str | None = None,
    ):
        import boto3
        from botocore.config import Config

        self.bucket_name = bucket_name or settings.R2_BUCKET_NAME
        self.account_id = account_id or settings.R2_ACCOUNT_ID
        self.access_key = access_key_id or settings.R2_ACCESS_KEY_ID
        self.secret_key = secret_access_key or settings.R2_SECRET_ACCESS_KEY
        self.endpoint_url = endpoint_url or settings.effective_r2_endpoint

        if not self.bucket_name:
            raise ValueError("R2_BUCKET_NAME must be configured for R2StorageBackend.")
        if not self.endpoint_url:
            raise ValueError("R2 endpoint URL or R2_ACCOUNT_ID must be provided.")

        self.s3_client = boto3.client(
            service_name="s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name="auto",  # Cloudflare R2 standard region
            config=Config(signature_version="s3v4"),
        )
        logger.info(f"[R2Storage] Initialized for bucket '{self.bucket_name}' at {self.endpoint_url}")

    def _sanitize_key(self, key: str) -> str:
        """Strip directory traversals and normalize object key."""
        clean = Path(key).name
        # Store uploads under 'uploads/' namespace in R2
        return f"uploads/{clean}"

    def save(self, key: str, content: bytes, content_type: str = "text/csv") -> str:
        obj_key = self._sanitize_key(key)
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=obj_key,
                Body=content,
                ContentType=content_type,
            )
            logger.info(f"[R2Storage] Uploaded {len(content)} bytes to s3://{self.bucket_name}/{obj_key}")
            # Also keep a local cache in UPLOADS_DIR if writable for fast local access
            try:
                local_cache = settings.UPLOADS_DIR / Path(key).name
                with open(local_cache, "wb") as f:
                    f.write(content)
            except Exception:
                pass
            return Path(key).name
        except Exception as e:
            logger.error(f"[R2Storage] Failed to upload {obj_key}: {e}")
            raise RuntimeError(f"Cloudflare R2 storage write failure: {e}")

    def get(self, key: str) -> bytes:
        # Check local cache first
        local_cache = settings.UPLOADS_DIR / Path(key).name
        if local_cache.exists() and local_cache.is_file():
            try:
                with open(local_cache, "rb") as f:
                    return f.read()
            except Exception:
                pass

        obj_key = self._sanitize_key(key)
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=obj_key)
            data = response["Body"].read()
            # Cache locally
            try:
                with open(local_cache, "wb") as f:
                    f.write(data)
            except Exception:
                pass
            return data
        except self.s3_client.exceptions.NoSuchKey:
            raise FileNotFoundError(f"Object '{obj_key}' not found in R2 bucket '{self.bucket_name}'.")
        except Exception as e:
            logger.error(f"[R2Storage] Failed to read {obj_key}: {e}")
            raise FileNotFoundError(f"Error reading '{obj_key}' from R2: {e}")

    def delete(self, key: str) -> bool:
        obj_key = self._sanitize_key(key)
        # Also clean local cache
        try:
            local_cache = settings.UPLOADS_DIR / Path(key).name
            if local_cache.exists():
                local_cache.unlink()
        except Exception:
            pass

        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=obj_key)
            logger.info(f"[R2Storage] Deleted s3://{self.bucket_name}/{obj_key}")
            return True
        except Exception as e:
            logger.error(f"[R2Storage] Failed to delete {obj_key}: {e}")
            return False

    def exists(self, key: str) -> bool:
        obj_key = self._sanitize_key(key)
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=obj_key)
            return True
        except Exception:
            return False

    def list_keys(self, prefix: str = "") -> list[str]:
        full_prefix = f"uploads/{prefix}" if prefix else "uploads/"
        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            keys = []
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=full_prefix):
                for obj in page.get("Contents", []):
                    # Strip 'uploads/' prefix for consistency
                    k = obj["Key"]
                    if k.startswith("uploads/"):
                        keys.append(k[len("uploads/"):])
                    else:
                        keys.append(k)
            return keys
        except Exception as e:
            logger.error(f"[R2Storage] Error listing keys: {e}")
            return []

    def clear_all(self) -> int:
        removed = 0
        try:
            keys = self.list_keys()
            for k in keys:
                if self.delete(k):
                    removed += 1
            return removed
        except Exception as e:
            logger.error(f"[R2Storage] Error clearing all objects: {e}")
            return removed


_storage_instance: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """
    Factory function returning the active storage backend.
    Uses Cloudflare R2 if STORAGE_BACKEND == 'r2' and credentials are configured;
    otherwise safely defaults to LocalStorageBackend.
    """
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance

    if settings.STORAGE_BACKEND == "r2":
        if settings.is_r2_configured:
            try:
                _storage_instance = R2StorageBackend()
                return _storage_instance
            except Exception as e:
                logger.error(f"Failed to initialize R2 storage backend: {e}. Falling back to local storage.")
        else:
            logger.warning("STORAGE_BACKEND='r2' requested but R2 credentials are incomplete. Falling back to local storage.")

    _storage_instance = LocalStorageBackend()
    return _storage_instance


def reset_storage_instance() -> None:
    """Reset cached storage singleton for testing purposes."""
    global _storage_instance
    _storage_instance = None


def generate_artifact_key(job_id: int, file_hash: str) -> str:
    """Generate canonical secure artifact key for an upload run."""
    return f"job_{job_id}_{file_hash[:12]}.csv"


def save_upload_artifact(job_id: int, file_hash: str, content: bytes) -> str:
    """Save upload content using the active storage backend."""
    storage = get_storage()
    key = generate_artifact_key(job_id, file_hash)
    return storage.save(key, content, content_type="text/csv")


def get_upload_artifact(artifact_name: str) -> bytes:
    """Retrieve raw upload content using the active storage backend."""
    storage = get_storage()
    return storage.get(artifact_name)


def delete_upload_artifact(artifact_name: str) -> bool:
    """Delete upload artifact using active storage backend."""
    storage = get_storage()
    return storage.delete(artifact_name)


def clear_all_upload_artifacts() -> int:
    """Clear all upload artifacts from active storage backend."""
    storage = get_storage()
    return storage.clear_all()
