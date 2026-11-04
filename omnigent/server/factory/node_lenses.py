"""
Lenses on one node: where it came from, its children over time, their money.

Every view of the cockpit is a node, a level and a lens (``api.md`` §3.7).
These are the lenses the node page does not carry itself, each computed from
a built :class:`~omnigent.server.factory.nodes.Tree` (pure) plus, for the
timeline, the spans the store recorded:

- :func:`lineage` reads top to bottom: each ancestor with its one line (a
  need with its evidence, a feature with the proposal that approved it), the
  node itself, the levels below it with their counts and, in the product tree,
  the results (merged → in the base env → readouts).
- :func:`timeline` draws one lane per child: work as bars (``stage`` spans
  for projects, ``task`` spans for tasks), and waits as hatched gaps, amber
  (``you``) when a person is the cause (an open ask, a merge a person makes,
  an agent parked on a person) and grey (``wait``) when the lane waits on
  another node or on the factory (a past queue names the project it waited
  on). The wait a lane is in now is its status line, from when it began: not
  before the lane's first bar, nor before its last recorded work ended (an
  ask made while work still ran waits from when that work stopped). Lanes
  never join: there is nothing between them.
- :func:`money` lists the children's cost against their budgets, most first,
  and what the node spent that no child holds (its leads and agents).

Nothing here names a product: words come from the factory's rows and the
definition's level names.
"""

from __future__ import annotations

import heapq
import itertools
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import STAGE_DONE, STAGE_MERGED, STAGE_QUEUED, STAGE_READY
from omnigent.server.factory import alarms, node_refs, node_status
from omnigent.server.factory.definition_blocks import PRODUCT_TREE
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities.factory import FactorySpan
    from omnigent.server.factory.nodes import _N, Tree
    from omnigent.stores.factory_store import FactoryStore

SPAN_KINDS: Final = ("stage", "task", "ready_wait", "decision", "wait")
"""The span kinds a timeline reads."""
TIMELINE_DEFAULT_S: Final = 24 * 3600
"""A timeline shows the last day unless asked for another window (less when work is newer)."""
TIMELINE_MAX_S: Final = 7 * 24 * 3600
"""The longest window one timeline covers."""
LOOKBACK_S: Final = 2 * 24 * 3600
"""Spans that began this long before a window are read too (a stage still running)."""
SPANS_MAX: Final = 5000
_WAITING: Final = frozenset({"needs", "waiting", "notstarted", "paused", "broken"})
"""Lane states whose wait now is drawn up to now."""
_LEAF: Final[dict[str, int]] = {"work": 1, "wait": 2, "you": 3}
"""A project's or task's own lane: what holds it wins over the stage it is in."""
_ROLLUP: Final[dict[str, int]] = {"wait": 1, "you": 2, "work": 3}
"""A lane over several projects works while any of them does."""
_NOW: Final = 4
"""The wait a lane is in now wins over every span (it is the status line)."""
_ROLLUP_LEVELS: Final = frozenset({"goal", "need", "feature", "area"})
_WAIT_STAGES: Final[dict[str, str]] = {STAGE_QUEUED: "queued", STAGE_READY: "ready to merge"}
_CLOSED_STAGES: Final = frozenset({STAGE_MERGED, STAGE_DONE})
_LABEL_MAX: Final = 80
_PROPOSAL_TITLE_MAX: Final = 120


def iso(seconds: float) -> str:
    """``2026-11-03T13:40:00Z``: an instant as the node API writes it."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(seconds)))


def line_words(line: Mapping[str, Any]) -> str:
    """
    A compact status line as plain words (the web's ``statusModel.ts`` reads it
    the same way): ``Needs you: Apply C-16 · via T-16``,
    ``Waits on assistant-core · held for C-16``.
    """
    label = str(line.get("label") or "")
    out: list[str] = []
    via: list[str] = []
    person: Mapping[str, Any] | None = None
    for seg in line.get("segments") or []:
        if seg.get("person"):
            person = seg.get("decision") or {}
        elif seg.get("sep") == "›":
            via.append(str(seg.get("title") or seg.get("ref") or ""))
        else:
            words = str(seg.get("label") or "")
            target = str(seg.get("title") or seg.get("ref") or "")
            out.append(f"{words} {target}".strip())
    if person is not None:
        verb = str(person.get("verb") or "")
        alone = label.lower().startswith(("needs you", "waiting"))
        out.append(verb if alone else f"needs you: {verb}")
    text = " · ".join(o for o in out if o)
    if via:
        text = f"{text} · via {' › '.join(via)}" if text else f"via {' › '.join(via)}"
    if line.get("detail"):
        text = f"{text} · {line['detail']}" if text else str(line["detail"])
    return f"{label} {text}".strip()


# ── Lineage ────────────────────────────────────────────────────


def _origin(tree: Tree, node: _N) -> str | None:
    """The proposal (``C-n``) that approved a feature, when one did."""
    if node.level != "feature":
        return None
    if node.implicit:
        key = node.ref.partition(":")[2]
        found = key.removeprefix("from-") if key.startswith("from-C-") else None
        return found if found and node_refs.parse(found) is not None else None
    row = next((i for i in tree.facts.initiatives if i.ref == node.ref), None)
    created = (row.created_by or "") if row is not None else ""
    return created if node_refs.parse(created) is not None and created.startswith("C-") else None


def _lineage_row(tree: Tree, node: _N) -> dict[str, Any]:
    out = tree.crumb(node)
    out["line"] = tree.status(node, compact=True)
    if node.parent is None and node.why:
        out["why_html"] = node.why
    if progress := tree.progress(node):
        out["progress"] = progress
    evidence = node.evidence if isinstance(node.evidence, Mapping) else None
    if node.level == "need" and evidence and evidence.get("summary"):
        out["evidence"] = str(evidence["summary"])
    if origin := _origin(tree, node):
        proposal = tree.proposals_by_ref.get(origin)
        title = node_status.clip(proposal.title, _PROPOSAL_TITLE_MAX) if proposal else ""
        kind = "from" if node.implicit else "approved by"
        link: dict[str, Any] = {"kind": kind, "ref": origin, "title": title or origin}
        out["links"] = [link]
    return out


def lineage(tree: Tree, ref: str) -> dict[str, Any] | None:
    """
    ``GET …/n/{ref}/lineage``: the node's ancestors top to bottom, each with
    its one line, the node, the levels below it and (product tree) its results.

    :returns: ``None`` when no node has *ref*.
    """
    found = tree.lookup(ref)
    if found is None:
        return None
    node = tree.fold(found)
    out: dict[str, Any] = {
        "scope": node.ref,
        "ancestors": [_lineage_row(tree, a) for a in tree.ancestors(node.ref)],
        "here": _lineage_row(tree, node),
        "below": tree.levels_below(node),
        "version": tree.version,
    }
    if node.tree == PRODUCT_TREE:
        out["results"] = tree.results(node)
    return out


# ── Money ──────────────────────────────────────────────────────


def money(tree: Tree, ref: str) -> dict[str, Any] | None:
    """
    ``GET …/n/{ref}/money``: the children's cost against their budgets, most
    first (the same numbers as their roll-ups), the total, the node's budget
    and ``rest``: what the node spent that no child holds.

    :returns: ``None`` when no node has *ref*.
    """
    found = tree.lookup(ref)
    if found is None:
        return None
    node = tree.fold(found)
    rows: list[dict[str, Any]] = []
    for kid in tree.shown_children(node.ref):
        if kid.ghost or kid.level == "run":
            continue
        cost = tree.cost(kid) or 0.0
        budget = tree.budget(kid)
        if cost < 0.005 and not budget:
            continue
        row: dict[str, Any] = {
            "ref": kid.ref,
            "title": kid.title,
            "state": tree.state(kid.ref),
            "cost": round(float(cost), 2),
        }
        if kid.tag and kid.level in ("task", "change"):
            row["tag"] = kid.tag
        if budget:
            row["budget"] = budget
        if burn := tree.burn(kid):
            row["burn"] = burn
        rows.append(row)
    rows.sort(key=lambda r: (-r["cost"], -(r.get("budget") or 0), r["title"]))
    total = round(sum(r["cost"] for r in rows), 2)
    out: dict[str, Any] = {
        "scope": node.ref,
        "rows": rows,
        "total": total,
        "version": tree.version,
    }
    if budget := tree.budget(node):
        out["budget"] = budget
    own = tree.cost(node)
    if own is not None and own - total >= 0.01:
        out["rest"] = round(float(own) - total, 2)
    return out


# ── Timeline ───────────────────────────────────────────────────


@dataclass(frozen=True)
class _Bar:
    """One interval of a lane, in epoch milliseconds, before lanes are flattened."""

    start: int
    end: int
    kind: str
    label: str
    prio: int


@dataclass
class _Lane:
    """What a lane covers: its subtree's refs, projects, sessions and PRs."""

    refs: set[str] = field(default_factory=set)
    projects: set[str] = field(default_factory=set)
    sessions: dict[str, str] = field(default_factory=dict)
    prs: set[str] = field(default_factory=set)


def _bare(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return norm_id(value)
    except ValueError:
        return None


def _scope(tree: Tree, node: _N) -> _Lane:
    """The refs, project slugs, task sessions (-> task code) and PR ids under *node*."""
    lane = _Lane()
    for item in [node, *tree.subtree(node.ref)]:
        lane.refs.add(item.ref)
        if item.level == "project" and item.order and not item.ghost and not item.later:
            lane.projects.add(item.order)
        elif item.level == "task" and (row := tree.task_rows.get(item.ref)) is not None:
            for session in row.session_ids or []:
                if sid := _bare(session):
                    lane.sessions[sid] = item.tag or item.ref
        elif item.level == "change" and item.row_id:
            lane.prs.add(item.row_id)
    return lane


@dataclass(frozen=True)
class _Reads:
    """
    What a timeline reads from its spans once: each decision span's node and
    whether it held work, and when each project finished (its first
    ``merged`` or ``done`` stage, epoch milliseconds).
    """

    at: Mapping[str, str | None]
    held: Mapping[str, bool]
    finished: Mapping[str, int]


def _reads(tree: Tree, spans: Iterable[FactorySpan]) -> _Reads:
    """
    The node each decision span is about (the open decision's, else its
    subject's) and whether it held work (an open one as the tree reads it, an
    answered one by :func:`_answered_held`), and when each project finished.
    """
    open_ = {d.ref: d for d in tree.facts.decisions}
    at: dict[str, str | None] = {}
    held: dict[str, bool] = {}
    finished: dict[str, int] = {}
    for span in spans:
        if span.kind == "stage" and span.name in _CLOSED_STAGES and span.project_slug:
            slug = span.project_slug
            finished[slug] = min(finished.get(slug, span.started_at), span.started_at)
            continue
        if span.kind != "decision":
            continue
        attrs = span.attrs or {}
        decision = open_.get(span.name)
        if decision is not None:
            at[span.id] = tree.decision_node.get(decision.id)
            held[span.id] = tree.blocks(decision)
            continue
        subject = attrs.get("subject")
        found = tree.lookup(str(subject)) if subject else None
        at[span.id] = tree.fold(found).ref if found is not None else None
        held[span.id] = _answered_held(tree, attrs)
    return _Reads(at, held, finished)


def _answered_held(tree: Tree, attrs: Mapping[str, Any]) -> bool:
    """
    Whether an answered ask held work: it said it blocked, or it was a
    proposal's and the proposal holds work by the tree's rule
    (:meth:`~omnigent.server.factory.nodes.Tree.proposal_holds_work`): a
    factory change, a new project, or a change to a project still active. An
    ask on a project that is done held nothing, and neither did a proposal
    the factory no longer lists (rejected).
    """
    if attrs.get("blocking"):
        return True
    if attrs.get("source") != "proposal":
        return False
    proposal = node_status.subject_proposal(str(attrs.get("subject") or ""), tree.proposals_by_ref)
    return proposal is not None and tree.proposal_holds_work(proposal)


def _waited_on(tree: Tree, span: FactorySpan, reads: _Reads) -> str | None:
    """
    What a project's past queue waited on: the first project it depends on
    that had not finished when the queue began (``waits on assistant-core``).
    """
    node = tree.nodes.get(tree.project_ref(span.project_slug) or "")
    if node is None:
        return None
    for dep in node.depends_on:
        target = tree.nodes.get(tree.project_ref(dep) or "")
        if target is None:
            continue
        done_at = reads.finished.get(dep)
        if done_at is None or done_at > span.started_at:
            return f"waits on {tree.paths.name(target.ref)}"
    return None


def _span_bar(
    tree: Tree,
    span: FactorySpan,
    lane: _Lane,
    reads: _Reads,
    now_ms: int,
    prio: Mapping[str, int],
) -> _Bar | None:
    """*span* as a bar of *lane*, or ``None`` when it is not about the lane."""
    end = span.ended_at if span.ended_at is not None else now_ms
    attrs = span.attrs or {}
    kind: str | None = None
    label = ""
    if span.kind == "stage" and span.project_slug in lane.projects:
        if span.name in _CLOSED_STAGES:
            return None
        if span.name in _WAIT_STAGES:
            kind, label = "wait", _WAIT_STAGES[span.name]
            if span.name == STAGE_QUEUED and span.ended_at is not None:
                label = _waited_on(tree, span, reads) or label
        else:
            kind, label = "work", span.name.replace("_", " ")
        if len(lane.projects) > 1:
            label = f"{span.project_slug}: {label}"
    elif span.kind == "task":
        sid = _bare(span.session_id)
        if sid in lane.sessions:
            kind, label = "work", lane.sessions[sid or ""]
        elif span.project_slug in lane.projects:
            kind, label = "work", node_status.clip(span.name, _LABEL_MAX)
    elif span.kind == "ready_wait":
        pr_id = _bare(span.pr_id)
        if pr_id in lane.prs or (pr_id is None and span.project_slug in lane.projects):
            pr = tree.pr_rows.get(pr_id or "")
            number = f"#{pr.number} " if pr is not None else ""
            if pr is not None and tree.wfacts.merges_itself(pr):
                kind, label = "wait", f"{number}waits for the server to merge it"
            else:
                kind, label = "you", f"{number}ready to merge".strip()
    elif span.kind == "decision":
        source = str(attrs.get("source") or "")
        if source in alarms.ALARM_SOURCES:
            return None  # the factory's own alarms: noise it handles, never a person's cause
        at = reads.at.get(span.id)
        mine = at in lane.refs or (at is None and span.project_slug in lane.projects)
        if mine and reads.held.get(span.id):
            words = "needs you" if span.ended_at is None else "waited on a person"
            kind, label = "you", f"{span.name} · {words}"
    elif span.kind == "wait" and _bare(span.session_id) in lane.sessions:
        kind, label = "you", "waits on a person"
    if kind is None or end <= span.started_at:
        return None
    return _Bar(span.started_at, end, kind, node_status.clip(label, _LABEL_MAX), prio[kind])


def _hops(path: node_status.Path) -> bool:
    """Whether a path leaves the node for another one before it ends (``→`` to a ref)."""
    return any(s.sep == "→" and s.ref for s in path.steps)


def _settled(bars: Sequence[_Bar], now_ms: int) -> int | None:
    """When the lane's last recorded work ended (work still open runs to now: not counted)."""
    return max((b.end for b in bars if b.kind == "work" and b.end < now_ms), default=None)


def _since_work(bars: Sequence[_Bar], now_ms: int) -> list[_Bar]:
    """
    The lane's recorded waits still open, begun no earlier than its last work
    ended: an ask made while a task still ran held nothing until it stopped.
    """
    settled = _settled(bars, now_ms)
    if settled is None:
        return list(bars)
    return [
        _Bar(settled, b.end, b.kind, b.label, b.prio)
        if b.kind != "work" and b.end >= now_ms and b.start < settled
        else b
        for b in bars
    ]


def _now_bar(
    tree: Tree, node: _N, bars: Sequence[_Bar], now_ms: int, *, rollup: bool
) -> _Bar | None:
    """
    The wait the lane is in now, from when it began: amber when its own path
    ends at a person (no hop to another node), grey otherwise; its label is
    the lane's status line. It begins when the ask was made (or the wait
    began), never before the lane's first bar nor before its last recorded
    work ended.
    """
    state = tree.state(node.ref)
    if state == "ghost":
        ask = next((d for d in tree.decisions_at.get(node.ref, []) if tree.blocks(d)), None)
        if ask is None:
            return None
        start = int(ask.created_at) * 1000
        label = f"proposed · {ask.ref}"
        return _Bar(start, now_ms, "you", label, _NOW) if start < now_ms else None
    if state not in _WAITING:
        return None
    path = tree.paths.path(node.ref)
    ask = path.ask
    you = path.end == "person" and ask is not None and ask.blocking and not _hops(path)
    start: int | None = None
    if you and ask is not None and ask.since:
        start = ask.since * 1000
    else:
        sinces = [w.since for w in tree.own_waits(node) if w.since]
        if sinces:
            start = min(sinces) * 1000
        elif node.since:
            start = int(node.since) * 1000
    if not rollup:
        # A recorded wait still open is the same wait: it began then. A person
        # is the cause only from their own (an ask, a merge), never a queue.
        kinds = ("you",) if you else ("wait", "you")
        held = [b.start for b in bars if b.kind in kinds and b.end >= now_ms]
        if held:
            start = min(held) if start is None else min(start, *held)
    if start is None:
        start = max((b.end for b in bars if b.end < now_ms), default=None)
    if start is not None and bars:
        start = max(start, min(b.start for b in bars), _settled(bars, now_ms) or 0)
    if start is None or start >= now_ms:
        return None
    words = line_words(tree.status(node, compact=True)) or state
    return _Bar(start, now_ms, "you" if you else "wait", node_status.clip(words, _LABEL_MAX), _NOW)


def _flatten(bars: Sequence[_Bar], start: int, end: int) -> list[_Bar]:
    """
    The bars of one lane as disjoint segments in [start, end]: the highest
    priority wins, then the one that began last, then the shorter one (a task
    inside its stage). One sweep over the bars' ends (a heap of the bars that
    have begun, the ones that ended dropped when they reach the top).
    """
    clipped = sorted(
        (
            _Bar(max(b.start, start), min(b.end, end), b.kind, b.label, b.prio)
            for b in bars
            if b.end > start and b.start < end
        ),
        key=lambda b: b.start,
    )
    if not clipped:
        return []
    points = sorted({p for b in clipped for p in (b.start, b.end)})
    out: list[_Bar] = []
    begun: list[tuple[int, int, int, int]] = []
    pending = 0
    for left, right in itertools.pairwise(points):
        while pending < len(clipped) and clipped[pending].start <= left:
            bar = clipped[pending]
            heapq.heappush(begun, (-bar.prio, -bar.start, bar.end, pending))
            pending += 1
        while begun and begun[0][2] <= left:
            heapq.heappop(begun)
        if not begun:
            continue
        top = clipped[begun[0][3]]
        last = out[-1] if out else None
        same = last is not None and (last.kind, last.label) == (top.kind, top.label)
        if last is not None and same and last.end == left:
            out[-1] = _Bar(last.start, right, last.kind, last.label, last.prio)
        else:
            out.append(_Bar(left, right, top.kind, top.label, top.prio))
    return out


def _one_work(segments: list[_Bar]) -> list[_Bar]:
    """
    A roll-up lane works while any of it does: work that runs on without a
    gap is one bar, named by what took longest in it (``+2``: and two more).
    """
    out: list[_Bar] = []
    run: list[_Bar] = []
    for seg in [*segments, None]:
        if seg is not None and seg.kind == "work" and (not run or run[-1].end == seg.start):
            run.append(seg)
            continue
        if run:
            took: dict[str, int] = {}
            for part in run:
                took[part.label] = took.get(part.label, 0) + part.end - part.start
            label = max(took, key=lambda k: took[k])
            if len(took) > 1:
                label = f"{node_status.clip(label, _LABEL_MAX - 6)} +{len(took) - 1}"
            out.append(_Bar(run[0].start, run[-1].end, "work", label, run[0].prio))
            run = []
        if seg is not None:
            if seg.kind == "work":
                run.append(seg)
            else:
                out.append(seg)
    return out


def _fill(segments: list[_Bar]) -> list[_Bar]:
    """Gaps between a lane's segments are waits: nothing in it worked then."""
    out: list[_Bar] = []
    for seg in segments:
        if out and seg.start > out[-1].end:
            out.append(_Bar(out[-1].end, seg.start, "wait", "waiting", 0))
        out.append(seg)
    return out


def _lane_bars(
    tree: Tree,
    node: _N,
    spans: Sequence[FactorySpan],
    reads: _Reads,
    now_ms: int,
    window: tuple[int, int],
) -> list[_Bar]:
    rollup = node.level in _ROLLUP_LEVELS
    prio = _ROLLUP if rollup else _LEAF
    lane = _scope(tree, node)
    found = [b for s in spans if (b := _span_bar(tree, s, lane, reads, now_ms, prio)) is not None]
    bars = _since_work(found, now_ms)
    if (now := _now_bar(tree, node, bars, now_ms, rollup=rollup)) is not None:
        bars.append(now)
    flat = _flatten(bars, *window)
    segments = _fill(_one_work(flat) if rollup else flat)
    if tree.state(node.ref) == "done":
        segments = [
            _Bar(s.start, s.end, "done", s.label, s.prio) if s.kind == "work" else s
            for s in segments
        ]
    return segments


def timeline(
    tree: Tree,
    ref: str,
    spans: Iterable[FactorySpan],
    *,
    start: int | None = None,
    end: int | None = None,
    last_look: int | None = None,
) -> dict[str, Any] | None:
    """
    ``GET …/n/{ref}/timeline``: one lane per child (folded; runs left out),
    each a row of bars over the window. Lanes with nothing to draw are left
    out, so a node whose children recorded nothing has no lanes.

    :param spans: The factory's spans of :data:`SPAN_KINDS` around the window
        (:func:`timeline_spans`); a span still open runs to now.
    :param start: The window's start (epoch seconds); default: the last day
        (:data:`TIMELINE_DEFAULT_S`), from the hour the first bar began when
        that is later.
    :param end: The window's end (epoch seconds); default: now.
    :param last_look: When this viewer last looked at the node (the shaded band).
    :returns: ``None`` when no node has *ref*.
    """
    found = tree.lookup(ref)
    if found is None:
        return None
    node = tree.fold(found)
    now = int(tree.facts.now)
    stop = int(end) if end is not None else now
    now_ms = min(now, stop) * 1000
    rows = list(spans)
    reads = _reads(tree, rows)
    begin = int(start) if start is not None else stop - TIMELINE_DEFAULT_S
    lanes: list[tuple[_N, list[_Bar]]] = []
    for kid in tree.shown_children(node.ref):
        if kid.level == "run" or kid.later:
            continue
        bars = _lane_bars(tree, kid, rows, reads, now_ms, (begin * 1000, stop * 1000))
        if bars:
            lanes.append((kid, bars))
    first = min((b.start for _kid, bars in lanes for b in bars), default=None)
    if start is None and first is not None:
        # The lanes were drawn over the last day; the axis starts at the hour work began.
        begin = max(begin, (first // 1000) // 3600 * 3600)
    out: dict[str, Any] = {
        "scope": node.ref,
        "axis": [iso(begin), iso(stop)],
        "now": iso(now),
        "lanes": [
            {
                "ref": kid.ref,
                "title": tree.paths.name(kid.ref),
                "state": tree.state(kid.ref),
                "bars": [
                    {
                        "from": iso(b.start / 1000),
                        "to": iso(b.end / 1000),
                        "kind": b.kind,
                        "label": b.label,
                    }
                    for b in bars
                ],
            }
            for kid, bars in lanes
        ],
        "version": tree.version,
    }
    if last_look is not None and last_look < stop:
        out["last_look"] = iso(max(last_look, begin))
    return out


def timeline_spans(
    store: FactoryStore, factory_id: str, start: int, end: int
) -> list[FactorySpan]:
    """
    The spans a timeline over [*start*, *end*] (epoch seconds) reads: those
    that began in it or shortly before, and the stages and asks still open
    however long ago they began.
    """
    found = {
        s.id: s
        for s in store.list_spans(
            factory_id,
            kinds=list(SPAN_KINDS),
            since_ms=(start - LOOKBACK_S) * 1000,
            until_ms=end * 1000,
            limit=SPANS_MAX,
            newest_first=True,
        )
    }
    for kind in ("stage", "decision", "ready_wait"):
        for span in store.list_open_spans(factory_id, kind):
            if span.started_at < end * 1000:
                found.setdefault(span.id, span)
    return sorted(found.values(), key=lambda s: (s.started_at, s.id))


__all__ = [
    "LOOKBACK_S",
    "SPAN_KINDS",
    "TIMELINE_DEFAULT_S",
    "TIMELINE_MAX_S",
    "iso",
    "line_words",
    "lineage",
    "money",
    "timeline",
    "timeline_spans",
]
