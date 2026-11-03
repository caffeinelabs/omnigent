"""What a DM item is, who it goes to, and whether it still matters.

Items E1-E4 (decisions) are level-triggered from the open decisions; E5-E8,
E10 and E11 come from events; E9 (idle spend) from the engine's spend samples.
Every item has a per-recipient dedupe key (checked against the ``notify.slack``
ledger), a *mark* (when it became due: an item whose mark falls before the
person linked Slack is never sent, the welcome DM listed it), and a relevance
check that runs again right before sending.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote

from omnigent.factory import PROPOSAL_OPEN_STATUSES
from omnigent.server.factory.slackdm import prefs as prefs_mod
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities.factory import (
        FactoryDecision,
        FactoryEvent,
        FactoryIssue,
        FactoryPerson,
        FactoryPr,
        FactoryProposal,
    )
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.slackdm.prefs import NotifyPrefs

REMINDER_STAGES: Final = (("r4h", 4 * 3600), ("r24h", 24 * 3600))
OWNERS_AFTER_S: Final = 24 * 3600
LAST_CALL_S: Final = 2 * 3600
LAST_CALL_BEFORE_QUIET_S: Final = 15 * 60
EDGE_MAX_AGE_S: Final = 12 * 3600
HELD_MAX_AGE_S: Final = 26 * 3600
"""An event item held through quiet hours (up to a day) still goes out after them."""
INCIDENT_REPOST_S: Final = 3600
IDLE_USD: Final = 5.0
IDLE_WINDOW_S: Final = 6 * 3600

EDGE_KINDS: Final = (
    "issue.opened",
    "pr.ready",
    "control.pause",
    "control.stop",
    "control.resume",
    "factory.warning",
    "budget.near",
    "budget.exceeded",
    "chat.posted",
    "retro.added",
)
"""Event kinds the engine's cursor reads."""
PROGRESS_KINDS: Final = (
    "pr.ready",
    "pr.merged",
    "task.done",
    "stage.changed",
    "demo.published",
    "proposal.applied",
)
"""Any of these in the idle window means the factory is not idling."""

RANK: Final = {
    "deadline": 0,
    "decisions": 1,
    "reminders": 1,
    "incidents": 2,
    "prs": 3,
    "factory": 4,
    "budget": 5,
    "mentions": 6,
    "retros": 7,
}
_SEVERITY_RANK: Final = {"info": 0, "notice": 1, "warning": 2, "critical": 3}


@dataclass
class Item:
    """
    One thing to tell one person.

    :param key: Dedupe key, unique per recipient (e.g. ``decision:<id>:r4h``).
    :param subject: The ledger event's subject (``decision:D-19``, ``issue:INC-7``, …).
    :param group: The person's event group that gates it (``decisions``, ``prs``, …).
    :param stage: ``new``, ``r4h``, ``r24h``, ``owners``, ``deadline``, ``ready``, …
    :param mark: Epoch second it became due (the linked-after rule).
    :param ref: What the relevance check re-reads (``{"type": "decision", "id": …}``).
    """

    key: str
    subject: str
    group: str
    stage: str
    title: str
    text: str
    why: str
    path: str
    link_label: str
    emoji: str
    mark: float
    ref: dict[str, Any]
    lines: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)
    severity: str | None = None
    critical: bool = False
    project: str | None = None
    asker: str | None = None
    target: str | None = None
    deadline: tuple[int, str] | None = None

    @property
    def rank(self) -> int:
        """:returns: Its place in a grouped DM (last calls first)."""
        if self.stage == "deadline":
            return RANK["deadline"]
        return RANK.get(self.group, 9)


@dataclass
class Person:
    """A roster row with its parsed settings."""

    user_id: str
    role: str
    prefs: NotifyPrefs
    is_creator: bool = False

    @property
    def is_owner(self) -> bool:
        """:returns: Owner or creator."""
        return self.role == prefs_mod.ROLE_OWNER

    @property
    def acts(self) -> bool:
        """:returns: Operator or owner (can answer, review, control)."""
        return self.role != prefs_mod.ROLE_VIEWER


@dataclass
class FactoryCtx:
    """One factory as the rules see it for one tick."""

    factory_id: str
    name: str
    people: dict[str, Person]
    autonomy: str | None = None
    proposals: dict[str, FactoryProposal] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)
    human_owners: dict[str, str | None] = field(default_factory=dict)

    @property
    def bare_id(self) -> str:
        """:returns: The root id as bare hex (as cockpit URLs spell it)."""
        try:
            return norm_id(self.factory_id)
        except ValueError:
            return self.factory_id

    def owners(self) -> list[Person]:
        """:returns: Owners and the creator, roster order."""
        return [p for p in self.people.values() if p.is_owner]

    def operators(self) -> list[Person]:
        """:returns: People whose role is operator (not owners)."""
        return [p for p in self.people.values() if p.role == prefs_mod.ROLE_OPERATOR]

    def reachable(self, people: Iterable[Person]) -> list[Person]:
        """:returns: Those of *people* Slack DMs can reach now."""
        return [p for p in people if p.prefs.reachable]


def build_people(rows: Sequence[FactoryPerson]) -> dict[str, Person]:
    """:returns: User id -> :class:`Person` of a roster."""
    out: dict[str, Person] = {}
    for row in rows:
        role = prefs_mod.role_key("creator" if row.is_creator else row.role)
        out[row.user_id] = Person(row.user_id, role, prefs_mod.for_person(row), row.is_creator)
    return out


# ── Paths, ages, deadlines ─────────────────────────────────────


def factory_path(ctx: FactoryCtx, rest: str = "") -> str:
    """:returns: ``/factory/<fid>`` plus *rest*."""
    return f"/factory/{ctx.bare_id}{rest}"


def _enc(value: str) -> str:
    return quote(value, safe="")


def fmt_age(seconds: float) -> str:
    """:returns: ``"35 min"``, ``"4 h"`` or ``"2 d 18 h"``."""
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min"
    hours = seconds // 3600
    if hours < 48:
        return f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def proposal_number(subject: str | None) -> int | None:
    """:returns: ``n`` of a ``proposal:C-n`` subject."""
    text = (subject or "").removeprefix("proposal:")
    if not text.startswith("C-"):
        return None
    try:
        return int(text[2:])
    except ValueError:
        return None


def deadline_for(
    decision: FactoryDecision, proposal: FactoryProposal | None, autonomy: str | None
) -> tuple[int, str] | None:
    """
    When an open decision defaults by itself, and what happens then (pure).

    - A pending projects proposal expires 72 h after it was made (nothing is
      started); under ``grace`` autonomy it applies once its 15 min pass.
    - A pending tuning or rules proposal expires 72 h after it was made.
    - An inconclusive trial's "Keep or revert" reverts one original window
      after the trial ended; an idle, harmless one stays applied at its
      ``default_at`` instead.

    :returns: ``(epoch second, effect line)``, or ``None`` when nothing defaults.
    """
    from omnigent.server.factory import tuning
    from omnigent.server.factory.evaluate import DEFAULT_KEEP
    from omnigent.server.factory.proposals import PROPOSAL_EXPIRY_S

    if proposal is None or decision.status != "open":
        return None
    result = proposal.result if isinstance(proposal.result, Mapping) else {}
    if proposal.kind == "projects" and proposal.status in PROPOSAL_OPEN_STATUSES:
        grace = result.get("grace_until")
        if autonomy == "grace" and proposal.status == "pending" and isinstance(grace, int):
            return grace, "auto-applies unless someone objects"
        return proposal.created_at + PROPOSAL_EXPIRY_S, "expires and nothing is started"
    if proposal.kind in ("tuning", "rules") and proposal.status in PROPOSAL_OPEN_STATUSES:
        return proposal.created_at + tuning.EXPIRY_S, "expires and is not applied"
    if (
        proposal.status == "trial"
        and isinstance(result.get("evaluation"), Mapping)
        and proposal.applied_at
        and proposal.trial_until
        and result.get("keep_decision_id") in (None, decision.id)
    ):
        default_at = result.get("default_at")
        if result.get("default") == DEFAULT_KEEP and isinstance(default_at, int):
            return default_at, f"C-{proposal.number} stays applied"
        stored = result.get("trial_window_s")
        window = int(stored) if isinstance(stored, int) and stored > 0 else 0
        window = window or max(3600, proposal.trial_until - proposal.applied_at)
        return proposal.trial_until + window, f"C-{proposal.number} reverts on its own"
    return None


def decision_deadline(ctx: FactoryCtx, decision: FactoryDecision) -> tuple[int, str] | None:
    """:func:`deadline_for` with the proposal and autonomy of *ctx*."""
    number = proposal_number(decision.subject)
    proposal = ctx.proposals.get(f"C-{number}") if number is not None else None
    return deadline_for(decision, proposal, ctx.autonomy)


def load_proposals(deps: FactoryDeps, factory_id: str) -> dict[str, FactoryProposal]:
    """:returns: ``C-n`` -> the pending and on-trial proposals (synchronous)."""
    rows = deps.factory_store.list_proposals(
        factory_id, status=["pending", "awaiting_cosign", "trial"], limit=500
    )
    return {f"C-{r.number}": r for r in rows}


def deadlines(
    deps: FactoryDeps,
    factory_id: str,
    decisions: Sequence[FactoryDecision],
    root: Any,
    *,
    proposals: dict[str, FactoryProposal] | None = None,
) -> dict[str, tuple[int, str]]:
    """
    :param proposals: ``C-n`` -> the open proposals when the caller has them
        (as :func:`load_proposals` reads them); read here otherwise.
    :returns: Decision id -> its deadline, for the open ones that have one (synchronous).
    """
    from omnigent.server.factory import autonomy

    open_ = [d for d in decisions if d.status == "open" and proposal_number(d.subject)]
    if not open_:
        return {}
    ctx = FactoryCtx(factory_id, "", {})
    ctx.proposals = proposals if proposals is not None else load_proposals(deps, factory_id)
    ctx.autonomy = autonomy.factory_autonomy(deps, root) if root is not None else None
    out: dict[str, tuple[int, str]] = {}
    for decision in open_:
        found = decision_deadline(ctx, decision)
        if found is not None:
            out[decision.id] = found
    return out


# ── Recipients ─────────────────────────────────────────────────


def decision_recipients(ctx: FactoryCtx, decision: FactoryDecision) -> list[tuple[str, str]]:
    """
    Who a decision's DMs go to, and why (§6.2).

    The claimer; else a person assignee; else operators (owners when no operator
    can get DMs); owners for owner decisions.

    :returns: ``[(user id, why)]``.
    """
    if decision.claimed_by:
        person = ctx.people.get(decision.claimed_by)
        return [(person.user_id, "you claimed it")] if person is not None else []
    assignee = decision.assignee or "operators"
    if assignee == "owners":
        return [(p.user_id, "assigned to owners") for p in ctx.owners()]
    if assignee == "operators":
        ops = ctx.reachable(ctx.operators())
        if ops:
            return [(p.user_id, "assigned to operators") for p in ops]
        return [
            (p.user_id, "assigned to operators; no operator gets Slack DMs, so owners get it")
            for p in ctx.owners()
        ]
    person = ctx.people.get(assignee)
    if person is None or not person.acts:
        return [(p.user_id, f"assigned to {assignee}, who cannot answer") for p in ctx.owners()]
    return [(person.user_id, "assigned to you")]


def incident_recipients(ctx: FactoryCtx, issue: FactoryIssue) -> list[tuple[str, str]]:
    """:returns: ``[(user id, why)]`` by ``issues.is_mine``: assignee, else operators."""
    assignee = issue.assignee
    out: list[tuple[str, str]] = []
    if assignee == "owners":
        out = [(p.user_id, "assigned to owners") for p in ctx.owners()]
    elif assignee in (None, "", "operators"):
        ops = ctx.reachable(ctx.operators())
        if ops:
            out = [(p.user_id, "operators get unassigned incidents") for p in ops]
        else:
            out = [
                (p.user_id, "no operator gets Slack DMs, so owners get it") for p in ctx.owners()
            ]
        if issue.severity == "critical":
            seen = {u for u, _ in out}
            out += [
                (p.user_id, "owners get every critical incident")
                for p in ctx.owners()
                if p.user_id not in seen
            ]
    else:
        person = ctx.people.get(assignee)
        if person is not None:
            out = [(person.user_id, "assigned to you")]
    return out


def owners_plus(
    ctx: FactoryCtx, project: str | None, *, why: str, exclude: str | None = None
) -> list[tuple[str, str]]:
    """:returns: Owners, plus the project's ``human_owner``; minus the actor's label."""
    out = [(p.user_id, why) for p in ctx.owners()]
    owner = ctx.human_owners.get(project) if project else None
    if owner and owner not in {u for u, _ in out}:
        out.append((owner, f"you own project {project}"))
    if exclude:
        out = [(u, w) for u, w in out if not is_actor(u, exclude)]
    return out


def is_actor(user_id: str, by: str) -> bool:
    """:returns: Whether an event's ``by`` label (``"alice (owner)"``) names *user_id*."""
    from omnigent.server.factory.control import human_name

    name = human_name(user_id)
    return by in (user_id, name) or by.startswith((f"{name} (", f"{user_id} ("))


# ── Decisions: E1-E4 ───────────────────────────────────────────


def _decision_link(ctx: FactoryCtx, decision: FactoryDecision) -> tuple[str, str]:
    number = proposal_number(decision.subject)
    if decision.source == "proposal" and number is not None:
        return factory_path(ctx, f"/improvements/C-{number}"), f"Open C-{number}"
    return (
        factory_path(ctx, f"/decisions?decision={decision.ref}"),
        f"Open {decision.ref} in the cockpit",
    )


def _options(decision: FactoryDecision) -> str:
    labels = [
        str(o.get("label") or o.get("id") or "")
        for o in decision.options or []
        if isinstance(o, Mapping)
    ]
    labels = [label for label in labels if label][:5]
    return " · ".join(labels)


def _recommended(decision: FactoryDecision) -> str | None:
    for option in decision.options or []:
        if isinstance(option, Mapping) and option.get("recommended"):
            return str(option.get("label") or option.get("id") or "") or None
    return None


def decision_item(
    ctx: FactoryCtx,
    decision: FactoryDecision,
    *,
    stage: str,
    why: str,
    mark: float,
    now: float,
    deadline: tuple[int, str] | None,
) -> Item:
    """One E1-E4 item for one recipient."""
    path, label = _decision_link(ctx, decision)
    age = fmt_age(now - decision.created_at)
    lines: list[str] = []
    options = _options(decision)
    if options:
        recommended = _recommended(decision)
        lines.append(
            f"options: {options}" + (f" (recommended: {recommended})" if recommended else "")
        )
    asker = ctx.titles.get(decision.asked_by_session_id or "")
    due = ""
    if deadline is not None and stage != "deadline":
        due = f" · {deadline[1]} in {fmt_age(deadline[0] - now)}"
    if stage == "deadline":
        title = f"Last call: {decision.ref}"
        emoji = ":hourglass_flowing_sand:"
        group = "reminders"
    elif stage in ("r4h", "r24h"):
        title = f"Reminder: {decision.ref} is still waiting on you"
        emoji = ":alarm_clock:"
        group = "reminders"
    elif stage == "owners":
        title = f"{decision.ref} is waiting on the owners"
        emoji = ":rotating_light:"
        group = "reminders"
    else:
        title = f"{decision.ref} needs you"
        emoji = ":large_orange_circle:"
        group = "decisions"
    return Item(
        key=f"decision:{decision.id}:{stage}",
        subject=f"decision:{decision.ref}",
        group=group,
        stage=stage,
        title=title,
        text=decision.question + (f" (asked by {asker})" if asker else ""),
        why=f"Why you: {why}. Open {age}{due}.",
        path=path,
        link_label=label,
        emoji=emoji,
        mark=mark,
        ref={"type": "decision", "id": decision.id},
        lines=lines,
        project=decision.project_slug,
        asker=decision.asked_by_session_id,
        deadline=deadline,
    )


def decision_items(
    ctx: FactoryCtx,
    decision: FactoryDecision,
    now: float,
    *,
    first_sent: Mapping[str, float],
    quiet_start_of: Any = None,
) -> list[tuple[str, Item]]:
    """
    Every E1-E4 item due now for one open decision.

    :param first_sent: User id -> when they got this decision's E1 (from the ledger).
    :param quiet_start_of: ``(person, at) -> epoch`` of the quiet start before *at*
        (the last call moves before quiet hours); ``None`` disables the move.
    :returns: ``[(user id, item)]``; the engine filters by settings and ledger.
    """
    out: list[tuple[str, Item]] = []
    recipients = decision_recipients(ctx, decision)
    deadline = decision_deadline(ctx, decision)
    # A decision routed to someone later (claim, assign) is new to them then.
    mark_new = float(max(decision.created_at, decision.claimed_at or 0, decision.updated_at))
    for user_id, why in recipients:
        out.append(
            (
                user_id,
                decision_item(
                    ctx,
                    decision,
                    stage="new",
                    why=why,
                    mark=mark_new,
                    now=now,
                    deadline=deadline,
                ),
            )
        )
        base = first_sent.get(user_id, float(decision.created_at))
        for stage, after in REMINDER_STAGES:
            at = base + after
            if now >= at:
                item = decision_item(
                    ctx, decision, stage=stage, why=why, mark=at, now=now, deadline=deadline
                )
                out.append((user_id, item))
    if now >= decision.created_at + OWNERS_AFTER_S:
        seen = {u for u, _ in recipients}
        for owner in ctx.owners():
            if owner.user_id in seen:
                continue
            out.append(
                (
                    owner.user_id,
                    decision_item(
                        ctx,
                        decision,
                        stage="owners",
                        why="open 24 h, so every owner gets it",
                        mark=decision.created_at + OWNERS_AFTER_S,
                        now=now,
                        deadline=deadline,
                    ),
                )
            )
    if deadline is not None and deadline[0] - decision.created_at > LAST_CALL_S:
        targets = dict(recipients)
        for owner in ctx.owners():
            targets.setdefault(owner.user_id, "owners get every last call")
        for user_id, why in targets.items():
            at = float(deadline[0] - LAST_CALL_S)
            person = ctx.people.get(user_id)
            if person is not None and quiet_start_of is not None:
                moved = quiet_start_of(person, float(deadline[0]))
                if moved is not None:
                    at = min(at, moved - LAST_CALL_BEFORE_QUIET_S)
            if now < at or now >= deadline[0]:
                continue
            item = decision_item(
                ctx, decision, stage="deadline", why=why, mark=at, now=now, deadline=deadline
            )
            out.append((user_id, item))
    return out


# ── Events: E5-E8, E10, E11 ────────────────────────────────────


def _event_item(
    event: FactoryEvent,
    *,
    key: str,
    group: str,
    stage: str,
    title: str,
    text: str,
    why: str,
    path: str,
    label: str,
    emoji: str,
    ref: dict[str, Any] | None = None,
    severity: str | None = None,
) -> Item:
    return Item(
        key=key,
        subject=event.subject or event.kind,
        group=group,
        stage=stage,
        title=title,
        text=text,
        why=f"Why you: {why}.",
        path=path,
        link_label=label,
        emoji=emoji,
        mark=event.ts / 1000,
        ref=ref or {"type": "event", "ts": event.ts},
        severity=severity,
        project=event.project_slug,
    )


def incident_items(
    ctx: FactoryCtx, event: FactoryEvent, issue: FactoryIssue
) -> list[tuple[str, Item]]:
    """E6: an incident opened (or reopened)."""
    if issue.class_ != "incident" or _SEVERITY_RANK.get(issue.severity, 0) < 2:
        return []
    out = []
    ref = issue.ref or f"INC {issue.id[:8]}"
    path = factory_path(ctx, f"/alerts/{issue.number}")
    for user_id, why in incident_recipients(ctx, issue):
        item = _event_item(
            event,
            key=f"issue:{issue.id}:{event.id}",
            group="incidents",
            stage="opened",
            title=f"{ref} {issue.severity}"
            + (" (reopened)" if event.data and event.data.get("reopened") else ""),
            text=f"{issue.summary} ({issue.rule})",
            why=why,
            path=path,
            label=f"Open {ref}",
            emoji=":red_circle:" if issue.severity == "critical" else ":warning:",
            ref={"type": "issue", "id": issue.id},
            severity=issue.severity,
        )
        item.critical = issue.severity == "critical"
        item.target = issue.target
        item.subject = f"issue:{ref}"
        out.append((user_id, item))
    return out


def pr_items(
    ctx: FactoryCtx, event: FactoryEvent, pr: FactoryPr, base_url: str | None
) -> list[tuple[str, Item]]:
    """E5: a PR became ready for human merge."""
    owner = ctx.human_owners.get(pr.project_slug or "")
    if owner and owner in ctx.people:
        recipients = [(owner, f"you own project {pr.project_slug}")]
    else:
        recipients = [
            (p.user_id, "you can review and nobody owns the project")
            for p in ctx.people.values()
            if p.acts
        ]
    from omnigent.server.factory.api.pr_preview import preview_stable_path

    links: list[tuple[str, str]] = []
    if isinstance(pr.preview, Mapping) and pr.preview and base_url:
        links.append((base_url + preview_stable_path(pr.id), "preview"))
    links.append((pr.url, "GitHub"))
    out = []
    for user_id, why in recipients:
        item = _event_item(
            event,
            key=f"pr:{pr.id}:{pr.head_sha[:12] or 'head'}",
            group="prs",
            stage="ready",
            title=f"PR #{pr.number} ready for human merge",
            text=pr.title or f"{pr.repo}#{pr.number}",
            why=why,
            path=factory_path(ctx, f"/prs/{_enc(pr.id)}"),
            label=f"Open PR #{pr.number} in the cockpit",
            emoji=":white_check_mark:",
            ref={"type": "pr", "id": pr.id, "head_sha": pr.head_sha},
        )
        item.subject = f"pr:{pr.repo}#{pr.number}"
        item.links = list(links)
        out.append((user_id, item))
    return out


def control_items(ctx: FactoryCtx, event: FactoryEvent) -> list[tuple[str, Item]]:
    """E7: a factory or project paused, stopped or resumed (not by you)."""
    data = event.data or {}
    target = str(data.get("target") or "")
    if target != "factory" and not target.startswith("project:"):
        return []
    action = event.kind.removeprefix("control.")
    verb = {"pause": "paused", "stop": "stopped", "resume": "resumed"}.get(action, action)
    by = str(data.get("by") or data.get("from") or "someone")
    project = target.removeprefix("project:") if target.startswith("project:") else None
    what = f"Project {project}" if project else "The factory"
    note = data.get("note")
    text = f"{what} {verb} by {by}" + (f": {note}" if note else "")
    path = factory_path(ctx, f"/projects/{_enc(project)}" if project else "")
    out = []
    for user_id, why in owners_plus(ctx, project, why="you own the factory", exclude=by):
        out.append(
            (
                user_id,
                _event_item(
                    event,
                    key=f"event:{event.id}",
                    group="factory",
                    stage=action,
                    title=f"{what} {verb}",
                    text=text,
                    why=why,
                    path=path,
                    label="Open the factory" if project is None else f"Open #{project}",
                    emoji=":double_vertical_bar:" if action != "resume" else ":arrow_forward:",
                ),
            )
        )
    return out


def warning_items(ctx: FactoryCtx, event: FactoryEvent) -> list[tuple[str, Item]]:
    """E7: ``factory.warning`` (e.g. GitHub budget low, an answer whose action failed)."""
    return [
        (
            user_id,
            _event_item(
                event,
                key=f"event:{event.id}",
                group="factory",
                stage="warning",
                title="Factory warning",
                text=event.summary,
                why=why,
                path=factory_path(ctx),
                label="Open the factory",
                emoji=":warning:",
                severity=event.severity,
            ),
        )
        for user_id, why in owners_plus(ctx, event.project_slug, why="you own the factory")
    ]


def budget_items(ctx: FactoryCtx, event: FactoryEvent) -> list[tuple[str, Item]]:
    """E8: a factory or project budget at 80 % or 100 % (a task's own budget is its loop's)."""
    if (event.data or {}).get("scope") == "task":
        return []
    exceeded = event.kind == "budget.exceeded"
    project = event.project_slug
    path = (
        factory_path(ctx, f"/projects/{_enc(project)}#loops")
        if project
        else factory_path(ctx, "/settings/budgets")
    )
    title = ("Budget exceeded" if exceeded else "Budget at 80 %") + (
        f": #{project}" if project else ": the factory today"
    )
    return [
        (
            user_id,
            _event_item(
                event,
                key=f"event:{event.id}",
                group="budget",
                stage="exceeded" if exceeded else "near",
                title=title,
                text=event.summary,
                why=why,
                path=path,
                label="Open the budgets" if not project else f"Open #{project}",
                emoji=":money_with_wings:",
                severity=event.severity,
            ),
        )
        for user_id, why in owners_plus(ctx, project, why="owners set the budgets")
    ]


def mention_items(ctx: FactoryCtx, event: FactoryEvent) -> list[tuple[str, Item]]:
    """E10: someone else @mentioned you in the Room."""
    data = event.data or {}
    mentions = {str(m) for m in data.get("mentions") or []}
    thread = str(data.get("thread") or "")
    message_id = str(data.get("message_id") or "")
    if not mentions or not thread or not message_id:
        return []
    author = str(data.get("by") or "")
    out = []
    for person in ctx.people.values():
        if f"@{person.user_id}" not in mentions or author == person.user_id:
            continue
        item = _event_item(
            event,
            key=f"chat:{message_id}",
            group="mentions",
            stage="mention",
            title=f"You were mentioned in {thread}",
            text=event.summary,
            why="you were @mentioned",
            path=factory_path(ctx, f"/room/{_enc(thread)}"),
            label="Open the thread",
            emoji=":speech_balloon:",
            ref={"type": "chat", "thread": thread, "seq": data.get("seq"), "ts": event.ts},
        )
        out.append((person.user_id, item))
    return out


def retro_items(
    ctx: FactoryCtx, event: FactoryEvent, issue: FactoryIssue | None = None
) -> list[tuple[str, Item]]:
    """E11: a lead or PL filed a retro or finding (opt-in)."""
    data = event.data or {}
    issue_id = str(data.get("issue_id") or event.id)
    what = "finding" if data.get("class") == "finding" else "retro"
    author = ctx.titles.get(str(data.get("by") or "")) or "An agent"
    out = []
    for person in ctx.people.values():
        item = _event_item(
            event,
            key=f"retro:{issue_id}",
            group="retros",
            stage="retro",
            title=f"{author} filed a {what}",
            text=event.summary,
            why="you asked for retros",
            path=factory_path(ctx, "/improvements?tab=findings"),
            label="Open the findings",
            emoji=":mag:",
        )
        item.target = issue.target if issue is not None else None
        out.append((person.user_id, item))
    return out


def idle_items(
    ctx: FactoryCtx, *, spent: float, window_s: float, now: float, rate: float | None
) -> list[tuple[str, Item]]:
    """E9: the factory spent money for hours with nothing to show."""
    hours = round(window_s / 3600)
    rate_text = f" (now ${rate:.2f}/h)" if rate is not None else ""
    out = []
    for person in ctx.owners():
        out.append(
            (
                person.user_id,
                Item(
                    key=f"idle:{ctx.bare_id}:{prefs_mod.utc_day(now)}",
                    subject="factory",
                    group="budget",
                    stage="idle",
                    title="The factory is idling",
                    text=(
                        f"${spent:.2f} spent in the last {hours} h{rate_text} with no PR "
                        "ready or merged, no task done and no stage change. Consider Pause."
                    ),
                    why="Why you: owners pay for idle heartbeats.",
                    path=factory_path(ctx),
                    link_label="Open the Overview (Pause is there)",
                    emoji=":zzz:",
                    mark=now,
                    ref={"type": "idle"},
                ),
            )
        )
    return out


# ── Relevance right before sending ─────────────────────────────


def still_relevant(
    deps: FactoryDeps,
    ctx: FactoryCtx,
    user_id: str,
    item: Item,
    now: float,
    *,
    root_state: str | None = None,
    held: bool = False,
) -> bool:
    """
    Whether *item* still matters to *user_id* (synchronous store reads).

    A decision must still be open and theirs; an incident still open; a PR
    still ready at that head with no human review since; a mention unread;
    an event at most 12 h old (a day when it waited for quiet hours to end).

    :param held: It waited for the person's quiet hours to end.
    """
    max_age = HELD_MAX_AGE_S if held else EDGE_MAX_AGE_S
    ref = item.ref
    kind = ref.get("type")
    store = deps.factory_store
    if kind == "decision":
        decision = store.get_decision(ref["id"])
        if decision is None or decision.status != "open":
            return False
        if item.stage == "deadline":
            deadline = decision_deadline(ctx, decision)
            if deadline is None or now >= deadline[0]:
                return False
        if item.stage in ("new", "r4h", "r24h"):
            return user_id in {u for u, _ in decision_recipients(ctx, decision)}
        return True
    if kind == "issue":
        issue = store.get_issue(ref["id"])
        return issue is not None and issue.status == "open"
    if kind == "pr":
        pr = store.get_pr(ref["id"])
        if pr is None or pr.state != "open" or not pr.ready:
            return False
        if ref.get("head_sha") and pr.head_sha != ref["head_sha"]:
            return False
        review = pr.human_review if isinstance(pr.human_review, Mapping) else None
        at = review.get("at") if review else None
        return not (isinstance(at, int | float) and pr.ready_at and at >= pr.ready_at)
    if kind == "chat":
        seq = ref.get("seq")
        if not isinstance(seq, int):
            return True
        reads = store.list_chat_reads(ctx.factory_id, user_id)
        last = next((r.last_seq for r in reads if r.thread == ref.get("thread")), 0)
        return last < seq and now - ref.get("ts", now * 1000) / 1000 < max_age
    if kind == "idle":
        return root_state not in ("paused", "stopped", "done")
    if kind == "event":
        return now - ref.get("ts", 0) / 1000 < max_age
    return True


def now_s() -> float:
    """:returns: Epoch seconds."""
    return time.time()


__all__ = [
    "EDGE_KINDS",
    "EDGE_MAX_AGE_S",
    "HELD_MAX_AGE_S",
    "IDLE_USD",
    "IDLE_WINDOW_S",
    "INCIDENT_REPOST_S",
    "PROGRESS_KINDS",
    "FactoryCtx",
    "Item",
    "Person",
    "budget_items",
    "build_people",
    "control_items",
    "deadline_for",
    "deadlines",
    "decision_deadline",
    "decision_items",
    "decision_recipients",
    "fmt_age",
    "idle_items",
    "incident_items",
    "incident_recipients",
    "is_actor",
    "load_proposals",
    "mention_items",
    "owners_plus",
    "pr_items",
    "retro_items",
    "still_relevant",
    "warning_items",
]
