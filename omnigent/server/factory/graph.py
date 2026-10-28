"""Factory graph computations: pure functions, no I/O.

The **work graph** is a project's tasks (``factory_tasks``) and their
dependencies. A dependency is another task's key, ``project:<slug>`` or
``decision:D-<n>``; the caller resolves the last two into ``external_met``.

- :func:`find_cycle` / :func:`unknown_deps`: validation (409 ``cycle`` with
  the path, 422 ``unknown_dep``).
- :func:`ready_set`: planned tasks whose dependencies are met.
- :func:`plan_waves`: a layered topological order of the pending tasks, each
  wave capped by free IC and preview slots and split by implicit
  serialization edges between same-wave tasks whose scope globs overlap.
- :func:`critical_path`: the longest weighted path through unfinished tasks.
- :func:`overlap_warnings`: ``scope_overlap`` and ``hub_file`` warnings.

The **bus**, **flow** and **org** graphs aggregate message events, spans and
members into nodes and edges for the cockpit's Graph page.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, Final

DONE: Final = "done"
IN_FLIGHT: Final = frozenset({"running", "review", "blocked"})
"""States with a worker on the task (or a PR under review)."""
OCCUPYING: Final = frozenset({"running", "blocked"})
"""States whose implement worker holds an IC slot."""
PENDING: Final = frozenset({"planned", "ready"})
"""States not started yet."""
DEAD: Final = frozenset({"failed", "cancelled"})
"""States a dependent cannot proceed past until a human or the PL acts."""

DEFAULT_WEIGHT_MIN: Final = 60.0
MIN_OBSERVED_SAMPLES: Final = 5
PROJECT_DEP_PREFIX: Final = "project:"
DECISION_DEP_PREFIX: Final = "decision:"
_WILDCARDS: Final = re.compile(r"[*?\[{]")


@dataclass(frozen=True)
class TaskNode:
    """
    One task as the computations see it.

    :param key: Branch suffix, unique within the project.
    :param deps: Canonical dependencies: task keys, ``project:<slug>``, ``decision:D-n``.
    :param scope: Path globs the task may touch.
    :param preview: The task's PR gets a factory preview (counts toward preview slots).
    """

    key: str
    number: int = 0
    kind: str = "implement"
    state: str = "planned"
    deps: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()
    priority: int = 2
    estimate_min: int | None = None
    preview: bool = False


def is_external(dep: str) -> bool:
    """:returns: Whether *dep* names a project or a decision rather than a task."""
    return dep.startswith((PROJECT_DEP_PREFIX, DECISION_DEP_PREFIX))


def task_deps(node: TaskNode) -> list[str]:
    """:returns: The task keys *node* depends on."""
    return [d for d in node.deps if not is_external(d)]


def ordered(nodes: Iterable[TaskNode]) -> list[TaskNode]:
    """:returns: *nodes* in scheduling order: priority (0 first), then number."""
    return sorted(nodes, key=lambda n: (n.priority, n.number, n.key))


# ── Validation ───────────────────────────────────────────────────


def find_cycle(nodes: Iterable[TaskNode]) -> list[str] | None:
    """
    Find one dependency cycle among task keys.

    :returns: The cycle as a path that ends where it starts, e.g.
        ``["a", "b", "a"]``; ``None`` when the graph is acyclic.
    """
    edges = {n.key: task_deps(n) for n in sorted(nodes, key=lambda n: (n.number, n.key))}
    color: dict[str, int] = {}
    stack: list[str] = []

    def visit(key: str) -> list[str] | None:
        color[key] = 1
        stack.append(key)
        for dep in edges.get(key, ()):
            if dep not in edges:
                continue
            if color.get(dep) == 1:
                return [*stack[stack.index(dep) :], dep]
            if color.get(dep) is None and (found := visit(dep)) is not None:
                return found
        stack.pop()
        color[key] = 2
        return None

    for key in edges:
        if color.get(key) is None and (found := visit(key)) is not None:
            return found
    return None


def unknown_deps(
    nodes: Iterable[TaskNode],
    *,
    projects: Iterable[str] = (),
    decisions: Iterable[str] = (),
) -> list[tuple[str, str]]:
    """
    Dependencies that name nothing.

    :param projects: Project slugs of the factory.
    :param decisions: Decision refs of the factory, e.g. ``["D-7"]``.
    :returns: ``(task key, dep)`` pairs, in task order.
    """
    items = list(nodes)
    keys = {n.key for n in items}
    known_projects, known_decisions = set(projects), set(decisions)
    out: list[tuple[str, str]] = []
    for node in items:
        for dep in node.deps:
            if dep.startswith(PROJECT_DEP_PREFIX):
                ok = dep.removeprefix(PROJECT_DEP_PREFIX) in known_projects
            elif dep.startswith(DECISION_DEP_PREFIX):
                ok = dep.removeprefix(DECISION_DEP_PREFIX) in known_decisions
            else:
                ok = dep in keys
            if not ok:
                out.append((node.key, dep))
    return out


# ── Scope overlap (the Spec-Kit "[P]" rule, by glob prefix) ─────


def _norm_glob(glob: str) -> str:
    text = glob.strip()
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _literal(glob: str) -> tuple[str, bool]:
    """:returns: ``(literal prefix before the first wildcard, has a wildcard)``."""
    match = _WILDCARDS.search(glob)
    return (glob[: match.start()], True) if match else (glob, False)


def globs_overlap(a: str, b: str) -> bool:
    """
    Whether two path globs may name a common path (a prefix heuristic).

    ``services/api/**`` overlaps ``services/api/src/x.ts`` and ``services/**``;
    ``services/api`` (a directory) overlaps ``services/api/x.ts`` but not
    ``services/api-gw/**``; ``services/api*`` overlaps ``services/api-gw/**``.
    """
    a, b = _norm_glob(a), _norm_glob(b)
    if not a or not b:
        return False
    (la, wa), (lb, wb) = _literal(a), _literal(b)
    (short, short_wild), (long, _) = sorted(((la, wa), (lb, wb)), key=lambda p: len(p[0]))
    if not long.startswith(short):
        return False
    if short_wild or short == long:
        return True
    # A concrete path overlaps what lies below it as a directory.
    return long.startswith(short.rstrip("/") + "/")


def scope_overlaps(a: Sequence[str], b: Sequence[str]) -> list[tuple[str, str]]:
    """:returns: The overlapping ``(glob of a, glob of b)`` pairs."""
    return [(x, y) for x in a for y in b if globs_overlap(x, y)]


def _ancestors(nodes: Mapping[str, TaskNode]) -> dict[str, set[str]]:
    """Every task key each task (transitively) depends on."""
    memo: dict[str, set[str]] = {}

    def walk(key: str, seen: frozenset[str]) -> set[str]:
        if key in memo:
            return memo[key]
        out: set[str] = set()
        for dep in task_deps(nodes[key]):
            if dep in nodes and dep not in seen:
                out.add(dep)
                out |= walk(dep, seen | {dep})
        memo[key] = out
        return out

    for key in nodes:
        walk(key, frozenset({key}))
    return memo


def overlap_warnings(
    nodes: Iterable[TaskNode], *, hub_files: Iterable[str] = ()
) -> list[dict[str, Any]]:
    """
    Unordered unfinished tasks that touch the same paths.

    :param hub_files: Shared files a planner analysis flagged (e.g. a router index).
    :returns: ``{"code": "scope_overlap"|"hub_file", "tasks": [keys], "paths": [...],
        "message"}`` items; ordered pairs (one depends on the other) never warn.
    """
    live = {n.key: n for n in ordered(nodes) if n.state != DONE and n.state != "cancelled"}
    ancestors = _ancestors(live)

    def unordered(a: str, b: str) -> bool:
        return a not in ancestors.get(b, set()) and b not in ancestors.get(a, set())

    out: list[dict[str, Any]] = []
    keys = list(live)
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            pairs = scope_overlaps(live[a].scope, live[b].scope)
            if pairs and unordered(a, b):
                paths = sorted({p for pair in pairs for p in pair})
                out.append(
                    {
                        "code": "scope_overlap",
                        "tasks": [a, b],
                        "paths": paths,
                        "message": f"{a} and {b} both touch {', '.join(paths)}; they run "
                        "one after the other unless one depends on the other",
                    }
                )
    for hub in dict.fromkeys(h for h in hub_files if isinstance(h, str) and h.strip()):
        touching = [k for k in keys if any(globs_overlap(g, hub) for g in live[k].scope)]
        loose = [
            k for k in touching if any(unordered(k, other) for other in touching if other != k)
        ]
        if len(loose) >= 2:
            out.append(
                {
                    "code": "hub_file",
                    "tasks": loose,
                    "paths": [hub],
                    "message": f"{', '.join(loose)} all touch the hub file {hub}",
                }
            )
    return out


# ── Readiness, waves and the critical path ──────────────────────


def waiting_on(
    node: TaskNode, by_key: Mapping[str, TaskNode], external_met: Mapping[str, bool]
) -> list[str]:
    """
    The dependencies of *node* that are not met yet.

    :param external_met: ``project:``/``decision:`` deps → met.
    :returns: e.g. ``["route", "decision:D-7"]``; a dead task dep reads ``"route (failed)"``.
    """
    out: list[str] = []
    for dep in node.deps:
        if is_external(dep):
            if not external_met.get(dep, False):
                out.append(dep)
            continue
        other = by_key.get(dep)
        if other is None:
            out.append(f"{dep} (unknown)")
        elif other.state in DEAD:
            out.append(f"{dep} ({other.state})")
        elif other.state != DONE:
            out.append(dep)
    return out


def deps_met(
    node: TaskNode, by_key: Mapping[str, TaskNode], external_met: Mapping[str, bool]
) -> bool:
    """:returns: Whether every dependency of *node* is met."""
    return not waiting_on(node, by_key, external_met)


def ready_set(
    nodes: Iterable[TaskNode],
    external_met: Mapping[str, bool],
    *,
    active: bool = True,
    paused: bool = False,
) -> list[str]:
    """
    Tasks that may start now: planned (or ready) with every dependency met.

    :param active: The project is active (not cancelled, merged or done).
    :param paused: The project or the factory is paused.
    :returns: Keys in scheduling order; empty for an inactive or paused project.
    """
    if not active or paused:
        return []
    items = ordered(nodes)
    by_key = {n.key: n for n in items}
    return [n.key for n in items if n.state in PENDING and deps_met(n, by_key, external_met)]


@dataclass(frozen=True)
class Slots:
    """
    Capacity of a project's waves.

    :param ics_free: IC slots free now (``budget_ics`` minus running implement workers).
    :param budget_ics: The charter's IC budget (later waves' cap).
    :param previews_free: Preview slots free now.
    :param preview_slots: The charter's preview slots (later waves' cap).
    :param previews_on: The charter has previews on; otherwise previews cap nothing.
    """

    ics_free: int = 3
    budget_ics: int = 3
    previews_free: int = 2
    preview_slots: int = 2
    previews_on: bool = True


@dataclass(frozen=True)
class WavePlan:
    """
    The pending work in execution order.

    :param waves: Task keys per wave; ``waves[0]`` may start now (it can be empty
        when no IC slot is free).
    :param serial: Implicit serialization edges ``(earlier, later)`` between
        tasks whose scopes overlap.
    :param blocked: Pending tasks that cannot be scheduled, with what they wait on.
    """

    waves: tuple[tuple[str, ...], ...] = ()
    serial: tuple[tuple[str, str], ...] = ()
    blocked: dict[str, list[str]] = field(default_factory=dict)

    @property
    def now(self) -> tuple[str, ...]:
        """:returns: The first wave (startable now)."""
        return self.waves[0] if self.waves else ()

    def wave_of(self) -> dict[str, int]:
        """:returns: Task key → 1-based wave."""
        return {key: i + 1 for i, wave in enumerate(self.waves) for key in wave}

    def nonempty(self) -> list[list[str]]:
        """:returns: The waves without empty ones (for summaries)."""
        return [list(w) for w in self.waves if w]


def _blocked_pending(
    items: Sequence[TaskNode], by_key: Mapping[str, TaskNode], external_met: Mapping[str, bool]
) -> dict[str, list[str]]:
    """Pending tasks that wait on something no schedule resolves (transitively)."""
    blocked: dict[str, list[str]] = {}
    visiting: set[str] = set()

    def check(node: TaskNode) -> list[str]:
        if node.key in blocked:
            return blocked[node.key]
        if node.key in visiting:
            return ["cycle"]
        visiting.add(node.key)
        reasons: list[str] = []
        for dep in node.deps:
            if is_external(dep):
                if not external_met.get(dep, False):
                    reasons.append(dep)
                continue
            other = by_key.get(dep)
            if other is None:
                reasons.append(f"{dep} (unknown)")
            elif other.state in DEAD:
                reasons.append(f"{dep} ({other.state})")
            elif other.state in PENDING and check(other):
                reasons.append(dep)
        visiting.discard(node.key)
        if reasons:
            blocked[node.key] = reasons
        return reasons

    for node in items:
        if node.state in PENDING:
            check(node)
    return blocked


def plan_waves(
    nodes: Iterable[TaskNode],
    external_met: Mapping[str, bool],
    slots: Slots,
    *,
    active: bool = True,
    paused: bool = False,
) -> WavePlan:
    """
    Layer the pending tasks into waves.

    A task joins the first wave where each task dependency is done, or in
    flight (from wave 2 on), or placed in an earlier wave. Each wave holds
    at most the IC cap and the preview cap (the free slots for wave 1, the
    charter's budgets after that); a task whose scope overlaps one already
    in the wave (or a running task, for wave 1) moves to a later wave.

    :param active: An inactive project starts nothing now (wave 1 is empty).
    :param paused: Likewise for a paused project or factory.
    """
    items = ordered(nodes)
    by_key = {n.key: n for n in items}
    blocked = _blocked_pending(items, by_key, external_met)
    remaining = [n for n in items if n.state in PENDING and n.key not in blocked]
    running = [n for n in items if n.state in OCCUPYING]
    placed: dict[str, int] = {}
    waves: list[tuple[str, ...]] = []
    serial: dict[tuple[str, str], None] = {}
    wave = 1

    def dep_ok(dep: str, at: int) -> bool:
        other = by_key.get(dep)
        if other is None or other.state == DONE:
            return other is not None
        if other.state in IN_FLIGHT:
            return at >= 2
        return dep in placed and placed[dep] < at

    while remaining:
        candidates = [n for n in remaining if all(dep_ok(d, wave) for d in task_deps(n))]
        if not candidates:
            for node in remaining:
                blocked[node.key] = ["cycle"]
            break
        first = wave == 1
        if first:
            cap_ics = max(slots.ics_free, 0) if active and not paused else 0
            cap_previews = max(slots.previews_free, 0)
        else:
            cap_ics, cap_previews = max(slots.budget_ics, 1), max(slots.preview_slots, 1)
        chosen: list[TaskNode] = []
        previews = 0
        for node in candidates:
            if len(chosen) >= cap_ics:
                break
            if slots.previews_on and node.preview and previews >= cap_previews:
                continue
            busy = [*running, *chosen] if first else chosen
            clash = next((c for c in busy if scope_overlaps(c.scope, node.scope)), None)
            if clash is not None:
                serial[(clash.key, node.key)] = None
                continue
            chosen.append(node)
            previews += int(node.preview)
        waves.append(tuple(n.key for n in chosen))
        for node in chosen:
            placed[node.key] = wave
            remaining.remove(node)
        wave += 1
        if wave > len(items) + 2:
            break
    return WavePlan(waves=tuple(waves), serial=tuple(serial), blocked=blocked)


def layers(nodes: Iterable[TaskNode]) -> dict[str, int]:
    """
    The structural layer of every task that is not cancelled (1 = no task deps).

    Done and running tasks keep their place, so a board can draw W1, W2, …
    as columns. A cycle leaves its members on the layer reached so far.
    """
    live = {n.key: n for n in nodes if n.state != "cancelled"}
    out: dict[str, int] = {}
    for _ in range(len(live) + 1):
        changed = False
        for key, node in live.items():
            level = 1 + max((out.get(d, 1) for d in task_deps(node) if d in live), default=0)
            if out.get(key) != level:
                out[key] = level
                changed = True
        if not changed:
            break
    return out


def weight_min(node: TaskNode, observed: Mapping[str, float]) -> float:
    """
    A task's critical-path weight in minutes.

    :param observed: Kind → observed p50 minutes (only kinds with enough samples).
    :returns: 0 for a done task; else the observed p50 of its kind, else its
        estimate, else :data:`DEFAULT_WEIGHT_MIN`.
    """
    if node.state == DONE:
        return 0.0
    if node.kind in observed:
        return float(observed[node.kind])
    if node.estimate_min is not None and node.estimate_min > 0:
        return float(node.estimate_min)
    return DEFAULT_WEIGHT_MIN


def critical_path(
    nodes: Iterable[TaskNode], weight: Callable[[TaskNode], float]
) -> tuple[list[str], float]:
    """
    The longest weighted path through the unfinished tasks.

    Done and cancelled tasks are left out (a done task weighs 0); edges run
    from a dependency to its dependent. Ties go to the lower task number.

    :returns: ``(keys from first to last, total minutes)``; ``([], 0)`` when
        nothing is unfinished or the graph has a cycle.
    """
    live = {n.key: n for n in nodes if n.state not in (DONE, "cancelled")}
    if not live or find_cycle(live.values()) is not None:
        return [], 0.0
    best: dict[str, tuple[float, list[str]]] = {}

    def longest(key: str) -> tuple[float, list[str]]:
        if key in best:
            return best[key]
        node = live[key]
        known = sorted({d for d in task_deps(node) if d in live}, key=_number(live))
        prior = [longest(d) for d in known]
        head = max(prior, key=lambda p: p[0], default=(0.0, []))
        best[key] = (head[0] + weight(node), [*head[1], key])
        return best[key]

    results = [longest(k) for k in sorted(live, key=_number(live))]
    total, path = max(results, key=lambda r: r[0])
    return path, round(total, 1)


def _number(live: Mapping[str, TaskNode]) -> Callable[[str], tuple[int, str]]:
    return lambda key: (live[key].number, key)


def p50(values: Iterable[float]) -> float | None:
    """:returns: The median of *values*, or ``None`` when there are none."""
    items = [float(v) for v in values]
    return float(statistics.median(items)) if items else None


def observed_p50(
    samples: Mapping[str, Sequence[float]], min_n: int = MIN_OBSERVED_SAMPLES
) -> dict[str, float]:
    """
    Kind → p50 minutes, for kinds with at least *min_n* observed durations.

    :param samples: Kind → observed task durations in minutes.
    """
    out: dict[str, float] = {}
    for kind, values in samples.items():
        if len(values) >= min_n and (median := p50(values)) is not None:
            out[kind] = round(median, 1)
    return out


# ── Bus graph (message.* events) ─────────────────────────────────


SERVER_NODE: Final = "server"


@dataclass(frozen=True)
class MailFact:
    """
    One mail event.

    :param ts: Epoch milliseconds.
    :param event: ``message.queued`` / ``message.delivered`` / ``message.bounced``.
    :param sender: Sending session id, or ``None`` for server mail.
    :param kind: The message kind (``request``, ``reply``, ``info``, …).
    """

    ts: int
    event: str
    sender: str | None
    target: str | None
    kind: str | None
    mail_id: str | None


def bus_edges(facts: Iterable[MailFact]) -> list[dict[str, Any]]:
    """
    Message edges aggregated per ``(sender, target)``.

    :returns: ``{"from", "to", "kind": "message", "count", "kinds", "delivered",
        "bounced", "requests", "unanswered", "p50_reply_s", "last_at"}`` per pair,
        busiest first; server mail comes from :data:`SERVER_NODE`.
    """
    queued: list[MailFact] = []
    delivered: set[str] = set()
    bounced: set[str] = set()
    for fact in sorted(facts, key=lambda f: f.ts):
        if fact.event == "message.queued" and fact.target:
            queued.append(fact)
        elif fact.event == "message.delivered" and fact.mail_id:
            delivered.add(fact.mail_id)
        elif fact.event == "message.bounced" and fact.mail_id:
            bounced.add(fact.mail_id)
    pairs: dict[tuple[str, str], list[MailFact]] = defaultdict(list)
    for fact in queued:
        pairs[(fact.sender or SERVER_NODE, str(fact.target))].append(fact)
    out: list[dict[str, Any]] = []
    for (sender, target), items in pairs.items():
        answers = [f.ts for f in pairs.get((target, sender), ()) if f.kind == "reply"]
        requests = [f for f in items if f.kind == "request"]
        latencies: list[float] = []
        unanswered = 0
        for request in requests:
            reply = next((t for t in answers if t >= request.ts), None)
            if reply is None:
                unanswered += 1
            else:
                latencies.append((reply - request.ts) / 1000)
        median = p50(latencies)
        out.append(
            {
                "from": sender,
                "to": target,
                "kind": "message",
                "count": len(items),
                "kinds": dict(Counter(f.kind or "info" for f in items)),
                "delivered": sum(1 for f in items if f.mail_id in delivered),
                "bounced": sum(1 for f in items if f.mail_id in bounced),
                "requests": len(requests),
                "unanswered": unanswered,
                "p50_reply_s": round(median, 1) if median is not None else None,
                "last_at": max(f.ts for f in items),
            }
        )
    out.sort(key=lambda e: (-e["count"], e["from"], e["to"]))
    return out


# ── Flow graph (stage, gate, pr_round and loop_iter spans) ───────


FLOW_STAGES: Final = ("plan", "build", "review", "verify", "demo", "ready", "merged")
"""The pipeline nodes, in order."""
_STAGE_NODE: Final = {
    "planning": "plan",
    "building": "build",
    "review": "review",
    "verify": "verify",
    "ready": "ready",
    "merged": "merged",
}
_GATE_NODE: Final = {"checks": "build", "review": "review", "verify": "verify", "demo": "demo"}
_ROLE_NODE: Final = {
    "ic": "build",
    "reviewer": "review",
    "env_keeper": "verify",
    "demo": "demo",
    "project_lead": "plan",
}
_BACK_EDGES: Final = {
    "checks": ("build", "CI fail"),
    "review": ("review", "changes"),
    "verify": ("verify", "FAIL"),
    "demo": ("demo", "FAIL"),
}


@dataclass(frozen=True)
class SpanFact:
    """
    The fields of a span the flow graph reads.

    :param started_at: Epoch milliseconds.
    :param ended_at: Epoch milliseconds, or ``None`` while open.
    """

    kind: str
    name: str
    started_at: int
    ended_at: int | None = None
    outcome: str = "open"
    project_slug: str | None = None
    pr_id: str | None = None
    role: str | None = None
    cost_usd: float | None = None
    attrs: Mapping[str, Any] = field(default_factory=dict)


def _task_node(span: SpanFact) -> str | None:
    """Which pipeline node a sub-agent run's cost belongs to (by its title, then role)."""
    title = span.name.rpartition(":")[2] if ":" in span.name else span.name
    if title.startswith("review-"):
        return "review"
    if title.startswith("demo-"):
        return "demo"
    return _ROLE_NODE.get(span.role or "")


def flow_graph(
    spans: Iterable[SpanFact],
    *,
    now_counts: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """
    The PR pipeline as an aggregated stage graph with loop back-edges.

    - **nodes** (:data:`FLOW_STAGES`): ``count`` now (from *now_counts*), ``n``
      and ``p50_dwell_s`` over closed ``stage`` spans (``gate`` spans for
      ``demo``, ``ready_wait`` for ``ready``), and ``usd`` from sub-agent runs
      (``task`` spans) by title and role.
    - **back_edges**: failed first verdicts per head (``gate`` spans:
      ``CI fail``, review ``changes``, verify and demo ``FAIL``), verify and
      demo failures split by class when ``loop_iter`` spans say ``code:`` or
      ``env:``, and ``rounds`` (extra heads per PR from ``pr_round`` spans).

    :param now_counts: Node → how many projects or PRs sit there now.
    """
    dwell: dict[str, list[float]] = defaultdict(list)
    usd: dict[str, float] = defaultdict(float)
    back: Counter[tuple[str, str, str]] = Counter()
    rounds: dict[str, int] = defaultdict(int)
    classed: Counter[tuple[str, str]] = Counter()
    for span in spans:
        closed = span.ended_at is not None
        length = (span.ended_at - span.started_at) / 1000 if closed and span.ended_at else 0.0
        if span.kind == "stage" and closed and (node := _STAGE_NODE.get(span.name)):
            dwell[node].append(length)
        elif span.kind == "ready_wait" and closed:
            dwell["ready"].append(length)
        elif span.kind == "gate":
            node = _GATE_NODE.get(span.name)
            if node == "demo" and closed:
                dwell["demo"].append(length)
            if span.outcome == "fail" and span.name in _BACK_EDGES:
                origin, label = _BACK_EDGES[span.name]
                back[(origin, "build", label)] += 1
        elif span.kind == "pr_round" and span.pr_id:
            rounds[span.pr_id] += 1
        elif span.kind == "loop_iter":
            reason = str((span.attrs or {}).get("reason") or (span.attrs or {}).get("class") or "")
            loop = str((span.attrs or {}).get("loop") or span.name)
            if loop in ("verify", "demo") and reason.startswith(("env", "code")):
                classed[(loop, "env" if reason.startswith("env") else "code")] += 1
        elif span.kind == "task" and span.cost_usd and (node := _task_node(span)):
            usd[node] += float(span.cost_usd)
    counts = now_counts or {}
    nodes = []
    for node in FLOW_STAGES:
        median = p50(dwell.get(node, ()))
        nodes.append(
            {
                "id": node,
                "count": int(counts.get(node, 0)),
                "n": len(dwell.get(node, ())),
                "p50_dwell_s": round(median, 1) if median is not None else None,
                "usd": round(usd[node], 2) if node in usd else None,
            }
        )
    edges = [{"from": a, "to": b} for a, b in pairwise(FLOW_STAGES)]
    back_edges = [
        {"from": origin, "to": target, "label": label, "count": n}
        for (origin, target, label), n in sorted(back.items())
    ]
    for (loop, cls), n in sorted(classed.items()):
        back_edges.append({"from": loop, "to": "build", "label": f"FAIL({cls})", "count": n})
    extra = sum(max(0, n - 1) for n in rounds.values())
    if extra:
        back_edges.append({"from": "ready", "to": "build", "label": "new head", "count": extra})
    return {"nodes": nodes, "edges": edges, "back_edges": back_edges}


# ── Org graph ────────────────────────────────────────────────────


HEALTH_ORDER: Final = ("dead", "blocked", "degraded", "ok")


def org_graph(members: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """
    Members and their sub-agents, with spawn and supervise edges, grouped by project.

    :param members: ``{"id", "title", "role", "project", "parent", "supervisor",
        "health", "status", "state", "is_top_level", "depth", "harness"}`` per session.
    :returns: ``{"nodes", "edges", "groups"}``; a group is ``{"project",
        "members", "health": worst health, "by_health": {health: n}}``.
    """
    rows = list(members)
    ids = {str(m["id"]) for m in rows}
    edges: list[dict[str, Any]] = []
    for m in rows:
        parent, supervisor = m.get("parent"), m.get("supervisor")
        if parent and parent in ids and parent != m["id"]:
            edges.append({"from": parent, "to": m["id"], "kind": "spawn"})
        if supervisor and supervisor in ids and supervisor != m["id"] and m.get("is_top_level"):
            edges.append({"from": supervisor, "to": m["id"], "kind": "supervise"})
    groups: dict[str | None, list[Mapping[str, Any]]] = defaultdict(list)
    for m in rows:
        groups[m.get("project")].append(m)
    out_groups = []
    for project in sorted(groups, key=lambda p: (p is not None, p or "")):
        items = groups[project]
        healths = Counter(str(m.get("health") or "ok") for m in items)
        worst = next((h for h in HEALTH_ORDER if healths.get(h)), "ok")
        out_groups.append(
            {
                "project": project,
                "members": [m["id"] for m in items],
                "health": worst,
                "by_health": dict(healths),
            }
        )
    return {"nodes": rows, "edges": edges, "groups": out_groups}


__all__ = [
    "DEFAULT_WEIGHT_MIN",
    "FLOW_STAGES",
    "MailFact",
    "Slots",
    "SpanFact",
    "TaskNode",
    "WavePlan",
    "bus_edges",
    "critical_path",
    "deps_met",
    "find_cycle",
    "flow_graph",
    "globs_overlap",
    "is_external",
    "layers",
    "observed_p50",
    "org_graph",
    "overlap_warnings",
    "p50",
    "plan_waves",
    "ready_set",
    "scope_overlaps",
    "task_deps",
    "unknown_deps",
    "waiting_on",
    "weight_min",
]
