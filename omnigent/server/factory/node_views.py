"""The work-levels API's response models (``notes`` api.md §3, ``web/…/v3/nodeTypes.ts``).

``GET /v1/factory/factories/{fid}/n/{ref}`` returns a :class:`NodePageView`.
The models forbid unknown keys, so the projection (:mod:`.nodes`) and the web
types change together; the web's ``nodeWire.ts`` reads the JSON the server
test writes to ``web/src/components/factory/v3/__fixtures__``.

Fields the API spec marks "(client)" (display names, tags, glyphs) are
optional here and filled where the server knows them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

NodeState = Literal[
    "broken",
    "needs",
    "waiting",
    "moving",
    "notstarted",
    "offered",
    "done",
    "later",
    "ghost",
    "paused",
]
StatusKind = Literal[
    "moving", "needs", "waiting", "done", "later", "offered", "proposed", "paused", "idle"
]
StatusEnd = Literal["person", "server", "moving", "cycle", "cut", "other"]


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActorView(_View):
    name: str
    role: str | None = None
    working: bool | None = None


class DecisionActionView(_View):
    id: str
    label: str


class DecisionRefView(_View):
    id: str
    verb: str
    actions: list[DecisionActionView]
    asker: str
    age: str
    expires: str | None = None
    external_url: str | None = None
    subject: str | None = None
    title: str | None = None
    text: str | None = None
    blocks_nothing: bool | None = None
    grouped: bool | None = None


class SegmentView(_View):
    """One step of a blocker path (``›`` down, ``→`` a hop, a hold or the person)."""

    sep: Literal["›", "→"]
    ref: str | None = None
    title: str | None = None
    label: str | None = None
    reason: str | None = None
    person: bool | None = None
    decision: DecisionRefView | None = None


class NodeLinkView(_View):
    ref: str
    title: str


class WhyItemView(_View):
    html: str
    tone: Literal["you", "ok"] | None = None


class MovingView(_View):
    ref: str
    title: str | None = None
    activity: str


class StatusLineView(_View):
    kind: StatusKind
    label: str
    segments: list[SegmentView]
    ends: StatusEnd
    decision: DecisionRefView | None = None
    also_holds: list[NodeLinkView | str] | None = None
    moving: MovingView | None = None
    detail: str | None = None
    why: list[WhyItemView | str] | None = None
    why_title: str | None = None
    note: str | None = None


class ProgressView(_View):
    value: float
    label: str
    what: Literal["acceptance", "tasks", "projects", "not planned"]
    secondary: float | None = None
    growing: bool | None = None


class GrandchildrenView(_View):
    count: int
    by_state: dict[NodeState, int]
    later: int
    noun: str | None = None


class NeedsView(_View):
    open: int
    blocking: int


class ChangeLinkView(_View):
    ref: str
    label: str


class NodeSummaryView(_View):
    ref: str
    level: str
    level_name: str | None = None
    kind: str
    kind_name: str | None = None
    glyph: str | None = None
    tag: str | None = None
    title: str
    short: str | None = None
    base: str | None = None
    implicit: bool | None = None
    folded_from: list[str] | None = None
    state: NodeState
    col: int | Literal["later"] | None = None
    held_for: str | None = None
    progress: ProgressView | None = None
    status_compact: StatusLineView
    needs: NeedsView
    cost: float | None = None
    burn: float | None = None
    budget: float | None = None
    in_base_env: bool | None = None
    try_ready: bool | None = None
    auto_merge: bool | None = None
    changed: list[str] | None = None
    grandchildren: GrandchildrenView | None = None
    owner: ActorView | None = None
    working: list[ActorView] | None = None
    idle: int | None = None
    change: ChangeLinkView | None = None


class AcceptanceStepView(_View):
    text: str
    result: Literal["pass", "fail", "none"]
    source: str
    ref: str | None = None


class AcceptanceView(_View):
    steps: list[AcceptanceStepView]
    note: str | None = None
    run_at: str | None = None


class EvidenceItemView(_View):
    kind: Literal["request", "signals", "finding", "gap"]
    text: str
    who: str | None = None
    quote: bool | None = None


class EvidenceView(_View):
    summary: str
    items: list[EvidenceItemView]


class TryStepView(_View):
    text: str
    how: str | None = None
    acceptance_step: int | None = None
    api: bool | None = None


class NodeFullView(NodeSummaryView):
    why_html: str
    try_it: list[TryStepView] | None = None
    acceptance: AcceptanceView | None = None
    evidence: EvidenceView | None = None


class CrumbView(_View):
    ref: str
    level: str
    level_name: str | None = None
    title: str
    state: NodeState
    tag: str | None = None
    implicit: bool | None = None
    folded: bool | None = None


class ChipView(_View):
    """A link to another node (kind · target · its state), or a count badge with ``list``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: str | None = None
    ref: str | None = None
    title: str
    state: str | None = None
    tone: Literal["amb", "warn", "acc", "try"] | None = None
    reason: str | None = None
    groups: list[ChipGroupView] | None = Field(None, alias="list")


class ChipGroupView(_View):
    crumb: str | None = None
    chips: list[ChipView]
    note: str | None = None


class VerbView(_View):
    verb: str
    label: str
    key: str | None = None
    enabled: bool
    reason: str | None = None


class ThreadLastView(_View):
    who: str
    at: str
    text: str


class ThreadRefView(_View):
    key: str
    responder: str | None = None
    unread: int
    last: ThreadLastView | None = None


class RollupCountView(_View):
    key: str
    name: str
    plural: str
    count: int
    later: int | None = None


class TeamView(_View):
    working: list[ActorView]
    idle: int


class InBaseView(_View):
    sha: str
    try_it: bool


class SinceView(_View):
    at: str
    items: list[str]
    more: int


class AlarmsClosedView(_View):
    count: int
    ref: str


class RollupView(_View):
    progress: ProgressView | None = None
    counts: list[RollupCountView] | None = None
    cost: float
    budget: float | None = None
    burn: float | None = None
    eta: str | None = None
    team: TeamView | None = None
    in_base_env: InBaseView | None = None
    since: SinceView | None = None
    moving_anywhere: bool
    alarms_closed: AlarmsClosedView | None = None


class LevelCountView(_View):
    level: str
    name: str
    count: int
    later: int
    needs: int
    by_state: dict[NodeState, int]
    debug: bool | None = None


class ReadoutsView(_View):
    day7: str | None = None
    day28: str | None = None


class ResultsView(_View):
    merged: int
    in_base_env: int
    open: list[str]
    readouts: Literal["not measured"] | ReadoutsView


class ColumnView(_View):
    title: str
    col: int


class ChildPageView(_View):
    level: str
    level_name: str | None = None
    columns: list[ColumnView] | None
    later_title: str | None = None
    later_note: str | None = None
    items: list[NodeSummaryView]
    later: list[NodeSummaryView]
    ghosts: list[NodeSummaryView]
    next: str | None = None
    total: int
    grouped_by_state: bool | None = None
    tiles: bool | None = None
    via_fold: int | None = None


class NodePageView(_View):
    """``GET …/n/{ref}``: one node, its breadcrumb, status, roll-up and children."""

    node: NodeFullView
    breadcrumb: list[CrumbView]
    status: StatusLineView
    rollup: RollupView
    chips: list[ChipView] = Field(max_length=3)
    more_chips: int
    chips_more: list[ChipGroupView] | None = None
    verbs: list[VerbView]
    levels_below: list[LevelCountView]
    results: ResultsView | None = None
    children: ChildPageView
    thread: ThreadRefView
    version: str
    home: str | None = None


ChipView.model_rebuild()


def validate_page(page: dict[str, Any]) -> dict[str, Any]:
    """
    Check *page* against :class:`NodePageView` and return it as built.

    :raises pydantic.ValidationError: When the projection drifted from the contract.
    """
    NodePageView.model_validate(page)
    return page


__all__ = [
    "ChildPageView",
    "NodePageView",
    "NodeState",
    "NodeSummaryView",
    "StatusLineView",
    "validate_page",
]
