"""One "Needs you" list: everything that waits on a person, one row per subject.

The new cockpit's Home, its phone tab bar and the Slack digest of someone on
the new cockpit all read this list, so they tell one story with one count.
:func:`build_asks` is pure and deterministic (no LLM, no I/O): it reads one
viewer's filtered snapshot (open decisions, PRs, projects, members), the
open incidents and the pending, co-signing and trial proposals, and returns
the ``AsksResponse`` JSON. :func:`asks_from_raw` gathers those inputs with the
status summary's loader (:func:`.status_summary.load_inputs`, which takes
the open proposals from the snapshot), so Home and the Now card read the
same facts.

**Kinds.** ``decision`` (a question an agent or the server asked), ``merge``
(ready pull requests), ``bet`` (a projects proposal that carries a bet),
``source``, ``profile`` and ``goal`` (definition proposals that change the
discovery sources, the product profile or the goals), ``readout`` (keeping or
dropping a measured bet, or answering "did it help?"), ``broken``
(something stopped that only a person can restart or close) and ``proposal``
(any other proposal a person approves).

**One row per subject.**

- Every ready PR the same person merges folds into one ``merge`` row, with
  at most :data:`ITEMS_MAX` sub-items and ``more_count`` for the rest; a
  viewer who merges as an operator and as a project's owner gets one row.
- An incident folds into the row of what it is about: a ready PR's merge
  row, or the decision whose options act on the same target (an agent that
  could not restart, a loop out of budget), as long as that row waits on
  the viewer whenever the incident does.
- A trial's "Keep or revert" always shows (a person decides it); a running
  trial is a ``handled`` sentence.
- An incident shows on its own only when a person must act
  (:func:`needs_person`); incidents the factory heals itself (an agent that
  is stuck until it moves again) become one calm ``handled`` sentence.
- An agent's proposal to change the definition (tuning, rules, method) waits
  in Steer and shows here only within :data:`DEADLINE_SOON_S` of its deadline
  or when it blocks something; proposals that change the sources, the
  product profile or the goals always show, since discovery waits on them.

**Order.** By the cost of waiting (:func:`cost_of_waiting`): something
broken first, then what blocks work, ready merges (more for each project
they hold), deadlines that come soon, then the rest; the longest wait first
within the same cost.

Every ``href`` is a cockpit path (``/factory/<id>/…``) the classic cockpit
serves and the new one redirects. Times are epoch seconds; ``age_s`` is as
of ``generated_at``. Nothing here names a product: every word that is not
fixed copy comes from the factory's own records.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import issues
from omnigent.server.factory import status_summary as summary_mod
from omnigent.server.factory.decision_routing import is_mine_by
from omnigent.server.factory.stuck import STUCK_RULES

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryIssue
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.snapshot import RawSnapshot

ITEMS_MAX: Final = 5
"""A row lists at most this many sub-items (ready PRs), then ``more_count``."""
DEADLINE_SOON_S: Final = 24 * 3600
"""An agent's definition proposal shows once its deadline is this close."""
TITLE_MAX: Final = 140
TEXT_MAX: Final = 200
SELF_HEALING_RULES: Final = frozenset({*STUCK_RULES, "mail.stuck"})
"""Incident rules that clear by themselves when the condition does (unless critical)."""
_WAIT_SEVERITIES: Final = summary_mod.WAIT_SEVERITIES
_ROLE_ASSIGNEES: Final = frozenset({"", "operators", "owners"})
_PROPOSAL_REF_RE: Final = re.compile(r"\bC-(\d+)\b")
_MARKDOWN_RE: Final = re.compile(r"[*_`#>]+")
_HEX_ID_RE: Final = re.compile(r"^[0-9a-fA-F-]{32,36}$")
_PL_PREFIX: Final = "PL · "

AskKind = Literal[
    "decision", "merge", "bet", "source", "readout", "broken", "profile", "goal", "proposal"
]
_DEFINITION_KINDS: Final = ("source", "profile", "goal")

_BASE_COST: Final[dict[str, int]] = {
    "broken": 90,
    "merge": 50,
    "bet": 45,
    "readout": 40,
    "source": 35,
    "profile": 35,
    "goal": 35,
    "proposal": 30,
    "decision": 30,
}
"""The cost of waiting each kind starts from (see :func:`cost_of_waiting`)."""


# ── The response ───────────────────────────────────────────────


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AskOption(_Model):
    """
    One answer a person can give in place (a decision's option).

    :param href: A link-only option: it opens a page instead of answering.
    :param reasons: The answer must name one of these (a required picker).
    """

    id: str
    label: str
    recommended: bool = False
    description: str | None = None
    href: str | None = None
    reasons: list[str] | None = None


class AskAction(_Model):
    """
    The row's one main button.

    :param kind: ``answer`` (send ``option_id`` to the decision), ``open``
        (go to ``href``) or ``peek`` (open the side panel ``peek`` on Home, e.g.
        ``merge``, with ``href`` as the page for anyone without one).
    """

    kind: Literal["answer", "open", "peek"]
    label: str
    href: str | None = None
    option_id: str | None = None
    peek: str | None = None


class AskItem(_Model):
    """
    One sub-item of a row, e.g. one ready PR of a merge row.

    :param title: What users get from it (its project's outcome), else its title.
    """

    ref: str
    title: str
    project: str | None = None
    pr_id: str | None = None
    url: str | None = None
    since: int | None = None
    href: str


class AskCost(_Model):
    """
    What waiting on the row costs, as one fixed-rule score and its reason.

    :param score: Higher waits worse (see :func:`cost_of_waiting`).
    :param level: ``high`` (70 and over), ``medium`` (40 and over) or ``low``.
    :param text: Why, in plain words, e.g. ``"blocks the search team"``.
    :param holds: Projects or agents that cannot go on until a person acts.
    """

    score: int
    level: Literal["high", "medium", "low"]
    text: str
    holds: int = 0


class Ask(_Model):
    """
    One thing that waits on a person, one row per subject.

    :param id: Stable per subject: ``D-41``, ``C-31``, ``INC-26``, ``merge``.
    :param subject: What it is about: ``decision:D-41``, ``proposal:C-31``,
        ``issue:INC-26``, ``prs:ready``.
    :param ref: The small id link shown after the title, when it has one.
    :param context: One line of why or what, from the factory's own records.
    :param meta: Short facts: the project, who asks, whom it is for.
    :param since: When it started waiting (epoch seconds); ``age_s`` as of the build.
    :param options: Answers a person can give in place (decisions only).
    :param items: Up to :data:`ITEMS_MAX` sub-items; ``more_count`` the rest.
    :param assignee: Whom it waits on from the viewer's side: ``you``,
        ``operators``, ``owners`` or a person's short name.
    :param mine: Assigned to the viewer by name (or claimed by them).
    :param decision_id: The decision an in-place answer goes to.
    :param deadline_at: When it defaults by itself, and ``deadline_effect`` what happens.
    :param folded: Refs of incidents folded into this row (``INC-23``).
    :param covers: The status summary's waiting keys this row stands for.
    :param critical: Something is down or about to be (a broken row's incident).
    :param opportunity: The opportunity a bet row comes from (``O-15``).
    """

    id: str
    kind: AskKind
    subject: str
    ref: str | None = None
    title: str
    context: str | None = None
    meta: list[str] = Field(default_factory=list)
    since: int
    age_s: int
    cost_of_waiting: AskCost
    primary_action: AskAction
    options: list[AskOption] | None = None
    allow_text: bool = False
    items: list[AskItem] = Field(default_factory=list)
    more_count: int = 0
    assignee: str
    mine: bool = False
    project: str | None = None
    decision_id: str | None = None
    deadline_at: int | None = None
    deadline_effect: str | None = None
    folded: list[str] = Field(default_factory=list)
    covers: list[str] = Field(default_factory=list)
    critical: bool = False
    opportunity: str | None = None
    href: str


class AskCounts(_Model):
    """
    :param total: Rows that need the viewer (the one Needs you count).
    :param mine: Of those, assigned to the viewer by name.
    :param others: Rows that wait on other people.
    :param in_steer: Agents' definition proposals that wait in Steer, not here.
    :param folded: Incidents folded into another row.
    """

    total: int
    mine: int = 0
    others: int = 0
    in_steer: int = 0
    folded: int = 0


class AsksResponse(_Model):
    """``GET /v1/factory/factories/{fid}/asks``: the viewer's Needs you, costliest first."""

    factory_id: str
    generated_at: int
    asks: list[Ask]
    handled: list[str] = Field(default_factory=list)
    counts: AskCounts


# ── Inputs ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class AskInputs:
    """
    Everything :func:`build_asks` reads.

    :param snapshot: The viewer's filtered ``FactorySnapshot`` JSON.
    :param user_id: The viewer (``None`` when auth is off: every lever is theirs).
    :param level: The viewer's session level on the factory root.
    :param incidents: Open or acked incidents the viewer may see (any severity).
    :param proposals: Pending, co-signing and trial proposals (``ProposalView``-like
        dicts: ``number``, ``kind``, ``status``, ``title``, ``before``, ``after``,
        ``author_kind``, ``opportunity``, ``bet``, ``case_against``, ...).
    :param owners: Project slug -> its ``human_owner`` on the roster.
    """

    snapshot: Mapping[str, Any]
    user_id: str | None
    level: int
    incidents: Sequence[FactoryIssue] = ()
    proposals: Sequence[Mapping[str, Any]] = ()
    owners: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_status(cls, inputs: summary_mod.StatusInputs) -> AskInputs:
        """The asks of the viewer a status summary is built for."""
        return cls(
            snapshot=inputs.snapshot,
            user_id=inputs.user_id,
            level=inputs.level,
            incidents=inputs.incidents,
            proposals=inputs.proposals,
            owners=inputs.owners,
        )


# ── Small helpers ──────────────────────────────────────────────


def _clip(text: str | None, limit: int = TEXT_MAX) -> str | None:
    return summary_mod.clip_words(text, limit)


def _first_line(text: str | None) -> str | None:
    """The first non-empty line of a markdown text, without its emphasis marks."""
    for line in (text or "").splitlines():
        plain = " ".join(_MARKDOWN_RE.sub("", line).split()).strip(" -")
        if plain:
            return plain
    return None


def _plural(n: int, noun: str, many: str | None = None) -> str:
    return f"{n} {noun if n == 1 else (many or noun + 's')}"


def _norm_target(target: str | None) -> str | None:
    """``member:<id>`` with the id as bare lowercase hex; other targets as they are."""
    if not target:
        return None
    kind, _, rest = target.partition(":")
    if rest and _HEX_ID_RE.match(rest):
        rest = rest.replace("-", "").lower()
    return f"{kind}:{rest}"


def _norm_id(session_id: str | None) -> str:
    return (session_id or "").replace("-", "").lower()


def _person(user_id: str) -> str:
    return user_id.split("@")[0] or user_id


_ROLE_WORD: Final = {
    "ic": "builder",
    "reviewer": "reviewer",
    "demo": "demo agent",
    "env_keeper": "env keeper",
    "planner": "planner",
}
"""What a team member without a handle is called before its session title."""


def member_name(member: Mapping[str, Any]) -> str:
    """
    How a row names an agent: its handle (``Nova``), else ``the search lead``,
    a lead's name (``Atlas``) or ``the builder <title>``.
    """
    handle = str(member.get("handle") or "").strip()
    if handle:
        spaced = handle.replace("-", " ")
        return spaced[:1].upper() + spaced[1:]
    title = str(member.get("title") or "").strip()
    if member.get("role") == "project_lead" or title.startswith(_PL_PREFIX):
        slug = title[len(_PL_PREFIX) :].strip() if title.startswith(_PL_PREFIX) else None
        slug = slug or member.get("project")
        return f"the {slug} lead" if slug else "a project lead"
    word = _ROLE_WORD.get(str(member.get("role") or ""))
    if word:
        return f"the {word} {title}" if title else f"a {word}"
    return title.rpartition(" · ")[2].strip() or "an agent"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _expected_text(expected: Any) -> str | None:
    """A bet's predicted change, e.g. ``up +1 to +4 pp``."""
    if not isinstance(expected, Mapping):
        return None
    low, high = expected.get("low"), expected.get("high")
    if not isinstance(low, int | float) or not isinstance(high, int | float):
        return None
    unit = str(expected.get("unit") or "").strip()
    return f"{expected.get('direction', '')} {low:+g} to {high:+g} {unit}".strip()


class _Links:
    """Cockpit paths (the classic ones; the new cockpit redirects each to its place)."""

    def __init__(self, factory_id: str) -> None:
        self.base = f"/factory/{factory_id}"

    def decision(self, ref: str) -> str:
        return f"{self.base}/decisions?decision={ref}"

    def proposal(self, ref: str) -> str:
        return f"{self.base}/improvements/{ref}"

    def pr(self, pr_id: str) -> str:
        return f"{self.base}/prs/{pr_id}"

    def incident(self, number: int) -> str:
        return f"{self.base}/alerts/{number}"

    def home(self, peek: str) -> str:
        return f"{self.base}?peek={peek}"


# ── Classification ─────────────────────────────────────────────


def proposal_paths(row: Mapping[str, Any]) -> list[str]:
    """The definition paths a proposal touches (its ``before``/``after`` keys, else its ops)."""
    paths: list[str] = []
    for side in ("before", "after"):
        value = row.get(side)
        if isinstance(value, Mapping):
            paths += [str(p) for p in value if str(p) not in paths]
    if not paths:
        for op in row.get("ops") or []:
            if isinstance(op, Mapping) and op.get("path") and str(op["path"]) not in paths:
                paths.append(str(op["path"]))
    return paths


def _under(paths: Iterable[str], prefix: str) -> list[str]:
    return [p for p in paths if p == prefix or p.startswith((f"{prefix}.", f"{prefix}/"))]


def definition_kind(paths: Sequence[str]) -> AskKind:
    """
    What a definition proposal asks a person, by the paths it touches.

    Sources first (an owner lets evidence in), then the product profile, then
    goals; anything else is a plain ``proposal``.
    """
    if _under(paths, "discovery.sources"):
        return "source"
    if _under(paths, "product"):
        return "profile"
    if _under(paths, "strategy.goals"):
        return "goal"
    return "proposal"


def needs_person(issue: FactoryIssue) -> bool:
    """
    Whether an open incident needs a person (rather than healing itself).

    A person was named on it, or someone filed it as a report, or it is
    critical (a stuck agent the factory could not get going again too), or it
    is at warning or worse and its rule is not one that clears by itself
    (:data:`SELF_HEALING_RULES`): a stopped agent, a loop out of budget, a
    spend alert, a gone preview, a broken token, and any rule this list does
    not know yet.
    """
    if (issue.assignee or "") not in _ROLE_ASSIGNEES or issue.rule == issues.HUMAN_RULE:
        return True
    if issue.severity == "critical":
        return True
    return issue.severity in _WAIT_SEVERITIES and issue.rule not in SELF_HEALING_RULES


def heals_itself(issue: FactoryIssue) -> bool:
    """An incident at warning or worse that clears by itself (see :data:`SELF_HEALING_RULES`)."""
    return (
        issue.rule in SELF_HEALING_RULES
        and issue.severity in _WAIT_SEVERITIES
        and not needs_person(issue)
    )


def cost_of_waiting(
    kind: str,
    *,
    now: float,
    text: str,
    blocking: bool = False,
    critical: bool = False,
    holds: int = 0,
    deadline_at: int | None = None,
) -> AskCost:
    """
    The cost of waiting, by fixed rules.

    Each kind starts from :data:`_BASE_COST` (broken 90, critical 100); a
    blocking ask costs at least 70; a merge adds 5 per project it holds (up
    to 6); a deadline adds 30 within an hour, 15 within 6 h and 5 within 24 h.
    """
    score = _BASE_COST.get(kind, 30)
    if kind == "broken" and critical:
        score = 100
    if blocking:
        score = max(score, 70)
    if kind == "merge":
        score += 5 * min(holds, 6)
    if deadline_at is not None:
        left = deadline_at - now
        score += 30 if left <= 3600 else 15 if left <= 6 * 3600 else 5 if left <= 86_400 else 0
    level: Literal["high", "medium", "low"] = (
        "high" if score >= 70 else "medium" if score >= 40 else "low"
    )
    return AskCost(score=score, level=level, text=text, holds=holds)


# ── Building rows ──────────────────────────────────────────────


@dataclass
class _Draft:
    """A row being built (the viewer's or someone else's)."""

    ask: dict[str, Any]
    for_you: bool
    targets: set[str] = field(default_factory=set)
    pr_ids: set[str] = field(default_factory=set)


@dataclass
class _Ctx:
    inputs: AskInputs
    now: float
    links: _Links
    members: dict[str, Mapping[str, Any]]
    projects: dict[str, Mapping[str, Any]]
    proposals: dict[str, Mapping[str, Any]]

    @property
    def user_id(self) -> str | None:
        return self.inputs.user_id

    @property
    def operator(self) -> bool:
        return self.inputs.user_id is None or self.inputs.level >= LEVEL_EDIT

    def project_title(self, slug: str | None) -> str | None:
        """What users get from a project: its outcome, else its title."""
        project = self.projects.get(slug or "")
        if project is None:
            return None
        charter = project.get("charter") or {}
        return _clip(charter.get("outcome") or project.get("title") or slug, TITLE_MAX)


def _who(assignee: str | None, claimed_by: str | None, user_id: str | None) -> str:
    return summary_mod.decision_who(assignee, claimed_by, user_id)


def _mine(assignee: str | None, claimed_by: str | None, user_id: str | None) -> bool:
    if user_id is None:
        return False
    return claimed_by == user_id if claimed_by else assignee == user_id


def _asker(decision: Mapping[str, Any], ctx: _Ctx) -> str | None:
    """Who asked: the asking agent's name, ``None`` for the server."""
    sid = decision.get("asked_by_session_id")
    if not sid:
        return None
    member = ctx.members.get(_norm_id(str(sid)))
    if member is not None:
        return member_name(member)
    return member_name(
        {
            "title": decision.get("asked_by_title"),
            "role": decision.get("asked_by_role"),
            "project": decision.get("project_slug"),
        }
    )


def _options(decision: Mapping[str, Any]) -> list[AskOption]:
    return [
        AskOption(
            id=str(option["id"]),
            label=str(option.get("label") or option["id"]),
            recommended=bool(option.get("recommended")),
            description=option.get("description"),
            href=option.get("href"),
            reasons=list(option["reasons"]) if option.get("reasons") else None,
        )
        for option in decision.get("options") or []
        if option.get("id")
    ]


def _answer_action(options: Sequence[AskOption], fallback: AskAction) -> AskAction:
    """The recommended one-click answer, else the first, else *fallback* (open the card)."""
    one_click = [o for o in options if not o.href and not o.reasons]
    pick = next((o for o in one_click if o.recommended), None) or (
        one_click[0] if len(one_click) == 1 else None
    )
    if pick is None:
        return fallback
    return AskAction(kind="answer", label=pick.label, option_id=pick.id)


def _proposal_ref(decision: Mapping[str, Any]) -> str | None:
    subject = str(decision.get("subject") or "")
    if subject.startswith("proposal:"):
        found = _PROPOSAL_REF_RE.search(subject)
        if found:
            return f"C-{found.group(1)}"
    found = _PROPOSAL_REF_RE.search(str(decision.get("question") or ""))
    return f"C-{found.group(1)}" if found else None


def _author(row: Mapping[str, Any] | None, decision: Mapping[str, Any], ctx: _Ctx) -> str | None:
    """Who filed a proposal: an agent's name, a person's short name."""
    if row is not None and row.get("author_kind") == "human" and row.get("author"):
        return _person(str(row["author"]))
    if row is not None and row.get("author_kind") == "agent" and row.get("author"):
        member = ctx.members.get(_norm_id(str(row["author"])))
        if member is not None:
            return member_name(member)
        if row.get("author_title"):
            return member_name({"title": row["author_title"], "role": row.get("author_role")})
    return _asker(decision, ctx)


def _evaluated(row: Mapping[str, Any] | None) -> bool:
    """Whether a trial has been evaluated (its result carries the evaluation)."""
    result = (row or {}).get("result")
    return isinstance(result, Mapping) and isinstance(result.get("evaluation"), Mapping)


def keep_or_revert(decision: Mapping[str, Any], row: Mapping[str, Any] | None) -> bool:
    """
    Whether a proposal decision is a trial's "Keep or revert": a person
    decides it whoever proposed the change, so it never waits in Steer.
    """
    return str(decision.get("question") or "").startswith("Keep or revert") or (
        row is not None and row.get("status") == "trial" and _evaluated(row)
    )


def trial_running(row: Mapping[str, Any], now: float) -> bool:
    """A trial whose window is still open and that nobody has evaluated yet."""
    until = row.get("trial_until")
    return (
        row.get("status") == "trial"
        and isinstance(until, int | float)
        and not isinstance(until, bool)
        and until > now
        and not _evaluated(row)
    )


def _deadline(decision: Mapping[str, Any]) -> int | None:
    at = decision.get("deadline_at")
    return int(at) if isinstance(at, int | float) and not isinstance(at, bool) else None


def _side(row: Mapping[str, Any], side: str, path: str) -> Any:
    """A proposal's value at *path* before or after it applies (``None`` when absent)."""
    values = row.get(side)
    return values.get(path) if isinstance(values, Mapping) else None


def _source_title(row: Mapping[str, Any], paths: Sequence[str]) -> str:
    for path in _under(paths, "discovery.sources"):
        before, after = _side(row, "before", path), _side(row, "after", path)
        value = after if isinstance(after, Mapping) else before
        value = value if isinstance(value, Mapping) else {}
        label = _clip(str(value.get("label") or value.get("id") or path.rpartition("/")[2]), 80)
        if after is None and before is not None:
            return f"Approve removing a source: {label}"
        if before is None:
            return f"Approve a new source: {label}"
        return f"Approve a change to a source: {label}"
    return "Approve a change to the sources"


def _goal_title(row: Mapping[str, Any], paths: Sequence[str]) -> str:
    for path in _under(paths, "strategy.goals"):
        before, after = _side(row, "before", path), _side(row, "after", path)
        if isinstance(after, Mapping) and after.get("statement"):
            statement = _clip(str(after["statement"]), TITLE_MAX)
            return f"Set a goal: {statement}" if before is None else f"Change a goal: {statement}"
        if after is None and isinstance(before, Mapping):
            return f"Remove a goal: {_clip(str(before.get('statement') or path), TITLE_MAX)}"
    return "Review a change to the goals"


def _bet_context(row: Mapping[str, Any], ctx: _Ctx) -> str | None:
    raw = row.get("bet")
    bet: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    parts: list[str] = []
    if row.get("opportunity"):
        parts.append(f"From {row['opportunity']}.")
    expected = _expected_text(bet.get("expected"))
    metric = _clip(str(bet.get("metric") or ""), 80)
    if expected:
        parts.append(f"Expects {metric}: {expected}." if metric else f"Expects {expected}.")
    if row.get("case_against"):
        cosigner = ctx.members.get(_norm_id(str(row.get("cosigned_by") or "")))
        who = member_name(cosigner) if cosigner is not None else "The other lead"
        parts.append(f"{_cap(who)} co-signed it with a case against.")
    return _clip(" ".join(parts)) if parts else _clip(row.get("rationale"))


def _decision_draft(decision: Mapping[str, Any], ctx: _Ctx, in_steer: list[str]) -> _Draft | None:
    """A row for an open decision, or ``None`` when it waits in Steer instead."""
    user_id, links = ctx.user_id, ctx.links
    ref = str(decision["ref"])
    assignee, claimed = decision.get("assignee"), decision.get("claimed_by")
    for_you = is_mine_by(assignee, claimed, user_id, ctx.inputs.level)
    source = decision.get("source")
    blocking = bool(decision.get("blocking"))
    deadline = _deadline(decision)
    project = decision.get("project_slug")
    asker = _asker(decision, ctx)
    options = _options(decision)
    open_card = AskAction(kind="open", label="Answer", href=links.decision(ref))
    question = _clip(str(decision.get("question") or ""), TITLE_MAX) or ref
    meta: list[str] = [str(project)] if project else []
    kind: AskKind = "decision"
    subject = f"decision:{ref}"
    shown_ref = ref
    href = links.decision(ref)
    context = _clip(_first_line(decision.get("context")))
    in_place: list[AskOption] | None = options
    row: Mapping[str, Any] | None = None
    action: AskAction
    cost_text = "blocks its asker" if blocking else "the factory goes on without it"
    if blocking and project:
        cost_text = f"blocks the {project} team"
    elif blocking and asker:
        cost_text = f"blocks {asker}"
    if source == "proposal":
        pref = _proposal_ref(decision)
        row = ctx.proposals.get(pref or "")
        if row is not None and row.get("status") == "awaiting_cosign":
            return None
        author = _author(row, decision, ctx)
        shown_ref = pref or ref
        subject = f"proposal:{shown_ref}"
        href = links.proposal(shown_ref) if pref else href
        in_place = None
        proposes = summary_mod._PROPOSES_RE.match(str(decision.get("question") or ""))
        title_text = _clip(
            str((row or {}).get("title") or (proposes["title"] if proposes else "")), TITLE_MAX
        )
        if row is not None and row.get("kind") != "projects":
            paths = proposal_paths(row)
            kind = definition_kind(paths)
            agent = row.get("author_kind") != "human"
            soon = deadline is not None and deadline - ctx.now <= DEADLINE_SOON_S
            readout = keep_or_revert(decision, row)
            if kind == "proposal" and agent and not blocking and not soon and not readout:
                in_steer.append(shown_ref)
                return None
        elif row is not None and row.get("bet"):
            kind = "bet"
        else:
            kind = "proposal"
        context = _clip(row.get("rationale")) if row is not None else context
        if kind == "bet" and row is not None:
            title = f"Approve a bet: {title_text or question}"
            context = _bet_context(row, ctx)
            action = AskAction(kind="open", label="Review the bet", href=href)
            cost_text = "a bet waits for its approval"
        elif kind == "source" and row is not None:
            title = _source_title(row, proposal_paths(row))
            action = AskAction(kind="open", label="Review the source", href=href)
            cost_text = "discovery reads only approved sources"
        elif kind == "profile":
            drafted = f"Review the product profile {author} drafted" if author else None
            title = drafted or "Review a change to the product profile"
            action = AskAction(kind="open", label="Review the profile", href=href)
            cost_text = "discovery sorts what it learns by the profile"
        elif kind == "goal" and row is not None:
            title = _goal_title(row, proposal_paths(row))
            action = AskAction(kind="open", label="Review the goal", href=href)
            cost_text = "opportunities are ranked against goals"
        else:
            if keep_or_revert(decision, row):
                title = f"Keep or revert {shown_ref}: {title_text}" if title_text else question
            else:
                whose = f"{author}'s proposal" if author else "a proposal"
                title = f"Approve {whose}: {title_text}" if title_text else _cap(question)
            action = AskAction(kind="open", label="Review the proposal", href=href)
            cost_text = cost_text if blocking else "it expires on its own"
        if author:
            meta.append(author)
    elif source == "readout":
        kind = "readout"
        title = _cap(question)
        subject = str(decision.get("subject") or subject)
        option_href = next((o.href for o in options if o.href), None)
        action = _answer_action(
            options, AskAction(kind="open", label="Open the readout", href=option_href or href)
        )
        if any(o.id == "answer" for o in options):
            # "Did C-n help?": a person's verdict is the measurement itself.
            cost_text = "the readout waits for your verdict; until then the bet is not measured"
        else:
            cost_text = "a measured bet waits for keep or drop; what it taught waits with it"
    elif source == "relaunch_failed":
        kind = "broken"
        # The question opens with the stopped agent's quoted title (the server asks).
        title = f"{_cap(summary_mod._subject_agent(decision))} stopped and could not restart"
        context = question
        action = _answer_action(options, open_card)
        cost_text = f"the {project} project is stopped" if project else "an agent is stopped"
    else:
        said = summary_mod._server_decision_text(decision)
        if said is not None:
            title = _cap(said)
            context = question if question != title else context
        elif asker:
            title = f"{_cap(asker)} asks: {question}"
            meta.append(asker)
        else:
            title = _cap(question)
        action = _answer_action(options, open_card)
    who = _who(assignee, claimed, user_id)
    if who in ("owners", "operators"):
        meta.append(f"for {who}")
    since = int(decision.get("created_at") or 0)
    # A deadline says what happens when nobody acts ("expires and nothing is started").
    if deadline is not None and kind != "broken" and decision.get("deadline_effect"):
        cost_text = str(decision["deadline_effect"])
    ask: dict[str, Any] = {
        "id": shown_ref,
        "kind": kind,
        "subject": subject,
        "ref": shown_ref,
        "title": title,
        "context": context,
        "meta": meta,
        "since": since,
        "age_s": max(0, int(ctx.now) - since),
        "cost_of_waiting": cost_of_waiting(
            kind,
            now=ctx.now,
            text=cost_text,
            blocking=blocking,
            holds=1 if blocking else 0,
            deadline_at=deadline,
        ),
        "primary_action": action,
        "options": in_place,
        "allow_text": bool(decision.get("allow_text", True)) and in_place is not None,
        "assignee": who,
        "mine": _mine(assignee, claimed, user_id),
        "project": project,
        "decision_id": str(decision["id"]),
        "deadline_at": deadline,
        "deadline_effect": decision.get("deadline_effect") if deadline is not None else None,
        "covers": [f"decision:{decision['id']}"],
        "opportunity": (row or {}).get("opportunity") if kind == "bet" else None,
        "href": href,
    }
    targets = {
        target
        for target in (_norm_target(o.get("target")) for o in decision.get("options") or [])
        if target
    }
    if decision.get("subject"):
        targets.add(str(decision["subject"]))
    return _Draft(ask=ask, for_you=for_you, targets=targets)


def _pr_who(ctx: _Ctx, project: str | None) -> tuple[str | None, str, bool, bool]:
    """
    ``(owner, who, for_you, mine)`` of a ready PR: its project's human owner
    merges it, else the operators (``owner`` ``None``).
    """
    owner = ctx.inputs.owners.get(project or "")
    user_id = ctx.user_id
    if owner:
        mine = user_id is None or owner == user_id
        return owner, ("you" if user_id is not None and mine else _person(owner)), mine, mine
    return None, "operators", ctx.operator, False


def _merge_context(prs: Sequence[Mapping[str, Any]], policy: str | None) -> str | None:
    n = len(prs)
    gates = [pr.get("gates") or {} for pr in prs]
    green = sum(1 for g in gates if g.get("checks") == "pass")
    demos = sum(1 for g in gates if g.get("demo") == "pass")
    parts: list[str] = []
    if n == 1:
        if green:
            parts.append("Its checks are green" + (" and its demo passed." if demos else "."))
    elif green == n:
        parts.append(
            f"Demos passed and checks are green on all {n}."
            if demos == n
            else f"Checks are green on all {n}."
        )
    elif green:
        parts.append(f"Checks are green on {green} of {n}.")
    if policy:
        parts.append(policy if policy.endswith(".") else f"{policy}.")
    return _clip(" ".join(parts), 300) if parts else None


def _merge_drafts(ctx: _Ctx) -> list[_Draft]:
    """One merge row per person who merges: every ready PR they wait on, oldest first."""
    body = ctx.inputs.snapshot
    groups: dict[tuple[str | None, str, bool, bool], list[Mapping[str, Any]]] = {}
    for pr in body.get("prs") or []:
        if pr.get("state") != "open" or not pr.get("ready"):
            continue
        groups.setdefault(_pr_who(ctx, pr.get("project_slug")), []).append(pr)
    yours = [key for key in groups if key[2]]
    if len(yours) > 1:
        # An operator who also owns projects merges both: one row, not one per group.
        mine = any(key[3] for key in yours)
        who = "you" if mine and ctx.user_id is not None else "operators"
        combined = [pr for key in yours for pr in groups.pop(key)]
        groups[(None, who, True, mine)] = combined
    product = ((body.get("factory") or {}).get("product") or {}) or {}
    policy = _clip(((product.get("delivery") or {}) or {}).get("merge_policy"), 300)
    drafts: list[_Draft] = []
    for (owner, who, for_you, mine), prs in groups.items():
        prs.sort(key=lambda p: (int(p.get("ready_since") or 0), int(p.get("number") or 0)))
        n = len(prs)
        projects = {str(p.get("project_slug")) for p in prs if p.get("project_slug")}
        items = [
            AskItem(
                ref=f"PR #{pr['number']}",
                title=ctx.project_title(pr.get("project_slug"))
                or _clip(pr.get("title"), TITLE_MAX)
                or f"PR #{pr['number']}",
                project=pr.get("project_slug"),
                pr_id=str(pr["id"]),
                url=pr.get("url"),
                since=int(pr.get("ready_since") or 0) or None,
                href=ctx.links.pr(str(pr["id"])),
            )
            for pr in prs
        ]
        since = int(prs[0].get("ready_since") or 0)
        # One row per person who merges: the operators' is "merge", an owner's "merge:<name>".
        key = "merge" if owner is None else f"merge:{_person(owner)}"
        holds = len(projects) or n
        action = (
            AskAction(kind="open", label="Review", href=items[0].href)
            if n == 1
            else AskAction(
                kind="peek", label=f"Review all {n}", peek="merge", href=ctx.links.home("merge")
            )
        )
        meta = [] if who in ("you", "operators") else [f"for {who}"]
        ask = {
            "id": key,
            "kind": "merge",
            "subject": "prs:ready" if owner is None else f"prs:ready:{_person(owner)}",
            "ref": None,
            "title": f"Merge {n} ready pull requests" if n > 1 else "Merge a ready pull request",
            "context": _merge_context(prs, policy),
            "meta": meta,
            "since": since,
            "age_s": max(0, int(ctx.now) - since) if since else 0,
            "cost_of_waiting": cost_of_waiting(
                "merge",
                now=ctx.now,
                text=_plural(holds, "project waits", "projects wait") + " for a merge",
                holds=holds,
            ),
            "primary_action": action,
            "items": items[:ITEMS_MAX],
            "more_count": max(0, n - ITEMS_MAX),
            "assignee": who,
            "mine": mine,
            "covers": [f"pr:{pr['id']}" for pr in prs],
            "href": action.href or ctx.links.home("merge"),
        }
        drafts.append(
            _Draft(ask=ask, for_you=for_you, pr_ids={_norm_id(str(p["id"])) for p in prs})
        )
    return drafts


def _incident_draft(issue: FactoryIssue, ctx: _Ctx) -> _Draft:
    ref = issue.ref or f"INC-{issue.number}"
    critical = issue.severity == "critical"
    who = (
        "operators"
        if (issue.assignee or "") in ("", "operators")
        else "owners"
        if issue.assignee == "owners"
        else ("you" if issue.assignee == ctx.user_id else _person(issue.assignee or ""))
    )
    meta = [issue.project_slug] if issue.project_slug else []
    if critical:
        meta.append("critical")
    if who in ("owners", "operators"):
        meta.append(f"for {who}")
    since = int(issue.first_seen or 0)
    href = ctx.links.incident(issue.number)
    ask = {
        "id": ref,
        "kind": "broken",
        "subject": f"issue:{ref}",
        "ref": ref,
        "title": _clip(issue.summary, TITLE_MAX) or "Something went wrong",
        "context": _clip(_first_line(issue.body)),
        "meta": meta,
        "since": since,
        "age_s": max(0, int(ctx.now) - since),
        "cost_of_waiting": cost_of_waiting(
            "broken",
            now=ctx.now,
            text="a person must look at it" if not critical else "something is down",
            critical=critical,
        ),
        "primary_action": AskAction(kind="open", label="See what to do", href=href),
        "assignee": who,
        "mine": ctx.user_id is not None and issue.assignee == ctx.user_id,
        "project": issue.project_slug,
        "critical": critical,
        "covers": [f"incident:{issue.id}"],
        "href": href,
    }
    for_you = ctx.user_id is None or issues.is_mine(issue, ctx.user_id, ctx.inputs.level)
    return _Draft(ask=ask, for_you=for_you)


def _stuck_sentence(issue: FactoryIssue, ctx: _Ctx) -> str:
    kind, _, sid = (issue.target or "").partition(":")
    member = ctx.members.get(_norm_id(sid)) if kind == "member" else None
    name = _cap(member_name(member)) if member is not None else "An agent"
    reason = (member or {}).get("stuck_reason")
    if not reason:
        evidence = issue.evidence if isinstance(issue.evidence, Mapping) else {}
        tool = evidence.get("tool")
        reason = (
            f"the same failing {tool} call again and again"
            if issue.rule == "agent.repeat" and tool
            else "the same failing call again and again"
            if issue.rule == "agent.repeat"
            else "no new activity while running"
        )
    return f"{name} is stuck ({reason}); it clears once the agent moves again."


def _handled(ctx: _Ctx, healing: Sequence[FactoryIssue]) -> list[str]:
    """The calm "Handled by the factory" sentences, stuck agents first."""
    lines: list[str] = []
    stuck = [i for i in healing if i.rule in STUCK_RULES]
    for issue in sorted(stuck, key=lambda i: (i.first_seen, i.number)):
        lines.append(_stuck_sentence(issue, ctx))
    for issue in sorted(
        (i for i in healing if i.rule not in STUCK_RULES), key=lambda i: (i.first_seen, i.number)
    ):
        summary = _clip(issue.summary, TITLE_MAX) or "Something went wrong"
        lines.append(
            f"{issue.ref or f'INC-{issue.number}'}: {summary.rstrip('.')}. "
            "It clears by itself when the condition does."
        )
    rows = sorted(ctx.proposals.values(), key=lambda r: int(r.get("number") or 0))
    for row in rows:
        ref = f"C-{row.get('number')}"
        if trial_running(row, ctx.now):
            title = _clip(str(row.get("title") or ""), TITLE_MAX)
            lines.append(f"Trial {ref} is running" + (f": {title}." if title else "."))
        elif row.get("status") == "trial" and not _evaluated(row):
            lines.append(f"Trial {ref} has ended; the factory is evaluating it.")
        elif row.get("status") == "awaiting_cosign":
            lines.append(
                f"The bet {ref} waits for the other lead's co-sign before it reaches a person."
            )
    return lines


@dataclass(frozen=True)
class Assembled:
    """
    Every row (the viewer's and other people's) before the viewer's cut.

    :param drafts: All rows, unsorted.
    :param handled: The calm "Handled by the factory" sentences.
    :param in_steer: Refs of agents' definition proposals left to Steer.
    :param folded: Refs of incidents folded into another row.
    :param folded_keys: Their waiting keys (``incident:<id>``).
    """

    drafts: list[_Draft]
    handled: list[str]
    in_steer: list[str]
    folded: list[str]
    folded_keys: frozenset[str] = frozenset()

    def covered(self) -> set[str]:
        """
        The status summary's waiting keys some row leads with (anyone's): an
        incident folded into another row is left out, so it is not counted twice.
        """
        return {
            key
            for draft in self.drafts
            for key in draft.ask["covers"]
            if key not in self.folded_keys
        }

    def yours(self) -> int:
        """How many rows wait on the viewer (the one Needs you count)."""
        return sum(1 for draft in self.drafts if draft.for_you)


def assemble(inputs: AskInputs, *, now: float) -> Assembled:
    """Every row of the factory as *inputs*' viewer sees it (see the module doc)."""
    body = inputs.snapshot
    factory = body.get("factory") or {}
    ctx = _Ctx(
        inputs=inputs,
        now=now,
        links=_Links(str(factory.get("id") or "")),
        members={_norm_id(str(m.get("session_id"))): m for m in body.get("members") or []},
        projects={str(p.get("slug")): p for p in body.get("projects") or []},
        proposals={
            f"C-{row.get('number')}": row
            for row in inputs.proposals
            if row.get("status") in ("pending", "awaiting_cosign", "trial")
        },
    )
    in_steer: list[str] = []
    drafts: list[_Draft] = []
    for decision in body.get("decisions") or []:
        if decision.get("status") != "open":
            continue
        draft = _decision_draft(decision, ctx, in_steer)
        if draft is not None:
            drafts.append(draft)
    drafts += _merge_drafts(ctx)
    healing: list[FactoryIssue] = []
    folded: list[str] = []
    folded_keys: set[str] = set()
    for issue in sorted(inputs.incidents, key=lambda i: (i.first_seen, i.number)):
        if issue.class_ != issues.INCIDENT or issue.status not in ("open", "acked"):
            continue
        target = _norm_target(issue.target) or ""
        # Fold only where the viewer sees it too: an incident that waits on
        # them never hides inside a row that waits on someone else.
        yours = inputs.user_id is None or issues.is_mine(issue, inputs.user_id, inputs.level)
        fits = [d for d in drafts if d.for_you or not yours]
        home = next((d for d in fits if target and target in d.targets), None)
        if home is None and target.startswith("pr:"):
            pr_id = target.partition(":")[2].partition("@")[0]
            home = next((d for d in fits if pr_id in d.pr_ids), None)
        ref = issue.ref or f"INC-{issue.number}"
        if home is not None:
            home.ask["folded"] = [*home.ask.get("folded", []), ref]
            home.ask["covers"] = [*home.ask["covers"], f"incident:{issue.id}"]
            folded.append(ref)
            folded_keys.add(f"incident:{issue.id}")
        elif needs_person(issue):
            drafts.append(_incident_draft(issue, ctx))
        elif heals_itself(issue):
            healing.append(issue)
    return Assembled(
        drafts=drafts,
        handled=_handled(ctx, healing),
        in_steer=in_steer,
        folded=folded,
        folded_keys=frozenset(folded_keys),
    )


def _order(ask: Mapping[str, Any]) -> tuple[int, int, str]:
    """Costliest first, then the longest wait, then by id."""
    return (-int(ask["cost_of_waiting"].score), int(ask["since"]), str(ask["id"]))


def build_asks(inputs: AskInputs, *, now: float) -> dict[str, Any]:
    """
    The viewer's ``AsksResponse`` JSON: their rows sorted by cost of waiting. Pure.

    :param inputs: See :class:`AskInputs` (:func:`asks_from_raw` gathers them).
    :param now: Epoch seconds (deadlines, ``age_s`` and ``generated_at``).
    """
    built = assemble(inputs, now=now)
    mine = sorted((d.ask for d in built.drafts if d.for_you), key=_order)
    factory = inputs.snapshot.get("factory") or {}
    response = AsksResponse(
        factory_id=str(factory.get("id") or ""),
        generated_at=int(now),
        asks=[Ask.model_validate(ask) for ask in mine],
        handled=built.handled,
        counts=AskCounts(
            total=len(mine),
            mine=sum(1 for ask in mine if ask["mine"]),
            others=len(built.drafts) - len(mine),
            in_steer=len(built.in_steer),
            folded=len(built.folded),
        ),
    )
    return response.model_dump(mode="json")


# ── Loading ────────────────────────────────────────────────────


def asks_from_raw(
    deps: FactoryDeps,
    root_id: str,
    raw: RawSnapshot,
    readable: set[str] | None,
    user_id: str | None,
    now: float,
) -> dict[str, Any]:
    """
    The viewer's asks from a snapshot in hand (synchronous: store reads).

    It loads with the status summary's own loader (the digest builds both
    from one :func:`.status_summary.inputs_from_raw`), so Home, the top bar
    and the digest agree.

    :param readable: The members the viewer reads; ``None`` reads them all.
    """
    from omnigent.server.factory.snapshot import filter_snapshot

    members = set(raw.top_level_of) if readable is None else readable
    body = filter_snapshot(raw, members)
    # The asks read no spend ledger and no pause events: skip those reads.
    loaded = summary_mod.load_inputs(deps, root_id, body, raw, members, user_id, now, ledger=False)
    return build_asks(AskInputs.from_status(loaded), now=now)


async def asks_for_viewer(
    deps: FactoryDeps,
    root_id: str,
    raw: RawSnapshot,
    readable: set[str],
    user_id: str | None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """The asks from a snapshot already in hand (the route's path)."""
    at = time.time() if now is None else now
    return await asyncio.to_thread(asks_from_raw, deps, root_id, raw, readable, user_id, at)


__all__ = [
    "DEADLINE_SOON_S",
    "ITEMS_MAX",
    "SELF_HEALING_RULES",
    "Ask",
    "AskAction",
    "AskCost",
    "AskCounts",
    "AskInputs",
    "AskItem",
    "AskKind",
    "AskOption",
    "AsksResponse",
    "Assembled",
    "asks_for_viewer",
    "asks_from_raw",
    "assemble",
    "build_asks",
    "cost_of_waiting",
    "definition_kind",
    "heals_itself",
    "keep_or_revert",
    "member_name",
    "needs_person",
    "proposal_paths",
    "trial_running",
]
