"""The daily digest, the welcome DM, the test DM and the verification-code DM.

The digest is built per person from what the cockpit already shows, filtered
to what they may read (the snapshot's ``readable_for``: hidden projects never
appear). Sections: Waiting on you, Last 24 h, Spend, Next, Sentinel. It is
skipped when nothing happened and nothing is waiting.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory import issues
from omnigent.server.factory.slackdm import prefs as prefs_mod
from omnigent.server.factory.slackdm import rules
from omnigent.server.factory.slackdm.render import esc, link, message

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryDecision, FactoryIssue, FactoryPr
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.slackdm.rules import FactoryCtx, Person
    from omnigent.server.factory.snapshot import RawSnapshot

WAITING_MAX: Final = 8
DAY_S: Final = 86400
COUNT_KINDS: Final = (
    "pr.ready",
    "pr.merged",
    "task.done",
    "issue.opened",
    "issue.resolved",
    "proposal.applied",
    "proposal.reverted",
    "proposal.kept",
    "decision.answered",
)
_COUNT_LABELS: Final = (
    ("pr.ready", "PR ready", "PRs ready"),
    ("pr.merged", "merged", "merged"),
    ("task.done", "task done", "tasks done"),
    ("issue.opened", "incident", "incidents"),
    ("decision.answered", "decision answered", "decisions answered"),
    ("proposal.applied", "change applied", "changes applied"),
    ("proposal.kept", "trial kept", "trials kept"),
    ("proposal.reverted", "change reverted", "changes reverted"),
)


@dataclass
class Visibility:
    """
    What one person may read in one factory (the snapshot's filter rules).

    :param readable: Session ids they read; ``None`` reads everything.
    :param hidden: Project slugs whose PL they cannot read.
    """

    readable: set[str] | None = None
    hidden: set[str] = field(default_factory=set)
    top_level_of: dict[str, str] = field(default_factory=dict)

    @property
    def everything(self) -> bool:
        """:returns: Whether nothing is hidden from them."""
        return self.readable is None or (
            not self.hidden and set(self.top_level_of.values()) <= self.readable
        )

    def project(self, slug: str | None) -> bool:
        """:returns: Whether *slug* (or no project) is visible."""
        return not slug or slug not in self.hidden

    def session(self, session_id: str | None) -> bool:
        """:returns: Whether a session (e.g. a decision's asker) is readable."""
        return not session_id or self.readable is None or session_id in self.readable

    def issue(self, project: str | None, target: str) -> bool:
        """:returns: :func:`issues.visible_to` for these sets."""
        if self.readable is None:
            return True
        return issues.visible_to(
            project,
            target,
            hidden=self.hidden,
            top_level_of=self.top_level_of,
            readable=self.readable,
        )

    def item(self, item: rules.Item) -> bool:
        """:returns: Whether a DM item is visible."""
        if not self.project(item.project) or not self.session(item.asker):
            return False
        return item.target is None or self.issue(item.project, item.target)


async def visibility_for(deps: FactoryDeps, raw: RawSnapshot | None, user_id: str) -> Visibility:
    """:returns: What *user_id* may read, from the factory's snapshot."""
    from omnigent.server.factory.snapshot import readable_for

    if deps.permission_store is None or raw is None:
        return Visibility(top_level_of=dict(raw.top_level_of) if raw is not None else {})
    readable = await readable_for(user_id, raw, deps)
    hidden = {slug for slug, pl in raw.project_pl.items() if pl is not None and pl not in readable}
    return Visibility(readable=set(readable), hidden=hidden, top_level_of=dict(raw.top_level_of))


# ── Waiting on you ─────────────────────────────────────────────


@dataclass
class Waiting:
    """What is waiting on one person (``lines`` are escaped mrkdwn)."""

    lines: list[str] = field(default_factory=list)
    count: int = 0


def _decision_line(
    ctx: FactoryCtx,
    decision: FactoryDecision,
    person: Person,
    now: float,
    base: str | None,
) -> str:
    path, _ = rules._decision_link(ctx, decision)
    head = link(path, decision.ref, base)
    waited = rules.fmt_age(now - decision.created_at)
    line = f"• {head} {esc(decision.question, 120)} — waiting {waited}"
    deadline = rules.decision_deadline(ctx, decision)
    if deadline is not None:
        at, effect = deadline
        when = prefs_mod.clock_label(person.prefs, at)
        line += f" · {esc(effect, 60)} at {when} (in {rules.fmt_age(at - now)})"
    return line


def waiting_on(
    deps: FactoryDeps,
    ctx: FactoryCtx,
    person: Person,
    vis: Visibility,
    now: float,
    base: str | None,
    *,
    decisions: Sequence[FactoryDecision] | None = None,
) -> Waiting:
    """
    Open decisions for the person (oldest first, with age and deadline), their
    open incidents, and ready PRs they can review (synchronous store reads).
    """
    from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE, LEVEL_READ

    store = deps.factory_store
    rows = (
        decisions
        if decisions is not None
        else store.list_decisions(factory_id=ctx.factory_id, status="open", limit=500)
    )
    out = Waiting()
    mine: list[FactoryDecision] = []
    for decision in sorted(rows, key=lambda d: d.created_at):
        if not vis.project(decision.project_slug) or not vis.session(decision.asked_by_session_id):
            continue
        targets = {u for u, _ in rules.decision_recipients(ctx, decision)}
        if person.is_owner and now - decision.created_at >= rules.OWNERS_AFTER_S:
            targets.add(person.user_id)
        if person.user_id in targets:
            mine.append(decision)
    level = {"owner": LEVEL_MANAGE, "operator": LEVEL_EDIT}.get(person.role, LEVEL_READ)
    incidents: list[FactoryIssue] = [
        i
        for i in store.list_issues(ctx.factory_id, class_="incident", statuses=["open"], limit=200)
        if issues.is_mine(i, person.user_id, level) and vis.issue(i.project_slug, i.target)
    ]
    prs: list[FactoryPr] = []
    if person.acts:
        for pr in store.list_prs(ctx.factory_id, states=["open"]):
            if not pr.ready or not vis.project(pr.project_slug):
                continue
            owner = ctx.human_owners.get(pr.project_slug or "")
            if owner and owner != person.user_id and owner in ctx.people:
                continue
            prs.append(pr)
    out.count = len(mine) + len(incidents) + len(prs)
    lines = [_decision_line(ctx, d, person, now, base) for d in mine]
    for issue in incidents:
        ref = issue.ref or "INC"
        path = rules.factory_path(ctx, f"/alerts/{issue.number}")
        lines.append(
            f"• {link(path, ref, base)} {esc(issue.severity, 10)} — {esc(issue.summary, 120)}"
        )
    for pr in prs:
        path = rules.factory_path(ctx, f"/prs/{pr.id}")
        age = rules.fmt_age(now - (pr.ready_at or now))
        label = link(path, f"PR #{pr.number}", base)
        lines.append(f"• {label} {esc(pr.title, 100)} — ready for merge {age}")
    if len(lines) > WAITING_MAX:
        more = len(lines) - WAITING_MAX
        lines = [*lines[:WAITING_MAX], f"…and {more} more"]
    out.lines = lines
    return out


# ── The digest ─────────────────────────────────────────────────


def _counts(
    deps: FactoryDeps, ctx: FactoryCtx, vis: Visibility, now: float
) -> tuple[dict[str, int], int]:
    found = deps.factory_store.find_events(
        ctx.factory_id, list(COUNT_KINDS), int((now - DAY_S) * 1000), limit=5000
    )
    counts: dict[str, int] = {}
    still_open = 0
    for event in found:
        if not vis.project(event.project_slug):
            continue
        data = event.data or {}
        if event.kind.startswith("issue.") and data.get("class") != "incident":
            continue
        counts[event.kind] = counts.get(event.kind, 0) + 1
    if counts.get("issue.opened"):
        still_open = sum(
            1
            for i in deps.factory_store.list_issues(
                ctx.factory_id, class_="incident", statuses=["open", "acked"], limit=200
            )
            if i.first_seen >= now - DAY_S and vis.issue(i.project_slug, i.target)
        )
    return counts, still_open


def _counts_line(counts: Mapping[str, int], still_open: int) -> str | None:
    parts = []
    for kind, one, many in _COUNT_LABELS:
        n = counts.get(kind, 0)
        if not n:
            continue
        text = f"{n} {one if n == 1 else many}"
        if kind == "issue.opened" and still_open:
            text += f" ({still_open} open)"
        parts.append(text)
    return " · ".join(parts) if parts else None


def _spend_line(
    deps: FactoryDeps,
    ctx: FactoryCtx,
    raw: RawSnapshot | None,
    now: float,
    idle: tuple[float, float] | None,
) -> str | None:
    from omnigent.server.factory import budgets, detectors

    rows = budgets.spends(deps, ctx.factory_id, now)
    factory = next((s for s in rows if s.scope == budgets.SCOPE_FACTORY), None)
    parts = []
    if factory is not None:
        parts.append(f"today ${factory.usd:.2f} of ${factory.limit:.0f}")
    rate = detectors.spend_rate(ctx.factory_id)
    if rate is None and raw is not None:
        rate = (raw.body.get("totals") or {}).get("usd_per_h")
    if isinstance(rate, int | float):
        parts.append(f"now ${rate:.2f}/h")
    waste = 0.0
    for issue in issues.open_issues(deps, ctx.factory_id, "heartbeat.waste"):
        evidence = issue.evidence if isinstance(issue.evidence, Mapping) else {}
        usd = evidence.get("usd")
        if isinstance(usd, int | float):
            waste += float(usd)
    if waste > 0:
        parts.append(f"${waste:.2f} today on heartbeats that changed nothing")
    if idle is not None:
        spent, window = idle
        parts.append(
            f":warning: ${spent:.2f} in {round(window / 3600)} h with no progress, consider Pause"
        )
    if not parts or (factory is not None and factory.usd <= 0 and idle is None and not waste):
        return None
    return " · ".join(parts)


def _next_line(raw: RawSnapshot | None, vis: Visibility) -> str | None:
    if raw is None:
        return None
    parts = []
    for project in raw.body.get("projects") or []:
        slug = project.get("slug")
        if not vis.project(slug) or project.get("status") not in (None, "active"):
            continue
        if (project.get("charter") or {}).get("kind") == "integration":
            continue
        text = f"#{slug} {project.get('stage')}"
        if project.get("overlay") == "waiting":
            text = f"#{slug} held ({esc(project.get('overlay_reason') or 'waiting', 60)})"
        summary = project.get("tasks_summary") or {}
        running, ready = summary.get("running") or 0, summary.get("ready") or 0
        if running or ready:
            text += f" ({running} running, {ready} ready)"
        parts.append(esc(text, 120))
    pending = (raw.body.get("counts") or {}).get("proposals_pending") or 0
    if pending:
        parts.append(f"{pending} proposal{'s' if pending != 1 else ''} waiting")
    return " · ".join(parts[:8]) if parts else None


def _retro_line(
    deps: FactoryDeps, ctx: FactoryCtx, vis: Visibility, now: float, base: str | None
) -> str | None:
    rows = deps.factory_store.list_issues(ctx.factory_id, class_="retro", limit=5)
    newest = next(
        (r for r in rows if r.first_seen >= now - DAY_S and vis.project(r.project_slug)), None
    )
    if newest is None:
        return None
    path = rules.factory_path(ctx, "/improvements?tab=findings")
    return f"“{esc(newest.summary, 200)}” · {link(path, 'Findings', base)}"


@dataclass
class Digest:
    """One person's digest (``lines`` are escaped mrkdwn)."""

    text: str
    blocks: list[dict[str, Any]]
    waiting: int = 0


def build_digest(
    deps: FactoryDeps,
    ctx: FactoryCtx,
    person: Person,
    vis: Visibility,
    raw: RawSnapshot | None,
    now: float,
    base: str | None,
    *,
    idle: tuple[float, float] | None = None,
) -> Digest | None:
    """:returns: The digest, or ``None`` when nothing happened and nothing waits (synchronous)."""
    waiting = waiting_on(deps, ctx, person, vis, now, base)
    counts, still_open = _counts(deps, ctx, vis, now)
    counted = _counts_line(counts, still_open)
    spend = _spend_line(deps, ctx, raw, now, idle) if vis.everything and person.acts else None
    nxt = _next_line(raw, vis)
    retro = _retro_line(deps, ctx, vis, now, base)
    if not waiting.count and counted is None and retro is None and idle is None:
        return None
    local = datetime.fromtimestamp(now, tz=prefs_mod.zone(person.prefs))
    head = (
        f"*{esc(ctx.name, 80)} · daily digest · {local.strftime('%a')} {local.day} "
        f"{local.strftime('%b')}*"
        f"  ({local.strftime('%H:%M')} {prefs_mod.tz_name(person.prefs)})"
    )
    lines = [head]
    if waiting.count:
        lines.append(f"*Waiting on you ({waiting.count})*")
        lines += waiting.lines
    else:
        lines.append("*Waiting on you:* nothing")
    lines.append(f"*Last 24 h*  {counted or 'nothing new'}")
    if spend:
        lines.append(f"*Spend*  {spend}")
    if nxt:
        lines.append(f"*Next*  {nxt}")
    if retro:
        lines.append(f"*Sentinel*  {retro}")
    settings = link(
        rules.factory_path(ctx, "/settings/notifications"), "Notification settings", base
    )
    text = esc(f"{ctx.name} daily digest: {waiting.count} waiting on you", 150)
    return Digest(text=text, blocks=message(lines, settings), waiting=waiting.count)


# ── Welcome, test and code DMs ─────────────────────────────────

_GROUP_WORDS: Final = {
    "decisions": "decisions",
    "reminders": "reminders",
    "prs": "PRs ready to merge",
    "incidents": "incidents",
    "factory": "pause/stop changes",
    "budget": "budgets and idle spend",
    "mentions": "@mentions",
    "retros": "retros and findings",
}


def settings_line(prefs: prefs_mod.NotifyPrefs) -> str:
    """:returns: ``"You'll get: decisions, … Quiet 22:00-08:00 Europe/Zagreb. Digest 09:00."``"""
    groups = [word for key, word in _GROUP_WORDS.items() if prefs.wants(key)]
    out = f"You'll get: {', '.join(groups) or 'nothing (every event is off)'}"
    if prefs.events.incidents:
        out += f" (incidents: {prefs.incident_min} and worse)"
    out += "."
    tz = prefs_mod.tz_name(prefs)
    if prefs.quiet.enabled and prefs.quiet.start != prefs.quiet.end:
        out += f" Quiet {prefs.quiet.start}-{prefs.quiet.end} {tz}"
        out += " (critical incidents come through)." if prefs.quiet.critical_through else "."
    if prefs.digest.enabled:
        when = "every day" if prefs.digest.days == "daily" else "on weekdays"
        out += f" Digest {prefs.digest.hour:02d}:00 {when}."
    return out


def test_message(
    ctx_name: str, prefs: prefs_mod.NotifyPrefs, *, by: str | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """:returns: ``(text, blocks)`` of a test DM."""
    text = esc(f"Test DM from Omnigent factory {ctx_name}", 150)
    lines = [f":wave: *Test DM* · {esc(ctx_name, 80)}", esc(settings_line(prefs), 600)]
    context = f"Sent by {esc(by, 80)}." if by else None
    return text, message(lines, context)


def bot_test_message(ctx_name: str, by: str) -> tuple[str, list[dict[str, Any]]]:
    """:returns: ``(text, blocks)`` of the owner's Slack app test DM."""
    text = esc(f"The Omnigent factory Slack app works ({ctx_name})", 150)
    lines = [
        f":white_check_mark: *The Slack app works* · {esc(ctx_name, 80)}",
        f"{esc(by, 80)} tested it from Settings > Alerts.",
    ]
    return text, message(lines)


def code_message(ctx_name: str, code: str, by: str) -> tuple[str, list[dict[str, Any]]]:
    """:returns: ``(text, blocks)`` of the verification-code DM (names who entered the account)."""
    text = f"Your code for Omnigent factory notifications is {code}"
    lines = [
        f"Your code for Omnigent factory notifications is *{code}*.",
        f"{esc(by, 120)} entered your Slack account in factory {esc(ctx_name, 80)}. "
        "If that wasn't you, ignore this message.",
    ]
    return text, message(lines, "The code works for 15 minutes.")


def welcome_message(
    ctx: FactoryCtx, prefs: prefs_mod.NotifyPrefs, waiting: Waiting, base: str | None
) -> tuple[str, list[dict[str, Any]]]:
    """:returns: ``(text, blocks)`` of the welcome DM after verification."""
    text = esc(f"Slack DMs are on for {ctx.name}: {waiting.count} waiting on you", 150)
    lines = [f":tada: *Slack DMs are on* · {esc(ctx.name, 80)}"]
    if waiting.count:
        lines.append(f"*Waiting on you now ({waiting.count})*")
        lines += waiting.lines
    else:
        lines.append("Nothing is waiting on you right now.")
    lines.append(esc(settings_line(prefs), 600))
    settings = link(
        rules.factory_path(ctx, "/settings/notifications"), "Change what you get", base
    )
    return text, message(lines, settings)


async def waiting_async(
    deps: FactoryDeps,
    ctx: FactoryCtx,
    person: Person,
    raw: RawSnapshot | None,
    now: float,
    base: str | None,
) -> Waiting:
    """:func:`waiting_on` with the person's visibility, off the event loop."""
    vis = await visibility_for(deps, raw, person.user_id)
    return await asyncio.to_thread(waiting_on, deps, ctx, person, vis, now, base)


__all__ = [
    "Digest",
    "Visibility",
    "Waiting",
    "bot_test_message",
    "build_digest",
    "code_message",
    "settings_line",
    "test_message",
    "visibility_for",
    "waiting_async",
    "waiting_on",
    "welcome_message",
]
