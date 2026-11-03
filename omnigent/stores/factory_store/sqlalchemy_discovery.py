"""The SQL half of the discovery ledger's store (mixed into ``SqlAlchemyFactoryStore``).

Signals and opportunities are numbered per factory (``S-n``, ``O-n``) inside
the write that inserts them; racing writers are serialized where the backend
can and otherwise replayed by the store's ``_write``.
"""

from __future__ import annotations

import builtins
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Final, Literal, TypeVar

from sqlalchemy import func, select, update

from omnigent.db.db_models import (
    SqlFactoryOpportunity,
    SqlFactoryScoreSnapshot,
    SqlFactorySignal,
    SqlFactorySignalLink,
    SqlFactorySourceState,
    current_workspace_id,
)
from omnigent.db.enum_codecs import (
    decode_factory_actor_kind,
    decode_factory_force,
    decode_factory_intensity,
    decode_factory_privacy,
    decode_factory_say_do,
    decode_factory_signal_kind,
    decode_factory_source_kind,
    encode_factory_actor_kind,
    encode_factory_force,
    encode_factory_intensity,
    encode_factory_privacy,
    encode_factory_say_do,
    encode_factory_signal_kind,
    encode_factory_source_kind,
)
from omnigent.entities.factory_discovery import (
    FactoryOpportunity,
    FactoryScoreSnapshot,
    FactorySignal,
    FactorySignalLink,
    FactorySourceState,
)
from omnigent.stores.factory_store.discovery import (
    EMPTY_JSON,
    LABEL_FIELDS,
    OPPORTUNITY_FIELDS,
    OPPORTUNITY_JSON,
    SIGNAL_FIELDS,
    SIGNAL_JSON,
    SNAPSHOT_FIELDS,
    SOURCE_STATE_FIELDS,
    SignalFilter,
    check_enums,
    check_fields,
    clip,
    norm_id,
    now_s,
    pointer_key,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_T = TypeVar("_T")

# Namespaces of the per-factory sequence locks (signals S-n, opportunities O-n).
SIGNAL_LOCK_NS: Final = 0x7369676E
OPPORTUNITY_LOCK_NS: Final = 0x6F707074

_ENCODERS: Final[dict[str, Callable[[str], int]]] = {
    "source_kind": encode_factory_source_kind,
    "kind": encode_factory_signal_kind,
    "say_do": encode_factory_say_do,
    "privacy": encode_factory_privacy,
    "intensity": encode_factory_intensity,
    "force": encode_factory_force,
    "author_kind": encode_factory_actor_kind,
    "created_kind": encode_factory_actor_kind,
}


def _dump(value: Any, key: str) -> str | None:
    if value is None or (key in EMPTY_JSON and value == EMPTY_JSON[key]):
        return None
    return json.dumps(value, separators=(",", ":"))


def _load(value: str | None, key: str) -> Any:
    if value is None:
        empty = EMPTY_JSON.get(key)
        return json.loads(json.dumps(empty)) if empty is not None else None
    return json.loads(value)


def _columns(fields: Mapping[str, Any], json_keys: frozenset[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in clip(fields).items():
        if key in json_keys:
            out[key] = _dump(value, key)
        elif key in _ENCODERS:
            out[key] = None if value is None else _ENCODERS[key](value)
        elif key == "query_event":
            out[key] = norm_id(value) if value is not None else None
        else:
            out[key] = value
    return out


def _opt(decode: Callable[[int], str], code: int | None) -> str | None:
    return None if code is None else decode(code)


def _signal(row: SqlFactorySignal) -> FactorySignal:
    return FactorySignal(
        factory_id=row.factory_id,
        number=row.number,
        source=row.source,
        source_kind=decode_factory_source_kind(row.source_kind),
        kind=_opt(decode_factory_signal_kind, row.kind),
        say_do=decode_factory_say_do(row.say_do),
        excerpt=row.excerpt,
        excerpt_sha=row.excerpt_sha,
        pointer=row.pointer,
        query_event=row.query_event,
        observed_at=row.observed_at,
        ingested_at=row.ingested_at,
        account=row.account,
        segments=_load(row.segments, "segments"),
        redactions=_load(row.redactions, "redactions"),
        privacy=decode_factory_privacy(row.privacy),
        expires_at=row.expires_at,
        injection=row.injection,
        flags=_load(row.flags, "flags"),
        author=row.author,
        author_kind=_opt(decode_factory_actor_kind, row.author_kind),
        meta=_load(row.meta, "meta"),
        paraphrase=row.paraphrase,
        intensity=_opt(decode_factory_intensity, row.intensity),
        force=_opt(decode_factory_force, row.force),
        labelled_by=row.labelled_by,
        labelled_at=row.labelled_at,
        withdrawn_at=row.withdrawn_at,
        withdrawn_by=row.withdrawn_by,
        expired_at=row.expired_at,
        workspace_id=row.workspace_id,
    )


def _link(row: SqlFactorySignalLink) -> FactorySignalLink:
    return FactorySignalLink(
        factory_id=row.factory_id,
        signal=row.signal,
        opportunity=row.opportunity,
        attached_by=row.attached_by,
        attached_kind=decode_factory_actor_kind(row.attached_kind),
        attached_at=row.attached_at,
        counter=bool(row.counter),
        confidence=row.confidence,
        via=row.via,
        detached_at=row.detached_at,
        detached_by=row.detached_by,
        detach_reason=row.detach_reason,
        workspace_id=row.workspace_id,
    )


def _opportunity(row: SqlFactoryOpportunity) -> FactoryOpportunity:
    return FactoryOpportunity(
        factory_id=row.factory_id,
        number=row.number,
        title=row.title,
        statement=row.statement,
        problem=row.problem,
        goal=row.goal,
        non_goal=row.non_goal,
        segments=_load(row.segments, "segments"),
        area=row.area,
        alternatives=_load(row.alternatives, "alternatives"),
        bet_note=_load(row.bet_note, "bet_note"),
        votes=_load(row.votes, "votes"),
        pinned_by=row.pinned_by,
        pinned_at=row.pinned_at,
        park=_load(row.park, "park"),
        kill=_load(row.kill, "kill"),
        merged_into=row.merged_into,
        split_from=row.split_from,
        research=_load(row.research, "research"),
        plan_request=_load(row.plan_request, "plan_request"),
        declines=_load(row.declines, "declines"),
        origin=_load(row.origin, "origin"),
        result=_load(row.result, "result"),
        created_by=row.created_by,
        created_kind=decode_factory_actor_kind(row.created_kind),
        created_at=row.created_at,
        updated_at=row.updated_at,
        workspace_id=row.workspace_id,
    )


def _snapshot(row: SqlFactoryScoreSnapshot) -> FactoryScoreSnapshot:
    return FactoryScoreSnapshot(
        id=row.id,
        factory_id=row.factory_id,
        opportunity=row.opportunity,
        at=row.at,
        reason=row.reason,
        def_version=row.def_version,
        rank=row.rank,
        band=row.band,
        level=row.level,
        inputs=_load(row.inputs, "inputs") or {},
        parts=_load(row.parts, "parts") or {},
        workspace_id=row.workspace_id,
    )


def _source_state(row: SqlFactorySourceState) -> FactorySourceState:
    return FactorySourceState(
        factory_id=row.factory_id,
        source=row.source,
        cursor=row.cursor,
        last_ok_at=row.last_ok_at,
        last_error_at=row.last_error_at,
        last_error=row.last_error,
        failures=row.failures,
        usage_day=row.usage_day,
        calls_today=row.calls_today,
        signals_total=row.signals_total,
        data=_load(row.data, "data"),
        updated_at=row.updated_at,
        workspace_id=row.workspace_id,
    )


class SqlDiscoveryStore:
    """The SQL half of :class:`~omnigent.stores.factory_store.discovery.DiscoveryStore`."""

    if TYPE_CHECKING:
        _session: Callable[[str], AbstractContextManager[Session]]

        def _write(
            self,
            operation: str,
            callback: Callable[[Session], _T],
            *,
            sequence: tuple[int, str] | None = None,
        ) -> _T: ...

    # ── Signals ─────────────────────────────────────────────────

    def add_signal(self, factory_id: str, **fields: Any) -> tuple[FactorySignal, bool]:
        check_fields(fields, SIGNAL_FIELDS, "add_signal")
        check_enums(fields)
        fid = norm_id(factory_id)
        key = pointer_key(fields.get("pointer"))
        columns = _columns(fields, SIGNAL_JSON)
        model = SqlFactorySignal

        def write(session: Session) -> tuple[FactorySignal, bool]:
            ws = current_workspace_id()
            if key is not None:
                existing = session.execute(
                    select(model).where(
                        model.workspace_id == ws,
                        model.factory_id == fid,
                        model.source == columns["source"],
                        model.pointer_key == key,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return _signal(existing), True
            top = session.execute(
                select(func.max(model.number)).where(
                    model.workspace_id == ws, model.factory_id == fid
                )
            ).scalar()
            row = model(factory_id=fid, number=(top or 0) + 1, pointer_key=key, **columns)
            session.add(row)
            session.flush()
            return _signal(row), False

        return self._write("store_factory_signal", write, sequence=(SIGNAL_LOCK_NS, fid))

    def get_signal(self, factory_id: str, number: int) -> FactorySignal | None:
        with self._session("get_factory_signal") as session:
            row = session.get(
                SqlFactorySignal, (current_workspace_id(), norm_id(factory_id), number)
            )
            return _signal(row) if row is not None else None

    def list_signals(
        self, factory_id: str, query: SignalFilter | None = None, *, limit: int = 200
    ) -> builtins.list[FactorySignal]:
        query = query or SignalFilter()
        model = SqlFactorySignal
        stmt = select(model).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == norm_id(factory_id),
        )
        if query.sources is not None:
            stmt = stmt.where(model.source.in_(query.sources))
        if query.kinds is not None:
            codes = [encode_factory_signal_kind(k) for k in query.kinds if k != "unlabelled"]
            cond = model.kind.in_(codes)
            if "unlabelled" in query.kinds:
                cond = cond | model.kind.is_(None)
            stmt = stmt.where(cond)
        if query.since is not None:
            stmt = stmt.where(model.ingested_at >= query.since)
        if query.before is not None:
            stmt = stmt.where(model.number < query.before)
        if query.numbers is not None:
            stmt = stmt.where(model.number.in_(query.numbers))
        stmt = stmt.order_by(model.number.desc()).limit(limit)
        with self._session("list_factory_signals") as session:
            return [_signal(r) for r in session.execute(stmt).scalars()]

    def count_signals(self, factory_id: str, *, since: int | None = None) -> int:
        model = SqlFactorySignal
        stmt = select(func.count()).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == norm_id(factory_id),
        )
        if since is not None:
            stmt = stmt.where(model.ingested_at >= since)
        with self._session("count_factory_signals") as session:
            return int(session.execute(stmt).scalar() or 0)

    def label_signal(
        self, factory_id: str, number: int, *, labelled_by: str, **labels: Any
    ) -> FactorySignal | None:
        check_fields(labels, LABEL_FIELDS, "label_signal")
        check_enums(labels)
        columns = _columns(labels, SIGNAL_JSON)
        key = (current_workspace_id(), norm_id(factory_id), number)

        def write(session: Session) -> FactorySignal | None:
            row = session.get(SqlFactorySignal, key)
            if row is None:
                return None
            for name, value in columns.items():
                setattr(row, name, value)
            row.labelled_by = labelled_by[:128]
            row.labelled_at = now_s()
            return _signal(row)

        return self._write("label_factory_signal", write)

    def clear_signal_text(
        self,
        factory_id: str,
        number: int,
        *,
        reason: Literal["withdrawn", "expired"],
        by: str | None = None,
        at: int | None = None,
    ) -> FactorySignal | None:
        key = (current_workspace_id(), norm_id(factory_id), number)
        when = at if at is not None else now_s()

        def write(session: Session) -> FactorySignal | None:
            row = session.get(SqlFactorySignal, key)
            if row is None:
                return None
            row.excerpt = None
            row.paraphrase = None
            if reason == "withdrawn":
                row.withdrawn_at = when
                row.withdrawn_by = by[:128] if by else None
            else:
                row.expired_at = when
            return _signal(row)

        return self._write("clear_factory_signal_text", write)

    def expire_signal_texts(self, now: int, *, limit: int = 500) -> int:
        model = SqlFactorySignal

        def write(session: Session) -> int:
            ws = current_workspace_id()
            keys = session.execute(
                select(model.factory_id, model.number)
                .where(
                    model.workspace_id == ws,
                    model.expires_at.is_not(None),
                    model.expires_at <= now,
                    model.expired_at.is_(None),
                    model.withdrawn_at.is_(None),
                )
                .limit(limit)
            ).all()
            for fid, number in keys:
                session.execute(
                    update(model)
                    .where(
                        model.workspace_id == ws,
                        model.factory_id == fid,
                        model.number == number,
                    )
                    .values(excerpt=None, paraphrase=None, expired_at=now)
                )
            return len(keys)

        return self._write("expire_factory_signal_texts", write)

    # ── Links ───────────────────────────────────────────────────

    def put_link(
        self,
        factory_id: str,
        signal: int,
        opportunity: int,
        *,
        attached_by: str,
        attached_kind: str,
        counter: bool = False,
        confidence: int | None = None,
        via: str | None = None,
    ) -> FactorySignalLink:
        check_enums({"attached_kind": attached_kind})
        fid = norm_id(factory_id)
        values = {
            "counter": 1 if counter else 0,
            "confidence": confidence,
            "attached_by": attached_by[:128],
            "attached_kind": encode_factory_actor_kind(attached_kind),
            "via": via[:32] if via else None,
            "detached_at": None,
            "detached_by": None,
            "detach_reason": None,
        }

        def write(session: Session) -> FactorySignalLink:
            key = (current_workspace_id(), fid, signal, opportunity)
            row = session.get(SqlFactorySignalLink, key)
            now = now_s()
            if row is None:
                row = SqlFactorySignalLink(
                    factory_id=fid,
                    signal=signal,
                    opportunity=opportunity,
                    attached_at=now,
                    **values,
                )
                session.add(row)
            else:
                for name, value in values.items():
                    setattr(row, name, value)
                row.attached_at = now
            session.flush()
            return _link(row)

        return self._write("attach_factory_signal", write)

    def detach_link(
        self,
        factory_id: str,
        signal: int,
        opportunity: int,
        *,
        by: str,
        reason: str | None = None,
    ) -> FactorySignalLink | None:
        key = (current_workspace_id(), norm_id(factory_id), signal, opportunity)

        def write(session: Session) -> FactorySignalLink | None:
            row = session.get(SqlFactorySignalLink, key)
            if row is None or row.detached_at is not None:
                return None
            row.detached_at = now_s()
            row.detached_by = by[:128]
            row.detach_reason = reason[:200] if reason else None
            return _link(row)

        return self._write("detach_factory_signal", write)

    def list_links(
        self,
        factory_id: str,
        *,
        signals: Sequence[int] | None = None,
        opportunities: Sequence[int] | None = None,
        active_only: bool = True,
    ) -> builtins.list[FactorySignalLink]:
        model = SqlFactorySignalLink
        stmt = select(model).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == norm_id(factory_id),
        )
        if signals is not None:
            stmt = stmt.where(model.signal.in_(list(signals)))
        if opportunities is not None:
            stmt = stmt.where(model.opportunity.in_(list(opportunities)))
        if active_only:
            stmt = stmt.where(model.detached_at.is_(None))
        stmt = stmt.order_by(model.opportunity, model.signal)
        with self._session("list_factory_signal_links") as session:
            return [_link(r) for r in session.execute(stmt).scalars()]

    # ── Opportunities ───────────────────────────────────────────

    def create_opportunity(
        self,
        factory_id: str,
        *,
        title: str,
        statement: str,
        created_by: str,
        created_kind: str,
        **fields: Any,
    ) -> FactoryOpportunity:
        check_fields(fields, OPPORTUNITY_FIELDS - {"title", "statement"}, "create_opportunity")
        check_enums({"created_kind": created_kind})
        fid = norm_id(factory_id)
        columns = _columns(
            {
                "title": title,
                "statement": statement,
                "created_by": created_by,
                "created_kind": created_kind,
                **fields,
            },
            OPPORTUNITY_JSON,
        )
        model = SqlFactoryOpportunity

        def write(session: Session) -> FactoryOpportunity:
            ws = current_workspace_id()
            top = session.execute(
                select(func.max(model.number)).where(
                    model.workspace_id == ws, model.factory_id == fid
                )
            ).scalar()
            now = now_s()
            row = model(
                factory_id=fid,
                number=(top or 0) + 1,
                created_at=now,
                updated_at=now,
                **columns,
            )
            session.add(row)
            session.flush()
            return _opportunity(row)

        return self._write("file_factory_opportunity", write, sequence=(OPPORTUNITY_LOCK_NS, fid))

    def get_opportunity(self, factory_id: str, number: int) -> FactoryOpportunity | None:
        with self._session("get_factory_opportunity") as session:
            row = session.get(
                SqlFactoryOpportunity, (current_workspace_id(), norm_id(factory_id), number)
            )
            return _opportunity(row) if row is not None else None

    def list_opportunities(
        self, factory_id: str, *, limit: int = 1000
    ) -> builtins.list[FactoryOpportunity]:
        model = SqlFactoryOpportunity
        stmt = (
            select(model)
            .where(
                model.workspace_id == current_workspace_id(),
                model.factory_id == norm_id(factory_id),
            )
            .order_by(model.number)
            .limit(limit)
        )
        with self._session("list_factory_opportunities") as session:
            return [_opportunity(r) for r in session.execute(stmt).scalars()]

    def update_opportunity(
        self, factory_id: str, number: int, **fields: Any
    ) -> FactoryOpportunity | None:
        check_fields(fields, OPPORTUNITY_FIELDS, "update_opportunity")
        columns = _columns(fields, OPPORTUNITY_JSON)
        key = (current_workspace_id(), norm_id(factory_id), number)

        def write(session: Session) -> FactoryOpportunity | None:
            row = session.get(SqlFactoryOpportunity, key)
            if row is None:
                return None
            for name, value in columns.items():
                setattr(row, name, value)
            row.updated_at = now_s()
            return _opportunity(row)

        return self._write("update_factory_opportunity", write)

    # ── Score snapshots ─────────────────────────────────────────

    def add_score_snapshots(self, factory_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        fid = norm_id(factory_id)
        for raw in rows:
            check_fields(raw, SNAPSHOT_FIELDS, "add_score_snapshots")
        values = [
            {
                **clip({k: v for k, v in raw.items() if k not in ("inputs", "parts")}),
                "inputs": _dump(raw.get("inputs"), "inputs"),
                "parts": _dump(raw.get("parts"), "parts"),
            }
            for raw in rows
        ]

        def write(session: Session) -> int:
            for value in values:
                session.add(SqlFactoryScoreSnapshot(id=uuid.uuid4().hex, factory_id=fid, **value))
            return len(values)

        return self._write("store_factory_score_snapshots", write) if values else 0

    def list_score_snapshots(
        self,
        factory_id: str,
        *,
        opportunity: int | None = None,
        since: int | None = None,
        limit: int = 200,
    ) -> builtins.list[FactoryScoreSnapshot]:
        model = SqlFactoryScoreSnapshot
        stmt = select(model).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == norm_id(factory_id),
        )
        if opportunity is not None:
            stmt = stmt.where(model.opportunity == opportunity)
        if since is not None:
            stmt = stmt.where(model.at >= since)
        stmt = stmt.order_by(model.at.desc(), model.id.desc()).limit(limit)
        with self._session("list_factory_score_snapshots") as session:
            return [_snapshot(r) for r in session.execute(stmt).scalars()]

    # ── Source state ────────────────────────────────────────────

    def get_source_state(self, factory_id: str, source: str) -> FactorySourceState | None:
        with self._session("get_factory_source_state") as session:
            row = session.get(
                SqlFactorySourceState, (current_workspace_id(), norm_id(factory_id), source)
            )
            return _source_state(row) if row is not None else None

    def put_source_state(self, factory_id: str, source: str, **fields: Any) -> FactorySourceState:
        check_fields(fields, SOURCE_STATE_FIELDS, "put_source_state")
        fid = norm_id(factory_id)
        columns = {k: (_dump(v, k) if k == "data" else v) for k, v in clip(fields).items()}

        def write(session: Session) -> FactorySourceState:
            key = (current_workspace_id(), fid, source)
            row = session.get(SqlFactorySourceState, key)
            if row is None:
                row = SqlFactorySourceState(
                    factory_id=fid, source=source, updated_at=now_s(), **columns
                )
                session.add(row)
            else:
                for name, value in columns.items():
                    setattr(row, name, value)
                row.updated_at = now_s()
            session.flush()
            return _source_state(row)

        return self._write("store_factory_source_state", write)

    def list_source_states(self, factory_id: str) -> builtins.list[FactorySourceState]:
        model = SqlFactorySourceState
        stmt = (
            select(model)
            .where(
                model.workspace_id == current_workspace_id(),
                model.factory_id == norm_id(factory_id),
            )
            .order_by(model.source)
        )
        with self._session("list_factory_source_states") as session:
            return [_source_state(r) for r in session.execute(stmt).scalars()]


__all__ = ["OPPORTUNITY_LOCK_NS", "SIGNAL_LOCK_NS", "SqlDiscoveryStore"]
