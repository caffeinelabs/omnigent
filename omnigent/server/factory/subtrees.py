"""Autonomy and auto-merge per subtree: an owner's setting on a node covers all under it.

Owners scope two settings to one node of the product tree (a goal ``G-n``, a
need ``O-n``, a feature ``I-n`` or a project slug). A scope covers the node and
every node under it, also the ones added later:

- ``autonomy.scopes`` ``[{ref, level}]``: the autonomy level that proposals
  about the projects under it are decided at (:func:`.autonomy.decide`);
- ``product.delivery.auto_merge_scopes`` ``[{ref, repo, base}]``: the server
  merges the ready PRs of the projects under it into ``repo#base``
  (:mod:`.auto_merge`); ``{ref, merge: false}`` keeps them merged by people,
  whatever an ancestor or the factory-wide targets say.

The nearest scoped ancestor decides, the node's own entry first. A project's
ancestors are read from the store the way the node tree (:class:`.nodes.Tree`)
files them: its home feature (:mod:`.features`), that feature's need (its
``plan.opportunity``, else the opportunity of the proposal that started it,
a live one) and the need's goal (one of ``strategy.goals``). A project with no
home sits in the implicit feature of the proposal that created it, under that
proposal's opportunity: a scope on the need or its goal covers it too.

Only owners set either list (class B, a person's edit). No agent proposes them
(:data:`.definition_blocks.AGENT_NEVER`), so no agent adds or widens a scope.
No base is ever inferred: an auto-merge scope names its repo and base.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Final, TypeVar

from pydantic import AfterValidator

from omnigent.factory import PROJECT_SLUG_RE, THREAD_FACTORY

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryInitiative, FactoryProject, FactoryProposal
    from omnigent.entities.factory_discovery import FactoryOpportunity
    from omnigent.server.factory.features import Home

SCOPES_MAX: Final = 50
"""Entries per scope list."""
SCOPE_LEVELS: Final = ("goal", "need", "feature", "project")
"""Levels of the product tree whose stored nodes a scope may name."""
_NUMBERED_RE: Final = re.compile(r"^(G|O|I)-[1-9][0-9]{0,8}$")
_RESERVED: Final = frozenset({"product", "fx", THREAD_FACTORY})
"""Words a node ref keeps for the roots (``node_refs.RESERVED``): never a project slug here."""
SCOPE_REFUSAL: Final = (
    "a scope names a goal (G-n), a need (O-n), a feature (I-n) or a project slug"
)

_T = TypeVar("_T")


def scope_ref(value: str) -> str:
    """:returns: *value* when a scope may name it (:data:`SCOPE_REFUSAL`); else raises."""
    text = value.strip()
    if _NUMBERED_RE.match(text) or (PROJECT_SLUG_RE.match(text) and text not in _RESERVED):
        return text
    raise ValueError(SCOPE_REFUSAL)


def is_scope_ref(value: object) -> bool:
    """Whether a scope may name *value*."""
    try:
        return isinstance(value, str) and scope_ref(value) == value
    except ValueError:
        return False


ScopeRef = Annotated[str, AfterValidator(scope_ref)]


def unique_refs(items: list[_T]) -> list[_T]:
    """A pydantic check: each node is scoped at most once per list."""
    seen: set[str] = set()
    for item in items:
        ref = str(getattr(item, "ref", ""))
        if ref in seen:
            raise ValueError(f"{ref} is scoped twice")
        seen.add(ref)
    return items


def nearest(chain: Iterable[str], scoped: Mapping[str, _T]) -> tuple[str, _T] | None:
    """
    :param chain: A node's ref, then its ancestors' (nearest first).
    :returns: ``(ref, entry)`` of the nearest scoped one, else ``None``.
    """
    for ref in chain:
        if ref in scoped:
            return ref, scoped[ref]
    return None


@dataclass
class Lineage:
    """
    The stored ancestors of projects, as the node tree files them.

    :param initiatives: The factory's initiatives (closed ones are nobody's
        home); ``None``: not read, a project is placed by its stored feature.
    :param opportunities: Live opportunities (not killed or merged) by ``O-n``.
    :param goals: The goal ids of ``strategy.goals``.
    :param proposal_needs: ``C-n`` -> the opportunity it planned (``O-n``): the
        need of the implicit feature of the projects it created with no home,
        and of an initiative it started that names no need of its own.
    :param creators: Project slug -> the applied ``C-n`` that created it
        (``result.created``), for a project whose charter names no proposal.
    """

    initiatives: Sequence[FactoryInitiative] | None = None
    opportunities: Mapping[str, FactoryOpportunity] = field(default_factory=dict)
    goals: frozenset[str] = frozenset()
    proposal_needs: Mapping[str, str] = field(default_factory=dict)
    creators: Mapping[str, str] = field(default_factory=dict)
    _homes: dict[str, Home] = field(default_factory=dict, repr=False)
    _by_ref: dict[str, FactoryInitiative] | None = field(default=None, repr=False)
    _find: Callable[[FactoryProject], Home] | None = field(default=None, repr=False)

    def _initiative(self, ref: str) -> FactoryInitiative | None:
        if self._by_ref is None:
            self._by_ref = {r.ref: r for r in self.initiatives or ()}
        return self._by_ref.get(ref)

    def home(self, project: FactoryProject) -> Home:
        """:returns: *project*'s home feature and the other features that list it."""
        from omnigent.server.factory.features import Home, HomeFinder

        found = self._homes.get(project.slug)
        if found is None:
            if self.initiatives is None:
                found = Home(project.feature or None, "stored" if project.feature else "none")
            else:
                if self._find is None:
                    self._find = HomeFinder(self.initiatives)
                found = self._find(project)
            self._homes[project.slug] = found
        return found

    def feature_of(self, project: FactoryProject) -> str | None:
        """:returns: *project*'s home feature (``I-n``), else ``None`` (implicit)."""
        return self.home(project).feature

    def origin_of(self, project: FactoryProject) -> str | None:
        """:returns: The ``C-n`` that created *project* (charter, else ``result.created``)."""
        from omnigent.server.factory.features import origin_proposal

        return origin_proposal(project) or self.creators.get(project.slug)

    def _live(self, ref: object) -> str | None:
        return ref if isinstance(ref, str) and ref in self.opportunities else None

    def proposal_need(self, proposal: str | None) -> str | None:
        """:returns: The live opportunity (``O-n``) proposal *proposal* planned, else ``None``."""
        return self._live(self.proposal_needs.get(proposal)) if proposal else None

    def need_of(self, feature: str | None) -> str | None:
        """
        :returns: The live opportunity (``O-n``) *feature* serves, else ``None``
            (it sits under a person's ask or an implicit need).
        """
        row = self._initiative(feature) if feature else None
        if row is None:
            return None
        from omnigent.server.factory import node_refs
        from omnigent.server.factory.initiative_view import plan_of

        plan = plan_of(row)
        if found := self._live(plan.get("opportunity")):
            return found
        asker = row.requested_by or {}
        if asker.get("kind") == "human" and _is_id((row.ask_ref or "").removeprefix("chat_")):
            return None
        named = node_refs.parse(plan.get("need"))
        if named is not None and named.kind == "need" and named.id.startswith("by-"):
            return None
        origin = row.created_by if (row.created_by or "").startswith("C-") else row.ref
        return self.proposal_need(origin)

    def goal_of(self, need: str | None) -> str | None:
        """:returns: The goal (``G-n``) *need* sits under, else ``None``."""
        row = self.opportunities.get(need) if need else None
        goal = row.goal if row is not None else None
        return goal if goal and goal in self.goals else None

    def above(self, feature: str | None, need: str | None = None) -> tuple[str, ...]:
        """
        :returns: *feature*, then its need and goal; with no *feature*, the live
            need *need* and its goal (an implicit feature's).
        """
        out: list[str] = [feature] if feature else []
        found = self.need_of(feature) if feature else self._live(need)
        if found:
            out.append(found)
            if goal := self.goal_of(found):
                out.append(goal)
        return tuple(out)

    def chain(self, project: FactoryProject) -> tuple[str, ...]:
        """:returns: *project*'s slug, then its feature, need and goal (those it has)."""
        if self.initiatives is not None:
            from omnigent.factory import PROJECT_KIND_WORK
            from omnigent.server.factory.charter import project_kind

            if project_kind(project) != PROJECT_KIND_WORK:
                return (project.slug,)  # the factory tree's: no product node holds it
        if feature := self.feature_of(project):
            return (project.slug, *self.above(feature))
        return (project.slug, *self.above(None, self.proposal_need(self.origin_of(project))))


def _is_id(value: str) -> bool:
    """Whether *value* is a row id (a person's ask in the Room is one message)."""
    from omnigent.stores.factory_store import norm_id

    try:
        norm_id(value)
    except ValueError:
        return False
    return bool(value)


def needs_rows(refs: Iterable[str]) -> tuple[bool, bool]:
    """
    :returns: ``(initiatives, needs)``: which rows a lineage must read to place
        the projects under *refs* (a project slug needs neither; a need or a
        goal also needs the opportunities and the applied proposals).
    """
    kinds = {ref.split("-", 1)[0] for ref in refs if _NUMBERED_RE.match(ref)}
    return bool(kinds), bool(kinds & {"O", "G"})


def load(store: Any, factory_id: str, refs: Iterable[str], goals: Iterable[Any] = ()) -> Lineage:
    """
    The lineage the scopes *refs* need, read from *store* (synchronous).

    :param goals: ``strategy.goals`` (``{id, …}``).
    """
    wanted = list(refs)
    initiatives, needs = needs_rows(wanted)
    return lineage_of(
        store.list_initiatives(factory_id, limit=5000) if initiatives else None,
        store.list_opportunities(factory_id, limit=5000) if needs else (),
        goals,
        store.list_proposals(factory_id, status=["applied"], limit=5000) if needs else (),
    )


def lineage_of(
    initiatives: Iterable[FactoryInitiative] | None,
    opportunities: Iterable[FactoryOpportunity],
    goals: Iterable[Any] = (),
    proposals: Iterable[FactoryProposal] = (),
) -> Lineage:
    """
    A :class:`Lineage` from rows already read.

    :param initiatives: ``None``: not read (projects placed by their stored feature).
    :param goals: ``strategy.goals``.
    :param proposals: Proposals read (the applied ones say what created a
        project; any one may have planned an opportunity).
    """
    from omnigent.server.factory.features import CLOSED

    needs: dict[str, str] = {}
    creators: dict[str, str] = {}
    for row in sorted(proposals, key=lambda p: p.number):
        if row.opportunity:
            needs.setdefault(row.ref, row.opportunity)
        created = (row.result or {}).get("created") if row.status == "applied" else None
        for slug in created if isinstance(created, list) else []:
            creators.setdefault(str(slug), row.ref)
    return Lineage(
        initiatives=None
        if initiatives is None
        else [r for r in initiatives if r.status not in CLOSED],
        opportunities={o.ref: o for o in opportunities if not o.kill and not o.merged_into},
        goals=frozenset(str(g.get("id")) for g in goals if isinstance(g, Mapping) and g.get("id")),
        proposal_needs=needs,
        creators=creators,
    )


__all__ = [
    "SCOPES_MAX",
    "SCOPE_LEVELS",
    "SCOPE_REFUSAL",
    "Lineage",
    "ScopeRef",
    "is_scope_ref",
    "lineage_of",
    "load",
    "nearest",
    "needs_rows",
    "scope_ref",
    "unique_refs",
]
