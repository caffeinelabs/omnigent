"""Which project proposals apply on their own, and which wait for a human.

Owners set two things in the definition (``autonomy``, class B, owners only;
agents never propose either):

- ``autonomy.level``: ``careful`` (the default), ``standard`` or ``tryout``,
  the one knob Steer explains in plain words (:data:`LEVELS`);
- ``autonomy.projects``: ``confirm``, ``requested`` (the default) or
  ``grace``, the older fine-grained setting.

Each op of a proposal falls in one class; the matrix below gives the approval
it needs under ``careful``, depending on whether an operator or owner asked
for it in the thread (``requested``) or the agent acted on its own. The
proposal needs the strictest approval of its ops.

=====================  ====================  =================================
Op class               a human asked         the agent acted on its own
=====================  ====================  =================================
create                 auto (Undo)           operator (``grace``: auto in 15 min)
create_joined          auto (Undo)           as ``create``
update_minor           auto (Undo)           auto (Undo) + an info line
update_routine         auto (Undo)           operator
update_major           auto (Undo)           operator
structural             operator              operator
owner                  owner                 owner
=====================  ====================  =================================

Under ``confirm`` every auto cell needs an operator. The level only ever
loosens that table, never tightens it: at ``standard`` and ``tryout`` an
``update_minor`` or ``update_routine`` op applies at once (an FYI in Needs
you, Undo for 10 minutes) whatever ``projects`` says, and at ``tryout`` so
does ``create_joined`` (a new project inside an initiative a person already
started). Structural ops, money and other repos keep their rules at every
level.

A proposal that carries out a person's answer to a decision (its
``reply_to`` names ``D-<n>``) never waits for a second approval: see
:mod:`.answer_authority`. Over-cap plans never get here: validation refuses
them. Money is the owners': an op that sets, raises or removes a USD budget
(:func:`raises_budgets`) is of class ``owner``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict

from omnigent.factory import LABEL_PREFIX

LABEL_AUTONOMY: Final = f"{LABEL_PREFIX}autonomy_projects"
"""Root label holding the factory's project autonomy until the definition holds it."""

AUTONOMY_CONFIRM: Final = "confirm"
AUTONOMY_REQUESTED: Final = "requested"
AUTONOMY_GRACE: Final = "grace"
AUTONOMY_MODES: Final = (AUTONOMY_CONFIRM, AUTONOMY_REQUESTED, AUTONOMY_GRACE)
DEFAULT_AUTONOMY: Final = AUTONOMY_REQUESTED

LEVEL_CAREFUL: Final = "careful"
LEVEL_STANDARD: Final = "standard"
LEVEL_TRYOUT: Final = "tryout"
AUTONOMY_LEVELS: Final = (LEVEL_CAREFUL, LEVEL_STANDARD, LEVEL_TRYOUT)
DEFAULT_LEVEL: Final = LEVEL_CAREFUL
LEVEL_PATH: Final = "autonomy.level"
AutonomyLevel = Literal["careful", "standard", "tryout"]

OpClass = Literal[
    "create",
    "create_joined",
    "update_minor",
    "update_routine",
    "update_major",
    "structural",
    "owner",
]
OP_CLASSES: Final[tuple[OpClass, ...]] = (
    "create",
    "create_joined",
    "update_minor",
    "update_routine",
    "update_major",
    "structural",
    "owner",
)
Approval = Literal["auto", "operator", "owner"]
_RANK: Final[dict[str, int]] = {"auto": 0, "operator": 1, "owner": 2}

MINOR_UPDATE_FIELDS: Final = frozenset(
    {"priority", "demo_focus", "demo", "preview", "preview_slots", "budget_ics", "depends_why"}
)
"""Charter fields an agent may change on its own (with Undo); a dependency's reason
explains it and changes nothing the server does."""
DEPENDS_FIELDS: Final = frozenset({"depends_on", "depends_mode"})
"""Dependency fields: routine only when the proposal says why (its rationale or reason)."""
ROUTINE_UPDATE_FIELDS: Final = MINOR_UPDATE_FIELDS | {"scope_paths", "constraints", "dod"}
"""A lead's routine update (with :data:`DEPENDS_FIELDS` when it gives a reason)."""
_BASE_CLASS: Final[dict[str, OpClass]] = {
    "create_joined": "create",
    "update_routine": "update_major",
}
"""What a level-only class is under ``careful``."""
LEVEL_AUTO: Final[dict[str, frozenset[str]]] = {
    LEVEL_CAREFUL: frozenset(),
    LEVEL_STANDARD: frozenset({"update_minor", "update_routine"}),
    LEVEL_TRYOUT: frozenset({"update_minor", "update_routine", "create_joined"}),
}
"""Op classes each level applies at once (with an FYI and Undo), whatever ``projects`` says."""


class AutonomyDef(BaseModel):
    """``autonomy`` in the definition: owners only (class B); agents never propose it."""

    model_config = ConfigDict(extra="forbid")

    projects: Literal["confirm", "requested", "grace"] = "requested"
    level: AutonomyLevel = "careful"


@dataclass(frozen=True)
class LevelText:
    """
    One level in plain words, as Steer shows it.

    :param at_once: What applies at once (with an FYI in Needs you and Undo).
    :param waits: What still waits for a person.
    """

    level: str
    label: str
    summary: str
    at_once: tuple[str, ...]
    waits: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        """:returns: The JSON the definition API serves (``autonomy_levels``)."""
        return {
            "level": self.level,
            "label": self.label,
            "summary": self.summary,
            "at_once": list(self.at_once),
            "waits": list(self.waits),
        }


_ALWAYS_AT_ONCE: Final = (
    "A change a person asked for in the Room in the last 24 hours (a create or an update).",
    "A change that carries out a person's answer to a decision: only the projects and "
    "changes that answer names, by the agent it was given to.",
    "A lead's priority, demo or preview tweak (an FYI in Needs you).",
)
_ALWAYS_WAITS: Final = (
    "Cancelling, splitting or merging projects.",
    "Setting or raising a budget, or a project on another repo or base (owners).",
    "Definition changes, discovery sources and bets (Steer's own rules).",
    "Merges: the merge policy decides those.",
    "This level itself: only owners change it, agents never.",
)
_ROUTINE: Final = (
    "A lead's routine update of a project: scope paths, constraints, definition of "
    "done, priority, and depends_on with a reason."
)
_JOINED: Final = (
    "A lead creating a project inside an initiative a person already started, "
    "within the project cap and without raising a budget."
)
LEVELS: Final[tuple[LevelText, ...]] = (
    LevelText(
        LEVEL_CAREFUL,
        "Careful",
        "Agents ask before every project change they start on their own.",
        _ALWAYS_AT_ONCE,
        (
            "A lead's other project updates and new projects it starts on its own.",
            *_ALWAYS_WAITS,
        ),
    ),
    LevelText(
        LEVEL_STANDARD,
        "Standard",
        "Routine project upkeep happens at once; you see an FYI and can undo it for 10 minutes.",
        (*_ALWAYS_AT_ONCE, _ROUTINE),
        (
            "New projects a lead starts on its own, and goal or outcome changes.",
            *_ALWAYS_WAITS,
        ),
    ),
    LevelText(
        LEVEL_TRYOUT,
        "Tryout",
        "For a tryout: leads also grow an initiative you started without asking; FYI and Undo.",
        (*_ALWAYS_AT_ONCE, _ROUTINE, _JOINED),
        (
            "A brand-new line of work nobody asked for, and goal or outcome changes.",
            *_ALWAYS_WAITS,
        ),
    ),
)
"""Every level in plain words (Steer › Budgets and autonomy; the definition API)."""


@dataclass(frozen=True)
class Verdict:
    """
    The approval a proposal (or one op) needs.

    :param approval: ``auto``, ``operator`` or ``owner``.
    :param grace: An operator approval that applies itself after the grace period.
    :param info_line: An auto-apply the agent chose itself: tell the thread (an FYI).
    :param by_level: The autonomy level that let it apply at once, if any.
    """

    approval: Approval
    grace: bool = False
    info_line: bool = False
    by_level: str | None = None


def autonomy_mode(labels: Mapping[str, str]) -> str:
    """:returns: The factory's project autonomy from its root labels (default ``requested``)."""
    value = (labels.get(LABEL_AUTONOMY) or "").strip()
    return value if value in AUTONOMY_MODES else DEFAULT_AUTONOMY


def factory_autonomy(deps: Any, root: Any) -> str:
    """:returns: ``definition.autonomy.projects`` of *root*'s factory (else its label)."""
    from omnigent.server.factory import definition

    return definition.autonomy_mode(deps, root.id) or autonomy_mode(root.labels)


def factory_level(deps: Any, factory_id: str) -> str:
    """:returns: ``definition.autonomy.level`` of the factory (``careful`` when unresolved)."""
    from omnigent.server.factory import definition

    value = definition.value(deps, factory_id, LEVEL_PATH)
    return value if value in AUTONOMY_LEVELS else DEFAULT_LEVEL


def update_class(fields: Iterable[str], *, has_reason: bool = True) -> OpClass:
    """
    :param has_reason: The proposal says why (a dependency change is routine only then).
    :returns: ``update_minor`` when only minor fields change, ``update_routine``
        for a lead's routine upkeep, else ``update_major``.
    """
    sent = set(fields)
    if sent <= MINOR_UPDATE_FIELDS:
        return "update_minor"
    if sent <= ROUTINE_UPDATE_FIELDS | DEPENDS_FIELDS and (
        has_reason or not sent & DEPENDS_FIELDS
    ):
        return "update_routine"
    return "update_major"


BUDGET_KEYS: Final = ("task_usd", "project_usd")


def sent_budgets(body: Any) -> dict[str, float] | None:
    """:returns: The ``budgets`` a charter body sends (``{}`` clears them), else ``None``."""
    if "budgets" not in getattr(body, "model_fields_set", ()):
        return None
    budgets = getattr(body, "budgets", None)
    return budgets.model_dump(exclude_none=True) if budgets is not None else {}


def raises_budgets(charter: Mapping[str, Any] | None, body: Any) -> bool:
    """
    Whether *body* sets, raises or removes a USD cap of *charter* (owners only).

    Lowering a cap, or sending the same caps, does not.

    :param charter: The normalized stored charter, or ``None`` for a new project.
    :param body: A ``ProjectIn`` or ``CharterPatchIn``.
    """
    sent = sent_budgets(body)
    if sent is None:
        return False
    old = (charter or {}).get("budgets") or {}
    for key in BUDGET_KEYS:
        before, after = old.get(key), sent.get(key)
        if before is None and after is None:
            continue
        if before is None or after is None or after > before:
            return True
    return False


def verdict(
    op_class: OpClass,
    *,
    requested: bool,
    mode: str = DEFAULT_AUTONOMY,
    level: str = DEFAULT_LEVEL,
) -> Verdict:
    """
    The approval one op needs.

    :param op_class: The op's class.
    :param requested: An operator or owner asked for it in the thread.
    :param mode: ``confirm``, ``requested`` or ``grace``.
    :param level: ``careful``, ``standard`` or ``tryout``: loosens, never tightens.
    """
    if op_class == "owner":
        return Verdict("owner")
    if op_class == "structural":
        return Verdict("operator")
    if op_class in LEVEL_AUTO.get(level, frozenset()):
        return Verdict("auto", info_line=not requested, by_level=None if requested else level)
    base = _BASE_CLASS.get(op_class, op_class)
    if requested:
        cell = Verdict("auto")
    elif base == "update_minor":
        cell = Verdict("auto", info_line=True)
    elif base == "create" and mode == AUTONOMY_GRACE:
        cell = Verdict("operator", grace=True)
    else:
        cell = Verdict("operator")
    if mode == AUTONOMY_CONFIRM and cell.approval == "auto":
        return Verdict("operator")
    return cell


def combine(verdicts: Iterable[Verdict]) -> Verdict:
    """
    The approval a whole proposal needs: the strictest of its ops.

    It stays graceful only when every op that needs an operator is graceful.
    """
    items = list(verdicts)
    if not items:
        return Verdict("operator")
    approval = max((v.approval for v in items), key=_RANK.__getitem__)
    if approval == "auto":
        level = next((v.by_level for v in items if v.by_level), None)
        return Verdict("auto", info_line=any(v.info_line for v in items), by_level=level)
    if approval == "owner":
        return Verdict("owner")
    gated = [v for v in items if v.approval == "operator"]
    return Verdict("operator", grace=all(v.grace for v in gated))


def decide(
    op_classes: Iterable[OpClass],
    *,
    requested: bool,
    mode: str = DEFAULT_AUTONOMY,
    level: str = DEFAULT_LEVEL,
) -> Verdict:
    """:returns: The proposal's verdict for ops of *op_classes*."""
    return combine(verdict(c, requested=requested, mode=mode, level=level) for c in op_classes)


# ── Initiatives (the seam ``create_joined`` reads) ─────────────

InitiativeLookup = Callable[[Any, str, Mapping[str, Any]], "str | None"]
"""``(deps, factory_id, ask) -> initiative id or None`` (:func:`register_initiative_lookup`)."""
_LOOKUPS: list[InitiativeLookup] = []


def register_initiative_lookup(lookup: InitiativeLookup) -> None:
    """
    Add a way to tell which existing initiative a new project joins.

    *lookup* gets ``(deps, factory_id, ask)`` where *ask* is
    ``{"reply_to", "opportunity", "project", "changes"}`` of the proposal (the
    new project's create body as ``project``) and returns the initiative's id
    when it has at least one live project, else ``None``. The first non-empty
    answer wins; the built-in one (:func:`.proposals.ask_initiative`) runs last.
    """
    if lookup not in _LOOKUPS:
        _LOOKUPS.append(lookup)


def initiative_lookups() -> tuple[InitiativeLookup, ...]:
    """:returns: The registered lookups, in order."""
    return tuple(_LOOKUPS)


__all__ = [
    "AUTONOMY_CONFIRM",
    "AUTONOMY_GRACE",
    "AUTONOMY_LEVELS",
    "AUTONOMY_MODES",
    "AUTONOMY_REQUESTED",
    "BUDGET_KEYS",
    "DEFAULT_AUTONOMY",
    "DEFAULT_LEVEL",
    "DEPENDS_FIELDS",
    "LABEL_AUTONOMY",
    "LEVELS",
    "LEVEL_AUTO",
    "LEVEL_CAREFUL",
    "LEVEL_PATH",
    "LEVEL_STANDARD",
    "LEVEL_TRYOUT",
    "MINOR_UPDATE_FIELDS",
    "OP_CLASSES",
    "ROUTINE_UPDATE_FIELDS",
    "Approval",
    "AutonomyDef",
    "AutonomyLevel",
    "InitiativeLookup",
    "LevelText",
    "OpClass",
    "Verdict",
    "autonomy_mode",
    "combine",
    "decide",
    "factory_autonomy",
    "factory_level",
    "initiative_lookups",
    "raises_budgets",
    "register_initiative_lookup",
    "sent_budgets",
    "update_class",
    "verdict",
]
