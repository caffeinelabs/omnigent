"""Each project's home feature (``factory_projects.feature``) and what else it serves.

The cockpit's levels give every project exactly one parent on the feature
level: its *home*, the feature that planned it, pays for it and accepts it.
It counts in roll-ups only there; any other feature that lists it sees an
"also serves" link, never a second parent.

Where the home comes from, first match wins:

1. ``factory_projects.feature``, written when a projects proposal applies
   with an initiative (:func:`.initiatives.link_created`), if that
   initiative is still there and not cancelled;
2. the initiatives whose ``plan.projects`` list the project: the one that
   added it first (its wave's ``at``, then the lower number);
3. the initiative that the proposal which created the project started or
   extended (``charter.why.proposal``, else ``charter.requested_by.proposal``).

None of these: the project's feature is implicit (made from its proposal by
the node projection, never stored). :func:`backfill` writes rules 2 and 3
into the column for live work projects that have none; it is idempotent,
the reconcile loop runs it every pass, and a pass with no new homeless
project reads nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from omnigent.factory import PROJECT_KIND_WORK
from omnigent.server.factory.charter import normalize_charter, project_kind
from omnigent.server.factory.initiative_view import plan_of
from omnigent.stores.factory_store import FEATURE_REF_RE

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryInitiative, FactoryProject

Source = Literal["stored", "plan", "proposal", "none"]

CLOSED: Final = frozenset({"cancelled"})
"""Initiative statuses that are nobody's home (a done one still is)."""
_GONE: Final = frozenset({"cancelled", "merged"})
"""Project statuses that need no home (merged: folded into another project)."""


@dataclass(frozen=True)
class Home:
    """
    A project's place on the feature level.

    :param feature: Its home ``I-<n>``, or ``None`` (its feature is implicit).
    :param source: Which rule found it (see the module docstring).
    :param also_serves: Other initiatives that list it, first first.
    """

    feature: str | None
    source: Source
    also_serves: tuple[str, ...] = ()


def origin_proposal(project: FactoryProject) -> str | None:
    """:returns: The ``C-<n>`` that created *project* (its frozen why, else who asked)."""
    charter = normalize_charter(project.charter)
    for block in (charter.get("why"), charter.get("requested_by")):
        ref = block.get("proposal") if isinstance(block, Mapping) else None
        if isinstance(ref, str) and ref.startswith("C-"):
            return ref
    return None


def _listed(rows: Sequence[FactoryInitiative]) -> dict[str, list[tuple[int, int, str]]]:
    """Slug -> ``(added at, number, I-n)`` of every initiative whose plan lists it."""
    out: dict[str, list[tuple[int, int, str]]] = {}
    for row in rows:
        plan = plan_of(row)
        added: dict[str, int] = {}
        for entry in plan["history"]:
            if not isinstance(entry, Mapping):
                continue
            for slug in entry.get("added") or []:
                added.setdefault(str(slug), int(entry.get("at") or row.created_at or 0))
        for slug in dict.fromkeys(str(s) for s in plan["projects"]):
            out.setdefault(slug, []).append(
                (added.get(slug, row.created_at or 0), row.number, row.ref)
            )
    for found in out.values():
        found.sort()
    return out


def _by_proposal(rows: Sequence[FactoryInitiative]) -> dict[str, str]:
    """``C-n`` -> the initiative it started or extended (the lowest number on a tie)."""
    out: dict[str, str] = {}
    for row in sorted(rows, key=lambda r: r.number):
        refs = [row.created_by] + [
            h.get("proposal") for h in plan_of(row)["history"] if isinstance(h, Mapping)
        ]
        for ref in refs:
            if isinstance(ref, str) and ref.startswith("C-"):
                out.setdefault(ref, row.ref)
    return out


class HomeFinder:
    """Each project's :class:`Home` among *initiatives*, their lookups built once. Pure."""

    def __init__(self, initiatives: Iterable[FactoryInitiative]) -> None:
        rows = [r for r in initiatives if r.status not in CLOSED]
        self._known = {r.ref for r in rows}
        self._listed = _listed(rows)
        self._started = _by_proposal(rows)

    def __call__(self, project: FactoryProject) -> Home:
        """:returns: *project*'s home feature and the other features that list it."""
        found = [ref for _at, _n, ref in self._listed.get(project.slug, [])]
        home: str | None
        source: Source
        if project.feature and project.feature in self._known:
            home, source = project.feature, "stored"
        elif found:
            home, source = found[0], "plan"
        elif (origin := origin_proposal(project)) is not None and origin in self._started:
            home, source = self._started[origin], "proposal"
        else:
            home, source = None, "none"
        also = tuple(ref for ref in dict.fromkeys(found) if ref != home)
        return Home(home, source, also)


def homes(
    projects: Iterable[FactoryProject], initiatives: Iterable[FactoryInitiative]
) -> dict[str, Home]:
    """
    Every project's home feature and the other features that list it. Pure.

    :returns: Slug -> :class:`Home`.
    """
    find = HomeFinder(initiatives)
    return {project.slug: find(project) for project in projects}


_settled: dict[str, frozenset[tuple[str, ...]]] = {}
"""Factory id -> the projects a pass found no home for (see :func:`backfill`)."""
_SETTLED_MAX: Final = 256


def _identity(project: FactoryProject) -> tuple[str, ...]:
    """What decides a project's home from its own side: its row, its origin, its stamp."""
    stamp = normalize_charter(project.charter).get("initiative")
    ref = stamp.get("ref") if isinstance(stamp, Mapping) else None
    return (project.id, project.slug, origin_proposal(project) or "", str(ref or ""))


def backfill(
    store: Any, factory_id: str, projects: Sequence[FactoryProject] | None = None
) -> list[tuple[str, str]]:
    """
    Write the home feature of every live work project that has none stored yet (sync).

    Idempotent: a project with a stored feature is never rewritten, so a
    second run writes nothing. Only rows written before the column need it
    (a plan that lists a project writes the column itself), so a project
    found homeless stays settled: later passes skip the initiatives' read
    until a homeless project is new or its origin changes.

    :param projects: The factory's project rows, when the caller has them.
    :returns: ``(slug, I-n)`` of each row written.
    """
    rows = store.list_projects(factory_id) if projects is None else projects
    homeless = [
        p
        for p in rows
        if not p.feature and p.status not in _GONE and project_kind(p) == PROJECT_KIND_WORK
    ]
    if not homeless:
        _settled.pop(factory_id, None)
        return []
    wanted = frozenset(_identity(p) for p in homeless)
    if wanted <= _settled.get(factory_id, frozenset()):
        return []
    initiatives = store.list_initiatives(factory_id, limit=5000)
    found = homes(homeless, initiatives)
    written: list[tuple[str, str]] = []
    left: set[tuple[str, ...]] = set()
    for project in homeless:
        home = found[project.slug]
        if home.feature is None or not FEATURE_REF_RE.match(home.feature):
            left.add(_identity(project))
            continue
        if store.update_project(project.id, feature=home.feature) is not None:
            written.append((project.slug, home.feature))
    if len(_settled) >= _SETTLED_MAX:
        _settled.clear()
    _settled[factory_id] = frozenset(left)
    return written


__all__ = ["CLOSED", "Home", "HomeFinder", "Source", "backfill", "homes", "origin_proposal"]
