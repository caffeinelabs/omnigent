"""Initiatives' part of the factory store (``factory_initiatives``).

:class:`InitiativeStore` is the contract every factory store implements;
:class:`InMemoryInitiativeStore` is the in-memory half
(``sqlalchemy_initiatives`` holds the SQL half). The server's initiatives
module is the only writer: a proposal that answers an ask bigger than one
project creates a row, later waves extend it, acceptance runs are appended,
and a person closes or reopens it. Every update compares and sets
``version``, so two writers (an acceptance report and a base move) never
lose each other's change.

This module imports nothing from the package ``__init__`` (which imports it).
"""

from __future__ import annotations

import builtins
import copy
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final

from omnigent.db.db_models import current_workspace_id, uuid_to_bytes
from omnigent.db.enum_codecs import FACTORY_INITIATIVE_STATUS
from omnigent.entities.factory import FactoryInitiative

if TYPE_CHECKING:
    import threading

INITIATIVE_FIELDS: Final = frozenset(
    {
        "title",
        "ask",
        "status",
        "requested_by",
        "ask_ref",
        "thread",
        "outcome",
        "plan",
        "runs",
        "created_by",
        "closed_at",
        "closed_by",
    }
)
"""What :meth:`InitiativeStore.create_initiative` and ``update_initiative`` accept."""
INITIATIVE_CLIPS: Final = {
    "title": 120,
    "ask_ref": 64,
    "thread": 128,
    "outcome": 280,
    "created_by": 128,
    "closed_by": 128,
}
"""Column widths: longer text is clipped, as every factory store does."""


class InitiativeVersionConflict(Exception):
    """An update whose ``expect_version`` is not the stored one; carries the stored row."""

    def __init__(self, initiative: FactoryInitiative) -> None:
        super().__init__(f"{initiative.ref} changed (version {initiative.version})")
        self.initiative = initiative


def check_initiative_fields(fields: Mapping[str, Any], where: str) -> None:
    """:raises TypeError: On an unknown field; :raises ValueError: on an unknown status."""
    unknown = sorted(set(fields) - INITIATIVE_FIELDS)
    if unknown:
        raise TypeError(f"{where}() got unexpected field(s): {', '.join(unknown)}")
    status = fields.get("status")
    if status is not None and status not in FACTORY_INITIATIVE_STATUS:
        raise ValueError(f"unknown initiative status value: {status!r}")


def clip_initiative(fields: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of *fields* with its text clipped to the column widths."""
    out = dict(fields)
    for key, limit in INITIATIVE_CLIPS.items():
        value = out.get(key)
        if isinstance(value, str) and len(value) > limit:
            out[key] = value[: limit - 1] + "…"
    return out


class InitiativeStore(ABC):
    """The contract for ``factory_initiatives``."""

    @abstractmethod
    def create_initiative(
        self, factory_id: str, *, title: str, ask: str, **fields: Any
    ) -> FactoryInitiative:
        """
        Store a new initiative as ``I-<next number>`` of the factory.

        :param fields: Any of :data:`INITIATIVE_FIELDS` (``status`` defaults to ``open``).
        """

    @abstractmethod
    def get_initiative(self, initiative_id: str) -> FactoryInitiative | None:
        """:returns: The initiative with row id *initiative_id*."""

    @abstractmethod
    def get_initiative_by_number(self, factory_id: str, number: int) -> FactoryInitiative | None:
        """:returns: ``I-<number>`` of the factory."""

    @abstractmethod
    def list_initiatives(
        self,
        factory_id: str,
        *,
        status: str | Sequence[str] | None = None,
        limit: int = 100,
    ) -> builtins.list[FactoryInitiative]:
        """:returns: The factory's initiatives, newest first."""

    @abstractmethod
    def update_initiative(
        self, initiative_id: str, *, expect_version: int | None = None, **fields: Any
    ) -> FactoryInitiative | None:
        """
        Update an initiative and bump its version.

        :param expect_version: Refuse unless the stored version is this one.
        :returns: The stored row, or ``None`` when there is none.
        :raises InitiativeVersionConflict: When *expect_version* is stale.
        """


# ── In memory ──────────────────────────────────────────────────


def _bare(value: str) -> str:
    return uuid_to_bytes(value).hex()


class InMemoryInitiativeStore:
    """The in-memory half of :class:`InitiativeStore` (mixed into the in-memory factory store)."""

    _lock: threading.RLock

    def _initiative_rows(self) -> dict[tuple[int, str], FactoryInitiative]:
        rows = getattr(self, "_initiatives", None)
        if rows is None:
            with self._lock:
                rows = getattr(self, "_initiatives", None)
                if rows is None:
                    rows = {}
                    self._initiatives = rows
        return rows

    def create_initiative(
        self, factory_id: str, *, title: str, ask: str, **fields: Any
    ) -> FactoryInitiative:
        check_initiative_fields(fields, "create_initiative")
        values = clip_initiative(copy.deepcopy({"title": title, **fields}))
        fid = _bare(factory_id)
        now = int(time.time())
        ws = current_workspace_id()
        with self._lock:
            rows = self._initiative_rows()
            numbers = [r.number for k, r in rows.items() if k[0] == ws and r.factory_id == fid]
            row = FactoryInitiative(
                id=uuid.uuid4().hex,
                factory_id=fid,
                number=max(numbers, default=0) + 1,
                ask=ask,
                status=values.pop("status", None) or "open",
                runs=values.pop("runs", None) or [],
                created_at=now,
                updated_at=now,
                workspace_id=ws,
                **values,
            )
            rows[(ws, row.id)] = row
            return copy.deepcopy(row)

    def get_initiative(self, initiative_id: str) -> FactoryInitiative | None:
        with self._lock:
            row = self._initiative_rows().get((current_workspace_id(), _bare(initiative_id)))
            return copy.deepcopy(row)

    def get_initiative_by_number(self, factory_id: str, number: int) -> FactoryInitiative | None:
        fid = _bare(factory_id)
        ws = current_workspace_id()
        with self._lock:
            for key, row in self._initiative_rows().items():
                if key[0] == ws and row.factory_id == fid and row.number == number:
                    return copy.deepcopy(row)
        return None

    def list_initiatives(
        self,
        factory_id: str,
        *,
        status: str | Sequence[str] | None = None,
        limit: int = 100,
    ) -> builtins.list[FactoryInitiative]:
        fid = _bare(factory_id)
        ws = current_workspace_id()
        statuses = [status] if isinstance(status, str) else (list(status) if status else None)
        with self._lock:
            rows = [
                copy.deepcopy(row)
                for key, row in self._initiative_rows().items()
                if key[0] == ws
                and row.factory_id == fid
                and (statuses is None or row.status in statuses)
            ]
        rows.sort(key=lambda r: r.number, reverse=True)
        return rows[:limit]

    def update_initiative(
        self, initiative_id: str, *, expect_version: int | None = None, **fields: Any
    ) -> FactoryInitiative | None:
        check_initiative_fields(fields, "update_initiative")
        values = clip_initiative(copy.deepcopy(fields))
        if "runs" in values and values["runs"] is None:
            values["runs"] = []
        with self._lock:
            rows = self._initiative_rows()
            key = (current_workspace_id(), _bare(initiative_id))
            current = rows.get(key)
            if current is None:
                return None
            if expect_version is not None and current.version != expect_version:
                raise InitiativeVersionConflict(copy.deepcopy(current))
            row = replace(
                current, **values, version=current.version + 1, updated_at=int(time.time())
            )
            rows[key] = row
            return copy.deepcopy(row)


__all__ = [
    "INITIATIVE_CLIPS",
    "INITIATIVE_FIELDS",
    "InMemoryInitiativeStore",
    "InitiativeStore",
    "InitiativeVersionConflict",
    "check_initiative_fields",
    "clip_initiative",
]
