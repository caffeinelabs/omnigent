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
"""

from __future__ import annotations

import copy
import functools
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from omnigent.factory import (
    CADENCE_DEMO,
    CADENCE_HEARTBEATS,
    ENGINE_MODES,
    LABEL_CADENCE,
    LABEL_DEF_VERSION,
    LABEL_ID,
    LABEL_PREFIX,
    MAX_HEARTBEAT_S,
    MIN_HEARTBEAT_S,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    ROLES,
)
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
RULES_PER_ROLE: Final = 20
RULE_TEXT_MAX: Final = 500
RULE_ROLES: Final = (*sorted(ROLES), "*")
TRIAL_H_DEFAULT: Final = 48
TRIAL_H_MIN: Final = 24
TRIAL_H_MAX: Final = 168
LABEL_ENGINE_MODE: Final = f"{LABEL_PREFIX}engine_mode"
LABEL_AUTONOMY: Final = f"{LABEL_PREFIX}autonomy_projects"
LABEL_ENGINE_SPAWN: Final = f"{LABEL_PREFIX}engine_spawn"
LEGACY_LABELS: Final = (LABEL_ENGINE_MODE, LABEL_AUTONOMY, LABEL_ENGINE_SPAWN)
"""Root labels whose values move into the doc on the first resolve."""
_ALIAS_RE: Final = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_RULE_ID_RE: Final = re.compile(r"^R-([1-9][0-9]{0,6})$")


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


class AutonomyDef(_Block):
    projects: Literal["confirm", "requested", "grace"] = "requested"


class DecisionsDef(_Block):
    escalate_after_m: int = 30
    escalate_owners_after_m: int = 120


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


class ModelsDef(_Block):
    """Model aliases (never model ids) per sub-agent kind."""

    ic: str | None = None
    reviewer: str | None = None
    planner: str | None = None

    @field_validator("ic", "reviewer", "planner")
    @classmethod
    def _alias(cls, value: str | None) -> str | None:
        if value is not None and not _ALIAS_RE.match(value):
            raise ValueError("a model alias such as 'sonnet' or 'opus', never a model id")
        return value


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
    models: ModelsDef = Field(default_factory=ModelsDef)
    rules: list[RuleDef] = Field(default_factory=list)
    rule_seq: int = 0

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
    "budgets.*": _always("B"),
    "models.*": _always("B"),
}
"""``path pattern -> (before, after) -> class``; the first matching pattern wins."""
CLASS_TABLE: Final = (
    ("cadence.*", "A when slower, else B (0 = off is B)"),
    ("loops.*.attempts|rounds|per_head|usd", "A when lower, else B"),
    ("loops.* (other keys)", "B"),
    ("mail.*", "A when lower, else B"),
    ("supervisor.*, alerts.*, decisions.*", "A"),
    ("previews.*, caps.*", "B"),
    ("rules", "B (owners), as a trial"),
    ("engine.*, autonomy.*, budgets.*, models.*", "B (owners)"),
    ("anything else, Tier 0", "refused: 422 tier0"),
)
""":data:`CLASS_MAP` in words (the Definition panel shows it)."""
OWNER_ONLY_PREFIXES: Final = ("engine.", "autonomy.", "budgets.", "models.")
TIER0_PATHS: Final = frozenset({"alerts.slack_webhook", "rule_seq"})


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _pattern_match(pattern: str, path: str) -> bool:
    want, have = pattern.split("."), path.split(".")
    if want[-1] == "*" and len(want) == 2:
        return have[0] == want[0] and len(have) >= 2
    if len(want) != len(have):
        return False
    return all(w == "*" or w == h for w, h in zip(want, have, strict=True))


def classify(path: str, before: Any, after: Any) -> Klass:
    """:returns: The approval class of one change (``CLASS_MAP``; unknown paths are ``B``)."""
    if path.startswith("rules"):
        return "B"
    for pattern, rule in CLASS_MAP.items():
        if _pattern_match(pattern, path):
            return rule(before, after)
    return "B"


def _leaf_paths(model: type[BaseModel], prefix: str = "") -> list[str]:
    out: list[str] = []
    for name, info in model.model_fields.items():
        annotation = info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            out += _leaf_paths(annotation, f"{prefix}{name}.")
        elif name not in ("rules", "rule_seq"):
            out.append(f"{prefix}{name}")
    return out


ALLOWED_PATHS: Final = frozenset(_leaf_paths(FactoryDefinitionDoc))
"""Every scalar path a change may replace (plus ``rules``)."""


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


def get_path(doc: Mapping[str, Any], path: str) -> Any:
    """:returns: The value at a dotted *path* of *doc*, or ``None``."""
    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def set_path(doc: dict[str, Any], path: str, value: Any) -> None:
    """Set a dotted *path* of *doc* in place, creating blocks on the way."""
    parts = path.split(".")
    node = doc
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


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
    """:returns: The v1 doc for a root with *labels* (its legacy labels migrated)."""
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
    return doc


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
    return FactoryDefinitionDoc.model_validate(_merge(default_doc(), stored)).model_dump(
        mode="json"
    )


def effective(stored: Mapping[str, Any]) -> dict[str, Any]:
    """:returns: *stored* over env and code defaults, validated (bad blocks fall back), clamped."""
    env = env_defaults()
    base = default_doc()
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
    doc = seed_doc(root.labels)
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
    validated = FactoryDefinitionDoc.model_validate(doc).model_dump(mode="json")
    row = deps.factory_store.put_definition(
        factory_id,
        doc=validated,
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

    :param path: Dotted path, e.g. ``"cadence.project_lead"`` or ``"rules"``.
    :param op: ``replace`` / ``add`` / ``remove``.
    :param rule_id: The rule a ``rules`` change adds, replaces or removes.
    """

    op: str
    path: str
    value: Any = None
    rule_id: str | None = None


def normalize_path(raw: str) -> str:
    """:returns: ``"/cadence/project_lead"`` or ``"cadence.project_lead"`` as the dotted form."""
    return raw.strip().lstrip("/").replace("/", ".")


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
    if path in TIER0_PATHS or not path:
        raise tier0(f"{path or 'that path'} is not part of the factory definition", field=field)
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


def next_rule_id(doc: Mapping[str, Any]) -> str:
    """:returns: The next free ``R-<n>`` of *doc*."""
    numbers = [
        int(m.group(1)) for r in doc.get("rules") or [] if (m := _RULE_ID_RE.match(r["id"]))
    ]
    seq = max([int(doc.get("rule_seq") or 0), *numbers], default=0)
    return f"R-{seq + 1}"


def apply_changes(
    stored: Mapping[str, Any],
    changes: Sequence[Change],
    *,
    origin: str | None = None,
    rule_state: str = "active",
) -> dict[str, Any]:
    """
    :returns: A new stored doc with *changes* applied (validated by the model).
    :raises CockpitError: 422 ``invalid_definition`` when the result does not validate,
        404 ``unknown_rule`` for a missing rule.
    """
    doc = _merge(default_doc(), stored)
    rules: list[dict[str, Any]] = [dict(r) for r in doc.get("rules") or []]
    for change in changes:
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
            body = dict(change.value) if isinstance(change.value, Mapping) else {}
            rules[index] = {**rules[index], **body, "id": change.rule_id, "origin": origin}
    doc["rules"] = rules
    try:
        return FactoryDefinitionDoc.model_validate(doc).model_dump(mode="json")
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise _error(
            422,
            "invalid_definition",
            f"{where}: {first.get('msg', 'invalid value')}" if where else str(exc)[:300],
            field=where or None,
        ) from None


def diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """:returns: ``[{path, before, after}]`` of the scalar paths and rules that differ."""
    a, b = _merge(default_doc(), before), _merge(default_doc(), after)
    out = [
        {"path": path, "before": get_path(a, path), "after": get_path(b, path)}
        for path in sorted(ALLOWED_PATHS)
        if get_path(a, path) != get_path(b, path)
    ]
    old = {r["id"]: r for r in a.get("rules") or []}
    new = {r["id"]: r for r in b.get("rules") or []}
    for rule_id in sorted(old.keys() | new.keys(), key=lambda r: int(r[2:])):
        if old.get(rule_id) != new.get(rule_id):
            out.append(
                {"path": f"rules.{rule_id}", "before": old.get(rule_id), "after": new.get(rule_id)}
            )
    return out


def diff_lines(changes: Iterable[Mapping[str, Any]], limit: int = 12) -> list[str]:
    """:returns: One line per change, e.g. ``"cadence.project_lead: 7200 → 10800"``."""
    lines: list[str] = []
    for change in changes:
        before, after = change.get("before"), change.get("after")
        if str(change.get("path", "")).startswith("rules."):
            if before is None and isinstance(after, Mapping):
                lines.append(f'+ {after["id"]} · {after["role"]} · "{after["text"]}"')
            elif after is None and isinstance(before, Mapping):
                lines.append(f'- {before["id"]} · {before["role"]} · "{before["text"]}"')
            elif isinstance(after, Mapping):
                lines.append(
                    f'~ {after["id"]} · {after["role"]} · "{after["text"]}" ({after.get("state")})'
                )
            continue
        lines.append(f"{change.get('path')}: {before} → {after}")
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
    "CLASS_MAP",
    "OWNER_ONLY_PREFIXES",
    "TIER0",
    "TRIAL_H_DEFAULT",
    "TRIAL_H_MAX",
    "TRIAL_H_MIN",
    "Change",
    "FactoryDefinitionDoc",
    "Resolved",
    "apply_changes",
    "autonomy_mode",
    "bounds",
    "classify",
    "default_doc",
    "definition_for",
    "diff",
    "diff_lines",
    "effective",
    "engine_mode",
    "env_defaults",
    "heartbeat_default",
    "install",
    "int_value",
    "invalidate",
    "mail_limits",
    "parse_change",
    "pin",
    "rule_texts",
    "rules_for",
    "seed_doc",
    "store_version",
    "supervisor_config",
    "tier0",
    "value",
]
