"""Opportunities ``O-n``: validation, the derived status, and the evidence and score behind each.

An opportunity is a user need in users' words, backed by signals. The server
computes everything else from stored facts, the way a project's stage is
derived today; people can only park, kill or merge it (:mod:`.levers`).

Status, in the order it is decided:

- ``merged`` once merged into another (it redirects there);
- ``kept`` / ``dropped`` after a readout decision (readouts write ``result``;
  "iterate" closes the measured bet instead, and the opportunity falls back
  to what its evidence says);
- ``dropped`` once killed, until its evidence rises a level above the level
  at the kill (re-proposal is blocked until then);
- ``parked`` while snoozed, or moved under a non-goal the strategy still lists;
- ``measured`` once a readout is written;
- ``shipped`` when every project an applied bet started is merged;
- ``building`` while such a project is open;
- ``proposed`` while a ``C-n`` that names it waits;
- ``validated`` once the evidence meets ``discovery.thresholds``;
- ``idea`` otherwise.

What an agent writes is checked here (:func:`create`): a statement in job
form that names no solution, at least two different solutions (a need, not
a disguised solution), ids that resolve, and quotes that match the ledger.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from omnigent.server.factory import bets, definition
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.discovery import evidence, quotes, rubric
from omnigent.server.factory.discovery.evidence import Linked, Meter
from omnigent.server.factory.discovery.ledger import INJECTION_SUSPECT
from omnigent.server.factory.discovery.rubric import Score
from omnigent.stores.factory_store import now_s
from omnigent.stores.factory_store.discovery import SignalFilter

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryProject, FactoryProposal
    from omnigent.entities.factory_discovery import (
        FactoryOpportunity,
        FactorySignal,
        FactorySignalLink,
    )
    from omnigent.server.factory.actor import Actor
    from omnigent.server.factory.context import FactoryDeps

REF_RE: Final = re.compile(r"^O-([1-9][0-9]{0,8})$")
SIGNAL_RE: Final = re.compile(r"^S-([1-9][0-9]{0,8})$")
TITLE_MAX: Final = 120
STATEMENT_MAX: Final = 200
PROBLEM_MAX: Final = 200
NOTE_FIELD_MAX: Final = 500
ALTERNATIVE_MAX: Final = 160
ALTERNATIVES_MAX: Final = 6
SIGNALS_MAX: Final = 100
STATUSES: Final = (
    "idea",
    "validated",
    "proposed",
    "building",
    "shipped",
    "measured",
    "kept",
    "dropped",
    "parked",
    "merged",
)
CLOSED: Final = frozenset({"merged", "dropped", "kept"})
"""Statuses out of the running (they take no band)."""
OPEN_PROPOSALS: Final = frozenset({"pending", "awaiting_cosign"})
APPLIED_PROPOSALS: Final = frozenset({"applied", "kept"})
SHIPPED_PROJECTS: Final = frozenset({"merged", "done"})
SHIPPED_STAGES: Final = frozenset({"merged", "done"})
"""A project still listed as active whose derived stage is one of these has shipped."""
LIFECYCLE: Final = ("idea", "validated", "proposed", "building", "shipped", "measured")
_SIGNALS_CHUNK: Final = 500
_JOB_RE: Final = re.compile(r"^\s*when\b.+\b(want|need)\b.+\bso\b", re.IGNORECASE | re.DOTALL)
_SOLUTION_VERB_RE: Final = re.compile(
    r"^\s*(please\s+)?(add|build|create|implement|integrate|introduce|make|ship|support|"
    r"enable|provide|allow|offer|launch|redesign|rewrite|refactor)\b",
    re.IGNORECASE,
)
_SOLUTION_NOUN_RE: Final = re.compile(
    r"\b(i|we|they|users?|people)\s+(want|need|wants|needs)\s+(a|an|the|some|more|new)\s+"
    r"(?:[\w-]+\s+){0,2}?(button|toggle|setting|page|screen|tab|menu|dashboard|"
    r"widget|endpoint|api|feature|integration|plugin|template|option|modal|popup|banner|"
    r"wizard|sidebar|report|export)s?\b(?!\s+(?:to|that|which|is|are|be|was|were)\b)",
    re.IGNORECASE,
)
_WORD_RE: Final = re.compile(r"[a-z0-9]+")


def parse_ref(ref: str) -> int:
    """
    :returns: The number of ``O-<n>``.
    :raises CockpitError: 404 ``unknown_opportunity`` on anything else.
    """
    match = REF_RE.match((ref or "").strip())
    if match is None:
        raise CockpitError(404, "unknown_opportunity", f"{ref!r} is no opportunity id (O-<n>)")
    return int(match.group(1))


def parse_signal(ref: str, where: str = "signal") -> int:
    """
    :returns: The number of ``S-<n>``.
    :raises CockpitError: 400 ``invalid_body``.
    """
    match = SIGNAL_RE.match((ref or "").strip())
    if match is None:
        raise CockpitError(400, "invalid_body", f"{ref!r} is no signal id (S-<n>)", field=where)
    return int(match.group(1))


def not_found(ref: str) -> CockpitError:
    """:returns: The 404 for an opportunity this factory does not have."""
    return CockpitError(
        404,
        "unknown_opportunity",
        f"{ref} is not in this factory. It may belong to another factory, or it was merged.",
    )


# ── Facts ──────────────────────────────────────────────────────


@dataclass
class Facts:
    """Everything the status, evidence and score of a factory's opportunities read."""

    factory_id: str
    now: int
    opportunities: list[FactoryOpportunity] = field(default_factory=list)
    links: dict[int, list[FactorySignalLink]] = field(default_factory=dict)
    signals: dict[int, FactorySignal] = field(default_factory=dict)
    proposals: dict[str, list[FactoryProposal]] = field(default_factory=dict)
    projects: dict[str, list[FactoryProject]] = field(default_factory=dict)

    def by_number(self, number: int) -> FactoryOpportunity | None:
        """:returns: ``O-<number>``, if loaded."""
        return next((o for o in self.opportunities if o.number == number), None)


def _signals(deps: FactoryDeps, factory_id: str, numbers: Iterable[int]) -> dict[int, Any]:
    wanted = sorted(set(numbers))
    out: dict[int, Any] = {}
    for start in range(0, len(wanted), _SIGNALS_CHUNK):
        chunk = tuple(wanted[start : start + _SIGNALS_CHUNK])
        for row in deps.factory_store.list_signals(
            factory_id, SignalFilter(numbers=chunk), limit=len(chunk)
        ):
            out[row.number] = row
    return out


def load_facts(
    deps: FactoryDeps,
    factory_id: str,
    *,
    numbers: Sequence[int] | None = None,
    now: int | None = None,
) -> Facts:
    """
    Load the facts behind the factory's opportunities (synchronous; run in a thread).

    :param numbers: Only these opportunities (and what they cite); all when ``None``.
    """
    store = deps.factory_store
    if numbers is None:
        rows = store.list_opportunities(factory_id)
    else:
        found = (store.get_opportunity(factory_id, n) for n in dict.fromkeys(numbers))
        rows = [r for r in found if r is not None]
    links = store.list_links(factory_id, opportunities=[r.number for r in rows])
    by_opportunity: dict[int, list[Any]] = {}
    for link in links:
        by_opportunity.setdefault(link.opportunity, []).append(link)
    refs = {r.ref for r in rows}
    proposals: dict[str, list[Any]] = {}
    # Only agents' proposals answer an opportunity; people's charter edits are skipped.
    for proposal in store.list_proposals(
        factory_id, kind="projects", limit=1000, author_kind="agent"
    ):
        if proposal.opportunity in refs:
            proposals.setdefault(str(proposal.opportunity), []).append(proposal)
    projects: dict[str, list[Any]] = {}
    for project in store.list_projects(factory_id):
        why = (project.charter or {}).get("why")
        ref = why.get("opportunity") if isinstance(why, Mapping) else None
        if ref in refs:
            projects.setdefault(str(ref), []).append(project)
    return Facts(
        factory_id=factory_id,
        now=now if now is not None else now_s(),
        opportunities=rows,
        links=by_opportunity,
        signals=_signals(deps, factory_id, (link.signal for link in links)),
        proposals=proposals,
        projects=projects,
    )


@dataclass(frozen=True)
class Evaluated:
    """
    One opportunity with what the server computes for it.

    :param open: Still in the running (not parked, merged, dropped or kept).
    :param band: ``now`` / ``next`` / ``later``, ``parked``, or ``None`` when closed.
    """

    row: FactoryOpportunity
    meter: Meter
    score: Score
    status: str
    reason: str
    linked: tuple[Linked, ...] = ()
    proposals: tuple[FactoryProposal, ...] = ()
    projects: tuple[FactoryProject, ...] = ()

    @property
    def open(self) -> bool:
        """:returns: Whether it is still in the running."""
        return self.status not in CLOSED and self.status != "parked"

    @property
    def band(self) -> str | None:
        """:returns: Its band in the table (``parked``; ``None`` once closed)."""
        if self.status in CLOSED:
            return None
        if self.status == "parked":
            return "parked"
        return self.score.band


def _parked(row: FactoryOpportunity, strategy: Mapping[str, Any], now: int) -> str | None:
    if row.non_goal and row.non_goal in [str(n) for n in strategy.get("non_goals") or []]:
        return f'under the non-goal "{row.non_goal}"'
    park = row.park or {}
    if park:
        until = park.get("until")
        if until is None or (isinstance(until, int) and until > now):
            why = park.get("reason") or "snoozed"
            return f"{why} (by {park.get('by_name') or park.get('by') or 'someone'})"
    return None


def project_shipped(project: FactoryProject) -> bool:
    """:returns: Whether *project*'s work is out: closed as merged or done, or every PR merged."""
    if project.status == "cancelled":
        return False
    return project.status in SHIPPED_PROJECTS or project.stage in SHIPPED_STAGES


def iterated(result: Mapping[str, Any] | None) -> frozenset[str]:
    """:returns: The bets (``C-n``) an "iterate" after their readout closed."""
    found = (result or {}).get("iterated")
    return frozenset(str(ref) for ref in found) if isinstance(found, list) else frozenset()


def kill_holds(row: FactoryOpportunity, meter: Meter) -> bool:
    """:returns: Whether a kill still holds (its evidence has not risen a level since)."""
    kill = row.kill or {}
    if not kill:
        return False
    return evidence.level_index(meter.level) <= evidence.level_index(kill.get("level"))


def derive_status(
    row: FactoryOpportunity,
    meter: Meter,
    *,
    thresholds: Mapping[str, Any],
    strategy: Mapping[str, Any],
    proposals: Sequence[FactoryProposal],
    projects: Sequence[FactoryProject],
    now: int,
) -> tuple[str, str]:
    """:returns: ``(status, why)`` from the stored facts (see the module doc)."""
    if row.merged_into is not None:
        return "merged", f"merged into O-{row.merged_into}"
    result = row.result or {}
    if result.get("decision") == "keep":
        return "kept", "kept after its readout"
    if result.get("decision") == "drop":
        return "dropped", "dropped after its readout"
    if kill_holds(row, meter):
        kill = row.kill or {}
        return "dropped", f"killed: {kill.get('reason')}"
    parked = _parked(row, strategy, now)
    if parked is not None:
        return "parked", parked
    closed = iterated(result)
    if closed:
        # Iterating closes the measured bet: its proposal and projects no longer speak for it.
        proposals = [p for p in proposals if p.ref not in closed]
        projects = [p for p in projects if _bet_of(p) not in closed]
    if result.get("measured_at") and result.get("decision") != "iterate":
        return "measured", "a readout is in"
    live = [p for p in projects if p.status != "cancelled"]
    if live:
        if all(project_shipped(p) for p in live):
            return "shipped", f"{len(live)} project{'s' if len(live) != 1 else ''} merged"
        open_slugs = ", ".join(f"#{p.slug}" for p in live if not project_shipped(p))
        return "building", f"building {open_slugs}"
    waiting = [p for p in proposals if p.status in OPEN_PROPOSALS]
    if waiting:
        return "proposed", f"{waiting[0].ref} waits"
    minimum = str(thresholds.get("validated_min_level") or "moderate")
    accounts = int(thresholds.get("min_accounts") or 0)
    if evidence.at_least(meter.level, minimum) and meter.accounts >= accounts:
        return "validated", f"{meter.level} evidence from {meter.accounts} accounts"
    return "idea", f"{meter.level} evidence; validated needs {minimum}"


def _bet_of(project: FactoryProject) -> str | None:
    why = (project.charter or {}).get("why")
    return str(why.get("proposal")) if isinstance(why, Mapping) and why.get("proposal") else None


def _linked(facts: Facts, number: int) -> list[Linked]:
    out: list[Linked] = []
    for link in facts.links.get(number, []):
        signal = facts.signals.get(link.signal)
        if signal is not None:
            out.append(Linked(signal=signal, counter=link.counter))
    return out


def evaluate(facts: Facts, row: FactoryOpportunity, doc: Mapping[str, Any]) -> Evaluated:
    """
    Evidence, score and status of one opportunity under definition *doc* (pure).

    :param doc: The effective definition (``definition.effective``).
    """
    discovery = doc.get("discovery") or {}
    strategy = doc.get("strategy") or {}
    linked = _linked(facts, row.number)
    meter = evidence.measure(linked, discovery.get("rubric"), now=facts.now)
    score = rubric.score(
        row,
        meter,
        linked,
        strategy=strategy,
        rubric=discovery.get("rubric"),
        now=facts.now,
    )
    proposals = facts.proposals.get(row.ref, [])
    projects = facts.projects.get(row.ref, [])
    status, reason = derive_status(
        row,
        meter,
        thresholds=discovery.get("thresholds") or {},
        strategy=strategy,
        proposals=proposals,
        projects=projects,
        now=facts.now,
    )
    return Evaluated(
        row=row,
        meter=meter,
        score=score,
        status=status,
        reason=reason,
        linked=tuple(linked),
        proposals=tuple(proposals),
        projects=tuple(projects),
    )


def evaluate_all(facts: Facts, doc: Mapping[str, Any]) -> list[Evaluated]:
    """:returns: Every loaded opportunity evaluated under *doc*, highest rank first."""
    out = [evaluate(facts, row, doc) for row in facts.opportunities]
    out.sort(key=lambda e: (-round(e.score.rank, 4), e.row.number))
    return out


def effective_doc(deps: FactoryDeps, factory_id: str) -> tuple[dict[str, Any], int | None]:
    """:returns: ``(effective definition, version)`` in force for the factory."""
    resolved = definition.definition_for(deps, factory_id)
    if resolved is None:
        return definition.effective({}), None
    return resolved.doc, resolved.version


def evaluate_one(deps: FactoryDeps, factory_id: str, number: int) -> Evaluated | None:
    """:returns: ``O-<number>`` evaluated now (synchronous), or ``None`` when unknown."""
    facts = load_facts(deps, factory_id, numbers=[number])
    row = facts.by_number(number)
    if row is None:
        return None
    doc, _version = effective_doc(deps, factory_id)
    return evaluate(facts, row, doc)


# ── Hooks for bets (:mod:`omnigent.server.factory.bets`) ───────


def lookup(deps: FactoryDeps, factory_id: str, ref: str) -> bets.OpportunityInfo | None:
    """The bets hook: ``O-n`` with its derived status (``None`` while discovery is off)."""
    if not definition.discovery_enabled(deps, factory_id):
        return None
    match = REF_RE.match(ref.strip())
    if match is None:
        return None
    found = evaluate_one(deps, factory_id, int(match.group(1)))
    if found is None:
        return None
    return bets.OpportunityInfo(
        id=found.row.ref, status=found.status, goal=found.row.goal, title=found.row.title
    )


def record_decline(deps: FactoryDeps, factory_id: str, ref: str, reason: str, by: str) -> None:
    """The bets hook: keep a lead's decline on the opportunity (the last 20)."""
    match = REF_RE.match(ref.strip())
    if match is None:
        return
    number = int(match.group(1))
    row = deps.factory_store.get_opportunity(factory_id, number)
    if row is None:
        return
    declines = [*row.declines, {"reason": reason[:300], "by": by[:128], "at": now_s()}][-20:]
    deps.factory_store.update_opportunity(factory_id, number, declines=declines)


# ── Writing ────────────────────────────────────────────────────


def solution_named(statement: str) -> str | None:
    """
    :returns: Why *statement* names a solution instead of a need, or ``None``.

    "Add a share button" and "I want a share page" are solutions; "I want to
    show it to someone before it goes live" is a need.
    """
    if _SOLUTION_VERB_RE.match(statement):
        return "it starts with what to build; say what people try to do"
    match = _SOLUTION_NOUN_RE.search(statement)
    if match is not None:
        return f"it asks for a {match.group(4).lower()}; say the need behind it"
    return None


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.casefold()))


def distinct_alternatives(raw: Sequence[str]) -> list[str]:
    """
    At least two different solutions to the need ("more than one solution").

    :raises CockpitError: 422 ``one_solution`` when fewer than two differ.
    """
    out: list[str] = []
    for alt in raw:
        flat = " ".join(str(alt).split())
        if not flat:
            continue
        words = _words(flat)
        near = any(len(words & _words(o)) / max(len(words | _words(o)), 1) >= 0.8 for o in out)
        if not near:
            out.append(flat[:ALTERNATIVE_MAX])
    if len(out) < 2:
        raise CockpitError(
            422,
            "one_solution",
            "name at least two different solutions: an opportunity is a need that more "
            "than one solution could meet",
            field="alternatives",
        )
    return out[:ALTERNATIVES_MAX]


class SignalRef(BaseModel):
    """A signal an opportunity cites."""

    model_config = ConfigDict(extra="forbid")

    signal: str
    counter: bool = False
    confidence: int | None = Field(None, ge=0, le=100)


_NoteText = Annotated[str, StringConstraints(min_length=1, max_length=NOTE_FIELD_MAX)]


class BetNote(BaseModel):
    """Scout's draft of a bet: solution ideas, the riskiest assumption, the cheapest test."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    solutions: list[_NoteText] = Field(default_factory=list, max_length=ALTERNATIVES_MAX)
    riskiest_assumption: _NoteText | None = None
    cheapest_test: _NoteText | None = None
    metric: str | None = Field(None, min_length=1, max_length=200)


class NewOpportunity(BaseModel):
    """What creating an opportunity takes (an agent's write, or a person's split)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    statement: str = Field(min_length=1, max_length=STATEMENT_MAX)
    problem: str | None = Field(None, max_length=PROBLEM_MAX)
    goal: str | None = Field(None, max_length=16)
    segments: list[str] = Field(default_factory=list, max_length=10)
    area: str | None = Field(None, max_length=32)
    alternatives: list[str] = Field(default_factory=list, max_length=ALTERNATIVES_MAX * 2)
    signals: list[SignalRef] = Field(default_factory=list, max_length=SIGNALS_MAX)
    bet_note: BetNote | None = None


def _profile_ids(doc: Mapping[str, Any], key: str) -> set[str]:
    product = doc.get("product") or {}
    return {str(i.get("id")) for i in product.get(key) or [] if isinstance(i, Mapping)}


def check_placement(
    doc: Mapping[str, Any], *, goal: str | None, segments: Sequence[str], area: str | None
) -> None:
    """
    The goal, segments and area must be the strategy's and the profile's.

    :raises CockpitError: 422 ``unknown_goal`` / ``unknown_segment`` / ``unknown_area``.
    """
    goals = {str(g.get("id")) for g in (doc.get("strategy") or {}).get("goals") or []}
    if goal is not None and goal not in goals:
        raise CockpitError(422, "unknown_goal", f"the strategy has no goal {goal}", field="goal")
    known = _profile_ids(doc, "segments")
    bad = [s for s in segments if s not in known]
    if bad:
        raise CockpitError(
            422, "unknown_segment", f"the profile has no segment {bad[0]}", field="segments"
        )
    if area is not None and area not in _profile_ids(doc, "areas"):
        raise CockpitError(422, "unknown_area", f"the profile has no area {area}", field="area")


def check_signals(
    deps: FactoryDeps, factory_id: str, refs: Sequence[SignalRef]
) -> list[tuple[int, SignalRef]]:
    """
    Every cited signal must resolve, and none may be a suspected injection.

    :raises CockpitError: 404 ``unknown_signal``; 422 ``injection_suspect``.
    """
    out: list[tuple[int, SignalRef]] = []
    for i, ref in enumerate(refs):
        number = parse_signal(ref.signal, f"signals[{i}].signal")
        row = deps.factory_store.get_signal(factory_id, number)
        if row is None:
            raise CockpitError(
                404, "unknown_signal", f"no signal {ref.signal} in this factory", field="signals"
            )
        if INJECTION_SUSPECT in row.flags:
            raise CockpitError(
                422,
                "injection_suspect",
                f"{ref.signal} reads like instructions and is kept out of scoring",
                field="signals",
            )
        out.append((number, ref))
    return out


def _strings(value: Any, where: str) -> Iterable[tuple[str, str]]:
    """Every string in *value*, at any depth, with its path."""
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, Mapping):
        for key, inner in value.items():
            yield from _strings(inner, f"{where}.{key}")
    elif isinstance(value, list | tuple):
        for i, inner in enumerate(value):
            yield from _strings(inner, f"{where}[{i}]")


def _agent_texts(body: NewOpportunity) -> list[tuple[str, str | None]]:
    texts: list[tuple[str, str | None]] = [
        ("title", body.title),
        ("statement", body.statement),
        ("problem", body.problem),
    ]
    texts += [(f"alternatives[{i}]", a) for i, a in enumerate(body.alternatives)]
    if body.bet_note is not None:
        texts += list(_strings(body.bet_note.model_dump(exclude_none=True), "bet_note"))
    return texts


def create(
    deps: FactoryDeps,
    factory_id: str,
    payload: Mapping[str, Any],
    *,
    actor: Actor,
    origin: Mapping[str, Any] | None = None,
    split_from: int | None = None,
) -> FactoryOpportunity:
    """
    Validate and store a new opportunity, attaching the signals it cites (synchronous).

    An agent's opportunity is held to the full checks: job form, no named
    solution, two different solutions, and verified quotes (citing the
    signals it lists). A person's (a split) needs only a statement.

    :raises CockpitError: 400 ``invalid_body``; 404 ``unknown_signal``; 422
        ``not_job_form`` / ``names_a_solution`` / ``one_solution`` /
        ``unknown_goal`` / ``unknown_segment`` / ``unknown_area`` /
        ``injection_suspect`` / ``quote_unverified``.
    """
    from omnigent.server.factory.start import parse_body

    body = parse_body(NewOpportunity, payload)
    doc, _version = effective_doc(deps, factory_id)
    check_placement(doc, goal=body.goal, segments=body.segments, area=body.area)
    cited = check_signals(deps, factory_id, body.signals)
    alternatives = list(body.alternatives)
    if actor.kind == "agent":
        if not _JOB_RE.match(body.statement):
            raise CockpitError(
                422,
                "not_job_form",
                'say the need in job form: "When I …, I want …, so I can …"',
                field="statement",
            )
        why = solution_named(body.statement)
        if why is not None:
            raise CockpitError(422, "names_a_solution", why, field="statement")
        alternatives = distinct_alternatives(body.alternatives)
        refs = [f"S-{n}" for n, _ in cited]
        quotes.check(deps, factory_id, _agent_texts(body), cited=refs)
    store = deps.factory_store
    row = store.create_opportunity(
        factory_id,
        title=body.title,
        statement=body.statement,
        created_by=actor.id,
        created_kind=actor.kind,
        problem=body.problem,
        goal=body.goal,
        segments=list(dict.fromkeys(body.segments)),
        area=body.area,
        alternatives=alternatives,
        bet_note=body.bet_note.model_dump(exclude_none=True) if body.bet_note else None,
        origin=dict(origin) if origin else None,
        split_from=split_from,
    )
    for number, ref in cited:
        store.put_link(
            factory_id,
            number,
            row.number,
            attached_by=actor.id,
            attached_kind=actor.kind,
            counter=ref.counter,
            confidence=ref.confidence,
        )
    return row


def snapshot(
    deps: FactoryDeps,
    factory_id: str,
    items: Sequence[Evaluated],
    *,
    reason: str,
    def_version: int | None,
    at: int | None = None,
) -> int:
    """Store the score of each of *items* as of now, marked *reason*. :returns: How many."""
    when = at if at is not None else now_s()
    rows = [
        {
            "opportunity": e.row.number,
            "at": when,
            "reason": reason,
            "def_version": def_version,
            "rank": round(e.score.rank, 4),
            "band": e.band or e.status,
            "level": e.meter.level,
            "inputs": {**e.score.inputs, "status": e.status},
            "parts": {p.name: round(p.value, 4) for p in e.score.parts},
        }
        for e in items
    ]
    return deps.factory_store.add_score_snapshots(factory_id, rows)


def snapshot_all(
    deps: FactoryDeps, factory_id: str, *, reason: str = "daily", at: int | None = None
) -> int:
    """Snapshot every open opportunity's score (a daily pass calls this). :returns: How many."""
    facts = load_facts(deps, factory_id, now=at)
    doc, version = effective_doc(deps, factory_id)
    items = [e for e in evaluate_all(facts, doc) if e.status not in CLOSED]
    return snapshot(deps, factory_id, items, reason=reason, def_version=version, at=facts.now)


__all__ = [
    "APPLIED_PROPOSALS",
    "CLOSED",
    "LIFECYCLE",
    "OPEN_PROPOSALS",
    "REF_RE",
    "SIGNAL_RE",
    "STATUSES",
    "BetNote",
    "Evaluated",
    "Facts",
    "NewOpportunity",
    "SignalRef",
    "check_placement",
    "check_signals",
    "create",
    "derive_status",
    "distinct_alternatives",
    "effective_doc",
    "evaluate",
    "evaluate_all",
    "evaluate_one",
    "iterated",
    "kill_holds",
    "load_facts",
    "lookup",
    "not_found",
    "parse_ref",
    "parse_signal",
    "project_shipped",
    "record_decline",
    "snapshot",
    "snapshot_all",
    "solution_named",
]
