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
ancestors are read from the store the way the node tree files them: its home
feature (:mod:`.features`), that feature's need (``plan.opportunity``, a live
opportunity) and the need's goal (one of ``strategy.goals``).

Only owners set either list (class B, a person's edit). No agent proposes them
(:data:`.definition_blocks.AGENT_NEVER`), so no agent adds or widens a scope.
No base is ever inferred: an auto-merge scope names its repo and base.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Final, TypeVar

from pydantic import AfterValidator

from omnigent.factory import PROJECT_SLUG_RE, THREAD_FACTORY

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryInitiative, FactoryProject
    from omnigent.entities.factory_discovery import FactoryOpportunity

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

    :param initiatives: The factory's initiatives that are someone's home
        (not cancelled).
    :param opportunities: Live opportunities (not killed or merged) by ``O-n``.
    :param goals: The goal ids of ``strategy.goals``.
    """

    initiatives: Sequence[FactoryInitiative] = ()
    opportunities: Mapping[str, FactoryOpportunity] = field(default_factory=dict)
    goals: frozenset[str] = frozenset()
    _homes: dict[str, str | None] = field(default_factory=dict, repr=False)
    _by_ref: dict[str, FactoryInitiative] | None = field(default=None, repr=False)
    _started: dict[str, str] | None = field(default=None, repr=False)

    def _initiative(self, ref: str) -> FactoryInitiative | None:
        if self._by_ref is None:
            self._by_ref = {r.ref: r for r in self.initiatives}
        return self._by_ref.get(ref)

    def feature_of(self, project: FactoryProject) -> str | None:
        """:returns: *project*'s home feature (``I-n``), else ``None`` (implicit)."""
        if project.slug not in self._homes:
            if self.initiatives:
                from omnigent.server.factory.features import homes

                self._homes[project.slug] = homes([project], self.initiatives)[
                    project.slug
                ].feature
            else:
                self._homes[project.slug] = project.feature or None
        return self._homes[project.slug]

    def started_by(self, proposal: str | None) -> str | None:
        """:returns: The feature (``I-n``) proposal *proposal* (``C-n``) started or extended."""
        if not proposal:
            return None
        if self._started is None:
            from omnigent.server.factory.features import by_proposal

            self._started = by_proposal(list(self.initiatives))
        return self._started.get(proposal)

    def need_of(self, feature: str | None) -> str | None:
        """:returns: The live opportunity (``O-n``) *feature* serves, else ``None``."""
        row = self._initiative(feature) if feature else None
        if row is None:
            return None
        from omnigent.server.factory.initiative_view import plan_of

        ref = plan_of(row).get("opportunity")
        return ref if isinstance(ref, str) and ref in self.opportunities else None

    def goal_of(self, need: str | None) -> str | None:
        """:returns: The goal (``G-n``) *need* sits under, else ``None``."""
        row = self.opportunities.get(need) if need else None
        goal = row.goal if row is not None else None
        return goal if goal and goal in self.goals else None

    def above(self, feature: str | None, need: str | None = None) -> tuple[str, ...]:
        """:returns: *feature*, then its need and goal (or *need*'s, for a new feature)."""
        out: list[str] = [feature] if feature else []
        need = self.need_of(feature) or (need if need in self.opportunities else None)
        if need:
            out.append(need)
            if goal := self.goal_of(need):
                out.append(goal)
        return tuple(out)

    def chain(self, project: FactoryProject) -> tuple[str, ...]:
        """:returns: *project*'s slug, then its feature, need and goal (those it has)."""
        return (project.slug, *self.above(self.feature_of(project)))


def needs_rows(refs: Iterable[str]) -> tuple[bool, bool]:
    """
    :returns: ``(initiatives, opportunities)``: which rows a lineage must read
        to place the projects under *refs* (a project slug needs neither).
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
        store.list_initiatives(factory_id, limit=5000) if initiatives else (),
        store.list_opportunities(factory_id, limit=5000) if needs else (),
        goals,
    )


def lineage_of(
    initiatives: Iterable[FactoryInitiative],
    opportunities: Iterable[FactoryOpportunity],
    goals: Iterable[Any] = (),
) -> Lineage:
    """A :class:`Lineage` from rows already read (``goals``: ``strategy.goals``)."""
    from omnigent.server.factory.features import CLOSED

    return Lineage(
        initiatives=[r for r in initiatives if r.status not in CLOSED],
        opportunities={o.ref: o for o in opportunities if not o.kill and not o.merged_into},
        goals=frozenset(str(g.get("id")) for g in goals if isinstance(g, Mapping) and g.get("id")),
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
