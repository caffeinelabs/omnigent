"""Artifact store — blob storage for agent bundles and user files."""

from abc import ABC, abstractmethod

S3_SCHEME = "s3://"
DBFS_VOLUMES_PREFIX = "dbfs:/Volumes/"


class ArtifactStore(ABC):
    """
    Blob storage for binary artifacts (agent bundles, user-uploaded
    files). Keyed by a unique string identifier. Metadata (filename,
    size, etc.) is managed separately by the route layer.
    """

    def __init__(self, storage_location: str) -> None:
        """
        Initialize the store with a backend-specific storage location.

        The interpretation of *storage_location* depends on the
        concrete implementation -- e.g. a filesystem path for local
        storage, an S3 URI for cloud storage, etc.

        :param storage_location: Backend-specific root location,
            e.g. ``"/data/artifacts"`` for local filesystem or
            ``"s3://my-bucket/artifacts"`` for S3.
        """
        self._storage_location = storage_location

    @property
    def storage_location(self) -> str:
        """
        The backend-specific storage location (path, URI, etc.).

        :returns: The storage location string passed at init.
        """
        return self._storage_location

    @abstractmethod
    def put(self, key: str, data: bytes) -> None:
        """
        Store a blob under the given key. Overwrites if the key
        already exists.

        :param key: Forward-slash-separated artifact key,
            e.g. ``"agents/agent_abc123/bundle.tar.gz"``.
        :param data: Raw bytes to store.
        """
        ...

    @abstractmethod
    def get(self, key: str) -> bytes:
        """
        Retrieve a blob by key.

        :param key: Forward-slash-separated artifact key,
            e.g. ``"agents/agent_abc123/bundle.tar.gz"``.
        :returns: The raw bytes of the stored blob.
        :raises KeyError: If no blob exists for the given key.
        """
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """
        Remove a blob. No-op if the key does not exist.

        :param key: Forward-slash-separated artifact key,
            e.g. ``"agents/agent_abc123/bundle.tar.gz"``.
        """
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        """
        Check whether a blob exists for the given key.

        :param key: Forward-slash-separated artifact key,
            e.g. ``"agents/agent_abc123/bundle.tar.gz"``.
        :returns: ``True`` if a blob exists, ``False`` otherwise.
        """
        ...

    def read_range(self, key: str, start: int, end: int) -> bytes:
        """
        Read the inclusive byte span ``start``..``end`` of a blob.

        This default reads the whole blob and slices it; stores that can
        read a span directly (a file seek, an S3 ranged GET) override it.

        :param key: Forward-slash-separated artifact key,
            e.g. ``"agents/agent_abc123/bundle.tar.gz"``.
        :param start: First byte offset, e.g. ``0``.
        :param end: Last byte offset, inclusive, e.g. ``1023``.
        :returns: The span's bytes; fewer when the blob ends before *end*.
        :raises KeyError: If no blob exists for the given key.
        """
        return self.get(key)[start : end + 1]


def create_artifact_store(location: str) -> ArtifactStore:
    """
    Open the artifact store a location names, chosen by its scheme.

    ``s3://bucket[/prefix]`` opens an
    :class:`~omnigent.stores.artifact_store.s3.S3ArtifactStore` (needs
    ``boto3``; credentials from the standard AWS chain), ``dbfs:/Volumes/...``
    a :class:`~omnigent.stores.artifact_store.databricks_volumes.DatabricksVolumesArtifactStore`
    (needs ``databricks-sdk``), and anything else a
    :class:`~omnigent.stores.artifact_store.local.LocalArtifactStore` directory.

    :param location: e.g. ``"s3://my-bucket/artifacts"`` or ``"/data/artifacts"``.
    :returns: The opened store.
    :raises ImportError: When the backend's optional package is missing.
    :raises ValueError: When an ``s3://`` URI has no bucket.
    """
    if location.startswith(S3_SCHEME):
        from omnigent.stores.artifact_store.s3 import S3ArtifactStore

        return S3ArtifactStore(location)
    if location.startswith(DBFS_VOLUMES_PREFIX):
        from omnigent.stores.artifact_store.databricks_volumes import (
            DatabricksVolumesArtifactStore,
        )

        return DatabricksVolumesArtifactStore(location)

    from omnigent.stores.artifact_store.local import LocalArtifactStore

    return LocalArtifactStore(location)
