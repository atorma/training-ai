"""Byte store behavior shared by the local directory and the Google Cloud Storage bucket."""

import hashlib
import io
import os
from uuid import uuid4

import pytest

from training_ai.attachment_store import GcsByteStore, LocalByteStore, MissingContent

CONTENT = b"%PDF-1.7 fictional week plan"
SHA = hashlib.sha256(CONTENT).hexdigest()


def exercise_store(store):
    store.put(SHA, io.BytesIO(CONTENT), "application/pdf")
    with store.open(SHA) as handle:
        assert handle.read() == CONTENT
    # Identical bytes are written once; a second put succeeds without changing them.
    store.put(SHA, io.BytesIO(CONTENT), "application/pdf")
    with store.open(SHA) as handle:
        assert handle.read() == CONTENT
    with pytest.raises(MissingContent):
        store.open("f" * 64)


def test_local_store_writes_reads_and_deduplicates(tmp_path):
    store = LocalByteStore(tmp_path / "attachments")
    exercise_store(store)
    assert (tmp_path / "attachments" / SHA[:2] / SHA).read_bytes() == CONTENT
    assert list((tmp_path / "attachments" / ".tmp").iterdir()) == []


def test_local_store_keeps_existing_bytes_and_removes_leftover_temporary_files(tmp_path):
    store = LocalByteStore(tmp_path)
    store.put(SHA, io.BytesIO(CONTENT), "application/pdf")
    leftover = tmp_path / ".tmp" / "interrupted-write"
    leftover.write_bytes(b"partial")
    store.remove_temporary_files()
    assert not leftover.exists()
    with store.open(SHA) as handle:
        assert handle.read() == CONTENT


@pytest.mark.integration
def test_gcs_store_against_emulator():
    """Run with `make test-integration`, which starts a local fake-gcs-server."""
    if not os.getenv("STORAGE_EMULATOR_HOST"):
        pytest.skip("STORAGE_EMULATOR_HOST is not set")
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import storage

    client = storage.Client(project="test", credentials=AnonymousCredentials())
    bucket = client.bucket(f"attachments-{uuid4().hex[:8]}")
    bucket.create()
    store = GcsByteStore(bucket.name, "chat/", client=client)
    store.check()
    exercise_store(store)
    blobs = list(client.list_blobs(bucket.name))
    assert [blob.name for blob in blobs] == [f"chat/{SHA[:2]}/{SHA}"]
    assert blobs[0].content_type == "application/pdf"
