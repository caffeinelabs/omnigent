"""The SQL half of :class:`~omnigent.stores.factory_store.initiatives.InitiativeStore`.

Mixed into :class:`~omnigent.stores.factory_store.sqlalchemy_store.SqlAlchemyFactoryStore`,
whose ``_session`` and ``_write`` it uses. Numbers (``I-<n>``) are taken under
the factory's sequence lock, like proposals and decisions.
"""

from __future__ import annotations

import builtins
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Final, TypeVar

from sqlalchemy import func, select

from omnigent.db.db_models import SqlFactoryInitiative, current_workspace_id
from omnigent.db.enum_codecs import (
    decode_factory_initiative_status,
    encode_factory_initiative_status,
)
from omnigent.entities.factory import FactoryInitiative
from omnigent.stores.factory_store.initiatives import (
    InitiativeVersionConflict,
    check_initiative_fields,
    clip_initiative,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_T = TypeVar("_T")

_INITIATIVE_LOCK_NS: Final = 0x696E6974
_JSON: Final = frozenset({"requested_by", "plan", "runs"})


def _dump(value: Any) -> str | None:
    import json

    return None if value is None else json.dumps(value, separators=(",", ":"))


def _load(value: str | None) -> Any:
    import json

    return None if value is None else json.loads(value)


def _bare(value: str) -> str:
    from omnigent.db.db_models import uuid_to_bytes

    return uuid_to_bytes(value).hex()


def _columns(fields: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in clip_initiative(fields).items():
        if key == "status":
            out[key] = encode_factory_initiative_status(value)
        elif key in _JSON:
            out[key] = _dump(value)
        else:
            out[key] = value
    return out


def _initiative(row: SqlFactoryInitiative) -> FactoryInitiative:
    return FactoryInitiative(
        id=row.id,
        factory_id=row.factory_id,
        number=row.number,
        title=row.title,
        ask=row.ask or "",
        status=decode_factory_initiative_status(row.status),
        requested_by=_load(row.requested_by),
        ask_ref=row.ask_ref,
        thread=row.thread,
        outcome=row.outcome,
        plan=_load(row.plan),
        runs=_load(row.runs) or [],
        created_by=row.created_by,
        version=row.version,
        closed_at=row.closed_at,
        closed_by=row.closed_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        workspace_id=row.workspace_id,
    )


class SqlInitiativeStore:
    """The SQL half of the initiatives contract."""

    if TYPE_CHECKING:
        _session: Callable[[str], AbstractContextManager[Session]]

        def _write(
            self,
            operation: str,
            callback: Callable[[Session], _T],
            *,
            sequence: tuple[int, str] | None = None,
        ) -> _T: ...

    def create_initiative(
        self, factory_id: str, *, title: str, ask: str, **fields: Any
    ) -> FactoryInitiative:
        import time

        check_initiative_fields(fields, "create_initiative")
        fid = _bare(factory_id)
        columns = _columns({"status": "open", **fields, "title": title})
        model = SqlFactoryInitiative

        def write(session: Session) -> FactoryInitiative:
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
                ask=ask,
                version=1,
                created_at=now,
                updated_at=now,
                **columns,
            )
            session.add(row)
            session.flush()
            return _initiative(row)

        return self._write("create_factory_initiative", write, sequence=(_INITIATIVE_LOCK_NS, fid))

    def get_initiative(self, initiative_id: str) -> FactoryInitiative | None:
        try:
            key = (current_workspace_id(), _bare(initiative_id))
        except ValueError:
            return None
        with self._session("get_factory_initiative") as session:
            row = session.get(SqlFactoryInitiative, key)
            return _initiative(row) if row is not None else None

    def get_initiative_by_number(self, factory_id: str, number: int) -> FactoryInitiative | None:
        model = SqlFactoryInitiative
        with self._session("get_factory_initiative_by_number") as session:
            row = session.execute(
                select(model).where(
                    model.workspace_id == current_workspace_id(),
                    model.factory_id == _bare(factory_id),
                    model.number == number,
                )
            ).scalar_one_or_none()
            return _initiative(row) if row is not None else None

    def list_initiatives(
        self,
        factory_id: str,
        *,
        status: str | Sequence[str] | None = None,
        limit: int = 100,
    ) -> builtins.list[FactoryInitiative]:
        model = SqlFactoryInitiative
        stmt = select(model).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == _bare(factory_id),
        )
        statuses = [status] if isinstance(status, str) else (list(status) if status else None)
        if statuses:
            stmt = stmt.where(
                model.status.in_([encode_factory_initiative_status(s) for s in statuses])
            )
        stmt = stmt.order_by(model.number.desc()).limit(limit)
        with self._session("list_factory_initiatives") as session:
            return [_initiative(r) for r in session.execute(stmt).scalars()]

    def update_initiative(
        self, initiative_id: str, *, expect_version: int | None = None, **fields: Any
    ) -> FactoryInitiative | None:
        import time

        check_initiative_fields(fields, "update_initiative")
        columns = _columns(fields)
        if "runs" in fields and fields["runs"] is None:
            columns["runs"] = _dump([])

        def write(session: Session) -> FactoryInitiative | None:
            key = (current_workspace_id(), _bare(initiative_id))
            row = session.get(SqlFactoryInitiative, key, with_for_update=True)
            if row is None:
                return None
            if expect_version is not None and row.version != expect_version:
                raise InitiativeVersionConflict(_initiative(row))
            for name, value in columns.items():
                setattr(row, name, value)
            row.version = row.version + 1
            row.updated_at = int(time.time())
            session.flush()
            return _initiative(row)

        return self._write("update_factory_initiative", write)


__all__ = ["SqlInitiativeStore"]
