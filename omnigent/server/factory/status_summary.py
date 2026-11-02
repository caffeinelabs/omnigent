"""The factory's "Now": what it is doing and what waits on people, in plain words.

The cockpit's Overview opens on it, so a person sees in ten seconds what the
factory is working on, what waits on them, what it spends and what comes next.

:func:`build_status_summary` is pure and deterministic (no LLM, no I/O): it
reads one viewer's filtered snapshot (the projects, PRs, decisions, members
and totals the cockpit already shows), the open incidents, the factory's
newest pause and today's and yesterday's cost-ledger cells, and returns the
``StatusSummary`` JSON. :func:`load_inputs` gathers those inputs with the
existing helpers (the cached snapshot plus at most three small store reads),
:func:`summary_for_viewer` builds from a snapshot already in hand (the route)
and :func:`status_summary_for` does everything for a factory root and a user.

The Slack daily digest (:mod:`.slackdm.digest`) is built from the same
builder for its recipient (:func:`summary_from_raw`), so the card and the
digest never disagree. Every ``href`` is a cockpit path (``/factory/<id>/…``):
prefix it with the server's public URL. Times are epoch seconds and texts
carry no durations, so the reader ages them against its own clock and a
cached summary never says something stale.

Sections:

- **Waiting on people** (``waiting``): open decisions (proposals included,
  since every proposal a human approves is mirrored as a decision), PRs
  ready for a human merge, and open incidents of warning or worse. Each
  item names who it waits on and whether that is the viewer; the viewer's
  come first, urgent ones (critical incidents, blocking decisions) next,
  then the oldest wait. An item that is all its project still needs
  (``holds``: every open PR ready for a merge, or blocked on that decision)
  stands for the project, which is then not under Working on.
- **Working on** (``working``): live projects past the queue that agents
  still work on, with stage, primary PR, time in stage and a plain status
  built from the stage, the task in hand and the PR's gates, preview and
  demo (the lead's own label only when none of that says anything). A
  project whose PRs merged still counts while its lead is running or was
  active in the last 30 minutes (e.g. it deploys a preview, then checks on
  it at its next beat).
- **Next** (``next``): queued projects with why they wait (or why they can't
  start, or which start-up step they are on), what Atlas (the root lead)
  recently said, with the ids in it linked, and when it checks in again.
- **Spend** (``spend``): the last 24 h (the ledger is per UTC day, so the
  part of yesterday inside the window is prorated), the current $/h (the
  header's), and whether the factory is idle (nothing in progress or queued:
  at most waiting on people) and since when.
- **Health** (``health``): a paused or finished factory with who paused it
  and why, projects that are blocked, failed or paused, and GitHub trouble.
- **Verdict** (``verdict``): the top bar's dot and one sentence by fixed
  rules: red when something is broken, amber when things only wait on the
  viewer, grey when paused, finished or idle, green otherwise; the digest
  opens with the same sentence.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from omnigent.factory import PROJECT_KIND_INTEGRATION, ROLE_PROJECT_LEAD, STAGES
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER
from omnigent.server.factory import issues
from omnigent.server.factory.decision_routing import is_mine_by, roster_owner

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryCostDaily, FactoryEvent, FactoryIssue
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.snapshot import RawSnapshot

DAY_S: Final = 86_400
TEXT_MAX: Final = 200
"""A question, title or one-liner is clipped to this many characters."""
NOTE_MAX: Final = 140
"""Atlas's one-liner is trimmed (at a word) to this many characters."""
LABEL_MAX: Final = 100
"""A lead's own label, shown only when nothing structured says what it does."""
TASK_TITLE_MAX: Final = 60
CONTROL_LOOKBACK_S: Final = 30 * DAY_S
"""How far back the factory's last pause is looked up (only while it is paused)."""
WAIT_SEVERITIES: Final = frozenset({"warning", "critical"})
"""Incidents at or above warning wait on people."""
IDLE_SPEND_MIN_USD_PER_H: Final = 0.01
"""Below this rate an idle factory is not called out as spending."""
MERGED_ACTIVE_S: Final = 30 * 60
"""A merged project whose lead was active this recently is still being validated."""
LEAD_NOTE_MAX_AGE_S: Final = 6 * 3600
"""Atlas's one-liner is left out once Atlas has been quiet this long (it is stale)."""

_PROPOSES_RE: Final = re.compile(
    r"^(?P<who>.+?) proposes (?P<ref>C-\d+)(?: \([^)]*\))?: (?P<title>.+)$"
)
_KEEP_OR_REVERT_RE: Final = re.compile(r"^Keep or revert (?P<ref>C-\d+)\b")
_BLOCKED_ON_RE: Final = re.compile(r"^Blocked on (?P<ref>D-\d+)")
_WAITING_ON_RE: Final = re.compile(r"^waiting on (?P<deps>.+)$")
_QUOTED_TITLE_RE: Final = re.compile(r'^"(?P<title>[^"]+)"')
_LOOP_SUBJECT_RE: Final = re.compile(r"\b(?P<ref>T-\d+|PR #\d+)")
_STALLED_RE: Final = re.compile(r" stalled - what next\?")
_PR_NUMBER_RE: Final = re.compile(r"#(?P<number>\d+)")
_PL_PREFIX: Final = "PL · "
_STAGE_RANK: Final = {stage: i for i, stage in enumerate(STAGES)}
_LIVE_STATUSES: Final = (None, "active")
_STUCK_OVERLAYS: Final = frozenset({"failed", "blocked", "paused"})
_PROVISIONING_TEXT: Final = {
    "provisioning": "getting a machine ready",
    "cloning": "copying the repository",
    "starting": "connecting its machine",
    "connecting": "starting its lead agent",
    "briefing": "briefing its lead agent",
}
_STEP_TEXT: Final = {
    "node": "waiting for a free machine",
    "image": "pulling its image",
    "clone": "copying the repository",
    "containers": "starting its containers",
    "host": "connecting its machine",
}
"""What a starting machine waits on (``ProvisioningView.step``), in plain words."""
_START_TEXT: Final = {
    **_PROVISIONING_TEXT,
    **_STEP_TEXT,
    "connect": "waiting for its lead agent to connect",
    "creating": "creating its lead agent",
}
"""A starting project's step (``ProjectStartView.phase``) in plain words."""
_TASK_VERB: Final = {"running": "working on", "review": "reviewing", "blocked": "stuck on"}
_STAGE_WORDS: Final = {
    "planning": "planning the work",
    "building": "building",
    "review": "in review",
    "verify": "verifying",
    "ready": "ready for a merge",
    "merged": "merged, checking the result",
}
_KIND_NOUN: Final = {"pr": "PR", "decision": "decision", "incident": "incident"}
_REF_TOKEN_RE: Final = re.compile(
    r"(?<![\w-])(?:PR #(?P<pr>\d+)|[#!](?P<num>\d+)|(?P<decision>D-\d+)|INC-(?P<incident>\d+))"
    r"(?![\w-])"
)
"""Ids an agent's note may name: ``PR #9826``, ``#9826``, ``!9630``, ``D-19``, ``INC-12``."""


# ── The response ───────────────────────────────────────────────


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SlackReceipt(_Model):
    """
    One Slack DM the viewer got about a waiting item (the ``notify.slack`` ledger).

    :param stage: ``new`` / ``ready`` / ``opened`` (the first DM), ``r4h`` /
        ``r24h`` (reminders), ``owners`` (escalated) or ``deadline`` (last call).
    """

    at: int
    stage: str


class WaitingItem(_Model):
    """
    One thing that waits on a person.

    :param kind: ``decision`` / ``pr`` / ``incident``.
    :param ref: ``D-19``, ``PR #9774`` or ``INC-3``.
    :param text: What to do, e.g. ``approve Atlas's proposal C-4``.
    :param detail: The question, PR title or incident summary.
    :param who: Whom it waits on: ``you``, ``operators``, ``owners`` or a name.
    :param for_you: The viewer may act on it (theirs or their role's).
    :param urgent: A blocking decision or a critical incident.
    :param since: When it started waiting (epoch seconds).
    :param holds: It is all its project still needs, so the project is not
        also listed under Working on.
    :param deadline_at: When an open decision defaults by itself, and
        ``deadline_effect`` what happens then.
    :param slack: The viewer's Slack DMs about it, oldest first.
    :param href: Its cockpit page.
    """

    kind: Literal["decision", "pr", "incident"]
    key: str
    ref: str
    text: str
    detail: str | None = None
    who: str
    for_you: bool
    urgent: bool = False
    since: int
    project: str | None = None
    severity: str | None = None
    holds: bool = False
    deadline_at: int | None = None
    deadline_effect: str | None = None
    slack: list[SlackReceipt] = Field(default_factory=list)
    href: str


class WorkingPr(_Model):
    """The PR a working project's line shows."""

    id: str
    number: int
    state: str
    ready: bool = False
    title: str
    href: str


class WorkingItem(_Model):
    """
    One project in progress.

    :param since: When it entered its stage (epoch seconds).
    :param status: One plain line on where it is, e.g. ``PR #9653: in agent
        review · working on T-7: Badge colours`` (see :func:`work_status`).
    :param status_source: ``facts`` (the stage, task and PR say it) or
        ``lead`` (only the lead's own label, trimmed, says anything).
    :param doing: Its lead agent's own one-liner, verbatim (not for display).
    :param problem: Plain words for a blocked, failed or paused project.
    :param running: Its lead or one of its agents is running a turn now.
    """

    project: str
    title: str
    stage: str
    since: int
    pr: WorkingPr | None = None
    status: str
    status_source: Literal["facts", "lead"] = "facts"
    doing: str | None = None
    problem: str | None = None
    running: bool = False
    href: str


class NextItem(_Model):
    """
    A project that has not started yet, and why.

    :param reason: Why it waits, e.g. ``waits for api-version to finish``.
    :param problem: Set when it can't start (its lead's machine failed, it is
        blocked or paused): the same words Health uses.
    :param step_since: When the start step in *reason* began (e.g. pulling its
        image), so the card can say for how long; ``None`` when unknown.
    """

    project: str
    title: str
    reason: str
    problem: str | None = None
    since: int
    step_since: int | None = None
    href: str


class TextPart(_Model):
    """A run of text; ``href`` when it names something the cockpit has a page for."""

    text: str
    href: str | None = None


class LeadNote(_Model):
    """
    What the root lead (Atlas) last said, quoted.

    :param text: Its one-liner, trimmed at a word to :data:`NOTE_MAX`.
    :param parts: ``text`` split so the PRs, decisions and incidents it names link.
    :param since: When Atlas was last active (the one-liner is at most this fresh).
    """

    who: str
    text: str
    parts: list[TextPart] = Field(default_factory=list)
    since: int | None = None
    session_id: str
    href: str


class CheckIn(_Model):
    """When the root lead (Atlas) next wakes up on its own (its next heartbeat)."""

    who: str
    at: int
    session_id: str
    href: str


class SpendBlock(_Model):
    """
    Spend for the viewer's part of the factory.

    :param last_24h_usd: Spend over the last 24 h (yesterday's UTC day prorated);
        ``None`` when nothing was priced.
    :param usd_per_h: The current rate, ``None`` when unknown (or partly hidden).
    :param idle: No project is in progress or queued.
    :param idle_since: Since when (the last project's end, else the start).
    :param idle_spend: Idle, yet still spending at least a cent an hour.
    """

    last_24h_usd: float | None = None
    today_usd: float | None = None
    usd_per_h: float | None = None
    total_usd: float | None = None
    idle: bool = False
    idle_since: int | None = None
    idle_spend: bool = False
    href: str


class HealthItem(_Model):
    """
    One reason the factory is not running freely.

    :param by: Who paused the factory.
    :param note: The note they left with the pause.
    """

    kind: Literal["paused", "done", "blocked", "failed", "project_paused", "github"]
    text: str
    by: str | None = None
    note: str | None = None
    project: str | None = None
    since: int | None = None
    href: str


class HealthBlock(_Model):
    """``ok``, ``paused``, ``done`` or ``attention`` (something is blocked or failed)."""

    state: Literal["ok", "paused", "done", "attention"]
    items: list[HealthItem]


class VerdictReason(_Model):
    """
    One fact behind the verdict's dot, worst first.

    :param kind: ``project`` (a live project stopped), ``agent`` (a lead
        stopped), ``incident`` (a critical one), ``github``, ``supervisor``,
        or ``waiting`` (what waits on the viewer, one per kind).
    """

    kind: Literal["project", "agent", "incident", "github", "supervisor", "waiting"]
    text: str
    href: str


class Verdict(_Model):
    """
    The top bar's one answer, built from facts by fixed rules (never by an agent).

    Red when something is broken (a live project or a lead stopped, a critical
    incident, GitHub unreachable, the supervisor failing); amber when nothing
    is broken and things wait on the viewer; grey when the factory is paused,
    finished or idle with nothing broken or waiting on the viewer; green
    otherwise. It never says green while something is broken. The Slack
    digest opens with the same sentence.

    :param sentence: e.g. ``"Running, 1 project stopped"`` or ``"Running smoothly"``.
    :param usd_per_h: The current rate (the header's), when known.
    :param usd_per_h_alert: The hourly spend alert of the factory, when known.
    """

    dot: Literal["red", "amber", "green", "grey"]
    sentence: str
    reasons: list[VerdictReason]
    usd_per_h: float | None = None
    usd_per_h_alert: float | None = None


class SummaryCounts(_Model):
    """
    Tallies for badges and the headline.

    :param cant_start: Queued projects that can't start (counted in ``queued`` too).
    :param held: Projects that only wait on people (not counted in ``working``).
    """

    waiting_for_you: int
    waiting_total: int
    working: int
    queued: int
    cant_start: int = 0
    held: int = 0


class StatusSummary(_Model):
    """
    The factory's "Now" for one viewer.

    :param headline: One plain line without times, e.g. ``"4 PRs wait on you
        · 1 project in progress"`` or ``"Idle · 2 things wait on you"``.
    :param verdict: The fact-built dot and sentence of the top bar (see :class:`Verdict`).
    :param generated_at: Server time of the build (epoch seconds).
    """

    factory_id: str
    factory_name: str
    generated_at: int
    headline: str
    verdict: Verdict
    state: Literal["working", "idle", "paused", "done"]
    waiting: list[WaitingItem]
    working: list[WorkingItem]
    next: list[NextItem]
    lead: LeadNote | None = None
    check_in: CheckIn | None = None
    spend: SpendBlock
    health: HealthBlock
    counts: SummaryCounts


# ── Inputs ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class StatusInputs:
    """
    Everything :func:`build_status_summary` reads.

    :param snapshot: The viewer's filtered ``FactorySnapshot`` JSON.
    :param user_id: The viewer (``None`` when auth is off: every lever is theirs).
    :param level: The viewer's session level on the factory root.
    :param incidents: Open or acked incidents the viewer may see.
    :param pause: The newest factory-wide ``control.pause`` / ``control.resume``.
    :param cost_cells: Today's and yesterday's ledger cells the viewer may see.
    :param notified: Subject -> Slack DM receipts (the snapshot's ``notify.slack`` read).
    :param owners: Project slug -> its ``human_owner`` on the roster: their
        ready PRs wait on them, not on every operator.
    :param usd_per_h_alert: The factory's hourly spend alert, for the verdict's rate.
    """

    snapshot: Mapping[str, Any]
    user_id: str | None
    level: int
    incidents: Sequence[FactoryIssue] = ()
    pause: FactoryEvent | None = None
    cost_cells: Sequence[FactoryCostDaily] = field(default_factory=tuple)
    notified: Mapping[str, Sequence[Mapping[str, Any]]] = field(default_factory=dict)
    owners: Mapping[str, str] = field(default_factory=dict)
    usd_per_h_alert: float | None = None


# ── Small helpers ──────────────────────────────────────────────


def _clip(text: str | None, limit: int = TEXT_MAX) -> str | None:
    flat = " ".join((text or "").split())
    if not flat:
        return None
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def clip_words(text: str | None, limit: int) -> str | None:
    """*text* on one line, cut at a word to at most *limit* characters (``…`` included)."""
    flat = " ".join((text or "").split())
    if not flat:
        return None
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ·,;:—-") + "…"


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _receipts(rows: Iterable[Mapping[str, Any]], user_id: str | None) -> list[SlackReceipt]:
    """The viewer's sent Slack DMs among *rows* (``NotifiedView`` JSON), oldest first."""
    if user_id is None:
        return []
    return [
        SlackReceipt(at=int(row["at"]), stage=str(row.get("stage") or ""))
        for row in rows
        if row.get("user_id") == user_id and row.get("ok", True) and row.get("at")
    ]


def _person(user_id: str) -> str:
    """A person's short name: the local part of an email-like id."""
    return user_id.split("@")[0] or user_id


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def utc_day(epoch_s: float) -> str:
    """:returns: ``YYYY-MM-DD`` of *epoch_s* in UTC (the cost ledger's day)."""
    return datetime.fromtimestamp(epoch_s, tz=UTC).strftime("%Y-%m-%d")


class _Links:
    """Cockpit paths of one factory (mirrors ``web/…/factoryRoutes.ts``)."""

    def __init__(self, factory_id: str) -> None:
        self.base = f"/factory/{quote(factory_id, safe='')}"

    def decision(self, ref: str) -> str:
        return f"{self.base}/decisions?decision={quote(ref, safe='')}"

    def pr(self, pr_id: str) -> str:
        return f"{self.base}/prs/{quote(pr_id, safe='')}"

    def incident(self, number: int) -> str:
        return f"{self.base}/alerts/{number}"

    def project(self, slug: str) -> str:
        return f"{self.base}/projects/{quote(slug, safe='')}"

    def agent(self, session_id: str) -> str:
        return f"{self.base}/agents/{quote(session_id, safe='')}"

    def insights(self) -> str:
        return f"{self.base}/insights"


def _is_live(project: Mapping[str, Any]) -> bool:
    """Open product work: not closed, not the integration env."""
    return (
        project.get("status") in _LIVE_STATUSES and project.get("kind") != PROJECT_KIND_INTEGRATION
    )


def _agent_name(title: str | None, role: str | None, project: str | None) -> str | None:
    """
    How a sentence names an agent: ``Atlas``, ``the db-version lead``, ``Sentinel``.

    A lead's ``<slot> · <name>`` title (``Lead B · Sentinel``) reads as its name.
    """
    text = (title or "").strip()
    if role == ROLE_PROJECT_LEAD or text.startswith(_PL_PREFIX):
        slug = text[len(_PL_PREFIX) :].strip() if text.startswith(_PL_PREFIX) else project
        return f"the {slug} lead" if slug else "a project lead"
    return text.rpartition(" · ")[2].strip() or None


# ── Waiting on people ──────────────────────────────────────────


def decision_who(assignee: str | None, claimed_by: str | None, user_id: str | None) -> str:
    """Whom an open decision waits on, from the viewer's side."""
    if claimed_by:
        return "you" if user_id is not None and claimed_by == user_id else _person(claimed_by)
    target = assignee or "operators"
    if target in ("operators", "owners"):
        return target
    return "you" if user_id is not None and target == user_id else _person(target)


def _subject_agent(decision: Mapping[str, Any]) -> str:
    """The agent a server decision is about (its question opens with its quoted title)."""
    quoted = _QUOTED_TITLE_RE.match(str(decision.get("question") or ""))
    title = quoted["title"] if quoted else decision.get("asked_by_title")
    project = decision.get("project_slug")
    return _agent_name(title, None, project) or (f"the {project} lead" if project else "an agent")


def _server_decision_text(decision: Mapping[str, Any]) -> str | None:
    """What to do about a decision the server opened, by its source."""
    source = decision.get("source")
    question = str(decision.get("question") or "")
    project = decision.get("project_slug")
    if source == "relaunch_failed":
        return f"decide what to do: {_subject_agent(decision)} couldn't restart"
    if source == "blocked_approval":
        return f"check the approval {_subject_agent(decision)} is waiting on"
    if source == "storm":
        return f"decide whether to pause {_subject_agent(decision)} (it sent too many messages)"
    if source == "access_request":
        return "approve or refuse an access request"
    if source == "done_check":
        return f"confirm {project} is done" if project else "confirm a project is done"
    if source == "pr_closed":
        closed = _PR_NUMBER_RE.search(question)
        pr = f"PR #{closed['number']}" if closed else "a PR"
        return f"decide what to do now that {pr} was closed without merging"
    if source == "loop":
        if question.rstrip().endswith("Pause or raise?"):
            return "decide whether to pause or raise a budget"
        subject = _LOOP_SUBJECT_RE.search(question)
        what = subject["ref"] if subject else project
        if what and _STALLED_RE.search(question):
            return f"decide on {what} (it stalled)"
        if what:
            return f"decide on {what} (budget or retries ran out)"
        return "decide what to do: a budget or retry limit ran out"
    return None


def _decision_text(decision: Mapping[str, Any]) -> tuple[str, str | None]:
    """``(what to do, detail)`` for an open decision."""
    question = str(decision.get("question") or "")
    asker = _agent_name(
        decision.get("asked_by_title"), decision.get("asked_by_role"), decision.get("project_slug")
    )
    if decision.get("source") == "proposal":
        keep = _KEEP_OR_REVERT_RE.match(question)
        if keep:
            return f"keep or revert {keep['ref']}", _clip(question)
        proposes = _PROPOSES_RE.match(question)
        if proposes:
            who = _agent_name(proposes["who"], None, None) or proposes["who"]
            return f"approve {who}'s proposal", _clip(f"{proposes['ref']}: {proposes['title']}")
        who = (
            f"{asker}'s proposal"
            if decision.get("asked_by_session_id") and asker
            else "a proposal"
        )
        return f"approve {who}", _clip(question)
    server = _server_decision_text(decision)
    if server is not None:
        return server, _clip(question)
    if decision.get("asked_by_session_id") and asker:
        return f"answer {asker}", _clip(question)
    project = decision.get("project_slug")
    return (f"answer a question about {project}" if project else "answer a question"), _clip(
        question
    )


def _incident_who(assignee: str | None, user_id: str | None) -> str:
    if assignee in (None, "", "operators"):
        return "operators"
    if assignee == "owners":
        return "owners"
    return "you" if user_id is not None and assignee == user_id else _person(assignee)


def _pr_who(owner: str | None, user_id: str | None, operator: bool) -> tuple[str, bool]:
    """``(who, for_you)`` of a ready PR: its project's human owner, else operators."""
    if owner:
        mine = user_id is None or owner == user_id
        return ("you" if user_id is not None and mine else _person(owner)), mine
    return "operators", operator


def waiting_items(inputs: StatusInputs, links: _Links) -> list[WaitingItem]:
    """Everything open that waits on a person, the viewer's first (see :func:`waiting_order`)."""
    body, user_id, level = inputs.snapshot, inputs.user_id, inputs.level
    operator = user_id is None or level >= LEVEL_EDIT
    items: list[WaitingItem] = []
    for decision in body.get("decisions") or []:
        if decision.get("status") != "open":
            continue
        assignee, claimed = decision.get("assignee"), decision.get("claimed_by")
        text, detail = _decision_text(decision)
        deadline = decision.get("deadline_at")
        items.append(
            WaitingItem(
                kind="decision",
                key=f"decision:{decision['id']}",
                ref=decision["ref"],
                text=text,
                detail=detail,
                who=decision_who(assignee, claimed, user_id),
                for_you=is_mine_by(assignee, claimed, user_id, level),
                urgent=bool(decision.get("blocking")),
                since=int(decision.get("created_at") or 0),
                project=decision.get("project_slug"),
                deadline_at=int(deadline) if isinstance(deadline, int | float) else None,
                deadline_effect=decision.get("deadline_effect") if deadline else None,
                slack=_receipts(decision.get("notified") or (), user_id),
                href=links.decision(decision["ref"]),
            )
        )
    for pr in body.get("prs") or []:
        if pr.get("state") != "open" or not pr.get("ready"):
            continue
        project = pr.get("project_slug")
        who, mine = _pr_who(inputs.owners.get(project or ""), user_id, operator)
        subject = f"pr:{pr.get('repo')}#{pr['number']}"
        items.append(
            WaitingItem(
                kind="pr",
                key=f"pr:{pr['id']}",
                ref=f"PR #{pr['number']}",
                text="review and merge",
                detail=_clip(pr.get("title")),
                who=who,
                for_you=mine,
                since=int(pr.get("ready_since") or 0),
                project=project,
                slack=_receipts(inputs.notified.get(subject, ()), user_id),
                href=links.pr(pr["id"]),
            )
        )
    for issue in inputs.incidents:
        if issue.status not in ("open", "acked") or issue.severity not in WAIT_SEVERITIES:
            continue
        ref = issue.ref or f"INC-{issue.number}"
        # The DM ledger names an incident as the DM rules do.
        subject = f"issue:{issue.ref or f'INC {issue.id[:8]}'}"
        items.append(
            WaitingItem(
                kind="incident",
                key=f"incident:{issue.id}",
                ref=ref,
                text=_clip(issue.summary) or "something went wrong",
                detail=None,
                who=_incident_who(issue.assignee, user_id),
                for_you=user_id is None or issues.is_mine(issue, user_id, level),
                urgent=issue.severity == "critical",
                since=int(issue.first_seen or 0),
                project=issue.project_slug,
                severity=issue.severity,
                slack=_receipts(inputs.notified.get(subject, ()), user_id),
                href=links.incident(issue.number),
            )
        )
    return waiting_order(items)


def held_projects(
    body: Mapping[str, Any], waiting: Sequence[WaitingItem], now: float
) -> dict[str, set[str]]:
    """
    Projects whose only remaining step is a person, and the waiting keys that hold each.

    A project in progress is held when every open PR of it is ready for a
    human merge and no task of it runs or is ready to start, or when it is
    blocked on an open decision that is listed as waiting. A failed or
    paused project is a problem, not a wait, and stays under Working on.
    """
    waiting_keys = {item.key for item in waiting}
    decision_keys = {item.ref: item.key for item in waiting if item.kind == "decision"}
    prs = {p["id"]: p for p in body.get("prs") or []}
    held: dict[str, set[str]] = {}
    for project in body.get("projects") or []:
        if not is_working(project, now):
            continue
        overlay = project.get("overlay")
        if overlay == "blocked":
            head = str(project.get("overlay_reason") or "").partition(" · ")[0]
            blocked = _BLOCKED_ON_RE.match(head)
            if blocked and blocked["ref"] in decision_keys:
                held[project["slug"]] = {decision_keys[blocked["ref"]]}
            continue
        if overlay is not None:
            continue
        own = [prs[i] for i in project.get("pr_ids") or [] if i in prs]
        open_keys = [f"pr:{p['id']}" for p in own if p.get("state") == "open"]
        if not open_keys or any(key not in waiting_keys for key in open_keys):
            continue
        tasks = project.get("tasks_summary") or {}
        if tasks.get("running") or tasks.get("ready"):
            continue
        held[project["slug"]] = set(open_keys)
    return held


def _ref_number(ref: str) -> int:
    digits = re.search(r"(\d+)$", ref)
    return int(digits.group(1)) if digits else 0


def waiting_order(items: Iterable[WaitingItem]) -> list[WaitingItem]:
    """The viewer's first, then urgent ones, then the longest wait; then by kind and number."""
    return sorted(
        items,
        key=lambda i: (not i.for_you, not i.urgent, i.since, i.kind, _ref_number(i.ref), i.key),
    )


# ── Working on and next ────────────────────────────────────────


def _project_running(project: Mapping[str, Any]) -> bool:
    pl = project.get("pl") or {}
    return bool(pl.get("running")) or any(m.get("running") for m in project.get("team") or [])


def _lead_activity(project: Mapping[str, Any]) -> int | None:
    """When the project's lead was last active (epoch seconds), if known."""
    last = (project.get("pl") or {}).get("last_activity_at")
    return int(last) if isinstance(last, int | float) and last > 0 else None


def _lead_active(project: Mapping[str, Any], now: float) -> bool:
    """Its lead (or a teammate) runs a turn now, or its live lead was active lately."""
    if _project_running(project):
        return True
    pl = project.get("pl") or {}
    if pl.get("status") == "failed" or pl.get("health") == "dead":
        return False
    last = _lead_activity(project)
    return last is not None and last >= now - MERGED_ACTIVE_S


def _problem(project: Mapping[str, Any]) -> str | None:
    """Plain words for a project's blocked, failed or paused overlay."""
    overlay = project.get("overlay")
    reason = str(project.get("overlay_reason") or "")
    head, _, tail = reason.partition(" · ")
    extra = f" · {tail}" if tail else ""
    if overlay == "paused":
        by = project.get("paused_by")
        return f"paused by {by}" if by else "paused"
    if overlay == "blocked":
        blocked = _BLOCKED_ON_RE.match(head)
        if blocked:
            return f"blocked until someone answers {blocked['ref']}{extra}"
        return f"its lead agent says it is blocked{extra}"
    if overlay == "failed":
        if "sandbox" in head:
            # "PL sandbox failed twice", "PL sandbox did not wake": keep what and how often.
            wake = "did not wake" in head
            times = head.partition("did not wake" if wake else "failed")[2].strip()
            what = "did not wake" if wake else "failed to start"
            return f"its lead agent's machine {what}{f' {times}' if times else ''}{extra}"
        return f"its lead agent stopped{extra}"
    if overlay == "pr_closed":
        return "its PR was closed without merging"
    return None


def _primary_pr(project: Mapping[str, Any], prs: Mapping[str, Mapping[str, Any]]) -> Any:
    """Ready first, then open, then the newest merged (as the web's ``primaryPr``)."""
    own = [prs[i] for i in project.get("pr_ids") or [] if i in prs]
    for wanted in (
        lambda p: p.get("state") == "open" and p.get("ready"),
        lambda p: p.get("state") == "open",
    ):
        found = next((p for p in own if wanted(p)), None)
        if found is not None:
            return found
    return next((p for p in reversed(own) if p.get("state") == "merged"), None)


def _work_pr(project: Mapping[str, Any], prs: Mapping[str, Mapping[str, Any]]) -> Any:
    """The PR agents work on: the first open one not ready yet, else the primary one."""
    own = [prs[i] for i in project.get("pr_ids") or [] if i in prs]
    working = next((p for p in own if p.get("state") == "open" and not p.get("ready")), None)
    return working if working is not None else _primary_pr(project, prs)


def is_working(project: Mapping[str, Any], now: float) -> bool:
    """
    A live project past the queue that is not finished.

    Merged counts while its lead is running a turn or was active in the last
    :data:`MERGED_ACTIVE_S`: the PRs landed but the lead is, say, deploying a
    preview and checks on it at its next beat, so it works in bursts.
    """
    if not _is_live(project):
        return False
    stage = project.get("stage")
    if stage in ("queued", "done"):
        return False
    return stage != "merged" or _lead_active(project, now)


def _changes_requested(pr: Mapping[str, Any]) -> bool:
    """A person asked for changes on the PR's head (:func:`.stage.changes_requested_on_head`)."""
    review = pr.get("human_review")
    if not isinstance(review, Mapping) or review.get("verdict") != "changes_requested":
        return False
    sha, head = review.get("head_sha"), pr.get("head_sha")
    if not isinstance(sha, str) or not sha or not head:
        return True
    return sha.lower() == str(head).lower()


def pr_status(pr: Mapping[str, Any]) -> str | None:
    """Where a PR is, in plain words, from its state, gates, preview and demo."""
    ref = f"PR #{pr['number']}"
    state = pr.get("state")
    if state == "merged":
        return f"{ref} merged"
    if state != "open":
        return None
    gates = pr.get("gates") or {}
    preview = (pr.get("preview") or {}).get("state")
    demo = pr.get("demo") or {}
    if _changes_requested(pr):
        return f"{ref}: making the changes a person asked for"
    if pr.get("ready"):
        return f"{ref} ready for a merge"
    if pr.get("is_draft") or gates.get("draft") == "fail":
        return f"{ref} is a draft"
    steps = (
        (gates.get("checks") == "fail", "fixing failing checks"),
        (gates.get("checks") == "pending", "checks running"),
        (gates.get("review") == "fail", "fixing what review found"),
        (gates.get("review") == "pending", "in agent review"),
        (preview == "deploying", "deploying a preview"),
        (preview == "failed", "its preview failed to deploy"),
        (gates.get("verify") == "fail", "verification failed"),
        (
            gates.get("demo") == "fail"
            or (demo.get("status") in ("FAIL", "ERROR") and demo.get("is_current_head")),
            "its demo failed",
        ),
        (gates.get("verify") == "pending", "verifying"),
        (gates.get("demo") == "pending", "recording a demo"),
        (gates.get("mergeable") == "fail", "has merge conflicts"),
    )
    words = next((text for hit, text in steps if hit), None)
    return f"{ref}: {words}" if words else f"{ref} open"


def work_status(
    project: Mapping[str, Any], pr: Mapping[str, Any] | None
) -> tuple[str, Literal["facts", "lead"]]:
    """
    One plain line on where a project in progress is, and what said so.

    From its primary PR (gates, preview, demo), the task in hand, else its
    tasks done, else its stage note; the lead's own label (trimmed) only when
    none of these exists, else the stage in words.
    """
    parts: list[str] = []
    said = pr_status(pr) if pr is not None else None
    if said:
        parts.append(said)
    task = project.get("active_task") or {}
    tasks = project.get("tasks_summary") or {}
    if task.get("state") in _TASK_VERB:
        title = clip_words(task.get("title"), TASK_TITLE_MAX)
        parts.append(
            f"{_TASK_VERB[task['state']]} {task['ref']}" + (f": {title}" if title else "")
        )
    elif tasks.get("total") and not said:
        parts.append(f"{tasks.get('done') or 0} of {tasks['total']} tasks done")
    if not parts and project.get("stage_note"):
        parts.append(str(project["stage_note"]))
    if parts:
        return " · ".join(parts), "facts"
    label = clip_words((project.get("pl") or {}).get("summary"), LABEL_MAX)
    if label:
        return label, "lead"
    stage = str(project.get("stage") or "")
    return _STAGE_WORDS.get(stage, stage), "facts"


def working_items(
    body: Mapping[str, Any], links: _Links, now: float, held: Iterable[str] = ()
) -> list[WorkingItem]:
    """
    Projects agents still work on: problems first, then furthest along, then longest in stage.

    :param held: Projects that only wait on people (listed under waiting instead).
    """
    prs = {p["id"]: p for p in body.get("prs") or []}
    skip = set(held)
    items: list[WorkingItem] = []
    for project in body.get("projects") or []:
        if not is_working(project, now) or project["slug"] in skip:
            continue
        # A ready PR already waits on a person: the line says what agents still do.
        pr = _work_pr(project, prs)
        status, source = work_status(project, pr)
        items.append(
            WorkingItem(
                project=project["slug"],
                title=project.get("title") or project["slug"],
                stage=project["stage"],
                since=int(project.get("stage_since") or 0),
                pr=WorkingPr(
                    id=pr["id"],
                    number=pr["number"],
                    state=pr["state"],
                    ready=bool(pr.get("ready")),
                    title=_clip(pr.get("title")) or f"PR #{pr['number']}",
                    href=links.pr(pr["id"]),
                )
                if pr is not None
                else None,
                status=status,
                status_source=source,
                doing=_clip((project.get("pl") or {}).get("summary")),
                problem=_problem(project),
                running=_project_running(project),
                href=links.project(project["slug"]),
            )
        )
    return sorted(
        items,
        key=lambda i: (i.problem is None, -_STAGE_RANK.get(i.stage, -1), i.since, i.project),
    )


def _queued_problem(project: Mapping[str, Any], factory_paused: bool) -> str | None:
    """Why a queued project can't start: its lead's machine failed, it is blocked or paused."""
    overlay = project.get("overlay")
    if overlay not in _STUCK_OVERLAYS or (overlay == "paused" and factory_paused):
        return None
    return _problem(project)


def start_reason(start: Mapping[str, Any], lead: str = "the lead") -> str | None:
    """
    Why a queued project has no working lead yet, from its ``start`` field.

    :param start: ``ProjectStartView`` JSON.
    :param lead: Who spawns project leads when the server does not (``Atlas``).
    """
    phase = str(start.get("phase") or "")
    label = str(start.get("label") or "").strip()
    if start.get("state") == "starting":
        attempt = start.get("attempt")
        if isinstance(attempt, int) and attempt > 1:
            verb = f"starting up on a new sandbox (try {attempt})"
        else:
            verb = "restarting" if start.get("restart") else "starting up"
        step = _START_TEXT.get(phase) or (
            _lower_first(label) if label and label != "Starting" else None
        )
        return f"{verb}: {step}" if step else verb
    if start.get("state") != "queued":
        return None
    if phase == "pod_cap":
        ahead = int(start.get("ahead") or 0)
        spot = f"{_plural(ahead, 'project')} ahead" if ahead else "next in line"
        full = _lower_first(label) if label else "every machine is in use"
        return f"waits for a free machine ({full}, {spot})"
    if phase == "lead":
        return f"waits for {lead} to start it"
    if phase == "server":
        return "starts within a minute"
    return _lower_first(label) or None


def _queued_reason(
    project: Mapping[str, Any], problem: str | None, factory_paused: bool, lead: str
) -> str:
    """Why a queued project has not started, read after its name."""
    if problem is not None:
        if project.get("overlay") == "paused":
            return f"is {problem}"
        verb = "couldn't start" if project.get("overlay") == "failed" else "can't start"
        return f"{verb}: {problem}"
    if factory_paused:
        return "waits until the factory resumes"
    waiting = _WAITING_ON_RE.match(str(project.get("overlay_reason") or ""))
    if waiting:
        return f"waits for {waiting['deps']} to finish"
    start = project.get("start")
    said = start_reason(start, lead) if isinstance(start, Mapping) else None
    if said:
        return said
    # Older servers: the sandbox step only.
    provisioning = project.get("provisioning") or {}
    step = _STEP_TEXT.get(str(provisioning.get("step") or "")) or _PROVISIONING_TEXT.get(
        str(provisioning.get("stage") or "")
    )
    attempt = provisioning.get("attempt")
    head = (
        f"starting up on a new sandbox (try {attempt})"
        if isinstance(attempt, int) and attempt > 1
        else "starting up"
    )
    if step:
        return f"{head}: {step}"
    if project.get("pl") is not None:
        return head
    return "not started yet"


def _step_since(project: Mapping[str, Any], problem: str | None) -> int | None:
    """When the start step a queued project's reason names began (its image pull...), if known."""
    if problem is not None:
        return None
    start = project.get("start")
    if isinstance(start, Mapping) and start.get("state") == "starting":
        since = start.get("phase_since") if start.get("phase") in _STEP_TEXT else None
        return since if isinstance(since, int) else None
    provisioning = project.get("provisioning") or {}
    since = provisioning.get("since")
    return since if provisioning.get("step") and isinstance(since, int) else None


def next_items(body: Mapping[str, Any], links: _Links) -> list[NextItem]:
    """Queued projects, those that can't start first, then oldest first, with why each waits."""
    paused = (body.get("factory") or {}).get("state") == "paused"
    root = _root_member(body)
    lead = _agent_name(root.get("title"), None, None) if root is not None else None
    items: list[NextItem] = []
    for project in body.get("projects") or []:
        if not _is_live(project) or project.get("stage") != "queued":
            continue
        problem = _queued_problem(project, paused)
        items.append(
            NextItem(
                project=project["slug"],
                title=project.get("title") or project["slug"],
                reason=_queued_reason(project, problem, paused, lead or "the lead"),
                problem=problem,
                since=int(project.get("stage_since") or 0),
                step_since=None if paused else _step_since(project, problem),
                href=links.project(project["slug"]),
            )
        )
    return sorted(items, key=lambda i: (i.problem is None, i.since, i.project))


def _root_member(body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    root_id = (body.get("factory") or {}).get("root_session_id")
    return next((m for m in body.get("members") or [] if m.get("session_id") == root_id), None)


def note_parts(
    text: str, body: Mapping[str, Any], incidents: Iterable[FactoryIssue], links: _Links
) -> list[TextPart]:
    """
    *text* split so each PR, decision or incident it names links to its page.

    An id links only when it resolves: a PR the factory tracks (``#9826``,
    ``PR #9826``, ``!9826``), a decision the viewer sees, an open incident.
    """
    prs: dict[int, str] = {}
    for pr in body.get("prs") or []:
        prs.setdefault(int(pr["number"]), str(pr["id"]))
    decisions = {str(d.get("ref")) for d in body.get("decisions") or []}
    numbers = {issue.number for issue in incidents}
    parts: list[TextPart] = []
    at = 0
    for match in _REF_TOKEN_RE.finditer(text):
        href = None
        number = match["pr"] or match["num"]
        if number is not None and int(number) in prs:
            href = links.pr(prs[int(number)])
        elif match["decision"] is not None and match["decision"] in decisions:
            href = links.decision(match["decision"])
        elif match["incident"] is not None and int(match["incident"]) in numbers:
            href = links.incident(int(match["incident"]))
        if href is None:
            continue
        if match.start() > at:
            parts.append(TextPart(text=text[at : match.start()]))
        parts.append(TextPart(text=match.group(0), href=href))
        at = match.end()
    if at < len(text):
        parts.append(TextPart(text=text[at:]))
    return parts


def lead_note(
    body: Mapping[str, Any], links: _Links, now: float, incidents: Iterable[FactoryIssue] = ()
) -> LeadNote | None:
    """
    The root lead's own one-liner (its beacon), when it set one and the viewer reads it.

    Trimmed at a word to :data:`NOTE_MAX`, with the ids it names linked. Left
    out once the lead has been quiet for :data:`LEAD_NOTE_MAX_AGE_S`: by then
    it no longer says what the factory is doing.
    """
    root = _root_member(body)
    text = clip_words((root or {}).get("summary"), NOTE_MAX)
    if root is None or text is None:
        return None
    last = root.get("last_activity_at")
    since = int(last) if isinstance(last, int | float) and last > 0 else None
    if since is not None and now - since > LEAD_NOTE_MAX_AGE_S:
        return None
    return LeadNote(
        who=_agent_name(root.get("title"), None, None) or "The lead",
        text=text,
        parts=note_parts(text, body, incidents, links),
        since=since,
        session_id=root["session_id"],
        href=links.agent(root["session_id"]),
    )


def check_in(body: Mapping[str, Any], links: _Links) -> CheckIn | None:
    """When the root lead next wakes up on its own; ``None`` while paused or done."""
    factory = body.get("factory") or {}
    if factory.get("state") in ("paused", "done"):
        return None
    root_id = factory.get("root_session_id")
    beat = next(
        (b for b in factory.get("next_beats") or [] if b.get("session_id") == root_id), None
    )
    if beat is None or not beat.get("at"):
        return None
    root = _root_member(body) or {}
    title = root.get("title") or beat.get("title")
    return CheckIn(
        who=_agent_name(title, None, None) or "The lead",
        at=int(beat["at"]),
        session_id=str(root_id),
        href=links.agent(str(root_id)),
    )


# ── Spend and health ───────────────────────────────────────────


def spend_block(
    body: Mapping[str, Any],
    cells: Sequence[FactoryCostDaily],
    *,
    idle: bool,
    now: float,
    links: _Links,
    held: Mapping[str, int] | None = None,
) -> SpendBlock:
    """
    Spend over the last 24 h, today and the current rate; idle and since when.

    The rate is the snapshot's ``totals.usd_per_h``, the one the header shows.

    :param held: Projects that only wait on people -> when the newest of their
        waits began: idle since then.
    """
    today, yesterday = utc_day(now), utc_day(now - DAY_S)
    into_today = now - datetime.fromisoformat(f"{today}T00:00:00+00:00").timestamp()
    today_usd = sum(c.cost_usd for c in cells if c.day_utc == today)
    yesterday_usd = sum(c.cost_usd for c in cells if c.day_utc == yesterday)
    priced = any(c.cost_usd > 0 for c in cells if c.day_utc in (today, yesterday))
    last_24h = today_usd + yesterday_usd * max(0.0, DAY_S - into_today) / DAY_S
    factory = body.get("factory") or {}
    rate = (body.get("totals") or {}).get("usd_per_h")
    idle_since = None
    if idle:
        waits = held or {}
        ends = [int(factory.get("started_at") or 0)]
        for project in body.get("projects") or []:
            if project.get("kind") == PROJECT_KIND_INTEGRATION:
                continue
            if project.get("slug") in waits:
                ends.append(int(waits[project["slug"]]))
            elif project.get("closed_at"):
                ends.append(int(project["closed_at"]))
            elif project.get("stage") == "merged":
                # Its lead may have validated it for a while after the merge.
                merged_at = int(project.get("stage_since") or 0)
                ends.append(max(merged_at, _lead_activity(project) or 0))
            elif project.get("stage") == "done":
                ends.append(int(project.get("stage_since") or 0))
        idle_since = max(ends) or None
    return SpendBlock(
        last_24h_usd=round(last_24h, 2) if priced else None,
        today_usd=round(today_usd, 2) if priced else None,
        usd_per_h=rate,
        total_usd=factory.get("cost_usd"),
        idle=idle,
        idle_since=idle_since,
        idle_spend=idle and rate is not None and rate >= IDLE_SPEND_MIN_USD_PER_H,
        href=links.insights(),
    )


def health_block(
    body: Mapping[str, Any], pause: FactoryEvent | None, links: _Links
) -> HealthBlock:
    """What keeps the factory from running freely, worst first."""
    factory = body.get("factory") or {}
    items: list[HealthItem] = []
    state = factory.get("state")
    if state == "paused":
        # The newest factory-wide pause says who paused it and why (a resume after it does not).
        event = pause if pause is not None and pause.kind == "control.pause" else None
        data = (event.data or {}) if event is not None else {}
        by, note = data.get("by"), data.get("note")
        items.append(
            HealthItem(
                kind="paused",
                text=(_clip(event.summary) if event is not None else None)
                or "The factory is paused",
                by=by if isinstance(by, str) and by else None,
                note=_clip(note) if isinstance(note, str) else None,
                since=event.ts // 1000 if event is not None else None,
                href=links.base,
            )
        )
    elif state == "done":
        items.append(HealthItem(kind="done", text="The factory has finished", href=links.base))
    for project in body.get("projects") or []:
        overlay = project.get("overlay")
        if not _is_live(project) or overlay not in ("blocked", "failed", "paused"):
            continue
        kind = "project_paused" if overlay == "paused" else overlay
        items.append(
            HealthItem(
                kind=kind,
                text=f"{project['slug']}: {_problem(project)}",
                project=project["slug"],
                since=int(project.get("stage_since") or 0) or None,
                href=links.project(project["slug"]),
            )
        )
    github = ((body.get("health") or {}).get("github") or {}).get("state")
    if github in ("degraded", "disconnected"):
        items.append(
            HealthItem(
                kind="github",
                text="GitHub can't be reached, so PR states may be out of date"
                if github == "disconnected"
                else "GitHub is slow to answer, so PR states may lag",
                href=links.base,
            )
        )
    order = {"paused": 0, "done": 0, "failed": 1, "blocked": 2, "project_paused": 3, "github": 4}
    items.sort(key=lambda i: (order[i.kind], i.project or ""))
    if state == "paused":
        overall: Literal["ok", "paused", "done", "attention"] = "paused"
    elif state == "done":
        overall = "done"
    elif any(i.kind in ("failed", "blocked", "github") for i in items):
        overall = "attention"
    else:
        overall = "ok"
    return HealthBlock(state=overall, items=items)


# ── The summary ────────────────────────────────────────────────


def waits_text(items: Sequence[WaitingItem], whom: str) -> str:
    """``4 PRs wait on you``, ``1 decision waits on others``, ``3 things wait on you``."""
    kinds = {item.kind for item in items}
    noun = _KIND_NOUN[next(iter(kinds))] if len(kinds) == 1 else "thing"
    verb = "waits" if len(items) == 1 else "wait"
    return f"{_plural(len(items), noun)} {verb} on {whom}"


def headline(
    state: str,
    counts: SummaryCounts,
    health: HealthBlock,
    idle_spend: bool,
    waiting: Sequence[WaitingItem] = (),
) -> str:
    """
    One plain line without clock times (each reader ages the summary itself).

    What waits on the viewer leads while work goes on (``4 PRs wait on you · 1
    project in progress``); a paused, finished or idle factory says so first
    (``Idle · 2 things wait on you``). Waits on others are named only when
    nothing waits on the viewer.
    """
    mine = [item for item in waiting if item.for_you]
    waits = waits_text(mine, "you") if mine else None
    others = waits_text(waiting, "others") if waiting and not mine else None
    if state == "paused":
        first = next((i for i in health.items if i.kind == "paused"), None)
        parts = [f"paused by {first.by}" if first is not None and first.by else "paused"]
    elif state == "done":
        parts = ["finished"]
    elif state == "idle":
        parts = ["idle, yet still spending" if idle_spend else "idle"]
    else:
        queued = counts.queued - counts.cant_start
        if counts.working:
            parts = [f"{_plural(counts.working, 'project')} in progress"]
            if counts.cant_start:
                parts.append(f"{counts.cant_start} can't start")
        elif counts.cant_start:
            parts = ["nothing in progress", f"{_plural(counts.cant_start, 'project')} can't start"]
        else:
            parts = ["nothing in progress yet"]
        if queued:
            alone = not counts.working and not counts.cant_start
            parts.append(f"{_plural(queued, 'project') if alone else queued} queued")
        if waits:
            parts.insert(0, waits)
            waits = None
    parts += [text for text in (waits, others) if text]
    line = " · ".join(parts)
    return line[:1].upper() + line[1:]


# ── The verdict ────────────────────────────────────────────────


def _and(parts: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def _state_word(state: str, health: HealthBlock) -> str:
    """``Running``, ``Idle``, ``Paused by luka``, ``Finished``."""
    if state == "paused":
        first = next((i for i in health.items if i.kind == "paused"), None)
        return f"Paused by {first.by}" if first is not None and first.by else "Paused"
    return {"done": "Finished", "idle": "Idle"}.get(state, "Running")


def _broken(
    body: Mapping[str, Any], incidents: Sequence[FactoryIssue], links: _Links
) -> tuple[list[str], list[VerdictReason]]:
    """What is broken, as sentence parts and reasons, worst first."""
    parts: list[str] = []
    reasons: list[VerdictReason] = []
    projects = [p for p in body.get("projects") or [] if _is_live(p)]
    failed = [p for p in projects if p.get("overlay") == "failed"]
    if failed:
        parts.append(f"{_plural(len(failed), 'project')} stopped")
        for project in failed:
            reasons.append(
                VerdictReason(
                    kind="project",
                    text=f"{project['slug']}: {_problem(project)}",
                    href=links.project(project["slug"]),
                )
            )
    gone = {p["slug"] for p in failed}
    live = {p["slug"] for p in projects}
    dead = [
        m
        for m in body.get("members") or []
        if m.get("is_top_level", True)
        and (m.get("health") == "dead" or m.get("status") == "failed")
        and m.get("state") != "done"
        and (not m.get("project") or (m["project"] in live and m["project"] not in gone))
    ]
    if dead:
        parts.append(f"{_plural(len(dead), 'lead')} stopped")
        for member in dead:
            name = _agent_name(member.get("title"), member.get("role"), member.get("project"))
            text = f"{name or 'a lead'} stopped"
            reasons.append(
                VerdictReason(
                    kind="agent",
                    text=text[:1].upper() + text[1:],
                    href=links.agent(str(member["session_id"])),
                )
            )
    critical = [
        i
        for i in incidents
        if i.severity == "critical"
        and i.status in ("open", "acked")
        and (i.project_slug or "") not in gone
    ]
    if critical:
        parts.append(f"{_plural(len(critical), 'critical incident')}")
        for issue in critical:
            reasons.append(
                VerdictReason(
                    kind="incident",
                    text=f"{issue.ref or f'INC-{issue.number}'}: "
                    f"{_clip(issue.summary) or 'something went wrong'}",
                    href=links.incident(issue.number),
                )
            )
    health = body.get("health") or {}
    if ((health.get("github") or {}).get("state")) == "disconnected":
        parts.append("GitHub can't be reached")
        reasons.append(
            VerdictReason(
                kind="github",
                text="GitHub can't be reached, so PR states may be out of date",
                href=links.base,
            )
        )
    if (health.get("supervisor") or {}).get("ok") is False:
        parts.append("the supervisor is failing")
        reasons.append(
            VerdictReason(
                kind="supervisor",
                text="The supervisor's last check failed, so beats and alerts may stop",
                href=links.base,
            )
        )
    return parts, reasons


def verdict(
    state: str,
    health: HealthBlock,
    waiting: Sequence[WaitingItem],
    inputs: StatusInputs,
    links: _Links,
) -> Verdict:
    """
    The top bar's dot and sentence, by fixed rules (see :class:`Verdict`).

    :param state: The summary's ``state`` (``working``, ``idle``, ``paused``, ``done``).
    """
    body = inputs.snapshot
    lead = _state_word(state, health)
    broken, reasons = _broken(body, inputs.incidents, links)
    mine = [item for item in waiting if item.for_you]
    for kind in ("decision", "pr", "incident"):
        of_kind = [item for item in mine if item.kind == kind]
        if of_kind:
            reasons.append(
                VerdictReason(
                    kind="waiting", text=waits_text(of_kind, "you"), href=of_kind[0].href
                )
            )
    if broken:
        dot: Literal["red", "amber", "green", "grey"] = "red"
        sentence = f"{lead}, {_and(broken)}"
    elif mine:
        dot, sentence = "amber", f"{lead}, {waits_text(mine, 'you')}"
    else:
        dot = "green" if state == "working" else "grey"
        calm = "Running smoothly" if state == "working" else lead
        sentence = f"{lead}, {waits_text(waiting, 'others')}" if waiting else calm
    rate = (body.get("totals") or {}).get("usd_per_h")
    return Verdict(
        dot=dot,
        sentence=sentence,
        reasons=reasons,
        usd_per_h=rate if isinstance(rate, int | float) else None,
        usd_per_h_alert=inputs.usd_per_h_alert,
    )


def build_status_summary(inputs: StatusInputs, *, now: float) -> dict[str, Any]:
    """
    The ``StatusSummary`` JSON of one viewer's factory. Pure and deterministic.

    :param inputs: See :class:`StatusInputs` (:func:`load_inputs` gathers them).
    :param now: Epoch seconds (the 24 h window and ``generated_at``).
    """
    body = inputs.snapshot
    factory = body.get("factory") or {}
    links = _Links(str(factory.get("id") or ""))
    waiting = waiting_items(inputs, links)
    held = held_projects(body, waiting, now)
    holding = {key for keys in held.values() for key in keys}
    waiting = [item.model_copy(update={"holds": item.key in holding}) for item in waiting]
    since = {item.key: item.since for item in waiting}
    held_since = {slug: max(since[key] for key in keys) for slug, keys in held.items()}
    working = working_items(body, links, now, held)
    upcoming = next_items(body, links)
    idle = not working and not upcoming
    spend = spend_block(body, inputs.cost_cells, idle=idle, now=now, links=links, held=held_since)
    health = health_block(body, inputs.pause, links)
    if factory.get("state") == "paused":
        state: Literal["working", "idle", "paused", "done"] = "paused"
    elif factory.get("state") == "done":
        state = "done"
    else:
        state = "idle" if idle else "working"
    counts = SummaryCounts(
        waiting_for_you=sum(1 for item in waiting if item.for_you),
        waiting_total=len(waiting),
        working=len(working),
        queued=len(upcoming),
        cant_start=sum(1 for item in upcoming if item.problem is not None),
        held=len(held),
    )
    summary = StatusSummary(
        factory_id=str(factory.get("id") or ""),
        factory_name=str(factory.get("name") or ""),
        generated_at=int(now),
        headline=headline(state, counts, health, spend.idle_spend, waiting),
        verdict=verdict(state, health, waiting, inputs, links),
        state=state,
        waiting=waiting,
        working=working,
        next=upcoming,
        lead=lead_note(body, links, now, inputs.incidents),
        check_in=check_in(body, links),
        spend=spend,
        health=health,
        counts=counts,
    )
    return summary.model_dump(mode="json")


def spend_text(spend: Mapping[str, Any]) -> str:
    """``$61.44 in the last 24 h · $3.10/h now``: what the card and the digest say of spend."""
    day, rate = spend.get("last_24h_usd"), spend.get("usd_per_h")
    text = (
        f"${day:.2f} in the last 24 h"
        if isinstance(day, int | float)
        else "No spend recorded in the last 24 h"
    )
    return text + (f" · ${rate:.2f}/h now" if isinstance(rate, int | float) else "")


def plain_lines(summary: Mapping[str, Any], *, now: float, base_url: str = "") -> list[str]:
    """
    The summary as short plain-text lines (e.g. for a log or a plain-text digest).

    :param summary: :func:`build_status_summary`'s JSON.
    :param now: Epoch seconds the waits are aged against.
    :param base_url: Prefix for the cockpit paths, e.g. ``https://omnigent.example``.
    """

    def age(since: int | None) -> str:
        if not since:
            return ""
        s = max(0, int(now) - since)
        days, hours, minutes = s // DAY_S, s % DAY_S // 3600, s % 3600 // 60
        if days:
            return f" · {days}d {hours}h" if hours else f" · {days}d"
        if hours:
            return f" · {hours}h {minutes}m" if minutes else f" · {hours}h"
        return f" · {minutes}m" if minutes else " · <1m"

    def until(at: int) -> str:
        s = max(0, at - int(now))
        hours, minutes = s // 3600, s % 3600 // 60
        if hours:
            return f"in {hours}h {minutes}m" if minutes else f"in {hours}h"
        return f"in {minutes}m" if minutes else "any moment"

    lines = [str(summary["headline"])]
    for item in summary["waiting"]:
        who = "you" if item["for_you"] else item["who"]
        lines.append(
            f"Waiting on {who}: {item['ref']} · {item['text']}{age(item['since'])}"
            f" {base_url}{item['href']}"
        )
    for item in summary["working"]:
        status = item.get("problem") or item["status"]
        lines.append(f"Working on {item['project']}: {status}{age(item['since'])}")
    for item in summary["next"]:
        lines.append(f"Next: {item['project']} {item['reason']}")
    beat = summary.get("check_in")
    if beat:
        lines.append(f"Next: {beat['who']} checks in again {until(int(beat['at']))}")
    spend = summary["spend"]
    if spend.get("last_24h_usd") is not None or spend.get("usd_per_h") is not None:
        lines.append(f"Spend: {spend_text(spend)}")
    for item in summary["health"]["items"]:
        lines.append(f"Health: {item['text']}")
    return [line.rstrip() for line in lines]


# ── Loading ────────────────────────────────────────────────────


def load_inputs(
    deps: FactoryDeps,
    root_id: str,
    body: Mapping[str, Any],
    raw: RawSnapshot,
    readable: set[str],
    user_id: str | None,
    now: float,
) -> StatusInputs:
    """
    Read what the snapshot does not carry (synchronous; run it in a worker thread).

    One issues read, one two-day ledger read, a roster read only when a
    project names a human owner, and a control-events read only while the
    factory is paused. Slack DM receipts come with the snapshot.

    :param body: The viewer's filtered snapshot.
    :param raw: The unfiltered snapshot (who may see which incident).
    :param readable: The members the viewer reads.
    """
    from omnigent.server.factory import detectors
    from omnigent.server.factory.people import viewer_level

    store = deps.factory_store
    hidden = {slug for slug, pl in raw.project_pl.items() if pl is not None and pl not in readable}
    named = {
        p["slug"]: p["human_owner"]
        for p in body.get("projects") or []
        if isinstance(p.get("human_owner"), str) and p["human_owner"].strip()
    }
    owners: dict[str, str] = {}
    if named:
        people = store.list_people(root_id)
        for slug, value in named.items():
            if (owner := roster_owner(people, value)) is not None:
                owners[slug] = owner
    incidents = [
        issue
        for issue in store.list_issues(root_id, class_=issues.INCIDENT, statuses=["open", "acked"])
        if issue.severity in WAIT_SEVERITIES
        and issues.visible_to(
            issue.project_slug,
            issue.target,
            hidden=hidden,
            top_level_of=raw.top_level_of,
            readable=readable,
        )
    ]
    cells = [
        cell
        for cell in store.list_cost_daily(
            root_id, since_day=utc_day(now - DAY_S), until_day=utc_day(now)
        )
        if not cell.project_slug or cell.project_slug not in hidden
    ]
    pause = None
    if (body.get("factory") or {}).get("state") == "paused":
        # Factory-wide controls only: a project's pauses would crowd out the newest one.
        controls = store.find_events(
            root_id,
            ["control.pause", "control.resume"],
            int((now - CONTROL_LOOKBACK_S) * 1000),
            subject="factory",
            limit=2000,
        )
        pause = controls[-1] if controls else None
    level = LEVEL_OWNER if deps.permission_store is None else viewer_level(deps, root_id, user_id)
    return StatusInputs(
        usd_per_h_alert=detectors.threshold(root_id, "usd_per_hour_alert"),
        snapshot=body,
        user_id=user_id,
        level=level,
        incidents=incidents,
        pause=pause,
        cost_cells=cells,
        notified=raw.notified,
        owners=owners,
    )


def summary_from_raw(
    deps: FactoryDeps,
    root_id: str,
    raw: RawSnapshot,
    readable: set[str] | None,
    user_id: str | None,
    now: float,
) -> dict[str, Any]:
    """
    The summary for one viewer from a snapshot in hand (synchronous: store reads).

    The route and the Slack digest both build through here, so they agree.

    :param readable: The members the viewer reads; ``None`` reads them all.
    """
    from omnigent.server.factory.snapshot import filter_snapshot

    members = set(raw.top_level_of) if readable is None else readable
    body = filter_snapshot(raw, members)
    inputs = load_inputs(deps, root_id, body, raw, members, user_id, now)
    return build_status_summary(inputs, now=now)


async def summary_for_viewer(
    deps: FactoryDeps,
    root_id: str,
    raw: RawSnapshot,
    readable: set[str],
    user_id: str | None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """The summary from a snapshot already in hand (the route's path)."""
    at = time.time() if now is None else now
    return await asyncio.to_thread(summary_from_raw, deps, root_id, raw, readable, user_id, at)


async def status_summary_for(
    app_state: Any, root: Conversation, user_id: str | None, *, now: float | None = None
) -> dict[str, Any]:
    """
    The ``StatusSummary`` of *root*'s factory as *user_id* may see it.

    The entry point for a digest: the caller checks that *user_id* may read
    the factory (as the route's READ check does).
    """
    from omnigent.server.factory.context import factory_deps
    from omnigent.server.factory.snapshot import get_snapshot, readable_for

    deps = factory_deps(app_state)
    raw = await get_snapshot(app_state, root)
    readable = await readable_for(user_id, raw, deps)
    return await summary_for_viewer(deps, root.id, raw, readable, user_id, now=now)


__all__ = [
    "CheckIn",
    "HealthBlock",
    "HealthItem",
    "LeadNote",
    "NextItem",
    "SlackReceipt",
    "SpendBlock",
    "StatusInputs",
    "StatusSummary",
    "SummaryCounts",
    "TextPart",
    "Verdict",
    "VerdictReason",
    "WaitingItem",
    "WorkingItem",
    "WorkingPr",
    "build_status_summary",
    "clip_words",
    "decision_who",
    "headline",
    "held_projects",
    "is_working",
    "load_inputs",
    "note_parts",
    "plain_lines",
    "pr_status",
    "spend_text",
    "start_reason",
    "status_summary_for",
    "summary_for_viewer",
    "summary_from_raw",
    "utc_day",
    "verdict",
    "waiting_items",
    "waiting_order",
    "waits_text",
    "work_status",
    "working_items",
]
