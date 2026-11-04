"""
The cockpit's levels: today's factory objects projected onto one strict tree.

Every node has exactly one parent, on the level directly above it, in its own
tree (product › goal › need › feature › project › task › change › run, and
fx › area › item). :class:`Tree` builds the projection from one load of facts
(:class:`NodeFacts`: the cached snapshot's project views plus the store rows
the views do not carry) and answers ``GET /factories/{fid}/n/{ref}`` one node
at a time (:meth:`Tree.page`): the node, its breadcrumb, its state and
progress, its children (60 a page) in order columns, grandchildren only as
state counts, the levels below it and its results. Nothing is stored here;
:func:`tree_for` builds one viewer's tree from the snapshot cut to what they
may read, memoizes it for :data:`FACTS_TTL_S` and drops it on any write the
event log records.

Adapters (``api.md`` §5):

- product: the definition's product name; goals: ``strategy.goals`` (``G-n``).
- need: open opportunities (``O-n``); a person's ask (``ask:<message id>``,
  from an initiative's ask or a projects proposal that answers one); else
  implicit ``need:from-C-n`` / ``need:by-<who>``; a need with no goal sits
  under the implicit ``goal:no-goal`` once the factory has goals.
- feature: initiatives (``I-n``); a project with no home feature
  (:mod:`.features`) gets the implicit ``feature:from-C-n`` of the proposal
  that created it (``feature:by-<who>`` without one). An initiative's later
  waves are held children (Later); open proposals that would add a project
  to it are ghosts.
- project: work projects; integration and self projects are items of the
  factory tree (under Environments). Tasks (``T-n``), their PRs
  (``PR-n``; a PR no task owns gets the implicit task ``adhoc:<pr id>``; a
  number two repos share is ``PR-n`` in the factory's repo and
  ``commit:<sha>`` in the other) and the sessions that ran a task
  (``run:<session id>``, under its PR; a task with runs and no PR yet has no
  run page yet).
- states: a closed project is done whatever tasks it left (those are history
  and read "Closed"); a project between queued and ready moves only while its
  lead or a teammate works, else it waits.
- the factory tree: its areas (each with its one line), open rule proposals
  (``C-n``), the working top-level members (and any an open ask names; idle
  ones are counted on Team) and the environments.

Implicit parents are keyed by their origin and fold into their only child
(``structure.fold_single_child``). A level hidden by the definition's
``show`` rule (``when_any`` with no node, ``when_many`` with one) is left out
and its nodes' children hang one level up. :func:`check_levels` proves the
one-parent rule on a built tree. Each node's status line is its blocker path
(:mod:`.node_status`); roll-ups add burn (``turn`` spans), budgets, an ETA
only from observed durations, Try it from passing acceptance steps and what
is in the base env (:func:`.base.contains`); the factory tree's Health lists
the alarms the server closed (:mod:`.alarms`).

Nothing here names a product: titles come from the factory's records and
level names from its definition.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import html
import logging
import re
import statistics
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    PROJECT_KIND_WORK,
    PROJECT_SLUG_RE,
    PROPOSAL_OPEN_STATUSES,
    STAGE_DONE,
    STAGE_MERGED,
    STAGE_QUEUED,
    STAGE_READY,
)
from omnigent.server.factory import (
    alarms,
    auto_merge,
    autonomy,
    features,
    levels,
    node_refs,
    node_status,
    subtrees,
    waits,
)
from omnigent.server.factory.base import DeployedHead, contains, same_commit
from omnigent.server.factory.charter import (
    change_dependency,
    normalize_charter,
    project_base_branch,
    project_kind,
)
from omnigent.server.factory.definition_blocks import (
    FACTORY_TREE,
    KIND_IS_LEVEL,
    PRODUCT_TREE,
    TREES,
)
from omnigent.server.factory.graph import DEFAULT_WEIGHT_MIN, TaskNode, critical_path, layers
from omnigent.server.factory.initiative_view import RUN_DONE, plan_of
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import (
        FactoryDecision,
        FactoryInitiative,
        FactoryLoop,
        FactoryPr,
        FactoryProject,
        FactoryProposal,
        FactorySpan,
        FactoryTask,
    )
    from omnigent.entities.factory_discovery import FactoryOpportunity
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.snapshot import RawSnapshot

_logger = logging.getLogger(__name__)

FACTS_TTL_S: Final = 5.0
"""How long one factory's projection is reused (any recorded write drops it sooner)."""
BURN_WINDOW_S: Final = 3600
"""Burn is the spend of the last hour."""
TURNS_LOOKBACK_S: Final = 3 * 3600
"""Turn spans read for the burn: long turns that began earlier still end in the hour."""
PAGE_SIZE: Final = 60
"""Children per page (``children.next`` reads the next ones)."""
NO_GOAL: Final = "goal:no-goal"
"""The implicit goal of needs that name none, once the factory has goals."""
_HEALTH: Final = f"{node_refs.FX}:health"
"""The factory area that lists the alarms the server closed."""
_ENVIRONMENTS: Final = f"{node_refs.FX}:environments"
"""The factory area that shows the base env (as the product root carries it)."""
_DEFINITION: Final = f"{node_refs.FX}:definition"
"""The factory area whose page renames the levels (``rename``)."""
ROW_LEVELS: Final = frozenset({"task", "change", "run"})
"""Levels drawn only as rows (never tiles); their crumbs carry their code."""

STATE_RANK: Final[dict[str, int]] = {
    "broken": 7,
    "needs": 6,
    "waiting": 5,
    "paused": 5,
    "moving": 4,
    "notstarted": 3,
    "offered": 3,
    "done": 1,
}
"""Worst first: a parent's state is the worst among its active children."""
_STATUS: Final[dict[str, tuple[str, str]]] = {
    "broken": ("needs", "Broken"),
    "needs": ("needs", "Needs you"),
    "waiting": ("waiting", "Waiting"),
    "paused": ("paused", "Paused"),
    "moving": ("moving", "Moving"),
    "notstarted": ("idle", "Not started"),
    "offered": ("offered", "Offered"),
    "done": ("done", "Done"),
    "later": ("later", "Later"),
    "ghost": ("proposed", "Proposed"),
}
"""State -> ``(StatusLine.kind, label)`` of the state-only status line."""
_TASK_STATE: Final[dict[str, str]] = {
    "planned": "notstarted",
    "ready": "notstarted",
    "running": "moving",
    "review": "moving",
    "blocked": "waiting",
    "done": "done",
    "failed": "broken",
}
_STAGE_STATE: Final[dict[str, str]] = {
    STAGE_QUEUED: "notstarted",
    STAGE_READY: "waiting",
    STAGE_MERGED: "done",
    STAGE_DONE: "done",
}
"""A project's own state by stage. Between queued and ready it moves only while
its lead or a teammate is working; otherwise it waits (:func:`_project_own`)."""
_PROJECT_WAIT_KINDS: Final = frozenset(
    {"stall", "proposal", "hold", "merge", "dependency", "capacity"}
)
"""Project waits that make it wait (an open question is a decision: it needs you)."""
_BUILDING: Final = frozenset({"running", "review"})
"""Task states whose builder works whatever its project still waits on."""
_RULE_KINDS: Final = frozenset({"tuning", "rules", "code"})
_RULE_OPEN: Final = frozenset({*PROPOSAL_OPEN_STATUSES, "trial"})
READ_PROPOSALS: Final = (*sorted(_RULE_OPEN), "applied")
"""The proposals the projection reads: open ones (ghosts, rules, decisions),
on trial (rules) and applied (what created a project; whose ask it answers)."""
_GONE: Final = frozenset({"cancelled", "merged"})
"""Project statuses that leave the tree (merged: folded into another project)."""
_FACTORY_SOURCES: Final[dict[str, str]] = {"access_request": "fx:team", "storm": "fx:health"}
"""Decisions about the factory itself, by source, when their subject names no node."""
_GLYPHS: Final[dict[str, str]] = {
    "product": "◆",
    "goal": "G",
    "ask": "ASK",
    "proposal": "C",
    "opportunity": "O",
    "study": "R",
    "task": "T",
    "adhoc": "T",
    "pr": "PR",
    "commit": "⎇",
    "run": "▶",
    "area": "▦",
    "item": "•",
    "rule": "§",
    "env": "⌂",
    "agent": "@",
}
_VERB_LABELS: Final[dict[str, tuple[str, str]]] = {
    "talk": ("Talk", "/"),
    "add": ("Add child", "a"),
    "start": ("Start now", "s"),
    "research": ("Start research", "s"),
    "team": ("Team", "t"),
    "auto": ("Autonomy & merge", "u"),
    "pause": ("Pause", "p"),
    "resume": ("Resume", "p"),
    "move": ("Move", "m"),
    "name": ("Name it / file under", "n"),
    "rename": ("Rename levels", "r"),
}
_READ_ONLY: Final = "You can read this factory; ask an owner for edit access to steer it."
_AREA_WHY: Final[dict[str, str]] = {
    "rules": "Rules agents follow, with trials. Changes are proposed from what went wrong.",
    "team": "Only working agents are drawn; idle ones are counted.",
    "environments": "The base env, its previews, and the projects that set them up.",
    "health": "Alarms the factory handled by itself, each with its cause. An alarm reaches "
    "you only when its cause path ends at a person.",
    "definition": "The factory's definition, versioned: levels, autonomy, budgets and gates. "
    "Level names come from here, not from code.",
}
"""Each factory area's one line on what it holds (``fx:<area>``)."""
_WHO_RE: Final = re.compile(r"[^a-z0-9]+")
_TITLE_MAX: Final = 120
_SHORT_MAX: Final = 140
_WHY_MAX: Final = 600


# ── Facts ──────────────────────────────────────────────────────


@dataclass
class NodeFacts:
    """
    What the projection reads, loaded once per factory (:func:`load_facts`).

    :param product: The product's name (the definition's, else the factory's).
    :param repo: The factory's default repo, ``owner/name``.
    :param base: Its default base branch.
    :param structure: The definition's ``structure`` block (levels, kinds, verbs …).
    :param goals: ``strategy.goals`` (``{id, statement, …}``).
    :param views: Project slug -> its snapshot ``ProjectView`` JSON (stage,
        cost, team, what it waits on).
    :param proposals: Open, on-trial and applied proposals (:data:`READ_PROPOSALS`).
    :param decisions: The open decisions the viewer may read.
    :param members: The viewer's snapshot's member views (``session_id``,
        ``title``, ``running``, ``is_top_level``, ``role``).
    :param asks: Chat message id (bare hex) -> ``(text, who)`` of the asks
        that the proposals behind live nodes answer.
    :param base_env: The snapshot's ``factory.base`` (head, the live env and
        the commit it runs, ``deployed``).
    :param cost: What the viewer may see spent so far; ``burn`` its spend
        per hour (``None`` when part of the factory is hidden from them).
    :param loops: The bad loops (stalled, exhausted, thrashing): stall waits.
    :param leads: Project slug -> its lead's session (questions its lead asked).
    :param targets: The owner-set auto-merge targets the server merges into now.
    :param merge_scopes: The definition's auto-merge per subtree (``auto_merge_scopes``),
        whether or not this server runs the merger: what the owners set.
    :param autonomy_scopes: ``autonomy.scopes`` as ``{ref: level}``; *level* the
        factory's own ``autonomy.level``.
    :param lineage: Where each project sits (feature, need, goal), as the
        merger and the autonomy decision read it.
    :param observed: Task kind -> observed p50 minutes (kinds with at least five
        samples): the only inputs an ETA is computed from.
    :param turns: Recent ``turn`` spans (the cost of the last hour: burn).
    :param closed_alarms: Alarms the server closed today, with their causes.
    """

    factory_id: str
    product: str
    repo: str | None = None
    base: str | None = None
    structure: Mapping[str, Any] = field(default_factory=dict)
    definition_version: int = 0
    goals: Sequence[Mapping[str, Any]] = ()
    opportunities: Sequence[FactoryOpportunity] = ()
    initiatives: Sequence[FactoryInitiative] = ()
    proposals: Sequence[FactoryProposal] = ()
    projects: Sequence[FactoryProject] = ()
    views: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    tasks: Sequence[FactoryTask] = ()
    prs: Sequence[FactoryPr] = ()
    decisions: Sequence[FactoryDecision] = ()
    members: Sequence[Mapping[str, Any]] = ()
    asks: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    base_env: Mapping[str, Any] | None = None
    cost: float | None = None
    burn: float | None = None
    now: float = 0.0
    loops: Sequence[FactoryLoop] = ()
    leads: Mapping[str, str | None] = field(default_factory=dict)
    targets: auto_merge.TargetsLike = frozenset()
    merge_scopes: Mapping[str, auto_merge.MergeScope] = field(default_factory=dict)
    autonomy_scopes: Mapping[str, str] = field(default_factory=dict)
    level: str = autonomy.DEFAULT_LEVEL
    lineage: subtrees.Lineage = field(default_factory=subtrees.Lineage)
    observed: Mapping[str, float] = field(default_factory=dict)
    turns: Sequence[FactorySpan] = ()
    closed_alarms: Sequence[alarms.ClosedAlarm] = ()

    def version(self) -> str:
        """:returns: A digest of the rows read (``NodePage.version``; a write's ``based_on``)."""
        parts: list[str] = [self.factory_id, str(self.definition_version)]
        for name, rows in (
            ("o", self.opportunities),
            ("i", self.initiatives),
            ("c", self.proposals),
            ("p", self.projects),
            ("t", self.tasks),
            ("r", self.prs),
            ("d", self.decisions),
        ):
            stamps = [int(getattr(r, "updated_at", 0) or 0) for r in rows]
            parts.append(f"{name}{len(stamps)}:{max(stamps, default=0)}:{sum(stamps)}")
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _bare(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return norm_id(value)
    except ValueError:
        return None


def _ask_ids(
    initiatives: Sequence[FactoryInitiative],
    proposals: Sequence[FactoryProposal],
    projects: Sequence[FactoryProject],
) -> set[str]:
    """
    The chat messages live nodes quote: the ask a projects proposal answers on a
    person's behalf, for the proposals that created a live project or started a
    live initiative (an initiative that carries the ask itself needs no read).
    """
    live = [i for i in initiatives if i.status not in features.CLOSED]
    covered = {_bare((i.ask_ref or "").removeprefix("chat_")) for i in live}
    origins = {i.created_by for i in live if (i.created_by or "").startswith("C-")}
    open_slugs = {p.slug for p in projects if p.status not in _GONE}
    for project in projects:
        if project.slug in open_slugs and (origin := features.origin_proposal(project)):
            origins.add(origin)
    for row in proposals:
        created = (row.result or {}).get("created") if row.status == "applied" else None
        if isinstance(created, list) and open_slugs.intersection(str(s) for s in created):
            origins.add(row.ref)
    wanted = {
        _bare(p.reply_to_chat)
        for p in proposals
        if p.ref in origins and p.kind == "projects" and p.on_behalf_of and p.reply_to_chat
    }
    return {m for m in wanted - covered if m}


def load_facts(
    deps: FactoryDeps,
    root: Conversation,
    body: Mapping[str, Any],
    *,
    readable: Iterable[str] | None = None,
    now: float | None = None,
    leads: Mapping[str, str | None] | None = None,
    targets: auto_merge.TargetsLike = frozenset(),
) -> NodeFacts:
    """
    Read what the projection needs (synchronous; a handful of row reads).

    :param body: The viewer's snapshot (``snapshot.filter_snapshot``): its
        project views, members, base env and totals are reused, not rebuilt,
        already cut to what the viewer may read.
    :param readable: The sessions the viewer may read; open decisions asked by
        any other session are left out (``None``: every decision).
    :param leads: Project slug -> its lead's session (the snapshot's); else
        each project row's own.
    :param targets: The auto-merge targets the server merges into now.
    """
    from omnigent.server.factory import definition
    from omnigent.server.factory.tasks import observed_weights

    store = deps.factory_store
    fid = root.id
    clock = time.time() if now is None else now
    resolved = definition.definition_for(deps, fid)
    doc = resolved.doc if resolved is not None else definition.default_doc()
    factory = body.get("factory") or {}
    base = factory.get("base") or {}
    initiatives = store.list_initiatives(fid, limit=5000)
    opportunities = store.list_opportunities(fid, limit=5000)
    goals = list((doc.get("strategy") or {}).get("goals") or [])
    lineage = subtrees.lineage_of(initiatives, opportunities, goals)
    found_targets = auto_merge.as_targets(targets)
    if found_targets.scopes:
        found_targets = found_targets.with_lineage(lineage)
    delivery = (doc.get("product") or {}).get("delivery") or {}
    own = doc.get("autonomy") or {}
    proposals = store.list_proposals(fid, status=list(READ_PROPOSALS), limit=5000)
    projects = store.list_projects(fid)
    asks: dict[str, tuple[str, str]] = {}
    found = store.get_chat_messages(sorted(_ask_ids(initiatives, proposals, projects)))
    for message_id, message in sorted(found.items()):
        if message.body.strip():
            asks[message_id] = (message.body.strip(), message.author or "")
    decisions = store.list_decisions(factory_id=fid, status="open", limit=1000)
    if readable is not None:
        sessions = set(readable)
        decisions = [
            d for d in decisions if not d.asked_by_session_id or d.asked_by_session_id in sessions
        ]
    product = (doc.get("product") or {}).get("name") or factory.get("name") or ""
    totals = body.get("totals") or {}
    turns = store.list_spans(
        fid,
        kinds=["turn"],
        since_ms=int((clock - TURNS_LOOKBACK_S) * 1000),
        limit=5000,
        newest_first=True,
    )
    return NodeFacts(
        factory_id=norm_id(fid),
        product=str(product),
        repo=base.get("repo"),
        base=base.get("branch"),
        structure=doc.get("structure") or {},
        definition_version=resolved.version if resolved is not None else 0,
        goals=goals,
        opportunities=opportunities,
        initiatives=initiatives,
        proposals=proposals,
        projects=projects,
        views={str(v.get("slug")): v for v in body.get("projects") or []},
        tasks=store.list_tasks(fid),
        prs=store.list_prs(fid),
        decisions=decisions,
        members=list(body.get("members") or []),
        asks=asks,
        base_env=base or None,
        cost=factory.get("cost_usd"),
        burn=totals.get("usd_per_h"),
        now=clock,
        loops=store.list_loops(fid, states=sorted(waits.BAD_LOOP_STATES)),
        leads=dict(leads) if leads is not None else {p.slug: p.pl_session_id for p in projects},
        targets=found_targets,
        merge_scopes=auto_merge.scopes_of(delivery),
        autonomy_scopes=autonomy.scopes_of(own.get("scopes")),
        level=str(own.get("level") or autonomy.DEFAULT_LEVEL),
        lineage=lineage,
        observed=observed_weights(deps, fid),
        turns=turns,
        closed_alarms=alarms.load(store, fid, clock),
    )


# ── The tree ───────────────────────────────────────────────────


@dataclass(eq=False)
class _N:
    """One projected node (internal; :meth:`Tree.page` turns it into the API's shapes)."""

    ref: str
    tree: str
    level: str
    kind: str
    title: str
    parent: str | None = None
    implicit: bool = False
    ghost: bool = False
    later: bool = False
    paused: bool = False
    own: str = "notstarted"
    leaf: bool = False
    waits: bool = False
    tag: str | None = None
    short: str | None = None
    why: str = ""
    held_for: str | None = None
    order: str | None = None
    deps: tuple[str, ...] = ()
    estimate: int | None = None
    cost: float | None = None
    budget: float | None = None
    base: str | None = None
    owner: str | None = None
    working: tuple[str, ...] = ()
    idle: int | None = None
    merged_at: int | None = None
    merged_sha: str | None = None
    acceptance: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    also_serves: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    depends_why: Mapping[str, str] = field(default_factory=dict)
    col: int | None = None
    note: str | None = None
    history: bool = False
    row_id: str | None = None
    since: int | None = None
    done_note: str | None = None
    steps: tuple[tuple[int, str, str | None, str], ...] = ()
    surfaces: tuple[str, ...] = ()


def _esc(text: Any) -> str:
    return html.escape(str(text or ""), quote=False)


def _clip(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _who(value: str | None) -> str:
    """An implicit key's origin words for a creator (``alice@x`` -> ``alice``)."""
    head = (value or "").split("@", 1)[0].lower()
    return (_WHO_RE.sub("-", head).strip("-") or "unknown")[:40]


def creator_words(value: str | None) -> str:
    """The ``<who>`` of the implicit ``by-<who>`` parents of what *value* started."""
    return _who(value)


def _project_ref(slug: str) -> str:
    return node_refs.Ref("project", slug).text


class Tree:
    """
    One factory projected onto the levels (pure: built from :class:`NodeFacts`).

    :param hidden: Project slugs this viewer may not read: they are left out,
        with everything under them.
    """

    def __init__(self, facts: NodeFacts, hidden: Iterable[str] = ()) -> None:
        self.facts = facts
        self.hidden = frozenset(hidden)
        structure = facts.structure or {}
        self.level_defs: Mapping[str, Mapping[str, Any]] = structure.get("levels") or {}
        self.kind_names: dict[str, str] = {
            kind: self._level_name(key) for kind, key in KIND_IS_LEVEL.items()
        } | dict(structure.get("kinds") or {})
        self.order_cols: list[str] = list((structure.get("order") or {}).get("cols") or ["First"])
        self.later_title: str = (structure.get("order") or {}).get("later") or "Later"
        self.responders: Mapping[str, Any] = structure.get("responders") or {}
        self.level_verbs: Mapping[str, Sequence[str]] = structure.get("verbs") or {}
        self.home: Mapping[str, str] = structure.get("home") or {}
        self.fold_enabled = structure.get("fold_single_child", "implicit_only") != "never"
        self.version = facts.version()
        self.nodes: dict[str, _N] = {}
        self.kids: dict[str, list[str]] = {}
        self.decisions_at: dict[str, list[FactoryDecision]] = {}
        self.decision_node: dict[str, str] = {}
        self.hidden_levels: dict[str, list[str]] = {}
        self._memo: dict[tuple[str, str], Any] = {}
        self._own: dict[str, list[waits.Wait]] = {}
        self.project_rows: dict[str, FactoryProject] = {
            p.slug: p for p in facts.projects if p.slug not in self.hidden
        }
        self.task_rows: dict[str, FactoryTask] = {
            t.ref: t for t in facts.tasks if t.project_slug not in self.hidden
        }
        self.pr_rows: dict[str, FactoryPr] = {norm_id(p.id): p for p in facts.prs}
        self.wfacts = waits.Facts(
            projects={p.slug: p for p in facts.projects},
            prs=facts.prs,
            decisions=facts.decisions,
            proposals=facts.proposals,
            tasks=facts.tasks,
            loops=facts.loops,
            leads=facts.leads,
            now=facts.now,
            targets=facts.targets,
        )
        self.closed_alarms = alarms.visible(facts.closed_alarms, self.hidden)
        self._quiet = self._held_alarms()
        self._build()
        self._hide_levels()
        self._index()
        self._place_decisions()
        self._order_columns()
        self.paths = node_status.Paths(self)

    # ── Building ───────────────────────────────────────────────

    def _level_name(self, key: str, plural: bool = False) -> str:
        level = self.level_defs.get(key) or {}
        name = level.get("plural" if plural else "name")
        return str(name) if name else key.replace("_", " ").capitalize()

    def _add(self, node: _N) -> _N:
        if node.ref in self.nodes:
            return self.nodes[node.ref]
        self.nodes[node.ref] = node
        return node

    def _build(self) -> None:
        facts = self.facts
        self._members = {
            _bare(m.get("session_id")) or "": m for m in facts.members if m.get("session_id")
        }
        self._roots()
        self._goals()
        self._opportunities()
        self._proposals = {p.ref: p for p in facts.proposals}
        self.proposals_by_ref = self._proposals
        self._created_by = self._creators()
        self._initiatives()
        self._projects()
        self._later_and_ghosts()
        self._tasks_and_changes()
        self._factory_items()

    def _roots(self) -> None:
        facts = self.facts
        repo, base = facts.repo, facts.base
        why = f"Builds <b>{_esc(repo)}</b> into <b>{_esc(base)}</b>." if repo and base else ""
        self._add(
            _N(
                node_refs.PRODUCT,
                PRODUCT_TREE,
                "product",
                "product",
                _clip(facts.product, _TITLE_MAX) or self._level_name("product"),
                why=why,
            )
        )
        self._add(
            _N(
                node_refs.FX,
                FACTORY_TREE,
                "fx",
                "factory",
                self._level_name("fx"),
                why="How the factory itself runs: its team, rules, environments, "
                "health and definition. Product work never shows here.",
            )
        )
        for key, title in node_refs.FACTORY_AREAS.items():
            self._add(
                _N(f"{node_refs.FX}:{key}", FACTORY_TREE, "area", "area", title, node_refs.FX)
            )

    def _goals(self) -> None:
        self._goal_ids: set[str] = set()
        for goal in self.facts.goals:
            ref = str(goal.get("id") or "")
            if node_refs.parse(ref) is None:
                continue
            self._goal_ids.add(ref)
            self._add(
                _N(
                    ref,
                    PRODUCT_TREE,
                    "goal",
                    "goal",
                    _clip(goal.get("statement"), _TITLE_MAX),
                    node_refs.PRODUCT,
                    why=_esc(goal.get("statement")),
                    tag=ref,
                )
            )

    def _need_parent(self, goal: str | None) -> str:
        """Where a need hangs: its goal, else "No goal yet" once goals exist, else the product."""
        if not self._goal_ids:
            return node_refs.PRODUCT
        if goal and goal in self._goal_ids:
            return goal
        self._add(
            _N(
                NO_GOAL,
                PRODUCT_TREE,
                "goal",
                "goal",
                "No goal yet",
                node_refs.PRODUCT,
                implicit=True,
                why="Needs that name no goal. File each under a goal.",
            )
        )
        return NO_GOAL

    def _opportunities(self) -> None:
        for opp in self.facts.opportunities:
            if opp.kill or opp.merged_into:
                continue
            park = opp.park or {}
            self._add(
                _N(
                    opp.ref,
                    PRODUCT_TREE,
                    "need",
                    "opportunity",
                    _clip(opp.title or opp.statement, _TITLE_MAX),
                    self._need_parent(opp.goal),
                    tag=opp.ref,
                    why=_esc(opp.statement),
                    later=bool(opp.park),
                    held_for=_clip(park.get("reason"), _SHORT_MAX) or None if opp.park else None,
                )
            )

    def _creators(self) -> dict[str, str]:
        """Slug -> the applied proposal that created it (its ``result.created``)."""
        out: dict[str, str] = {}
        for row in sorted(self.facts.proposals, key=lambda p: p.number):
            created = (row.result or {}).get("created") if row.status == "applied" else None
            for slug in created if isinstance(created, list) else []:
                out.setdefault(str(slug), row.ref)
        return out

    def _ask_need(self, message_id: str | None, text: str, who: str, at_goal: str | None) -> str:
        """The need of a person's ask (``ask:<message id>``), made once."""
        ref = f"ask:{message_id}"
        if ref not in self.nodes:
            self._add(
                _N(
                    ref,
                    PRODUCT_TREE,
                    "need",
                    "ask",
                    _clip(text.splitlines()[0] if text else "", _TITLE_MAX) or "An ask",
                    self._need_parent(at_goal),
                    why=f"<b>“{_esc(_clip(text, _WHY_MAX))}”</b>" if text else "",
                    evidence={
                        "summary": "an ask · no user evidence yet",
                        "items": [
                            {
                                "kind": "request",
                                "text": _clip(text, 2000),
                                **({"who": who} if who else {}),
                                "quote": True,
                            }
                        ],
                    }
                    if text
                    else None,
                )
            )
        return ref

    def _implicit_need(self, key: str, title: str, why: str) -> str:
        ref = f"need:{key}"
        self._add(
            _N(
                ref,
                PRODUCT_TREE,
                "need",
                "proposal",
                title,
                self._need_parent(None),
                implicit=True,
                why=why,
            )
        )
        return ref

    def _open_need(self, ref: str | None) -> str | None:
        return ref if ref and ref in self.nodes and self.nodes[ref].level == "need" else None

    def _proposal_need(self, proposal: FactoryProposal | None, origin: str) -> str:
        """The need a proposal's work answers: its opportunity, its ask, else an implicit one."""
        if proposal is not None:
            if found := self._open_need(proposal.opportunity):
                return found
            message = _bare(proposal.reply_to_chat) if proposal.on_behalf_of else None
            if message and f"ask:{message}" in self.nodes:
                return f"ask:{message}"
            if message and message in self.facts.asks:
                text, who = self.facts.asks[message]
                return self._ask_need(message, text, who, None)
        return self._implicit_need(
            f"from-{origin}",
            f"From {origin}",
            f"Implicit: the work {_esc(origin)} planned names no ask or opportunity. "
            "Name it, or file it under a need.",
        )

    def _initiatives(self) -> None:
        for row in self.facts.initiatives:
            if row.status in features.CLOSED:
                continue
            plan = plan_of(row)
            asker = row.requested_by or {}
            message = _bare((row.ask_ref or "").removeprefix("chat_"))
            if found := self._open_need(plan.get("opportunity")):
                need = found
            elif message and asker.get("kind") == "human":
                need = self._ask_need(message, row.ask, str(asker.get("name") or ""), None)
            elif (named := self._named_need(plan.get("need"))) is not None:
                need = named
            else:
                origin = row.created_by if (row.created_by or "").startswith("C-") else row.ref
                need = self._proposal_need(self._proposals.get(origin or ""), str(origin))
            node = self._add(
                _N(
                    row.ref,
                    PRODUCT_TREE,
                    "feature",
                    "feature",
                    _clip(row.title, _TITLE_MAX),
                    need,
                    tag=row.ref,
                    why=_esc(_clip(row.outcome or row.ask, _WHY_MAX)),
                    short=_clip(row.outcome, _SHORT_MAX) or None,
                )
            )
            if row.status == "done":
                node.own, node.leaf = "done", True
            node.acceptance = _acceptance(row)
            node.steps = _steps(row, node.acceptance)
            node.surfaces = tuple(str(s) for s in plan.get("user_facing") or [])

    def _named_need(self, ref: Any) -> str | None:
        """
        The implicit need a person named a feature under (``plan.need``): the need of
        work started without a proposal keeps its place once that work is named.
        """
        parsed = node_refs.parse(ref) if isinstance(ref, str) else None
        if parsed is None or parsed.kind != "need" or not parsed.id.startswith("by-"):
            return None
        who = parsed.id.removeprefix("by-")
        return self._implicit_need(
            parsed.id,
            f"Started by {who}",
            "Implicit: work started without a proposal. Name it, or file it under a need.",
        )

    def _feature_of(self, project: FactoryProject, home: features.Home) -> str:
        """The project's feature: its home initiative, else the implicit one of its origin."""
        if home.feature and home.feature in self.nodes:
            return home.feature
        origin = features.origin_proposal(project) or self._created_by.get(project.slug)
        if origin is not None:
            ref = f"feature:from-{origin}"
            if ref not in self.nodes:
                proposal = self._proposals.get(origin)
                self._add(
                    _N(
                        ref,
                        PRODUCT_TREE,
                        "feature",
                        "proposal",
                        _clip(f"From {origin}: {proposal.title}", _TITLE_MAX)
                        if proposal is not None and proposal.title
                        else f"From {origin}",
                        self._proposal_need(proposal, origin),
                        implicit=True,
                        why=f"Implicit: the projects {_esc(origin)} created, with no initiative. "
                        "Name it, or file it under a feature.",
                    )
                )
            return ref
        who = _who(project.created_by)
        ref = f"feature:by-{who}"
        if ref not in self.nodes:
            need = self._implicit_need(
                f"by-{who}",
                f"Started by {who}",
                "Implicit: work started without a proposal. Name it, or file it under a need.",
            )
            self._add(
                _N(
                    ref,
                    PRODUCT_TREE,
                    "feature",
                    "proposal",
                    f"Started by {who}",
                    need,
                    implicit=True,
                    why="Implicit: projects started without a proposal. "
                    "Name it, or file it under a feature.",
                )
            )
        return ref

    def _project_node(self, project: FactoryProject, parent: str, *, item: bool) -> _N:
        facts = self.facts
        view = facts.views.get(project.slug) or {}
        charter = normalize_charter(project.charter)
        stage = str(view.get("stage") or project.stage or STAGE_QUEUED)
        status = view.get("status") or project.status
        held = any(w.kind in _PROJECT_WAIT_KINDS for w in self._project_waits(project, view))
        pl = view.get("pl") or {}
        team = list(view.get("team") or [])
        working = tuple(str(m.get("title")) for m in team if m.get("running") and m.get("title"))
        running = bool(pl.get("running")) or any(m.get("running") for m in team)
        own, note = _project_own(stage, status, running=running)
        budget = (charter.get("budgets") or {}).get("project_usd")
        depends_on = [str(d) for d in charter.get("depends_on") or []]
        what = charter.get("outcome") or charter.get("goal")
        part = charter.get("initiative") or {}
        why = _esc(_clip(what, _WHY_MAX))
        if isinstance(part, Mapping) and part.get("title") and not item:
            why = (
                f"{why} <b>Wave {int(part.get('wave') or 1)} of {_esc(part['title'])}.</b>".strip()
            )
        base = (
            project.base_branch
            if project.base_branch and project.base_branch != facts.base
            else None
        )
        return _N(
            _project_ref(project.slug),
            FACTORY_TREE if item else PRODUCT_TREE,
            "item" if item else "project",
            "env" if item else "project",
            _clip(project.title or project.slug, _TITLE_MAX),
            parent,
            own=own,
            note=note,
            leaf=item,
            waits=held,
            since=project.created_at if own == "notstarted" else None,
            paused=bool(view.get("paused_by")),
            short=_clip(charter.get("outcome"), _SHORT_MAX) or None,
            why=why,
            order=project.slug,
            deps=tuple(depends_on),
            depends_on=tuple(depends_on),
            depends_why=dict(charter.get("depends_why") or {}),
            cost=view.get("cost_usd"),
            budget=float(budget) if isinstance(budget, int | float) else None,
            base=base,
            owner=str(pl.get("title")) if pl.get("title") else None,
            working=working,
            idle=len(team) - len(working) if team else None,
        )

    def _project_waits(self, project: FactoryProject, view: Mapping[str, Any]) -> list[waits.Wait]:
        """
        The project's own waits (not a task's), most decisive first: the
        snapshot's ``waiting_on`` (:func:`.waits.project_waits`, already cut to
        the viewer), else computed from the facts for a project it has no view of.
        """
        listed = view.get("waiting_on") if view else None
        if isinstance(listed, list):
            found = [_wait(w) for w in listed if isinstance(w, Mapping) and not w.get("task")]
            own = [w for w in found if w is not None]
        else:
            start = view.get("start") if isinstance(view.get("start"), Mapping) else None
            computed = waits.project_waits(project, self.wfacts, start=start) if project.id else []
            own = [w for w in computed if w.task is None]
        own = [w for w in own if not self._quiet_wait(w)]
        self._own[_project_ref(project.slug)] = own
        return own

    def _quiet_wait(self, wait: waits.Wait) -> bool:
        """A wait on an alarm the server closes by itself (:meth:`_held_alarms`)."""
        return bool(wait.decision_id) and _bare(wait.decision_id) in self._quiet

    def _task_waits(self, task: FactoryTask) -> list[waits.Wait]:
        """
        The task's waits, most decisive first. A task being built does not
        wait on its project's dependencies (its builder works regardless).
        """
        found = [w for w in waits.task_waits(task, self.wfacts) if not self._quiet_wait(w)]
        if task.state in _BUILDING:
            found = [w for w in found if w.kind not in ("dependency", "capacity")]
        ranked = sorted(enumerate(found), key=lambda p: (waits.KINDS.index(p[1].kind), p[0]))
        own = [w for _, w in ranked]
        self._own[task.ref] = own
        return own

    def own_waits(self, node: _N) -> list[waits.Wait]:
        """:returns: What *node* itself waits on, most decisive first (projects and tasks)."""
        return self._own.get(node.ref, [])

    def _projects(self) -> None:
        facts = self.facts
        live = sorted(
            (p for p in facts.projects if p.status not in _GONE and p.slug not in self.hidden),
            key=lambda p: (p.created_at, p.slug),
        )
        work = [p for p in live if project_kind(p) == PROJECT_KIND_WORK]
        found = features.homes(work, facts.initiatives)
        self._work: dict[str, str] = {}
        for project in live:
            if project_kind(project) != PROJECT_KIND_WORK:
                self._add(self._project_node(project, "fx:environments", item=True))
                continue
            home = found[project.slug]
            node = self._add(
                self._project_node(project, self._feature_of(project, home), item=False)
            )
            node.also_serves = tuple(r for r in home.also_serves if r in self.nodes)
            self._work[project.slug] = node.ref

    def _later_and_ghosts(self) -> None:
        taken = {p.slug for p in self.facts.projects}
        for row in self.facts.initiatives:
            if row.ref not in self.nodes:
                continue
            for wave in plan_of(row)["waves"]:
                if not isinstance(wave, Mapping):
                    continue
                goal = _clip(wave.get("goal"), _SHORT_MAX)
                for slug in wave.get("projects") or []:
                    slug = str(slug)
                    ref = _project_ref(slug) if PROJECT_SLUG_RE.match(slug) else ""
                    if not ref or slug in taken or ref in self.nodes:
                        continue
                    self._add(
                        _N(
                            ref,
                            PRODUCT_TREE,
                            "project",
                            "project",
                            slug,
                            row.ref,
                            later=True,
                            held_for=f"wave {int(wave.get('n') or 2)}: {goal}"
                            if goal
                            else f"wave {int(wave.get('n') or 2)}",
                            why=f"Planned for a later wave of {_esc(row.title)}.",
                        )
                    )
        for proposal in self.facts.proposals:
            if proposal.kind != "projects" or proposal.status not in PROPOSAL_OPEN_STATUSES:
                continue
            block = _initiative_block(proposal.analysis)
            target = block.get("id") if isinstance(block, Mapping) else None
            feature = f"I-{target.removeprefix('I-')}" if isinstance(target, str) else ""
            if feature not in self.nodes:
                continue
            for slug, title in _planned(proposal):
                ref = _project_ref(slug)
                if slug in taken or ref in self.nodes:
                    continue
                self._add(
                    _N(
                        ref,
                        PRODUCT_TREE,
                        "project",
                        "project",
                        _clip(title or slug, _TITLE_MAX),
                        feature,
                        ghost=True,
                        tag=proposal.ref,
                        why=f"Proposed in {_esc(proposal.ref)}: {_esc(proposal.title)}",
                        short=f"proposed in {proposal.ref}",
                    )
                )

    def _tasks_and_changes(self) -> None:
        facts = self.facts
        tasks_by_id: dict[str, _N] = {}
        estimates: dict[str, list[int]] = {}
        for task in facts.tasks:
            if task.estimate_min:
                estimates.setdefault(task.project_slug, []).append(task.estimate_min)
        self._median = {s: float(statistics.median(v)) for s, v in estimates.items()}
        for task in sorted(facts.tasks, key=lambda t: t.number):
            project = self._work.get(task.project_slug)
            if project is None or task.state == "cancelled":
                continue
            sessions = [s for s in task.session_ids or [] if _bare(s)]
            member = self._members.get(_bare(sessions[-1]) or "") if sessions else None
            own = _TASK_STATE.get(task.state, "notstarted")
            # A closed project's unfinished tasks are history: they never colour it.
            history = own != "done" and self.nodes[project].own == "done"
            held = (
                not history
                and task.state in waits.OPEN_TASK_STATES
                and bool(self._task_waits(task))
            )
            node = self._add(
                _N(
                    task.ref,
                    PRODUCT_TREE,
                    "task",
                    "adhoc" if task.kind == "adhoc" else "task",
                    _clip(task.title, _TITLE_MAX),
                    project,
                    tag=task.ref,
                    own="done" if history else own,
                    note=f"{task.state} when its {self._level_name('project').lower()} closed"
                    if history
                    else None,
                    history=history,
                    leaf=True,
                    waits=held,
                    why=_esc(_clip((task.card or {}).get("text"), _WHY_MAX)),
                    order=task.key,
                    deps=tuple(str(d) for d in task.deps or ()),
                    estimate=task.estimate_min,
                    cost=task.cost_usd,
                    owner=str(member.get("title")) if member and member.get("title") else None,
                )
            )
            tasks_by_id[norm_id(task.id)] = node
        own_pr = {t.ref: _bare(t.pr_id) for t in facts.tasks if t.pr_id}
        change_of: dict[str, str] = {}
        self._pr_ref: dict[str, str] = {}
        for pr, ref in self._pr_refs():
            project = self._work.get(pr.project_slug or "")
            if project is None:
                continue
            task = tasks_by_id.get(_bare(pr.task_id) or "")
            parent = task.ref if task is not None else self._adhoc(pr, project)
            same_repo = not facts.repo or pr.repo == facts.repo
            self._add(
                _N(
                    ref,
                    PRODUCT_TREE,
                    "change",
                    "pr",
                    _clip(pr.title or f"#{pr.number}", _TITLE_MAX),
                    parent,
                    tag=f"#{pr.number}" if same_repo else f"{pr.repo}#{pr.number}",
                    own=self._pr_state(pr),
                    leaf=True,
                    merged_at=pr.merged_at,
                    merged_sha=pr.merged_sha,
                    base=pr.base_ref or None,
                    row_id=norm_id(pr.id),
                )
            )
            self._pr_ref[norm_id(pr.id)] = ref
            if task is not None and (
                own_pr.get(task.ref) == _bare(pr.id) or task.ref not in change_of
            ):
                change_of[task.ref] = ref
        self._runs(change_of)

    def _pr_refs(self) -> list[tuple[FactoryPr, str]]:
        """
        The PRs that are changes, with their refs (closed ones are left out).

        A number is ``PR-n`` once; when two repos share it, the one in the
        factory's repo (else the oldest) keeps ``PR-n`` and the others are named
        by their commit (``commit:<sha>``), so every PR keeps one home.
        """
        live = [p for p in self.facts.prs if p.state != "closed"]
        by_number: dict[int, list[FactoryPr]] = {}
        for pr in live:
            by_number.setdefault(pr.number, []).append(pr)
        out: list[tuple[FactoryPr, str]] = []
        taken: set[str] = set()
        for number in sorted(by_number):
            rows = sorted(
                by_number[number],
                key=lambda p: (p.repo != self.facts.repo, p.created_at, p.repo),
            )
            out.append((rows[0], f"PR-{number}"))
            for pr in rows[1:]:
                ref = f"commit:{(pr.merged_sha or pr.head_sha or '').lower()}"
                if node_refs.parse(ref) is None or ref in taken:
                    _logger.info(
                        "factory %s: %s#%s shares PR-%s with another repo and has no "
                        "commit of its own to be named by; it is not shown",
                        self.facts.factory_id,
                        pr.repo,
                        number,
                        number,
                    )
                    continue
                taken.add(ref)
                out.append((pr, ref))
        return out

    def _pr_state(self, pr: FactoryPr) -> str:
        if pr.state == "merged":
            return "done"
        return "waiting" if pr.ready else "moving"

    def _adhoc(self, pr: FactoryPr, project: str) -> str:
        """The implicit task of a PR no task owns."""
        ref = f"adhoc:{norm_id(pr.id)}"
        self._add(
            _N(
                ref,
                PRODUCT_TREE,
                "task",
                "adhoc",
                _clip(pr.title or f"#{pr.number}", _TITLE_MAX),
                project,
                implicit=True,
                tag=f"#{pr.number}",
                order=ref,
                why="Implicit: a pull request no task owns.",
            )
        )
        return ref

    def _runs(self, change_of: Mapping[str, str]) -> None:
        seen: set[str] = set()
        for task in self.facts.tasks:
            change = change_of.get(task.ref)
            if change is None:
                continue
            for n, session in enumerate(task.session_ids or [], start=1):
                sid = _bare(session)
                if sid is None or sid in seen:
                    continue
                seen.add(sid)
                member = self._members.get(sid) or {}
                self._add(
                    _N(
                        f"run:{sid}",
                        PRODUCT_TREE,
                        "run",
                        "run",
                        _clip(member.get("title") or f"{task.ref} run {n}", _TITLE_MAX),
                        change,
                        tag=f"run {n}",
                        own="moving" if member.get("running") else "done",
                        leaf=True,
                        owner=str(member.get("title")) if member.get("title") else None,
                        cost=member.get("cost_usd"),
                    )
                )

    def _factory_items(self) -> None:
        for proposal in self.facts.proposals:
            if proposal.kind not in _RULE_KINDS or proposal.status not in _RULE_OPEN:
                continue
            self._add(
                _N(
                    proposal.ref,
                    FACTORY_TREE,
                    "item",
                    "rule",
                    _clip(proposal.title, _TITLE_MAX),
                    "fx:rules",
                    tag=proposal.ref,
                    own="moving" if proposal.status == "trial" else "notstarted",
                    leaf=True,
                    why=_esc(_clip(proposal.rationale, _WHY_MAX)),
                )
            )
        self._team()
        self._closed_alarms()
        self._areas()

    def _closed_alarms(self) -> None:
        """Alarms the server closed today, under Health, each with its cause and real ask."""
        open_by_ref = {d.ref: d for d in self.facts.decisions if d.status == "open"}
        for i, alarm in enumerate(self.closed_alarms):
            if alarm.real_ask and not alarm.real_ask_verb and alarm.real_ask in open_by_ref:
                decision = open_by_ref[alarm.real_ask]
                proposal = node_status.decision_proposal(decision, self._proposals)
                alarm = dataclasses.replace(
                    alarm, real_ask_verb=node_status.ask_verb(decision, proposal)
                )
                self.closed_alarms[i] = alarm
            if alarm.ref in self.nodes:
                continue
            verb = f" ({alarm.real_ask_verb})" if alarm.real_ask_verb else ""
            real = f" The real ask is {alarm.real_ask}{verb}." if alarm.real_ask else ""
            self._add(
                _N(
                    alarm.ref,
                    FACTORY_TREE,
                    "item",
                    "alarm",
                    _clip(alarm.was, _TITLE_MAX),
                    _HEALTH,
                    tag=alarm.ref,
                    own="done",
                    leaf=True,
                    why=f"Closed by the server: {_esc(alarm.cause)}.{_esc(real)}",
                    done_note=_clip(f"closed by the server: {alarm.cause}.{real}", _WHY_MAX),
                )
            )

    def _team(self) -> None:
        """Working top-level agents are drawn (and any an open ask names); idle ones counted."""
        named = {node_refs.normalize(d.subject) for d in self.facts.decisions if d.subject}
        tops = [
            m for m in self.facts.members if m.get("is_top_level") and _bare(m.get("session_id"))
        ]
        drawn: list[str] = []
        for member in tops:
            sid = _bare(member.get("session_id")) or ""
            ref = f"member:{sid}"
            if not member.get("running") and ref not in named:
                continue
            self._add(
                _N(
                    ref,
                    FACTORY_TREE,
                    "item",
                    "agent",
                    _clip(member.get("title") or sid[:8], _TITLE_MAX),
                    "fx:team",
                    own="moving" if member.get("running") else "notstarted",
                    leaf=True,
                    short=_clip(member.get("summary"), _SHORT_MAX) or None,
                    cost=member.get("cost_usd"),
                )
            )
            if member.get("running"):
                drawn.append(str(member.get("title") or sid[:8]))
        team = self.nodes[f"{node_refs.FX}:team"]
        team.working = tuple(drawn)
        team.idle = sum(1 for m in tops if not m.get("running")) or None
        everyone = self.facts.members
        if everyone:
            running = sum(1 for m in everyone if m.get("running"))
            team.short = f"{len(everyone)} members · {running} running"

    def _areas(self) -> None:
        """Each area's one line on what it holds; an area with nothing to act on is done."""
        for key in node_refs.FACTORY_AREAS:
            area = self.nodes[f"{node_refs.FX}:{key}"]
            area.own, area.why = "done", _AREA_WHY.get(key, "")
        rules = sum(1 for n in self.nodes.values() if n.parent == "fx:rules")
        if rules:
            self.nodes["fx:rules"].short = f"{rules} open"
        if closed := len(self.closed_alarms):
            noun = "alarm" if closed == 1 else "alarms"
            health = self.nodes[_HEALTH]
            health.short = f"{closed} {noun} closed by the server today"
            health.done_note = f"{health.short}, each with its cause"
        base = self.facts.base_env or {}
        reported = (base.get("env") or {}).get("reported") or {}
        sha = str((reported.get("deployed") or {}).get("sha") or "")[:7]
        if base.get("branch") and (sha or reported.get("state")):
            words = [f"Base env {base['branch']}" + (f" @{sha}" if sha else "")]
            if reported.get("state"):
                words.append(str(reported["state"]))
            self.nodes[_ENVIRONMENTS].short = " · ".join(words)
        if self.facts.definition_version:
            self.nodes[_DEFINITION].short = f"version {self.facts.definition_version}"

    # ── Levels, parents, columns ───────────────────────────────

    def _counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for node in self.nodes.values():
            if node.parent is not None and not node.ghost:
                out[node.level] = out.get(node.level, 0) + 1
        return out

    def _hide_levels(self) -> None:
        """Leave out the levels the definition hides now; their nodes' children move up."""
        counts = self._counts()
        for tree, keys in TREES.items():
            hidden = [
                key
                for key in keys[1:]
                if levels.hidden(
                    str((self.level_defs.get(key) or {}).get("show") or "always"),
                    counts.get(key, 0),
                )
            ]
            self.hidden_levels[tree] = hidden
            for key in hidden:
                gone = {ref for ref, n in self.nodes.items() if n.level == key and n.tree == tree}
                for node in self.nodes.values():
                    while (up := node.parent) is not None and up in gone:
                        node.parent = self.nodes[up].parent
                for ref in gone:
                    del self.nodes[ref]
        self.ladder: dict[str, list[str]] = {
            tree: [k for k in keys if k not in self.hidden_levels[tree]]
            for tree, keys in TREES.items()
        }

    def _index(self) -> None:
        self.kids = {}
        for node in self.nodes.values():
            if node.parent is not None:
                self.kids.setdefault(node.parent, []).append(node.ref)

    def _place_decisions(self) -> None:
        self._prs_by_id = {norm_id(p.id): p for p in self.facts.prs}
        self._tasks_by_key = {(t.project_slug, t.key): t for t in self.facts.tasks}
        self._tasks_by_number = {t.number: t for t in self.facts.tasks}
        self._tasks_by_id = {norm_id(t.id): t for t in self.facts.tasks}
        for decision in self.facts.decisions:
            if decision.status != "open" or norm_id(decision.id) in self._quiet:
                continue
            ref = self._decision_target(decision)
            if ref is not None:
                self.decisions_at.setdefault(ref, []).append(decision)
                self.decision_node[decision.id] = ref

    def _held_alarms(self) -> frozenset[str]:
        """
        The open stall alarms the server closes by itself (bare decision ids):
        a no-progress stall of a task held on purpose runs again (the stall
        clock waits on a held task) and the server answers its alarm with that
        cause, the hold's own decision being the real ask. Until then it is no
        ask. A wall stall, an exhausted or a thrashing loop stays an alarm.
        """
        tasks = {norm_id(t.id): t for t in self.facts.tasks}
        riding: dict[str, list[FactoryLoop]] = {}
        for loop in self.facts.loops:
            if loop.decision_id and loop.state in waits.BAD_LOOP_STATES:
                riding.setdefault(norm_id(loop.decision_id), []).append(loop)
        return frozenset(
            decision_id
            for decision_id, rows in riding.items()
            if all(self._held_and_resumes(loop, tasks) for loop in rows)
        )

    def _held_and_resumes(self, loop: FactoryLoop, tasks: Mapping[str, FactoryTask]) -> bool:
        """*loop*'s task is held on purpose and the hold ends its stall."""
        from omnigent.server.factory.loops import resumes_when_held

        task = tasks.get(norm_id(loop.task_id)) if loop.task_id else None
        held = waits.explicit_hold(task, self.wfacts) if task is not None and task.hold else None
        return held is not None and held.on_purpose and resumes_when_held(loop, self.facts.now)

    def _decision_target(self, decision: FactoryDecision) -> str | None:
        """The node a decision is about (``api.md`` §5): its subject, else its project."""
        subject = (decision.subject or "").strip()
        head, _sep, rest = subject.partition(":")
        target: str | None = None
        if head.lower() == "pr" and (pid := _bare(rest.split("@", 1)[0])):
            pr = self._prs_by_id.get(pid)
            if pr is not None and pr.project_slug in self.hidden:
                return None
            target = self._pr_ref.get(pid)
        elif head.lower() == "task" and "/" in rest:
            slug, _s, key = rest.partition("/")
            task = self._tasks_by_key.get((slug, key))
            target = task.ref if task is not None else None
        else:
            target = node_refs.normalize(subject) if subject else None
        if decision.project_slug in self.hidden or self._hides(target):
            return None
        parsed = node_refs.parse(target) if target else None
        if parsed is not None and parsed.kind == "proposal":
            target = self._proposal_target(parsed.text)
        if target and target in self.nodes:
            return target
        if decision.project_slug and (ref := _project_ref(decision.project_slug)) in self.nodes:
            return ref
        fallback = _FACTORY_SOURCES.get(decision.source)
        return fallback if fallback in self.nodes else node_refs.PRODUCT

    def _hides(self, ref: str | None) -> bool:
        """Whether *ref* names work in a project this viewer cannot read."""
        parsed = node_refs.parse(ref) if ref and self.hidden else None
        if parsed is None:
            return False
        if parsed.kind == "project":
            return parsed.id in self.hidden
        if parsed.kind == "task":
            task = self._tasks_by_number.get(int(parsed.id))
            return task is not None and task.project_slug in self.hidden
        if parsed.kind == "pr":
            return any(
                p.project_slug in self.hidden
                for p in self._prs_by_id.values()
                if p.number == int(parsed.id)
            )
        if parsed.kind == "proposal" and (proposal := self._proposals.get(parsed.text)):
            from omnigent.server.factory.waits import proposal_slugs

            slugs = proposal_slugs(proposal)
            return bool(slugs) and slugs <= self.hidden
        return False

    def _proposal_target(self, ref: str) -> str | None:
        """A projects proposal sits on the project it changes (or the feature it adds to)."""
        proposal = self._proposals.get(ref)
        if proposal is None or proposal.kind != "projects":
            return ref
        from omnigent.server.factory.waits import proposal_slugs

        for slug in sorted(proposal_slugs(proposal)):
            if (found := _project_ref(slug)) in self.nodes:
                return found
        for slug, _title in _planned(proposal):
            if (found := _project_ref(slug)) in self.nodes:
                return found
        return node_refs.PRODUCT

    def pr_node(self, pr_id: str | None) -> str | None:
        """:returns: The change node of a PR row (``PR-n``, ``commit:<sha>``), if shown."""
        ref = self._pr_ref.get(_bare(pr_id) or "")
        return ref if ref in self.nodes else None

    def proposal_nodes(self, ref: str) -> list[str]:
        """
        The nodes a proposal (``C-n``) is about, shown as they fold, highest
        level first: a rule's own item, the features it started, the project
        it changes or adds and the projects it created.
        """
        found: dict[str, None] = {}
        if ref in self.nodes:
            found[ref] = None
        if (proposal := self._proposals.get(ref)) is not None and proposal.kind == "projects":
            target = self._proposal_target(ref)
            if target and target in self.nodes and target != node_refs.PRODUCT:
                found[target] = None
        for row in self.facts.initiatives:
            if row.created_by == ref and row.ref in self.nodes:
                found[row.ref] = None
        for slug, origin in sorted(self._created_by.items()):
            if origin == ref and (project := _project_ref(slug)) in self.nodes:
                found[project] = None
        if (implicit := f"feature:from-{ref}") in self.nodes:
            found[implicit] = None
        out: dict[str, None] = {}
        for item in found:
            out[self.fold(self.nodes[item]).ref] = None
        return sorted(out, key=lambda r: self.level_index(self.nodes[r]))

    def _order_columns(self) -> None:
        """Siblings in columns by their dependencies on each other (``graph.layers``)."""
        for refs in self.kids.values():
            sibs = [self.nodes[r] for r in refs]
            ordered = [n for n in sibs if n.order and not n.later and not n.ghost]
            if len(ordered) < 2 or ordered[0].level not in ("project", "task"):
                continue
            keys = {n.order for n in ordered}
            graph = [
                TaskNode(
                    key=str(n.order), deps=tuple(d for d in n.deps if d in keys and d != n.order)
                )
                for n in ordered
            ]
            depth = layers(graph)
            for node in ordered:
                node.col = max(0, depth.get(str(node.order), 1) - 1)

    # ── Reading the tree ───────────────────────────────────────

    def _cached(self, kind: str, ref: str, make: Callable[[], Any]) -> Any:
        key = (kind, ref)
        if key not in self._memo:
            self._memo[key] = make()
        return self._memo[key]

    def memo(self, kind: str, key: str, make: Callable[[], Any]) -> Any:
        """*make*'s value, made once while this projection is reused (a read beside it)."""
        return self._cached(kind, key, make)

    def pr_refs_by_id(self) -> dict[str, str]:
        """:returns: PR row id (bare hex) -> its change's ref, for the PRs that are changes."""
        return dict(self._pr_ref)

    def lookup(self, ref: str) -> _N | None:
        """:returns: The node *ref* names (any spelling :func:`.node_refs.normalize` reads)."""
        found = self.nodes.get(ref)
        if found is None:
            canonical = node_refs.normalize(ref)
            found = self.nodes.get(canonical) if canonical else None
        return found

    def children(self, ref: str) -> list[_N]:
        """:returns: *ref*'s children as stored on the tree (implicit ones not folded)."""
        return [self.nodes[r] for r in self.kids.get(ref, [])]

    def real_children(self, ref: str) -> list[_N]:
        return [k for k in self.children(ref) if not k.ghost]

    def folded(self, node: _N) -> bool:
        """An implicit parent with one child shows as that child."""
        return self.fold_enabled and node.implicit and len(self.real_children(node.ref)) == 1

    def fold(self, node: _N) -> _N:
        while self.folded(node):
            node = self.real_children(node.ref)[0]
        return node

    def shown_children(self, ref: str) -> list[_N]:
        return [self.fold(k) for k in self.children(ref)]

    def ancestors(self, ref: str) -> list[_N]:
        """:returns: *ref*'s ancestors, root first."""
        out: list[_N] = []
        node = self.nodes.get(ref)
        while node is not None and node.parent is not None:
            node = self.nodes.get(node.parent)
            if node is not None:
                out.append(node)
        return out[::-1]

    def subtree(self, ref: str) -> Iterator[_N]:
        stack = list(reversed(self.kids.get(ref, [])))
        while stack:
            node = self.nodes[stack.pop()]
            yield node
            stack.extend(reversed(self.kids.get(node.ref, [])))

    def level_index(self, node: _N) -> int:
        ladder = self.ladder[node.tree]
        return ladder.index(node.level) if node.level in ladder else -1

    def state(self, ref: str) -> str:
        """The node's state: its own on a leaf, else the worst among its active children."""
        return self._cached("state", ref, lambda: self._state(self.nodes[ref]))

    def _state(self, node: _N) -> str:
        if node.ghost:
            return "ghost"
        if node.later:
            return "later"
        if node.level == "project" and node.own == "done":
            return "done"  # closed: the tasks it left are history, not active children
        if node.paused:
            return "paused"
        active = [
            k
            for k in self.shown_children(node.ref)
            if not k.ghost and not k.later and k.level != "run"
        ]
        if node.leaf or node.level in ("change", "run") or not active:
            state = node.own
        else:
            live = [s for s in (self.state(k.ref) for k in active) if s != "done"]
            state = _worst(live) if live else "done"
            if state == "done" and node.level == "project" and node.own != "done":
                state = node.own
        if state != "done" and node.waits:
            state = _worst([state, "waiting"])
        if state not in ("done", "later", "ghost") and any(
            self.blocks(d) for d in self.own_decisions(node)
        ):
            state = "needs"
        return state

    def blocks(self, decision: FactoryDecision) -> bool:
        """
        Whether an open decision holds work. An ask says so itself; a proposal's
        decision (stored as blocking nothing) holds what it proposes. Every kind
        but ``projects`` (``tuning``, ``rules`` and tier-2 ``code``) is a factory
        change that waits on its answer, so it always holds; a ``projects``
        proposal holds while it adds a project (create or split) or changes a
        project that is still active.
        """
        if decision.blocking:
            return True
        if decision.source != "proposal":
            return False
        return bool(self._cached("blocks", decision.id, lambda: self._holds_work(decision)))

    def _holds_work(self, decision: FactoryDecision) -> bool:
        proposal = node_status.decision_proposal(decision, self._proposals)
        return proposal is not None and self.proposal_holds_work(proposal)

    def proposal_holds_work(self, proposal: FactoryProposal) -> bool:
        """
        Whether a proposal's decision holds work (:meth:`blocks`): every kind
        but ``projects`` does; a ``projects`` proposal while it adds a project
        or changes one that is still active. The timeline asks it of answered
        proposals too (an ask on a finished project held nothing).
        """
        if proposal.kind != "projects" or _planned(proposal):
            return True
        rows = self.wfacts.projects
        return any(
            (row := rows.get(slug)) is not None and row.status == "active"
            for slug in waits.proposal_slugs(proposal)
        )

    def own_decisions(self, node: _N) -> list[FactoryDecision]:
        """Open decisions about *node*, and about the children it was proposed to get."""
        found = list(self.decisions_at.get(node.ref, []))
        for kid in self.children(node.ref):
            if kid.ghost:
                found += self.decisions_at.get(kid.ref, [])
        return found

    def needs(self, ref: str) -> tuple[int, int]:
        """``(open, blocking)``: distinct open decisions in *ref*'s subtree."""

        def make() -> tuple[int, int]:
            found: dict[str, FactoryDecision] = {}
            for node in [self.nodes[ref], *self.subtree(ref)]:
                for d in self.decisions_at.get(node.ref, []):
                    found[d.id] = d
            return len(found), sum(1 for d in found.values() if self.blocks(d))

        return self._cached("needs", ref, make)

    def moving_anywhere(self, ref: str) -> bool:
        def make() -> bool:
            for node in [self.nodes[ref], *self.subtree(ref)]:
                if node.ghost or node.later:
                    continue
                if (node.leaf or not self.kids.get(node.ref)) and self.state(node.ref) == "moving":
                    return True
            return False

        return self._cached("moving", ref, make)

    def cost(self, node: _N) -> float | None:
        """Spend of a node: its own on a project, task or run, else its subtree's projects'."""
        if node.ref == node_refs.PRODUCT:
            return self.facts.cost
        if node.level in ("project", "task", "run", "item") or node.leaf:
            return node.cost

        def make() -> float | None:
            found = [
                n.cost
                for n in self.subtree(node.ref)
                if n.level == "project" or (n.level == "item" and n.kind == "env")
            ]
            priced = [c for c in found if isinstance(c, int | float)]
            return round(sum(priced), 2) if priced else None

        return self._cached("cost", node.ref, make)

    def _weight(self, task: _N, project: str) -> float:
        if task.estimate:
            return float(task.estimate)
        slug = (node_refs.parse(project) or node_refs.Ref("project", project)).id
        return self._median.get(slug, DEFAULT_WEIGHT_MIN)

    def _project_progress(self, node: _N) -> tuple[float, int, int, float] | None:
        """
        ``(done by weight, tasks done, tasks, total weight)``; ``None`` without a task.

        A closed project's unfinished tasks are history: they count nowhere.
        """

        def make() -> tuple[float, int, int, float] | None:
            tasks = [
                k for k in self.real_children(node.ref) if k.level == "task" and not k.history
            ]
            if not tasks:
                return None
            weights = [self._weight(t, node.ref) for t in tasks]
            done = [w for t, w in zip(tasks, weights, strict=True) if self.state(t.ref) == "done"]
            total = sum(weights)
            return (sum(done) / total if total else 0.0, len(done), len(tasks), total)

        return self._cached("pprogress", node.ref, make)

    def _build_progress(self, node: _N) -> tuple[float, bool, int, int] | None:
        """A feature's ``(build fraction, still growing, projects done, projects)``."""
        projects = [
            k for k in self.real_children(node.ref) if k.level == "project" and not k.later
        ]
        if not projects:
            return None
        found = [self._project_progress(p) for p in projects]
        weights = {p.ref: f[3] for p, f in zip(projects, found, strict=True) if f}
        planned = sorted(weights.values())
        median = planned[len(planned) // 2] if planned else DEFAULT_WEIGHT_MIN
        total = done = 0.0
        growing = False
        for project, progress in zip(projects, found, strict=True):
            weight = weights.get(project.ref, median)
            total += weight
            if self.state(project.ref) == "done":
                done += weight  # a closed project is complete, whatever it left
            elif progress is None:
                growing = True
            else:
                done += weight * progress[0]
        finished = sum(1 for p in projects if self.state(p.ref) == "done")
        return (done / total if total else 0.0, growing, finished, len(projects))

    def progress(self, node: _N) -> dict[str, Any] | None:
        """``Progress`` of a feature (acceptance first) or a project (tasks by weight)."""
        return self._cached("progress", node.ref, lambda: self._progress(node))

    def _progress(self, node: _N) -> dict[str, Any] | None:
        if node.level == "feature" and node.kind in ("feature", "proposal"):
            build = self._build_progress(node)
            tally = (node.acceptance or {}).get("tally")
            if tally and tally["total"] and not node.implicit:
                out: dict[str, Any] = {
                    "value": round(tally["passed"] / tally["total"], 4),
                    "label": f"{tally['passed']}/{tally['total']}",
                    "what": "acceptance",
                }
                if build is not None:
                    out["secondary"] = round(build[0], 4)
                    if build[1]:
                        out["growing"] = True
                return out
            if build is not None:
                out = {
                    "value": round(build[0], 4),
                    "label": f"{build[2]}/{build[3]}",
                    "what": "projects",
                }
                if build[1] and not node.implicit:
                    out["growing"] = True
                return out
            return None
        if node.level == "project":
            found = self._project_progress(node)
            if found is not None:
                return {
                    "value": round(found[0], 4),
                    "label": f"{found[1]}/{found[2]}",
                    "what": "tasks",
                }
            if not node.later and not node.ghost and self.state(node.ref) != "done":
                return {"value": 0, "label": "–", "what": "not planned", "growing": True}
        return None

    def in_base_env(self, node: _N) -> bool:
        """
        Whether *node*'s merged changes are all in the base env: the commit it
        runs contains each merge (:func:`.base.contains`, by ancestry).
        """

        def make() -> bool:
            if node.level == "change":
                return self._change_in_base(node)
            merged = [n for n in self.subtree(node.ref) if n.level == "change" and n.merged_at]
            return bool(merged) and all(self._change_in_base(n) for n in merged)

        return self._cached("inbase", node.ref, make)

    def _env(self) -> Mapping[str, Any]:
        return ((self.facts.base_env or {}).get("env") or {}).get("reported") or {}

    def _deployed(self) -> DeployedHead | None:
        """
        The commit the base env runs while it is up: the server's recorded fact
        (placed in the base's history), else the env's own report.
        """

        def make() -> DeployedHead | None:
            env = self._env()
            if env.get("state") != "ready":
                return None
            reported = str((env.get("deployed") or {}).get("sha") or "").lower()
            stored = (self.facts.base_env or {}).get("deployed") or {}
            sha = str(stored.get("sha") or "").lower()
            if sha and (not reported or same_commit(sha, reported)):
                head_at = stored.get("head_at")
                return DeployedHead(
                    sha, int(stored.get("at") or 0), int(head_at) if head_at else None
                )
            if reported:
                return DeployedHead(reported, int(env.get("at") or 0))
            return None

        return self._cached("deployed", "", make)

    def _change_in_base(self, node: _N) -> bool:
        """A merge into the base env's own repo and branch, which the deployed commit holds."""
        deployed = self._deployed()
        base = self.facts.base_env or {}
        branch, repo = base.get("branch"), str(base.get("repo") or "").lower()
        if deployed is None or not node.merged_at:
            return False
        if branch and node.base and node.base != branch:
            return False
        pr = self.pr_rows.get(node.row_id or "")
        if repo and pr is not None and pr.repo.lower() != repo:
            return False
        return contains(deployed, merged_sha=node.merged_sha, merged_at=node.merged_at)

    # ── The API's shapes ───────────────────────────────────────

    def _level_key(self, node: _N) -> str:
        return node.level

    def _level_label(self, node: _N) -> str:
        return self._level_name(node.level)

    def status(self, node: _N, *, compact: bool = False) -> dict[str, Any]:
        """
        The node's ``StatusLine``: what moves, or its blocker path ending at
        whoever must act (:mod:`.node_status`); the state alone when nothing
        holds it. *compact* is a tile's or a row's line: the end of the path.
        """
        key = "line:compact" if compact else "line"
        return self._cached(key, node.ref, lambda: self._status(node, compact=compact))

    def _status(self, node: _N, *, compact: bool) -> dict[str, Any]:
        out = self.paths.line(node, compact=compact) or self._state_line(node)
        if not compact and out["kind"] != "done" and (note := self.paths.note(node)):
            out["note"] = note
        return out

    def _state_line(self, node: _N) -> dict[str, Any]:
        """The ``StatusLine`` the state alone says (done, later, proposed, not started …)."""
        state = self.state(node.ref)
        kind, label = _STATUS.get(state, ("idle", state.capitalize()))
        out: dict[str, Any] = {"kind": kind, "label": label, "segments": [], "ends": "other"}
        if state == "moving":
            out["ends"] = "moving"
        if state == "later" and node.held_for:
            out["detail"] = f"held for {node.held_for}"
        elif state == "ghost":
            ask = next((d for d in self.decisions_at.get(node.ref, []) if self.blocks(d)), None)
            if ask is not None:
                out["decision"] = self.paths.decision_ask(ask).to_json()
            if node.short:
                out["detail"] = node.short
        elif state == "done" and node.history:
            out["label"] = "Closed"
            if node.note:
                out["detail"] = node.note
        elif state == "done":
            if detail := self._done_detail(node):
                out["detail"] = detail
        elif state == "notstarted" and node.since and node.level == "project":
            out["detail"] = f"queued {node_status.duration(self.facts.now - node.since)}"
        elif node.note and state == node.own:
            out["detail"] = node.note
        return out

    def _done_detail(self, node: _N) -> str | None:
        """What a finished node left: its note, its merged changes and where they run."""
        if node.done_note:
            return node.done_note
        kids = self.real_children(node.ref)
        left = sum(1 for k in kids if k.history)
        if left:
            noun = self._level_name(kids[0].level, plural=left != 1).lower()
            return f"closed · {left} unfinished {noun} left as history"
        merged = [
            n for n in [node, *self.subtree(node.ref)] if n.level == "change" and n.merged_at
        ]
        if merged and node.tree == PRODUCT_TREE:
            tags = [n.tag or n.ref for n in merged[:3]]
            more = f" +{len(merged) - 3}" if len(merged) > 3 else ""
            where = ""
            if self._deployed() is not None:
                where = (
                    " · in base env"
                    if all(self._change_in_base(n) for n in merged)
                    else " · not yet in base env"
                )
            return f"{', '.join(tags)}{more} merged{where}"
        if len(kids) > 1:
            return f"all {len(kids)} done"
        return None

    def _grandchildren(self, node: _N) -> dict[str, Any] | None:
        if node.level in ROW_LEVELS:
            return None
        kids = [k for k in self.shown_children(node.ref) if k.level != "run" and not k.ghost]
        if not kids:
            return None
        active = [k for k in kids if not k.later]
        by_state: dict[str, int] = {}
        for kid in active:
            state = self.state(kid.ref)
            by_state[state] = by_state.get(state, 0) + 1
        noun = self._level_name(kids[0].level, plural=len(active) != 1).lower()
        return {
            "count": len(active),
            "by_state": by_state,
            "later": len(kids) - len(active),
            "noun": noun,
        }

    def folded_from(self, node: _N) -> list[str]:
        """Implicit parents folded into *node* (it is shown in their place), nearest first."""
        out: list[str] = []
        parent = self.nodes.get(node.parent or "")
        while parent is not None and self.folded(parent):
            out.append(parent.ref)
            parent = self.nodes.get(parent.parent or "")
        return out

    def summary(self, node: _N) -> dict[str, Any]:
        """A ``NodeSummary``: how *node* reads as a tile, a row or a dot."""
        state = self.state(node.ref)
        out: dict[str, Any] = {
            "ref": node.ref,
            "level": self._level_key(node),
            "level_name": self._level_label(node),
            "kind": node.kind,
            "kind_name": self.kind_names.get(node.kind, node.kind.capitalize()),
            "title": node.title,
            "state": state,
            "status_compact": self.status(node, compact=True),
            "needs": dict(zip(("open", "blocking"), self.needs(node.ref), strict=True)),
        }
        if glyph := _GLYPHS.get(node.kind):
            out["glyph"] = glyph
        if node.tag:
            out["tag"] = node.tag
        if node.short:
            out["short"] = node.short
        if node.base:
            out["base"] = node.base
        if node.implicit:
            out["implicit"] = True
        if folded := self.folded_from(node):
            out["folded_from"] = folded
        if node.later:
            out["col"] = "later"
        elif node.col is not None:
            out["col"] = node.col
        if node.held_for:
            out["held_for"] = node.held_for
        if progress := self.progress(node):
            out["progress"] = progress
        cost = self.cost(node)
        if cost:
            out["cost"] = round(float(cost), 2)
        if (burn := self.burn(node)) and node.ref != node_refs.PRODUCT:
            out["burn"] = burn
        if budget := self.budget(node):
            out["budget"] = budget
        if node.tree == PRODUCT_TREE and node.level != "product" and self.in_base_env(node):
            out["in_base_env"] = True
        if self.try_it(node):
            out["try_ready"] = True
        if node.level in subtrees.SCOPE_LEVELS and self.auto_merged(node):
            out["auto_merge"] = True
        if (grand := self._grandchildren(node)) is not None:
            out["grandchildren"] = grand
        if owner := self._owner(node):
            out["owner"] = {
                "name": owner,
                "working": node.level == "project" and state == "moving",
            }
        if node.working or node.idle:
            out["working"] = [{"name": n, "working": True} for n in node.working]
            out["idle"] = node.idle or 0
        if node.level == "task" and state != "done":
            change = next((k for k in self.children(node.ref) if k.level == "change"), None)
            if change is not None and change.tag:
                out["change"] = {"ref": change.ref, "label": change.tag}
        return out

    def _responder_seat(self, node: _N) -> str | None:
        key = "study" if node.kind == "study" else node.level
        seat = self.responders.get(key)
        return str(seat) if seat else None

    def _owner(self, node: _N) -> str | None:
        if node.owner:
            return node.owner
        seat = self._responder_seat(node)
        return seat.capitalize() if seat and seat != "lead" else None

    def responder(self, node: _N) -> str | None:
        """Who answers *node*'s thread, by name: a seat, or the lead of its project."""
        seat = self._responder_seat(node)
        if seat is None:
            return None
        if seat != "lead":
            return seat.capitalize()
        for found in [node, *reversed(self.ancestors(node.ref))]:
            if found.level == "project":
                return found.owner or "the lead"
        return "the lead"

    def crumb(self, node: _N) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ref": node.ref,
            "level": self._level_key(node),
            "level_name": self._level_label(node),
            "title": node.title,
            "state": self.state(node.ref),
        }
        if node.tag and node.level in ROW_LEVELS and not node.implicit:
            out["tag"] = node.tag
        if node.implicit:
            out["implicit"] = True
        if self.folded(node):
            out["folded"] = True
        return out

    def at_level(self, ref: str, level: str) -> list[_N]:
        """The nodes on *level* under *ref*: folded nodes count where they show."""

        def make() -> list[_N]:
            out: dict[str, _N] = {}
            stack = list(reversed(self.kids.get(ref, [])))
            while stack:
                node = self.nodes[stack.pop()]
                if node.level == level:
                    shown = self.fold(node)
                    out.setdefault(shown.ref, shown)
                else:
                    stack.extend(reversed(self.kids.get(node.ref, [])))
            return list(out.values())

        return self._cached(f"at:{level}", ref, make)

    def levels_below(self, node: _N) -> list[dict[str, Any]]:
        """The strip: each level below *node* with a node on it, with counts and states."""
        out: list[dict[str, Any]] = []
        ladder = self.ladder[node.tree]
        start = ladder.index(node.level) + 1 if node.level in ladder else len(ladder)
        for key in ladder[start:]:
            items = [n for n in self.at_level(node.ref, key) if not n.ghost]
            if not items:
                continue
            active = [n for n in items if not n.later]
            by_state: dict[str, int] = {}
            for item in active:
                state = self.state(item.ref)
                by_state[state] = by_state.get(state, 0) + 1
            row: dict[str, Any] = {
                "level": key,
                "name": self._level_name(key, plural=True),
                "count": len(active),
                "later": len(items) - len(active),
                "needs": by_state.get("needs", 0),
                "by_state": by_state,
            }
            if (self.level_defs.get(key) or {}).get("debug"):
                row["debug"] = True
            out.append(row)
        return out

    def results(self, node: _N) -> dict[str, Any]:
        """``Results``: merged changes, how many are in the base env, the open ones."""

        def make() -> dict[str, Any]:
            changes = [n for n in [node, *self.subtree(node.ref)] if n.level == "change"]
            merged = [c for c in changes if c.own == "done"]
            out: dict[str, Any] = {
                "merged": len(merged),
                "open": [c.ref for c in changes if c.own != "done"][:PAGE_SIZE],
                "readouts": "not measured",
            }
            # Without a base env there is nothing to be in: no count, not a zero.
            if self._deployed() is not None:
                out["in_base_env"] = sum(1 for c in merged if self._change_in_base(c))
            return out

        return dict(self._cached("results", node.ref, make))

    def rollup(self, node: _N) -> dict[str, Any]:
        out: dict[str, Any] = {
            "cost": round(float(self.cost(node) or 0.0), 2),
            "moving_anywhere": self.moving_anywhere(node.ref),
        }
        if burn := self.burn(node):
            out["burn"] = burn
        if node.ref == node_refs.PRODUCT:
            if counts := self._root_counts(node):
                out["counts"] = counts
            if self.closed_alarms:
                out["alarms_closed"] = {"count": len(self.closed_alarms), "ref": _HEALTH}
        elif node.ref == node_refs.FX:
            members = [m for m in self.facts.members if m.get("is_top_level")]
            working = [m for m in members if m.get("running")]
            out["team"] = {
                "working": [{"name": str(m.get("title") or ""), "working": True} for m in working],
                "idle": len(members) - len(working),
            }
        else:
            if progress := self.progress(node):
                out["progress"] = progress
            if budget := self.budget(node):
                out["budget"] = budget
            if eta := self.eta(node):
                out["eta"] = eta
            if node.working or node.idle:
                out["team"] = {
                    "working": [{"name": n, "working": True} for n in node.working],
                    "idle": node.idle or 0,
                }
        deployed = self._deployed()
        if node.tree == PRODUCT_TREE and deployed is not None and self.in_base_env(node):
            out["in_base_env"] = {"sha": deployed.sha[:7], "try_it": self.can_try(node)}
        return out

    def can_try(self, node: _N) -> bool:
        """Whether something at or under *node* passes in the base env and can be tried."""
        if node.level in ("feature", "project"):
            return bool(self.try_it(node))
        if node.level in ROW_LEVELS:
            return False
        return any(self.try_it(n) for n in self.at_level(node.ref, "feature"))

    # ── Money, time and what can be tried ──────────────────────

    def _burn_by_project(self) -> dict[str, float]:
        """
        Project slug -> its spend in the last hour, from turn spans: the cost at
        a turn's open and close, prorated to the hour; a turn still open counts
        up to its session's cost now (the snapshot's member).
        """

        def make() -> dict[str, float]:
            now_ms = int(self.facts.now * 1000)
            start = now_ms - BURN_WINDOW_S * 1000
            spent_now = {
                sid: float(cost)
                for m in self.facts.members
                if (sid := _bare(m.get("session_id")))
                and isinstance(cost := m.get("cost_usd"), int | float)
            }
            out: dict[str, float] = {}
            for span in self.facts.turns:
                attrs = span.attrs or {}
                begin, end = attrs.get("cost_start"), attrs.get("cost_end")
                slug = span.project_slug
                if not slug or slug in self.hidden or not isinstance(begin, int | float):
                    continue
                closed = span.ended_at
                if closed is None:
                    end = spent_now.get(_bare(span.session_id) or "")
                    closed = now_ms
                if not isinstance(end, int | float):
                    continue
                inside = min(closed, now_ms) - max(span.started_at, start)
                if inside <= 0 or end <= begin:
                    continue
                length = max(1, closed - span.started_at)
                out[slug] = out.get(slug, 0.0) + (end - begin) * min(1.0, inside / length)
            return out

        return self._cached("burns", "", make)

    def burn(self, node: _N) -> float | None:
        """
        Spend per hour now: the last hour's turns, summed over the subtree's
        projects. The root shows the factory's own rate (the snapshot's, as its
        cost is): it also counts the leads and agents that work for no project,
        so it is more than its features' sum. Hidden from a viewer, it is the
        sum of what they may read.
        """
        if node.ref == node_refs.PRODUCT:
            if self.facts.burn is not None:
                return round(float(self.facts.burn), 2) if self.facts.burn else None
            seen = sum(self._burn_by_project().values())
            return round(seen, 2) if seen >= 0.01 else None
        burns = self._burn_by_project()
        if not burns:
            return None
        if node.level == "project" or (node.level == "item" and node.kind == "env"):
            found = burns.get(node.order or "", 0.0)
        elif node.level in ROW_LEVELS or node.tree != PRODUCT_TREE:
            return None
        else:
            found = sum(
                burns.get(n.order or "", 0.0)
                for n in self.subtree(node.ref)
                if n.level == "project"
            )
        return round(found, 2) if found >= 0.01 else None

    def budget(self, node: _N) -> float | None:
        """
        A node's cap: its own, else the sum of its children's caps when every
        child it counts the cost of has one (a part-capped sum would compare
        all the cost against some of the caps).
        """
        if node.budget:
            return float(node.budget)
        if node.level not in ("feature", "need", "goal") or node.implicit:
            return None
        kids = [k for k in self.real_children(node.ref) if not k.later and not k.ghost]
        found = [self.budget(k) for k in kids]
        if not found or any(b is None for b in found):
            return None
        return round(sum(b for b in found if b is not None), 2)

    def project_weight(self, node: _N) -> float:
        """A project's weight: its tasks' estimates, else its siblings' median, else 60 min."""
        found = self._project_progress(node)
        if found is not None:
            return found[3]
        parent = self.nodes.get(node.parent or "")
        sibs = [
            p[3]
            for k in (self.real_children(parent.ref) if parent is not None else [])
            if k.level == "project" and k.ref != node.ref and (p := self._project_progress(k))
        ]
        sibs.sort()
        return sibs[len(sibs) // 2] if sibs else DEFAULT_WEIGHT_MIN

    def task_weight(self, node: _N) -> float:
        return self._weight(node, node.parent or "")

    def _project_eta_min(self, node: _N) -> float | None:
        """Minutes left on a project's critical path, only from observed task durations."""
        observed = self.facts.observed
        if not observed:
            return None
        rows = [
            t
            for t in self.facts.tasks
            if t.project_slug == node.order and t.state not in ("cancelled", "failed")
        ]
        live = [t for t in rows if t.state != "done"]
        if not live or any(t.kind not in observed for t in live):
            return None
        graph = [
            TaskNode(
                key=t.key,
                number=t.number,
                kind=t.kind,
                state=t.state,
                deps=tuple(str(d) for d in t.deps or ()),
                estimate_min=t.estimate_min,
            )
            for t in rows
        ]
        _path, total = critical_path(graph, lambda n: float(observed[n.kind]))
        return total or None

    def eta_min(self, node: _N) -> float | None:
        """
        Minutes left, shown only from observations (each kind with at least
        :data:`~.graph.MIN_OBSERVED_SAMPLES` samples): a project's remaining
        critical path; a feature's slowest project per column, summed.
        """

        def make() -> float | None:
            if node.level == "project" and not node.later and not node.ghost:
                if self.state(node.ref) == "done":
                    return None
                return self._project_eta_min(node)
            if node.level != "feature":
                return None
            live = [
                k
                for k in self.real_children(node.ref)
                if k.level == "project" and not k.later and self.state(k.ref) != "done"
            ]
            if not live:
                return None
            slowest: dict[int, float] = {}
            for kid in live:
                left = self.eta_min(kid)
                if left is None:
                    return None
                col = kid.col or 0
                slowest[col] = max(slowest.get(col, 0.0), left)
            return sum(slowest.values()) or None

        return self._cached("eta", node.ref, make)

    def eta(self, node: _N) -> str | None:
        left = self.eta_min(node)
        if left is None:
            return None
        if left < 90:
            return f"~{max(1, round(left))} min"
        if left < 48 * 60:
            return f"~{round(left / 60)} h"
        return f"~{round(left / 1440)} d"

    def try_it(self, node: _N) -> list[dict[str, Any]]:
        """
        What can be tried in the base env: the acceptance steps that pass there,
        on their feature and on the project that owns each. A step whose
        user-facing projects are not built yet is tried through the API.
        """

        def make() -> list[dict[str, Any]]:
            if self._deployed() is None:
                return []
            if node.level == "feature":
                feature, owner = node, None
            elif node.level == "project":
                feature = self.nodes.get(node.parent or "")
                owner = node.order
            else:
                return []
            if feature is None or not feature.steps:
                return []
            unbuilt = [
                self.nodes[r].title
                for r in (_project_ref(slug) for slug in feature.surfaces)
                if r in self.nodes and self.state(r) != "done"
            ]
            out: list[dict[str, Any]] = []
            for n, text, step_owner, verdict in feature.steps:
                if verdict != "pass" or (owner is not None and step_owner != owner):
                    continue
                item: dict[str, Any] = {"text": text, "acceptance_step": n}
                if unbuilt and step_owner not in feature.surfaces:
                    item["how"] = f"API only: {', '.join(unbuilt)} not built yet"
                    item["api"] = True
                out.append(item)
            return out

        return self._cached("try", node.ref, make)

    def _root_counts(self, node: _N) -> list[dict[str, Any]]:
        """What the tree holds, level by level: features, studies, projects (none of zero)."""
        out: list[dict[str, Any]] = []
        shown = [n for n in self.at_level(node.ref, "feature") if not n.ghost]
        studies = sum(1 for n in shown if n.kind == "study")
        if len(shown) - studies:
            out.append(
                {
                    "key": "feature",
                    "name": self._level_name("feature"),
                    "plural": self._level_name("feature", plural=True),
                    "count": len(shown) - studies,
                }
            )
        if studies:
            name = self.kind_names.get("study", "Study")
            out.append({"key": "study", "name": name, "plural": f"{name}s", "count": studies})
        projects = [n for n in self.at_level(node.ref, "project") if not n.ghost]
        later = sum(1 for n in projects if n.later)
        if projects:
            row: dict[str, Any] = {
                "key": "project",
                "name": self._level_name("project"),
                "plural": self._level_name("project", plural=True),
                "count": len(projects) - later,
            }
            if later:
                row["later"] = later
            out.append(row)
        return out

    def _words(self, ref: str) -> str:
        """A node's state as a chip says it (``needs you``, ``waiting`` …)."""
        state = self.state(ref)
        return _STATUS.get(state, ("", state))[1].lower()

    def _feature_ref(self, node: _N) -> str | None:
        found = next((a for a in self.ancestors(node.ref) if a.level == "feature"), None)
        return found.ref if found is not None else None

    def met_links(self, node: _N) -> list[tuple[str, str]]:
        """``(dependency, its ref)`` of the project links that no longer hold *node* (history)."""
        done = self.state(node.ref) == "done"
        out: list[tuple[str, str]] = []
        for dep in node.depends_on:
            if change_dependency(dep) is not None:
                continue  # a change link is its own chip ("PR-9876 · met")
            target = self.nodes.get(_project_ref(dep))
            if target is not None and (done or self.state(target.ref) == "done"):
                out.append((dep, target.ref))
        return out

    def chips(self, node: _N) -> list[dict[str, Any]]:
        """Links to other nodes as chips: kind · target · its state. Never a line."""
        out: list[dict[str, Any]] = []
        met = self.met_links(node)
        done = self.state(node.ref) == "done"
        for dep in node.depends_on:
            if (change := self._change_chip(node, dep)) is not None:
                out.append(change)
                continue
            target = self.nodes.get(_project_ref(dep))
            if target is None or (dep, target.ref) in met:
                continue
            chip: dict[str, Any] = {
                "kind": "depends on",
                "ref": target.ref,
                "title": target.title,
                "state": self._words(target.ref),
            }
            if reason := node.depends_why.get(dep):
                chip["reason"] = _clip(reason, _SHORT_MAX)
            out.append(chip)
        if node.level == "project" and not done:
            out += [
                {
                    "kind": "needed by",
                    "ref": other.ref,
                    "title": other.title,
                    "state": self._words(other.ref),
                }
                for other in self._dependents(node)
            ]
        out += [
            {
                "kind": "also serves",
                "ref": ref,
                "title": self.nodes[ref].title,
                "state": self._words(ref),
            }
            for ref in node.also_serves
        ]
        if node.level == "feature":
            out += [
                {
                    "kind": "also serves",
                    "ref": project.ref,
                    "title": project.title,
                    "state": f"home: {self.nodes[project.parent or ''].title}",
                }
                for project in self._served_by(node)
            ]
        if met:
            out.append(self._met_badge(node, met))
        if closed := self._alarms_about(node):
            out.append(
                {
                    "kind": "alarm closed" if len(closed) == 1 else "alarms closed",
                    "ref": closed[0].ref,
                    "title": ", ".join(a.ref for a in closed[:3]),
                    "state": _clip(closed[0].cause, 60),
                }
            )
        out += self._scope_chips(node)
        return out

    # ── Autonomy and auto-merge per subtree ────────────────────

    def project_of(self, node: _N) -> FactoryProject | None:
        """The stored project *node* is or sits in (a task, change or run), else ``None``."""
        if node.tree != PRODUCT_TREE:
            return None
        if node.level == "project":
            return self.project_rows.get(node.order or "")
        if node.level not in ("task", "change", "run"):
            return None
        found = next((a for a in reversed(self.ancestors(node.ref)) if a.level == "project"), None)
        return self.project_rows.get(found.order or "") if found is not None else None

    def scope_chain(self, node: _N, *, own: bool = True) -> tuple[str, ...]:
        """
        *node*'s ref (with *own*), then its ancestors' (nearest first): a project
        and what is under it as the merger places the project (:mod:`.subtrees`).
        """
        project = self.project_of(node)
        if project is not None:
            chain = self.facts.lineage.chain(project)
            if node.level != "project":
                chain = (node.ref, *chain)
        else:
            chain = (node.ref, *(a.ref for a in reversed(self.ancestors(node.ref))))
        return chain if own else chain[1:]

    def merge_scope(
        self, node: _N, *, own: bool = True
    ) -> tuple[str, auto_merge.MergeScope] | None:
        """The auto-merge scope covering *node* (its own first, with *own*), else ``None``."""
        if not self.facts.merge_scopes or node.tree != PRODUCT_TREE or node.ghost:
            return None
        return subtrees.nearest(self.scope_chain(node, own=own), self.facts.merge_scopes)

    def autonomy_scope(self, node: _N, *, own: bool = True) -> tuple[str, str] | None:
        """``(ref, level)`` of the autonomy scope covering *node*, else ``None``."""
        if not self.facts.autonomy_scopes or node.tree != PRODUCT_TREE or node.ghost:
            return None
        return subtrees.nearest(self.scope_chain(node, own=own), self.facts.autonomy_scopes)

    def auto_merged(self, node: _N) -> bool:
        """Whether a scope makes the server merge *node*'s PRs (a project's: into its base)."""
        hit = self.merge_scope(node)
        if hit is None or hit[1].pair is None:
            return False
        project = self.project_of(node)
        if project is None:
            return node.level in subtrees.SCOPE_LEVELS
        return hit[1].pair == ((project.repo or "").lower(), project_base_branch(project))

    def _overrides(self, ref: str) -> bool:
        """Whether an off scope at *ref* stops a merge: an ancestor's scope or a factory target."""
        at = self.nodes.get(ref)
        above = self.merge_scope(at, own=False) if at is not None else None
        if above is not None:
            return above[1].pair is not None
        return bool(auto_merge.as_targets(self.facts.targets).pairs)

    def _scope_title(self, ref: str) -> str:
        found = self.nodes.get(ref)
        return found.title if found is not None else ref

    def _scope_chips(self, node: _N) -> list[dict[str, Any]]:
        """``auto-merge → <base> · this subtree / from <node>``; autonomy when it differs."""
        out: list[dict[str, Any]] = []
        hit = self.merge_scope(node)
        if hit is not None and node.level not in ("change", "run"):
            ref, scope = hit
            where = "this subtree" if ref == node.ref else f"from {self._scope_title(ref)}"
            if scope.pair is not None and self.auto_merged(node):
                out.append(
                    {"kind": "auto-merge →", "ref": ref, "title": scope.pair[1], "state": where}
                )
            elif scope.pair is None and self._overrides(ref):
                out.append({"kind": "auto-merge", "ref": ref, "title": "off", "state": where})
        found = self.autonomy_scope(node)
        if (
            found is not None
            and found[1] != self.facts.level
            and node.level in subtrees.SCOPE_LEVELS
        ):
            ref, level = found
            where = "this subtree" if ref == node.ref else f"from {self._scope_title(ref)}"
            out.append(
                {"kind": "autonomy", "ref": ref, "title": level.capitalize(), "state": where}
            )
        return out

    def _change_chip(self, node: _N, dep: str) -> dict[str, Any] | None:
        """
        A ``depends_on`` item that names a change (``PR-9876``): ``met`` once it
        merged, else the change's state; ``None`` for a project slug.
        """
        number = change_dependency(dep)
        if number is None:
            return None
        from omnigent.server.factory.gating import change_met

        project = self.project_rows.get(node.order or "") if node.level == "project" else None
        # The gate's own rule: the dependent's repo first, then any of the factory's repos.
        merged = change_met(number, self.facts.prs, project.repo if project else None)
        target = self.nodes.get(dep)
        chip: dict[str, Any] = {
            "kind": "depends on",
            "title": dep,
            "state": "met" if merged else (self._words(dep) if target is not None else "open"),
        }
        if target is not None:
            chip["ref"] = target.ref
        if reason := node.depends_why.get(dep):
            chip["reason"] = _clip(reason, _SHORT_MAX)
        return chip

    def _alarms_about(self, node: _N) -> list[alarms.ClosedAlarm]:
        """The closed alarms that were about *node* (shown once, as a chip)."""

        def make() -> dict[str, list[alarms.ClosedAlarm]]:
            out: dict[str, list[alarms.ClosedAlarm]] = {}
            for alarm in self.closed_alarms:
                if (ref := self._alarm_node(alarm)) is not None:
                    out.setdefault(ref, []).append(alarm)
            return out

        return list(self._cached("alarms", "", make).get(node.ref, []))

    def _alarm_node(self, alarm: alarms.ClosedAlarm) -> str | None:
        node = self.lookup(alarm.about) if alarm.about else None
        if node is None or node.tree != PRODUCT_TREE:
            return None
        return self.fold(node).ref

    def _met_badge(self, node: _N, met: Sequence[tuple[str, str]]) -> dict[str, Any]:
        """Met dependencies are history: one count badge that opens them."""
        mine = self._feature_ref(node)
        chips: list[dict[str, Any]] = []
        silent = 0
        for dep, ref in met:
            words = "done"
            if dep not in node.depends_why:
                silent += 1
                across = self._feature_ref(self.nodes[ref]) != mine
                words += " · no reason given" + (", across features" if across else "")
            chips.append(
                {"kind": "depended on", "ref": ref, "title": self.nodes[ref].title, "state": words}
            )
        badge: dict[str, Any] = {
            "title": f"{len(met)} met link{'s' if len(met) > 1 else ''}",
            "state": f"{silent} had no reason" if silent else "hidden",
            "list": [
                {
                    "chips": chips,
                    "note": "Met links are history; they no longer hold this node.",
                }
            ],
        }
        if silent:
            badge["tone"] = "warn"
        return badge

    def _dependents(self, node: _N) -> list[_N]:
        """Projects that wait on *node* and are not finished, held or proposed."""
        slug = node.order or ""
        return [
            n
            for n in self.nodes.values()
            if n.level == "project"
            and slug in n.depends_on
            and not n.later
            and not n.ghost
            and self.state(n.ref) != "done"
        ]

    def dependents_of(self, ref: str) -> list[_N]:
        """
        What waits on the node *ref* names and is not finished: projects that
        depend on a project, tasks of the same project that depend on a task.
        """

        def make() -> list[_N]:
            node = self.nodes.get(ref)
            if node is None:
                return []
            if node.level == "project":
                return self._dependents(node)
            if node.level != "task" or not node.order:
                return []
            return [
                k
                for k in self.real_children(node.parent or "")
                if k.level == "task"
                and node.order in k.deps
                and not k.history
                and self.state(k.ref) != "done"
            ]

        return list(self._cached("dependents", ref, make))

    def asker_of(self, decision: FactoryDecision, proposal: FactoryProposal | None = None) -> str:
        """Who asked a decision, by name: the asking member, the proposal's author, the server."""
        member = self._members.get(_bare(decision.asked_by_session_id) or "")
        if member is not None and member.get("title"):
            return str(member["title"])
        if proposal is not None and proposal.author:
            author = str(proposal.author).split("@", 1)[0]
            named = next(
                (
                    str(m["title"])
                    for m in self.facts.members
                    if str(m.get("title") or "").lower() == author.lower()
                ),
                None,
            )
            return named or author[:1].upper() + author[1:]
        return "an agent" if decision.source == "agent" else "the server"

    def lead_name(self, slug: str | None) -> str | None:
        """The name of project *slug*'s lead, when the viewer may see it."""
        node = self.nodes.get(_project_ref(slug)) if slug else None
        return node.owner if node is not None else None

    def project_ref(self, slug: str | None) -> str | None:
        """:returns: The ref of project *slug* (``None`` for text that is no slug)."""
        if not slug or not PROJECT_SLUG_RE.match(slug):
            return None
        return _project_ref(slug)

    def home_index(self, tree: str) -> int:
        """The index of the level a root lists by default (the strip's home)."""
        ladder = self.ladder[tree]
        home = self.home.get(tree) or (ladder[1:2] or [""])[0]
        return ladder.index(home) if home in ladder else -1

    def level_name(self, key: str, plural: bool = False) -> str:
        """A level's name from the definition (``Feature``, ``Projects``)."""
        return self._level_name(key, plural=plural)

    def _served_by(self, node: _N) -> list[_N]:
        """Projects whose home is elsewhere but which this feature's plan also lists."""
        return self._cached(
            "served",
            node.ref,
            lambda: [n for n in self.nodes.values() if node.ref in n.also_serves],
        )

    def verbs(self, node: _N, *, can_edit: bool) -> list[dict[str, Any]]:
        """The verbs *node*'s level offers that apply to it, enabled for editors."""
        key = "study" if node.kind == "study" else node.level
        state = self.state(node.ref)
        out: list[dict[str, Any]] = []
        for verb in self.level_verbs.get(key, []):
            if not self._verb_applies(verb, node, state):
                continue
            shown = "resume" if verb == "pause" and node.paused else verb
            label, keys = _VERB_LABELS.get(shown, (shown.capitalize(), ""))
            item: dict[str, Any] = {"verb": shown, "label": label, "enabled": can_edit}
            if keys:
                item["key"] = keys
            if not can_edit:
                item["reason"] = _READ_ONLY
            out.append(item)
        return out

    def _verb_applies(self, verb: str, node: _N, state: str) -> bool:
        """Whether *verb* applies to *node*: exactly what the verb routes serve here."""
        stored = not node.implicit and not node.ghost and not node.later
        checks: dict[str, Callable[[], bool]] = {
            "talk": lambda: self.responder(node) is not None,
            "add": lambda: stored and state != "done" and self._seats(self.adds(node), "create"),
            "start": lambda: (
                node.level == "project"
                and stored
                and bool(node.depends_on)
                and state not in ("moving", "done")
            ),
            "research": lambda: node.kind == "study",
            "team": lambda: node.level == "project" and not node.later and not node.ghost,
            "auto": lambda: (
                stored
                and state != "done"
                and node.tree == PRODUCT_TREE
                and node.level in subtrees.SCOPE_LEVELS
                and subtrees.is_scope_ref(node.ref)
            ),
            "pause": lambda: state not in ("done", "later", "offered") and not node.ghost,
            "move": lambda: stored and self._seats(self.moves(node), "move"),
            "name": lambda: node.implicit and self._nameable(node),
            # The level names live in the definition: renamed from its area only.
            "rename": lambda: node.ref == _DEFINITION,
        }
        check = checks.get(verb)
        return check() if check is not None else True

    def adds(self, node: _N) -> str | None:
        """The level a person adds under *node* (feature -> project …), or ``None``."""
        if node.level == "feature" and node.kind == "feature":
            return "project"
        if node.level == "project" and node.tree == PRODUCT_TREE:
            return "task"
        if node.level == "need" and node.kind in ("opportunity", "ask"):
            return "feature"
        return None

    def moves(self, node: _N) -> str | None:
        """*node*'s level when a person moves it under another parent, else ``None``."""
        if node.level == "project" and node.tree == PRODUCT_TREE:
            return "project"
        if node.level == "feature" and node.kind == "feature":
            return "feature"
        return None

    def _seats(self, level: str | None, which: str) -> bool:
        """Whether anyone may make a *which* change to *level*'s nodes (its authority)."""
        if level is None:
            return False
        authority = (self.level_defs.get(level) or {}).get("authority") or {}
        return bool(authority.get(which))

    def _nameable(self, node: _N) -> bool:
        """An implicit feature, or an implicit need that comes from a proposal."""
        if node.level == "feature":
            return True
        if node.level != "need":
            return False
        origin = node_refs.parse(node.ref.removeprefix("need:").removeprefix("from-"))
        return (
            origin is not None
            and origin.kind == "proposal"
            and (origin.text in self.proposals_by_ref)
        )

    def thread(self, node: _N) -> dict[str, Any]:
        """The node's thread key and who answers it (a run shows its task's thread)."""
        target = node
        if node.level == "run":
            target = next(
                (a for a in reversed(self.ancestors(node.ref)) if a.level == "task"), node
            )
        key = node_refs.thread_key(target.ref)
        if target.level == "change" and target.kind == "pr":
            key = f"pr:{target.row_id}" if target.row_id else None
        root_thread = "improvements" if target.tree == FACTORY_TREE else "factory"
        out: dict[str, Any] = {"key": key or root_thread, "unread": 0}
        if responder := self.responder(target):
            out["responder"] = responder
        return out

    def node_full(self, node: _N) -> dict[str, Any]:
        out = self.summary(node)
        why = node.why
        if node.ref in (node_refs.PRODUCT, node_refs.FX):
            hidden = [k for k in self.hidden_levels.get(node.tree, []) if k != "run"]
            shown = self.ladder[node.tree][1:]
            if hidden and shown:
                names = " or ".join(self._level_name(k, plural=True).lower() for k in hidden)
                why = (
                    f"{why} No {_esc(names)} are set yet, so that level is hidden and "
                    f"{_esc(self._level_name(shown[0], plural=True).lower())} hang off the "
                    f"{_esc(self._level_name(node.level).lower())}."
                ).strip()
        out["why_html"] = why
        if node.acceptance and node.acceptance.get("steps"):
            out["acceptance"] = {
                k: v for k, v in node.acceptance.items() if k != "tally" and v is not None
            }
        if node.evidence:
            out["evidence"] = node.evidence
        if tried := self.try_it(node):
            out["try_it"] = tried
        if node.ref in (node_refs.PRODUCT, _ENVIRONMENTS) and (env := self.base_env_view()):
            out["env"] = env
        if node.ref == _HEALTH and self.closed_alarms:
            out["closed_alarms"] = [a.to_json() for a in self.closed_alarms]
        if node.ref == _DEFINITION:
            # The levels every view names (GET …/levels), whether or not this viewer renames them.
            out["levels"] = True
        return out

    def _smoke(self) -> dict[str, Any] | None:
        """Whether the base env answers, when the server's probe ran (else no smoke at all)."""
        probe = ((self.facts.base_env or {}).get("env") or {}).get("probe") or {}
        health = probe.get("health")
        if not health or health == "unknown":
            return None
        out: dict[str, Any] = {"ok": health == "ok"}
        checks = [
            f"{key} {code}"
            for key in ("app", "api")
            if (code := (probe.get(key) or {}).get("code")) is not None
        ]
        if checks:
            out["checks"] = " · ".join(checks)
        return out

    def base_env_view(self) -> dict[str, Any] | None:
        """The base env as the root shows it: its branch, the commit it runs, its links."""
        base = self.facts.base_env or {}
        branch = base.get("branch")
        env = self._env()
        if not branch or not env:
            return None
        out: dict[str, Any] = {"name": "Base env", "branch": str(branch)}
        deployed = self._deployed()
        if deployed is not None:
            out["sha"] = deployed.sha[:7]
            if deployed.at:
                out["deployed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(deployed.at))
        if env.get("state"):
            out["state"] = str(env["state"])
        if smoke := self._smoke():
            out["smoke"] = smoke
        urls = env.get("urls") or {}
        for key, name in (("app", "app_url"), ("api", "api_url")):
            if urls.get(key):
                out[name] = str(urls[key])
        if env.get("note"):
            out["note"] = _clip(env["note"], _SHORT_MAX)
        if base.get("env_project") and _project_ref(base["env_project"]) in self.nodes:
            out["ref"] = _project_ref(base["env_project"])
        # Where the factory shows it (``ref`` is the project that sets it up).
        out["area"] = _ENVIRONMENTS
        return out

    def child_page(self, node: _N, offset: int = 0) -> dict[str, Any]:
        """The node's direct children (folded), in order columns, 60 a page from *offset*."""
        ladder = self.ladder[node.tree]
        index = ladder.index(node.level) if node.level in ladder else len(ladder) - 1
        level = ladder[index + 1] if index + 1 < len(ladder) else ""
        shown = self.shown_children(node.ref)
        items = [k for k in shown if not k.ghost and not k.later]
        later = [k for k in shown if k.later and not k.ghost]
        ghosts = [k for k in shown if k.ghost]
        grouped = len(items) > PAGE_SIZE
        if grouped:
            items.sort(key=lambda k: (-STATE_RANK.get(self.state(k.ref), 0), k.col or 0))
        else:
            items.sort(key=lambda k: k.col or 0)
        cols = sorted({k.col or 0 for k in items})
        ordered = len(cols) > 1 or bool(later)
        page = items[offset : offset + PAGE_SIZE]
        first = offset == 0
        out: dict[str, Any] = {
            "level": level,
            "level_name": self._level_name(level, plural=True) if level else "",
            "columns": [
                {"title": self.order_cols[min(i, len(self.order_cols) - 1)], "col": c}
                for i, c in enumerate(cols)
            ]
            if ordered
            else None,
            "items": [self.summary(k) for k in page],
            "later": [self.summary(k) for k in later] if first else [],
            "ghosts": [self.summary(k) for k in ghosts] if first else [],
            "total": len(items) + len(later) + len(ghosts),
            "tiles": level not in ROW_LEVELS,
        }
        if later and first:
            out["later_title"] = self.later_title
        if offset + PAGE_SIZE < len(items):
            out["next"] = str(offset + PAGE_SIZE)
        if grouped:
            out["grouped_by_state"] = True
        via = sum(1 for k in shown if k.level != level)
        if via:
            out["via_fold"] = via
        return out

    def page(
        self, ref: str, *, cursor: str | None = None, can_edit: bool = True
    ) -> dict[str, Any] | None:
        """
        ``GET …/n/{ref}``: one node with its breadcrumb, state, roll-up and children.

        A folded implicit node answers as its only child.

        :param cursor: ``children.next`` of a previous page (the next 60 children).
        :returns: The ``NodePage``, or ``None`` when no node has *ref*.
        :raises ValueError: On a cursor this tree never handed out.
        """
        found = self.lookup(ref)
        if found is None:
            return None
        node = self.fold(found)
        offset = _offset(cursor)
        chips = self.chips(node)
        out: dict[str, Any] = {
            "node": self.node_full(node),
            "breadcrumb": [self.crumb(a) for a in self.ancestors(node.ref)],
            "status": self.status(node),
            "rollup": self.rollup(node),
            "chips": chips[:3],
            "more_chips": max(0, len(chips) - 3),
            "verbs": self.verbs(node, can_edit=can_edit),
            "levels_below": self.levels_below(node),
            "children": self.child_page(node, offset),
            "thread": self.thread(node),
            "version": self.version,
        }
        if len(chips) > 3:
            out["chips_more"] = [{"chips": chips[3:]}]
        if node.tree == PRODUCT_TREE:
            out["results"] = self.results(node)
        if node.parent is None:
            out["home"] = self.home.get(node.tree) or (self.ladder[node.tree][1:2] or [""])[0]
        return out

    def level_view(
        self,
        ref: str,
        level: str,
        *,
        cursor: str | None = None,
        state: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any] | None:
        """
        ``GET …/n/{ref}/level/{level}``: the nodes on *level* under *ref*,
        grouped under each one's parent (sections, never nodes joined by lines).

        Groups whose work is all done fold; each group shows 60 items and hands
        out a cursor for its next 60 (with one, only that group comes back).

        :param state: Only nodes in this state (``needs``, ``done`` …); with
            ``later`` or ``ghost`` the held or proposed nodes are the items.
        :param q: Only nodes whose title or code holds these words (any case).
            A cursor pages the filtered group. A group with nothing to show
            (no items, nothing later) is left out.
        :returns: The ``LevelView``, or ``None`` when *ref* names no node or
            *level* is not below it in its tree.
        :raises ValueError: On a cursor this tree never handed out.
        """
        found = self.lookup(ref)
        if found is None:
            return None
        scope = self.fold(found)
        ladder = self.ladder[scope.tree]
        if level not in ladder or ladder.index(level) <= self.level_index(scope):
            return None
        want, offset = _group_cursor(cursor)
        words = " ".join((q or "").lower().split())
        filtered = state is not None or bool(words)

        def keep(node: _N) -> bool:
            if state is not None and self.state(node.ref) != state:
                return False
            return not words or words in node.title.lower() or words in (node.tag or "").lower()

        grouped: dict[str, list[_N]] = {}
        for raw in self._raw_at_level(scope.ref, level):
            shown = self.fold(raw)
            if filtered and not keep(shown):
                continue
            grouped.setdefault(raw.parent or scope.ref, []).append(shown)
        if want is not None and want not in grouped:
            raise ValueError(f"no group {want!r} under {scope.ref} at {level}")
        order = sorted(
            grouped,
            key=lambda p: (
                -STATE_RANK.get(self.state(p), 0),
                -len(self.dependents_of(p)),
            ),
        )
        groups: list[dict[str, Any]] = []
        done_groups: list[_N] = []
        shown_total = 0
        via = 0
        for parent_ref in order:
            members = grouped[parent_ref]
            if state in ("later", "ghost"):
                active, later = members, []  # asked for by name: they are the items
            else:
                active = [k for k in members if not k.ghost and not k.later]
                later = [k for k in members if k.later and not k.ghost]
            if not active and not later:
                continue  # only proposals: nothing this view shows
            shown_total += len(active)
            via += sum(1 for k in members if k.level != level)
            done = bool(active) and all(self.state(k.ref) == "done" for k in active)
            parent = self.nodes[parent_ref]
            if done:
                done_groups.append(parent)
            if want is not None and parent_ref != want:
                continue
            active.sort(key=lambda k: (-STATE_RANK.get(self.state(k.ref), 0), k.col or 0))
            start = offset if want is not None else 0
            group: dict[str, Any] = {
                "parent": self._group_parent(parent),
                "items": [self.summary(k) for k in active[start : start + PAGE_SIZE]],
            }
            if done:
                group["folded_done"] = True
            if later and start == 0:
                group["later"] = [k.title for k in later]
            if start + PAGE_SIZE < len(active):
                group["next"] = f"{parent_ref}@{start + PAGE_SIZE}"
            groups.append(group)
        index = ladder.index(level)
        out: dict[str, Any] = {
            "scope": scope.ref,
            "level": level,
            "level_name": self._level_name(level, plural=True),
            "total": shown_total,
            "groups": groups,
            "tiles": level not in ROW_LEVELS,
        }
        if index - 1 > 0:
            out["parent_level_name"] = self._level_name(ladder[index - 1], plural=True).lower()
        if len(done_groups) > 1 and want is None:
            noun = out.get("parent_level_name") or "groups"
            out["done_groups"] = {
                "count": len(done_groups),
                "titles": [p.title for p in done_groups],
                "in_base_env": all(self.in_base_env(p) for p in done_groups),
                "noun": f"{noun} all done",
            }
        if via:
            out["via_fold"] = via
        return out

    def _raw_at_level(self, ref: str, level: str) -> list[_N]:
        """The stored or implicit nodes on *level* under *ref*, before folding."""
        out: list[_N] = []
        stack = list(reversed(self.kids.get(ref, [])))
        while stack:
            node = self.nodes[stack.pop()]
            if node.level == level:
                out.append(node)
            else:
                stack.extend(reversed(self.kids.get(node.ref, [])))
        return out

    def _group_parent(self, node: _N) -> dict[str, Any]:
        """A group's title: the parent's crumb, its visible ancestors, a need's evidence."""
        out = self.crumb(node)
        out["ancestors"] = [
            a.title
            for a in self.ancestors(node.ref)
            if a.parent is not None and not self.folded(a)
        ]
        summary = (node.evidence or {}).get("summary") if isinstance(node.evidence, dict) else None
        if summary:
            out["evidence"] = str(summary)
        return out


def _group_cursor(cursor: str | None) -> tuple[str | None, int]:
    """``(group parent, offset)`` of a level view's cursor (``<parent ref>@<offset>``)."""
    if not cursor:
        return None, 0
    parent, sep, offset = cursor.rpartition("@")
    if not sep or not parent or not offset.isdigit() or int(offset) % PAGE_SIZE:
        raise ValueError(f"bad cursor {cursor!r}")
    return parent, int(offset)


def _wait(item: Mapping[str, Any]) -> waits.Wait | None:
    """A ``WaitingOnView`` item read back as a :class:`.waits.Wait`."""
    kind = str(item.get("kind") or "")
    if kind not in waits.KINDS:
        return None
    since = item.get("since")
    return waits.Wait(
        kind=kind,
        ref=str(item.get("ref") or ""),
        label=str(item.get("label") or ""),
        who=str(item.get("who") or ""),
        person=bool(item.get("person")),
        since=int(since) if isinstance(since, int | float) else None,
        task=str(item["task"]) if item.get("task") else None,
        decision_id=str(item["decision_id"]) if item.get("decision_id") else None,
    )


def _project_own(stage: str, status: str | None, *, running: bool) -> tuple[str, str | None]:
    """
    A project's own state (and the one line that says why) from its stage.

    Closed (status done, or stage merged / done) is done whatever tasks it left.
    Queued has not started and ready waits for its merge. Any stage between
    moves only while its lead or a teammate is working; otherwise it waits.
    """
    if status == "done" or stage in (STAGE_MERGED, STAGE_DONE):
        return "done", None
    if stage in _STAGE_STATE:
        return _STAGE_STATE[stage], None
    if running:
        return "moving", None
    return "waiting", f"in {stage.replace('_', ' ')} · nobody is working on it"


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    if not cursor.isdigit() or int(cursor) % PAGE_SIZE:
        raise ValueError(f"unknown cursor {cursor!r}")
    return int(cursor)


def _worst(states: Sequence[str]) -> str:
    return max(states, key=lambda s: STATE_RANK.get(s, 0))


def _initiative_block(analysis: Any) -> Any:
    from omnigent.server.factory.initiatives import initiative_block

    return initiative_block(analysis)


def _planned(proposal: FactoryProposal) -> list[tuple[str, str]]:
    """``(slug, title)`` of every project a proposal would create."""
    out: list[tuple[str, str]] = []
    for op in proposal.ops or []:
        if not isinstance(op, Mapping):
            continue
        created = (
            [op.get("project")]
            if op.get("op") == "create"
            else list(op.get("into") or [])
            if op.get("op") == "split"
            else []
        )
        for item in created:
            if isinstance(item, Mapping) and PROJECT_SLUG_RE.match(str(item.get("slug") or "")):
                out.append((str(item["slug"]), str(item.get("title") or item["slug"])))
    return out


def _steps(
    row: FactoryInitiative, acceptance: Mapping[str, Any] | None
) -> tuple[tuple[int, str, str | None, str], ...]:
    """``(n, text, owner slug, result)`` of each acceptance step (:func:`_acceptance`'s)."""
    shown = (acceptance or {}).get("steps") or []
    steps = [s for s in plan_of(row)["acceptance"] if isinstance(s, Mapping)]
    return tuple(
        (
            int(step.get("n") or i),
            str(view["text"]),
            str(step["owner"]) if step.get("owner") else None,
            str(view["result"]),
        )
        for i, (step, view) in enumerate(zip(steps, shown, strict=False), start=1)
    )


def _acceptance(row: FactoryInitiative) -> dict[str, Any] | None:
    """A feature's acceptance steps with their newest result on the base env, and the tally."""
    steps = [s for s in plan_of(row)["acceptance"] if isinstance(s, Mapping)]
    if not steps:
        return None
    finished = [r for r in row.runs or [] if r.get("status") in RUN_DONE]
    last = finished[-1] if finished else None
    seen = {
        int(s.get("n") or 0): s for s in (last or {}).get("steps") or [] if isinstance(s, Mapping)
    }
    out_steps: list[dict[str, Any]] = []
    passed = 0
    for i, step in enumerate(steps, start=1):
        result = seen.get(int(step.get("n") or i))
        status = (result or {}).get("status")
        verdict = "pass" if status == "pass" else "fail" if status == "fail" else "none"
        passed += verdict == "pass"
        text = str(step.get("step") or "")
        if step.get("expect"):
            text = f"{text}: {step['expect']}"
        if result is not None:
            source = f"run {last.get('n')}" if last else "run"
            if result.get("note"):
                source = f"{source} · {_clip(result['note'], _SHORT_MAX)}"
        else:
            source = "not run yet"
        out_steps.append({"text": _clip(text, 400), "result": verdict, "source": source})
    out: dict[str, Any] = {
        "steps": out_steps,
        "tally": {"total": len(steps), "passed": passed},
    }
    if last is not None and last.get("reported_at"):
        out["run_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(last["reported_at"])))
    return out


# ── The one-parent rule ────────────────────────────────────────


class LevelError(AssertionError):
    """A projected node is not exactly one level under its parent, in its own tree."""


def check_levels(tree: Tree) -> list[str]:
    """
    Every node has exactly one parent, on the level directly above, in its own tree.

    :returns: One line per node that breaks the rule (empty: the tree holds).
    """
    bad: list[str] = []
    for node in tree.nodes.values():
        if node.parent is None:
            if node.ref not in (node_refs.PRODUCT, node_refs.FX):
                bad.append(f"{node.ref}: no parent")
            continue
        parent = tree.nodes.get(node.parent)
        if parent is None:
            bad.append(f"{node.ref}: parent {node.parent} is not in the tree")
            continue
        if parent.tree != node.tree:
            bad.append(f"{node.ref}: parent {parent.ref} is in the {parent.tree} tree")
            continue
        mine, theirs = tree.level_index(node), tree.level_index(parent)
        if mine < 0:
            bad.append(f"{node.ref}: level {node.level} is not shown in the {node.tree} tree")
        elif theirs != mine - 1:
            bad.append(
                f"{node.ref} ({node.level}) sits under {parent.ref} ({parent.level}), "
                "not one level up"
            )
    seen: dict[str, int] = {}
    for refs in tree.kids.values():
        for ref in refs:
            seen[ref] = seen.get(ref, 0) + 1
    bad += [f"{ref}: listed under {n} parents" for ref, n in seen.items() if n > 1]
    return bad


def assert_levels(tree: Tree) -> Tree:
    """:raises LevelError: When :func:`check_levels` finds a node out of place."""
    if bad := check_levels(tree):
        raise LevelError("; ".join(bad[:20]))
    return tree


# ── The cache ──────────────────────────────────────────────────

_trees: dict[tuple[str, str], tuple[float, Tree]] = {}
_locks: dict[tuple[int, str], asyncio.Lock] = {}
_TREES_MAX: Final = 64


def viewer_key(readable: Iterable[str]) -> str:
    """:returns: A digest of the sessions a viewer may read (who read the same share a tree)."""
    return hashlib.sha256("\n".join(sorted(set(readable))).encode()).hexdigest()[:24]


def hidden_projects(raw: RawSnapshot, readable: Iterable[str]) -> frozenset[str]:
    """:returns: The slugs of the projects whose lead the viewer cannot read."""
    sessions = set(readable)
    return frozenset(
        slug for slug, pl in raw.project_pl.items() if pl is not None and pl not in sessions
    )


async def tree_for(
    deps: FactoryDeps,
    root: Conversation,
    raw: RawSnapshot,
    readable: Iterable[str],
    *,
    app_state: Any = None,
) -> Tree:
    """
    The factory as a viewer who may read the sessions *readable* sees it, built
    from the cached snapshot cut to that viewer (``snapshot.filter_snapshot``:
    members, team chips, cost and burn, the base env) and from the store rows
    of the projects they may read; reused for :data:`FACTS_TTL_S`.

    :param app_state: The server's state, read on a rebuild for the auto-merge
        targets the server merges into now (a ready PR into one waits on the
        server, not a person); ``None``: a person merges every PR.
    """
    from omnigent.server.factory.snapshot import filter_snapshot

    fid = norm_id(root.id)
    sessions = set(readable)
    key = (fid, viewer_key(sessions))
    cached = _trees.get(key)
    if cached is not None and time.monotonic() - cached[0] < FACTS_TTL_S:
        return cached[1]
    lock = _locks.setdefault((id(asyncio.get_running_loop()), fid), asyncio.Lock())
    async with lock:
        cached = _trees.get(key)
        if cached is not None and time.monotonic() - cached[0] < FACTS_TTL_S:
            return cached[1]

        def build() -> Tree:
            body = filter_snapshot(raw, sessions)
            targets = waits.auto_targets(app_state, deps, root.id)
            facts = load_facts(
                deps, root, body, readable=sessions, leads=raw.project_pl, targets=targets
            )
            tree = Tree(facts, hidden=hidden_projects(raw, sessions))
            if bad := check_levels(tree):
                _logger.warning(
                    "factory %s: the level projection breaks the rule: %s", fid, bad[:5]
                )
            return tree

        tree = await asyncio.to_thread(build)
        if len(_trees) >= _TREES_MAX:
            _trees.clear()
        _trees[key] = (time.monotonic(), tree)
        return tree


def invalidate(factory_id: str) -> None:
    """Forget a factory's projections (a write changed what they read)."""
    fid = _bare(factory_id)
    for key in [k for k in _trees if k[0] == fid]:
        _trees.pop(key, None)


def reset_cache() -> None:
    """Forget every projection (tests)."""
    _trees.clear()
    _locks.clear()


_QUIET_EVENTS: Final = ("message.", "chat.", "presence.", "mail.")
"""Event kinds that change nothing the projection reads."""


def _on_event(_app_state: Any, event: Any) -> None:
    if not str(event.kind).startswith(_QUIET_EVENTS):
        invalidate(event.factory_id)


def register() -> None:
    """Drop a factory's projection on every recorded write (idempotent)."""
    from omnigent.server.factory import events

    if _on_event not in events._listeners:
        events.add_listener(_on_event)


register()


__all__ = [
    "FACTS_TTL_S",
    "NO_GOAL",
    "PAGE_SIZE",
    "LevelError",
    "NodeFacts",
    "Tree",
    "assert_levels",
    "check_levels",
    "creator_words",
    "hidden_projects",
    "invalidate",
    "load_facts",
    "register",
    "reset_cache",
    "tree_for",
    "viewer_key",
]
