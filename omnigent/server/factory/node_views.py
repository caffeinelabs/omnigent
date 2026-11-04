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
    row_id: str | None = None
    in_your_move: bool | None = None
    """The page's Your move already asks it: drawn there, not here again."""


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
    full_title: str | None = None
    """The text the short ``title`` stands for (a person's ask, a machine label), for Details."""
    named: Literal["person", "factory", "words"] | None = None
    """Where the short title came from: a person, the factory's namer, or its first words."""
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


class SmokeView(_View):
    ok: bool
    checks: str | None = None


class BaseEnvView(_View):
    """The base env on the root: its branch, the commit it runs and its links."""

    name: str
    branch: str
    sha: str | None = None
    deployed_at: str | None = None
    state: str | None = None
    smoke: SmokeView | None = None
    app_url: str | None = None
    api_url: str | None = None
    note: str | None = None
    ref: str | None = None
    """The project that sets the base env up."""
    area: str | None = None
    """The factory area that shows it (``fx:environments``)."""


class ClosedAlarmView(_View):
    """An alarm the server closed: what it asked, why it closed, the real ask."""

    id: str
    was: str
    cause: str
    real_ask: str | None = None
    real_ask_verb: str | None = None
    ref: str | None = None


class NodeFullView(NodeSummaryView):
    why_html: str
    try_it: list[TryStepView] | None = None
    acceptance: AcceptanceView | None = None
    evidence: EvidenceView | None = None
    env: BaseEnvView | None = None
    closed_alarms: list[ClosedAlarmView] | None = None
    levels: bool | None = None
    """``fx:definition`` only: the page shows the levels (``GET …/levels``)."""


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
    """Unread conversation (people's and agents' words, live cards for this viewer)."""
    activity: int | None = None
    """System activity in the thread since this viewer's read mark (never shown inline)."""
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
    in_base_env: int | None = None
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


class YourMoveActionView(_View):
    """A row's one primary action: answer with ``option``, open ``url``, or open the ask."""

    label: str
    option: str | None = None
    url: str | None = None


class YourMoveItemView(_View):
    """One thing the viewer must do: "Apply C-16 · unblocks Agent assistant · [Apply]"."""

    id: str
    """The ask: a decision (``D-45``) or a group of merges (``merge:main``)."""
    ref: str
    """The node it sits on (where its card and details are)."""
    verb: str
    object: str
    action: YourMoveActionView
    decision: DecisionRefView
    weight: float
    unblocks: str | None = None
    """
    What answering it lets move, by short name ("Agent assistant"); the page's
    own node is "this task" ("this task and Assistant panel UX").
    """
    unblocks_ref: str | None = None
    """The first of ``unblocks_refs`` that is not the page's own node (where a row links)."""
    unblocks_refs: list[str] | None = None
    object_name: str | None = None
    """What a proposal changes in a few words ("Update assistant core"), after its code."""
    about: str | None = None
    """A question's few words (``Start a follow-up project``), for an ask that is no apply."""
    blocks_nothing: bool | None = None


class YourMoveView(_View):
    """What the viewer must do here, most held work first; ``more`` waits behind "+n"."""

    items: list[YourMoveItemView] = Field(max_length=3)
    more: int


class NodePageView(_View):
    """``GET …/n/{ref}``: one node, its breadcrumb, status, roll-up and children."""

    node: NodeFullView
    breadcrumb: list[CrumbView]
    your_move: YourMoveView
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
    decided_since: int | None = Field(None, ge=1)
    """Decisions the factory made in this subtree since the viewer's last look (left out at 0)."""


class GroupParentView(CrumbView):
    ancestors: list[str]
    evidence: str | None = None


class LevelGroupView(_View):
    parent: GroupParentView
    items: list[NodeSummaryView] = Field(max_length=60)
    folded_done: bool | None = None
    later: list[str] | None = None
    next: str | None = None


class DoneGroupsView(_View):
    count: int
    titles: list[str]
    in_base_env: bool
    noun: str | None = None


class LevelViewView(_View):
    """``GET …/n/{ref}/level/{level}``: one level under a node, grouped under parents."""

    scope: str
    level: str
    level_name: str | None = None
    parent_level_name: str | None = None
    total: int
    groups: list[LevelGroupView]
    done_groups: DoneGroupsView | None = None
    tiles: bool | None = None
    via_fold: int | None = None


class LinkView(_View):
    """A lineage link outside the breadcrumb: the proposal that approved a feature."""

    kind: str
    ref: str | None = None
    title: str
    state: str | None = None


class LineageRowView(CrumbView):
    """One row of the lineage: a node with its one line (and its evidence or links)."""

    line: StatusLineView
    why_html: str | None = None
    progress: ProgressView | None = None
    evidence: str | None = None
    links: list[LinkView] | None = None


class LineageView(_View):
    """``GET …/n/{ref}/lineage``: ancestors top to bottom, the node, the levels below, results."""

    scope: str
    ancestors: list[LineageRowView]
    here: LineageRowView
    below: list[LevelCountView]
    results: ResultsView | None = None
    version: str


class BarView(_View):
    """One bar of a timeline lane (``from``/``to`` are ISO instants)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    start: str = Field(alias="from")
    end: str = Field(alias="to")
    kind: Literal["work", "done", "wait", "you"]
    label: str


class LaneView(_View):
    ref: str
    title: str
    state: NodeState
    bars: list[BarView] = Field(min_length=1)


class TimelineView(_View):
    """``GET …/n/{ref}/timeline``: one lane per child, bars over the window."""

    scope: str
    axis: list[str] = Field(min_length=2, max_length=2)
    now: str
    last_look: str | None = None
    lanes: list[LaneView]
    version: str


class MoneyRowView(_View):
    ref: str
    title: str
    tag: str | None = None
    state: NodeState
    cost: float
    budget: float | None = None
    burn: float | None = None


class MoneyView(_View):
    """``GET …/n/{ref}/money``: the children's cost against their budgets, most first."""

    scope: str
    rows: list[MoneyRowView]
    total: float
    budget: float | None = None
    rest: float | None = None
    version: str


class NeedsItemView(_View):
    """One ask a person owes: its decision, where it sits, what it holds and its weight."""

    decision: DecisionRefView
    breadcrumb: list[CrumbView] = Field(min_length=1)
    holds: list[str]
    weight: float
    move: YourMoveItemView | None = None
    """The row Your move shows for this ask on the tree's root page (short names, one action)."""


class NeedsYouView(_View):
    """``GET /factories/{fid}/needs-you``: one tree's asks, ranked by what they hold."""

    tree: Literal["product", "factory"]
    items: list[NeedsItemView]
    blocks_nothing: list[NeedsItemView]
    closed_alarms: list[ClosedAlarmView] | None = None
    other_tree: int
    version: str


class FindItemView(_View):
    ref: str
    level: str
    level_name: str | None = None
    title: str
    tag: str | None = None
    state: NodeState
    implicit: bool | None = None
    breadcrumb: list[CrumbView]
    match: Literal["ref", "ask", "proposal", "title", "needs"]
    via: str | None = None


class FindView(_View):
    """``GET /factories/{fid}/find?q=``: nodes by ref or title, with breadcrumbs."""

    q: str
    items: list[FindItemView]
    version: str


class SeenView(_View):
    """``PUT …/n/{ref}/seen``: this person's last look at the node, now."""

    ref: str
    seen_at: str


ChipView.model_rebuild()


def validate_page(page: dict[str, Any]) -> dict[str, Any]:
    """
    Check *page* against :class:`NodePageView` and return it as built.

    :raises pydantic.ValidationError: When the projection drifted from the contract.
    """
    NodePageView.model_validate(page)
    return page


def validate_level(view: dict[str, Any]) -> dict[str, Any]:
    """
    Check *view* against :class:`LevelViewView` and return it as built.

    :raises pydantic.ValidationError: When the projection drifted from the contract.
    """
    LevelViewView.model_validate(view)
    return view


def validate(model: type[BaseModel], body: dict[str, Any]) -> dict[str, Any]:
    """
    Check *body* against *model* and return it as built.

    :raises pydantic.ValidationError: When the projection drifted from the contract.
    """
    model.model_validate(body)
    return body


__all__ = [
    "ChildPageView",
    "FindView",
    "LevelViewView",
    "LineageView",
    "MoneyView",
    "NeedsYouView",
    "NodePageView",
    "NodeState",
    "NodeSummaryView",
    "SeenView",
    "StatusLineView",
    "TimelineView",
    "validate",
    "validate_level",
    "validate_page",
]
