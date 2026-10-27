"""Streamed factory exports for offline analysis (``GET …/export``).

Rows are filtered the way the cockpit filters them (projects whose PL the
viewer cannot read are dropped, with their PRs, spans, cost, demos and
events) and never carry transcript content: no mail text or excerpts, no
turn output, agent notes, decision context or free-text answers.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from omnigent.entities.factory import (
    FactoryCostDaily,
    FactoryDecision,
    FactoryDemo,
    FactoryEvent,
    FactoryPr,
    FactorySpan,
)
from omnigent.factory_gates import DEMO_GATE, READY_GATES
from omnigent.stores.factory_store import FactoryStore, kind_matches

TABLES: Final[tuple[str, ...]] = ("spans", "cost", "decisions", "prs", "demos", "events")
FORMATS: Final[tuple[str, ...]] = ("ndjson", "csv")
TRANSCRIPT_KINDS: Final[tuple[str, ...]] = ("message.*", "notice.*", "chat.*", "alert.failed")
"""Events whose summary quotes agent or human messages: exported without it."""
TEXT_KEYS: Final = frozenset(
    {
        "text",
        "excerpt",
        "body",
        "output",
        "detail",
        "note",
        "message",
        "blocked_on",
        "question",
        "context",
        "answer_text",
        "reason",
    }
)
"""Event data keys that carry free text; never exported."""
QUESTION_MAX: Final = 300
_PAGE: Final = 1000

COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "spans": (
        "id",
        "trace_key",
        "parent_id",
        "kind",
        "name",
        "project_slug",
        "pr_id",
        "task_id",
        "loop_id",
        "head_sha",
        "session_id",
        "role",
        "agent_name",
        "actor",
        "started_at",
        "ended_at",
        "outcome",
        "attempt",
        "cost_usd",
        "tokens_in",
        "tokens_out",
        "version_id",
        "def_version",
        "attrs",
    ),
    "cost": (
        "day_utc",
        "project_slug",
        "role",
        "agent_name",
        "model",
        "cost_usd",
        "tokens_in",
        "tokens_out",
        "turns",
        "updated_at",
    ),
    "decisions": (
        "id",
        "ref",
        "source",
        "status",
        "blocking",
        "project_slug",
        "asked_by_session_id",
        "question",
        "answer_option_id",
        "answered_by",
        "answered_at",
        "delivered_at",
        "assignee",
        "claimed_by",
        "created_at",
        "updated_at",
    ),
    "prs": (
        "id",
        "repo",
        "number",
        "url",
        "title",
        "project_slug",
        "task",
        "state",
        "is_draft",
        "head_ref",
        "base_ref",
        "head_sha",
        "additions",
        "deletions",
        "changed_files",
        "ready",
        "gates",
        "sentinel_verdict",
        "opened_at",
        "first_ready_at",
        "ready_at",
        "merged_at",
        "merged_by",
        "closed_at",
        "source",
        "created_at",
    ),
    "demos": (
        "id",
        "pr_id",
        "repo",
        "pr_number",
        "project_slug",
        "head_sha",
        "status",
        "title",
        "steps",
        "duration_s",
        "session_id",
        "created_at",
    ),
    "events": (
        "id",
        "ts",
        "kind",
        "severity",
        "project_slug",
        "from_id",
        "to_id",
        "subject",
        "summary",
        "data",
    ),
}


@dataclass(frozen=True)
class Visibility:
    """
    What a viewer may export.

    :param hidden_projects: Projects whose PL the viewer cannot read.
    :param members: Every session of the factory.
    :param readable: The members the viewer can read.
    """

    hidden_projects: frozenset[str] = frozenset()
    members: frozenset[str] = frozenset()
    readable: frozenset[str] = field(default_factory=frozenset)

    def project(self, slug: str | None) -> bool:
        """:returns: Whether rows of project *slug* (``None`` = factory-wide) are visible."""
        return slug not in self.hidden_projects if slug else True

    def session(self, session_id: str | None) -> bool:
        """:returns: Whether a row naming *session_id* is visible (non-members always are)."""
        return session_id is None or session_id not in self.members or session_id in self.readable


# ── Row projections (no transcript content) ──────────────────────


def span_row(span: FactorySpan) -> dict[str, Any]:
    """:returns: A span as an export row."""
    return {name: getattr(span, name) for name in COLUMNS["spans"]}


def cost_row(cell: FactoryCostDaily) -> dict[str, Any]:
    """:returns: A ledger cell as an export row."""
    return {name: getattr(cell, name) for name in COLUMNS["cost"]}


def decision_row(decision: FactoryDecision) -> dict[str, Any]:
    """:returns: A decision without its context or free-text answer."""
    row = {name: getattr(decision, name, None) for name in COLUMNS["decisions"] if name != "ref"}
    row["ref"] = decision.ref
    row["question"] = (decision.question or "")[:QUESTION_MAX]
    return {name: row.get(name) for name in COLUMNS["decisions"]}


def pr_row(pr: FactoryPr) -> dict[str, Any]:
    """:returns: A PR with gate states only (no evidence, review text or preview)."""
    gates = pr.gates or {}
    row: dict[str, Any] = {
        name: getattr(pr, name, None)
        for name in COLUMNS["prs"]
        if name not in ("gates", "sentinel_verdict")
    }
    row["gates"] = {g: gates.get(g) for g in (*READY_GATES, DEMO_GATE) if g in gates}
    row["sentinel_verdict"] = (pr.sentinel or {}).get("verdict")
    return {name: row.get(name) for name in COLUMNS["prs"]}


def demo_row(demo: FactoryDemo) -> dict[str, Any]:
    """:returns: A demo run without its summary, steps text or files."""
    manifest = demo.manifest if isinstance(demo.manifest, dict) else {}
    steps = manifest.get("steps")
    return {
        "id": demo.id,
        "pr_id": demo.pr_id,
        "repo": demo.repo,
        "pr_number": demo.pr_number,
        "project_slug": demo.project_slug,
        "head_sha": demo.head_sha,
        "status": demo.status,
        "title": demo.title,
        "steps": len(steps) if isinstance(steps, list) else None,
        "duration_s": manifest.get("duration_s"),
        "session_id": demo.session_id,
        "created_at": demo.created_at,
    }


def _clean(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _clean(v) for k, v in value.items() if k not in TEXT_KEYS}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def event_row(event: FactoryEvent) -> dict[str, Any]:
    """:returns: An event without message text (free-text data keys, quoting summaries)."""
    quoting = kind_matches(event.kind, TRANSCRIPT_KINDS)
    return {
        "id": event.id,
        "ts": event.ts,
        "kind": event.kind,
        "severity": event.severity,
        "project_slug": event.project_slug,
        "from_id": event.from_id,
        "to_id": event.to_id,
        "subject": event.subject,
        "summary": None if quoting else event.summary,
        "data": _clean(event.data) if isinstance(event.data, Mapping) else None,
    }


# ── Readers ──────────────────────────────────────────────────────


def _paged(fetch: Callable[[int], list[Any]], ts: Callable[[Any], int]) -> Iterable[list[Any]]:
    seen: set[str] = set()
    since = 0
    while True:
        page = fetch(since)
        fresh = [row for row in page if row.id not in seen]
        if not fresh:
            return
        seen.update(row.id for row in fresh)
        yield fresh
        if len(page) < _PAGE:
            return
        since = ts(page[-1])


def _rows(
    store: FactoryStore, factory_id: str, table: str, vis: Visibility, since_ms: int
) -> Iterable[list[dict[str, Any]]]:
    """Visible export rows of one table, in pages (synchronous generator)."""
    if table == "spans":
        pages = _paged(
            lambda since: store.list_spans(factory_id, since_ms=max(since, since_ms), limit=_PAGE),
            lambda span: span.started_at,
        )
        for page in pages:
            yield [
                span_row(s)
                for s in page
                if vis.project(s.project_slug) and vis.session(s.session_id)
            ]
    elif table == "events":
        pages = _paged(
            lambda since: store.find_events(factory_id, "*", max(since, since_ms), limit=_PAGE),
            lambda event: event.ts,
        )
        for page in pages:
            yield [
                event_row(e)
                for e in page
                if vis.project(e.project_slug) and vis.session(e.from_id) and vis.session(e.to_id)
            ]
    elif table == "cost":
        yield [
            cost_row(c) for c in store.list_cost_daily(factory_id) if vis.project(c.project_slug)
        ]
    elif table == "decisions":
        rows = store.list_decisions(factory_id=factory_id, limit=100_000, newest_first=False)
        yield [
            decision_row(d)
            for d in rows
            if vis.project(d.project_slug) and vis.session(d.asked_by_session_id)
        ]
    elif table == "prs":
        yield [pr_row(p) for p in store.list_prs(factory_id) if vis.project(p.project_slug)]
    elif table == "demos":
        demos = store.list_demos(factory_id, limit=100_000)
        yield [
            demo_row(d)
            for d in reversed(demos)
            if vis.project(d.project_slug) and vis.session(d.session_id)
        ]


def _next_page(pages: Iterator[list[dict[str, Any]]]) -> list[dict[str, Any]] | None:
    return next(pages, None)


def _csv_line(values: Iterable[Any]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer).writerow(
        json.dumps(v, sort_keys=True) if isinstance(v, dict | list) else v for v in values
    )
    return buffer.getvalue()


async def stream_export(
    store: FactoryStore,
    factory_id: str,
    tables: Iterable[str],
    vis: Visibility,
    *,
    fmt: str = "ndjson",
    since_ms: int = 0,
) -> AsyncIterator[str]:
    """
    Yield the export, table by table, page by page.

    ``ndjson`` lines carry a ``table`` key; ``csv`` gives each table a header
    row whose first cell is ``table``.
    """
    for table in tables:
        if fmt == "csv":
            yield _csv_line(("table", *COLUMNS[table]))
        pages = iter(_rows(store, factory_id, table, vis, since_ms))
        while True:
            page = await asyncio.to_thread(_next_page, pages)
            if page is None:
                break
            out: list[str] = []
            for row in page:
                if fmt == "csv":
                    out.append(_csv_line((table, *(row.get(c) for c in COLUMNS[table]))))
                else:
                    out.append(json.dumps({"table": table, **row}, sort_keys=False) + "\n")
            if out:
                yield "".join(out)


__all__ = [
    "COLUMNS",
    "FORMATS",
    "TABLES",
    "TEXT_KEYS",
    "TRANSCRIPT_KINDS",
    "Visibility",
    "cost_row",
    "decision_row",
    "demo_row",
    "event_row",
    "pr_row",
    "span_row",
    "stream_export",
]
