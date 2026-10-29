"""Factory spans (``factory_spans``): durable, OTel-shaped records of what happened.

Every write goes through :func:`write_span`, idempotent on ``dedupe_key``:
writing a span again merges into the stored row (closing it, filling missing
fields, merging attributes) instead of adding one. :func:`install` registers
the event recorder (:mod:`.recorder`), which turns the factory event log into
project, stage, ready_wait, decision, task, env, preview, demo, incident,
intervention and proposal spans as events are stored; :mod:`.pr_rounds`
writes the pr, pr_round and gate spans of each GitHub refresh, and
:mod:`.heal` rebuilds them from the tables nightly. Later writers (loops,
chat turns, turns, tools) reuse :func:`write_span` and the key helpers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.entities.factory import FactorySpan
from omnigent.factory import LABEL_DEF_VERSION, LABEL_VERSION, STAGE_DONE, STAGE_MERGED
from omnigent.server.factory import events
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.telemetry import versions
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.stores.factory_store import FactoryStore

_logger = logging.getLogger(__name__)

OPEN: Final = "open"
OK: Final = "ok"
FAIL: Final = "fail"
ABANDONED: Final = "abandoned"
SUPERSEDED: Final = "superseded"
TIMEOUT: Final = "timeout"
CANCELLED: Final = "cancelled"

TERMINAL_STAGES: Final = frozenset({STAGE_MERGED, STAGE_DONE})
"""Stages that end a project span (a later stage change reopens it)."""
CONTEXT_TTL_S: Final = 60.0
NAME_MAX: Final = 64
FIELD_MAX: Final[dict[str, int]] = {"role": 16, "agent_name": 64, "actor": 128}
"""Free-text ``factory_spans`` columns, clipped to their width before a write."""
ID_COLUMNS: Final = frozenset(
    {"parent_id", "pr_id", "task_id", "loop_id", "session_id", "version_id"}
)
"""``factory_spans`` columns that hold row ids (32-hex uuids), never numbers or refs."""
_CONTEXTS_MAX: Final = 4096


class SpanFieldError(ValueError):
    """
    A span id column was given something that is not a row id (a PR number, a ref, a slug).

    :param field: The column, e.g. ``"pr_id"``.
    :param value: What it was given, e.g. ``"9724"``.
    """

    def __init__(self, field: str, value: Any) -> None:
        super().__init__(f"factory_spans.{field} takes a row id, got {value!r}")
        self.field = field
        self.value = value


def span_ids(fields: Mapping[str, Any]) -> dict[str, Any]:
    """
    A copy of *fields* with each id column as bare hex (``None`` passes).

    :raises SpanFieldError: Naming the first id column whose value is not a uuid.
    """
    out = dict(fields)
    for key in sorted(ID_COLUMNS & out.keys()):
        value = out[key]
        if value is None:
            continue
        try:
            out[key] = norm_id(value)
        except (ValueError, TypeError, AttributeError) as exc:
            raise SpanFieldError(key, value) from exc
    return out


# ── Keys ─────────────────────────────────────────────────────────
# Live writers and heal derive the same keys, so their writes converge.


def project_key(slug: str) -> str:
    """:returns: The dedupe key of a project's span."""
    return f"project:{slug}"


def stage_key(slug: str, ts: int) -> str:
    """:returns: The dedupe key of the stage episode that began at *ts* (ms)."""
    return f"stage:{slug}:{ts}"


def pr_key(pr_id: str) -> str:
    """:returns: The dedupe key of a PR's span."""
    return f"pr:{pr_id}"


def round_key(pr_id: str, sha: str) -> str:
    """:returns: The dedupe key of the PR round of head *sha*."""
    return f"pr_round:{pr_id}:{sha}"


def gate_key(pr_id: str, sha: str, gate: str) -> str:
    """:returns: The dedupe key of *gate* on head *sha*."""
    return f"gate:{pr_id}:{sha}:{gate}"


def ready_wait_key(pr_id: str, ts: int) -> str:
    """:returns: The dedupe key of the ready wait that began at *ts* (ms)."""
    return f"ready_wait:{pr_id}:{ts}"


def decision_key(decision_id: str) -> str:
    """:returns: The dedupe key of a decision's span."""
    return f"decision:{decision_id}"


def task_key(session_id: str) -> str:
    """:returns: The dedupe key of a sub-agent run (one per dispatched session)."""
    return f"task:{session_id}"


def env_key(slug: str, ts: int) -> str:
    """:returns: The dedupe key of a dev-env state episode."""
    return f"env:{slug}:{ts}"


def preview_key(pr_id: str, ts: int) -> str:
    """:returns: The dedupe key of a preview state episode."""
    return f"preview:{pr_id}:{ts}"


def demo_key(demo_id: str) -> str:
    """:returns: The dedupe key of a demo run."""
    return f"demo:{demo_id}"


def incident_key(ref: str) -> str:
    """:returns: The dedupe key of an incident (an issue id or the opening event id)."""
    return f"incident:{ref}"


def intervention_key(event_id: str) -> str:
    """:returns: The dedupe key of a human control."""
    return f"intervention:{event_id}"


def proposal_key(ref: str) -> str:
    """:returns: The dedupe key of a proposal."""
    return f"proposal:{ref}"


def trace_key_for(*, pr_id: str | None = None, project: str | None = None) -> str:
    """:returns: ``pr:<id>``, ``project:<slug>`` or ``factory``: the span tree a span joins."""
    if pr_id:
        return f"pr:{pr_id}"
    if project:
        return f"project:{project}"
    return "factory"


# ── Writer ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class SpanContext:
    """
    What new spans are stamped with.

    :param version_id: ``factory_versions`` id of the platform.
    :param def_version: The factory's pinned definition version.
    """

    version_id: str | None = None
    def_version: int | None = None


NO_CONTEXT: Final = SpanContext()


def _closing(
    span: FactorySpan,
    ended_at: int | None,
    outcome: str | None,
    *,
    force: bool,
    reopen: bool,
    keep_after: int | None,
) -> dict[str, Any]:
    if outcome is None:
        return {}
    if keep_after is not None and span.ended_at is not None and span.ended_at > keep_after:
        # Closed by a live writer after heal read its facts: heal's view is older.
        return {}
    if outcome == OPEN:
        if reopen and span.outcome != OPEN:
            return {"ended_at": None, "outcome": OPEN}
        return {}
    end = max(ended_at if ended_at is not None else span.started_at, span.started_at)
    if span.outcome == OPEN or (force and (span.outcome, span.ended_at) != (outcome, end)):
        return {"ended_at": end, "outcome": outcome}
    return {}


def merged_attrs(
    current: Mapping[str, Any] | None, new: Mapping[str, Any] | None, set_once: Iterable[str]
) -> dict[str, Any] | None:
    if not new:
        return None
    merged = dict(current or {})
    keep = set(set_once)
    for key, value in new.items():
        if key in keep and merged.get(key) is not None:
            continue
        merged[key] = value
    return merged if merged != (current or {}) else None


def write_span(
    store: FactoryStore,
    factory_id: str,
    *,
    kind: str,
    name: str,
    dedupe_key: str,
    started_at: int,
    trace_key: str | None = None,
    ended_at: int | None = None,
    outcome: str | None = None,
    attrs: Mapping[str, Any] | None = None,
    set_once: Iterable[str] = (),
    overwrite: Iterable[str] = (),
    force: bool = False,
    reopen: bool = False,
    keep_closed_after: int | None = None,
    ctx: SpanContext = NO_CONTEXT,
    **fields: Any,
) -> FactorySpan:
    """
    Insert a span, or merge into the one stored under *dedupe_key* (synchronous).

    On a merge: an open span is closed with *ended_at*/*outcome* (a closed
    one only with *force*, or reopened with ``outcome="open"`` and *reopen*);
    *attrs* are merged (keys in *set_once* keep a stored non-null value);
    *fields* fill columns that are still null (those in *overwrite* replace).
    ``started_at`` and the stamped version never change.

    :param started_at: Epoch milliseconds.
    :param keep_closed_after: Heal's read time (epoch ms): a span closed after
        it keeps its close (no reopen, no forced rewrite).
    :param ended_at: Epoch milliseconds (clamped to ``started_at``).
    :param outcome: ``open`` / ``ok`` / ``fail`` / ``abandoned`` / ….
    :param fields: Any other ``factory_spans`` column, e.g. ``pr_id`` or ``cost_usd``.
    :returns: The stored span.
    :raises SpanFieldError: When an id column (``pr_id``, ``session_id``, …) is not a row id.
    """
    values = span_ids(
        {
            k: v[: FIELD_MAX[k]] if k in FIELD_MAX and isinstance(v, str) else v
            for k, v in fields.items()
            if v is not None
        }
    )
    closed = outcome is not None and outcome != OPEN
    insert: dict[str, Any] = {**values, "outcome": outcome or OPEN}
    if closed:
        insert["ended_at"] = max(ended_at if ended_at is not None else started_at, started_at)
    if attrs:
        insert["attrs"] = dict(attrs)
    if ctx.version_id:
        insert.setdefault("version_id", ctx.version_id)
    if ctx.def_version is not None:
        insert.setdefault("def_version", ctx.def_version)
    span, created = store.insert_span(
        factory_id,
        kind=kind,
        name=name[:NAME_MAX] or kind,
        trace_key=trace_key
        or trace_key_for(pr_id=values.get("pr_id"), project=values.get("project_slug")),
        started_at=started_at,
        dedupe_key=dedupe_key,
        **insert,
    )
    if created:
        return span
    updates = _closing(
        span, ended_at, outcome, force=force, reopen=reopen, keep_after=keep_closed_after
    )
    merged = merged_attrs(span.attrs, attrs, set_once)
    if merged is not None:
        updates["attrs"] = merged
    replace = set(overwrite)
    for key, value in values.items():
        current = getattr(span, key, None)
        if (key in replace and current != value) or current is None:
            updates[key] = value
    if not updates:
        return span
    return store.update_span(span.id, **updates) or span


def close_open(
    store: FactoryStore,
    factory_id: str,
    kind: str,
    *,
    ended_at: int,
    outcome: str = OK,
    name: str | None = None,
    project_slug: str | None = None,
    pr_id: str | None = None,
    session_id: str | None = None,
) -> list[FactorySpan]:
    """
    Close the open *kind* spans of one subject that started by *ended_at*.

    :param name: Only spans with this name.
    :param project_slug: Only this project's spans (likewise *pr_id*, *session_id*).
    :returns: The spans closed.
    :raises SpanFieldError: When *pr_id* or *session_id* is not a row id.
    """
    closed: list[FactorySpan] = []
    ids = span_ids({"pr_id": pr_id, "session_id": session_id})
    found = store.list_open_spans(factory_id, kind, project_slug=project_slug, **ids)
    for span in found:
        if span.started_at > ended_at or (name is not None and span.name != name):
            continue
        updated = store.update_span(span.id, ended_at=ended_at, outcome=outcome)
        if updated is not None:
            closed.append(updated)
    return closed


# ── Context ──────────────────────────────────────────────────────

_contexts: dict[str, tuple[float, SpanContext]] = {}


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _version_label(labels: Mapping[str, str], factory_id: str) -> str | None:
    """The root's version label, when it is a ``factory_versions`` id (it stamps every span)."""
    value = labels.get(LABEL_VERSION)
    if not value:
        return None
    try:
        return norm_id(value)
    except ValueError:
        _logger.warning("factory spans: %s has a version label %r, not an id", factory_id, value)
        return None


def span_context(deps: FactoryDeps, factory_id: str) -> SpanContext:
    """
    The version and definition a factory's new spans carry (cached 60 s).

    ``version_id`` is the platform version this server recorded (the root's
    ``omnigent.factory.version`` label before it has one); ``def_version`` is
    the root's pinned definition (empty before one).
    """
    now = time.monotonic()
    cached = _contexts.get(factory_id)
    if cached is not None and now - cached[0] < CONTEXT_TTL_S:
        return cached[1]
    labels: Mapping[str, str] = {}
    try:
        root = deps.conversation_store.get_conversation(factory_id)
        labels = root.labels if root is not None else {}
    except Exception:  # noqa: BLE001 - spans are written without a version then
        _logger.debug("factory spans: no root labels for %s", factory_id, exc_info=True)
    def_version = _int(labels.get(LABEL_DEF_VERSION))
    if def_version is None and labels:
        # Before anything resolved the definition, seed v1 now so rows are stamped from the start.
        from omnigent.server.factory import definition

        def_version = definition.pinned_version(deps, factory_id)
    ctx = SpanContext(
        version_id=versions.current_version_id() or _version_label(labels, factory_id),
        def_version=def_version,
    )
    if len(_contexts) > _CONTEXTS_MAX:
        _contexts.clear()
    _contexts[factory_id] = (now, ctx)
    return ctx


def stamps(deps: FactoryDeps, factory_id: str) -> dict[str, Any]:
    """:returns: ``{def_version, version_id}`` to stamp on a new project or issue (known ones)."""
    ctx = span_context(deps, factory_id)
    return {
        k: v
        for k, v in (("def_version", ctx.def_version), ("version_id", ctx.version_id))
        if v is not None
    }


def reset() -> None:
    """Forget cached contexts (tests)."""
    _contexts.clear()


def session_start_ms(conv: Any) -> int | None:
    """:returns: When a session was created (epoch ms), the start of its task span."""
    created = getattr(conv, "created_at", None)
    return int(created * 1000) if created else None


def task_cost_fields(child: Any) -> dict[str, Any]:
    """:returns: ``cost_usd`` / ``tokens_in`` / ``tokens_out`` of a sub-agent's whole run."""
    usage = child.session_usage if isinstance(child.session_usage, dict) else {}
    cost = usage.get("total_cost_usd")
    tokens_in = sum(
        int(usage.get(k) or 0)
        for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    )
    return {
        "cost_usd": float(cost) if isinstance(cost, int | float) else None,
        "tokens_in": tokens_in or None,
        "tokens_out": int(usage.get("output_tokens") or 0) or None,
    }


def attach_task_cost_sync(deps: FactoryDeps, factory_id: str, child: Any) -> FactorySpan | None:
    """Put *child*'s cumulative cost on its task span (synchronous)."""
    fields = {k: v for k, v in task_cost_fields(child).items() if v is not None}
    if not fields:
        return None
    created = session_start_ms(child) or int(time.time() * 1000)
    return write_span(
        deps.factory_store,
        factory_id,
        kind="task",
        name=(child.title or child.id)[:NAME_MAX],
        dedupe_key=task_key(child.id),
        started_at=created,
        overwrite=tuple(fields),
        ctx=span_context(deps, factory_id),
        session_id=child.id,
        **fields,
    )


async def attach_task_cost(app_state: Any, factory_id: str, child: Any) -> None:
    """
    The supervisor's child-close hook: the run's cost onto its task span. Never raises.

    The task span covers the sub-agent's whole session, so its cost is the
    session's cumulative usage: the sum of every run's delta.
    """
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return
    try:
        await asyncio.to_thread(attach_task_cost_sync, deps, factory_id, child)
    except Exception:  # noqa: BLE001 - heal re-attaches costs nightly
        _logger.warning("factory spans: no cost for task %s", child.id, exc_info=True)


def install() -> Callable[[], None]:
    """Register the span recorder on the event log. :returns: The uninstaller."""
    from omnigent.server.factory.telemetry.recorder import record_event

    return events.add_listener(record_event)


__all__ = [
    "ABANDONED",
    "CANCELLED",
    "FAIL",
    "FIELD_MAX",
    "ID_COLUMNS",
    "NAME_MAX",
    "NO_CONTEXT",
    "OK",
    "OPEN",
    "SUPERSEDED",
    "TERMINAL_STAGES",
    "TIMEOUT",
    "SpanContext",
    "SpanFieldError",
    "attach_task_cost",
    "attach_task_cost_sync",
    "close_open",
    "decision_key",
    "demo_key",
    "env_key",
    "gate_key",
    "incident_key",
    "install",
    "intervention_key",
    "merged_attrs",
    "pr_key",
    "preview_key",
    "project_key",
    "proposal_key",
    "ready_wait_key",
    "reset",
    "round_key",
    "session_start_ms",
    "span_context",
    "span_ids",
    "stage_key",
    "task_cost_fields",
    "task_key",
    "trace_key_for",
    "write_span",
]
