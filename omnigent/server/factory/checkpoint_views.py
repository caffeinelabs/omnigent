"""The wire shapes of checkpoints: the Decisions lens, a checkpoint's details, changing direction.

Every route that serves them validates against these models (``extra`` is
forbidden), so the cockpit's types and the server cannot drift apart.

- ``GET …/n/{ref}/decisions`` → :class:`DecisionsView`
- ``GET …/checkpoints/{ref}`` → :class:`CheckpointView`
- ``POST …/checkpoints/{ref}/preview`` → :class:`DirectionPreviewView`
- ``POST …/checkpoints/{ref}/apply`` → :class:`DirectionAppliedView`
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CheckpointStatus = Literal["decided", "asked", "answered", "changed"]
ByKind = Literal["agent", "server", "person"]


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ByView(_View):
    kind: ByKind
    name: str


class CheckpointRowView(_View):
    """One line of the Decisions lens ("The factory decided: Next to Chat")."""

    ref: str
    """``K-n`` (stored), ``D-n`` or ``C-n`` (a decision or a proposal read as a checkpoint)."""
    line: str
    about: str
    """The question, clipped to one line."""
    at: str
    status: CheckpointStatus
    by: ByView
    by_factory: bool
    node: str | None = None
    """The node it is placed on (the first, when it touches several)."""
    changed_by: str | None = None
    overrides: str | None = None


class DecisionsView(_View):
    """``GET …/n/{ref}/decisions``: newest first; ``next`` reads on."""

    ref: str
    items: list[CheckpointRowView]
    total: int
    next: str | None = None


class OptionView(_View):
    id: str
    label: str
    description: str | None = None
    recommended: bool | None = None


class ChoiceView(_View):
    id: str | None = None
    label: str | None = None
    direction: str | None = None


class EffectView(_View):
    ref: str
    kind: str
    text: str | None = None


class CheckpointView(CheckpointRowView):
    """``GET …/checkpoints/{ref}``: one checkpoint's details (the peek)."""

    question: str
    source: Literal["stored", "decision", "proposal"]
    options: list[OptionView] | None = None
    choice: ChoiceView | None = None
    direction: str | None = None
    why: str | None = None
    confidence: float | None = None
    class_: str | None = Field(None, alias="class")
    decision: str | None = None
    proposal: str | None = None
    project: str | None = None
    effects: list[EffectView] | None = None
    policy: dict[str, Any] | None = None
    """The policy's verdict: ``{who, kind, kinds, level, reasons, tier0?, rule?}``."""
    facts: dict[str, Any] | None = None
    nodes: list[str] | None = None
    can_change: bool

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ReplanView(_View):
    to: str
    text: str | None = None
    mail_id: str | None = None


class DirectionPreviewView(_View):
    """``POST …/checkpoints/{ref}/preview``."""

    preview_id: str
    ref: str
    checkpoint: CheckpointView
    choice: ChoiceView
    title: str
    undo: list[str]
    """Proposals undone or rejected."""
    revert: list[str]
    """Projects cancelled, charters put back, revert tasks added (never a force-push)."""
    effects: list[str]
    replan: ReplanView
    cost: str
    warnings: list[str]
    can_apply: bool
    approver_note: str


class UndoView(_View):
    token: str
    expires_at: str


class DirectionAppliedView(_View):
    """``POST …/checkpoints/{ref}/apply``: the person's checkpoint and what was taken back."""

    checkpoint: CheckpointView | None
    changed: str
    steps: list[str]
    warnings: list[str]
    replan: ReplanView
    undo: UndoView
    line: str
    message: str


def validate(model: type[BaseModel], body: dict[str, Any]) -> dict[str, Any]:
    """
    Check *body* against *model* and return it as built.

    :raises pydantic.ValidationError: When the projection drifted from the contract.
    """
    model.model_validate(body)
    return body


__all__ = [
    "CheckpointRowView",
    "CheckpointView",
    "DecisionsView",
    "DirectionAppliedView",
    "DirectionPreviewView",
    "validate",
]
