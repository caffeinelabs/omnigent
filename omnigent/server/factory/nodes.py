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
:func:`tree_for` memoizes a factory's tree for :data:`FACTS_TTL_S` and drops
it on any write the event log records.

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
  (``PR-n``; a PR no task owns gets the implicit task ``adhoc:<pr id>``) and
  the sessions that ran a task (``run:<session id>``, under its PR).
- the factory tree: its areas, open rule proposals (``C-n``), the top-level
  members and the environments.

Implicit parents are keyed by their origin and fold into their only child
(``structure.fold_single_child``). A level hidden by the definition's
``show`` rule (``when_any`` with no node, ``when_many`` with one) is left out
and its nodes' children hang one level up. :func:`check_levels` proves the
one-parent rule on a built tree; the status line here is the state only (the
blocker path comes later).

Nothing here names a product: titles come from the factory's records and
level names from its definition.
"""

from __future__ import annotations

import asyncio
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
from omnigent.server.factory import features, levels, node_refs
from omnigent.server.factory.charter import normalize_charter, project_kind
from omnigent.server.factory.definition_blocks import (
    FACTORY_TREE,
    KIND_IS_LEVEL,
    PRODUCT_TREE,
    TREES,
)
from omnigent.server.factory.graph import DEFAULT_WEIGHT_MIN, TaskNode, layers
from omnigent.server.factory.initiative_view import RUN_DONE, plan_of
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import (
        FactoryChatMessage,
        FactoryDecision,
        FactoryInitiative,
        FactoryPr,
        FactoryProject,
        FactoryProposal,
        FactoryTask,
    )
    from omnigent.entities.factory_discovery import FactoryOpportunity
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

FACTS_TTL_S: Final = 5.0
"""How long one factory's projection is reused (any recorded write drops it sooner)."""
PAGE_SIZE: Final = 60
"""Children per page (``children.next`` reads the next ones)."""
NO_GOAL: Final = "goal:no-goal"
"""The implicit goal of needs that name none, once the factory has goals."""
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
"""A project's own state by stage (the stages between queued and ready move)."""
_PROJECT_WAIT_KINDS: Final = frozenset(
    {"stall", "proposal", "hold", "merge", "dependency", "capacity"}
)
"""Project waits that make it wait (an open question is a decision: it needs you)."""
_RULE_KINDS: Final = frozenset({"tuning", "rules", "code"})
_RULE_OPEN: Final = frozenset({*PROPOSAL_OPEN_STATUSES, "trial"})
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
    "rename": ("Rename", "r"),
}
_READ_ONLY: Final = "You can read this factory; ask an owner for edit access to steer it."
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
    :param decisions: The open decisions.
    :param members: The snapshot's member views (``session_id``, ``title``,
        ``running``, ``is_top_level``, ``role``).
    :param asks: Chat message id (bare hex) -> ``(text, who)`` of the asks
        projects proposals answer.
    :param base_env: The snapshot's ``factory.base`` (head, the live env).
    :param cost: The factory's spend so far; ``burn`` its spend per hour.
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


def _ask_ids(initiatives: Iterable[Any], proposals: Iterable[Any]) -> set[str]:
    """The chat messages a projects proposal answers on a person's behalf (no initiative's ask)."""
    covered = {_bare((i.ask_ref or "").removeprefix("chat_")) for i in initiatives}
    wanted = {
        _bare(p.reply_to_chat)
        for p in proposals
        if p.kind == "projects" and p.on_behalf_of and p.reply_to_chat
    }
    return {m for m in wanted - covered if m}


def load_facts(
    deps: FactoryDeps, root: Conversation, body: Mapping[str, Any], *, now: float | None = None
) -> NodeFacts:
    """
    Read what the projection needs (synchronous; a handful of row reads).

    :param body: The factory's unfiltered snapshot body (``RawSnapshot.body``):
        its project views, members, base and totals are reused, not rebuilt.
    """
    from omnigent.server.factory import definition

    store = deps.factory_store
    fid = root.id
    resolved = definition.definition_for(deps, fid)
    doc = resolved.doc if resolved is not None else definition.default_doc()
    factory = body.get("factory") or {}
    base = factory.get("base") or {}
    initiatives = store.list_initiatives(fid, limit=5000)
    proposals = store.list_proposals(fid, limit=5000)
    asks: dict[str, tuple[str, str]] = {}
    for message_id in sorted(_ask_ids(initiatives, proposals)):
        try:
            message: FactoryChatMessage | None = store.get_chat_message(message_id)
        except ValueError:
            message = None
        if message is not None and message.body.strip():
            asks[message_id] = (message.body.strip(), message.author or "")
    product = (doc.get("product") or {}).get("name") or factory.get("name") or ""
    totals = body.get("totals") or {}
    return NodeFacts(
        factory_id=norm_id(fid),
        product=str(product),
        repo=base.get("repo"),
        base=base.get("branch"),
        structure=doc.get("structure") or {},
        definition_version=resolved.version if resolved is not None else 0,
        goals=list((doc.get("strategy") or {}).get("goals") or []),
        opportunities=store.list_opportunities(fid, limit=5000),
        initiatives=initiatives,
        proposals=proposals,
        projects=store.list_projects(fid),
        views={str(v.get("slug")): v for v in body.get("projects") or []},
        tasks=store.list_tasks(fid),
        prs=store.list_prs(fid),
        decisions=store.list_decisions(factory_id=fid, status="open", limit=1000),
        members=list(body.get("members") or []),
        asks=asks,
        base_env=base or None,
        cost=factory.get("cost_usd"),
        burn=totals.get("usd_per_h"),
        now=time.time() if now is None else now,
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


def _esc(text: Any) -> str:
    return html.escape(str(text or ""), quote=False)


def _clip(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _who(value: str | None) -> str:
    """An implicit key's origin words for a creator (``alice@x`` -> ``alice``)."""
    head = (value or "").split("@", 1)[0].lower()
    return (_WHO_RE.sub("-", head).strip("-") or "unknown")[:40]


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
        self.hidden_levels: dict[str, list[str]] = {}
        self._memo: dict[tuple[str, str], Any] = {}
        self._build()
        self._hide_levels()
        self._index()
        self._place_decisions()
        self._order_columns()

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
        own = "done" if status == "done" else _STAGE_STATE.get(stage, "moving")
        waits = any(
            w.get("kind") in _PROJECT_WAIT_KINDS and not w.get("task")
            for w in view.get("waiting_on") or []
        )
        pl = view.get("pl") or {}
        team = list(view.get("team") or [])
        working = tuple(str(m.get("title")) for m in team if m.get("running") and m.get("title"))
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
            leaf=item,
            waits=waits,
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

    def _projects(self) -> None:
        facts = self.facts
        live = sorted(
            (
                p
                for p in facts.projects
                if p.status not in ("cancelled", "merged") and p.slug not in self.hidden
            ),
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
        held = self._held_tasks()
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
            node = self._add(
                _N(
                    task.ref,
                    PRODUCT_TREE,
                    "task",
                    "adhoc" if task.kind == "adhoc" else "task",
                    _clip(task.title, _TITLE_MAX),
                    project,
                    tag=task.ref,
                    own=_TASK_STATE.get(task.state, "notstarted"),
                    leaf=True,
                    waits=task.state != "done" and task.ref in held,
                    why=_esc(_clip((task.card or {}).get("text"), _WHY_MAX)),
                    order=task.key,
                    deps=tuple(str(d) for d in task.deps or ()),
                    estimate=task.estimate_min,
                    cost=task.cost_usd,
                    owner=str(member.get("title")) if member and member.get("title") else None,
                )
            )
            tasks_by_id[norm_id(task.id)] = node
        counts: dict[int, int] = {}
        for pr in facts.prs:
            counts[pr.number] = counts.get(pr.number, 0) + 1
        own_pr = {t.ref: _bare(t.pr_id) for t in facts.tasks if t.pr_id}
        change_of: dict[str, str] = {}
        for pr in sorted(facts.prs, key=lambda p: p.number):
            project = self._work.get(pr.project_slug or "")
            if project is None or counts[pr.number] > 1:
                continue
            if pr.state == "closed":
                continue
            task = tasks_by_id.get(_bare(pr.task_id) or "")
            parent = task.ref if task is not None else self._adhoc(pr, project)
            ref = f"PR-{pr.number}"
            self._add(
                _N(
                    ref,
                    PRODUCT_TREE,
                    "change",
                    "pr",
                    _clip(pr.title or f"#{pr.number}", _TITLE_MAX),
                    parent,
                    tag=f"#{pr.number}",
                    own=self._pr_state(pr),
                    leaf=True,
                    merged_at=pr.merged_at,
                    merged_sha=pr.merged_sha,
                    base=pr.base_ref or None,
                )
            )
            if task is not None and (
                own_pr.get(task.ref) == _bare(pr.id) or task.ref not in change_of
            ):
                change_of[task.ref] = ref
        self._runs(change_of)

    def _held_tasks(self) -> set[str]:
        """Tasks something holds: a wait the snapshot lists on them, or a hold not lapsed."""
        out: set[str] = set()
        for view in self.facts.views.values():
            for wait in view.get("waiting_on") or []:
                if wait.get("task"):
                    out.add(str(wait["task"]))
        for task in self.facts.tasks:
            hold = task.hold or {}
            until = hold.get("until")
            if hold and (not isinstance(until, int | float) or until > self.facts.now):
                out.add(task.ref)
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
        for member in self.facts.members:
            sid = _bare(member.get("session_id"))
            if not sid or not member.get("is_top_level"):
                continue
            self._add(
                _N(
                    f"member:{sid}",
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
        for ref in node_refs.FACTORY_AREAS:
            area = self.nodes[f"{node_refs.FX}:{ref}"]
            area.own = "done"

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
        for decision in self.facts.decisions:
            if decision.status != "open":
                continue
            ref = self._decision_target(decision)
            if ref is not None:
                self.decisions_at.setdefault(ref, []).append(decision)

    def _decision_target(self, decision: FactoryDecision) -> str | None:
        """The node a decision is about (``api.md`` §5): its subject, else its project."""
        subject = (decision.subject or "").strip()
        head, _sep, rest = subject.partition(":")
        target: str | None = None
        if head.lower() == "pr" and (pid := _bare(rest.split("@", 1)[0])):
            pr = self._prs_by_id.get(pid)
            target = f"PR-{pr.number}" if pr is not None else None
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
            d.blocking for d in self.decisions_at.get(node.ref, [])
        ):
            state = "needs"
        return state

    def needs(self, ref: str) -> tuple[int, int]:
        """``(open, blocking)``: distinct open decisions in *ref*'s subtree."""

        def make() -> tuple[int, int]:
            found: dict[str, FactoryDecision] = {}
            for node in [self.nodes[ref], *self.subtree(ref)]:
                for d in self.decisions_at.get(node.ref, []):
                    found[d.id] = d
            return len(found), sum(1 for d in found.values() if d.blocking)

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
        """``(done by weight, tasks done, tasks, total weight)``; ``None`` without a task."""

        def make() -> tuple[float, int, int, float] | None:
            tasks = [k for k in self.real_children(node.ref) if k.level == "task"]
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
            if progress is None:
                growing = growing or self.state(project.ref) != "done"
                done += weight if self.state(project.ref) == "done" else 0.0
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
        Whether *node*'s merged changes are all in the base env (a change: merged
        before the live env last deployed; ancestry of the merge commit comes later).
        """

        def make() -> bool:
            if node.level == "change":
                return self._change_in_base(node)
            merged = [n for n in self.subtree(node.ref) if n.level == "change" and n.merged_at]
            return bool(merged) and all(self._change_in_base(n) for n in merged)

        return self._cached("inbase", node.ref, make)

    def _env(self) -> Mapping[str, Any]:
        return ((self.facts.base_env or {}).get("env") or {}).get("reported") or {}

    def _change_in_base(self, node: _N) -> bool:
        env = self._env()
        if not node.merged_at or env.get("state") != "ready":
            return False
        deployed = (env.get("deployed") or {}).get("sha")
        if deployed and node.merged_sha and str(deployed).startswith(str(node.merged_sha)[:7]):
            return True
        return int(env.get("at") or 0) >= int(node.merged_at)

    # ── The API's shapes ───────────────────────────────────────

    def _level_key(self, node: _N) -> str:
        return node.level

    def _level_label(self, node: _N) -> str:
        return self._level_name(node.level)

    def status(self, node: _N) -> dict[str, Any]:
        """The state-only ``StatusLine`` (the blocker path is not computed here yet)."""
        state = self.state(node.ref)
        kind, label = _STATUS.get(state, ("idle", state.capitalize()))
        out: dict[str, Any] = {"kind": kind, "label": label, "segments": [], "ends": "other"}
        if state == "moving":
            out["ends"] = "moving"
        if state == "later" and node.held_for:
            out["detail"] = f"held for {node.held_for}"
        elif state == "ghost" and node.short:
            out["detail"] = node.short
        elif state == "done":
            kids = len(self.real_children(node.ref))
            if kids > 1:
                out["detail"] = f"all {kids} done"
        return out

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
            "status_compact": self.status(node),
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
        if node.budget:
            out["budget"] = node.budget
        if node.tree == PRODUCT_TREE and node.level != "product" and self.in_base_env(node):
            out["in_base_env"] = True
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
            return {
                "merged": len(merged),
                "in_base_env": sum(1 for c in merged if self._change_in_base(c)),
                "open": [c.ref for c in changes if c.own != "done"][:PAGE_SIZE],
                "readouts": "not measured",
            }

        return dict(self._cached("results", node.ref, make))

    def rollup(self, node: _N) -> dict[str, Any]:
        out: dict[str, Any] = {
            "cost": round(float(self.cost(node) or 0.0), 2),
            "moving_anywhere": self.moving_anywhere(node.ref),
        }
        if node.ref == node_refs.PRODUCT:
            if counts := self._root_counts(node):
                out["counts"] = counts
            if self.facts.burn:
                out["burn"] = round(float(self.facts.burn), 2)
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
            if node.budget:
                out["budget"] = node.budget
            if node.working or node.idle:
                out["team"] = {
                    "working": [{"name": n, "working": True} for n in node.working],
                    "idle": node.idle or 0,
                }
        env = self._env()
        sha = (env.get("deployed") or {}).get("sha")
        if node.tree == PRODUCT_TREE and sha and self.results(node)["in_base_env"]:
            out["in_base_env"] = {"sha": str(sha)[:7], "try_it": False}
        return out

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

    def chips(self, node: _N) -> list[dict[str, Any]]:
        """Links to other nodes as chips: kind · target · its state. Never a line."""
        out: list[dict[str, Any]] = []
        met: list[tuple[str, str]] = []
        done = self.state(node.ref) == "done"
        for dep in node.depends_on:
            target = self.nodes.get(_project_ref(dep))
            if target is None:
                continue
            if done or self.state(target.ref) == "done":
                met.append((dep, target.ref))
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
        return out

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
        checks: dict[str, Callable[[], bool]] = {
            "talk": lambda: self.responder(node) is not None,
            "add": lambda: not node.implicit and not node.ghost and state != "done",
            "start": lambda: bool(node.depends_on) and state not in ("moving", "done"),
            "research": lambda: node.kind == "study",
            "team": lambda: node.level == "project" and not node.later and not node.ghost,
            "auto": lambda: state != "done" and not node.ghost,
            "pause": lambda: state not in ("done", "later", "offered") and not node.ghost,
            "move": lambda: not node.implicit and not node.ghost,
            "name": lambda: node.implicit,
        }
        check = checks.get(verb)
        return check() if check is not None else True

    def thread(self, node: _N) -> dict[str, Any]:
        """The node's thread key and who answers it (a run shows its task's thread)."""
        target = node
        if node.level == "run":
            target = next(
                (a for a in reversed(self.ancestors(node.ref)) if a.level == "task"), node
            )
        key = node_refs.thread_key(target.ref)
        if target.level == "change" and target.kind == "pr":
            number = int(target.ref.removeprefix("PR-"))
            pr = next((p for p in self._prs_by_id.values() if p.number == number), None)
            key = f"pr:{norm_id(pr.id)}" if pr is not None else None
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

_trees: dict[tuple[str, frozenset[str]], tuple[float, Tree]] = {}
_locks: dict[tuple[int, str], asyncio.Lock] = {}
_TREES_MAX: Final = 64


async def tree_for(
    deps: FactoryDeps,
    root: Conversation,
    body: Mapping[str, Any],
    *,
    hidden: Iterable[str] = (),
) -> Tree:
    """
    The factory's projection for a viewer who cannot read *hidden* projects,
    built from the cached snapshot *body* and reused for :data:`FACTS_TTL_S`.
    """
    fid = norm_id(root.id)
    key = (fid, frozenset(hidden))
    cached = _trees.get(key)
    if cached is not None and time.monotonic() - cached[0] < FACTS_TTL_S:
        return cached[1]
    lock = _locks.setdefault((id(asyncio.get_running_loop()), fid), asyncio.Lock())
    async with lock:
        cached = _trees.get(key)
        if cached is not None and time.monotonic() - cached[0] < FACTS_TTL_S:
            return cached[1]

        def build() -> Tree:
            tree = Tree(load_facts(deps, root, body), hidden=key[1])
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
    "invalidate",
    "load_facts",
    "register",
    "reset_cache",
    "tree_for",
]
