"""Which project proposals apply on their own, and which wait for a human.

Owners set a factory's autonomy for project changes to ``confirm``,
``requested`` (the default) or ``grace``. Each op of a proposal falls in one
class; the matrix below gives the approval it needs, depending on whether an
operator or owner asked for it in the thread (``requested``) or the agent
acted on its own. The proposal needs the strictest approval of its ops.

=====================  ====================  =================================
Op class               a human asked         the agent acted on its own
=====================  ====================  =================================
create                 auto (Undo)           operator (``grace``: auto in 15 min)
update_minor           auto (Undo)           auto (Undo) + an info line
update_major           auto (Undo)           operator
structural             operator              operator
owner                  owner                 owner
=====================  ====================  =================================

Under ``confirm`` every auto cell needs an operator. Over-cap plans never get
here: validation refuses them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from omnigent.factory import LABEL_PREFIX

LABEL_AUTONOMY: Final = f"{LABEL_PREFIX}autonomy_projects"
"""Root label holding the factory's project autonomy until the definition holds it."""

AUTONOMY_CONFIRM: Final = "confirm"
AUTONOMY_REQUESTED: Final = "requested"
AUTONOMY_GRACE: Final = "grace"
AUTONOMY_MODES: Final = (AUTONOMY_CONFIRM, AUTONOMY_REQUESTED, AUTONOMY_GRACE)
DEFAULT_AUTONOMY: Final = AUTONOMY_REQUESTED

OpClass = Literal["create", "update_minor", "update_major", "structural", "owner"]
OP_CLASSES: Final[tuple[OpClass, ...]] = (
    "create",
    "update_minor",
    "update_major",
    "structural",
    "owner",
)
Approval = Literal["auto", "operator", "owner"]
_RANK: Final[dict[str, int]] = {"auto": 0, "operator": 1, "owner": 2}

MINOR_UPDATE_FIELDS: Final = frozenset(
    {"priority", "demo_focus", "demo", "preview", "preview_slots", "budget_ics"}
)
"""Charter fields an agent may change on its own (with Undo)."""


@dataclass(frozen=True)
class Verdict:
    """
    The approval a proposal (or one op) needs.

    :param approval: ``auto``, ``operator`` or ``owner``.
    :param grace: An operator approval that applies itself after the grace period.
    :param info_line: An auto-apply the agent chose itself: tell the thread.
    """

    approval: Approval
    grace: bool = False
    info_line: bool = False


def autonomy_mode(labels: Mapping[str, str]) -> str:
    """:returns: The factory's project autonomy from its root labels (default ``requested``)."""
    value = (labels.get(LABEL_AUTONOMY) or "").strip()
    return value if value in AUTONOMY_MODES else DEFAULT_AUTONOMY


def update_class(fields: Iterable[str]) -> OpClass:
    """:returns: ``update_minor`` when only minor fields change, else ``update_major``."""
    return "update_minor" if set(fields) <= MINOR_UPDATE_FIELDS else "update_major"


def verdict(op_class: OpClass, *, requested: bool, mode: str = DEFAULT_AUTONOMY) -> Verdict:
    """
    The approval one op needs.

    :param op_class: The op's class.
    :param requested: An operator or owner asked for it in the thread.
    :param mode: ``confirm``, ``requested`` or ``grace``.
    """
    if op_class == "owner":
        return Verdict("owner")
    if op_class == "structural":
        return Verdict("operator")
    if requested:
        cell = Verdict("auto")
    elif op_class == "update_minor":
        cell = Verdict("auto", info_line=True)
    elif op_class == "create" and mode == AUTONOMY_GRACE:
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
        return Verdict("auto", info_line=any(v.info_line for v in items))
    if approval == "owner":
        return Verdict("owner")
    gated = [v for v in items if v.approval == "operator"]
    return Verdict("operator", grace=all(v.grace for v in gated))


def decide(
    op_classes: Iterable[OpClass], *, requested: bool, mode: str = DEFAULT_AUTONOMY
) -> Verdict:
    """:returns: The proposal's verdict for ops of *op_classes*."""
    return combine(verdict(c, requested=requested, mode=mode) for c in op_classes)


__all__ = [
    "AUTONOMY_CONFIRM",
    "AUTONOMY_GRACE",
    "AUTONOMY_MODES",
    "AUTONOMY_REQUESTED",
    "DEFAULT_AUTONOMY",
    "LABEL_AUTONOMY",
    "MINOR_UPDATE_FIELDS",
    "OP_CLASSES",
    "Approval",
    "OpClass",
    "Verdict",
    "autonomy_mode",
    "combine",
    "decide",
    "update_class",
    "verdict",
]
