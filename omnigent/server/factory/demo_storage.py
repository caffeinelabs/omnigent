"""Where a demo's file bytes live, and reading them back.

By default a demo file is a session file of the PL: a ``file_store`` row plus
bytes under its file id in the server's artifact store, so it also shows in
the PL's Files panel. With ``OMNIGENT_FACTORY_ARTIFACT_LOCATION`` set (e.g.
``s3://bucket/factory``) new demo files go to that store instead, under
``demos/<factory id>/<demo id>/<file id>-<filename>``, and the demo row is
their only record. Such an entry carries its ``store`` location and ``key``,
so a file is read from where it was written whatever the setting is now; an
entry without them is a session file.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.db.utils import generate_file_id
from omnigent.stores.artifact_store import ArtifactStore, create_artifact_store

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryDemo
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.demos import DemoFileIn

_logger = logging.getLogger(__name__)

FACTORY_ARTIFACT_LOCATION_ENV: Final = "OMNIGENT_FACTORY_ARTIFACT_LOCATION"


def factory_artifact_store_from_env() -> ArtifactStore | None:
    """
    Open the store ``OMNIGENT_FACTORY_ARTIFACT_LOCATION`` names.

    :returns: The store, or ``None`` when the variable is unset or blank (demo
        files then stay session files in the server's artifact store).
    :raises ImportError: When the backend's optional package is missing.
    :raises ValueError: When the location is malformed, e.g. ``"s3://"``.
    """
    location = os.environ.get(FACTORY_ARTIFACT_LOCATION_ENV, "").strip()
    if not location:
        return None
    return create_artifact_store(location)


def demo_object_key(factory_id: str, demo_id: str, file_id: str, filename: str) -> str:
    """
    The factory-store key of one demo file.

    :returns: e.g. ``"demos/<factory id>/<demo id>/<file id>-demo.webm"``.
    """
    return f"demos/{factory_id}/{demo_id}/{file_id}-{filename}"


def file_entry(item: DemoFileIn, file_id: str) -> dict[str, Any]:
    """The demo row's entry for one stored file (without its location)."""
    entry: dict[str, Any] = {
        "file_id": file_id,
        "name": item.name,
        "kind": item.kind,
        "caption": item.caption,
        "content_type": item.content_type,
        "bytes": len(item.content),
        "filename": item.filename,
    }
    if item.step is not None:
        entry["step"] = item.step
    return entry


def store_in_factory_store(
    store: ArtifactStore, *, factory_id: str, demo_id: str, files: Sequence[DemoFileIn]
) -> list[dict[str, Any]]:
    """
    Write a demo's files to the factory store.

    :param store: The store ``OMNIGENT_FACTORY_ARTIFACT_LOCATION`` names.
    :param factory_id: Bare-hex factory id, a key segment.
    :param demo_id: Bare-hex id the demo row will get, a key segment.
    :param files: The checked uploads.
    :returns: The demo's file entries, each with ``store`` and ``key``.
    """
    entries: list[dict[str, Any]] = []
    try:
        for item in files:
            file_id = generate_file_id()
            key = demo_object_key(factory_id, demo_id, file_id, item.filename)
            store.put(key, item.content)
            entries.append(
                {**file_entry(item, file_id), "store": store.storage_location, "key": key}
            )
    except Exception:
        discard_factory_entries(store, entries)
        raise
    return entries


def is_factory_entry(entry: Mapping[str, Any]) -> bool:
    """Whether a demo file entry lives in a factory store (it records ``store``)."""
    return isinstance(entry.get("store"), str) and bool(entry["store"])


def discard_factory_entries(store: ArtifactStore, entries: Sequence[Mapping[str, Any]]) -> None:
    """Best-effort removal of factory-store objects of a demo that was not recorded."""
    for entry in entries:
        key = entry.get("key")
        if not isinstance(key, str):
            continue
        try:
            store.delete(key)
        except Exception:  # noqa: BLE001 - cleanup is best effort
            _logger.warning("factory: could not discard demo object %s", key, exc_info=True)


@dataclass(frozen=True)
class DemoBlob:
    """
    One demo file's bytes and what serving them needs.

    :param store: The artifact store holding the bytes.
    :param key: Its key there, e.g. ``"demos/<fid>/<demo>/<file>-demo.webm"``.
    :param filename: Stored file name; it alone picks the served type.
    :param size: Content length in bytes.
    """

    store: ArtifactStore
    key: str
    filename: str
    size: int

    def read(self, span: tuple[int, int] | None) -> bytes:
        """
        The whole file, or one inclusive span of it.

        :raises KeyError: When the bytes are gone.
        """
        if span is None:
            return self.store.get(self.key)
        return self.store.read_range(self.key, span[0], span[1])


def _store_at(deps: FactoryDeps, location: str) -> ArtifactStore:
    """The store at a recorded location: a configured one, else opened once and kept."""
    for store in (deps.factory_artifact_store, deps.artifact_store):
        if store is not None and store.storage_location == location:
            return store
    store = deps.recorded_artifact_stores.get(location)
    if store is None:
        store = deps.recorded_artifact_stores[location] = create_artifact_store(location)
    return store


def locate_demo_file(
    deps: FactoryDeps, demo: FactoryDemo, entry: Mapping[str, Any]
) -> DemoBlob | None:
    """
    Find where one of a demo's files is stored.

    :param deps: The factory deps (stores).
    :param demo: The demo the file belongs to.
    :param entry: The file's entry in ``demo.files``.
    :returns: The file, or ``None`` when it cannot be found or opened.
    """
    if is_factory_entry(entry):
        key, filename, size = entry.get("key"), entry.get("filename"), entry.get("bytes")
        if not (isinstance(key, str) and isinstance(filename, str) and isinstance(size, int)):
            return None
        try:
            store = _store_at(deps, str(entry["store"]))
        except (ImportError, ValueError):
            _logger.warning("factory: cannot open demo store %s", entry["store"], exc_info=True)
            return None
        return DemoBlob(store=store, key=key, filename=filename, size=size)
    if deps.file_store is None or deps.artifact_store is None:
        return None
    stored = deps.file_store.get(str(entry.get("file_id")), session_id=demo.session_id)
    if stored is None:
        return None
    return DemoBlob(
        store=deps.artifact_store, key=stored.id, filename=stored.filename, size=stored.bytes
    )


__all__ = [
    "FACTORY_ARTIFACT_LOCATION_ENV",
    "DemoBlob",
    "demo_object_key",
    "discard_factory_entries",
    "factory_artifact_store_from_env",
    "file_entry",
    "is_factory_entry",
    "locate_demo_file",
    "store_in_factory_store",
]
