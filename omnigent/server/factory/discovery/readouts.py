"""Readouts: the server measures every bet after its release and writes the result back.

A bet is a ``C-n`` projects proposal whose plan was frozen into the charters
of the projects it started (:mod:`..bets`). Once those projects are out, the
server notices the release the way the factory's profile says
(``product.delivery.release``: ``merged``, ``github_release``,
``github_deployment:<env>`` or ``manual``) and schedules one readout per
readout day of the plan (7 and 28 by default), counted from the release. On
each day it measures the bet with the plan's method, and fixed rules
(:func:`judge`, Tier 0, never prompt text) set the verdict and the
recommendation:

============  =======================================================  ==============
verdict       when                                                     recommendation
============  =======================================================  ==============
won           moved the promised way by at least the near end of the   keep
              predicted range, with no guardrail broken
flat          moved less than that; a move the wrong way smaller than  iterate
              the near end is noise
lost          moved the wrong way by the near end or more, or a        roll_back
              guardrail broke
inconclusive  too little data (n under the plan's minimum), or a data  extend (once;
              check failed                                             then iterate)
============  =======================================================  ==============

The near end is the end of the signed range closest to zero in the promised
direction (``low`` going up, ``high`` going down; zero when the range spans
it). ``predicted_vs_actual`` says whether the effect fell inside the range.

**Methods** (``mechanism.md`` §7.2). ``signal_trend`` needs nothing extra:
the distinct accounts behind the opportunity's linked user signals in the
readout's window after the release against the same window before it, in the
plan's unit (a relative change for ``share``/``%``, a weekly change for a
count per week). ``human_verdict`` waits for a person's answer to "did this
help?" with a reason. ``before_after``, ``adoption`` and ``controlled`` are
hooks (:func:`register_method`): until a source can measure them, their
readouts are inconclusive and say so. Guardrails no source measures yet are
``unmeasured``; a person may report one broken, which makes the readout lost.

**Write-back** goes through the ledger only: each written readout stores a
``test_result`` signal of the server's own source (its verdict, method and
the evidence level the method can show), links it to the opportunity, and
marks the opportunity measured; the prediction ledger
(:func:`prediction_ledger`, metric definitions in ``telemetry/metrics.py``)
scores the bet against its predicted range, stamped with the evidence level
and the rubric version at filing.

**A person decides** (``POST …/readouts/{id}/decide``) keep, drop, iterate
or extend, once a readout is written; the last readout of a bet, and any lost
one, opens a "Needs you" decision whose one-click answers follow the
recommendation. Going against the recommendation needs an owner, a named
reviewer and a reason, all shown on the readout. Keep and drop close the
opportunity (kept, dropped); iterate closes the bet and puts the opportunity
back to what its evidence says; extend adds one later readout, once.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from omnigent.factory import LABEL_ROLE, THREAD_FACTORY, thread_kind
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE
from omnigent.server.factory import control, definition, events, people
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.discovery import evidence, ledger, opportunities
from omnigent.server.factory.discovery.ledger import CONVICTION_KINDS, INJECTION_SUSPECT
from omnigent.stores.factory_store import now_s
from omnigent.stores.factory_store.discovery import SignalFilter

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryEvent, FactoryProject, FactoryProposal
    from omnigent.entities.factory_discovery import FactoryReadout

_logger = logging.getLogger(__name__)

DAY_S: Final = 86400
WEEK_DAYS: Final = 7
EVAL_DAY_S: Final = 60
"""A readout day in an eval-mode factory: readouts land minutes after the release."""
DEFAULT_DAYS: Final = (7, 28)
MAX_DAY: Final = 365
SIGNAL_TREND_MIN_N: Final = 5
"""Distinct accounts (before and after together) a signal trend needs unless the plan says."""
RELEASE_POLL_S: Final = 900
"""How often one bet's GitHub release or deployment is looked up."""
SUBJECT: Final = "readout:"
DECISION_SOURCE: Final = "readout"
REASON_MAX: Final = 300
REVIEWER_MAX: Final = 128
GUARDRAILS_MAX: Final = 10
SERVER_BY: Final = "server"

MVP_METHODS: Final = ("signal_trend", "human_verdict")
METHOD_LABEL: Final[dict[str, str]] = {
    "signal_trend": "signal trend",
    "human_verdict": "a person's verdict",
    "before_after": "before and after",
    "adoption": "adoption",
    "controlled": "controlled test",
}
REACHES: Final[dict[str, str]] = {
    "controlled": "proven",
    "before_after": "strong",
    "adoption": "strong",
    "signal_trend": "moderate",
    "human_verdict": "weak",
}
"""The evidence level a won readout can show, by method (``mechanism.md`` §7.2)."""
VERDICTS: Final = ("won", "flat", "lost", "inconclusive")
RECOMMENDATIONS: Final = ("keep", "iterate", "roll_back", "extend")
DECISIONS: Final = ("keep", "drop", "iterate", "extend")
ANSWERS: Final[dict[str, str]] = {"helped": "won", "no_change": "flat", "hurt": "lost"}
"""A person's "did this help?" and the verdict it sets."""
ALIGNED: Final[dict[str, tuple[str, ...]]] = {
    "keep": ("keep",),
    "iterate": ("iterate", "drop"),
    "roll_back": ("drop", "iterate"),
    "extend": ("extend",),
}
"""Decisions that follow each recommendation (the first is the recommended one)."""
DECISION_LABEL: Final[dict[str, str]] = {
    "keep": "Keep it",
    "drop": "Drop it",
    "iterate": "Iterate",
    "extend": "Extend once",
}
OPEN_STATUSES: Final = frozenset({"scheduled", "awaiting_answer"})
CLOSING: Final = frozenset({"keep", "drop", "iterate"})
"""Decisions that settle the bet (its later readouts are cancelled)."""
_RELATIVE: Final[dict[str, float]] = {
    "share": 1.0,
    "ratio": 1.0,
    "fraction": 1.0,
    "%": 100.0,
    "percent": 100.0,
    "pct": 100.0,
}
_PER_WEEK_WORDS: Final = ("week", "/w", "signal", "account", "count")
_SKIP_KINDS: Final = frozenset({*CONVICTION_KINDS, "simulation", "test_result", "behavior"})
_last_poll: dict[tuple[str, int], float] = {}


# ── The fixed rules ────────────────────────────────────────────


@dataclass(frozen=True)
class Judgement:
    """
    A readout's verdict and what to do about it.

    :param predicted: ``inside`` / ``below`` / ``above`` the predicted range, or
        ``None`` when the method gives no number.
    """

    verdict: str
    recommendation: str
    predicted: str | None
    why: str


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def near_end(expected: Mapping[str, Any]) -> float:
    """:returns: How far the plan promised to move, at least (≥ 0, in the plan's unit)."""
    low, high = _number(expected.get("low")), _number(expected.get("high"))
    if low is None or high is None:
        return 0.0
    edge = low if expected.get("direction") == "up" else -high
    return max(edge, 0.0)


def predicted_vs_actual(expected: Mapping[str, Any], effect: float | None) -> str | None:
    """:returns: ``inside``, ``below`` or ``above`` the signed range, or ``None``."""
    low, high = _number(expected.get("low")), _number(expected.get("high"))
    if effect is None or low is None or high is None:
        return None
    if effect < low:
        return "below"
    if effect > high:
        return "above"
    return "inside"


def judge(
    *,
    expected: Mapping[str, Any],
    effect: float | None,
    n: int,
    min_n: int,
    guardrails: Sequence[Mapping[str, Any]] = (),
    checks: Sequence[Mapping[str, Any]] = (),
    answer: str | None = None,
    extended: bool = False,
) -> Judgement:
    """
    The verdict and recommendation of one readout (pure; Tier 0).

    :param expected: The plan's ``{direction, low, high, unit}`` (a signed change).
    :param effect: The measured change in the plan's unit, or ``None``.
    :param guardrails: ``[{name, state: held | broken | unmeasured}]``.
    :param checks: ``[{name, ok, note}]``; a failed one makes it inconclusive.
    :param answer: A person's ``helped`` / ``no_change`` / ``hurt`` (``human_verdict``).
    :param extended: The bet was extended once already (inconclusive then recommends iterate).
    """
    predicted = predicted_vs_actual(expected, effect)
    broken = [str(g.get("name")) for g in guardrails if g.get("state") == "broken"]
    if broken:
        return Judgement("lost", "roll_back", predicted, f"guardrail broke: {', '.join(broken)}")
    unsure = "iterate" if extended else "extend"
    failed = [c for c in checks if not c.get("ok")]
    if failed:
        return Judgement("inconclusive", unsure, None, str(failed[0].get("note") or "a check"))
    if answer is not None:
        verdict = ANSWERS[answer]
        rec = {"won": "keep", "flat": "iterate", "lost": "roll_back"}[verdict]
        return Judgement(verdict, rec, None, f"a person answered: {answer.replace('_', ' ')}")
    if effect is None:
        return Judgement("inconclusive", unsure, None, "nothing was measured")
    if n < min_n:
        return Judgement("inconclusive", unsure, predicted, f"too little data: n={n} < {min_n}")
    sign = 1.0 if expected.get("direction") == "up" else -1.0
    moved = sign * effect
    near = near_end(expected)
    if moved > 0 and moved >= near:
        return Judgement(
            "won", "keep", predicted, "moved the promised way by the near end or more"
        )
    if moved < 0 and -moved >= near:
        return Judgement("lost", "roll_back", predicted, "moved the wrong way")
    return Judgement("flat", "iterate", predicted, "moved less than the near end of the range")


def aligned(recommendation: str | None, decision: str) -> bool:
    """:returns: Whether *decision* follows *recommendation*."""
    return decision in ALIGNED.get(recommendation or "", ())


# ── Methods ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Measured:
    """
    What a method measured for one readout.

    :param pending: It waits for a person (``human_verdict`` before the answer).
    :param guardrails: ``{name: held | broken}`` for the guardrails it could read.
    """

    effect: float | None = None
    baseline: float | None = None
    after: float | None = None
    unit: str | None = None
    n: int = 0
    min_n: int = 1
    interval: tuple[float, float] | None = None
    checks: tuple[dict[str, Any], ...] = ()
    guardrails: Mapping[str, str] = field(default_factory=dict)
    inputs: Mapping[str, Any] = field(default_factory=dict)
    pending: bool = False


@dataclass(frozen=True)
class MethodContext:
    """What a method reads: the readout, its frozen plan, and the readout day's length."""

    deps: FactoryDeps
    factory_id: str
    readout: FactoryReadout
    plan: Mapping[str, Any]
    day_s: int
    now: int


Method = Callable[[MethodContext], Measured]
GuardrailCheck = Callable[[MethodContext, Sequence[str]], Mapping[str, str]]


def _check(name: str, note: str) -> dict[str, Any]:
    return {"name": name, "ok": False, "note": note}


def _unit_scale(unit: str) -> tuple[str, float] | None:
    """:returns: ``("relative", scale)`` or ``("weekly", 1)`` for a unit a signal trend reads."""
    flat = unit.strip().casefold()
    if flat in _RELATIVE:
        return "relative", _RELATIVE[flat]
    if any(word in flat for word in _PER_WEEK_WORDS):
        return "weekly", 1.0
    return None


def _accounts(deps: FactoryDeps, factory_id: str, opportunity: int) -> list[tuple[int, str]]:
    """``(seen_at, account)`` of the user signals linked to *opportunity* (not against it)."""
    links = [
        link
        for link in deps.factory_store.list_links(factory_id, opportunities=[opportunity])
        if not link.counter
    ]
    out: list[tuple[int, str]] = []
    numbers = sorted({link.signal for link in links})
    for start in range(0, len(numbers), 500):
        chunk = tuple(numbers[start : start + 500])
        for signal in deps.factory_store.list_signals(
            factory_id, SignalFilter(numbers=chunk), limit=len(chunk)
        ):
            if signal.kind in _SKIP_KINDS or signal.source_kind == ledger.SERVER_REF.kind:
                continue
            if INJECTION_SUSPECT in signal.flags:
                continue
            out.append((signal.seen_at, evidence.account_key(signal)))
    return out


def signal_trend(ctx: MethodContext) -> Measured:
    """
    The opportunity's linked user signals after the release against before it.

    Each window is the readout's length (``day`` readout days); an account
    counts once per window, so one loud user is one voice. The effect is in the
    plan's unit: a relative change (``share``, ``%``) or a change per week.
    """
    row = ctx.readout
    expected = ctx.plan.get("expected") or {}
    unit = str(expected.get("unit") or "")
    measurement = ctx.plan.get("measurement") or {}
    min_n = int(measurement.get("min_n") or SIGNAL_TREND_MIN_N)
    if row.opportunity is None:
        return Measured(
            unit=unit,
            min_n=min_n,
            checks=(_check("opportunity", "a signal trend reads the bet's opportunity; none"),),
        )
    scale = _unit_scale(unit)
    if scale is None:
        return Measured(
            unit=unit,
            min_n=min_n,
            checks=(
                _check(
                    "unit",
                    f"a signal trend measures a relative change (share, %) or a count per "
                    f"week, not {unit or 'no unit'}",
                ),
            ),
        )
    span = row.day * ctx.day_s
    released = row.released_at
    before: set[str] = set()
    after: set[str] = set()
    for seen, account in _accounts(ctx.deps, ctx.factory_id, row.opportunity):
        if released - span <= seen < released:
            before.add(account)
        elif released <= seen < released + span:
            after.add(account)
    weeks = span / (WEEK_DAYS * ctx.day_s)
    n = len(before) + len(after)
    inputs = {
        "accounts_before": len(before),
        "accounts_after": len(after),
        "window_days": row.day,
        "before": [released - span, released],
        "after": [released, released + span],
    }
    kind, factor = scale
    if kind == "relative":
        if not before:
            return Measured(
                unit=unit,
                n=n,
                min_n=min_n,
                inputs=inputs,
                checks=(
                    _check(
                        "baseline",
                        f"no signals in the {row.day} days before the release: a relative "
                        "change needs a baseline",
                    ),
                ),
            )
        effect = (len(after) - len(before)) / len(before) * factor
        return Measured(
            effect=round(effect, 4),
            baseline=float(len(before)),
            after=float(len(after)),
            unit=unit,
            n=n,
            min_n=min_n,
            inputs=inputs,
        )
    return Measured(
        effect=round((len(after) - len(before)) / weeks, 4),
        baseline=round(len(before) / weeks, 4),
        after=round(len(after) / weeks, 4),
        unit=unit,
        n=n,
        min_n=min_n,
        inputs=inputs,
    )


def human_verdict(ctx: MethodContext) -> Measured:
    """A person's "did this help?": pending until they answer, then n is one person."""
    answer = ctx.readout.answer or {}
    if not answer.get("answer"):
        return Measured(pending=True, unit=(ctx.plan.get("expected") or {}).get("unit"))
    return Measured(n=1, min_n=1, inputs={"answer": answer.get("answer")})


def _unavailable(method: str) -> Method:
    def run(ctx: MethodContext) -> Measured:
        return Measured(
            unit=str((ctx.plan.get("expected") or {}).get("unit") or ""),
            checks=(
                _check(
                    "method",
                    f"{METHOD_LABEL.get(method, method)} needs a metric binding a source can "
                    "read; none is connected yet",
                ),
            ),
        )

    return run


_methods: dict[str, Method] = {"signal_trend": signal_trend, "human_verdict": human_verdict}
_guardrail_checks: list[GuardrailCheck] = []


def register_method(name: str, method: Method | None) -> None:
    """Measure *name* (``before_after``, ``adoption``, ``controlled``); ``None`` unhooks it."""
    if method is None:
        if name not in MVP_METHODS:
            _methods.pop(name, None)
        return
    _methods[name] = method


def register_guardrail_check(check: GuardrailCheck | None) -> None:
    """Read guardrails with *check* (``{name: held | broken}``); ``None`` removes every check."""
    if check is None:
        _guardrail_checks.clear()
    else:
        _guardrail_checks.append(check)


def method_for(name: str) -> Method:
    """:returns: The method that measures *name* (an unavailable one says so)."""
    return _methods.get(name) or _unavailable(name)


def guardrail_rows(
    ctx: MethodContext, measured: Measured, reported: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """
    One entry per guardrail of the plan: ``held`` / ``broken`` from a source or a
    person's report, else ``unmeasured``.
    """
    names = [str(g) for g in ctx.plan.get("guardrails") or [] if g]
    states: dict[str, str] = dict(measured.guardrails)
    for check in _guardrail_checks:
        try:
            states.update(check(ctx, names))
        except Exception:  # noqa: BLE001 - one failing reader leaves the guardrail unmeasured
            _logger.warning("factory readouts: a guardrail check failed", exc_info=True)
    out = []
    for name in names:
        report = reported.get(name) if isinstance(reported, Mapping) else None
        if isinstance(report, Mapping):
            out.append(
                {
                    "name": name,
                    "state": "broken",
                    "note": f"reported by {report.get('by')}",
                    "by": report.get("by"),
                }
            )
            continue
        state = states.get(name)
        out.append(
            {
                "name": name,
                "state": state if state in ("held", "broken") else "unmeasured",
                "note": None if state in ("held", "broken") else "no source measures it yet",
            }
        )
    return out


# ── Bets and releases ──────────────────────────────────────────


@dataclass(frozen=True)
class Bet:
    """
    An applied bet: its proposal, the projects it started, and the plan frozen in them.

    :param plan: The frozen ``charter.why`` (the newest plan version among its projects).
    """

    proposal: FactoryProposal
    projects: tuple[FactoryProject, ...]
    plan: Mapping[str, Any]

    @property
    def ref(self) -> str:
        """:returns: e.g. ``"C-31"``."""
        return self.proposal.ref

    @property
    def opportunity(self) -> int | None:
        """:returns: The opportunity's number, if the bet answers one."""
        ref = self.plan.get("opportunity") or self.proposal.opportunity
        match = opportunities.REF_RE.match(str(ref or ""))
        return int(match.group(1)) if match else None

    @property
    def live(self) -> list[FactoryProject]:
        """:returns: Its projects that were not cancelled."""
        return [p for p in self.projects if p.status != "cancelled"]

    @property
    def days(self) -> list[int]:
        """:returns: Its readout days, earliest first."""
        raw = (self.plan.get("measurement") or {}).get("readout_days") or DEFAULT_DAYS
        days = sorted({int(d) for d in raw if isinstance(d, int) and 0 < d <= MAX_DAY})
        return days or list(DEFAULT_DAYS)

    @property
    def method(self) -> str:
        """:returns: The plan's method (``signal_trend`` when it names none)."""
        return str((self.plan.get("measurement") or {}).get("method") or "signal_trend")


def _why(project: FactoryProject) -> Mapping[str, Any] | None:
    why = (project.charter or {}).get("why")
    return why if isinstance(why, Mapping) and why.get("measurement") else None


def bets(deps: FactoryDeps, factory_id: str) -> list[Bet]:
    """:returns: The factory's applied bets that froze a plan into a project (synchronous)."""
    store = deps.factory_store
    rows = [
        p
        for p in store.list_proposals(factory_id, kind="projects", status="applied", limit=1000)
        if p.bet
    ]
    if not rows:
        return []
    by_ref: dict[str, list[FactoryProject]] = {}
    for project in store.list_projects(factory_id):
        why = _why(project)
        if why is not None and why.get("proposal"):
            by_ref.setdefault(str(why["proposal"]), []).append(project)
    out: list[Bet] = []
    for row in sorted(rows, key=lambda p: p.number):
        projects = by_ref.get(row.ref) or []
        if not projects:
            continue
        frozen = max(
            (_why(p) or {} for p in projects), key=lambda w: int(w.get("plan_version") or 1)
        )
        out.append(Bet(proposal=row, projects=tuple(projects), plan=dict(frozen)))
    return out


def release_mode(deps: FactoryDeps, factory_id: str) -> str:
    """:returns: ``product.delivery.release`` (``merged`` unless the profile says otherwise)."""
    mode = definition.value(deps, factory_id, "product.delivery.release", "merged")
    return mode if isinstance(mode, str) and mode else "merged"


def merged_at(bet: Bet, prs: Iterable[Any]) -> int | None:
    """
    :returns: When the last of the bet's live projects shipped (its newest merged PR,
        else when its stage last changed), or ``None`` while one is still open.
    """
    live = bet.live
    if not live or not all(opportunities.project_shipped(p) for p in live):
        return None
    merged: dict[str, int] = {}
    for pr in prs:
        if pr.state == "merged" and pr.merged_at and pr.project_slug:
            merged[pr.project_slug] = max(merged.get(pr.project_slug, 0), int(pr.merged_at))
    times = [
        merged.get(p.slug) or int(p.stage_changed_at or p.closed_at or p.updated_at or 0)
        for p in live
    ]
    return max(times) if times else None


class ReleaseReader(Protocol):
    """Looks up a GitHub release or deployment of the factory's repos (on the server)."""

    async def released_after(
        self, factory_id: str, repos: Sequence[str], mode: str, after: int
    ) -> int | None:
        """:returns: When the first release (or deployment) at or after *after* went out."""
        ...


_RELEASES_QUERY: Final = (
    "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) {"
    " releases(first: 20, orderBy: {field: CREATED_AT, direction: DESC}) {"
    " nodes { publishedAt isDraft isPrerelease } } } }"
)
_DEPLOYMENTS_QUERY: Final = (
    "query($owner: String!, $name: String!, $env: [String!]) {"
    " repository(owner: $owner, name: $name) {"
    " deployments(first: 20, environments: $env, orderBy: {field: CREATED_AT, direction: DESC}) {"
    " nodes { latestStatus { state createdAt } } } } }"
)


def _epoch(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return calendar.timegm(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


class GitHubReleaseReader:
    """Reads releases and deployments with the factory owner's GitHub connection (read-only)."""

    def __init__(self, app_state: Any) -> None:
        self._app_state = app_state

    async def released_after(
        self, factory_id: str, repos: Sequence[str], mode: str, after: int
    ) -> int | None:
        from omnigent.server.factory.discovery.kinds.repo import AppGitHubReader

        reader = AppGitHubReader(self._app_state)
        env = mode.partition(":")[2] if mode.startswith("github_deployment:") else None
        found: list[int] = []
        for repo in repos:
            owner, _, name = repo.partition("/")
            variables: dict[str, Any] = {"owner": owner, "name": name}
            query = _RELEASES_QUERY
            if env is not None:
                query, variables["env"] = _DEPLOYMENTS_QUERY, [env]
            data = await reader.graphql(factory_id, query, variables)
            repository = (data or {}).get("repository") or {}
            if env is None:
                nodes = (repository.get("releases") or {}).get("nodes") or []
                times = [
                    _epoch(n.get("publishedAt"))
                    for n in nodes
                    if isinstance(n, Mapping)
                    and not n.get("isDraft")
                    and not n.get("isPrerelease")
                ]
            else:
                nodes = (repository.get("deployments") or {}).get("nodes") or []
                statuses = [n.get("latestStatus") or {} for n in nodes if isinstance(n, Mapping)]
                times = [
                    _epoch(s.get("createdAt"))
                    for s in statuses
                    if str(s.get("state") or "").upper() == "SUCCESS"
                ]
            found += [t for t in times if t is not None and t >= after]
        return min(found) if found else None


_reader_factory: Callable[[Any], ReleaseReader] = GitHubReleaseReader


def set_release_reader(make: Callable[[Any], ReleaseReader] | None) -> None:
    """Use *make* to build the GitHub release reader (tests); ``None`` restores the default."""
    global _reader_factory
    _reader_factory = make or GitHubReleaseReader


def day_seconds(deps: FactoryDeps, factory_id: str) -> int:
    """:returns: How long a readout day is: a day, or :data:`EVAL_DAY_S` in eval mode."""
    from omnigent.server.factory.eval_mode import is_eval

    root = deps.conversation_store.get_conversation(factory_id)
    return EVAL_DAY_S if root is not None and is_eval(root.labels) else DAY_S


# ── Stamping a bet for the prediction ledger ───────────────────


def _origin(bet: Bet) -> str:
    if str(bet.plan.get("kind") or (bet.proposal.bet or {}).get("kind")) == "hunch":
        return "hunch"
    return "requested" if bet.proposal.on_behalf_of else "discovered"


def stamp(deps: FactoryDeps, factory_id: str, bet: Bet) -> dict[str, Any]:
    """
    The bet as it was filed: the evidence level and rubric version that ranked
    it (the opportunity's newest score snapshot at filing), the sources of its
    evidence then, its origin and kind.
    """
    filed = int(bet.proposal.created_at or 0)
    out: dict[str, Any] = {
        "origin": _origin(bet),
        "kind": bet.plan.get("kind") or (bet.proposal.bet or {}).get("kind"),
        "goal": bet.plan.get("goal"),
        "filed_at": filed,
        "evidence_level": None,
        "def_version": None,
        "sources": [],
    }
    number = bet.opportunity
    if number is None:
        return out
    store = deps.factory_store
    snaps = store.list_score_snapshots(factory_id, opportunity=number, limit=200)
    before = [s for s in snaps if s.at <= filed + 60]
    if before:
        out.update(evidence_level=before[0].level, def_version=before[0].def_version)
    else:
        found = opportunities.evaluate_one(deps, factory_id, number)
        _doc, version = opportunities.effective_doc(deps, factory_id)
        if found is not None:
            out.update(evidence_level=found.meter.level, def_version=version, approx=True)
    links = [
        link
        for link in store.list_links(factory_id, opportunities=[number])
        if not link.counter and link.attached_at <= filed + 60
    ]
    numbers = tuple(sorted({link.signal for link in links}))[:500]
    if numbers:
        signals = store.list_signals(factory_id, SignalFilter(numbers=numbers), limit=len(numbers))
        out["sources"] = sorted(
            {
                s.source
                for s in signals
                if s.kind not in _SKIP_KINDS and INJECTION_SUSPECT not in s.flags
            }
        )
    return out


# ── Scheduling ─────────────────────────────────────────────────


def _plan_view(bet: Bet) -> dict[str, Any]:
    keys = (
        "proposal",
        "opportunity",
        "kind",
        "goal",
        "outcome",
        "metric",
        "hypothesis",
        "expected",
        "guardrails",
        "flag",
        "measurement",
        "kill",
        "plan_version",
        "frozen_at",
        "frozen_by",
    )
    return {k: bet.plan[k] for k in keys if k in bet.plan}


def schedule(
    deps: FactoryDeps,
    factory_id: str,
    bet: Bet,
    released_at: int,
    *,
    how: str,
    by: str = SERVER_BY,
) -> list[FactoryReadout]:
    """
    Store one scheduled readout per readout day of *bet*, counted from *released_at*.

    :param how: How the release was seen (``merged``, ``github_release``, ``manual`` …).
    :returns: The rows it stored now (none when they existed already).
    """
    day_s = day_seconds(deps, factory_id)
    stamped = stamp(deps, factory_id, bet)
    plan = _plan_view(bet)
    created: list[FactoryReadout] = []
    for day in bet.days:
        row, existed = deps.factory_store.add_readout(
            factory_id,
            proposal=bet.proposal.number,
            day=day,
            status="scheduled",
            opportunity=bet.opportunity,
            method=bet.method if bet.method in METHOD_LABEL else "signal_trend",
            plan_version=int(bet.plan.get("plan_version") or 1),
            released_at=released_at,
            due_at=released_at + day * day_s,
            plan=plan,
            stamp=stamped,
            result={"release": {"how": how, "by": by, "at": released_at, "day_s": day_s}},
        )
        if not existed:
            created.append(row)
    return created


async def _announce_scheduled(
    app_state: Any, factory_id: str, bet: Bet, rows: Sequence[FactoryReadout], how: str, by: str
) -> None:
    if not rows:
        return
    days = ", ".join(str(r.day) for r in rows)
    await events.emit(
        app_state,
        factory_id,
        "readout.scheduled",
        subject=f"proposal:{bet.ref}",
        summary=f"{bet.ref} is out ({how}): readouts on day {days} after the release",
        data={
            "proposal": bet.ref,
            "opportunity": f"O-{bet.opportunity}" if bet.opportunity else None,
            "released_at": rows[0].released_at,
            "how": how,
            "by": by,
            "readouts": [r.key for r in rows],
        },
    )


async def detect_releases(
    app_state: Any, factory_id: str, *, now: int | None = None
) -> list[FactoryReadout]:
    """
    Schedule the readouts of every applied bet whose release the profile's mode sees.

    ``merged``: all its projects are out. ``github_release`` / ``github_deployment:<env>``:
    then a release (or a successful deployment to *env*) of their repos at or
    after that, looked up every :data:`RELEASE_POLL_S`. ``manual``: never here
    (a person marks it, :func:`mark_released`).
    """
    deps = factory_deps(app_state)
    at = now if now is not None else now_s()

    def unreleased() -> tuple[list[tuple[Bet, int]], str]:
        known = {r.proposal for r in deps.factory_store.list_readouts(factory_id, limit=5000)}
        prs = deps.factory_store.list_prs(factory_id, states=["merged"])
        out = []
        for bet in bets(deps, factory_id):
            if bet.proposal.number in known:
                continue
            shipped = merged_at(bet, prs)
            if shipped is not None:
                out.append((bet, shipped))
        return out, release_mode(deps, factory_id)

    due, mode = await asyncio.to_thread(unreleased)
    created: list[FactoryReadout] = []
    for bet, shipped in due:
        if mode == "manual":
            continue
        released: int | None = shipped
        if mode != "merged":
            key = (factory_id, bet.proposal.number)
            if at - _last_poll.get(key, 0.0) < RELEASE_POLL_S:
                continue
            _last_poll[key] = at
            repos = sorted({p.repo for p in bet.live if p.repo})
            try:
                released = await _reader_factory(app_state).released_after(
                    factory_id, repos, mode, shipped
                )
            except Exception as exc:  # noqa: BLE001 - GitHub may be down; try again later
                _logger.info("factory readouts: release lookup for %s failed: %s", bet.ref, exc)
                continue
        if released is None:
            continue
        rows = await asyncio.to_thread(schedule, deps, factory_id, bet, released, how=mode)
        await _announce_scheduled(app_state, factory_id, bet, rows, mode, SERVER_BY)
        created += rows
    return created


# ── Writing a readout ──────────────────────────────────────────


def _context(deps: FactoryDeps, factory_id: str, row: FactoryReadout, now: int) -> MethodContext:
    day_s = int((row.result or {}).get("release", {}).get("day_s") or 0) or day_seconds(
        deps, factory_id
    )
    return MethodContext(
        deps=deps, factory_id=factory_id, readout=row, plan=row.plan or {}, day_s=day_s, now=now
    )


def _extended(deps: FactoryDeps, factory_id: str, proposal: int) -> bool:
    return any(
        (r.plan or {}).get("extension")
        for r in deps.factory_store.list_readouts(factory_id, proposal=proposal)
    )


def _final(deps: FactoryDeps, factory_id: str, row: FactoryReadout) -> bool:
    later = [
        r
        for r in deps.factory_store.list_readouts(factory_id, proposal=row.proposal)
        if r.day > row.day and r.status != "cancelled"
    ]
    return not later


def evaluate(
    deps: FactoryDeps, factory_id: str, row: FactoryReadout, *, now: int
) -> tuple[Measured, list[dict[str, Any]], Judgement | None]:
    """
    Measure *row* and judge it (synchronous). :returns: ``(measured, guardrails,
    judgement)``; the judgement is ``None`` while the method waits for a person.
    """
    ctx = _context(deps, factory_id, row, now)
    try:
        measured = method_for(row.method)(ctx)
    except Exception:  # noqa: BLE001 - a broken method reads as a failed check, never a verdict
        _logger.warning("factory readouts: %s failed for %s", row.method, row.ref, exc_info=True)
        measured = Measured(checks=(_check("method", "the measurement failed"),))
    reported = (row.result or {}).get("reported") or {}
    rails = guardrail_rows(ctx, measured, reported)
    answer = (row.answer or {}).get("answer")
    broken = any(g["state"] == "broken" for g in rails)
    if measured.pending and not broken:
        return measured, rails, None
    judgement = judge(
        expected=(row.plan or {}).get("expected") or {},
        effect=measured.effect,
        n=measured.n,
        min_n=measured.min_n,
        guardrails=rails,
        checks=measured.checks,
        answer=answer if answer in ANSWERS else None,
        extended=_extended(deps, factory_id, row.proposal),
    )
    return measured, rails, judgement


def _result(
    row: FactoryReadout, measured: Measured, rails: list[dict[str, Any]], judgement: Judgement
) -> dict[str, Any]:
    return {
        **(row.result or {}),
        "effect": measured.effect,
        "baseline": measured.baseline,
        "after": measured.after,
        "unit": measured.unit,
        "n": measured.n,
        "min_n": measured.min_n,
        "interval": list(measured.interval) if measured.interval else None,
        "guardrails": rails,
        "data_checks": [dict(c) for c in measured.checks],
        "inputs": dict(measured.inputs),
        "predicted_vs_actual": judgement.predicted,
        "why": judgement.why,
        "reaches": REACHES.get(row.method, "weak"),
    }


def _signal_text(row: FactoryReadout, judgement: Judgement, measured: Measured) -> str:
    from omnigent.server.factory.bets import expected_text

    answer = (row.answer or {}).get("answer")
    if measured.effect is not None:
        moved = f"measured {measured.effect:+g} {measured.unit or ''}".strip()
    elif answer:
        moved = f"a person answered {str(answer).replace('_', ' ')}"
    else:
        moved = "nothing measured"
    return (
        f"Readout {row.ref} by {METHOD_LABEL.get(row.method, row.method)}: {judgement.verdict}; "
        f"{moved}; predicted {expected_text((row.plan or {}).get('expected'))}; "
        f"recommendation {judgement.recommendation.replace('_', ' ')}."
    )


def write_back(
    deps: FactoryDeps,
    factory_id: str,
    row: FactoryReadout,
    judgement: Judgement,
    measured: Measured,
    *,
    now: int,
) -> int | None:
    """
    The readout's result reaches its opportunity through the ledger only: a
    ``test_result`` signal of the server's own source, linked to it, and the
    opportunity marked measured (synchronous).

    :returns: The signal's number, or ``None`` for a bet without an opportunity.
    """
    if row.opportunity is None:
        return None
    store = deps.factory_store
    version = int((row.result or {}).get("judged", 0)) + 1
    item = ledger.Item(
        text=_signal_text(row, judgement, measured),
        pointer=f"{SUBJECT}{row.id}:{version}",
        observed_at=now,
        kind="test_result",
        meta={
            "verdict": judgement.verdict,
            "method": row.method,
            "reaches": REACHES.get(row.method, "weak"),
            "readout": row.key,
            "proposal": row.bet,
            "day": row.day,
            "predicted": judgement.predicted,
        },
    )
    stored = ledger.ingest(deps, factory_id, ledger.SERVER_REF, [item], now=now)
    if not stored.signals:
        return None
    signal = stored.signals[0]
    if row.signal is not None and row.signal != signal.number:
        store.detach_link(
            factory_id, row.signal, row.opportunity, by=SERVER_BY, reason="readout judged again"
        )
    store.put_link(
        factory_id,
        signal.number,
        row.opportunity,
        attached_by=SERVER_BY,
        attached_kind="server",
        via=f"readout:{row.bet}",
    )
    opp = store.get_opportunity(factory_id, row.opportunity)
    if opp is not None:
        result = dict(opp.result or {})
        if result.get("decision") == "iterate":
            result.pop("decision", None)
        result.update(
            measured_at=now,
            readout=row.key,
            proposal=row.bet,
            day=row.day,
            verdict=judgement.verdict,
        )
        store.update_opportunity(factory_id, row.opportunity, result=result)
        found = opportunities.evaluate_one(deps, factory_id, row.opportunity)
        if found is not None:
            _doc, def_version = opportunities.effective_doc(deps, factory_id)
            opportunities.snapshot(
                deps, factory_id, [found], reason="readout", def_version=def_version, at=now
            )
    return signal.number


def _record(
    deps: FactoryDeps,
    factory_id: str,
    row: FactoryReadout,
    measured: Measured,
    rails: list[dict[str, Any]],
    judgement: Judgement,
    *,
    now: int,
    expect: str | Sequence[str],
) -> FactoryReadout | None:
    signal = write_back(deps, factory_id, row, judgement, measured, now=now)
    result = _result(row, measured, rails, judgement)
    result["judged"] = int((row.result or {}).get("judged", 0)) + 1
    return deps.factory_store.update_readout(
        factory_id,
        row.id,
        expect_status=expect,
        status="written",
        written_at=row.written_at or now,
        verdict=judgement.verdict,
        recommendation=judgement.recommendation,
        signal=signal if signal is not None else row.signal,
        result=result,
    )


def discovery_thread() -> str:
    """:returns: ``discovery`` when the Room has that thread, else ``factory``."""
    return "discovery" if thread_kind("discovery") else THREAD_FACTORY


def link(factory_id: str, row: FactoryReadout) -> str:
    """:returns: The cockpit path of the readout."""
    return f"/factory/{factory_id}/discovery/impact?readout={row.key}"


def _outcome(row: FactoryReadout) -> str:
    return str((row.plan or {}).get("outcome") or row.bet)


async def _room(app_state: Any, factory_id: str, text: str, key: str) -> None:
    from omnigent.server.factory import chat

    try:
        await chat.post_system(
            app_state,
            factory_id,
            discovery_thread(),
            text,
            message_id=chat.derived_id(factory_id, key),
        )
    except Exception:  # noqa: BLE001 - the Room line is a mirror; the readout is stored
        _logger.warning("factory readouts: could not post to the Room", exc_info=True)


def _scout(deps: FactoryDeps, factory_id: str) -> str | None:
    from omnigent.server.factory.membership import factory_members

    for conv in factory_members(factory_id, deps.conversation_store):
        if conv.labels.get(LABEL_ROLE) == "scout" and not control.is_gone(conv):
            return conv.id
    return None


async def _tell_scout(app_state: Any, factory_id: str, row: FactoryReadout) -> None:
    deps = factory_deps(app_state)
    scout = await asyncio.to_thread(_scout, deps, factory_id)
    if scout is None:
        return
    opp = f" on O-{row.opportunity}" if row.opportunity else ""
    text = (
        f"[Factory readout · {row.bet} · day {row.day} · {row.verdict}] "
        f"{_outcome(row)}{opp}: {(row.result or {}).get('why')}; recommendation "
        f"{str(row.recommendation).replace('_', ' ')}. Note it on the opportunity and "
        "suggest what to try next."
    )
    try:
        control.send_text(app_state, scout, text, kind="info")
    except Exception:  # noqa: BLE001 - Scout reads the readout on its next sweep anyway
        _logger.warning("factory readouts: could not tell Scout about %s", row.ref, exc_info=True)


def _options(factory_id: str, row: FactoryReadout) -> list[dict[str, Any]]:
    """In-place answers that follow the recommendation; anything else opens the readout."""
    out: list[dict[str, Any]] = []
    for i, decision in enumerate(ALIGNED.get(row.recommendation or "", ())):
        out.append({"id": decision, "label": DECISION_LABEL[decision], "recommended": i == 0})
    out.append(
        {
            "id": "other",
            "label": "Decide otherwise…",
            "href": link(factory_id, row),
            "description": "against the recommendation: an owner, a named reviewer and a reason",
        }
    )
    return out


async def _ask(app_state: Any, factory_id: str, row: FactoryReadout, *, answer: bool) -> None:
    """Open the readout's "Needs you" decision: keep or drop, or "did this help?"."""
    deps = factory_deps(app_state)
    if answer:
        question = f"Did {row.bet} help? {_outcome(row)} · day {row.day} after the release"
        options = [
            {
                "id": "answer",
                "label": "Answer in the cockpit",
                "href": link(factory_id, row),
                "recommended": True,
            }
        ]
        context = (
            f"{row.ref} is measured by a person's verdict: answer helped, no change or hurt, "
            "with a reason."
        )
    else:
        question = f"Keep or drop {row.bet}? {row.ref}: {row.verdict} · {_outcome(row)}"
        options = _options(factory_id, row)
        context = (
            f"Recommendation: {str(row.recommendation).replace('_', ' ')} "
            f"({(row.result or {}).get('why')})."
        )
    project = next(
        (
            p.slug
            for p in deps.factory_store.list_projects(factory_id)
            if ((p.charter or {}).get("why") or {}).get("proposal") == row.bet
        ),
        None,
    )

    def create() -> Any:
        return deps.factory_store.create_decision(
            factory_id,
            dedupe_key=f"{SUBJECT}{row.id}"[:64],
            question=control.clip(question, 1000),
            source=DECISION_SOURCE,
            options=options,
            blocking=False,
            allow_text=False,
            project_slug=project,
            context=context[:4000],
            assignee="operators",
            thread=discovery_thread(),
            subject=f"{SUBJECT}{row.key}",
        )

    try:
        decision, deduped = await asyncio.to_thread(create)
    except Exception:  # noqa: BLE001 - the readout is stored; the cockpit still lists it
        _logger.warning(
            "factory readouts: could not open a decision for %s", row.ref, exc_info=True
        )
        return
    await asyncio.to_thread(
        deps.factory_store.update_readout, factory_id, row.id, decision_id=decision.id
    )
    if not deduped:
        await events.emit(
            app_state,
            factory_id,
            "decision.opened",
            severity="notice",
            project=project,
            to_id="human",
            subject=f"decision:{decision.ref}",
            summary=f"{decision.ref}: {decision.question}",
            data={"decision_id": decision.id, "source": DECISION_SOURCE, "readout": row.key},
        )


def _written_line(row: FactoryReadout) -> str:
    from omnigent.server.factory.bets import expected_text

    result = row.result or {}
    effect = result.get("effect")
    moved = (
        f"{effect:+g} {result.get('unit') or ''}".strip()
        if isinstance(effect, int | float)
        else "no number"
    )
    return (
        f"Readout {row.ref} · {row.verdict}: {_outcome(row)}. {moved} against a predicted "
        f"{expected_text((row.plan or {}).get('expected'))} "
        f"({METHOD_LABEL.get(row.method, row.method)}). Recommendation: "
        f"{str(row.recommendation).replace('_', ' ')}."
    )


async def _written(app_state: Any, factory_id: str, row: FactoryReadout, *, final: bool) -> None:
    """Everything that follows a written readout: the event, the Room, Scout, Needs you."""
    await events.emit(
        app_state,
        factory_id,
        "readout.written",
        severity="warning" if row.verdict == "lost" else "notice",
        subject=f"proposal:{row.bet}",
        summary=_written_line(row)[:300],
        data={
            "readout": row.key,
            "readout_id": row.id,
            "proposal": row.bet,
            "opportunity": f"O-{row.opportunity}" if row.opportunity else None,
            "day": row.day,
            "method": row.method,
            "verdict": row.verdict,
            "recommendation": row.recommendation,
            "predicted_vs_actual": (row.result or {}).get("predicted_vs_actual"),
            "signal": f"S-{row.signal}" if row.signal else None,
            "final": final,
        },
    )
    await _room(app_state, factory_id, _written_line(row), f"{row.id}:written:{row.verdict}")
    await _tell_scout(app_state, factory_id, row)
    if final or row.verdict == "lost":
        deps = factory_deps(app_state)
        earlier = await asyncio.to_thread(
            lambda: deps.factory_store.list_readouts(factory_id, proposal=row.proposal)
        )
        for other in earlier:
            # One question per bet: the newest readout's asks it.
            if other.day < row.day and other.decision_id and other.status == "written":
                await _close_decision(app_state, other.decision_id, f"{row.ref} is in")
        await _ask(app_state, factory_id, row, answer=False)


async def write_due(
    app_state: Any, factory_id: str, *, now: int | None = None
) -> list[FactoryReadout]:
    """Write every scheduled readout whose day came (oldest first). :returns: The rows it moved."""
    deps = factory_deps(app_state)
    at = now if now is not None else now_s()
    rows = await asyncio.to_thread(
        lambda: deps.factory_store.list_readouts(factory_id, statuses=["scheduled"], due_before=at)
    )
    done: list[FactoryReadout] = []
    for row in rows:
        try:
            moved = await _write_one(app_state, factory_id, row, at)
        except Exception:  # noqa: BLE001 - one readout must not stop the others
            _logger.warning("factory readouts: writing %s failed", row.ref, exc_info=True)
            continue
        if moved is not None:
            done.append(moved)
    return done


async def _write_one(
    app_state: Any, factory_id: str, row: FactoryReadout, now: int
) -> FactoryReadout | None:
    deps = factory_deps(app_state)

    def run() -> tuple[FactoryReadout | None, bool, bool]:
        measured, rails, judgement = evaluate(deps, factory_id, row, now=now)
        if judgement is None:
            waiting = deps.factory_store.update_readout(
                factory_id,
                row.id,
                expect_status="scheduled",
                status="awaiting_answer",
                result={**(row.result or {}), "guardrails": rails},
            )
            return waiting, True, False
        stored = _record(
            deps, factory_id, row, measured, rails, judgement, now=now, expect="scheduled"
        )
        return stored, False, stored is not None and _final(deps, factory_id, stored)

    stored, waits, final = await asyncio.to_thread(run)
    if stored is None:
        return None
    if waits:
        await _ask(app_state, factory_id, stored, answer=True)
        await _room(
            app_state,
            factory_id,
            f"Readout {stored.ref}: did {_outcome(stored)} help? A person answers in the cockpit.",
            f"{stored.id}:awaiting",
        )
        return stored
    await _written(app_state, factory_id, stored, final=final)
    return stored


async def tick(app_state: Any, factory_id: str, *, now: int | None = None) -> dict[str, int]:
    """Detect releases and write due readouts for one factory with discovery on."""
    deps = factory_deps(app_state)
    if not await asyncio.to_thread(definition.discovery_enabled, deps, factory_id):
        return {"scheduled": 0, "written": 0}
    at = now if now is not None else now_s()
    scheduled = await detect_releases(app_state, factory_id, now=at)
    written = await write_due(app_state, factory_id, now=at)
    return {"scheduled": len(scheduled), "written": len(written)}


# ── People: release, answer, decide ────────────────────────────


class ReleaseIn(BaseModel):
    """``POST …/readouts/release``: a person says a bet is out (``manual`` release)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    proposal: str | None = Field(None, max_length=16)
    project: str | None = Field(None, max_length=64)
    at: int | None = Field(None, ge=0)
    note: str | None = Field(None, max_length=REASON_MAX)


class DecideIn(BaseModel):
    """``POST …/readouts/{id}/decide``: answer "did this help?", report a guardrail, decide."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["keep", "drop", "iterate", "extend"] | None = None
    reason: str | None = Field(None, max_length=REASON_MAX)
    reviewer: str | None = Field(None, max_length=REVIEWER_MAX)
    answer: Literal["helped", "no_change", "hurt"] | None = None
    guardrails_broken: list[str] = Field(default_factory=list, max_length=GUARDRAILS_MAX)


def find(deps: FactoryDeps, factory_id: str, ref: str) -> FactoryReadout:
    """
    :param ref: A readout id (hex) or its key, e.g. ``C-31-d28``.
    :raises CockpitError: 404 ``unknown_readout``.
    """
    flat = (ref or "").strip()
    row: FactoryReadout | None = None
    if flat.upper().startswith("C-") and "-D" in flat.upper():
        head, _, day = flat.upper().partition("-D")
        number = head[2:]
        if number.isdigit() and day.isdigit():
            found = deps.factory_store.list_readouts(factory_id, proposal=int(number))
            row = next((r for r in found if r.day == int(day)), None)
    else:
        row = deps.factory_store.get_readout(factory_id, flat)
    if row is None:
        raise CockpitError(404, "unknown_readout", f"{ref} is no readout of this factory")
    return row


def _find_bet(deps: FactoryDeps, factory_id: str, body: ReleaseIn) -> Bet:
    every = bets(deps, factory_id)
    if body.proposal:
        found = next((b for b in every if b.ref == body.proposal.upper()), None)
        if found is None:
            raise CockpitError(
                404,
                "unknown_bet",
                f"{body.proposal} is no applied bet with a frozen plan in this factory",
                field="proposal",
            )
        return found
    if body.project:
        slug = body.project.lstrip("#")
        found = next((b for b in every if any(p.slug == slug for p in b.projects)), None)
        if found is None:
            raise CockpitError(
                404, "unknown_bet", f"#{slug} carries no bet's plan", field="project"
            )
        return found
    raise CockpitError(400, "invalid_body", "name the bet (proposal) or its project")


async def mark_released(
    app_state: Any, root: Conversation, user_id: str | None, payload: Any
) -> dict[str, Any]:
    """
    A person says a bet is out (EDIT): its readouts are scheduled from *at* (now by default).

    :raises CockpitError: 400 ``invalid_body``, 404 ``unknown_bet``, 409
        ``already_released``, 422 ``release_before_apply``.
    """
    from omnigent.server.factory.projects import actor_name, human_actor
    from omnigent.server.factory.start import parse_body

    body = parse_body(ReleaseIn, payload if payload is not None else {})
    deps = factory_deps(app_state)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    actor = await asyncio.to_thread(human_actor, deps, user_id, root.id)
    by = actor_name(actor)
    now = now_s()
    at = body.at if body.at is not None else now
    bet = await asyncio.to_thread(_find_bet, deps, root.id, body)
    existing = await asyncio.to_thread(
        lambda: deps.factory_store.list_readouts(root.id, proposal=bet.proposal.number)
    )
    if existing:
        raise CockpitError(
            409,
            "already_released",
            f"{bet.ref} was released already; its readouts count from then",
            released_at=existing[0].released_at,
        )
    if at > now or at < int(bet.proposal.applied_at or bet.proposal.created_at or 0):
        raise CockpitError(
            422,
            "release_before_apply",
            "a release falls between the bet's approval and now",
            field="at",
        )
    rows = await asyncio.to_thread(schedule, deps, root.id, bet, at, how="manual", by=by)
    await _announce_scheduled(app_state, root.id, bet, rows, "manual", by)
    return {
        "proposal": bet.ref,
        "released_at": at,
        "readouts": [readout_view(r, now=now) for r in rows],
    }


def _report(row: FactoryReadout, names: Sequence[str], by: str, now: int) -> dict[str, Any] | None:
    known = {str(g) for g in (row.plan or {}).get("guardrails") or []}
    wanted = [" ".join(n.split()) for n in names if n and n.strip()]
    unknown = [n for n in wanted if n not in known]
    if unknown:
        raise CockpitError(
            422,
            "unknown_guardrail",
            f"{unknown[0]!r} is no guardrail of {row.bet}'s plan",
            field="guardrails_broken",
        )
    if not wanted:
        return None
    reported = dict((row.result or {}).get("reported") or {})
    for name in wanted:
        reported.setdefault(name, {"by": by, "at": now})
    return reported


async def decide(
    app_state: Any,
    root: Conversation,
    user_id: str | None,
    ref: str,
    payload: Any,
    *,
    via: str | None = None,
    by_name: str | None = None,
) -> dict[str, Any]:
    """
    A person answers "did this help?", reports a broken guardrail, or decides a
    written readout (keep, drop, iterate, extend).

    Following the recommendation needs EDIT; going against it an owner, a named
    reviewer and a reason.

    :param via: How it came (``decision D-n`` from Needs you).
    :param by_name: Who answered, when the caller already resolved it (a decision).
    :raises CockpitError: 400 ``invalid_body``, 403 ``owners_only``, 404
        ``unknown_readout``, 409 ``not_due`` / ``readout_cancelled`` /
        ``already_decided`` / ``answer_first`` / ``already_answered`` /
        ``already_extended``, 422 ``reason_required`` / ``reviewer_required`` /
        ``unknown_guardrail``.
    """
    from omnigent.server.factory.projects import actor_name, human_actor
    from omnigent.server.factory.start import parse_body

    body = parse_body(DecideIn, payload if payload is not None else {})
    if body.decision is None and body.answer is None and not body.guardrails_broken:
        raise CockpitError(
            400, "invalid_body", "give a decision, an answer, or a broken guardrail"
        )
    deps = factory_deps(app_state)
    if via is None:
        await control.require(deps, user_id, root.id, LEVEL_EDIT)
    actor = await asyncio.to_thread(human_actor, deps, user_id, root.id)
    by = by_name or actor_name(actor)
    level = await asyncio.to_thread(people.viewer_level, deps, root.id, user_id)
    now = now_s()
    row = await asyncio.to_thread(find, deps, root.id, ref)
    if row.status == "scheduled":
        raise CockpitError(
            409,
            "not_due",
            f"{row.ref} is due {time.strftime('%Y-%m-%d', time.gmtime(row.due_at))}",
        )
    if row.status == "cancelled":
        raise CockpitError(409, "readout_cancelled", f"{row.ref} was cancelled")
    if row.status == "decided":
        raise CockpitError(
            409, "already_decided", f"{row.ref} was decided: {row.decision} by {row.decided_by}"
        )
    if body.answer is not None and (row.answer or {}).get("answer"):
        raise CockpitError(409, "already_answered", f"{row.ref} was answered already")
    if body.answer is not None and row.method != "human_verdict":
        raise CockpitError(
            400, "invalid_body", f"{row.ref} is measured by {row.method}, not a person's verdict"
        )
    if row.status == "awaiting_answer" and body.answer is None and not body.guardrails_broken:
        raise CockpitError(409, "answer_first", f"{row.ref} waits for its answer: did it help?")
    if body.answer is not None and not body.reason:
        raise CockpitError(422, "reason_required", "say why it helped or not", field="reason")
    reported = _report(row, body.guardrails_broken, by, now)
    if body.answer is not None or reported is not None:
        row = await _rejudge(app_state, root.id, row, body, by, reported, now)
    if body.decision is not None:
        row = await _apply_decision(app_state, root.id, row, body, by, level, now, via=via)
    return readout_view(row, now=now)


async def _rejudge(
    app_state: Any,
    factory_id: str,
    row: FactoryReadout,
    body: DecideIn,
    by: str,
    reported: Mapping[str, Any] | None,
    now: int,
) -> FactoryReadout:
    """Store an answer or a reported guardrail and judge the readout again."""
    deps = factory_deps(app_state)
    first = row.status == "awaiting_answer"

    def run() -> tuple[FactoryReadout | None, bool]:
        fields: dict[str, Any] = {}
        if body.answer is not None:
            fields["answer"] = {"answer": body.answer, "reason": body.reason, "by": by, "at": now}
        if reported is not None:
            fields["result"] = {**(row.result or {}), "reported": dict(reported)}
        updated = deps.factory_store.update_readout(
            factory_id, row.id, expect_status=row.status, **fields
        )
        if updated is None:
            return None, False
        measured, rails, judgement = evaluate(deps, factory_id, updated, now=now)
        if judgement is None:
            return updated, False
        stored = _record(
            deps, factory_id, updated, measured, rails, judgement, now=now, expect=updated.status
        )
        return stored, stored is not None and _final(deps, factory_id, stored)

    stored, final = await asyncio.to_thread(run)
    if stored is None:
        raise CockpitError(409, "readout_changed", f"{row.ref} changed meanwhile; read it again")
    if stored.status != "written":
        return stored
    if row.decision_id:
        # Its question (did it help? keep or drop?) was asked of the old verdict.
        why = f"answered by {by}" if first else f"judged again after {by}'s report"
        await _close_decision(app_state, row.decision_id, why)
    await _written(app_state, factory_id, stored, final=final)
    return stored


async def _close_decision(app_state: Any, decision_id: str, why: str) -> None:
    from omnigent.server.factory import server_decisions

    await server_decisions.expire_decision(app_state, decision_id, why=why)


async def _apply_decision(
    app_state: Any,
    factory_id: str,
    row: FactoryReadout,
    body: DecideIn,
    by: str,
    level: int,
    now: int,
    *,
    via: str | None,
) -> FactoryReadout:
    deps = factory_deps(app_state)
    decision = str(body.decision)
    if row.status != "written":
        raise CockpitError(409, "answer_first", f"{row.ref} waits for its answer: did it help?")
    against = not aligned(row.recommendation, decision)
    if against and via is not None:
        raise CockpitError(
            409,
            "recommendation_changed",
            f"{row.ref} now recommends {str(row.recommendation).replace('_', ' ')}: decide it "
            "in the cockpit",
        )
    if against:
        if level < LEVEL_MANAGE:
            raise CockpitError(
                403,
                "owners_only",
                f"{row.ref} recommends {str(row.recommendation).replace('_', ' ')}: going "
                "against it is for owners",
            )
        if not body.reviewer:
            raise CockpitError(
                422,
                "reviewer_required",
                "going against the recommendation names the person who reviewed it",
                field="reviewer",
            )
        if not body.reason:
            raise CockpitError(
                422, "reason_required", "going against the recommendation says why", field="reason"
            )
    if decision == "extend" and await asyncio.to_thread(_extended, deps, factory_id, row.proposal):
        raise CockpitError(409, "already_extended", f"{row.bet} was extended once already")

    def run() -> tuple[FactoryReadout | None, list[FactoryReadout], FactoryReadout | None]:
        stored = deps.factory_store.update_readout(
            factory_id,
            row.id,
            expect_status="written",
            status="decided",
            decision=decision,
            decided_by=by[:128],
            decided_at=now,
            review={
                "reason": body.reason,
                "reviewer": body.reviewer,
                "against_recommendation": against,
                "via": via or "cockpit",
            },
        )
        if stored is None:
            return None, [], None
        cancelled: list[FactoryReadout] = []
        added: FactoryReadout | None = None
        later = [
            r
            for r in deps.factory_store.list_readouts(factory_id, proposal=row.proposal)
            if r.status in OPEN_STATUSES and r.id != row.id
        ]
        if decision in CLOSING:
            for other in later:
                done = deps.factory_store.update_readout(
                    factory_id,
                    other.id,
                    expect_status=other.status,
                    status="cancelled",
                    review={"reason": f"{row.bet} was decided on day {row.day}: {decision}"},
                )
                if done is not None:
                    cancelled.append(done)
            _settle_opportunity(deps, factory_id, stored, decision, now)
        elif decision == "extend" and not later:
            day = min(MAX_DAY, row.day * 2)
            added, _existed = deps.factory_store.add_readout(
                factory_id,
                proposal=row.proposal,
                day=day,
                status="scheduled",
                opportunity=row.opportunity,
                method=row.method,
                plan_version=row.plan_version,
                released_at=row.released_at,
                due_at=row.released_at + (row.due_at - row.released_at) // max(row.day, 1) * day,
                plan={**(row.plan or {}), "extension": True},
                stamp=row.stamp,
                result={"release": (row.result or {}).get("release")},
            )
        return stored, cancelled, added

    stored, cancelled, added = await asyncio.to_thread(run)
    if stored is None:
        raise CockpitError(409, "readout_changed", f"{row.ref} changed meanwhile; read it again")
    for other in [stored, *cancelled]:
        if other.decision_id and via is None:
            await _close_decision(app_state, other.decision_id, f"decided: {decision} by {by}")
        elif other.decision_id and other.id != stored.id:
            await _close_decision(app_state, other.decision_id, f"{row.bet} was decided")
    flag = ((stored.plan or {}).get("flag") or {}).get("key")
    follow = ""
    if decision == "drop" and flag:
        follow = f" Turn the flag {flag} off in the product's own tooling."
    elif decision == "extend" and added is not None:
        follow = f" One more readout on day {added.day}."
    await events.emit(
        app_state,
        factory_id,
        "readout.decided",
        severity="notice",
        subject=f"proposal:{stored.bet}",
        summary=f"{by} decided {decision} on {stored.ref}"
        + (" against the recommendation" if against else ""),
        data={
            "readout": stored.key,
            "readout_id": stored.id,
            "proposal": stored.bet,
            "opportunity": f"O-{stored.opportunity}" if stored.opportunity else None,
            "decision": decision,
            "recommendation": stored.recommendation,
            "against_recommendation": against,
            "reviewer": body.reviewer,
            "reason": body.reason,
            "by": by,
            "via": via or "cockpit",
            "cancelled": [r.key for r in cancelled],
            "extended_to": added.key if added is not None else None,
        },
    )
    await _room(
        app_state,
        factory_id,
        f"{by} decided {decision} on {stored.ref} ({stored.verdict}): {_outcome(stored)}."
        + (
            f" Against the recommendation; reviewed by {body.reviewer}: {body.reason}"
            if against
            else ""
        )
        + follow,
        f"{stored.id}:decided",
    )
    return stored


def _settle_opportunity(
    deps: FactoryDeps, factory_id: str, row: FactoryReadout, decision: str, now: int
) -> None:
    """Keep and drop close the opportunity; iterate closes the bet and reopens it."""
    if row.opportunity is None:
        return
    opp = deps.factory_store.get_opportunity(factory_id, row.opportunity)
    if opp is None:
        return
    result = dict(opp.result or {})
    result.update(decided_at=now, readout=row.key, proposal=row.bet)
    if decision == "iterate":
        result["decision"] = "iterate"
        result["iterated"] = sorted({*opportunities.iterated(opp.result), row.bet})
    else:
        result["decision"] = decision
    deps.factory_store.update_opportunity(factory_id, row.opportunity, result=result)


# ── Hooks: a decision answered in place, a bet filed ───────────


async def _on_event(app_state: Any, event: FactoryEvent) -> None:
    if event.kind == "decision.answered":
        await _decision_answered(app_state, event)
    elif event.kind == "bet.filed":
        await _bet_filed(app_state, event)


async def _decision_answered(app_state: Any, event: FactoryEvent) -> None:
    data = event.data or {}
    option = data.get("option_id")
    subject = event.subject or ""
    if option not in DECISIONS or not subject.startswith("decision:D-"):
        return
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return
    number = subject.removeprefix("decision:D-")
    if not number.isdigit():
        return
    decision = await asyncio.to_thread(
        deps.factory_store.get_decision_by_number, event.factory_id, int(number)
    )
    if decision is None or decision.source != DECISION_SOURCE:
        return
    rows = await asyncio.to_thread(
        lambda: deps.factory_store.list_readouts(event.factory_id, statuses=["written"])
    )
    row = next((r for r in rows if control.same_id(r.decision_id, decision.id)), None)
    root = await control.get_conv(deps, event.factory_id)
    if row is None or root is None:
        return
    try:
        await decide(
            app_state,
            root,
            None,
            row.id,
            {"decision": option},
            via=f"decision {decision.ref}",
            by_name=str(data.get("by") or decision.answered_by or "someone"),
        )
    except CockpitError as exc:
        _logger.info("factory readouts: %s answered but not applied: %s", decision.ref, exc.code)


async def _bet_filed(app_state: Any, event: FactoryEvent) -> None:
    """Snapshot the opportunity's score when a bet is filed (the ledger's "at filing")."""
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    ref = (event.data or {}).get("opportunity")
    match = opportunities.REF_RE.match(str(ref or ""))
    if not isinstance(deps, FactoryDeps) or match is None:
        return

    def snap() -> None:
        if not definition.discovery_enabled(deps, event.factory_id):
            return
        found = opportunities.evaluate_one(deps, event.factory_id, int(match.group(1)))
        if found is None:
            return
        _doc, version = opportunities.effective_doc(deps, event.factory_id)
        opportunities.snapshot(deps, event.factory_id, [found], reason="bet", def_version=version)

    try:
        await asyncio.to_thread(snap)
    except Exception:  # noqa: BLE001 - the ledger falls back to the newest snapshot
        _logger.warning("factory readouts: could not snapshot %s at filing", ref, exc_info=True)


def install() -> Callable[[], None]:
    """Listen for answered readout decisions and filed bets. :returns: The unhook."""
    return events.add_listener(_on_event)


# ── Views ──────────────────────────────────────────────────────


def readout_view(row: FactoryReadout, *, now: int) -> dict[str, Any]:
    """One readout as the cockpit shows it (everything is server-written)."""
    result = row.result or {}
    plan = row.plan or {}
    measurement = plan.get("measurement") or {}
    decision = None
    if row.decision:
        review = row.review or {}
        decision = {
            "decision": row.decision,
            "by": row.decided_by,
            "at": row.decided_at,
            "reviewer": review.get("reviewer"),
            "reason": review.get("reason"),
            "against_recommendation": bool(review.get("against_recommendation")),
            "via": review.get("via"),
        }
    options = None
    if row.status == "written":
        options = [
            {
                "decision": d,
                "label": DECISION_LABEL[d],
                "recommended": i == 0,
                "against_recommendation": False,
            }
            for i, d in enumerate(ALIGNED.get(row.recommendation or "", ()))
        ]
        options += [
            {
                "decision": d,
                "label": DECISION_LABEL[d],
                "recommended": False,
                "against_recommendation": True,
                "needs": ["owner", "reviewer", "reason"],
            }
            for d in DECISIONS
            if d not in ALIGNED.get(row.recommendation or "", ())
        ]
    return {
        "id": row.id,
        "key": row.key,
        "ref": row.ref,
        "proposal": row.bet,
        "opportunity": f"O-{row.opportunity}" if row.opportunity else None,
        "day": row.day,
        "status": row.status,
        "title": plan.get("outcome"),
        "metric": plan.get("metric"),
        "method": row.method,
        "method_label": METHOD_LABEL.get(row.method, row.method),
        "reaches": REACHES.get(row.method),
        "expected": plan.get("expected"),
        "guardrails": result.get("guardrails")
        or [{"name": g, "state": "unmeasured"} for g in plan.get("guardrails") or []],
        "flag": plan.get("flag"),
        "kill": plan.get("kill"),
        "plan_version": row.plan_version,
        "frozen": {"at": plan.get("frozen_at"), "by": plan.get("frozen_by")},
        "released_at": row.released_at,
        "release": result.get("release"),
        "due_at": row.due_at,
        "due_in_s": max(0, row.due_at - now) if row.status == "scheduled" else 0,
        "written_at": row.written_at,
        "baseline": result.get("baseline"),
        "after": result.get("after"),
        "effect": result.get("effect"),
        "unit": result.get("unit") or (plan.get("expected") or {}).get("unit"),
        "interval": result.get("interval"),
        "n": result.get("n"),
        "min_n": result.get("min_n") or measurement.get("min_n"),
        "data_checks": result.get("data_checks") or [],
        "inputs": result.get("inputs") or {},
        "verdict": row.verdict,
        "recommendation": row.recommendation,
        "predicted_vs_actual": result.get("predicted_vs_actual"),
        "why": result.get("why"),
        "answer": row.answer,
        "decision": decision,
        "options": options,
        "signal": f"S-{row.signal}" if row.signal else None,
        "evidence_level": (row.stamp or {}).get("evidence_level"),
        "def_version": (row.stamp or {}).get("def_version"),
        "origin": (row.stamp or {}).get("origin"),
        "extension": bool(plan.get("extension")),
    }


def pending_bets(deps: FactoryDeps, factory_id: str) -> list[dict[str, Any]]:
    """Applied bets with no readout yet: the predicted band only, "not measured yet"."""
    known = {r.proposal for r in deps.factory_store.list_readouts(factory_id, limit=5000)}
    out = []
    for bet in bets(deps, factory_id):
        if bet.proposal.number in known:
            continue
        shipped = all(opportunities.project_shipped(p) for p in bet.live) and bool(bet.live)
        out.append(
            {
                "proposal": bet.ref,
                "opportunity": f"O-{bet.opportunity}" if bet.opportunity else None,
                "title": bet.plan.get("outcome"),
                "metric": bet.plan.get("metric"),
                "method": bet.method,
                "expected": bet.plan.get("expected"),
                "readout_days": bet.days,
                "projects": [
                    {"slug": p.slug, "status": p.status, "stage": p.stage} for p in bet.live
                ],
                "state": "awaiting_release" if shipped else "building",
            }
        )
    return out


def prediction_ledger(
    deps: FactoryDeps, factory_id: str, *, rows: Sequence[FactoryReadout] | None = None
) -> dict[str, Any]:
    """
    The prediction ledger over every readout (metric definitions in ``telemetry/metrics.py``):
    the share inside the predicted range, the win rate by evidence level at filing, the
    yield per source and the hit rate by origin, each with n, and grouped by the rubric
    version that ranked the bets. Rates under :data:`.metrics.WIN_RATE_MIN_PER_LEVEL`
    readouts say "not enough data".
    """
    from omnigent.server.factory.telemetry import metrics

    store = deps.factory_store
    readouts = list(rows) if rows is not None else store.list_readouts(factory_id, limit=5000)
    facts = metrics.Facts(
        factory_id=factory_id,
        window=metrics.Window(0, now_s() + 1),
        projects=store.list_projects(factory_id),
        readouts=readouts,
    )
    found = {
        name: metrics.evaluate_metric(
            facts,
            metrics.DISCOVERY_CATALOG[name],
            facts.window,
            metrics.Filters(),
            group_by="def_version",
        )
        for name in metrics.LEDGER_METRICS
    }
    hits, wins, origins = (
        found["prediction_hit_rate"],
        found["bet_win_rate"],
        found["origin_hit_rate"],
    )
    measured = len(metrics.latest_readouts(readouts))
    enough = metrics.WIN_RATE_MIN_PER_LEVEL
    return {
        "readouts": measured,
        "inside": {"n": _count(hits), "of": int(hits.get("n") or 0), "share": hits.get("value")},
        "win_rate": {"value": wins.get("value"), "n": int(wins.get("n") or 0)},
        "win_rate_by_level": wins.get("by") or {},
        "min_per_level": enough,
        "by_source": found["source_yield"].get("by") or {},
        "by_origin": origins.get("by") or {},
        "requested": origins.get("requested") or {"shipped": 0, "measured": 0},
        "by_def_version": {
            str(version): {
                "won": _count(group),
                "n": int(group.get("n") or 0),
                "inside": _count((hits.get("groups") or {}).get(version) or {}),
            }
            for version, group in (wins.get("groups") or {}).items()
        },
        "metrics": found,
        "note": None
        if any(level.get("enough") for level in (wins.get("by") or {}).values())
        else f"not enough data: a rate needs {enough} readouts per evidence level",
    }


def _count(result: Mapping[str, Any]) -> int:
    """:returns: How many samples of a share were 1 (its value times n)."""
    value, n = result.get("value"), result.get("n")
    if not isinstance(value, int | float) or not isinstance(n, int):
        return 0
    return round(value * n)


def summary_fields(deps: FactoryDeps, factory_id: str, *, now: int) -> dict[str, Any]:
    """The readout and prediction-ledger fields of ``GET …/discovery`` (synchronous)."""
    from omnigent.server.factory.telemetry.metrics import latest_readouts

    rows = deps.factory_store.list_readouts(factory_id, limit=5000)
    scheduled = [r for r in rows if r.status == "scheduled"]
    written = [r for r in rows if r.status in ("written", "decided")]
    waiting = [r for r in rows if r.status == "awaiting_answer"] + [
        r for r in rows if r.status == "written" and r.decision_id
    ]
    latest = sorted(written, key=lambda r: r.written_at or 0, reverse=True)[:3]
    return {
        "readouts": {
            "written": len(written),
            "scheduled": len(scheduled),
            "waiting": len(waiting),
            "won": sum(1 for r in latest_readouts(written) if r.verdict == "won"),
            "next_due_at": min((r.due_at for r in scheduled), default=None),
            "latest": [readout_view(r, now=now) for r in latest],
            "release": release_mode(deps, factory_id),
        },
        "ledger": prediction_ledger(deps, factory_id, rows=rows),
    }


__all__ = [
    "ALIGNED",
    "ANSWERS",
    "DAY_S",
    "DECISIONS",
    "DECISION_SOURCE",
    "EVAL_DAY_S",
    "METHOD_LABEL",
    "MVP_METHODS",
    "REACHES",
    "RECOMMENDATIONS",
    "VERDICTS",
    "Bet",
    "DecideIn",
    "GitHubReleaseReader",
    "Judgement",
    "Measured",
    "MethodContext",
    "ReleaseIn",
    "ReleaseReader",
    "aligned",
    "bets",
    "day_seconds",
    "decide",
    "detect_releases",
    "discovery_thread",
    "evaluate",
    "find",
    "guardrail_rows",
    "human_verdict",
    "install",
    "judge",
    "mark_released",
    "merged_at",
    "near_end",
    "pending_bets",
    "predicted_vs_actual",
    "prediction_ledger",
    "readout_view",
    "register_guardrail_check",
    "register_method",
    "release_mode",
    "schedule",
    "set_release_reader",
    "signal_trend",
    "stamp",
    "summary_fields",
    "tick",
    "write_back",
    "write_due",
]
