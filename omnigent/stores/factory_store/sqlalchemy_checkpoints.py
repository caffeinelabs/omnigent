"""The SQL half of :class:`~omnigent.stores.factory_store.checkpoints.CheckpointStore`.

Mixed into :class:`~omnigent.stores.factory_store.sqlalchemy_store.SqlAlchemyFactoryStore`,
whose ``_session`` and ``_write`` it uses. Numbers (``K-<n>``) are taken under
the factory's sequence lock, like proposals, decisions and initiatives.
"""

from __future__ import annotations

import builtins
import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Final, TypeVar

from sqlalchemy import func, select

from omnigent.db.db_models import SqlFactoryCheckpoint, current_workspace_id, uuid_to_bytes
from omnigent.db.enum_codecs import (
    decode_factory_checkpoint_by,
    decode_factory_checkpoint_status,
    encode_factory_checkpoint_by,
    encode_factory_checkpoint_status,
)
from omnigent.entities.factory import FactoryCheckpoint
from omnigent.stores.factory_store.checkpoints import (
    CheckpointConflict,
    check_checkpoint_fields,
    clip_checkpoint,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_T = TypeVar("_T")

_CHECKPOINT_LOCK_NS: Final = 0x636B7074
_JSON: Final = frozenset({"options", "policy", "effects", "facts"})


def _dump(value: Any) -> str | None:
    return None if value is None else json.dumps(value, separators=(",", ":"))


def _load(value: str | None, default: Any) -> Any:
    return default if value is None else json.loads(value)


def _bare(value: str) -> str:
    return uuid_to_bytes(value).hex()


def _columns(fields: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in clip_checkpoint(fields).items():
        if key == "status":
            out[key] = encode_factory_checkpoint_status(value)
        elif key == "decided_by_kind":
            out[key] = encode_factory_checkpoint_by(value)
        elif key in _JSON:
            out[key] = _dump(value)
        else:
            out[key] = value
    return out


def _checkpoint(row: SqlFactoryCheckpoint) -> FactoryCheckpoint:
    return FactoryCheckpoint(
        id=row.id,
        factory_id=row.factory_id,
        number=row.number,
        question=row.question,
        status=decode_factory_checkpoint_status(row.status),
        decided_by_kind=decode_factory_checkpoint_by(row.decided_by_kind),
        decided_by=row.decided_by,
        decided_by_name=row.decided_by_name,
        node_ref=row.node_ref,
        project_slug=row.project_slug,
        options=_load(row.options, []),
        choice_id=row.choice_id,
        choice_label=row.choice_label,
        direction=row.direction,
        why=row.why,
        confidence=row.confidence,
        decision_class=row.decision_class,
        policy=_load(row.policy, {}),
        effects=_load(row.effects, []),
        facts=_load(row.facts, {}),
        decision_id=row.decision_id,
        proposal_id=row.proposal_id,
        overrides=row.overrides,
        changed_by=row.changed_by,
        asked_by_session_id=row.asked_by_session_id,
        dedupe_key=row.dedupe_key,
        created_at=row.created_at,
        updated_at=row.updated_at,
        workspace_id=row.workspace_id,
    )


class SqlCheckpointStore:
    """The SQL half of the checkpoints contract."""

    if TYPE_CHECKING:
        _session: Callable[[str], AbstractContextManager[Session]]

        def _write(
            self,
            operation: str,
            callback: Callable[[Session], _T],
            *,
            sequence: tuple[int, str] | None = None,
        ) -> _T: ...

    def create_checkpoint(
        self, factory_id: str, *, question: str, **fields: Any
    ) -> FactoryCheckpoint:
        check_checkpoint_fields(fields, "create_checkpoint")
        fid = _bare(factory_id)
        columns = _columns(
            {"status": "decided", "decided_by_kind": "server", **fields, "question": question}
        )
        model = SqlFactoryCheckpoint

        def write(session: Session) -> FactoryCheckpoint:
            ws = current_workspace_id()
            top = session.execute(
                select(func.max(model.number)).where(
                    model.workspace_id == ws, model.factory_id == fid
                )
            ).scalar()
            now = int(time.time())
            row = model(
                id=uuid.uuid4().hex,
                factory_id=fid,
                number=(top or 0) + 1,
                created_at=now,
                updated_at=now,
                **columns,
            )
            session.add(row)
            session.flush()
            return _checkpoint(row)

        return self._write("create_factory_checkpoint", write, sequence=(_CHECKPOINT_LOCK_NS, fid))

    def get_checkpoint(self, checkpoint_id: str) -> FactoryCheckpoint | None:
        try:
            key = (current_workspace_id(), _bare(checkpoint_id))
        except ValueError:
            return None
        with self._session("get_factory_checkpoint") as session:
            row = session.get(SqlFactoryCheckpoint, key)
            return _checkpoint(row) if row is not None else None

    def get_checkpoint_by_number(self, factory_id: str, number: int) -> FactoryCheckpoint | None:
        model = SqlFactoryCheckpoint
        with self._session("get_factory_checkpoint_by_number") as session:
            row = session.execute(
                select(model).where(
                    model.workspace_id == current_workspace_id(),
                    model.factory_id == _bare(factory_id),
                    model.number == number,
                )
            ).scalar_one_or_none()
            return _checkpoint(row) if row is not None else None

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
        model = SqlFactoryCheckpoint
        stmt = select(model).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == _bare(factory_id),
        )
        if since is not None:
            stmt = stmt.where(model.created_at >= since)
        if decision_id:
            stmt = stmt.where(model.decision_id == _bare(decision_id))
        if proposal_id:
            stmt = stmt.where(model.proposal_id == _bare(proposal_id))
        if overrides is not None:
            stmt = stmt.where(model.overrides == overrides)
        if dedupe_key is not None:
            stmt = stmt.where(model.dedupe_key == dedupe_key)
        stmt = stmt.order_by(model.number.desc()).limit(limit)
        with self._session("list_factory_checkpoints") as session:
            return [_checkpoint(r) for r in session.execute(stmt).scalars()]

    def update_checkpoint(
        self,
        checkpoint_id: str,
        *,
        expect_status: str | Sequence[str] | None = None,
        **fields: Any,
    ) -> FactoryCheckpoint | None:
        check_checkpoint_fields(fields, "update_checkpoint")
        columns = _columns(fields)
        expected = (
            None
            if expect_status is None
            else [expect_status]
            if isinstance(expect_status, str)
            else list(expect_status)
        )

        def write(session: Session) -> FactoryCheckpoint | None:
            key = (current_workspace_id(), _bare(checkpoint_id))
            row = session.get(SqlFactoryCheckpoint, key, with_for_update=True)
            if row is None:
                return None
            if expected is not None and decode_factory_checkpoint_status(row.status) not in (
                expected
            ):
                raise CheckpointConflict(_checkpoint(row))
            for name, value in columns.items():
                setattr(row, name, value)
            row.updated_at = int(time.time())
            session.flush()
            return _checkpoint(row)

        return self._write("update_factory_checkpoint", write)


__all__ = ["SqlCheckpointStore"]
