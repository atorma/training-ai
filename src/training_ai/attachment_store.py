"""Content-addressed byte stores for chat attachments: local disk or Google Cloud Storage.

Both are synchronous; async callers run them in a worker thread.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Protocol
from uuid import uuid4

if TYPE_CHECKING:
    from training_ai.config import Config

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
GCS_TIMEOUT = 30  # Seconds per request.
GCS_RETRY_TIMEOUT = 30.0  # Total seconds of retries before reporting the store unavailable.


class MissingContent(Exception):
    """The store has no bytes under this hash."""


class StoreUnavailable(Exception):
    """The store could not be reached or refused access."""


class ByteStore(Protocol):
    def put(self, sha256: str, source: BinaryIO, media_type: str) -> None: ...

    def open(self, sha256: str) -> BinaryIO: ...


def object_key(sha256: str) -> str:
    return f"{sha256[:2]}/{sha256}"


class LocalByteStore:
    """Files under `root/<sha[0:2]>/<sha>`, written through a temporary file and renamed.

    Plain files keep no media type; the metadata row is what serves it.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.work = self.root / ".tmp"

    def remove_temporary_files(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def put(self, sha256: str, source: BinaryIO, media_type: str) -> None:
        target = self.root / object_key(sha256)
        if target.exists():
            return
        try:
            self.work.mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.work / str(uuid4())
            try:
                source.seek(0)
                with temporary.open("wb") as handle:
                    shutil.copyfileobj(source, handle, CHUNK_SIZE)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            raise StoreUnavailable("Attachment storage is not writable.") from exc

    def open(self, sha256: str) -> BinaryIO:
        try:
            return (self.root / object_key(sha256)).open("rb")
        except FileNotFoundError as exc:
            raise MissingContent(sha256) from exc
        except OSError as exc:
            raise StoreUnavailable("Attachment storage is not readable.") from exc


class GcsByteStore:
    """Objects in a private bucket, created only if absent so identical uploads are harmless.

    Identical bytes have one detected media type, so the type set by the first upload holds.
    """

    def __init__(self, bucket: str, prefix: str = "", client=None):
        from google.cloud import storage
        from google.cloud.storage.retry import DEFAULT_RETRY

        self.client = client or storage.Client()
        self.bucket = self.client.bucket(bucket)
        self.prefix = prefix.strip("/")
        # Writes are idempotent through the precondition, so they retry like reads.
        self.retry = DEFAULT_RETRY.with_timeout(GCS_RETRY_TIMEOUT)

    def _name(self, sha256: str) -> str:
        key = object_key(sha256)
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, sha256: str, source: BinaryIO, media_type: str) -> None:
        from google.api_core.exceptions import PreconditionFailed

        blob = self.bucket.blob(self._name(sha256))
        try:
            source.seek(0)
            blob.upload_from_file(
                source,
                content_type=media_type,
                if_generation_match=0,
                timeout=GCS_TIMEOUT,
                retry=self.retry,
            )
        except PreconditionFailed:
            return  # Already stored; content-addressed bytes are identical.
        except Exception as exc:
            raise StoreUnavailable("Attachment storage is unavailable.") from exc

    def open(self, sha256: str) -> BinaryIO:
        from google.api_core.exceptions import NotFound

        try:
            data = self.bucket.blob(self._name(sha256)).download_as_bytes(
                timeout=GCS_TIMEOUT, retry=self.retry
            )
        except NotFound as exc:
            raise MissingContent(sha256) from exc
        except Exception as exc:
            raise StoreUnavailable("Attachment storage is unavailable.") from exc
        return io.BytesIO(data)

    def check(self) -> None:
        """One metadata read that needs only object read access."""
        try:
            blob = self.bucket.blob(self._name("0" * 64))
            blob.exists(timeout=10, retry=None)  # pyright: ignore[reportArgumentType]
        except Exception as exc:
            raise StoreUnavailable("Attachment storage is unavailable.") from exc


def create_byte_store(config: Config) -> ByteStore:
    """Choose the store in one place; an unreachable bucket only logs a warning."""
    local = LocalByteStore(config.attachment_directory())
    local.remove_temporary_files()
    if not config.attachment_bucket:
        return local
    try:
        store = GcsByteStore(config.attachment_bucket, config.attachment_prefix)
    except Exception:
        logger.warning("Attachment bucket client could not be created; retrying on first use.")
        return _UnavailableUntilReachable(config.attachment_bucket, config.attachment_prefix)
    try:
        store.check()
    except StoreUnavailable:
        logger.warning("Attachment bucket is unreachable; attachments fail until it is reachable.")
    return store


class _UnavailableUntilReachable:
    """Retries creating the bucket client on use, e.g. when credentials were not ready yet."""

    def __init__(self, bucket: str, prefix: str):
        self.bucket = bucket
        self.prefix = prefix
        self.store: GcsByteStore | None = None

    def _get(self) -> GcsByteStore:
        if self.store is None:
            try:
                self.store = GcsByteStore(self.bucket, self.prefix)
            except Exception as exc:
                raise StoreUnavailable("Attachment storage is unavailable.") from exc
        return self.store

    def put(self, sha256: str, source: BinaryIO, media_type: str) -> None:
        self._get().put(sha256, source, media_type)

    def open(self, sha256: str) -> BinaryIO:
        return self._get().open(sha256)
