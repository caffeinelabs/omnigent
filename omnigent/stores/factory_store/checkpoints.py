"""Checkpoints' part of the factory store (``factory_checkpoints``).

:class:`CheckpointStore` is the contract every factory store implements;
:class:`InMemoryCheckpointStore` is the in-memory half
(``sqlalchemy_checkpoints`` holds the SQL half). The server's checkpoints
module is the only writer: one row per decision the factory makes (an agent's
own, or the server's on an agent's ask or a proposal) and per change of
direction a person makes. A person's answers and applied proposals are not
copied here; they read as checkpoints through an adapter.

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
from omnigent.db.enum_codecs import FACTORY_CHECKPOINT_BY, FACTORY_CHECKPOINT_STATUS
from omnigent.entities.factory import FactoryCheckpoint

if TYPE_CHECKING:
    import threading

CHECKPOINT_FIELDS: Final = frozenset(
    {
        "status",
        "decided_by_kind",
        "decided_by",
        "decided_by_name",
        "node_ref",
        "project_slug",
        "options",
        "choice_id",
        "choice_label",
        "direction",
        "why",
        "confidence",
        "decision_class",
        "policy",
        "effects",
        "facts",
        "decision_id",
        "proposal_id",
        "overrides",
        "changed_by",
        "asked_by_session_id",
        "dedupe_key",
    }
)
"""What :meth:`CheckpointStore.create_checkpoint` and ``update_checkpoint`` accept."""
CHECKPOINT_CLIPS: Final = {
    "question": 1000,
    "decided_by": 128,
    "decided_by_name": 120,
    "node_ref": 96,
    "project_slug": 64,
    "choice_id": 16,
    "choice_label": 200,
    "direction": 2000,
    "why": 300,
    "decision_class": 32,
    "overrides": 32,
    "changed_by": 32,
    "dedupe_key": 64,
}
"""Column widths: longer text is clipped, as every factory store does."""
CHECKPOINT_IDS: Final = ("decision_id", "proposal_id", "asked_by_session_id")
"""Row-id columns, stored as bare hex."""


class CheckpointConflict(Exception):
    """An update whose ``expect_status`` is not the stored one; carries the stored row."""

    def __init__(self, checkpoint: FactoryCheckpoint) -> None:
        super().__init__(f"{checkpoint.ref} is {checkpoint.status}")
        self.checkpoint = checkpoint


def _bare(value: str) -> str:
    return uuid_to_bytes(value).hex()


def check_checkpoint_fields(fields: Mapping[str, Any], where: str) -> None:
    """:raises TypeError: On an unknown field; :raises ValueError: on an unknown code."""
    unknown = sorted(set(fields) - CHECKPOINT_FIELDS)
    if unknown:
        raise TypeError(f"{where}() got unexpected field(s): {', '.join(unknown)}")
    status = fields.get("status")
    if status is not None and status not in FACTORY_CHECKPOINT_STATUS:
        raise ValueError(f"unknown checkpoint status value: {status!r}")
    by = fields.get("decided_by_kind")
    if by is not None and by not in FACTORY_CHECKPOINT_BY:
        raise ValueError(f"unknown checkpoint decided_by_kind value: {by!r}")


def clip_checkpoint(fields: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of *fields* with its text clipped to the column widths and ids made bare."""
    out = dict(fields)
    for key, limit in CHECKPOINT_CLIPS.items():
        value = out.get(key)
        if isinstance(value, str) and len(value) > limit:
            out[key] = value[: limit - 1] + "…"
    for key in CHECKPOINT_IDS:
        value = out.get(key)
        if isinstance(value, str) and value:
            out[key] = _bare(value)
    return out


class CheckpointStore(ABC):
    """The contract for ``factory_checkpoints``."""

    @abstractmethod
    def create_checkpoint(
        self, factory_id: str, *, question: str, **fields: Any
    ) -> FactoryCheckpoint:
        """
        Store a new checkpoint as ``K-<next number>`` of the factory.

        :param fields: Any of :data:`CHECKPOINT_FIELDS` (``status`` defaults to ``decided``).
        """

    @abstractmethod
    def get_checkpoint(self, checkpoint_id: str) -> FactoryCheckpoint | None:
        """:returns: The checkpoint with row id *checkpoint_id*."""

    @abstractmethod
    def get_checkpoint_by_number(self, factory_id: str, number: int) -> FactoryCheckpoint | None:
        """:returns: ``K-<number>`` of the factory."""

    @abstractmethod
    def list_checkpoints(
        self,
        factory_id: str,
        *,
        since: int | None = None,
        decision_id: str | None = None,
        proposal_id: str | None = None,
        overrides: str | None = None,
        dedupe_key: str | None = None,
        limit: int = 200,
    ) -> builtins.list[FactoryCheckpoint]:
        """
        :param since: Only rows created at or after this (epoch seconds).
        :param decision_id: Only rows about this decision.
        :param proposal_id: Only rows about this proposal.
        :param overrides: Only the rows that change this checkpoint (``K-3``, ``D-49``).
        :param dedupe_key: Only rows with this key.
        :returns: The factory's checkpoints, newest first.
        """

    @abstractmethod
    def update_checkpoint(
        self,
        checkpoint_id: str,
        *,
        expect_status: str | Sequence[str] | None = None,
        **fields: Any,
    ) -> FactoryCheckpoint | None:
        """
        Update a checkpoint.

        :param expect_status: Refuse unless the stored status is (one of) this.
        :returns: The stored row, or ``None`` when there is none.
        :raises CheckpointConflict: When *expect_status* does not match.
        """


# ── In memory ──────────────────────────────────────────────────


def _statuses(value: str | Sequence[str] | None) -> list[str] | None:
    if value is None:
        return None
    return [value] if isinstance(value, str) else list(value)


class InMemoryCheckpointStore:
    """The in-memory half of :class:`CheckpointStore` (mixed into the in-memory factory store)."""

    _lock: threading.RLock

    def _checkpoint_rows(self) -> dict[tuple[int, str], FactoryCheckpoint]:
        rows = getattr(self, "_checkpoints", None)
        if rows is None:
            with self._lock:
                rows = getattr(self, "_checkpoints", None)
                if rows is None:
                    rows = {}
                    self._checkpoints = rows
        return rows

    def create_checkpoint(
        self, factory_id: str, *, question: str, **fields: Any
    ) -> FactoryCheckpoint:
        check_checkpoint_fields(fields, "create_checkpoint")
        values = clip_checkpoint(copy.deepcopy({"question": question, **fields}))
        fid = _bare(factory_id)
        now = int(time.time())
        ws = current_workspace_id()
        with self._lock:
            rows = self._checkpoint_rows()
            numbers = [r.number for k, r in rows.items() if k[0] == ws and r.factory_id == fid]
            row = FactoryCheckpoint(
                id=uuid.uuid4().hex,
                factory_id=fid,
                number=max(numbers, default=0) + 1,
                status=values.pop("status", None) or "decided",
                decided_by_kind=values.pop("decided_by_kind", None) or "server",
                options=values.pop("options", None) or [],
                policy=values.pop("policy", None) or {},
                effects=values.pop("effects", None) or [],
                facts=values.pop("facts", None) or {},
                created_at=now,
                updated_at=now,
                workspace_id=ws,
                **values,
            )
            rows[(ws, row.id)] = row
            return copy.deepcopy(row)

    def get_checkpoint(self, checkpoint_id: str) -> FactoryCheckpoint | None:
        try:
            key = (current_workspace_id(), _bare(checkpoint_id))
        except ValueError:
            return None
        with self._lock:
            return copy.deepcopy(self._checkpoint_rows().get(key))

    def get_checkpoint_by_number(self, factory_id: str, number: int) -> FactoryCheckpoint | None:
        fid = _bare(factory_id)
        ws = current_workspace_id()
        with self._lock:
            for key, row in self._checkpoint_rows().items():
                if key[0] == ws and row.factory_id == fid and row.number == number:
                    return copy.deepcopy(row)
        return None

    def list_checkpoints(
        self,
        factory_id: str,
        *,
        since: int | None = None,
        decision_id: str | None = None,
        proposal_id: str | None = None,
        overrides: str | None = None,
        dedupe_key: str | None = None,
        limit: int = 200,
    ) -> builtins.list[FactoryCheckpoint]:
        fid = _bare(factory_id)
        ws = current_workspace_id()
        decision = _bare(decision_id) if decision_id else None
        proposal = _bare(proposal_id) if proposal_id else None
        with self._lock:
            rows = [
                copy.deepcopy(row)
                for key, row in self._checkpoint_rows().items()
                if key[0] == ws
                and row.factory_id == fid
                and (since is None or row.created_at >= since)
                and (decision is None or row.decision_id == decision)
                and (proposal is None or row.proposal_id == proposal)
                and (overrides is None or row.overrides == overrides)
                and (dedupe_key is None or row.dedupe_key == dedupe_key)
            ]
        rows.sort(key=lambda r: r.number, reverse=True)
        return rows[:limit]

    def update_checkpoint(
        self,
        checkpoint_id: str,
        *,
        expect_status: str | Sequence[str] | None = None,
        **fields: Any,
    ) -> FactoryCheckpoint | None:
        check_checkpoint_fields(fields, "update_checkpoint")
        values = clip_checkpoint(copy.deepcopy(fields))
        expected = _statuses(expect_status)
        with self._lock:
            rows = self._checkpoint_rows()
            key = (current_workspace_id(), _bare(checkpoint_id))
            current = rows.get(key)
            if current is None:
                return None
            if expected is not None and current.status not in expected:
                raise CheckpointConflict(copy.deepcopy(current))
            row = replace(current, **values, updated_at=int(time.time()))
            rows[key] = row
            return copy.deepcopy(row)


__all__ = [
    "CHECKPOINT_CLIPS",
    "CHECKPOINT_FIELDS",
    "CheckpointConflict",
    "CheckpointStore",
    "InMemoryCheckpointStore",
    "check_checkpoint_fields",
    "clip_checkpoint",
]
