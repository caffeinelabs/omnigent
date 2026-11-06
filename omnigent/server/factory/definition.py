"""The versioned per-factory definition (Tier 1): settings, loop budgets, autonomy and rules.

Each factory has immutable versions in ``factory_definitions``; the root label
``omnigent.factory.def_version`` pins the one in force. :func:`definition_for`
resolves it (5 s cache): every value comes from the pinned doc, then today's
env default, then the code default, and is clamped to the code bounds and env
ceilings. v1 is seeded lazily on the first resolve from today's effective env
values (and the root's legacy ``engine_mode``, ``autonomy_projects`` and
``engine_spawn`` labels), so seeding changes nothing.

Changes are JSON-patch-like ``{op, path, value}`` on an allowlist of paths.
:data:`CLASS_MAP` decides their approval class in code: ``A`` (cheaper or
safer: a peer lead co-signs) or ``B`` (owners). Any path outside the schema,
or a Tier-0 one, is refused with 422 ``tier0``. Rules are advisory text served
by ``sys_factory_rules``; they never widen a policy or a gate.

The product-agnostic blocks ``product``, ``strategy``, ``discovery``,
``briefs``, ``role_defaults`` and ``structure`` (:mod:`.definition_blocks`)
hold everything the factory knows about its product and how its cockpit
names the levels of work. Their lists with ids (goals, segments,
areas, sources) change item by item, like ``rules``. The ``team`` block
(:mod:`.team_spec`) holds each project's team, member by member.
"""

from __future__ import annotations

import copy
import functools
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from omnigent.factory import (
    CADENCE_DEMO,
    CADENCE_HEARTBEATS,
    ENGINE_MODES,
    LABEL_CADENCE,
    LABEL_DEF_VERSION,
    LABEL_DEFAULT_REPO,
    LABEL_ID,
    LABEL_PREFIX,
    MAX_HEARTBEAT_S,
    MIN_HEARTBEAT_S,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    ROLES,
)
from omnigent.server.factory import definition_blocks as blocks
from omnigent.server.factory import team_spec
from omnigent.server.factory.autonomy import AutonomyDef
from omnigent.server.factory.definition_blocks import (
    BriefsDef,
    DiscoveryDef,
    EnvironmentDef,
    ProductDef,
    RoleDefaultsDef,
    StrategyDef,
    StructureDef,
)
from omnigent.server.factory.team_spec import TeamDef
from omnigent.stores.factory_store import DefinitionVersionExists, canonical_hash, norm_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryDefinition
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.control import CockpitError
    from omnigent.server.factory.supervisor import SupervisorConfig

_logger = logging.getLogger(__name__)

CACHE_S: Final = 5.0
TIER0: Final = "tier0"
PREVIEW_DOMAIN_PATH: Final = "product.delivery.preview_domain"
"""Bounded by the deployment's preview domains (:func:`.env_urls.preview_domains`)."""
RULES_PER_ROLE: Final = 20
RULE_TEXT_MAX: Final = 500
RULE_ROLES: Final = (*sorted({*ROLES, "ic_codex"}), "*")
"""Roles a rule may target: the factory roles, the ``ic_codex`` agent and ``*``."""
TRIAL_H_DEFAULT: Final = 48
TRIAL_H_MIN: Final = 24
TRIAL_H_MAX: Final = 168
DISCOVERY_TRIAL_H: Final = (7 * 24, 14 * 24, 56 * 24)
"""A trial of discovery's method: ``(min, default, max)`` hours (7 to 56 days, 14 by default)."""
TRIAL_H_LIMIT: Final = DISCOVERY_TRIAL_H[2]
"""The longest trial a body may ask for (:func:`trial_hours` checks it per guard set)."""
GUARDS_DELIVERY: Final = "delivery"
GUARDS_DISCOVERY: Final = "discovery"
DISCOVERY_OFF: Final = "discovery off for this factory"
"""Why discovery's method does not trial in a factory with ``discovery.enabled`` off."""
UNDO_WINDOW_S: Final = 600
"""Re-pinning the parent of a version this young is an Undo (the version gets ``undone_by``)."""
LABEL_ENGINE_MODE: Final = f"{LABEL_PREFIX}engine_mode"
LABEL_AUTONOMY: Final = f"{LABEL_PREFIX}autonomy_projects"
LABEL_ENGINE_SPAWN: Final = f"{LABEL_PREFIX}engine_spawn"
LEGACY_LABELS: Final = (LABEL_ENGINE_MODE, LABEL_AUTONOMY, LABEL_ENGINE_SPAWN)
"""Root labels whose values move into the doc on the first resolve."""
_RULE_ID_RE: Final = re.compile(r"^R-([1-9][0-9]{0,6})$")
_EVIDENCE_RE: Final = re.compile(r"[A-Za-z0-9!#:_./-]{1,64}")
_RULE_EDIT_KEYS: Final = frozenset({"role", "project", "text"})


def _evidence_id(value: str) -> str:
    # One token: an id is quoted verbatim in server mail, so no spaces or newlines.
    if not _EVIDENCE_RE.fullmatch(value):
        raise ValueError(
            "an evidence id is one token like F-9, D-2, INC-1, !42 or #slug "
            "(letters, digits and !#:_./-, at most 64 chars)"
        )
    return value


EvidenceId = Annotated[str, AfterValidator(_evidence_id)]
"""An evidence reference of ``sys_factory_propose`` / ``sys_factory_retro``."""


# ── The document ───────────────────────────────────────────────


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EngineDef(_Block):
    mode: Literal["observe", "assist", "enforce"] = "assist"
    spawn: Literal["server", "lead"] = "server"
    next_hints: bool = True


class CadenceDef(_Block):
    """Heartbeat defaults in seconds (``0`` off, else 120..86400)."""

    lead: int = 3600
    project_lead: int = 7200
    demo_project_lead: int = 600


class SupervisorDef(_Block):
    stuck_s: int = 5400
    blocked_escalate_s: int = 1800
    quiet_beats: bool = True


class MailDef(_Block):
    sender_rate: int = 60
    pair_rate: int = 20


class PreviewsDef(_Block):
    """``ttl_h`` 0 turns parking off; ``*_probe_s`` 0 turns probing off."""

    ttl_h: float = 72.0
    max: int = 8
    env_probe_s: int = 60
    preview_probe_s: int = 300


class CapsDef(_Block):
    max_projects: int = 10


class ImplementLoop(_Block):
    attempts: int = 3
    usd: float = 15.0
    wall_h: float = 8
    no_progress_s: int = 3600


class ReviewLoop(_Block):
    rounds: int = 3


class VerifyLoop(_Block):
    per_head: int = 2
    env_retries: int = 1


class DemoLoop(_Block):
    per_head: int = 2
    per_pr_heads: int = 6


class CiLoop(_Block):
    flake_reruns: int = 1


class ClaimLoop(_Block):
    mismatches: int = 2
    wait_h: float = 2


class PlanLoop(_Block):
    stall_h: float = 24


class IntakeLoop(_Block):
    revisions: int = 3


class ImproveLoop(_Block):
    usd: float = 3.0


class LoopsDef(_Block):
    implement: ImplementLoop = Field(default_factory=ImplementLoop)
    review: ReviewLoop = Field(default_factory=ReviewLoop)
    verify: VerifyLoop = Field(default_factory=VerifyLoop)
    demo: DemoLoop = Field(default_factory=DemoLoop)
    ci: CiLoop = Field(default_factory=CiLoop)
    claim: ClaimLoop = Field(default_factory=ClaimLoop)
    plan: PlanLoop = Field(default_factory=PlanLoop)
    intake: IntakeLoop = Field(default_factory=IntakeLoop)
    improve: ImproveLoop = Field(default_factory=ImproveLoop)


class BudgetsDef(_Block):
    project_usd: float = 150.0
    factory_usd_per_day: float = 400.0
    usd_per_hour_alert: float = 15.0


class DecisionsDef(_Block):
    escalate_after_m: int = 30
    escalate_owners_after_m: int = 120


class ReviewCriterion(_Block):
    """
    One line of the done review's rubric: what counts as done.

    :param key: A short id the reviewer cites (``ask_covered``).
    :param blocking: A gap on it makes the verdict "not done", whatever its severity.
    """

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    text: str = Field(min_length=1, max_length=300)
    blocking: bool = False


DEFAULT_RUBRIC: Final[tuple[tuple[str, str, bool], ...]] = (
    (
        "ask_covered",
        "Everything the person asked for is built, or planned in a later wave with what it "
        "waits for; scope still parked means the feature is not done yet.",
        True,
    ),
    (
        "acceptance_passes",
        "The latest acceptance run on the base env passes every step; a step the env could "
        "not run is a gap unless a person accepted it.",
        True,
    ),
    (
        "reachable",
        "People can reach it: an entry point where users would look, behind its flag.",
        True,
    ),
    (
        "demos_pass",
        "Every demo of the feature's changes passes; PARTIAL or FAIL is a gap, with what "
        "was partial.",
        False,
    ),
    (
        "asks_settled",
        "No question about the feature's work is still open, and every answered one was "
        "carried out.",
        False,
    ),
    (
        "gaps_closed",
        "Every gap an agent raised (a finding, a failing check, an unfinished task) is fixed "
        "or has a follow-up.",
        False,
    ),
    (
        "flags_ready",
        "Each flag still off says what turning it on needs, and who provides it.",
        False,
    ),
)
"""The rubric a factory starts with: ``(key, text, blocking)``."""


def _default_rubric() -> list[ReviewCriterion]:
    return [ReviewCriterion(key=k, text=t, blocking=b) for k, t, b in DEFAULT_RUBRIC]


def _unique_keys(items: list[ReviewCriterion]) -> list[ReviewCriterion]:
    if len({c.key for c in items}) != len(items):
        raise ValueError("a rubric key is listed twice")
    return items


class DoneReviewDef(_Block):
    """
    How a feature's done review judges and who reviews it.

    :param rubric: What counts as done (owners edit it; agents propose changes).
    :param reviewer: ``sentinel`` (the lead that did not plan the work) or
        ``server`` (the server's own reading of the evidence).
    :param answer_h: Hours the reviewer has; then the server's evidence verdict stands.
    :param rounds: Reviews the server asks for per wave before it is a person's call.
    """

    rubric: Annotated[list[ReviewCriterion], AfterValidator(_unique_keys)] = Field(
        default_factory=_default_rubric, min_length=1, max_length=12
    )
    reviewer: Literal["sentinel", "server"] = "sentinel"
    answer_h: float = 4.0
    rounds: int = 3


class AlertsDef(_Block):
    """The detectors' thresholds (the Slack webhook is a secret: Tier 0)."""

    repeat_tool_n: int = 5
    repeat_window_m: int = 30
    quiet_m: int = 10
    ready_unreviewed_h: float = 4
    decision_waiting_m: int = 30
    mail_stuck_m: int = 10
    spend_window_m: int = 30
    pingpong_n: int = 6
    pingpong_window_m: int = 30
    orphan_h: float = 2
    gate_flaps: int = 3
    gate_flap_window_h: float = 2
    ask_unneeded_n: int = 3
    ask_unneeded_s: int = 120
    heartbeat_waste_usd: float = 5.0
    dead_ticks: int = 2


class RuleDef(_Block):
    """One advisory rule, e.g. ``R-3 · ic · "Run the package's unit tests before every push."``."""

    id: str
    role: str
    project: str = "*"
    text: str = Field(min_length=1, max_length=RULE_TEXT_MAX)
    origin: str | None = None
    state: Literal["trial", "active"] = "active"

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _RULE_ID_RE.match(value):
            raise ValueError("a rule id is R-<n>")
        return value

    @field_validator("role")
    @classmethod
    def _role(cls, value: str) -> str:
        if value not in RULE_ROLES:
            raise ValueError(f"role must be one of {', '.join(RULE_ROLES)}")
        return value

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        text = " ".join(value.split())
        if not text:
            raise ValueError("a rule needs text")
        return text


class FactoryDefinitionDoc(_Block):
    """The whole Tier-1 definition of one factory."""

    engine: EngineDef = Field(default_factory=EngineDef)
    cadence: CadenceDef = Field(default_factory=CadenceDef)
    supervisor: SupervisorDef = Field(default_factory=SupervisorDef)
    mail: MailDef = Field(default_factory=MailDef)
    previews: PreviewsDef = Field(default_factory=PreviewsDef)
    caps: CapsDef = Field(default_factory=CapsDef)
    loops: LoopsDef = Field(default_factory=LoopsDef)
    budgets: BudgetsDef = Field(default_factory=BudgetsDef)
    autonomy: AutonomyDef = Field(default_factory=AutonomyDef)
    decisions: DecisionsDef = Field(default_factory=DecisionsDef)
    alerts: AlertsDef = Field(default_factory=AlertsDef)
    rules: list[RuleDef] = Field(default_factory=list)
    rule_seq: int = 0
    product: ProductDef = Field(default_factory=ProductDef)
    environment: EnvironmentDef = Field(default_factory=EnvironmentDef)
    strategy: StrategyDef = Field(default_factory=StrategyDef)
    discovery: DiscoveryDef = Field(default_factory=DiscoveryDef)
    briefs: BriefsDef = Field(default_factory=BriefsDef)
    role_defaults: RoleDefaultsDef = Field(default_factory=RoleDefaultsDef)
    goal_seq: int = 0
    team: TeamDef = Field(default_factory=TeamDef)
    structure: StructureDef = Field(default_factory=StructureDef)
    done_review: DoneReviewDef = Field(default_factory=DoneReviewDef)

    @field_validator("rules")
    @classmethod
    def _rules(cls, rules: list[RuleDef]) -> list[RuleDef]:
        seen: set[str] = set()
        per_role: dict[str, int] = {}
        for rule in rules:
            if rule.id in seen:
                raise ValueError(f"duplicate rule id {rule.id}")
            seen.add(rule.id)
            per_role[rule.role] = per_role.get(rule.role, 0) + 1
            if per_role[rule.role] > RULES_PER_ROLE:
                raise ValueError(f"at most {RULES_PER_ROLE} rules for role {rule.role}")
        return rules


# ── Bounds and classes (Tier 0: code only) ─────────────────────


def _heartbeat_ok(value: float) -> bool:
    return value == 0 or MIN_HEARTBEAT_S <= value <= MAX_HEARTBEAT_S


BOUNDS: Final[dict[str, tuple[float, float]]] = {
    "cadence.lead": (0, MAX_HEARTBEAT_S),
    "cadence.project_lead": (0, MAX_HEARTBEAT_S),
    "cadence.demo_project_lead": (0, MAX_HEARTBEAT_S),
    "supervisor.stuck_s": (900, 14400),
    "supervisor.blocked_escalate_s": (900, 14400),
    "mail.sender_rate": (1, 600),
    "mail.pair_rate": (1, 200),
    "previews.ttl_h": (0, 720),
    "previews.max": (1, 32),
    "previews.env_probe_s": (0, 3600),
    "previews.preview_probe_s": (0, 3600),
    "caps.max_projects": (1, 12),
    "loops.implement.attempts": (1, 6),
    "loops.implement.usd": (0.5, 50),
    "loops.implement.wall_h": (1, 72),
    "loops.implement.no_progress_s": (600, 14400),
    "loops.review.rounds": (1, 6),
    "loops.verify.per_head": (1, 5),
    "loops.verify.env_retries": (0, 3),
    "loops.demo.per_head": (1, 5),
    "loops.demo.per_pr_heads": (1, 12),
    "loops.ci.flake_reruns": (0, 3),
    "loops.claim.mismatches": (1, 5),
    "loops.claim.wait_h": (1, 24),
    "loops.plan.stall_h": (1, 168),
    "loops.intake.revisions": (1, 6),
    "loops.improve.usd": (0.5, 10),
    "budgets.project_usd": (1, 1000),
    "budgets.factory_usd_per_day": (1, 5000),
    "budgets.usd_per_hour_alert": (1, 200),
    "decisions.escalate_after_m": (5, 240),
    "decisions.escalate_owners_after_m": (15, 1440),
    "alerts.repeat_tool_n": (2, 50),
    "alerts.repeat_window_m": (5, 240),
    "alerts.quiet_m": (2, 120),
    "alerts.ready_unreviewed_h": (0.5, 72),
    "alerts.decision_waiting_m": (5, 480),
    "alerts.mail_stuck_m": (2, 120),
    "alerts.spend_window_m": (5, 240),
    "alerts.pingpong_n": (2, 50),
    "alerts.pingpong_window_m": (5, 240),
    "alerts.orphan_h": (0.5, 48),
    "alerts.gate_flaps": (2, 20),
    "alerts.gate_flap_window_h": (0.5, 48),
    "alerts.ask_unneeded_n": (1, 20),
    "alerts.ask_unneeded_s": (30, 1800),
    "alerts.heartbeat_waste_usd": (0.5, 100),
    "alerts.dead_ticks": (1, 10),
    "strategy.mix.outcome": (0, 100),
    "strategy.mix.tests": (0, 100),
    "strategy.mix.enablers": (0, 100),
    "strategy.mix.hunches": (0, 100),
    "discovery.budgets.usd_per_day": (0, 1000),
    "discovery.budgets.per_ask": (0, 100),
    "discovery.cadence.sweep_h": (0, 168),
    "discovery.cadence.synthesis_h": (0, 720),
    "discovery.thresholds.min_accounts": (1, 100),
    "discovery.thresholds.bets_per_week": (0, 50),
    "discovery.thresholds.max_open_bets": (0, 50),
    "discovery.rubric.ladder.hunch": (0, 1),
    "discovery.rubric.ladder.weak": (0, 1),
    "discovery.rubric.ladder.moderate": (0, 1),
    "discovery.rubric.ladder.strong": (0, 1),
    "discovery.rubric.ladder.proven": (0, 1),
    "discovery.rubric.moderate_min_accounts": (1, 100),
    "discovery.rubric.moderate_min_source_types": (1, 10),
    "discovery.rubric.window_days": (7, 365),
    "discovery.rubric.per_account_cap": (1, 10),
    "discovery.rubric.decay_half_life_days": (7, 365),
    "discovery.rubric.counter_share_down": (0, 1),
    "discovery.rubric.reach_ratio.low": (1, 1000),
    "discovery.rubric.reach_ratio.high": (1, 1000),
    "discovery.rubric.reach_ref": (1, 10_000_000),
    "discovery.rubric.intensity.blocker": (0, 1),
    "discovery.rubric.intensity.workaround": (0, 1),
    "discovery.rubric.intensity.annoyance": (0, 1),
    "discovery.rubric.intensity.nice": (0, 1),
    "discovery.rubric.off_focus_factor": (0, 1),
    "discovery.rubric.bands.now": (0, 1),
    "discovery.rubric.bands.next": (0, 1),
    "discovery.rubric.map.value_high": (0, 1),
    "discovery.rubric.map.confidence_high": (0, 1),
    "discovery.rubric.human_minute_usd": (0, 100),
    "structure.tiles_max": blocks.TILES_MAX_BOUNDS,
    "done_review.answer_h": (0.25, 48),
    "done_review.rounds": (1, 6),
}
"""Code bounds of every numeric path (an env default outside them widens them)."""
ENV_CEILINGS: Final = frozenset(
    {"mail.sender_rate", "mail.pair_rate", "previews.ttl_h", "previews.max", "caps.max_projects"}
)
"""Paths the env value caps: the definition may only lower them."""

Klass = Literal["A", "B"]


def _lower_is_a(before: Any, after: Any) -> Klass:
    a, b = _num(after), _num(before)
    return "A" if a is not None and a <= (b or 0) else "B"


def _slower_is_a(before: Any, after: Any) -> Klass:
    a, b = _num(after), _num(before)
    if a is None or b is None or a == 0 or b == 0:
        return "B"
    return "A" if a >= b else "B"


def _always(klass: Klass) -> Callable[[Any, Any], Klass]:
    return lambda _before, _after: klass


_A_LOOP_KEYS: Final = frozenset({"attempts", "rounds", "per_head", "usd"})
CLASS_MAP: Final[dict[str, Callable[[Any, Any], Klass]]] = {
    "cadence.*": _slower_is_a,
    "loops.*.attempts": _lower_is_a,
    "loops.*.rounds": _lower_is_a,
    "loops.*.per_head": _lower_is_a,
    "loops.*.usd": _lower_is_a,
    "loops.*": _always("B"),
    "mail.*": _lower_is_a,
    "supervisor.*": _always("A"),
    "alerts.*": _always("A"),
    "decisions.*": _always("A"),
    "previews.*": _always("B"),
    "caps.*": _always("B"),
    "rules": _always("B"),
    "engine.*": _always("B"),
    "autonomy.*": _always("B"),
    # Lowering a budget only reduces spend: editors apply it at once (spec §8.4).
    "budgets.*": _lower_is_a,
    "models.*": _always("B"),
    "product.*": _always("B"),
    "environment.*": _always("B"),
    "strategy.*": _always("B"),
    "discovery.enabled": blocks.off_is_a,
    "discovery.sources.*": blocks.source_class,
    "discovery.budgets.*": _lower_is_a,
    "discovery.cadence.*": blocks.slower_or_off_is_a,
    "discovery.*": _always("B"),
    "briefs.*": _always("B"),
    "role_defaults.*": _always("B"),
    "structure.*": _always("B"),
    "done_review.*": _always("B"),
}
"""``path pattern -> (before, after) -> class``; the first matching pattern wins."""
CLASS_TABLE: Final = (
    ("cadence.*", "A when slower, else B (0 = off is B)"),
    ("loops.*.attempts|rounds|per_head", "A when lower, else B"),
    ("loops.*.usd", "A when lower (editors apply it at once), else B"),
    ("loops.* (other keys)", "B"),
    ("mail.*", "A when lower, else B"),
    ("supervisor.*, alerts.*, decisions.*", "A"),
    ("previews.*, caps.*", "B"),
    ("rules", "B (owners), as a trial"),
    ("engine.*", "B (owners)"),
    ("autonomy.*", "B (owners only: agents never propose it)"),
    ("budgets.*", "A when lower (editors apply it at once), else B (owners)"),
    ("product.*, strategy.*", "B (owners); never on trial, applies as a version"),
    ("environment.*", "B (owners only: agents never propose it); applies as a version"),
    ("discovery.enabled", "A when turning it off, else B (owners)"),
    ("discovery.sources.<id>", "A when it only narrows (pause, less budget, fewer roles), else B"),
    ("discovery.budgets.*", "A when lower (editors apply it at once), else B (owners)"),
    ("discovery.cadence.*", "A when slower or off, else B (owners)"),
    ("discovery.seat|rubric|thresholds|autonomy", "B (owners)"),
    ("discovery.*, briefs.scout", "never on trial until discovery guards exist"),
    ("briefs.*, role_defaults.*", "B (owners)"),
    ("structure.*", "B (owners only: agents never propose it); applies as a version"),
    ("done_review.*", "B (owners; agents propose the rubric); applies as a version"),
    ("team.templates.*", "B (owners); never on trial (it reaches a project on a switch)"),
    (
        "team.projects.<slug> members",
        "adding a member, a vendor or model change, a brief: B (owners); removing one or a "
        "new handle: A (editors apply it)",
    ),
    (
        "team.projects.<slug>.builders_at_once|budget_usd",
        "A when lower, else B (owners); never on trial (too few PRs per team)",
    ),
    ("owner-only paths, proposed by an agent", "B (owners), whatever their class"),
    ("anything else, Tier 0", "refused: 422 tier0"),
)
""":data:`CLASS_MAP` in words (the Definition panel shows it)."""
OWNER_ONLY_PREFIXES: Final = (
    "engine.",
    "autonomy.",
    "budgets.",
    "models.",
    "product.",
    "environment.",
    "strategy.",
    "discovery.",
    "briefs.",
    "role_defaults.",
    "structure.",
)
"""Only owners apply these: an agent's proposal of one always waits for them (class B)."""
TIER0_PATHS: Final = frozenset(
    {"alerts.slack_webhook", "alerts.slack_bot_token", "rule_seq", "goal_seq"}
)
TIER0_REASONS: Final[dict[str, str]] = {
    "discovery.rubric.formula": "the rubric's formula is code (Tier 0); its numbers are data",
    "goal_seq": "goal numbers are handed out by the server (Tier 0)",
    "rule_seq": "rule numbers are handed out by the server (Tier 0)",
}
QUIET_BLOCKS: Final = blocks.BLOCKS
"""Blocks whose readers fetch them as data: their changes are not mailed to members."""


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _pattern_match(pattern: str, path: str) -> bool:
    want, have = pattern.split("."), path.split(".")
    if want[-1] == "*":
        # A trailing ``*`` matches one or more segments: ``discovery.*`` covers
        # ``discovery.rubric.ladder.weak``.
        head = want[:-1]
        return len(have) > len(head) and all(
            w == "*" or w == h for w, h in zip(head, have[: len(head)], strict=True)
        )
    if len(want) != len(have):
        return False
    return all(w == "*" or w == h for w, h in zip(want, have, strict=True))


def classify(path: str, before: Any, after: Any) -> Klass:
    """:returns: The approval class of one change (``CLASS_MAP``; unknown paths are ``B``)."""
    if path.startswith("rules"):
        return "B"
    if team_spec.is_team_path(path):
        return team_spec.classify(path, before, after)
    for pattern, rule in CLASS_MAP.items():
        if _pattern_match(pattern, path):
            return rule(before, after)
    return "B"


BUDGET_PREFIXES: Final = ("budgets.", "discovery.budgets.")
"""Dollar budgets a person lowers at once: lowering one only reduces spend (spec §8.4)."""
_LOOP_USD_RE: Final = re.compile(r"^loops\.[a-z_]+\.usd$")


def is_budget_path(path: str) -> bool:
    """
    Whether *path* is a dollar budget: the factory's and discovery's
    (:data:`BUDGET_PREFIXES`) or a loop's (``loops.<loop>.usd``, e.g. the
    per-task default).
    """
    return path.startswith(BUDGET_PREFIXES) or _LOOP_USD_RE.match(path) is not None


def budgets_lower_only(entries: Iterable[Mapping[str, Any]]) -> bool:
    """
    Whether diff *entries* (``{path, before, after}``) only lower budgets:
    each is a :func:`is_budget_path` that :func:`classify` calls A (a lower
    value). Editors apply those at once, with Undo; raising one, or setting a
    cap that was unset, stays the owners' (class B).
    """
    found = list(entries)
    return bool(found) and all(
        is_budget_path(str(e.get("path") or ""))
        and classify(str(e.get("path") or ""), e.get("before"), e.get("after")) == "A"
        for e in found
    )


def owner_only(path: str) -> bool:
    """Whether only owners may apply *path* (agents only propose it)."""
    return any(path.startswith(prefix) for prefix in OWNER_ONLY_PREFIXES)


def _leaf_paths(model: type[BaseModel], prefix: str = "") -> list[str]:
    out: list[str] = []
    for name, info in model.model_fields.items():
        annotation = info.annotation
        if not prefix and name == team_spec.TEAM:
            continue  # Changed member by member (team_spec), never as plain paths.
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            out += _leaf_paths(annotation, f"{prefix}{name}.")
        elif name not in ("rules", "rule_seq", "goal_seq"):
            out.append(f"{prefix}{name}")
    return out


ALLOWED_PATHS: Final = frozenset(_leaf_paths(FactoryDefinitionDoc))
"""Every path a change may replace: scalars, and whole lists (plus ``rules``)."""
LIST_PATHS: Final = frozenset(blocks.LISTS)
"""Lists of :data:`.definition_blocks.LISTS` (diffed and changed item by item)."""


# ── Env defaults and the seed ──────────────────────────────────


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def env_defaults() -> dict[str, Any]:
    """:returns: Today's effective values of the env-backed paths (code defaults when unset)."""
    from omnigent.server.factory import mailer, previews, quiet_beats
    from omnigent.server.factory.api.agent_state import max_previews
    from omnigent.server.factory.background import env_prober
    from omnigent.server.factory.start import max_projects

    sender, pair = mailer.rate_limits_from_env()
    ttl = previews.preview_ttl_h()
    stuck = _env_float("OMNIGENT_FACTORY_STUCK_S")
    blocked = _env_float("OMNIGENT_FACTORY_BLOCKED_ESCALATE_S")
    env_probe = _env_float(env_prober.ENV_PROBE_ENV)
    preview_probe = _env_float(env_prober.PREVIEW_PROBE_ENV)
    return {
        "supervisor.stuck_s": int(stuck) if stuck and stuck > 0 else 5400,
        "supervisor.blocked_escalate_s": int(blocked) if blocked and blocked > 0 else 1800,
        "supervisor.quiet_beats": quiet_beats.quiet_beats_enabled(),
        "mail.sender_rate": sender,
        "mail.pair_rate": pair,
        "previews.ttl_h": float(ttl) if ttl is not None else 0.0,
        "previews.max": max_previews(),
        "previews.env_probe_s": int(env_probe)
        if env_probe is not None
        else int(env_prober.DEFAULT_ENV_PROBE_S),
        "previews.preview_probe_s": int(preview_probe)
        if preview_probe is not None
        else int(env_prober.DEFAULT_PREVIEW_PROBE_S),
        "caps.max_projects": max_projects(),
    }


def _item_index(items: list[Any], item_id: str) -> int | None:
    return next(
        (i for i, x in enumerate(items) if isinstance(x, Mapping) and x.get("id") == item_id),
        None,
    )


def get_path(doc: Mapping[str, Any], path: str) -> Any:
    """
    :returns: The value at a dotted *path* of *doc*, or ``None``. A part after
        a list names an item by id (``strategy.goals.G-2``).
    """
    node: Any = doc
    for part in path.split("."):
        if isinstance(node, list):
            index = _item_index(node, part)
            if index is None:
                return None
            node = node[index]
        elif isinstance(node, Mapping) and part in node:
            node = node[part]
        else:
            return None
    return node


def set_path(doc: dict[str, Any], path: str, value: Any) -> None:
    """
    Set a dotted *path* of *doc* in place, creating blocks on the way. On a
    list item (``strategy.goals.G-2``) a mapping replaces or appends the item
    and ``None`` removes it.
    """
    parts = path.split(".")
    node: Any = doc
    for part in parts[:-1]:
        if isinstance(node, list):
            index = _item_index(node, part)
            if index is None:
                return
            node = node[index]
            continue
        child = node.get(part)
        if not isinstance(child, dict | list):
            child = {}
            node[part] = child
        node = child
    last = parts[-1]
    if isinstance(node, list):
        index = _item_index(node, last)
        if value is None:
            if index is not None:
                node.pop(index)
        elif index is None:
            node.append({**value, "id": last})
        else:
            node[index] = {**value, "id": last}
        return
    node[last] = value


SPARSE_KEYS: Final = (*blocks.BLOCKS, "goal_seq", team_spec.TEAM, "done_review")
"""Keys a version stores only once they differ from their defaults (see :func:`stored_form`):
a server from before one of them still reads every version that never changed it."""
SPARSE_FIELDS: Final = (
    "product.delivery.auto_merge",
    "product.delivery.auto_merge_scopes",
    "autonomy.level",
    "autonomy.scopes",
    "structure.node_header",
)
"""Fields inside a stored block left out while they hold their defaults, so a server
from before them still reads the block (see :func:`stored_form`)."""


@functools.cache
def _code_defaults() -> dict[str, Any]:
    return FactoryDefinitionDoc().model_dump(mode="json")


def stored_form(doc: Mapping[str, Any]) -> dict[str, Any]:
    """
    :returns: *doc* (validated) as a version stores it. The blocks of
        :mod:`.definition_blocks` and ``goal_seq`` are left out while they hold
        their defaults, and role defaults that set only the old model aliases
        are kept as ``models``. A server from before those blocks then still
        reads every version of a factory that has not used them (a rollback
        keeps working); every reader fills the defaults back in.
    """
    defaults = _code_defaults()
    out = {k: v for k, v in doc.items() if not (k in SPARSE_KEYS and v == defaults.get(k))}
    return blocks.downgrade(_without_default_fields(out, defaults))


def _without_default_fields(doc: Mapping[str, Any], defaults: Mapping[str, Any]) -> dict[str, Any]:
    """:returns: *doc* without the :data:`SPARSE_FIELDS` that hold their defaults."""
    out = dict(doc)
    for path in SPARSE_FIELDS:
        *parents, leaf = path.split(".")
        node = out
        for key in parents:
            child = node.get(key)
            if not isinstance(child, Mapping):
                break
            # Copied on the way down: the caller's doc stays as it was.
            node[key] = node = dict(child)
        else:
            if leaf in node and node[leaf] == get_path(defaults, path):
                del node[leaf]
    return out


def _with_defaults(stored: Mapping[str, Any]) -> dict[str, Any]:
    """:returns: *stored* over the defaults, its legacy blocks moved (see ``blocks.upgrade``)."""
    return _merge(default_doc(), blocks.upgrade(dict(stored)))


def default_doc() -> dict[str, Any]:
    """:returns: The code defaults with today's env values: what v1 is seeded with."""
    from omnigent.server.factory import loops

    doc = FactoryDefinitionDoc().model_dump(mode="json")
    for path, value in env_defaults().items():
        set_path(doc, path, value)
    for kind, keys in loops.DEFAULT_BUDGETS.items():
        for key, value in keys.items():
            path = f"budgets.{key}" if kind == "budgets" else f"loops.{kind}.{key}"
            if path in ALLOWED_PATHS:
                set_path(doc, path, value)
    doc["cadence"] = {
        "lead": CADENCE_HEARTBEATS["normal"][ROLE_LEAD],
        "project_lead": CADENCE_HEARTBEATS["normal"][ROLE_PROJECT_LEAD],
        "demo_project_lead": CADENCE_HEARTBEATS[CADENCE_DEMO][ROLE_PROJECT_LEAD],
    }
    return doc


def seed_doc(labels: Mapping[str, str]) -> dict[str, Any]:
    """
    :returns: The v1 doc for a root with *labels*: its legacy labels migrated,
        and the deployment's delivery and environment settings for its default
        repo (if any) as its ``product.delivery`` and ``environment``.
    """
    doc = default_doc()
    mode = (labels.get(LABEL_ENGINE_MODE) or "").strip()
    if mode in ENGINE_MODES:
        doc["engine"]["mode"] = mode
    autonomy = (labels.get(LABEL_AUTONOMY) or "").strip()
    if autonomy in ("confirm", "requested", "grace"):
        doc["autonomy"]["projects"] = autonomy
    spawn = (labels.get(LABEL_ENGINE_SPAWN) or "").strip()
    if spawn in ("server", "lead"):
        doc["engine"]["spawn"] = spawn
    from omnigent.server.factory.product_profile import seed_into

    # The deployment's profile seeds for this factory's repo (none for another repo).
    return seed_into(doc, labels.get(LABEL_DEFAULT_REPO))


# ── Resolution ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Resolved:
    """
    The definition in force for one factory.

    :param doc: The effective doc: stored values over env and code defaults, clamped.
    :param stored: The doc as stored in the pinned version.
    """

    factory_id: str
    version: int
    doc: dict[str, Any]
    stored: dict[str, Any]
    hash: str
    created_via: str
    created_by: str
    proposal_id: str | None = None

    def get(self, path: str, default: Any = None) -> Any:
        """:returns: The effective value at *path*."""
        value = get_path(self.doc, path)
        return default if value is None else value


_cache: dict[str, tuple[float, Resolved]] = {}
_lock = threading.Lock()
_deps: FactoryDeps | None = None


def invalidate(factory_id: str | None = None) -> None:
    """Forget the cached definition of *factory_id* (all factories when ``None``)."""
    with _lock:
        if factory_id is None:
            _cache.clear()
        else:
            _cache.pop(_key(factory_id), None)


def _key(factory_id: str) -> str:
    try:
        return norm_id(factory_id)
    except ValueError:
        return factory_id


def bounds(path: str, env: Mapping[str, Any] | None = None) -> tuple[float, float] | None:
    """:returns: The clamp range of *path*: code bounds, widened or capped by the env value."""
    code = BOUNDS.get(path)
    if code is None:
        return None
    lo, hi = code
    value = _num((env or {}).get(path))
    if value is None:
        return lo, hi
    if path in ENV_CEILINGS:
        return min(lo, value), value
    return min(lo, value), max(hi, value)


def _clamp(doc: dict[str, Any], env: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    for path in BOUNDS:
        value = _num(get_path(out, path))
        rng = bounds(path, env)
        if value is None or rng is None:
            continue
        clamped = min(max(value, rng[0]), rng[1])
        if path.startswith("cadence.") and not _heartbeat_ok(clamped):
            clamped = MIN_HEARTBEAT_S
        if clamped != value:
            original = get_path(out, path)
            set_path(out, path, type(original)(clamped) if isinstance(original, int) else clamped)
    for path in ("previews.env_probe_s", "previews.preview_probe_s", "previews.ttl_h"):
        if not env.get(path):
            # Off in the env stays off: the definition only tunes what runs.
            set_path(out, path, env.get(path) or 0)
    return out


def _merge(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def full_doc(stored: Mapping[str, Any]) -> dict[str, Any]:
    """:returns: *stored* completed with the defaults and validated (not clamped)."""
    return FactoryDefinitionDoc.model_validate(_with_defaults(stored)).model_dump(mode="json")


def effective(stored: Mapping[str, Any]) -> dict[str, Any]:
    """:returns: *stored* over env and code defaults, validated (bad blocks fall back), clamped."""
    env = env_defaults()
    base = default_doc()
    stored = blocks.upgrade(dict(stored))
    merged = _merge(base, stored)
    try:
        doc = FactoryDefinitionDoc.model_validate(merged).model_dump(mode="json")
    except ValidationError:
        _logger.warning("factory definition: stored doc is invalid; bad blocks use defaults")
        doc = FactoryDefinitionDoc.model_validate(base).model_dump(mode="json")
        for key, block in stored.items():
            candidate = _merge(doc, {key: block})
            try:
                doc = FactoryDefinitionDoc.model_validate(candidate).model_dump(mode="json")
            except ValidationError:
                continue
    return _clamp(doc, env)


def _is_root(conv: Conversation | None) -> bool:
    if conv is None:
        return False
    fid = conv.labels.get(LABEL_ID)
    return bool(fid) and _key(fid) == _key(conv.id)


def _seed(deps: FactoryDeps, root: Conversation) -> FactoryDefinition:
    store = deps.factory_store
    doc = stored_form(seed_doc(root.labels))
    try:
        row = store.put_definition(
            root.id, doc=doc, created_by="server", created_via="seed", version=1
        )
    except DefinitionVersionExists as exc:
        # Another resolve seeded it first.
        row = exc.definition
    deps.conversation_store.set_labels(root.id, {LABEL_DEF_VERSION: str(row.version)})
    for label in LEGACY_LABELS:
        if label in root.labels:
            deps.conversation_store.delete_label(root.id, label)
    _logger.info("factory %s: definition v1 seeded", root.id)
    return row


def _pinned(deps: FactoryDeps, root: Conversation) -> FactoryDefinition:
    store = deps.factory_store
    raw = (root.labels.get(LABEL_DEF_VERSION) or "").strip()
    row = store.get_definition(root.id, int(raw)) if raw.isdigit() else None
    if row is not None:
        return row
    latest = store.latest_definition(root.id)
    if latest is None:
        return _seed(deps, root)
    deps.conversation_store.set_labels(root.id, {LABEL_DEF_VERSION: str(latest.version)})
    return latest


def definition_for(deps: FactoryDeps, factory_id: str, *, fresh: bool = False) -> Resolved | None:
    """
    The definition in force for *factory_id* (synchronous; 5 s cache).

    Seeds v1 on the first call for a factory.

    :returns: ``None`` when *factory_id* is not a factory root.
    """
    key = _key(factory_id)
    now = time.monotonic()
    if not fresh:
        with _lock:
            hit = _cache.get(key)
        if hit is not None and now - hit[0] < CACHE_S:
            return hit[1]
    root = deps.conversation_store.get_conversation(factory_id)
    if not _is_root(root):
        return None
    assert root is not None
    row = _pinned(deps, root)
    stored = dict(row.doc or {})
    resolved = Resolved(
        factory_id=root.id,
        version=row.version,
        doc=effective(stored),
        stored=stored,
        hash=row.hash or canonical_hash(stored),
        created_via=row.created_via,
        created_by=row.created_by,
        proposal_id=row.proposal_id,
    )
    with _lock:
        _cache[key] = (now, resolved)
    return resolved


def value(deps: FactoryDeps | None, factory_id: str | None, path: str, default: Any = None) -> Any:
    """:returns: The effective value at *path* for the factory, or *default*. Never raises."""
    deps = deps or _deps
    if deps is None or not factory_id:
        return default
    try:
        resolved = definition_for(deps, factory_id)
    except Exception:  # noqa: BLE001 - callers fall back to their defaults
        _logger.warning("factory definition: could not resolve %s", path, exc_info=True)
        return default
    return resolved.get(path, default) if resolved is not None else default


# ── Writing versions ───────────────────────────────────────────


def store_version(
    deps: FactoryDeps,
    factory_id: str,
    doc: Mapping[str, Any],
    *,
    created_by: str,
    created_via: str,
    parent_version: int | None,
    proposal_id: str | None = None,
    pin_it: bool = True,
) -> FactoryDefinition:
    """Store *doc* as the next version and pin it. :returns: The new version."""
    validated = FactoryDefinitionDoc.model_validate(blocks.upgrade(dict(doc))).model_dump(
        mode="json"
    )
    # Carry the highest rule and goal numbers forward, so a version built on a
    # re-pinned older one never hands out an id another version already used.
    validated["rule_seq"] = max(validated["rule_seq"], rule_floor(deps, factory_id))
    validated["goal_seq"] = max(validated["goal_seq"], goal_floor(deps, factory_id))
    row = deps.factory_store.put_definition(
        factory_id,
        doc=stored_form(validated),
        created_by=created_by,
        created_via=created_via,
        parent_version=parent_version,
        proposal_id=proposal_id,
    )
    if pin_it:
        pin(deps, factory_id, row.version)
    return row


def set_values(
    deps: FactoryDeps,
    factory_id: str,
    values: Mapping[str, Any],
    *,
    created_by: str,
    created_via: str = "human",
) -> FactoryDefinition:
    """Store the version in force with *values* (dotted paths) replaced, and pin it."""
    resolved = definition_for(deps, factory_id, fresh=True)
    if resolved is None:
        raise ValueError(f"{factory_id} is not a factory root")
    doc = full_doc(resolved.stored)
    for path, found in values.items():
        set_path(doc, path, found)
    return store_version(
        deps,
        factory_id,
        doc,
        created_by=created_by,
        created_via=created_via,
        parent_version=resolved.version,
    )


def pin(deps: FactoryDeps, factory_id: str, version: int) -> None:
    """Pin *version* on the factory root (the label the resolver reads)."""
    deps.conversation_store.set_labels(factory_id, {LABEL_DEF_VERSION: str(version)})
    invalidate(factory_id)


# ── Changes ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Change:
    """
    One validated change of a definition doc.

    :param path: Dotted path, e.g. ``"cadence.project_lead"``, ``"rules"`` or a
        list of :data:`LIST_PATHS` such as ``"strategy.goals"``.
    :param op: ``replace`` / ``add`` / ``remove``.
    :param rule_id: The rule a ``rules`` change adds, replaces or removes.
    :param item_id: The item of a list a change adds, replaces or removes
        (``None`` with ``replace``: the whole list).
    """

    op: str
    path: str
    value: Any = None
    rule_id: str | None = None
    item_id: str | None = None

    @property
    def full_path(self) -> str:
        """:returns: The path with its rule or item, e.g. ``"strategy.goals.G-2"``."""
        found = self.rule_id or self.item_id
        return f"{self.path}.{found}" if found else self.path


def normalize_path(raw: str) -> str:
    """:returns: ``"/cadence/project_lead"`` or ``"cadence.project_lead"`` as the dotted form."""
    return canonical_path(raw.strip().lstrip("/").replace("/", "."))


def canonical_path(path: str) -> str:
    """:returns: *path*, with a legacy alias (``models.ic``) as the path it moved to."""
    return blocks.LEGACY_MODELS.get(path, path)


def _error(status: int, code: str, message: str, **extra: Any) -> CockpitError:
    from omnigent.server.factory.control import CockpitError

    return CockpitError(status, code, message, **extra)


def tier0(message: str, **extra: Any) -> CockpitError:
    """:returns: The 422 ``tier0`` refusal."""
    return _error(422, TIER0, message, **extra)


def parse_change(raw: Mapping[str, Any], index: int = 0) -> Change:
    """
    Validate one ``{op, path, value}``.

    :raises CockpitError: 422 ``tier0`` for a path outside the schema or in Tier 0;
        400 ``invalid_body`` for a bad op.
    """
    op = str(raw.get("op") or "replace")
    path = normalize_path(str(raw.get("path") or ""))
    field = f"changes[{index}]"
    if op not in ("replace", "add", "remove"):
        raise _error(400, "invalid_body", f"{field}.op must be replace, add or remove")
    if path in TIER0_REASONS:
        raise tier0(f"{path}: {TIER0_REASONS[path]}", field=field)
    if path in TIER0_PATHS or not path:
        raise tier0(f"{path or 'that path'} is not part of the factory definition", field=field)
    listed = blocks.list_of(path)
    if listed is not None:
        return _parse_list_change(op, path, raw.get("value"), listed, field)
    if team_spec.is_team_path(path):
        try:
            parsed = team_spec.parse(op, path, raw.get("value"))
        except team_spec.TeamError as exc:
            raise _team_error(exc, field) from None
        return Change(parsed[0], parsed[1], parsed[2], item_id=parsed[3])
    if path == "rules" or path.startswith("rules."):
        rest = path.removeprefix("rules").lstrip(".")
        rule_id = rest.split(".", 1)[0] or None
        if op == "add" and rule_id is None:
            return Change("add", "rules", raw.get("value"))
        if op in ("replace", "remove") and rule_id and _RULE_ID_RE.match(rule_id):
            return Change(op, "rules", raw.get("value"), rule_id=rule_id)
        raise _error(
            400,
            "invalid_body",
            f"{field}: add a rule at 'rules', replace or remove one at 'rules/R-<n>'",
            field=field,
        )
    if path not in ALLOWED_PATHS:
        raise tier0(
            f"{path} is not a Tier-1 setting (guardrails, gates, metric definitions and "
            "secrets are Tier 0)",
            field=field,
        )
    if op != "replace":
        raise _error(400, "invalid_body", f"{field}: settings are replaced, not {op}")
    _check_bounds(path, raw.get("value"), field)
    return Change("replace", path, raw.get("value"))


def _team_error(exc: team_spec.TeamError, field: str | None = None) -> CockpitError:
    """:returns: *exc* as the cockpit's error (``tier0`` refusals keep their code)."""
    return _error(exc.status, exc.code, exc.message, field=field, **exc.extra)


def _check_team(
    before: Mapping[str, Any], after: Mapping[str, Any], facts: team_spec.FactsLookup | None
) -> None:
    """
    Tier 0 for team changes: no gate loses its last member, every template has
    the roles every project needs, no template a team names is removed, no team
    for an unknown project.

    :raises CockpitError: 422 ``gate_requires_role`` (all refusals in ``blocked``),
        409 ``in_use``, 404 ``unknown_project``.
    """
    refused = [
        *team_spec.gate_refusals(before, after, facts),
        *team_spec.template_refusals(before, after),
    ]
    if refused:
        first = refused[0]
        raise _error(
            422,
            "gate_requires_role",
            first["message"],
            field=first["path"],
            gate=first["gate"],
            blocked=refused,
        )
    in_use = team_spec.templates_in_use(before, after)
    if in_use:
        template_id, slug = in_use[0]
        raise _error(
            409,
            "in_use",
            f"template {template_id} is the template of {slug}'s team: switch "
            f"{', '.join(sorted({s for t, s in in_use if t == template_id}))} to another first",
            field=f"{team_spec.TEMPLATES}.{template_id}",
            item=template_id,
        )
    unknown = team_spec.unknown_projects(before, after, facts)
    if unknown:
        raise _error(
            404,
            "unknown_project",
            f"no project {unknown[0]} in this factory",
            field=f"{team_spec.PROJECTS}.{unknown[0]}",
        )


def _parse_list_change(
    op: str,
    path: str,
    value: Any,
    listed: tuple[blocks.ListSpec, str | None, str | None],
    field: str,
) -> Change:
    """A change of a list with ids: add at the list, replace or remove one item (or a field)."""
    spec, item_id, item_field = listed
    if item_id is not None and not blocks.item_id_ok(spec, item_id):
        raise _error(400, "invalid_body", f"{field}: {item_id!r} is not an id of {spec.path}")
    if item_field is not None:
        if blocks.has_secret({item_field: value}):
            raise tier0(f"{path}: {blocks.SECRET_REFUSAL}", field=field)
        if op != "replace" or item_field == "id":
            raise _error(400, "invalid_body", f"{field}: an item's field is replaced, never {op}")
        return Change("replace", spec.path, {item_field: value}, item_id=item_id)
    if op == "add":
        if spec.numbered and item_id is not None:
            raise _error(
                400,
                "invalid_body",
                f"{field}: add a {spec.prefix}-<n> item at {spec.path}; the server numbers it",
            )
        if isinstance(value, Mapping) and blocks.has_secret(value):
            raise tier0(f"{path}: {blocks.SECRET_REFUSAL}", field=field)
        return Change("add", spec.path, value, item_id=item_id)
    if item_id is None:
        if op == "remove" or spec.numbered:
            raise _error(
                400,
                "invalid_body",
                f"{field}: add an item at {spec.path}, replace or remove one at {spec.path}/<id>",
            )
        if blocks.has_secret(value):
            raise tier0(f"{path}: {blocks.SECRET_REFUSAL}", field=field)
        return Change("replace", spec.path, value)
    if op == "replace" and isinstance(value, Mapping) and blocks.has_secret(value):
        raise tier0(f"{path}: {blocks.SECRET_REFUSAL}", field=field)
    return Change(op, spec.path, value, item_id=item_id)


def _check_bounds(path: str, value: Any, field: str) -> None:
    """A proposed value stays within the code bounds (and under an env ceiling)."""
    rng = BOUNDS.get(path)
    number = _num(value)
    if rng is None or number is None:
        return
    lo, hi = rng
    if path in ENV_CEILINGS:
        ceiling = _num(env_defaults().get(path))
        hi = min(hi, ceiling) if ceiling is not None else hi
    if not lo <= number <= hi or (path.startswith("cadence.") and not _heartbeat_ok(number)):
        raise _error(
            422,
            "out_of_bounds",
            f"{path} must be within {lo:g}..{hi:g}"
            + (" (0 turns heartbeats off)" if path.startswith("cadence.") else ""),
            field=field,
        )


def rule_seq(doc: Mapping[str, Any]) -> int:
    """:returns: The highest rule number *doc* has used (its ``rule_seq`` or a rule id)."""
    numbers = [
        int(m.group(1))
        for r in doc.get("rules") or []
        if isinstance(r, Mapping) and (m := _RULE_ID_RE.match(str(r.get("id"))))
    ]
    return max([int(doc.get("rule_seq") or 0), *numbers], default=0)


def next_rule_id(doc: Mapping[str, Any]) -> str:
    """:returns: The next free ``R-<n>`` of *doc*."""
    return f"R-{rule_seq(doc) + 1}"


def rule_floor(deps: FactoryDeps, factory_id: str) -> int:
    """:returns: The highest rule number any stored version used (``0`` when none)."""
    latest = deps.factory_store.latest_definition(factory_id)
    return rule_seq(latest.doc or {}) if latest is not None else 0


_GOALS: Final = blocks.LISTS["strategy.goals"]


def goal_seq(doc: Mapping[str, Any]) -> int:
    """:returns: The highest goal number *doc* has used (its ``goal_seq`` or a goal id)."""
    goals = get_path(doc, _GOALS.path)
    return blocks.seq_of(dict(doc), _GOALS, list(goals) if isinstance(goals, list) else [])


def goal_floor(deps: FactoryDeps, factory_id: str) -> int:
    """:returns: The highest goal number any stored version used (``0`` when none)."""
    latest = deps.factory_store.latest_definition(factory_id)
    return goal_seq(latest.doc or {}) if latest is not None else 0


def _apply_list(doc: dict[str, Any], change: Change) -> None:
    """Add, replace (merge its fields) or remove one item of a list with ids, or the whole list."""
    spec = blocks.LISTS[change.path]
    found = get_path(doc, change.path)
    items: list[dict[str, Any]] = [dict(x) for x in found] if isinstance(found, list) else []
    if change.op == "replace" and change.item_id is None:
        if not isinstance(change.value, list):
            raise _error(422, "invalid_definition", f"{spec.path}: a list of items")
        set_path(doc, spec.path, [dict(x) if isinstance(x, Mapping) else x for x in change.value])
        return
    if change.op != "remove" and not isinstance(change.value, Mapping):
        raise _error(
            422, "invalid_definition", f"{change.full_path}: an item is an object", field="value"
        )
    body = dict(change.value) if isinstance(change.value, Mapping) else {}
    if change.op == "add":
        if spec.numbered:
            number = blocks.seq_of(doc, spec, items) + 1
            item_id = f"{spec.prefix}-{number}"
            doc[spec.seq or ""] = number
        else:
            item_id = change.item_id or str(body.get("id") or "")
            if not blocks.item_id_ok(spec, item_id):
                raise _error(
                    422, "invalid_definition", f"{spec.path}: an item needs an id like 'beta'"
                )
            if _item_index(items, item_id) is not None:
                raise _error(
                    409, "duplicate_item", f"{spec.path} already has {item_id}", item=item_id
                )
        items.append({**body, "id": item_id})
    else:
        index = _item_index(items, change.item_id or "")
        if index is None:
            raise _error(
                404, "unknown_item", f"no {change.item_id} in {spec.path}", item=change.item_id
            )
        if change.op == "remove":
            items.pop(index)
        else:
            items[index] = {**items[index], **body, "id": change.item_id}
    set_path(doc, spec.path, items)


def apply_changes(
    stored: Mapping[str, Any],
    changes: Sequence[Change],
    *,
    origin: str | None = None,
    rule_state: str = "active",
    min_rule_seq: int = 0,
    min_goal_seq: int = 0,
    facts: team_spec.FactsLookup | None = None,
) -> dict[str, Any]:
    """
    :param min_rule_seq: Added rules number past this (see :func:`rule_floor`).
    :param min_goal_seq: Added goals number past this (see :func:`goal_floor`).
    :param facts: The factory's projects (``slug -> env and previews``) for the
        team's Tier-0 checks; ``None`` assumes env and previews on.
    :returns: A new stored doc with *changes* applied (validated by the model).
    :raises CockpitError: 422 ``invalid_definition`` when the result does not validate,
        404 ``unknown_rule`` / ``unknown_item`` for a missing rule or list item,
        409 ``duplicate_item`` when an added item's id is taken, 422
        ``gate_requires_role`` when a team loses the last member a gate needs.
    """
    doc = _with_defaults(stored)
    preview_before = get_path(doc, PREVIEW_DOMAIN_PATH)
    teams = any(team_spec.is_team_path(c.path) for c in changes)
    before = copy.deepcopy(doc) if teams else doc
    doc["rule_seq"] = max(rule_seq(doc), min_rule_seq)
    doc["goal_seq"] = max(goal_seq(doc), min_goal_seq)
    rules: list[dict[str, Any]] = [dict(r) for r in doc.get("rules") or []]
    for change in changes:
        if change.path in LIST_PATHS:
            _apply_list(doc, change)
            continue
        if team_spec.is_project_path(change.path):
            try:
                team_spec.apply(doc, change.op, change.path, change.value, change.item_id)
            except team_spec.TeamError as exc:
                raise _team_error(exc, change.full_path) from None
            continue
        if change.path != "rules":
            set_path(doc, change.path, change.value)
            continue
        if change.op == "add":
            body = dict(change.value) if isinstance(change.value, Mapping) else {}
            rule_id = next_rule_id({**doc, "rules": rules})
            rules.append(
                {
                    "id": rule_id,
                    "role": body.get("role"),
                    "project": body.get("project") or "*",
                    "text": body.get("text"),
                    "origin": origin,
                    "state": rule_state,
                }
            )
            doc["rule_seq"] = int(rule_id.removeprefix("R-"))
            continue
        index = next((i for i, r in enumerate(rules) if r.get("id") == change.rule_id), None)
        if index is None:
            raise _error(404, "unknown_rule", f"no rule {change.rule_id} in the definition")
        if change.op == "remove":
            rules.pop(index)
        else:
            raw = change.value if isinstance(change.value, Mapping) else {}
            body = {k: v for k, v in raw.items() if k in _RULE_EDIT_KEYS}
            if "project" in body:
                body["project"] = body["project"] or "*"
            rules[index] = {
                **rules[index],
                **body,
                "id": change.rule_id,
                "origin": origin,
                "state": rule_state,
            }
    doc["rules"] = rules
    try:
        after = FactoryDefinitionDoc.model_validate(doc).model_dump(mode="json")
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise _error(
            422,
            "invalid_definition",
            f"{where}: {first.get('msg', 'invalid value')}" if where else str(exc)[:300],
            field=where or None,
        ) from None
    _check_preview_domain(preview_before, get_path(after, PREVIEW_DOMAIN_PATH))
    if teams:
        _check_team(before, after, facts)
    return after


def _check_preview_domain(before: Any, after: Any) -> None:
    """
    A changed ``product.delivery.preview_domain`` names one of the deployment's
    preview domains: which hosts the server probes and redirects to is Tier 0.

    :raises CockpitError: 422 ``tier0`` for any other domain.
    """
    from omnigent.server.factory.env_urls import preview_domain_error

    def norm(value: Any) -> str:
        return str(value or "").strip().strip(".").lower()

    problem = None if norm(before) == norm(after) else preview_domain_error(after)
    if problem is not None:
        raise tier0(f"{PREVIEW_DOMAIN_PATH}: {problem}", field=PREVIEW_DOMAIN_PATH)


def _list_diff(path: str, before: Any, after: Any) -> list[dict[str, Any]]:
    old = {x["id"]: x for x in before or [] if isinstance(x, Mapping) and "id" in x}
    new = {x["id"]: x for x in after or [] if isinstance(x, Mapping) and "id" in x}
    ids = [*old, *(i for i in new if i not in old)]
    return [
        {"path": f"{path}.{i}", "before": old.get(i), "after": new.get(i)}
        for i in ids
        if old.get(i) != new.get(i)
    ]


def diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """
    :returns: ``[{path, before, after}]`` of the paths that differ: scalars (and
        lists without ids) whole, lists with ids per item (``strategy.goals.G-2``), rules per rule.
    """
    a, b = _with_defaults(before), _with_defaults(after)
    out = [
        {"path": path, "before": get_path(a, path), "after": get_path(b, path)}
        for path in sorted(ALLOWED_PATHS - LIST_PATHS)
        if get_path(a, path) != get_path(b, path)
    ]
    for path in sorted(LIST_PATHS):
        out += _list_diff(path, get_path(a, path), get_path(b, path))
    out += team_spec.diff(a, b)
    old = {r["id"]: r for r in a.get("rules") or []}
    new = {r["id"]: r for r in b.get("rules") or []}
    for rule_id in sorted(old.keys() | new.keys(), key=lambda r: int(r[2:])):
        if old.get(rule_id) != new.get(rule_id):
            out.append(
                {"path": f"rules.{rule_id}", "before": old.get(rule_id), "after": new.get(rule_id)}
            )
    return out


def _rule_key(rule: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    return rule.get("role"), rule.get("project") or "*", rule.get("text")


def same_rule(a: Any, b: Any) -> bool:
    """Whether *a* and *b* (rule dicts or ``None``) say the same; trial/active and origin aside."""
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return _rule_key(a) == _rule_key(b)
    return a is None and b is None


def moved_paths(before: Mapping[str, Any], stored: Mapping[str, Any]) -> list[str]:
    """
    The paths of a proposal's *before* (``{path: value}`` as it read the definition)
    whose value *stored* no longer has.

    A rule addition (``before`` ``None``) never moves: it takes a fresh id when it
    applies. A replaced or removed rule moves once its role, scope or text changed
    or it is gone; its trial/active state does not count.
    """
    doc = _with_defaults(stored)
    rules = {str(r.get("id")): r for r in doc.get("rules") or [] if isinstance(r, Mapping)}
    out: list[str] = []
    for path, was in before.items():
        if team_spec.is_project_path(path):
            if team_spec.effective_get(doc, path) != was:
                out.append(path)
            continue
        if not path.startswith("rules."):
            if get_path(doc, canonical_path(path)) != was:
                out.append(path)
            continue
        if not isinstance(was, Mapping):
            continue
        if not same_rule(rules.get(path.removeprefix("rules.")), was):
            out.append(path)
    return out


def is_item_path(path: str) -> bool:
    """Whether *path* names one item of a list with ids (``strategy.goals.G-2``)."""
    listed = blocks.list_of(path)
    return listed is not None and listed[1] is not None and listed[2] is None


def is_new_item(path: str) -> bool:
    """Whether *path* is an unnumbered addition of a pending change (``strategy.goals.new``)."""
    listed = blocks.list_of(path)
    if path.startswith("rules.new"):
        return True
    return (
        listed is not None
        and listed[0].numbered
        and bool(re.fullmatch(r"new(-[0-9]+)?", listed[1] or ""))
    )


_SHORT_MAX: Final = 80


def _short(value: Any) -> Any:
    """*value* for a diff line: long text and structures clipped (short values as they are)."""
    if isinstance(value, Mapping | list):
        text = json.dumps(value, separators=(",", ":"), default=str, ensure_ascii=False)
    elif isinstance(value, str):
        text = value
    else:
        return value
    return text if len(text) <= _SHORT_MAX else f"{text[: _SHORT_MAX - 1]}…"


def _item_label(item: Mapping[str, Any]) -> str:
    for key in ("label", "statement", "name"):
        if item.get(key):
            return f'"{_short(str(item[key]))}"'
    return str(item.get("id") or "")


def _item_line(path: str, before: Any, after: Any) -> str:
    if before is None and isinstance(after, Mapping):
        return f"+ {path} · {_item_label(after)}"
    if after is None and isinstance(before, Mapping):
        return f"- {path} · {_item_label(before)}"
    was = before if isinstance(before, Mapping) else {}
    now = after if isinstance(after, Mapping) else {}
    keys = [k for k in dict.fromkeys([*was, *now]) if was.get(k) != now.get(k)]
    parts = [f"{k} {_short(was.get(k))} → {_short(now.get(k))}" for k in keys[:3]]
    more = f" (+{len(keys) - 3})" if len(keys) > 3 else ""
    return f"~ {path} · {'; '.join(parts)}{more}"


def diff_lines(changes: Iterable[Mapping[str, Any]], limit: int = 12) -> list[str]:
    """:returns: One line per change, e.g. ``"cadence.project_lead: 7200 → 10800"``."""
    lines: list[str] = []
    for change in changes:
        before, after = change.get("before"), change.get("after")
        path = str(change.get("path", ""))
        if team_spec.is_project_path(path):
            lines.append(team_spec.diff_line(change))
            continue
        if is_item_path(path):
            lines.append(_item_line(path, before, after))
            continue
        if path.startswith("rules."):
            if before is None and isinstance(after, Mapping):
                rule_id = after.get("id") or "new rule"
                lines.append(f'+ {rule_id} · {after["role"]} · "{after["text"]}"')
            elif after is None and isinstance(before, Mapping):
                lines.append(f'- {before["id"]} · {before["role"]} · "{before["text"]}"')
            elif isinstance(after, Mapping):
                lines.append(
                    f'~ {after["id"]} · {after["role"]} · "{after["text"]}" ({after.get("state")})'
                )
            continue
        lines.append(f"{path}: {_short(before)} → {_short(after)}")
    if len(lines) > limit:
        lines = [*lines[:limit], f"… and {len(lines) - limit} more"]
    return lines


# ── Rules ──────────────────────────────────────────────────────


def rules_for(
    doc: Mapping[str, Any], role: str | None, project: str | None
) -> list[dict[str, Any]]:
    """:returns: The rules for *role* in *project* (``*`` matches any), in id order."""
    out = []
    for rule in doc.get("rules") or []:
        if rule.get("role") not in ("*", role):
            continue
        scope = rule.get("project") or "*"
        if scope != "*" and scope != project:
            continue
        out.append(dict(rule))
    return sorted(out, key=lambda r: int(str(r.get("id", "R-0"))[2:] or 0))


def rule_texts(deps: FactoryDeps, factory_id: str, role: str, project: str | None) -> list[str]:
    """:returns: ``"R-3: <text>"`` lines for a card or brief. Never raises."""
    try:
        resolved = definition_for(deps, factory_id)
    except Exception:  # noqa: BLE001 - a card goes out without rules rather than not at all
        _logger.warning("factory definition: no rules for %s", factory_id, exc_info=True)
        return []
    if resolved is None:
        return []
    return [f"{r['id']}: {r['text']}" for r in rules_for(resolved.doc, role, project)]


# ── Readers (the hooks in other modules call these) ────────────


def engine_mode(deps: FactoryDeps, factory_id: str) -> str | None:
    """:returns: ``definition.engine.mode``, or ``None`` when it cannot be resolved."""
    mode = value(deps, factory_id, "engine.mode")
    return mode if mode in ENGINE_MODES else None


def autonomy_mode(deps: FactoryDeps, factory_id: str) -> str | None:
    """:returns: ``definition.autonomy.projects``, or ``None`` when unresolved."""
    mode = value(deps, factory_id, "autonomy.projects")
    return mode if mode in ("confirm", "requested", "grace") else None


def heartbeat_default(deps: FactoryDeps, root: Conversation, role: str) -> int | None:
    """:returns: The definition's heartbeat for a new *role* member (demo cadence aware)."""
    demo = (root.labels.get(LABEL_CADENCE) or CADENCE_DEMO) == CADENCE_DEMO
    if role == ROLE_PROJECT_LEAD:
        path = "cadence.demo_project_lead" if demo else "cadence.project_lead"
    elif role == ROLE_LEAD and not demo:
        path = "cadence.lead"
    else:
        return None
    found = _num(value(deps, root.id, path))
    return int(found) if found is not None else None


def supervisor_config(config: SupervisorConfig, factory_id: str | None) -> SupervisorConfig:
    """:returns: *config* with the factory's stuck, blocked, quiet-beat and TTL values."""
    resolved = None
    if _deps is not None and factory_id:
        try:
            resolved = definition_for(_deps, factory_id)
        except Exception:  # noqa: BLE001 - the process-wide timings apply
            _logger.warning("factory definition: supervisor config fell back", exc_info=True)
    if resolved is None:
        return config
    ttl = _num(resolved.get("previews.ttl_h"))
    return replace(
        config,
        stuck_s=float(resolved.get("supervisor.stuck_s", config.stuck_s)),
        blocked_escalate_s=float(
            resolved.get("supervisor.blocked_escalate_s", config.blocked_escalate_s)
        ),
        quiet_beats=bool(resolved.get("supervisor.quiet_beats", config.quiet_beats)),
        # Parking the env turned off stays off; 0 in the definition turns it off too.
        preview_ttl_h=None if config.preview_ttl_h is None or not ttl else ttl,
    )


def mail_limits(app_state: Any, factory_id: str | None) -> tuple[int, int] | None:
    """:returns: ``(sender, pair)`` posts per hour of the factory, or ``None`` (env limits)."""
    from omnigent.server.factory.context import FACTORY_STATE_ATTR

    deps = getattr(app_state, FACTORY_STATE_ATTR, None) or _deps
    if deps is None or not factory_id:
        return None
    sender, pair = (
        value(deps, factory_id, "mail.sender_rate"),
        value(deps, factory_id, "mail.pair_rate"),
    )
    if _num(sender) is None or _num(pair) is None:
        return None
    return int(sender), int(pair)


def pinned_version(deps: FactoryDeps | None, factory_id: str | None) -> int | None:
    """:returns: The version in force (``None`` when unresolved). Never raises."""
    deps = deps or _deps
    if deps is None or not factory_id:
        return None
    try:
        resolved = definition_for(deps, factory_id)
    except Exception:  # noqa: BLE001 - callers stamp nothing then
        return None
    return resolved.version if resolved is not None else None


def int_value(deps: FactoryDeps | None, factory_id: str | None, path: str, default: int) -> int:
    """:returns: The integer at *path* (``default`` when unresolved)."""
    found = _num(value(deps, factory_id, path))
    return int(found) if found is not None else default


def discovery_enabled(deps: FactoryDeps | None, factory_id: str | None) -> bool:
    """Whether discovery runs for the factory: the server env and ``discovery.enabled`` both on."""
    if not blocks.discovery_server_on():
        return False
    return value(deps, factory_id, "discovery.enabled", False) is True


# ── Trials ─────────────────────────────────────────────────────


def trial_block(path: str) -> str | None:
    """
    :returns: The block a trial on *path* holds: the top-level block of
        :mod:`.definition_blocks` it lies in (``"discovery"``, ``"briefs"``).
        ``None`` for today's settings and rules: they trial side by side as
        before, each change holding only its own paths.
    """
    head = canonical_path(path).split(".", 1)[0]
    return head if head in blocks.BLOCKS or head == team_spec.TEAM else None


def trial_blocks(paths: Iterable[str]) -> list[str]:
    """:returns: The blocks a trial on *paths* would hold, sorted."""
    return sorted({b for p in paths if (b := trial_block(str(p))) is not None})


def discovery_method(path: str) -> bool:
    """Whether *path* is discovery's method (the rubric, thresholds, Scout's brief)."""
    path = canonical_path(str(path))
    return any(blocks.under(path, prefix) for prefix in blocks.DISCOVERY_TRIALS)


def _method_trials(factory_id: str | None) -> bool:
    """
    Whether discovery's method trials on its own guards: the server runs discovery
    and the factory, when one is named, has it on.
    """
    if not blocks.discovery_server_on():
        return False
    return factory_id is None or discovery_enabled(None, factory_id)


def guard_set(paths: Iterable[str], *, factory_id: str | None = None) -> str:
    """
    :returns: Which guards judge a trial on *paths*: ``discovery`` when every
        path is discovery's method on a server that runs discovery (and in
        *factory_id*, when given, with discovery on), else ``delivery``.
    """
    paths = [str(p) for p in paths]
    if paths and _method_trials(factory_id) and all(discovery_method(p) for p in paths):
        return GUARDS_DISCOVERY
    return GUARDS_DELIVERY


def not_trialable_table(factory_id: str | None = None) -> dict[str, str]:
    """:returns: ``{prefix: reason}`` of the paths that never trial (in *factory_id*)."""
    if not blocks.discovery_server_on():
        return dict(blocks.NOT_TRIALABLE)
    if not _method_trials(factory_id):
        # Its guards would measure nothing: the method applies as a version.
        return {
            p: DISCOVERY_OFF if r == blocks.NEEDS_DISCOVERY_GUARDS else r
            for p, r in blocks.NOT_TRIALABLE.items()
        }
    out = {p: r for p, r in blocks.NOT_TRIALABLE.items() if p not in blocks.DISCOVERY_TRIALS}
    out["discovery"] = blocks.DISCOVERY_SETTING
    return out


def not_trialable(paths: Iterable[str], *, factory_id: str | None = None) -> dict[str, str]:
    """
    :returns: ``{prefix: reason}`` of :data:`.definition_blocks.NOT_TRIALABLE`
        for each of *paths* that never runs as a trial (``{}`` when none does).
        On a server that runs discovery, discovery's method trials on its own
        guards (in *factory_id*, when given, only with discovery on), so it may
        not share a trial with other paths.
    """
    table = not_trialable_table(factory_id)
    method_trials = _method_trials(factory_id)
    out: dict[str, str] = {}
    method = other = False
    for path in paths:
        path = canonical_path(str(path))
        if method_trials and discovery_method(path):
            method = True
            continue
        hit = [prefix for prefix in table if blocks.under(path, prefix)]
        for prefix in hit:
            out[prefix] = table[prefix]
        other = other or not hit
    if method and other:
        out["discovery"] = blocks.MIXED_GUARDS
    return {p: out[p] for p in table if p in out}


def trial_window_h(paths: Iterable[str], *, factory_id: str | None = None) -> tuple[int, int, int]:
    """:returns: ``(min, default, max)`` hours of a trial on *paths* (by its guard set)."""
    if guard_set(paths, factory_id=factory_id) == GUARDS_DISCOVERY:
        return DISCOVERY_TRIAL_H
    return TRIAL_H_MIN, TRIAL_H_DEFAULT, TRIAL_H_MAX


def trial_hours(
    paths: Iterable[str], requested: int | None, *, factory_id: str | None = None
) -> int:
    """
    :returns: The trial's length in hours: *requested*, or the default of its guard set.
    :raises CockpitError: 422 ``invalid_trial_window`` outside the guard set's window.
    """
    paths = list(paths)
    low, default, high = trial_window_h(paths, factory_id=factory_id)
    if requested is None:
        return default
    if not low <= requested <= high:
        guards = guard_set(paths, factory_id=factory_id)
        if guards == GUARDS_DISCOVERY:
            span = f"{low // 24}-{high // 24} days (default {trial_text(default, guards)})"
        else:
            span = f"{low}-{high} h (default {default} h)"
        raise _error(
            422,
            "invalid_trial_window",
            f"a trial on {guards} guards runs {span}",
            field="trial_h",
            min=low,
            max=high,
            default=default,
        )
    return requested


def trial_text(hours: int, guards: str = GUARDS_DELIVERY) -> str:
    """:returns: A trial's length in words: days for discovery's windows, else hours."""
    if guards == GUARDS_DISCOVERY and hours % 24 == 0:
        days = hours // 24
        return f"{days} day{'s' if days != 1 else ''}"
    return f"{hours}h"


_UNTRIALABLE_NAMES: Final = {
    "product": "product profile",
    "environment": "environment profile",
    "strategy": "strategy",
    "discovery": "discovery",
    "briefs.scout": "Scout's brief",
    team_spec.PROJECTS: "a project's team",
    team_spec.TEMPLATES: "team template",
    "done_review": "done review",
}
_UNTRIALABLE_WHY: Final = {
    blocks.NOT_TRIALABLE_REASON: "are judged on outcomes",
    blocks.ENV_FACTS: "are facts about how the product runs, not a way of working",
    blocks.DISCOVERY_SETTING: (
        "are settings, not discovery's method (the rubric, thresholds and Scout's brief "
        "trial on discovery guards)"
    ),
    blocks.MIXED_GUARDS: (
        "to its method trial on discovery guards and cannot share a trial with other changes"
    ),
    blocks.NEEDS_DISCOVERY_GUARDS: (
        "are judged on discovery guards, which this server does not measure yet"
    ),
    DISCOVERY_OFF: (
        "are judged on discovery guards, which measure nothing while this factory's "
        "discovery is off"
    ),
    blocks.TOO_FEW_PRS: (
        "would rarely reach the 3 merged PRs a trial needs (one team merges about one a day)"
    ),
    blocks.NO_PROJECT_FOLLOWS: (
        "reach a project only when it switches to the template, so a trial would measure "
        "nothing yet"
    ),
    blocks.TOO_FEW_REVIEWS: (
        "are judged by how often a done feature is reopened, and too few features finish "
        "in a trial's window"
    ),
}


def untrialable_text(hit: Mapping[str, str]) -> str:
    """:returns: Why the changes of *hit* (see :func:`not_trialable`) never trial, in words."""
    parts = []
    for reason in dict.fromkeys(hit.values()):
        names = " and ".join(_UNTRIALABLE_NAMES.get(p, p) for p, r in hit.items() if r == reason)
        parts.append(f"{names} changes {_UNTRIALABLE_WHY.get(reason, reason)}")
    return "; ".join(parts)


def trialable(paths: Iterable[str], *, factory_id: str | None = None) -> tuple[bool, str | None]:
    """
    Whether a change of *paths* may run as a trial (in *factory_id*, when given).

    :returns: ``(False, reason)`` when it touches a path a trial would judge on
        the wrong metrics (strategy, the product profile, discovery, Scout's
        brief): it applies as a version.
    """
    hit = not_trialable(paths, factory_id=factory_id)
    if not hit:
        return True, None
    if blocks.MIXED_GUARDS in hit.values():
        return False, f"{untrialable_text(hit)}: split them, or apply them as a version"
    return False, f"{untrialable_text(hit)}: they apply as a version"


# ── Install ────────────────────────────────────────────────────


def _budget(factory_id: str, kind: str, key: str) -> Any:
    path = f"budgets.{key}" if kind == "budgets" else f"loops.{kind}.{key}"
    return value(None, factory_id, path)


def _threshold(factory_id: str, key: str) -> Any:
    path = "budgets.usd_per_hour_alert" if key == "usd_per_hour_alert" else f"alerts.{key}"
    return value(None, factory_id, path)


def install(app_state: Any) -> Callable[[], None]:
    """
    Make the definition the source of loop budgets, detector thresholds and the
    supervisor's per-factory timings for this process.

    :returns: A callable that restores the previous wiring.
    """
    global _deps
    from omnigent.server.factory import detectors, loops
    from omnigent.server.factory.context import FACTORY_STATE_ATTR

    previous = _deps
    _deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    restore_budgets = loops.set_budget_resolver(_budget)
    restore_thresholds = detectors.set_threshold_resolver(_threshold)

    def _restore() -> None:
        global _deps
        _deps = previous
        restore_budgets()
        restore_thresholds()
        invalidate()

    return _restore


@functools.cache
def schema_paths() -> list[str]:
    """:returns: The sorted allowlist (for the API and the docs)."""
    return sorted(ALLOWED_PATHS)


__all__ = [
    "ALLOWED_PATHS",
    "BOUNDS",
    "BUDGET_PREFIXES",
    "CLASS_MAP",
    "DISCOVERY_OFF",
    "DISCOVERY_TRIAL_H",
    "GUARDS_DELIVERY",
    "GUARDS_DISCOVERY",
    "LIST_PATHS",
    "OWNER_ONLY_PREFIXES",
    "QUIET_BLOCKS",
    "SPARSE_FIELDS",
    "SPARSE_KEYS",
    "TIER0",
    "TIER0_REASONS",
    "TRIAL_H_DEFAULT",
    "TRIAL_H_LIMIT",
    "TRIAL_H_MAX",
    "TRIAL_H_MIN",
    "UNDO_WINDOW_S",
    "Change",
    "EvidenceId",
    "FactoryDefinitionDoc",
    "Resolved",
    "apply_changes",
    "autonomy_mode",
    "bounds",
    "budgets_lower_only",
    "canonical_path",
    "classify",
    "default_doc",
    "definition_for",
    "diff",
    "diff_lines",
    "discovery_enabled",
    "discovery_method",
    "effective",
    "engine_mode",
    "env_defaults",
    "goal_floor",
    "goal_seq",
    "guard_set",
    "heartbeat_default",
    "install",
    "int_value",
    "invalidate",
    "is_budget_path",
    "is_item_path",
    "is_new_item",
    "mail_limits",
    "moved_paths",
    "not_trialable",
    "not_trialable_table",
    "owner_only",
    "parse_change",
    "pin",
    "rule_floor",
    "rule_seq",
    "rule_texts",
    "rules_for",
    "same_rule",
    "seed_doc",
    "store_version",
    "stored_form",
    "supervisor_config",
    "tier0",
    "trial_block",
    "trial_blocks",
    "trial_hours",
    "trial_text",
    "trial_window_h",
    "trialable",
    "untrialable_text",
    "value",
]
