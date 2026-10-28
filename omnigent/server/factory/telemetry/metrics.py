"""The factory's metric catalog: one explicit definition per metric, always with n.

Metrics are computed on demand from the tables, spans, the cost ledger and the
event log, with a 5-minute cache. Each metric turns the facts of a window into
*samples* (one per PR, decision, loop, …, carrying its project, role, agent,
person, platform version and definition version) and aggregates them, so every
metric can be grouped by ``day | project | role | agent | person | version |
def_version`` the same way. Definitions are Tier 0: code only.

The nightly human-prompt scan (:func:`scan_human_prompts`) counts the
human-authored conversation items (``created_by``) of the factory's members and
records one ``metrics.prompts`` event per factory and day.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    LABEL_ID,
    LABEL_ROLE,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    THREAD_FACTORY,
    THREAD_IMPROVEMENTS,
)

if TYPE_CHECKING:
    from omnigent.entities.factory import (
        FactoryChatMessage,
        FactoryDecision,
        FactoryEvent,
        FactoryPr,
        FactoryProject,
        FactorySpan,
    )
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

CACHE_S: Final = 300.0
DAY_S: Final = 86400
DEFAULT_WINDOW: Final = "7d"
MAX_WINDOW_S: Final = 90 * DAY_S
GROUP_KEYS: Final = ("day", "project", "role", "agent", "person", "version", "def_version")
OWNER_ONLY_GROUPS: Final = frozenset({"person"})
DAISY_BAND: Final = (30, 50)
"""Human prompts per day the reference operator works with (60/35/5 lead/PL/other)."""
PROMPT_SCAN_KIND: Final = "metrics.prompts"
UNNEEDED_FAST_S: Final = 120
_EVENT_LIMIT: Final = 20000
_SPAN_LIMIT: Final = 20000
_SCAN_ITEMS: Final = 500


# ── Windows ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Window:
    """``[since, until)`` in epoch seconds."""

    since: int
    until: int

    @property
    def seconds(self) -> int:
        return max(1, self.until - self.since)

    @property
    def days(self) -> float:
        return self.seconds / DAY_S

    def holds(self, ts: float | None) -> bool:
        return ts is not None and self.since <= ts < self.until

    def previous(self) -> Window:
        """:returns: The same-length window right before this one."""
        return Window(self.since - self.seconds, self.since)


def parse_window(raw: str | None, now: float | None = None) -> Window:
    """
    :param raw: e.g. ``"24h"``, ``"7d"`` or ``"30d"`` (default 7d, at most 90d).
    :raises ValueError: On anything else.
    """
    text = (raw or DEFAULT_WINDOW).strip().lower()
    unit = text[-1:]
    try:
        amount = float(text[:-1])
    except ValueError:
        raise ValueError(f"window must look like 24h or 7d, not {raw!r}") from None
    scale = {"h": 3600, "d": DAY_S, "m": 60}.get(unit)
    if scale is None or amount <= 0 or amount * scale > MAX_WINDOW_S:
        raise ValueError(f"window must look like 24h or 7d (at most 90d), not {raw!r}")
    # Until the end of the current second, so what just happened counts.
    until = int(now if now is not None else time.time()) + 1
    return Window(until - int(amount * scale), until)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")


# ── Facts ──────────────────────────────────────────────────────


@dataclass
class Facts:
    """Everything the catalog reads for one factory, loaded once per request."""

    factory_id: str
    window: Window
    projects: list[FactoryProject] = field(default_factory=list)
    prs: list[FactoryPr] = field(default_factory=list)
    decisions: list[FactoryDecision] = field(default_factory=list)
    tasks: list[Any] = field(default_factory=list)
    loops: list[Any] = field(default_factory=list)
    issues: list[Any] = field(default_factory=list)
    feedback: list[Any] = field(default_factory=list)
    cost: list[Any] = field(default_factory=list)
    events: list[FactoryEvent] = field(default_factory=list)
    chat: list[FactoryChatMessage] = field(default_factory=list)
    proposals: list[Any] = field(default_factory=list)
    spans: list[FactorySpan] = field(default_factory=list)
    roles: dict[str, str] = field(default_factory=dict)
    """Member session id → factory role."""
    titles: dict[str, str] = field(default_factory=dict)

    def kind(self, *kinds: str) -> list[FactoryEvent]:
        """:returns: Events of these kinds (``prefix.*`` allowed), oldest first."""
        out = []
        for event in self.events:
            for kind in kinds:
                if event.kind == kind or (
                    kind.endswith(".*") and event.kind.startswith(kind[:-1])
                ):
                    out.append(event)
                    break
        return out

    def project(self, slug: str | None) -> FactoryProject | None:
        return next((p for p in self.projects if p.slug == slug), None)

    def pr(self, pr_id: str | None) -> FactoryPr | None:
        return next((p for p in self.prs if pr_id and _same(p.id, pr_id)), None)


def _same(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return a.replace("-", "").lower() == b.replace("-", "").lower()


def load_facts(deps: FactoryDeps, factory_id: str, window: Window) -> Facts:
    """Read what the metrics of *window* (and the window before it) need. Synchronous."""
    from omnigent.server.factory.membership import factory_members

    store = deps.factory_store
    since = window.since - window.seconds
    since_ms = since * 1000
    facts = Facts(factory_id=factory_id, window=window)
    facts.projects = store.list_projects(factory_id)
    facts.prs = store.list_prs(factory_id)
    facts.decisions = store.list_decisions(factory_id=factory_id, limit=5000, newest_first=False)
    facts.tasks = store.list_tasks(factory_id)
    facts.loops = store.list_loops(factory_id)
    facts.issues = store.list_issues(factory_id, limit=5000)
    facts.feedback = store.list_feedback(factory_id, since=since, limit=5000)
    facts.cost = store.list_cost_daily(
        factory_id, since_day=_day(since), until_day=_day(window.until)
    )
    facts.events = store.find_events(factory_id, list(_EVENT_KINDS), since_ms, limit=_EVENT_LIMIT)
    facts.chat = [m for m in store.list_chat(factory_id, limit=5000) if m.created_at >= since]
    facts.proposals = store.list_proposals(factory_id, limit=500)
    facts.spans = store.list_spans(
        factory_id, kinds=list(_SPAN_KINDS), since_ms=since_ms, limit=_SPAN_LIMIT
    )
    for member in factory_members(factory_id, deps.conversation_store):
        facts.roles[member.id] = member.labels.get(LABEL_ROLE) or ""
        facts.titles[member.id] = member.title or member.id
    return facts


_EVENT_KINDS: Final = (
    "pr.claim",
    "pr.human_review",
    "decision.escalated",
    "decision.answered",
    "message.queued",
    "message.delivered",
    "message.bounced",
    "dispatch",
    "task.failed",
    "engine.next",
    "heartbeat.delivered",
    "heartbeat.skipped",
    "control.*",
    "loop.budget_raised",
    "preview.sha_mismatch",
    PROMPT_SCAN_KIND,
)
_SPAN_KINDS: Final = ("task", "stage", "gate", "env", "preview")


# ── Samples and aggregation ────────────────────────────────────


@dataclass(frozen=True)
class Sample:
    """
    One observation of a metric.

    :param value: The observed number (seconds, dollars, 1/0 for shares).
    :param key: A category for mixes and per-kind breakdowns (e.g. a stage or gate).
    """

    ts: float
    value: float
    key: str | None = None
    project: str | None = None
    role: str | None = None
    agent: str | None = None
    person: str | None = None
    version: str | None = None
    def_version: int | None = None

    def group(self, by: str) -> str:
        """:returns: This sample's value of a ``group_by`` key (``""`` when it has none)."""
        if by == "day":
            return _day(self.ts)
        found = getattr(self, by, None)
        return "" if found is None else str(found)


def _pct(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


Agg = Callable[[list[Sample], Window], dict[str, Any]]


def agg_p50p90(samples: list[Sample], _window: Window) -> dict[str, Any]:
    values = [s.value for s in samples]
    return {"p50": _r(_pct(values, 0.5)), "p90": _r(_pct(values, 0.9)), "n": len(values)}


def agg_share(samples: list[Sample], _window: Window) -> dict[str, Any]:
    n = len(samples)
    return {"value": _r(sum(s.value for s in samples) / n, 4) if n else None, "n": n}


def agg_sum(samples: list[Sample], _window: Window) -> dict[str, Any]:
    return {"value": _r(sum(s.value for s in samples), 4), "n": len(samples)}


def agg_count(samples: list[Sample], _window: Window) -> dict[str, Any]:
    return {"value": len(samples), "n": len(samples)}


def agg_mean(samples: list[Sample], _window: Window) -> dict[str, Any]:
    values = [s.value for s in samples]
    return {"value": _r(statistics.fmean(values)) if values else None, "n": len(values)}


def agg_per_day(samples: list[Sample], window: Window) -> dict[str, Any]:
    total = sum(s.value for s in samples)
    return {"value": _r(total / window.days), "total": _r(total), "n": len(samples)}


def by_key(inner: Agg) -> Agg:
    """:returns: An aggregation per sample key, plus the overall n."""

    def _agg(samples: list[Sample], window: Window) -> dict[str, Any]:
        keys: dict[str, list[Sample]] = defaultdict(list)
        for sample in samples:
            keys[sample.key or ""].append(sample)
        return {
            "by": {k: inner(v, window) for k, v in sorted(keys.items())},
            "n": len(samples),
        }

    return _agg


def agg_mix(samples: list[Sample], _window: Window) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for sample in samples:
        counts[sample.key or ""] += 1
    n = len(samples)
    return {
        "mix": {k: {"count": c, "share": _r(c / n, 4)} for k, c in sorted(counts.items())},
        "n": n,
    }


# ── The catalog ────────────────────────────────────────────────


@dataclass(frozen=True)
class Metric:
    """
    One catalog entry.

    :param definition: The exact definition (shown in the cockpit and the API).
    :param samples: Facts → samples inside the window (ratio metrics may add extras).
    :param extra: Facts, samples → extra fields of the overall value (not grouped).
    """

    name: str
    family: str
    title: str
    unit: str
    definition: str
    source: str
    samples: Callable[[Facts, Window], list[Sample]]
    agg: Agg
    extra: Callable[[Facts, Window, list[Sample]], dict[str, Any]] | None = None


def _pr_sample(facts: Facts, pr: FactoryPr, ts: float, value: float, **kw: Any) -> Sample:
    project = facts.project(pr.project_slug)
    return Sample(
        ts=ts,
        value=value,
        project=pr.project_slug,
        version=pr.version_id,
        def_version=project.def_version if project is not None else None,
        **kw,
    )


def _merged_in(facts: Facts, w: Window) -> list[FactoryPr]:
    return [p for p in facts.prs if p.state == "merged" and w.holds(p.merged_at)]


def _lead_time(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for project in facts.projects:
        merged = sorted(
            (p for p in facts.prs if p.project_slug == project.slug and p.merged_at),
            key=lambda p: p.merged_at or 0,
        )
        if merged and w.holds(merged[0].merged_at) and project.created_at:
            first = merged[0]
            value = float((first.merged_at or 0) - project.created_at)
            out.append(_pr_sample(facts, first, first.merged_at or 0, value))
    return out


def _time_to_first_pr(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for project in facts.projects:
        opened = [
            (p.opened_at or p.created_at, p) for p in facts.prs if p.project_slug == project.slug
        ]
        if not opened or not project.created_at:
            continue
        at, pr = min(opened, key=lambda x: x[0])
        if w.holds(at):
            out.append(_pr_sample(facts, pr, at, float(max(0, at - project.created_at))))
    return out


def _task_cycle(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for task in facts.tasks:
        pr = facts.pr(task.pr_id)
        ready = pr.first_ready_at if pr is not None else None
        if task.started_at and ready and w.holds(ready):
            out.append(
                Sample(
                    ts=ready,
                    value=float(max(0, ready - task.started_at)),
                    project=task.project_slug,
                    def_version=task.def_version,
                    version=pr.version_id if pr is not None else None,
                )
            )
    return out


def _span_samples(facts: Facts, w: Window, kind: str, *, key_attr: str = "name") -> list[Sample]:
    out = []
    for span in facts.spans:
        if span.kind != kind or span.ended_at is None or not w.holds(span.ended_at / 1000):
            continue
        out.append(
            Sample(
                ts=span.ended_at / 1000,
                value=max(0.0, (span.ended_at - span.started_at) / 1000),
                key=getattr(span, key_attr, None),
                project=span.project_slug,
                role=span.role,
                agent=span.agent_name,
                version=span.version_id,
                def_version=span.def_version,
            )
        )
    return out


def _stage_dwell(facts: Facts, w: Window) -> list[Sample]:
    return _span_samples(facts, w, "stage")


def _human_wait(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for pr in _merged_in(facts, w):
        opened, ready, merged = pr.opened_at or pr.created_at, pr.first_ready_at, pr.merged_at
        if ready and opened:
            out.append(
                _pr_sample(facts, pr, merged or 0, float(max(0, ready - opened)), key="agent")
            )
        if ready and merged:
            out.append(
                _pr_sample(facts, pr, merged, float(max(0, merged - ready)), key="human_merge")
            )
    for d in facts.decisions:
        if d.blocking and d.answered_at and w.holds(d.answered_at):
            out.append(
                Sample(
                    ts=d.answered_at,
                    value=float(max(0, d.answered_at - d.created_at)),
                    key="decision",
                    project=d.project_slug,
                    person=d.answered_by,
                )
            )
    return out


def _throughput(facts: Facts, w: Window) -> list[Sample]:
    return [_pr_sample(facts, p, p.merged_at or 0, 1.0) for p in _merged_in(facts, w)]


def _wip_extra(facts: Facts, _w: Window, _s: list[Sample]) -> dict[str, Any]:
    from omnigent.server.factory.previews import preview_is_live

    open_prs = [p for p in facts.prs if p.state == "open"]
    stages: dict[str, int] = defaultdict(int)
    for project in facts.projects:
        if project.status == "active":
            stages[project.stage] += 1
    return {
        "open_prs": len(open_prs),
        "live_previews": sum(1 for p in open_prs if preview_is_live(p.preview)),
        "projects_by_stage": dict(stages),
    }


def _task_spans(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for span in facts.spans:
        if span.kind != "task":
            continue
        start = span.started_at / 1000
        end = (span.ended_at / 1000) if span.ended_at else w.until
        if end <= w.since or start >= w.until:
            continue
        out.append(
            Sample(
                ts=max(start, w.since),
                value=min(end, w.until) - max(start, w.since),
                project=span.project_slug,
                role=span.role,
                agent=span.agent_name,
                version=span.version_id,
                def_version=span.def_version,
                key=f"{start}:{end}",
            )
        )
    return out


def _agg_parallelism(samples: list[Sample], window: Window) -> dict[str, Any]:
    edges: list[tuple[float, int]] = []
    for sample in samples:
        start, end = (float(x) for x in (sample.key or "0:0").split(":"))
        edges += [(max(start, window.since), 1), (min(end, window.until), -1)]
    peak = level = 0
    for _, delta in sorted(edges, key=lambda e: (e[0], e[1])):
        level += delta
        peak = max(peak, level)
    busy = sum(s.value for s in samples)
    return {"peak": peak, "mean": _r(busy / window.seconds), "n": len(samples)}


def _critical_path(facts: Facts, w: Window) -> list[Sample]:
    from omnigent.server.factory.graph import critical_path, weight_min
    from omnigent.server.factory.tasks import node_of

    out = []
    by_project: dict[str, list[Any]] = defaultdict(list)
    for task in facts.tasks:
        by_project[task.project_slug].append(task)
    for slug, rows in by_project.items():
        done = [t for t in rows if t.state == "done" and t.started_at and t.closed_at]
        if not done or len(done) != len([t for t in rows if t.state != "cancelled"]):
            continue
        end = max(t.closed_at for t in done)
        if not w.holds(end) or not any(t.estimate_min for t in done):
            continue
        nodes = [replace(node_of(t), state="planned") for t in done]
        # The estimate as planned: card estimates only (no observed p50s).
        _, estimated = critical_path(nodes, lambda n: weight_min(n, {}))
        actual = (end - min(t.started_at for t in done)) / 60
        if estimated > 0:
            out.append(Sample(ts=end, value=actual / estimated, project=slug))
    return out


def _loop_ended(facts: Facts, w: Window) -> list[Any]:
    return [
        lp
        for lp in facts.loops
        if (lp.ended_at and w.holds(lp.ended_at))
        or (lp.state in ("exhausted", "stalled", "thrashing") and w.holds(lp.updated_at))
    ]


def _loop_sample(lp: Any, value: float, key: str | None) -> Sample:
    return Sample(
        ts=float(lp.ended_at or lp.updated_at),
        value=value,
        key=key,
        project=lp.project_slug,
        def_version=lp.def_version,
    )


def _iterations(facts: Facts, w: Window) -> list[Sample]:
    return [
        _loop_sample(lp, float(lp.iteration), lp.kind)
        for lp in _loop_ended(facts, w)
        if lp.state == "converged"
    ]


def _exit_mix(facts: Facts, w: Window) -> list[Sample]:
    return [_loop_sample(lp, 1.0, lp.state) for lp in _loop_ended(facts, w)]


def _exhaustion(facts: Facts, w: Window) -> list[Sample]:
    return [
        _loop_sample(lp, 1.0 if lp.state == "exhausted" else 0.0, lp.kind)
        for lp in _loop_ended(facts, w)
    ]


def _first_pass(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for span in facts.spans:
        attrs = span.attrs or {}
        if span.kind != "gate" or not isinstance(attrs.get("first_pass"), bool):
            continue
        if not w.holds(span.started_at / 1000):
            continue
        out.append(
            Sample(
                ts=span.started_at / 1000,
                value=1.0 if attrs["first_pass"] else 0.0,
                key=span.name,
                project=span.project_slug,
                version=span.version_id,
                def_version=span.def_version,
            )
        )
    return out


def _changes_requested(facts: Facts, pr: FactoryPr, after: float | None = None) -> list[Any]:
    """Human CHANGES_REQUESTED reviews of *pr* (cockpit or GitHub), at or after *after*."""
    subject = f"pr:{pr.repo}#{pr.number}"
    out = []
    for event in facts.kind("pr.human_review"):
        data = event.data or {}
        if data.get("verdict") != "changes_requested":
            continue
        if not (_same(data.get("pr_id"), pr.id) or event.subject == subject):
            continue
        if after is None or event.ts / 1000 >= after:
            out.append(event)
    return out


def _review_rounds(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for pr in _merged_in(facts, w):
        failed = sum(
            1
            for s in facts.spans
            if s.kind == "gate"
            and s.name == "review"
            and s.outcome == "fail"
            and _same(s.pr_id, pr.id)
        )
        human = len(_changes_requested(facts, pr))
        out.append(_pr_sample(facts, pr, pr.merged_at or 0, float(failed + human)))
    return out


def _budget_raises(facts: Facts, w: Window) -> list[Sample]:
    return [
        Sample(
            ts=e.ts / 1000,
            value=1.0,
            key=str((e.data or {}).get("kind") or ""),
            project=e.project_slug,
        )
        for e in facts.kind("loop.budget_raised")
        if w.holds(e.ts / 1000)
    ]


def _acceptance(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for pr in facts.prs:
        if pr.state == "merged" and w.holds(pr.merged_at):
            out.append(_pr_sample(facts, pr, pr.merged_at or 0, 1.0))
        elif pr.state == "closed" and w.holds(pr.closed_at):
            out.append(_pr_sample(facts, pr, pr.closed_at or 0, 0.0))
    return out


def _closure_mix(facts: Facts, w: Window, _s: list[Sample]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for fb in facts.feedback:
        if fb.kind == "closure" and w.holds(fb.created_at):
            for tag in fb.tags or ["unknown"]:
                counts[tag] += 1
    return {"closure_reasons": dict(sorted(counts.items()))}


def _escape(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for pr in facts.prs:
        if not w.holds(pr.first_ready_at):
            continue
        escaped = bool(_changes_requested(facts, pr, after=pr.first_ready_at))
        out.append(_pr_sample(facts, pr, pr.first_ready_at or 0, 1.0 if escaped else 0.0))
    return out


def _sentinel_agreement(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for pr in facts.prs:
        verdict = (pr.sentinel or {}).get("verdict")
        end = pr.merged_at if pr.state == "merged" else pr.closed_at
        if verdict != "verified" or pr.state == "open" or not w.holds(end):
            continue
        at = (pr.sentinel or {}).get("at")
        clean = pr.state == "merged" and not _changes_requested(
            facts, pr, after=float(at) if isinstance(at, int | float) else None
        )
        out.append(_pr_sample(facts, pr, end or 0, 1.0 if clean else 0.0))
    return out


def _claims(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for e in facts.kind("pr.claim"):
        data = e.data or {}
        if data.get("claim") != "ready_for_human_merge" or not w.holds(e.ts / 1000):
            continue
        out.append(
            Sample(
                ts=e.ts / 1000,
                value=1.0 if data.get("claim_mismatch") else 0.0,
                project=e.project_slug,
                agent=facts.titles.get(e.from_id or "", e.from_id),
                role=facts.roles.get(e.from_id or ""),
            )
        )
    return out


def _ratings(facts: Facts, w: Window) -> list[Sample]:
    return [
        Sample(
            ts=fb.created_at,
            value=float(fb.rating),
            key=fb.target_kind,
            project=fb.project_slug,
            person=fb.author if fb.author_kind == "human" else None,
            def_version=fb.def_version,
        )
        for fb in facts.feedback
        if fb.kind in ("rating", "useful") and fb.rating is not None and w.holds(fb.created_at)
    ]


def _ratings_extra(_facts: Facts, _w: Window, samples: list[Sample]) -> dict[str, Any]:
    dist: dict[str, int] = defaultdict(int)
    for s in samples:
        if s.key != "demo":
            dist[str(int(s.value))] += 1
    useful = [s.value for s in samples if s.key == "demo"]
    return {
        "distribution": dict(sorted(dist.items())),
        "demo_useful": _r(sum(1 for v in useful if v > 0) / len(useful), 4) if useful else None,
    }


def _thread_role(thread: str) -> str:
    if thread in (THREAD_FACTORY, THREAD_IMPROVEMENTS):
        return ROLE_LEAD
    if thread.startswith(("project:", "pr:")):
        return ROLE_PROJECT_LEAD
    return "other"


def _prompts(facts: Facts, w: Window) -> list[Sample]:
    out = [
        Sample(ts=m.created_at, value=1.0, role=_thread_role(m.thread), person=m.author)
        for m in facts.chat
        if m.author_kind == "human" and w.holds(m.created_at)
    ]
    for e in facts.kind(PROMPT_SCAN_KIND):
        data = e.data or {}
        day = data.get("day")
        try:
            ts = datetime.strptime(str(day), "%Y-%m-%d").replace(tzinfo=UTC).timestamp() + 43200
        except ValueError:
            continue
        if not w.holds(ts):
            continue
        for row in data.get("counts") or []:
            if isinstance(row, Mapping) and isinstance(row.get("n"), int):
                out.append(
                    Sample(
                        ts=ts,
                        value=float(row["n"]),
                        role=str(row.get("role") or "other"),
                        person=row.get("person"),
                        project=row.get("project"),
                    )
                )
    return out


def _prompts_extra(_facts: Facts, w: Window, samples: list[Sample]) -> dict[str, Any]:
    total = sum(s.value for s in samples) or 0.0
    split: dict[str, float] = defaultdict(float)
    for s in samples:
        bucket = s.role if s.role in (ROLE_LEAD, ROLE_PROJECT_LEAD) else "other"
        split[bucket] += s.value
    shares = {k: _r(v / total, 4) if total else None for k, v in sorted(split.items())}
    per_day = total / w.days
    lo, hi = DAISY_BAND
    return {
        "split": shares,
        "band": [lo, hi],
        "in_band": lo <= per_day <= hi if total else None,
    }


def _human_acts(facts: Facts, w: Window) -> list[Sample]:
    out = [
        Sample(
            ts=e.ts / 1000,
            value=1.0,
            key=e.kind,
            project=e.project_slug,
            person=(e.data or {}).get("by"),
        )
        for e in facts.kind("control.*", "pr.human_review")
        if w.holds(e.ts / 1000) and (e.data or {}).get("by_kind", "human") == "human"
    ]
    out += [
        Sample(
            ts=d.answered_at or 0,
            value=1.0,
            key="decision",
            project=d.project_slug,
            person=d.answered_by,
        )
        for d in facts.decisions
        if d.answered_at
        and w.holds(d.answered_at)
        and d.answered_by not in (None, "server", "eval-auto")
    ]
    out += [
        Sample(ts=m.created_at, value=1.0, key="chat", person=m.author)
        for m in facts.chat
        if m.author_kind == "human" and w.holds(m.created_at)
    ]
    return out


def _per_merged(facts: Facts, w: Window, samples: list[Sample]) -> dict[str, Any]:
    merged = len(_merged_in(facts, w))
    total = sum(s.value for s in samples)
    return {"value": _r(total / merged) if merged else None, "acts": total, "merged": merged}


def _decision_latency(facts: Facts, w: Window) -> list[Sample]:
    return [
        Sample(
            ts=d.answered_at or 0,
            value=float(max(0, (d.answered_at or 0) - d.created_at)),
            project=d.project_slug,
            person=d.answered_by,
            key=d.source,
        )
        for d in facts.decisions
        if d.answered_at and w.holds(d.answered_at)
    ]


def _escalation_extra(facts: Facts, w: Window, _s: list[Sample]) -> dict[str, Any]:
    created = [d for d in facts.decisions if w.holds(d.created_at)]
    escalated = {
        (e.data or {}).get("decision_id")
        for e in facts.kind("decision.escalated")
        if w.holds(e.ts / 1000)
    }
    return {
        "escalation_rate": _r(len(escalated) / len(created), 4) if created else None,
        "opened": len(created),
    }


def _unneeded(facts: Facts, w: Window) -> list[Sample]:
    flagged = {fb.target_id for fb in facts.feedback if fb.kind == "unneeded"}
    out = []
    for d in facts.decisions:
        if not d.answered_at or not w.holds(d.answered_at):
            continue
        option = next((o for o in d.options if o.get("id") == d.answer_option_id), None)
        fast = (
            option is not None
            and bool(option.get("recommended"))
            and d.answered_at - d.created_at < UNNEEDED_FAST_S
        )
        hit = fast or d.id in flagged or d.ref in flagged
        out.append(
            Sample(
                ts=d.answered_at,
                value=1.0 if hit else 0.0,
                project=d.project_slug,
                person=d.answered_by,
            )
        )
    return out


def _chat_reply(facts: Facts, w: Window) -> list[Sample]:
    by_thread: dict[str, list[Any]] = defaultdict(list)
    for m in sorted(facts.chat, key=lambda m: m.seq):
        by_thread[m.thread].append(m)
    out = []
    for rows in by_thread.values():
        for i, m in enumerate(rows):
            if m.author_kind != "human" or not w.holds(m.created_at):
                continue
            reply = next((r for r in rows[i + 1 :] if r.author_kind == "agent"), None)
            if reply is None:
                continue
            out.append(
                Sample(
                    ts=m.created_at,
                    value=float(max(0, reply.created_at - m.created_at)),
                    role=_thread_role(m.thread),
                    person=m.author,
                    key="auto" if reply.auto_reply else "tool",
                )
            )
    return out


def _chat_extra(_f: Facts, _w: Window, samples: list[Sample]) -> dict[str, Any]:
    auto = sum(1 for s in samples if s.key == "auto")
    return {"auto_reply_share": _r(auto / len(samples), 4) if samples else None}


_DECIDED: Final = frozenset({"applied", "trial", "kept", "reverted", "rejected", "undone"})


def _proposals(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for row in facts.proposals:
        at = (
            row.decided_at
            or row.applied_at
            or (row.updated_at if row.status in _DECIDED else None)
        )
        if row.status not in _DECIDED or not w.holds(at):
            continue
        accepted = row.status in ("applied", "trial", "kept", "reverted", "undone")
        out.append(Sample(ts=at or 0, value=1.0 if accepted else 0.0, key=row.kind))
    return out


def _proposal_extra(facts: Facts, w: Window, samples: list[Sample]) -> dict[str, Any]:
    rows = [r for r in facts.proposals if w.holds(r.applied_at or r.decided_at or r.created_at)]
    applied = [r for r in rows if r.status in ("applied", "trial", "kept", "reverted", "undone")]
    return {
        "acceptance": _r(sum(s.value for s in samples) / len(samples), 4) if samples else None,
        "edited": sum(1 for r in applied if (r.result or {}).get("edited")),
        "undo_rate": _r(sum(1 for r in applied if r.status == "undone") / len(applied), 4)
        if applied
        else None,
        "trials_kept": sum(1 for r in rows if r.status == "kept"),
        "trials_reverted": sum(1 for r in rows if r.status == "reverted"),
        "on_trial": sum(1 for r in facts.proposals if r.status == "trial"),
    }


def _cost(facts: Facts, w: Window) -> list[Sample]:
    first, last = _day(w.since), _day(w.until - 1)
    return [
        Sample(
            ts=datetime.strptime(c.day_utc, "%Y-%m-%d").replace(tzinfo=UTC).timestamp(),
            value=float(c.cost_usd or 0.0),
            key=c.model,
            project=c.project_slug or None,
            role=c.role,
            agent=c.agent_name,
        )
        for c in facts.cost
        if first <= c.day_utc <= last
    ]


def _cost_extra(_f: Facts, _w: Window, samples: list[Sample]) -> dict[str, Any]:
    models: dict[str, float] = defaultdict(float)
    for s in samples:
        models[s.key or ""] += s.value
    return {"by_model": {k: _r(v) for k, v in sorted(models.items())}}


def _cost_per_pr(facts: Facts, w: Window) -> list[Sample]:
    merged = _merged_in(facts, w)
    spend: dict[str, float] = defaultdict(float)
    for s in _cost(facts, w):
        spend[s.project or ""] += s.value
    count: dict[str, int] = defaultdict(int)
    for pr in merged:
        count[pr.project_slug or ""] += 1
    out = []
    for pr in merged:
        slug = pr.project_slug or ""
        out.append(_pr_sample(facts, pr, pr.merged_at or 0, spend.get(slug, 0.0) / count[slug]))
    return out


def _agg_cost_per_pr(samples: list[Sample], _w: Window) -> dict[str, Any]:
    n = len(samples)
    return {"value": _r(sum(s.value for s in samples) / n) if n else None, "n": n}


def _heartbeats(facts: Facts, w: Window) -> list[Sample]:
    return [
        Sample(
            ts=e.ts / 1000,
            value=1.0,
            project=e.project_slug,
            agent=facts.titles.get(e.to_id or ""),
        )
        for e in facts.kind("heartbeat.delivered")
        if w.holds(e.ts / 1000)
    ]


def _heartbeat_extra(facts: Facts, w: Window, samples: list[Sample]) -> dict[str, Any]:
    first, last = _day(w.since), _day(w.until - 1)
    cells = [
        c
        for c in facts.cost
        if c.role in (ROLE_LEAD, ROLE_PROJECT_LEAD) and first <= c.day_utc <= last
    ]
    turns = sum(int(c.turns or 0) for c in cells)
    usd = sum(float(c.cost_usd or 0.0) for c in cells)
    share = min(1.0, len(samples) / turns) if turns else None
    return {
        "value": _r(share, 4),
        "turns": turns,
        "usd_estimate": _r(usd * share) if share is not None else None,
    }


def _wasted(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for lp in _loop_ended(facts, w):
        if lp.state not in ("exhausted", "cancelled"):
            continue
        usd = (lp.spent or {}).get("usd")
        if isinstance(usd, int | float) and usd > 0:
            out.append(_loop_sample(lp, float(usd), lp.kind))
    return out


def _incidents(facts: Facts, w: Window) -> list[Sample]:
    return [
        Sample(
            ts=i.first_seen,
            value=1.0,
            key=i.rule,
            project=i.project_slug,
            def_version=i.def_version,
            version=i.version_id,
        )
        for i in facts.issues
        if i.class_ == "incident" and w.holds(i.first_seen)
    ]


def _incident_extra(facts: Facts, w: Window, _s: list[Sample]) -> dict[str, Any]:
    fixes = [
        float(i.resolved_at - i.first_seen)
        for i in facts.issues
        if i.class_ == "incident" and i.resolved_at and w.holds(i.resolved_at)
    ]
    return {"mttr_p50_s": _r(_pct(fixes, 0.5)), "resolved": len(fixes)}


def _failures(facts: Facts, w: Window) -> list[Sample]:
    failed = {e.subject for e in facts.kind("task.failed") if w.holds(e.ts / 1000)}
    return [
        Sample(
            ts=e.ts / 1000,
            value=1.0 if e.subject in failed else 0.0,
            project=e.project_slug,
            agent=(e.data or {}).get("agent"),
        )
        for e in facts.kind("dispatch")
        if w.holds(e.ts / 1000)
    ]


def _mail(facts: Facts, w: Window) -> list[Sample]:
    queued: dict[str, float] = {}
    for e in facts.kind("message.queued"):
        mail_id = (e.data or {}).get("mail_id")
        if mail_id and w.holds(e.ts / 1000):
            queued[mail_id] = e.ts / 1000
    out = []
    for e in facts.kind("message.delivered", "message.bounced"):
        mail_id = (e.data or {}).get("mail_id")
        if mail_id in queued:
            out.append(
                Sample(
                    ts=e.ts / 1000,
                    value=max(0.0, e.ts / 1000 - queued.pop(mail_id)),
                    key="bounced" if e.kind == "message.bounced" else "delivered",
                    project=e.project_slug,
                )
            )
    return out


def _agg_mail(samples: list[Sample], _w: Window) -> dict[str, Any]:
    ages = [s.value for s in samples if s.key == "delivered"]
    n = len(samples)
    bounced = sum(1 for s in samples if s.key == "bounced")
    return {
        "bounce_rate": _r(bounced / n, 4) if n else None,
        "queue_age_p90_s": _r(_pct(ages, 0.9)),
        "n": n,
    }


def _availability(facts: Facts, w: Window) -> list[Sample]:
    out = []
    for span in facts.spans:
        if span.kind not in ("env", "preview"):
            continue
        start = span.started_at / 1000
        end = (span.ended_at / 1000) if span.ended_at else w.until
        lo, hi = max(start, w.since), min(end, w.until)
        if hi <= lo:
            continue
        out.append(
            Sample(
                ts=lo,
                value=hi - lo,
                key=f"{span.kind}:{'ready' if span.name == 'ready' else 'other'}",
                project=span.project_slug,
            )
        )
    return out


def _agg_availability(samples: list[Sample], _w: Window) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(samples)}
    for kind in ("env", "preview"):
        total = sum(s.value for s in samples if (s.key or "").startswith(f"{kind}:"))
        ready = sum(s.value for s in samples if s.key == f"{kind}:ready")
        out[f"{kind}_ready_share"] = _r(ready / total, 4) if total else None
    return out


def _availability_extra(facts: Facts, w: Window, _s: list[Sample]) -> dict[str, Any]:
    started = [s for s in facts.spans if s.kind == "preview" and w.holds(s.started_at / 1000)]
    deploys = [s for s in started if s.name in ("deploying", "starting")]
    ready = [s for s in started if s.name == "ready"]
    return {
        "preview_deploy_success": _r(min(1.0, len(ready) / len(deploys)), 4) if deploys else None,
        "sha_mismatches": sum(
            1 for e in facts.kind("preview.sha_mismatch") if w.holds(e.ts / 1000)
        ),
    }


def _wakes(facts: Facts, w: Window) -> list[Sample]:
    return [
        Sample(
            ts=e.ts / 1000, value=1.0 if e.kind == "engine.next" else 0.0, project=e.project_slug
        )
        for e in facts.kind("engine.next", "heartbeat.delivered")
        if w.holds(e.ts / 1000)
    ]


def _m(
    name: str,
    family: str,
    title: str,
    unit: str,
    definition: str,
    source: str,
    samples: Any,
    agg: Agg,
    extra: Any = None,
) -> Metric:
    return Metric(name, family, title, unit, definition, source, samples, agg, extra)


CATALOG: Final[dict[str, Metric]] = {
    m.name: m
    for m in (
        _m(
            "lead_time",
            "flow",
            "Lead time",
            "s",
            "project created_at → its first PR merged (p50/p90), by merge time",
            "projects, prs",
            _lead_time,
            agg_p50p90,
        ),
        _m(
            "time_to_first_pr",
            "flow",
            "Time to first PR",
            "s",
            "project created_at → its first PR opened_at",
            "projects, prs",
            _time_to_first_pr,
            agg_p50p90,
        ),
        _m(
            "task_cycle_time",
            "flow",
            "Task cycle time",
            "s",
            "task started_at → its PR first_ready_at (p50/p90)",
            "tasks, prs",
            _task_cycle,
            agg_p50p90,
        ),
        _m(
            "stage_dwell",
            "flow",
            "Stage dwell",
            "s",
            "duration of closed stage spans per stage (p50/p90)",
            "stage spans",
            _stage_dwell,
            by_key(agg_p50p90),
        ),
        _m(
            "human_wait",
            "flow",
            "Agent time vs human wait",
            "s",
            "Σ open→first ready (agent) and first ready→merged (human_merge) of merged PRs; "
            "Σ created→answered of blocking decisions (decision)",
            "prs, decisions",
            _human_wait,
            by_key(agg_sum),
        ),
        _m(
            "throughput",
            "flow",
            "Throughput / WIP",
            "per_day",
            "merged PRs per day; open PRs, live previews and active projects by stage now",
            "prs, projects",
            _throughput,
            agg_per_day,
            _wip_extra,
        ),
        _m(
            "parallelism",
            "flow",
            "Parallelism",
            "tasks",
            "peak and mean concurrent running task spans",
            "task spans",
            _task_spans,
            _agg_parallelism,
        ),
        _m(
            "critical_path_accuracy",
            "flow",
            "Critical-path accuracy",
            "ratio",
            "actual (first start → last close) / estimated critical path (estimate_min) "
            "of finished projects",
            "tasks",
            _critical_path,
            agg_p50p90,
        ),
        _m(
            "loop_iterations",
            "loops",
            "Iterations to converge",
            "iterations",
            "iterations of converged loops per kind (p50/p90)",
            "loops",
            _iterations,
            by_key(agg_p50p90),
        ),
        _m(
            "loop_exit_mix",
            "loops",
            "Exit-reason mix",
            "share",
            "share of converged, exhausted, stalled, thrashing, cancelled among loops that ended",
            "loops",
            _exit_mix,
            agg_mix,
        ),
        _m(
            "loop_exhaustion_rate",
            "loops",
            "Loop exhaustion rate",
            "share",
            "exhausted loops / loops that ended or went bad",
            "loops",
            _exhaustion,
            agg_share,
        ),
        _m(
            "first_pass_yield",
            "loops",
            "First-pass yield",
            "share",
            "share of heads passing a gate (ci, review, verify, demo) on its first run",
            "gate spans",
            _first_pass,
            by_key(agg_share),
        ),
        _m(
            "review_rounds",
            "loops",
            "Review rounds",
            "rounds",
            "failed review gates + human changes requested per merged PR",
            "gate spans, events",
            _review_rounds,
            agg_p50p90,
        ),
        _m(
            "budget_extensions",
            "loops",
            "Budget extensions",
            "count",
            "loop.budget_raised per loop kind",
            "events",
            _budget_raises,
            by_key(agg_count),
        ),
        _m(
            "merge_acceptance",
            "quality",
            "Merge acceptance",
            "share",
            "merged / (merged + closed without merge), with the closure-reason mix",
            "prs, feedback",
            _acceptance,
            agg_share,
            _closure_mix,
        ),
        _m(
            "escape_rate",
            "quality",
            "Escape rate",
            "share",
            "PRs with a human CHANGES_REQUESTED after first READY / PRs first READY",
            "prs, events",
            _escape,
            agg_share,
        ),
        _m(
            "sentinel_agreement",
            "quality",
            "Sentinel agreement",
            "share",
            "Sentinel-verified PRs merged without later change requests / verified PRs closed",
            "prs, events",
            _sentinel_agreement,
            agg_share,
        ),
        _m(
            "claim_mismatch_rate",
            "quality",
            "Claim calibration",
            "share",
            "ready claims whose gates disagreed (claim_mismatch) / ready claims, per PL",
            "pr.claim",
            _claims,
            agg_share,
        ),
        _m(
            "human_ratings",
            "quality",
            "Human ratings",
            "rating",
            "mean PR rating (1-5) with its distribution; demo 👍 share",
            "feedback",
            _ratings,
            agg_mean,
            _ratings_extra,
        ),
        _m(
            "human_prompts_per_day",
            "human",
            "Human prompts per day",
            "per_day",
            "human Room posts + human-authored items on factory members (nightly scan), "
            "per day, split lead/PL/other against the 30-50 band",
            "chat, metrics.prompts",
            _prompts,
            agg_per_day,
            _prompts_extra,
        ),
        _m(
            "interventions_per_merged_pr",
            "human",
            "Interventions per merged PR",
            "acts",
            "(controls + human decision answers + reviews + human chat posts) / merged PRs",
            "events, decisions, chat",
            _human_acts,
            agg_count,
            _per_merged,
        ),
        _m(
            "decision_latency",
            "human",
            "Decision latency",
            "s",
            "decision created → answered (p50/p90); escalation rate",
            "decisions, events",
            _decision_latency,
            agg_p50p90,
            _escalation_extra,
        ),
        _m(
            "unnecessary_questions",
            "human",
            "Unnecessary questions",
            "share",
            "answered decisions flagged unnecessary, or answered with the recommended option "
            "in under 2 min",
            "feedback, decisions",
            _unneeded,
            agg_share,
        ),
        _m(
            "chat_reply_time",
            "human",
            "Chat time to reply",
            "s",
            "human Room message → first agent reply in its thread (p50/p90); auto-reply share",
            "chat",
            _chat_reply,
            agg_p50p90,
            _chat_extra,
        ),
        _m(
            "proposal_health",
            "human",
            "Proposal health",
            "share",
            "accepted / decided proposals; edits, undo rate, trials kept vs reverted",
            "proposals",
            _proposals,
            agg_share,
            _proposal_extra,
        ),
        _m(
            "cost_usd",
            "cost",
            "$ spent",
            "usd",
            "Σ display cost of the ledger (by day, project, role, agent, model)",
            "factory_cost_daily",
            _cost,
            agg_sum,
            _cost_extra,
        ),
        _m(
            "cost_per_merged_pr",
            "cost",
            "$ per merged PR",
            "usd",
            "a project's ledger cost in the window / its PRs merged in the window, per merged PR",
            "ledger, prs",
            _cost_per_pr,
            _agg_cost_per_pr,
        ),
        _m(
            "heartbeat_share",
            "cost",
            "Heartbeat and idle share",
            "share",
            "heartbeat-delivered turns / lead and PL turns; $ estimate = that share of their cost",
            "events, ledger",
            _heartbeats,
            agg_count,
            _heartbeat_extra,
        ),
        _m(
            "wasted_usd",
            "cost",
            "$ wasted",
            "usd",
            "Σ spent.usd of loops that ended exhausted or cancelled",
            "loops",
            _wasted,
            agg_sum,
        ),
        _m(
            "incidents",
            "reliability",
            "Incidents and MTTR",
            "count",
            "incidents first seen in the window; MTTR p50 of those resolved",
            "issues",
            _incidents,
            agg_count,
            _incident_extra,
        ),
        _m(
            "subagent_failure_rate",
            "reliability",
            "Sub-agent failure rate",
            "share",
            "task.failed / dispatch",
            "events",
            _failures,
            agg_share,
        ),
        _m(
            "mail_health",
            "reliability",
            "Mail health",
            "share",
            "bounced / queued mail; queued → delivered age p90",
            "message.* events",
            _mail,
            _agg_mail,
        ),
        _m(
            "env_availability",
            "reliability",
            "Env and preview availability",
            "share",
            "ready time / total time of env and preview episodes; deploy success; sha mismatches",
            "env, preview spans",
            _availability,
            _agg_availability,
            _availability_extra,
        ),
        _m(
            "engine_wake_efficiency",
            "reliability",
            "Engine wake efficiency",
            "share",
            "engine.next / (engine.next + heartbeat.delivered)",
            "events",
            _wakes,
            agg_share,
        ),
    )
}
"""Every metric of the catalog (Tier 0)."""

KPIS: Final = (
    "merge_acceptance",
    "lead_time",
    "human_wait",
    "human_prompts_per_day",
    "cost_per_merged_pr",
    "incidents",
    "engine_wake_efficiency",
)
"""The Insights header tiles."""
GUARDS: Final[dict[str, tuple[str, float]]] = {
    "merge_acceptance": ("pp_drop", 0.10),
    "cost_per_merged_pr": ("pct_rise", 0.10),
    "incidents": ("abs_rise", 1.0),
    "human_prompts_per_day": ("pct_rise", 0.20),
    "loop_exhaustion_rate": ("pp_rise", 0.10),
}
"""Guard metrics a trial must not regress, with the allowed change (Tier 0)."""


# ── Computing ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Filters:
    """Keep one project, platform version or definition version; drop hidden projects."""

    project: str | None = None
    version: str | None = None
    def_version: int | None = None
    hidden_projects: frozenset[str] = frozenset()

    def keep(self, sample: Sample) -> bool:
        if sample.project and sample.project in self.hidden_projects:
            return False
        if self.project is not None and sample.project != self.project:
            return False
        if self.version is not None and sample.version != self.version:
            return False
        return self.def_version is None or sample.def_version == self.def_version


def evaluate_metric(
    facts: Facts, metric: Metric, window: Window, filters: Filters, group_by: str | None = None
) -> dict[str, Any]:
    """:returns: ``{value fields…, n, [groups]}`` of one metric over *window*."""
    samples = [s for s in metric.samples(facts, window) if filters.keep(s)]
    out = dict(metric.agg(samples, window))
    if metric.extra is not None:
        out.update(metric.extra(facts, window, samples))
    if group_by:
        groups: dict[str, list[Sample]] = defaultdict(list)
        for sample in samples:
            groups[sample.group(group_by)].append(sample)
        out["groups"] = {k: metric.agg(v, window) for k, v in sorted(groups.items())}
    return out


_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}


def compute(
    deps: FactoryDeps,
    factory_id: str,
    names: Iterable[str] | None = None,
    *,
    window: Window,
    group_by: str | None = None,
    filters: Filters | None = None,
    previous: bool = False,
    use_cache: bool = True,
) -> dict[str, dict[str, Any]]:
    """
    Compute metrics (synchronous; 5-minute cache).

    :param names: Catalog names (all of them when ``None``).
    :param previous: Also compute each over the previous window (``previous`` key).
    :raises KeyError: For an unknown metric name.
    """
    wanted = list(names) if names is not None else list(CATALOG)
    for name in wanted:
        if name not in CATALOG:
            raise KeyError(name)
    filters = filters or Filters()
    bucket = window.until // int(CACHE_S)
    key = (factory_id, tuple(wanted), window.seconds, bucket, group_by, filters, previous)
    hit = _cache.get(key) if use_cache else None
    if hit is not None and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    facts = load_facts(deps, factory_id, window)
    out: dict[str, dict[str, Any]] = {}
    for name in wanted:
        metric = CATALOG[name]
        try:
            value = evaluate_metric(facts, metric, window, filters, group_by)
            if previous:
                value["previous"] = evaluate_metric(facts, metric, window.previous(), filters)
        except Exception:  # noqa: BLE001 - one broken metric must not hide the others
            _logger.warning("factory metrics: %s failed", name, exc_info=True)
            value = {"error": "unavailable", "n": 0}
        out[name] = {
            "name": name,
            "family": metric.family,
            "title": metric.title,
            "unit": metric.unit,
            "definition": metric.definition,
            **value,
        }
    if use_cache:
        if len(_cache) > 256:
            _cache.clear()
        _cache[key] = (time.monotonic(), out)
    return out


def headline(result: Mapping[str, Any]) -> float | None:
    """:returns: The single number a guard or tile compares (``value``, else ``p50``)."""
    for key in ("value", "p50", "bounce_rate"):
        found = result.get(key)
        if isinstance(found, int | float):
            return float(found)
    return None


def reset_cache() -> None:
    """Forget cached results (tests)."""
    _cache.clear()


# ── Nightly human-prompt scan ──────────────────────────────────


def scan_human_prompts(deps: FactoryDeps, factory_id: str, day: str) -> list[dict[str, Any]]:
    """
    Count human-authored items on the factory's members for one UTC *day*.

    Room deliveries (``[Factory …`` envelopes) are left out: the chat table
    already counts those posts. Synchronous.

    :returns: ``[{role, person, project, n}]``.
    """
    from omnigent.factory import LABEL_PROJECT
    from omnigent.server.factory.membership import factory_members

    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
    end = start + DAY_S
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for member in factory_members(factory_id, deps.conversation_store):
        if member.labels.get(LABEL_ID) is None:
            continue
        role = member.labels.get(LABEL_ROLE) or "other"
        page = deps.conversation_store.list_items(
            member.id, limit=_SCAN_ITEMS, order="desc", type="message"
        )
        for item in page.data:
            if item.created_at < start:
                break
            if item.created_at >= end or not item.created_by or _is_envelope(item):
                continue
            key = (role, item.created_by, member.labels.get(LABEL_PROJECT) or "")
            counts[key] += 1
    return [
        {"role": role, "person": person, "project": project or None, "n": n}
        for (role, person, project), n in sorted(counts.items())
    ]


def _is_envelope(item: Any) -> bool:
    data = getattr(item, "data", None)
    content = getattr(data, "content", None)
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for part in content:
            text = getattr(part, "text", None)
            if text is None and isinstance(part, Mapping):
                text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
    return any(t.lstrip().startswith("[Factory ") for t in texts[:1])


async def record_prompt_scan(app_state: Any, factory_id: str, day: str) -> bool:
    """Scan *day* once per factory (idempotent on the event subject). :returns: Whether it ran."""
    import asyncio

    from omnigent.server.factory import events
    from omnigent.server.factory.context import factory_deps

    deps = factory_deps(app_state)
    subject = f"prompts:{day}"
    seen = await asyncio.to_thread(
        deps.factory_store.find_events, factory_id, PROMPT_SCAN_KIND, 0, subject, limit=1
    )
    if seen:
        return False
    counts = await asyncio.to_thread(scan_human_prompts, deps, factory_id, day)
    total = sum(row["n"] for row in counts)
    await events.emit(
        app_state,
        factory_id,
        PROMPT_SCAN_KIND,
        subject=subject,
        summary=f"{total} human prompts on members on {day}",
        data={"day": day, "counts": counts, "by": "server", "by_kind": "server"},
    )
    return True


__all__ = [
    "CATALOG",
    "DAISY_BAND",
    "GROUP_KEYS",
    "GUARDS",
    "KPIS",
    "OWNER_ONLY_GROUPS",
    "PROMPT_SCAN_KIND",
    "Facts",
    "Filters",
    "Metric",
    "Sample",
    "Window",
    "compute",
    "evaluate_metric",
    "headline",
    "load_facts",
    "parse_window",
    "record_prompt_scan",
    "reset_cache",
    "scan_human_prompts",
]
