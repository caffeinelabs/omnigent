"""The artifact location's scheme picks the store: S3, UC Volumes or a local directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from moto import mock_aws

from omnigent.cli import _create_artifact_store
from omnigent.stores.artifact_store import ArtifactStore, create_artifact_store
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.artifact_store.s3 import S3ArtifactStore


@pytest.fixture()
def aws_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        yield


@pytest.mark.parametrize("create", [create_artifact_store, _create_artifact_store])
def test_s3_uri_opens_the_s3_store(aws_env: None, create: Any) -> None:
    store = create("s3://factory-artifacts/omnigent")
    assert isinstance(store, S3ArtifactStore)
    assert store.storage_location == "s3://factory-artifacts/omnigent"


@pytest.mark.parametrize("create", [create_artifact_store, _create_artifact_store])
def test_a_path_opens_the_local_store(tmp_path: Path, create: Any) -> None:
    store = create(str(tmp_path / "artifacts"))
    assert isinstance(store, LocalArtifactStore)
    assert (tmp_path / "artifacts").is_dir()


def test_a_bucketless_s3_uri_is_refused(aws_env: None) -> None:
    with pytest.raises(ValueError, match="bucket"):
        create_artifact_store("s3://")


class _Whole(ArtifactStore):
    """A store with only the abstract methods: read_range slices the whole blob."""

    def __init__(self) -> None:
        super().__init__("memory://")
        self.blobs = {"k": b"0123456789"}

    def put(self, key: str, data: bytes) -> None:
        self.blobs[key] = data

    def get(self, key: str) -> bytes:
        return self.blobs[key]

    def delete(self, key: str) -> None:
        self.blobs.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.blobs


def test_default_read_range_slices_the_blob() -> None:
    assert _Whole().read_range("k", 3, 6) == b"3456"
    with pytest.raises(KeyError):
        _Whole().read_range("missing", 0, 1)
