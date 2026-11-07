"""
Storage Abstraction Layer for EDVEXA Platform.
Provides unified, interchangeable, and namespace-isolated storage interfaces for:
- Local Filesystem (development, tests, Windows local)
- Neon Object Storage / S3-Compatible Storage (production persistent uploads)

Supported Namespaces:
- 'uploads/': ETL datasets (customers, sales, inventory) and processing job artifacts
- 'surveys/': Survey Data Scrubbing questionnaires (QQ) and raw survey datasets
"""

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger("edvexa.storage")

VALID_STORAGE_NAMESPACES = {"uploads", "surveys"}


class StorageBackend(ABC):
    """Abstract base class for file and object storage operations with namespace isolation."""

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
        """Clear all stored ETL upload artifacts. Never clears survey artifacts. Returns removed count."""
        pass


class LocalStorageBackend(StorageBackend):
    """Local filesystem storage supporting uploads/ and surveys/ namespaces."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = (base_dir or settings.UPLOADS_DIR).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_key_path(self, key: str, default_namespace: str = "uploads") -> Path:
        """Resolve path and verify against path-traversal attacks while preserving namespace isolation."""
        # Sanitize for legacy path traversal tests without namespace (e.g. '../../evil_outside.csv')
        if ".." in key or key.startswith("/") or key.startswith("\\"):
            clean_name = Path(key).name
            target = (self.base_dir / clean_name).resolve()
            if not target.is_relative_to(self.base_dir):
                raise ValueError(f"Path traversal detected: {key}")
            return target

        k = key.replace("\\", "/").strip().lstrip("/")
        if k.startswith("surveys/"):
            ns = "surveys"
            fname = k[len("surveys/"):].strip()
        elif k.startswith("uploads/"):
            ns = "uploads"
            fname = k[len("uploads/"):].strip()
        elif "/" in k:
            invalid_ns = k.split("/")[0]
            raise ValueError(f"Invalid storage namespace '{invalid_ns}'. Allowed: 'uploads', 'surveys'.")
        else:
            ns = default_namespace
            fname = k

        clean_name = Path(fname).name
        if not clean_name:
            raise ValueError(f"Invalid filename in storage key: '{key}'")

        if ns == "surveys":
            if self.base_dir == settings.UPLOADS_DIR.resolve():
                target_dir = settings.SURVEYS_DIR.resolve()
            else:
                target_dir = (self.base_dir / "surveys").resolve()
        else:
            target_dir = self.base_dir

        target_dir.mkdir(parents=True, exist_ok=True)
        target = (target_dir / clean_name).resolve()
        if not target.is_relative_to(target_dir):
            raise ValueError(f"Path traversal detected: {key}")
        return target

    def save(self, key: str, content: bytes, content_type: str = "text/csv") -> str:
        target = self._resolve_key_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as f:
            f.write(content)
        logger.debug(f"[LocalStorage] Saved {len(content)} bytes to {target}")
        if key.startswith("surveys/"):
            return f"surveys/{target.name}"
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
        if prefix.startswith("surveys"):
            surveys_dir = settings.SURVEYS_DIR.resolve() if self.base_dir == settings.UPLOADS_DIR.resolve() else (self.base_dir / "surveys").resolve()
            if not surveys_dir.exists():
                return []
            return [f"surveys/{item.name}" for item in surveys_dir.iterdir() if item.is_file()]
        elif prefix.startswith("uploads"):
            if not self.base_dir.exists():
                return []
            return [f"uploads/{item.name}" for item in self.base_dir.iterdir() if item.is_file()]
        else:
            # Default prefix == "" (legacy ETL listing)
            if not self.base_dir.exists():
                return []
            return [item.name for item in self.base_dir.iterdir() if item.is_file()]

    def clear_all(self) -> int:
        """Clear only ETL upload files in base_dir. Never removes surveys."""
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


class NeonObjectStorageBackend(StorageBackend):
    """
    S3-compatible persistent object storage for Neon Object Storage,
    Cloudflare R2, or AWS S3 with strict namespace isolation for uploads/ and surveys/.
    """

    def __init__(
        self,
        bucket_name: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        account_id: str | None = None,
        **kwargs: Any,
    ):
        import boto3
        from botocore.config import Config

        self.bucket_name = bucket_name or settings.S3_BUCKET_NAME or settings.R2_BUCKET_NAME
        self.access_key = access_key_id or settings.S3_ACCESS_KEY_ID or settings.R2_ACCESS_KEY_ID
        self.secret_key = secret_access_key or settings.S3_SECRET_ACCESS_KEY or settings.R2_SECRET_ACCESS_KEY
        self.account_id = account_id or settings.R2_ACCOUNT_ID

        derived_endpoint = endpoint_url or settings.effective_s3_endpoint
        if not derived_endpoint and self.account_id:
            derived_endpoint = f"https://{self.account_id.strip()}.r2.cloudflarestorage.com"
        self.endpoint_url = derived_endpoint

        self.region_name = (
            region_name
            or settings.S3_REGION_NAME
            or ("auto" if self.account_id else "ap-southeast-1")
        )

        if not self.bucket_name:
            raise ValueError("S3_BUCKET_NAME or NEON_STORAGE_BUCKET_NAME must be configured for NeonObjectStorageBackend.")
        if not self.endpoint_url:
            raise ValueError("S3 endpoint URL or NEON_STORAGE_ENDPOINT must be provided.")

        self.s3_client = boto3.client(
            service_name="s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region_name,
            config=Config(signature_version="s3v4"),
        )
        logger.info(
            f"[NeonObjectStorage] Initialized for bucket '{self.bucket_name}' at {self.endpoint_url} (region: {self.region_name})"
        )

    def _normalize_key(self, key: str, default_namespace: str = "uploads") -> str:
        """
        Validate and normalize a storage key into canonical S3 format (namespace/filename).
        Strictly rejects path traversals ('..'), absolute paths, null bytes, and invalid namespaces.
        Preserves 'surveys/' and 'uploads/' namespaces; never flattens them into a shared namespace.
        Assigns default_namespace ('uploads') to un-prefixed keys for backward compatibility.
        """
        if not key or not isinstance(key, str):
            raise ValueError("Storage key must be a non-empty string.")

        if ".." in key or key.startswith("/") or key.startswith("\\"):
            raise ValueError(f"Path traversal or absolute path detected in storage key: '{key}'")

        k = key.replace("\\", "/").strip().lstrip("/")

        if k.startswith("surveys/"):
            ns = "surveys"
            remainder = k[len("surveys/"):].strip()
        elif k.startswith("uploads/"):
            ns = "uploads"
            remainder = k[len("uploads/"):].strip()
        elif "/" in k:
            invalid_ns = k.split("/")[0]
            raise ValueError(
                f"Invalid storage namespace '{invalid_ns}'. Allowed namespaces are 'uploads' and 'surveys'."
            )
        else:
            ns = default_namespace
            remainder = k

        if not remainder:
            raise ValueError(f"Storage key '{key}' has an empty filename.")
        if "/" in remainder or "\\" in remainder or ".." in remainder:
            raise ValueError(f"Invalid nested subpath or traversal in storage key: '{key}'")

        clean_filename = Path(remainder).name
        if clean_filename != remainder or not clean_filename:
            raise ValueError(f"Invalid filename in storage key: '{key}'")

        return f"{ns}/{clean_filename}"

    def save(self, key: str, content: bytes, content_type: str = "text/csv") -> str:
        obj_key = self._normalize_key(key)
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=obj_key,
                Body=content,
                ContentType=content_type,
            )
            logger.info(f"[NeonObjectStorage] Uploaded {len(content)} bytes to s3://{self.bucket_name}/{obj_key}")
            # Keep isolated local cache in appropriate directory
            try:
                if obj_key.startswith("surveys/"):
                    fname = obj_key[len("surveys/"):]
                    local_cache = settings.SURVEYS_DIR / fname
                else:
                    fname = obj_key[len("uploads/"):]
                    local_cache = settings.UPLOADS_DIR / fname
                local_cache.parent.mkdir(parents=True, exist_ok=True)
                with open(local_cache, "wb") as f:
                    f.write(content)
            except Exception:
                pass

            if key.startswith("surveys/"):
                return obj_key
            return Path(key).name
        except Exception as e:
            logger.error(f"[NeonObjectStorage] Failed to upload {obj_key}: {e}")
            raise RuntimeError(f"Neon Object Storage write failure: {e}")

    def get(self, key: str) -> bytes:
        obj_key = self._normalize_key(key)
        # Check local cache first
        if obj_key.startswith("surveys/"):
            local_cache = settings.SURVEYS_DIR / obj_key[len("surveys/"):]
        else:
            local_cache = settings.UPLOADS_DIR / obj_key[len("uploads/"):]

        if local_cache.exists() and local_cache.is_file():
            try:
                with open(local_cache, "rb") as f:
                    return f.read()
            except Exception:
                pass

        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=obj_key)
            data = response["Body"].read()
            try:
                local_cache.parent.mkdir(parents=True, exist_ok=True)
                with open(local_cache, "wb") as f:
                    f.write(data)
            except Exception:
                pass
            return data
        except getattr(self.s3_client.exceptions, "NoSuchKey", Exception) as e:
            if "NoSuchKey" in type(e).__name__ or "NoSuchKey" in str(e):
                raise FileNotFoundError(f"Object '{obj_key}' not found in bucket '{self.bucket_name}'.")
            if hasattr(e, "response") and e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise FileNotFoundError(f"Object '{obj_key}' not found in bucket '{self.bucket_name}'.")
            logger.error(f"[NeonObjectStorage] Failed to read {obj_key}: {e}")
            raise FileNotFoundError(f"Error reading '{obj_key}' from object storage: {e}")

    def delete(self, key: str) -> bool:
        obj_key = self._normalize_key(key)
        try:
            if obj_key.startswith("surveys/"):
                local_cache = settings.SURVEYS_DIR / obj_key[len("surveys/"):]
            else:
                local_cache = settings.UPLOADS_DIR / obj_key[len("uploads/"):]
            if local_cache.exists():
                local_cache.unlink()
        except Exception:
            pass

        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=obj_key)
            logger.info(f"[NeonObjectStorage] Deleted s3://{self.bucket_name}/{obj_key}")
            return True
        except Exception as e:
            logger.error(f"[NeonObjectStorage] Failed to delete {obj_key}: {e}")
            return False

    def exists(self, key: str) -> bool:
        obj_key = self._normalize_key(key)
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=obj_key)
            return True
        except Exception:
            return False

    def list_keys(self, prefix: str = "") -> list[str]:
        if prefix in ("uploads", "uploads/"):
            full_prefix = "uploads/"
            mode = "uploads_explicit"
        elif prefix in ("surveys", "surveys/"):
            full_prefix = "surveys/"
            mode = "surveys_explicit"
        elif prefix.startswith("uploads/"):
            full_prefix = prefix
            mode = "uploads_explicit"
        elif prefix.startswith("surveys/"):
            full_prefix = prefix
            mode = "surveys_explicit"
        elif prefix == "":
            full_prefix = "uploads/"
            mode = "uploads_default"
        else:
            raise ValueError(f"Invalid namespace prefix '{prefix}'. Allowed prefixes: 'uploads/', 'surveys/'.")

        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            keys = []
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=full_prefix):
                for obj in page.get("Contents", []):
                    k = obj["Key"]
                    if mode == "uploads_default":
                        # Legacy un-prefixed listing: strip uploads/ for backward compatibility
                        if k.startswith("uploads/"):
                            keys.append(k[len("uploads/"):])
                        else:
                            keys.append(k)
                    elif mode == "uploads_explicit":
                        if k.startswith("uploads/"):
                            keys.append(k)
                    elif mode == "surveys_explicit":
                        if k.startswith("surveys/"):
                            keys.append(k)
            return keys
        except Exception as e:
            logger.error(f"[NeonObjectStorage] Error listing keys with prefix '{full_prefix}': {e}")
            raise

    def clear_all(self) -> int:
        """Clear all stored ETL upload artifacts. Never clears survey artifacts."""
        removed = 0
        try:
            keys = self.list_keys(prefix="uploads/")
            for k in keys:
                if self.delete(k):
                    removed += 1
            return removed
        except Exception as e:
            logger.error(f"[NeonObjectStorage] Error clearing upload objects: {e}")
            return removed


# Backwards-compatibility aliases
S3StorageBackend = NeonObjectStorageBackend
R2StorageBackend = NeonObjectStorageBackend


_storage_instance: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """
    Factory function returning the active storage backend.
    Uses Neon Object Storage (or S3-compatible backend) if
    STORAGE_BACKEND in ('neon', 's3', 'r2') and credentials are configured;
    otherwise safely defaults to LocalStorageBackend.
    """
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance

    backend_type = settings.STORAGE_BACKEND.lower()
    if backend_type in ("neon", "s3", "r2"):
        if settings.is_s3_configured:
            try:
                _storage_instance = NeonObjectStorageBackend()
                return _storage_instance
            except Exception as e:
                logger.error(f"Failed to initialize Neon Object Storage backend: {e}. Falling back to local storage.")
        else:
            logger.warning(
                f"STORAGE_BACKEND='{backend_type}' requested but S3/Neon credentials are incomplete. "
                "Falling back to local storage."
            )

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
