"""PR spans: the PR itself, one round per head and one span per gate per head.

Each GitHub refresh already carries the PR's commit dates, every review,
verify and demo marker, and the head's checks; :func:`record_refresh` turns
that history into facts (heads with their start, gate verdicts per head) and
:func:`apply_facts` writes them as ``pr``, ``pr_round`` and ``gate`` spans.
Heal builds the same facts from ``gate.changed`` events and the PR row, so
both converge on one row per dedupe key.

A gate span starts when its head was pushed and ends at its first pass (or
its first failure while it never passed); ``attrs.first_pass`` records
whether the head's first verdict passed, and is never rewritten.

Only a sha seen as the PR's head (a refresh's ``headRefOid``, a
``gate.changed`` event) opens a round. Marker, evidence and demo shas are
free text: anything but 7-40 hex is dropped, and a verdict only lands on a
round already recorded for its sha.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Final

from omnigent.entities.factory import FactoryEvent, FactoryPr, FactorySpan
from omnigent.factory_gates import (
    DEMO_MARKER,
    DEMO_PASSING,
    GATE_FAIL,
    GATE_PASS,
    GATE_SKIP,
    REVIEW_MARKER,
    VERIFY_MARKER,
    marker_hits,
    parse_time,
)
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.telemetry.spans import (
    ABANDONED,
    NO_CONTEXT,
    OK,
    OPEN,
    SUPERSEDED,
    SpanContext,
    gate_key,
    pr_key,
    round_key,
    span_context,
    write_span,
)

if TYPE_CHECKING:
    from omnigent.stores.factory_store import FactoryStore

_logger = logging.getLogger(__name__)

GATES: Final[tuple[str, ...]] = ("checks", "review", "verify", "demo")
"""Gates that run per head (the rest are PR properties, not runs)."""
_DEMO_VERDICTS: Final = DEMO_PASSING | {"FAIL"}
_SHA_RE: Final = re.compile(r"[0-9a-f]{7,40}")


@dataclass(frozen=True)
class GateRun:
    """
    One verdict of a gate on a head.

    :param at: Epoch milliseconds.
    """

    gate: str
    sha: str
    at: int
    passed: bool


@dataclass
class PrFacts:
    """
    What is known about a PR's heads and gate runs.

    :param heads: Head sha -> when it was pushed (epoch ms), best known; only
        shas seen as the PR's head.
    :param runs: Gate verdicts per sha (a short sha matches the round it abbreviates).
    :param current_gates: Gate states of the current head (open PRs).
    """

    pr: FactoryPr
    heads: dict[str, int] = field(default_factory=dict)
    runs: list[GateRun] = field(default_factory=list)
    current_gates: dict[str, str | None] = field(default_factory=dict)


def _ms(value: object) -> int | None:
    when = parse_time(value)
    return int(when.timestamp() * 1000) if when is not None else None


def commit_sha(value: object) -> str | None:
    """:returns: *value* as a lowercased 7-40 hex sha, else ``None``."""
    sha = str(value or "").strip().lower()
    return sha if _SHA_RE.fullmatch(sha) else None


def _resolve(sha: str, known: Sequence[str]) -> str:
    """A short sha as the one *known* sha it abbreviates, else unchanged."""
    if sha in known:
        return sha
    found = [k for k in known if k.startswith(sha)]
    return found[0] if len(found) == 1 else sha


def pr_start(pr: FactoryPr) -> int:
    """:returns: When the PR was opened on GitHub (else first tracked), epoch ms."""
    return (pr.opened_at or pr.created_at) * 1000


def _demo_runs(facts: PrFacts, demos: Sequence[Mapping[str, Any]]) -> None:
    for demo in demos:
        status = str(demo.get("status") or "").upper()
        sha = commit_sha(demo.get("head_sha"))
        created = demo.get("created_at")
        if sha and status in _DEMO_VERDICTS and isinstance(created, int):
            facts.runs.append(GateRun("demo", sha, created * 1000, status in DEMO_PASSING))


def facts_from_refresh(
    pr: FactoryPr,
    pr_json: Mapping[str, Any],
    gates: Mapping[str, Any],
    demos: Sequence[Mapping[str, Any]] = (),
    *,
    now: int,
) -> PrFacts:
    """
    Facts from one refresh: commit dates, marker history, recorded demos, head checks.

    :param pr: The PR row after the refresh.
    :param pr_json: The refresh in the ``gh pr view`` shape (``normalize_pr``).
    :param gates: The gates just evaluated for the head.
    :param demos: Recorded demos as ``{head_sha, status, created_at}``.
    :param now: Epoch seconds of the refresh.
    """
    now_ms = now * 1000
    commits: dict[str, int] = {}
    for commit in pr_json.get("commits") or []:
        if not isinstance(commit, Mapping):
            continue
        oid, at = commit_sha(commit.get("oid")), _ms(commit.get("committedDate"))
        if oid and at is not None:
            commits[oid] = at
    head = commit_sha(pr_json.get("headRefOid")) or commit_sha(pr.head_sha)
    facts = PrFacts(pr=pr)
    if head:
        facts.heads[head] = commits.get(head, now_ms)
    for gate, pattern, attr in (
        ("review", REVIEW_MARKER, None),
        ("verify", VERIFY_MARKER, None),
        ("demo", DEMO_MARKER, "status"),
    ):
        for hit in marker_hits(pr_json, pattern, outcome_attr=attr):
            sha = commit_sha(hit.sha)
            if sha is None or (gate == "demo" and hit.outcome not in _DEMO_VERDICTS):
                continue
            passed = hit.outcome in (DEMO_PASSING if gate == "demo" else {"APPROVE", "PASS"})
            facts.runs.append(GateRun(gate, sha, int(hit.at.timestamp() * 1000), passed))
    _demo_runs(facts, demos)
    if head and pr.state == "open":
        facts.current_gates = {g: gates.get(g) for g in GATES}
        checks = gates.get("checks")
        if checks in (GATE_PASS, GATE_FAIL):
            facts.runs.append(GateRun("checks", head, now_ms, checks == GATE_PASS))
    return facts


def facts_from_history(
    pr: FactoryPr,
    events: Sequence[FactoryEvent],
    demos: Sequence[Mapping[str, Any]] = (),
    *,
    now: int,
) -> PrFacts:
    """
    Facts heal can rebuild without GitHub: ``gate.changed`` events, the PR row's evidence, demos.

    :param events: The PR's ``gate.changed`` events, oldest first.
    :param demos: Recorded demos as ``{head_sha, status, created_at}``.
    :param now: Epoch seconds.
    """
    facts = PrFacts(pr=pr)
    head = commit_sha(pr.head_sha)
    checks_at: int | None = None
    for event in events:
        data = event.data if isinstance(event.data, dict) else {}
        sha = commit_sha(data.get("head_sha"))
        if event.kind != "gate.changed" or sha is None:
            continue
        facts.heads.setdefault(sha, event.ts)
        gate, to = data.get("gate"), data.get("to")
        if gate in GATES and to in (GATE_PASS, GATE_FAIL):
            facts.runs.append(GateRun(str(gate), sha, event.ts, to == GATE_PASS))
            if gate == "checks" and sha == head:
                checks_at = event.ts
    gates = pr.gates or {}
    evidence = gates.get("evidence") if isinstance(gates.get("evidence"), Mapping) else {}
    for gate, key, passing in (
        ("review", "verdict", frozenset({"APPROVE"})),
        ("verify", "result", frozenset({"PASS"})),
        ("demo", "status", DEMO_PASSING),
    ):
        found = evidence.get(gate)
        if not isinstance(found, Mapping) or not isinstance(found.get("at"), int):
            continue
        sha = commit_sha(found.get("sha"))
        verdict = str(found.get(key) or "").upper()
        if sha is None or (gate == "demo" and verdict not in _DEMO_VERDICTS):
            continue
        facts.runs.append(GateRun(gate, sha, int(found["at"]) * 1000, verdict in passing))
    _demo_runs(facts, demos)
    if head:
        facts.heads.setdefault(head, pr_start(pr))
    if head and pr.state == "open":
        facts.current_gates = {g: gates.get(g) for g in GATES}
        checks = gates.get("checks")
        if checks in (GATE_PASS, GATE_FAIL):
            at = checks_at or (pr.last_polled_at or now) * 1000
            facts.runs.append(GateRun("checks", head, at, checks == GATE_PASS))
    return facts


@dataclass(frozen=True)
class _Round:
    sha: str
    start: int
    end: int | None
    outcome: str


def _rounds(
    pr: FactoryPr, heads: Mapping[str, int], existing: Mapping[str, FactorySpan]
) -> list[_Round]:
    opened = pr_start(pr)
    starts = {sha: span.started_at for sha, span in existing.items()}
    latest = max(starts.values(), default=opened)
    head = (pr.head_sha or "").lower()
    for sha, at in heads.items():
        if sha in starts:
            continue
        start = max(at, opened)
        if sha == head:
            # The current head was pushed after every round already recorded.
            start = max(start, latest + 1)
        starts[sha] = start
    ordered = sorted(starts.items(), key=lambda item: (item[1], item[0]))
    if ordered and not existing:
        # The first head is what the PR was opened with.
        ordered[0] = (ordered[0][0], opened)
    if head in starts and ordered and ordered[-1][0] != head:
        # A force-push back to an older head: it is the latest round again.
        ordered = [item for item in ordered if item[0] != head] + [(head, starts[head])]
    rounds: list[_Round] = []
    for index, (sha, start) in enumerate(ordered):
        if index + 1 < len(ordered):
            rounds.append(_Round(sha, start, ordered[index + 1][1], SUPERSEDED))
        elif pr.state == "merged" and pr.merged_at:
            rounds.append(_Round(sha, start, pr.merged_at * 1000, OK))
        elif pr.state == "closed" and pr.closed_at:
            rounds.append(_Round(sha, start, pr.closed_at * 1000, ABANDONED))
        else:
            rounds.append(_Round(sha, start, None, OPEN))
    return rounds


def _gate_target(
    runs: list[GateRun],
    existing: FactorySpan | None,
    round_: _Round,
) -> tuple[str, int | None, bool | None]:
    """:returns: ``(outcome, ended_at, first_pass)`` of one gate on one head."""
    stored = (existing.attrs or {}).get("first_pass") if existing is not None else None
    first_pass = stored if isinstance(stored, bool) else (runs[0].passed if runs else None)
    passes = [r.at for r in runs if r.passed]
    if existing is not None and existing.outcome == OK:
        end = existing.ended_at if existing.ended_at is not None else (passes or [round_.start])[0]
        return OK, min([end, *passes[:1]]), first_pass
    if passes:
        return OK, passes[0], first_pass
    if existing is not None and existing.outcome == "fail" and existing.ended_at is not None:
        return "fail", existing.ended_at, first_pass
    if runs:
        return "fail", runs[0].at, first_pass
    if round_.outcome != OPEN:
        return (SUPERSEDED if round_.outcome == SUPERSEDED else ABANDONED), round_.end, first_pass
    return OPEN, None, first_pass


def _unchanged(
    span: FactorySpan | None,
    outcome: str,
    end: int | None,
    attrs: Mapping[str, Any],
    parent: str | None,
) -> bool:
    if span is None:
        return False
    current = span.attrs or {}
    return (
        span.outcome == outcome
        and span.ended_at == end
        and all(current.get(k) == v for k, v in attrs.items())
        and (parent is None or span.parent_id == parent)
    )


def apply_facts(
    store: FactoryStore,
    facts: PrFacts,
    ctx: SpanContext = NO_CONTEXT,
    *,
    keep_closed_after: int | None = None,
) -> None:
    """
    Write the pr, pr_round and gate spans *facts* imply (synchronous, idempotent).

    :param keep_closed_after: Heal's read time (epoch ms): spans closed later are left alone.
    """
    pr = facts.pr
    fid = pr.factory_id
    common: dict[str, Any] = {"pr_id": pr.id, "project_slug": pr.project_slug}
    if pr.state == "merged" and pr.merged_at:
        pr_end, pr_outcome = pr.merged_at * 1000, OK
    elif pr.state == "closed" and pr.closed_at:
        pr_end, pr_outcome = pr.closed_at * 1000, ABANDONED
    else:
        pr_end, pr_outcome = None, OPEN
    pr_span = write_span(
        store,
        fid,
        kind="pr",
        name=f"#{pr.number}",
        dedupe_key=pr_key(pr.id),
        started_at=pr_start(pr),
        ended_at=pr_end,
        outcome=pr_outcome,
        force=True,
        reopen=True,
        keep_closed_after=keep_closed_after,
        ctx=ctx,
        attrs={
            "repo": pr.repo,
            "number": pr.number,
            "task": pr.task,
            "base_ref": pr.base_ref or None,
        },
        **common,
    )
    spans = store.list_spans(fid, kinds=["pr_round", "gate"], pr_id=pr.id, limit=2000)
    rounds_by_sha = {s.head_sha: s for s in spans if s.kind == "pr_round" and s.head_sha}
    gates_by_key = {s.dedupe_key: s for s in spans if s.kind == "gate"}
    head = commit_sha(pr.head_sha)
    heads = {sha: at for sha, at in facts.heads.items() if commit_sha(sha) == sha}
    rounds = _rounds(pr, heads, rounds_by_sha)
    shas = [r.sha for r in rounds]
    runs = [replace(run, sha=_resolve(run.sha, shas)) for run in facts.runs]
    for index, round_ in enumerate(rounds, start=1):
        existing = rounds_by_sha.get(round_.sha)
        attrs = {"round": index}
        round_span = existing
        if not _unchanged(existing, round_.outcome, round_.end, attrs, pr_span.id):
            round_span = write_span(
                store,
                fid,
                kind="pr_round",
                name=f"r{index} {round_.sha[:7]}",
                dedupe_key=round_key(pr.id, round_.sha),
                started_at=round_.start,
                ended_at=round_.end,
                outcome=round_.outcome,
                force=True,
                reopen=True,
                keep_closed_after=keep_closed_after,
                overwrite=("parent_id",),
                ctx=ctx,
                attrs=attrs,
                parent_id=pr_span.id,
                head_sha=round_.sha,
                **common,
            )
        assert round_span is not None
        for gate in GATES:
            mine = sorted(
                (r for r in runs if r.gate == gate and r.sha == round_.sha),
                key=lambda r: r.at,
            )
            key = gate_key(pr.id, round_.sha, gate)
            existing_gate = gates_by_key.get(key)
            state = facts.current_gates.get(gate) if round_.sha == head else None
            evaluated = state is not None and state != GATE_SKIP
            if existing_gate is None and not mine and not evaluated:
                continue
            outcome, end, first_pass = _gate_target(mine, existing_gate, round_)
            gate_attrs: dict[str, Any] = {"gate": gate, "first_pass": first_pass}
            if _unchanged(existing_gate, outcome, end, gate_attrs, round_span.id):
                continue
            write_span(
                store,
                fid,
                kind="gate",
                name=gate,
                dedupe_key=key,
                started_at=round_.start,
                ended_at=end,
                outcome=outcome,
                force=True,
                reopen=True,
                keep_closed_after=keep_closed_after,
                overwrite=("parent_id",),
                set_once=("first_pass",),
                ctx=ctx,
                attrs=gate_attrs,
                parent_id=round_span.id,
                head_sha=round_.sha,
                **common,
            )


async def record_refresh(
    app_state: Any,
    pr: FactoryPr,
    pr_json: Mapping[str, Any],
    gates: Mapping[str, Any],
    demos: Iterable[Mapping[str, Any]] = (),
    *,
    now: int,
) -> None:
    """The ``prs.apply_refresh`` hook: write this refresh's PR spans. Never raises."""
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return
    try:
        facts = facts_from_refresh(pr, pr_json, gates, list(demos), now=now)

        def _write() -> None:
            apply_facts(deps.factory_store, facts, span_context(deps, pr.factory_id))

        await asyncio.to_thread(_write)
    except Exception:  # noqa: BLE001 - heal rebuilds what a failed write missed
        _logger.warning("factory spans: could not record the refresh of %s", pr.id, exc_info=True)


__all__ = [
    "GATES",
    "GateRun",
    "PrFacts",
    "apply_facts",
    "commit_sha",
    "facts_from_history",
    "facts_from_refresh",
    "pr_start",
    "record_refresh",
]
