"""Bets on projects proposals: the opportunity, the bet and the case against it.

A bet is the ``C-n`` projects proposal with more fields (``mechanism.md``
§5.4): a lead fills in ``opportunity`` and ``bet``, the server checks them
here, the other lead writes the ``case_against`` when it co-signs, and
applying the proposal freezes the bet into ``charter.why`` of every project it
creates, so the metric, the range and the readout days cannot move after
anyone has seen data.

What the server checks when a bet is filed:

- each kind has its prerequisite: ``outcome`` needs a validated opportunity,
  ``test`` an open one (an idea is enough), ``enabler`` a goal of the
  strategy, and ``hunch`` an owner's approval ("bet anyway");
- the plan is complete: metric, expected range, measurement with readout
  days, a kill rule and a flag (or why there is none);
- the metric can be measured: a goal's metric binding, or a method the server
  runs without one (``signal_trend``, ``human_verdict``);
- quoted user words in agent text match a stored excerpt of a cited signal
  (a hook the discovery ledger fills; until it does every quote is refused);
- charter fields carry no user quotes: the outcome, metric, kill date and unit
  refuse them, and freezing replaces any left in the other fields.

The flag's convention and provider come from the owner's
``product.delivery.flags``; a lead names only the key, or why there is none.

Opportunities live in the discovery ledger. It registers its lookup with
:func:`register_opportunities`; while none is registered no opportunity is
known, so outcome and test bets wait for discovery and nothing changes for a
factory without it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omnigent.server.factory.charter import OUTCOME_MAX
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.definition_blocks import GOAL_ID_RE

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryProposal
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

BET_KINDS: Final = ("outcome", "test", "enabler", "hunch")
"""The capacity-mix categories a bet falls in (``strategy.mix``)."""
METHODS: Final = ("controlled", "before_after", "adoption", "signal_trend", "human_verdict")
UNBOUND_METHODS: Final = frozenset({"signal_trend", "human_verdict"})
"""Methods the server measures without a metric binding (``mechanism.md`` §7.2)."""
VALIDATED_STATUSES: Final = frozenset({"validated", "proposed", "building", "shipped", "measured"})
"""Opportunity statuses with evidence at the validated threshold or past it."""
OPEN_STATUSES: Final = VALIDATED_STATUSES | {"idea"}
"""Opportunity statuses a test may target (not parked, dropped, merged or kept)."""
CASE_AGAINST_LINES: Final = 3
CASE_LINE_MAX: Final = 300
DECLINE_REASON_MAX: Final = 300
QUOTE_MIN_WORDS: Final = 4
"""A quoted span this long reads as someone's words, not a label like "Share draft"."""
PLAN_VERSION: Final = 1
OMITTED: Final = "(user words omitted)"

OPPORTUNITY_RE: Final = re.compile(r"^O-[1-9][0-9]{0,8}$")
SIGNAL_REF_RE: Final = re.compile(r"\bS-[1-9][0-9]{0,8}\b")
_FLAG_KEY_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,63}$")
_QUOTE_RE: Final = re.compile(
    r"“([^”]{1,2000})”|\"([^\"]{1,2000})\"|«([^»]{1,2000})»|„([^“”]{1,2000})[“”]"
)
_REQUIRED: Final = ("hypothesis", "outcome", "metric", "expected", "flag", "measurement", "kill")


# ── Hooks the discovery ledger fills ───────────────────────────


@dataclass(frozen=True)
class OpportunityInfo:
    """
    What a bet's checks read from an opportunity.

    :param status: ``idea``, ``validated``, ``proposed``, ``building`` … (derived).
    :param goal: The goal it sits under, e.g. ``"G-1"``.
    """

    id: str
    status: str
    goal: str | None = None
    title: str | None = None


OpportunityLookup = Callable[["FactoryDeps", str, str], OpportunityInfo | None]
"""``(deps, factory id, "O-12")`` → the opportunity, or ``None`` when unknown."""
QuoteCheck = Callable[["FactoryDeps", str, Sequence[str], Sequence[str]], list[str]]
"""``(deps, factory id, quotes, cited signal ids)`` → the quotes no stored excerpt matches."""
DeclineHook = Callable[["FactoryDeps", str, str, str, str], None]
"""``(deps, factory id, "O-12", reason, by)``: record a lead's decline on the opportunity."""


@dataclass
class _Hooks:
    opportunities: OpportunityLookup | None = None
    on_decline: DeclineHook | None = None
    quotes: QuoteCheck | None = None


_hooks = _Hooks()


def register_opportunities(
    lookup: OpportunityLookup | None, *, on_decline: DeclineHook | None = None
) -> None:
    """Let bets resolve opportunities (``None`` unregisters: none is known)."""
    _hooks.opportunities = lookup
    _hooks.on_decline = on_decline if lookup is not None else None


def register_quote_check(check: QuoteCheck | None) -> None:
    """Let bets verify quoted words against stored excerpts (``None``: refuse every quote)."""
    _hooks.quotes = check


def lookup_opportunity(deps: FactoryDeps, factory_id: str, ref: str) -> OpportunityInfo:
    """
    :returns: The opportunity *ref* (``O-n``) of the factory.
    :raises CockpitError: 404 ``unknown_opportunity`` (none known while discovery is off).
    """
    lookup = _hooks.opportunities
    found = lookup(deps, factory_id, ref) if lookup is not None else None
    if found is None:
        why = (
            "" if lookup is not None else ": this server keeps no opportunities (discovery is off)"
        )
        raise CockpitError(
            404, "unknown_opportunity", f"no opportunity {ref}{why}", field="opportunity"
        )
    return found


def record_decline(deps: FactoryDeps, factory_id: str, ref: str, reason: str, by: str) -> None:
    """Record a lead's decline on opportunity *ref* (with the ledger's hook, when one is set)."""
    if _hooks.on_decline is not None:
        _hooks.on_decline(deps, factory_id, ref, reason, by)


# ── Quotes ─────────────────────────────────────────────────────


def find_quotes(text: str | None) -> list[str]:
    """:returns: Quoted spans of at least :data:`QUOTE_MIN_WORDS` words in *text*."""
    out: list[str] = []
    for match in _QUOTE_RE.finditer(text or ""):
        span = next(g for g in match.groups() if g is not None).strip()
        if len(span.split()) >= QUOTE_MIN_WORDS:
            out.append(span)
    return out


def strip_quotes(text: str) -> str:
    """:returns: *text* with each quoted span of someone's words replaced by :data:`OMITTED`."""

    def repl(match: re.Match[str]) -> str:
        span = next(g for g in match.groups() if g is not None)
        return OMITTED if len(span.split()) >= QUOTE_MIN_WORDS else match.group(0)

    return _QUOTE_RE.sub(repl, text)


def check_charter_quotes(texts: Sequence[tuple[str, str | None]]) -> None:
    """
    Charters stay clean: no user quotes in fields a PL reads.

    :param texts: ``(field path, text)`` pairs, e.g. ``("bet.outcome", "…")``.
    :raises CockpitError: 422 ``quote_in_charter`` naming the field.
    """
    for where, text in texts:
        if find_quotes(text):
            raise CockpitError(
                422,
                "quote_in_charter",
                "charter fields carry no user quotes; say it in your own words and cite "
                "the signal ids",
                field=where,
            )


def verify_quotes(
    deps: FactoryDeps, factory_id: str, texts: Sequence[tuple[str, str | None]]
) -> None:
    """
    Quoted words in agent text must match a stored excerpt of a signal the text cites.

    :param texts: ``(field path, text)`` pairs an agent wrote.
    :raises CockpitError: 422 ``quote_unverified`` with the refused ``quotes``.
    """
    quotes = [q for _, text in texts for q in find_quotes(text)]
    if not quotes:
        return
    cited = sorted({ref for _, text in texts for ref in SIGNAL_REF_RE.findall(text or "")})
    check = _hooks.quotes
    refused = check(deps, factory_id, quotes, cited) if check is not None else list(quotes)
    if not refused:
        return
    _logger.warning(
        "factory %s: refused %d unverified quote(s) citing %s", factory_id, len(refused), cited
    )
    raise CockpitError(
        422,
        "quote_unverified",
        "quoted words must match a stored excerpt of a cited signal (S-n); cite the "
        "signal and paraphrase instead",
        field=next(where for where, text in texts if find_quotes(text)),
        quotes=[q[:120] for q in refused[:5]],
    )


# ── The bet body ───────────────────────────────────────────────


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _one_line(value: str | None, what: str) -> str | None:
    if value is not None and ("\n" in value or "\r" in value):
        raise ValueError(f"{what} must be a single line")
    return value


class ExpectedIn(_Body):
    """
    The predicted effect as a signed change, e.g. up +0.01 to +0.04 ``share``
    or down -4 to -1 ``pp``.

    :param low: The low end of the signed change (``low <= high``).
    """

    direction: Literal["up", "down"]
    low: float = Field(ge=-1e9, le=1e9)
    high: float = Field(ge=-1e9, le=1e9)
    unit: str = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def _order(self) -> ExpectedIn:
        if self.low > self.high:
            raise ValueError("expected.low is at most expected.high")
        if self.direction == "up" and self.high <= 0:
            raise ValueError("up predicts a rise: low and high are the signed change, high > 0")
        if self.direction == "down" and self.low >= 0:
            raise ValueError("down predicts a drop: low and high are the signed change, low < 0")
        return self


class FlagIn(_Body):
    """
    The flag the change ships behind, or why there is none. The convention and
    provider are the owner's (``product.delivery.flags``), never the lead's.
    """

    key: str | None = Field(None, max_length=64)
    none: str | None = Field(None, min_length=1, max_length=300)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str | None) -> str | None:
        if value is not None and not _FLAG_KEY_RE.match(value):
            raise ValueError("a flag key is letters, digits and . _ : / - (≤ 64)")
        return value

    @model_validator(mode="after")
    def _one(self) -> FlagIn:
        if (self.key is None) == (self.none is None):
            raise ValueError("give the flag's key, or none: <why the product has no flag>")
        return self


class MeasurementIn(_Body):
    """How the server measures the bet after release, and on which days."""

    method: Literal["controlled", "before_after", "adoption", "signal_trend", "human_verdict"]
    readout_days: list[int] = Field(min_length=1, max_length=4)
    min_n: int | None = Field(None, ge=1, le=100_000_000)

    @field_validator("readout_days")
    @classmethod
    def _days(cls, value: list[int]) -> list[int]:
        if any(day < 1 or day > 365 for day in value):
            raise ValueError("readout days are 1-365 days after release")
        return sorted(set(value))


class KillIn(_Body):
    """When to stop: the state that kills the bet and by when."""

    state: str = Field(min_length=1, max_length=300)
    date: str = Field(min_length=1, max_length=120)


class CostIn(_Body):
    agent_usd: float | None = Field(None, ge=0, le=100_000)
    human_review_min: int | None = Field(None, ge=0, le=100_000)
    risk: Literal["low", "medium", "high"] | None = None


class BetIn(_Body):
    """A bet as a lead files it on a projects proposal."""

    kind: Literal["outcome", "test", "enabler", "hunch"]
    goal: str | None = None
    hypothesis: str = Field(min_length=1, max_length=1000)
    outcome: str = Field(min_length=1, max_length=OUTCOME_MAX)
    metric: str = Field(min_length=1, max_length=120)
    expected: ExpectedIn
    guardrails: list[str] = Field(default_factory=list, max_length=10)
    flag: FlagIn
    measurement: MeasurementIn
    kill: KillIn
    cost: CostIn | None = None

    @field_validator("goal")
    @classmethod
    def _goal(cls, value: str | None) -> str | None:
        if value is not None and not GOAL_ID_RE.match(value):
            raise ValueError("a goal id is G-<n>")
        return value

    @field_validator("outcome", "metric")
    @classmethod
    def _line(cls, value: str) -> str:
        return _one_line(value, "it") or value

    @field_validator("guardrails")
    @classmethod
    def _guardrails(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in (v.strip() for v in value):
            _one_line(item, "a guardrail")
            if len(item) > 200:
                raise ValueError("each guardrail is at most 200 chars")
            if item and item not in out:
                out.append(item)
        return out


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str | list | dict) and not value)


def _missing(raw: Mapping[str, Any]) -> list[str]:
    """The parts of a complete plan *raw* lacks, e.g. ``["kill", "measurement.readout_days"]``."""
    out = [key for key in _REQUIRED if _blank(raw.get(key))]
    for key, parts in (("measurement", ("method", "readout_days")), ("kill", ("state", "date"))):
        inner = raw.get(key)
        if isinstance(inner, Mapping):
            out += [f"{key}.{part}" for part in parts if _blank(inner.get(part))]
    return out


def parse_case_against(raw: Any) -> list[str]:
    """
    :param raw: Up to three lines, as a list or one newline-separated string.
    :returns: The lines, stripped.
    :raises CockpitError: 400 ``invalid_body`` (field ``case_against``).
    """
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list) or not all(isinstance(line, str) for line in raw):
        raise CockpitError(
            400, "invalid_body", "case_against is up to three lines", field="case_against"
        )
    lines = [line.strip().lstrip("-•* ").strip() for line in raw]
    lines = [line for line in lines if line]
    if not lines or len(lines) > CASE_AGAINST_LINES:
        raise CockpitError(
            400,
            "invalid_body",
            f"case_against is 1-{CASE_AGAINST_LINES} lines: the strongest reasons the bet "
            "is wrong",
            field="case_against",
        )
    if any(len(line) > CASE_LINE_MAX for line in lines):
        raise CockpitError(
            400,
            "invalid_body",
            f"each case_against line is at most {CASE_LINE_MAX} chars",
            field="case_against",
        )
    return lines


# ── Checking a filed bet ───────────────────────────────────────


@dataclass(frozen=True)
class Checked:
    """
    A proposal's checked discovery fields.

    :param bet: The bet as stored (strategy guardrails and the flag convention added).
    :param owner: Only an owner may approve it (a hunch bet).
    """

    opportunity: str | None = None
    bet: dict[str, Any] | None = None
    owner: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Strategy:
    goals: dict[str, Mapping[str, Any]]
    guardrails: list[str]
    flags: Mapping[str, Any]

    @property
    def bindings(self) -> set[str]:
        return {str(g["metric"]) for g in self.goals.values() if g.get("metric")}


def _strategy(deps: FactoryDeps, factory_id: str) -> _Strategy:
    from omnigent.server.factory import definition

    resolved = definition.definition_for(deps, factory_id)
    get = resolved.get if resolved is not None else (lambda _path, default=None: default)
    goals = {
        str(g["id"]): g
        for g in get("strategy.goals", []) or []
        if isinstance(g, Mapping) and g.get("id")
    }
    guardrails = [str(g) for g in get("strategy.guardrails", []) or [] if g]
    flags = get("product.delivery.flags", {}) or {}
    return _Strategy(goals, guardrails, flags if isinstance(flags, Mapping) else {})


def _prerequisite(message: str, where: str = "bet.kind") -> CockpitError:
    return CockpitError(422, "bet_prerequisite", message, field=where)


def check(
    deps: FactoryDeps,
    factory_id: str,
    *,
    opportunity: str | None,
    bet: Any,
    requested: bool,
    creates: int,
    charter_texts: Sequence[tuple[str, str | None]] = (),
    agent_texts: Sequence[tuple[str, str | None]] = (),
) -> Checked:
    """
    Check a projects proposal's ``opportunity`` and ``bet`` (synchronous; run in a thread).

    :param requested: A person asked for the work (a bet is then optional).
    :param creates: How many projects the proposal creates.
    :param charter_texts: ``(field path, text)`` of the charter fields the
        proposal writes (no user quotes allowed).
    :param agent_texts: Other agent text a person reads with the bet (the
        rationale): its quotes must be verified once the proposal names an
        opportunity or carries a bet.
    :returns: The :class:`Checked` fields to store.
    :raises CockpitError: 400 ``invalid_body``, 404 ``unknown_opportunity``,
        422 ``bet_required`` / ``bet_incomplete`` / ``bet_prerequisite`` /
        ``unknown_goal`` / ``metric_not_measurable`` / ``quote_in_charter`` /
        ``quote_unverified``.
    """
    ref = (opportunity or "").strip() or None
    if ref is not None and not OPPORTUNITY_RE.match(ref):
        raise CockpitError(400, "invalid_body", "an opportunity id is O-<n>", field="opportunity")
    info = lookup_opportunity(deps, factory_id, ref) if ref is not None else None
    if bet is None:
        if ref is not None and not requested:
            raise CockpitError(
                422,
                "bet_required",
                f"work from {ref} carries a bet (kind, hypothesis, metric, expected, flag, "
                "measurement, kill); or decline it with op='decline' and a reason",
                field="bet",
            )
        check_charter_quotes(charter_texts)
        if ref is not None:
            verify_quotes(deps, factory_id, agent_texts)
        return Checked(opportunity=ref)
    if not isinstance(bet, Mapping):
        raise CockpitError(400, "invalid_body", "bet must be an object", field="bet")
    if creates == 0:
        raise CockpitError(
            400,
            "invalid_body",
            "a bet starts at least one project: create or split",
            field="bet",
        )
    missing = _missing(bet)
    if missing:
        raise CockpitError(
            422,
            "bet_incomplete",
            f"a bet needs a complete plan; missing: {', '.join(missing)}",
            field=f"bet.{missing[0]}",
            missing=missing,
        )
    from omnigent.server.factory.start import parse_body

    parsed = parse_body(BetIn, bet, prefix="bet")
    strategy = _strategy(deps, factory_id)
    goal = parsed.goal or (info.goal if info is not None else None)
    if parsed.goal is not None and parsed.goal not in strategy.goals:
        raise CockpitError(
            422, "unknown_goal", f"the strategy has no goal {parsed.goal}", field="bet.goal"
        )
    owner = _check_kind(parsed, ref, info, goal)
    if parsed.measurement.method not in UNBOUND_METHODS and parsed.metric not in strategy.bindings:
        raise CockpitError(
            422,
            "metric_not_measurable",
            f"{parsed.metric!r} is no metric binding a source measures; use the method "
            "signal_trend or human_verdict until one does",
            field="bet.metric",
        )
    check_charter_quotes(
        [
            ("bet.outcome", parsed.outcome),
            ("bet.metric", parsed.metric),
            ("bet.kill.date", parsed.kill.date),
            ("bet.expected.unit", parsed.expected.unit),
            *charter_texts,
        ]
    )
    verify_quotes(
        deps,
        factory_id,
        [
            ("bet.hypothesis", parsed.hypothesis),
            ("bet.kill.state", parsed.kill.state),
            ("bet.flag.none", parsed.flag.none),
            *((f"bet.guardrails[{i}]", g) for i, g in enumerate(parsed.guardrails)),
            *agent_texts,
        ],
    )
    stored = parsed.model_dump(mode="json", exclude_none=True)
    stored["guardrails"] = list(dict.fromkeys([*parsed.guardrails, *strategy.guardrails]))[:20]
    if goal is not None:
        stored["goal"] = goal
    flag = stored["flag"]
    if parsed.flag.key is not None:
        for key in ("convention", "provider"):
            value = strategy.flags.get(key)
            if isinstance(value, str) and value.strip():
                flag[key] = value.strip()
    warnings: list[str] = []
    if owner:
        warnings.append("hunch_bet: no evidence behind it; only an owner approves (bet anyway)")
    if parsed.flag.none is not None:
        warnings.append("no_flag: the readout uses a weaker method until the product has flags")
    return Checked(opportunity=ref, bet=stored, owner=owner, warnings=warnings)


def _check_kind(
    bet: BetIn, ref: str | None, info: OpportunityInfo | None, goal: str | None
) -> bool:
    """Each kind's prerequisite; :returns: whether only an owner may approve it."""
    if bet.kind == "outcome":
        if info is None:
            raise _prerequisite(
                "an outcome bet answers a validated opportunity: set opportunity=O-<n>",
                "opportunity",
            )
        if info.status not in VALIDATED_STATUSES:
            raise _prerequisite(
                f"{ref} is {info.status}: an outcome bet needs a validated opportunity; "
                "file a test (kind='test') to learn more first"
            )
    elif bet.kind == "test":
        if info is None:
            raise _prerequisite(
                "a test targets an opportunity (an idea is enough): set opportunity=O-<n>",
                "opportunity",
            )
        if info.status not in OPEN_STATUSES:
            raise _prerequisite(f"{ref} is {info.status}: a test needs an open opportunity")
    elif bet.kind == "enabler" and goal is None:
        raise _prerequisite("an enabler attaches to a goal: set bet.goal=G-<n>", "bet.goal")
    return bet.kind == "hunch"


# ── Freezing ───────────────────────────────────────────────────


def _strip_all(value: Any) -> Any:
    """*value* with :func:`strip_quotes` applied to every string in it."""
    if isinstance(value, str):
        return strip_quotes(value)
    if isinstance(value, Mapping):
        return {k: _strip_all(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_all(v) for v in value]
    return value


def frozen_why(row: FactoryProposal, *, by: str, at: int) -> dict[str, Any] | None:
    """
    The ``charter.why`` an applied proposal gives each project it creates.

    Quoted user words never reach a charter: every string is passed through
    :func:`strip_quotes`, so each quote is replaced by :data:`OMITTED`.

    :param by: Who applied it (a name).
    :param at: Epoch seconds of the apply.
    :returns: ``None`` when the proposal has neither an opportunity nor a bet.
    """
    bet = row.bet if isinstance(row.bet, Mapping) else None
    if bet is None and not row.opportunity:
        return None
    why: dict[str, Any] = {
        "proposal": row.ref,
        "opportunity": row.opportunity,
        "frozen_at": at,
        "frozen_by": by,
    }
    if bet is not None:
        why.update(
            {key: bet.get(key) for key in ("kind", "goal", "outcome", "metric", "cost")},
            hypothesis=bet.get("hypothesis"),
            expected=bet.get("expected"),
            guardrails=list(bet.get("guardrails") or []),
            flag=bet.get("flag"),
            measurement=bet.get("measurement"),
            kill=bet.get("kill"),
            plan_version=PLAN_VERSION,
        )
    return _strip_all({k: v for k, v in why.items() if not _blank(v)})


def split_why(parent: Any, *, split_from: str) -> dict[str, Any] | None:
    """
    The ``charter.why`` a split child inherits from its parent when the split
    carries no bet of its own: the same frozen plan (proposal, ``frozen_at``,
    ``plan_version``), so its readouts follow the bet the parent was approved on.

    :returns: ``None`` when the parent has no why.
    """
    if not isinstance(parent, Mapping) or not parent:
        return None
    return _strip_all({**parent, "split_from": split_from})


def autonomy_mode(deps: FactoryDeps, factory_id: str) -> str | None:
    """:returns: ``discovery.autonomy.bets``: who approves a bet a lead files on its own."""
    from omnigent.server.factory import definition

    mode = definition.value(deps, factory_id, "discovery.autonomy.bets")
    return mode if mode in ("confirm", "requested", "grace") else None


# ── Texts ──────────────────────────────────────────────────────


def expected_text(expected: Mapping[str, Any] | None) -> str:
    """:returns: The signed change, e.g. ``"up +0.01 to +0.04 share"``, ``"down -4 to -1 pp"``."""
    if not isinstance(expected, Mapping):
        return "no range"
    return (
        f"{expected.get('direction', '?')} {expected.get('low', '?'):+g} to "
        f"{expected.get('high', '?'):+g} {expected.get('unit', '')}".strip()
        if isinstance(expected.get("low"), int | float)
        and isinstance(expected.get("high"), int | float)
        else "no range"
    )


def flag_text(flag: Mapping[str, Any] | None) -> str:
    """:returns: e.g. ``"share_draft (convention: …)"`` or ``"none: <why>"``."""
    if not isinstance(flag, Mapping):
        return "not set"
    if flag.get("none"):
        return f"none: {flag['none']}"
    convention = f" (convention: {flag['convention']})" if flag.get("convention") else ""
    return f"{flag.get('key', '?')}{convention}"


def summary_lines(bet: Mapping[str, Any], opportunity: str | None) -> list[str]:
    """:returns: The bet in a few plain lines (for a decision, a mail or a log)."""
    source = f" from {opportunity}" if opportunity else ""
    goal = f" under {bet['goal']}" if bet.get("goal") else ""
    measurement = bet.get("measurement") or {}
    days = ", ".join(str(d) for d in measurement.get("readout_days") or [])
    kill = bet.get("kill") or {}
    return [
        f"{str(bet.get('kind', 'outcome')).capitalize()} bet{source}{goal}: {bet.get('outcome')}",
        f"Hypothesis: {bet.get('hypothesis')}",
        (
            f"Metric: {bet.get('metric')} · expects {expected_text(bet.get('expected'))} · "
            f"{measurement.get('method', '?')}, readout days {days or '?'}"
        ),
        f"Kill: {kill.get('state', '?')} ({kill.get('date', '?')})",
        f"Flag: {flag_text(bet.get('flag'))}",
    ]


__all__ = [
    "BET_KINDS",
    "CASE_AGAINST_LINES",
    "METHODS",
    "OMITTED",
    "OPEN_STATUSES",
    "OPPORTUNITY_RE",
    "QUOTE_MIN_WORDS",
    "UNBOUND_METHODS",
    "VALIDATED_STATUSES",
    "BetIn",
    "Checked",
    "DeclineHook",
    "OpportunityInfo",
    "OpportunityLookup",
    "QuoteCheck",
    "autonomy_mode",
    "check",
    "check_charter_quotes",
    "expected_text",
    "find_quotes",
    "flag_text",
    "frozen_why",
    "lookup_opportunity",
    "parse_case_against",
    "record_decline",
    "register_opportunities",
    "register_quote_check",
    "split_why",
    "strip_quotes",
    "summary_lines",
    "verify_quotes",
]
