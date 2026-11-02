"""The daily digest, the welcome DM, the test DM and the verification-code DM.

The digest tells the story the cockpit tells: its verdict sentence (the top
bar's), headline, Waiting on you, In progress, Spend and Next come from the
Now card's own builder
(:func:`.status_summary.summary_from_raw`) for the recipient, filtered to
what they may read (the snapshot's ``readable_for``: hidden projects never
appear). The digest adds the last 24 h's counts, factory-wide budget lines
for owners and operators who read everything, and Sentinel's newest retro.
It is skipped when nothing happened and nothing is waiting.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory import issues, status_summary
from omnigent.server.factory.slackdm import prefs as prefs_mod
from omnigent.server.factory.slackdm import rules
from omnigent.server.factory.slackdm.render import esc, link, message

if TYPE_CHECKING:
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.slackdm.rules import FactoryCtx, Person
    from omnigent.server.factory.snapshot import RawSnapshot

WAITING_MAX: Final = 8
PROGRESS_MAX: Final = 6
NEXT_MAX: Final = 6
DAY_S: Final = 86400
STAGE_LABELS: Final = {
    "planning": "Planning",
    "building": "Building",
    "review": "Review",
    "verify": "Verify",
    "ready": "Ready",
    "merged": "Merged",
}
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


# ── The card's builder ─────────────────────────────────────────


def summary_for(
    deps: FactoryDeps,
    ctx: FactoryCtx,
    person: Person,
    vis: Visibility,
    raw: RawSnapshot | None,
    now: float,
) -> dict[str, Any] | None:
    """
    The person's Now (the cockpit card's ``StatusSummary``), or ``None`` without a snapshot.

    Synchronous (store reads): the digest and the welcome DM run it in a thread.
    """
    if raw is None:
        return None
    return status_summary.summary_from_raw(
        deps, ctx.factory_id, raw, vis.readable, person.user_id, now
    )


# ── Waiting on you ─────────────────────────────────────────────


@dataclass
class Waiting:
    """What is waiting on one person (``lines`` are escaped mrkdwn)."""

    lines: list[str] = field(default_factory=list)
    count: int = 0


def waiting_line(item: Mapping[str, Any], person: Person, now: float, base: str | None) -> str:
    """One item the card lists under Waiting on you, as a mrkdwn bullet."""
    head = link(str(item["href"]), str(item["ref"]), base)
    text = str(item["text"])
    if item["kind"] == "incident" and item.get("severity"):
        text = f"{item['severity']}: {text}"
    detail = item.get("detail")
    project = item.get("project")
    if item["kind"] == "pr" and project:
        detail = f"{project}: {detail}" if detail else str(project)
    line = f"• {head} {esc(text, 120)}"
    if detail:
        line += f" — {esc(detail, 120)}"
    since = item.get("since")
    if since:
        line += f" · waiting {rules.fmt_age(now - since)}"
    at, effect = item.get("deadline_at"), item.get("deadline_effect")
    if isinstance(at, int) and at > now and effect:
        when = prefs_mod.clock_label(person.prefs, at)
        line += f" · {esc(effect, 60)} at {when} (in {rules.fmt_age(at - now)})"
    return line


def waiting_lines(
    summary: Mapping[str, Any] | None, person: Person, now: float, base: str | None
) -> Waiting:
    """The card's Waiting on you for *person*: the same items, in the same order."""
    mine = [item for item in (summary or {}).get("waiting") or [] if item.get("for_you")]
    lines = [waiting_line(item, person, now, base) for item in mine]
    if len(lines) > WAITING_MAX:
        lines = [*lines[:WAITING_MAX], f"…and {len(lines) - WAITING_MAX} more"]
    return Waiting(lines=lines, count=len(mine))


def progress_lines(summary: Mapping[str, Any] | None, base: str | None) -> list[str]:
    """The card's Working on: each project with its stage and plain status (or problem)."""
    items = (summary or {}).get("working") or []
    lines = []
    for item in items[:PROGRESS_MAX]:
        stage = STAGE_LABELS.get(str(item["stage"]), str(item["stage"]))
        status = item.get("problem") or item["status"]
        mark = ":warning: " if item.get("problem") else ""
        name = link(str(item["href"]), str(item["project"]), base)
        lines.append(f"• {mark}{name} ({esc(stage, 20)}) — {esc(status, 160)}")
    if len(items) > PROGRESS_MAX:
        lines.append(f"…and {len(items) - PROGRESS_MAX} more")
    return lines


def spend_line(summary: Mapping[str, Any] | None, extras: Sequence[str] = ()) -> str | None:
    """
    The card's spend (the last 24 h and the current $/h), then the digest's *extras*.

    :returns: ``None`` when nothing was priced and there is nothing else to say.
    """
    spend = (summary or {}).get("spend") or {}
    priced = spend.get("last_24h_usd") is not None or spend.get("usd_per_h") is not None
    parts = [esc(status_summary.spend_text(spend), 120)] if priced else []
    parts += list(extras)
    return " · ".join(parts) if parts else None


def next_line(
    summary: Mapping[str, Any] | None, person: Person, now: float, base: str | None
) -> str | None:
    """The card's Next: queued projects with why they wait, then when Atlas checks in."""
    if summary is None:
        return None
    items = summary.get("next") or []
    parts = [
        f"{link(str(item['href']), str(item['project']), base)} {esc(item['reason'], 100)}"
        for item in items[:NEXT_MAX]
    ]
    if len(items) > NEXT_MAX:
        parts.append(f"{len(items) - NEXT_MAX} more queued")
    beat = summary.get("check_in")
    if beat and int(beat["at"]) > now:
        when = prefs_mod.clock_label(person.prefs, int(beat["at"]))
        parts.append(f"{esc(beat['who'], 40)} checks in again at {when}")
    return " · ".join(parts) if parts else None


def lead_line(summary: Mapping[str, Any] | None, base: str | None) -> str | None:
    """What Atlas said, quoted, with the ids it names linked (as the card shows it)."""
    lead = (summary or {}).get("lead")
    if not lead:
        return None
    parts = lead.get("parts") or [{"text": lead["text"]}]
    # The note is one line, already trimmed: each run is escaped as is, so the
    # spaces around a linked id stay (``esc`` would strip them).
    quote = "".join(
        link(str(part["href"]), str(part["text"]), base)
        if part.get("href")
        else issues.slack_escape(" ".join(str(part["text"]).splitlines()))
        for part in parts
    )
    return f"“{quote}”"


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


def _budget_extras(
    deps: FactoryDeps, ctx: FactoryCtx, now: float, idle: tuple[float, float] | None
) -> list[str]:
    """Factory-wide spend the card does not show: today's budget, wasted heartbeats, idling."""
    from omnigent.server.factory import budgets

    rows = budgets.spends(deps, ctx.factory_id, now)
    factory = next((s for s in rows if s.scope == budgets.SCOPE_FACTORY), None)
    parts = []
    if factory is not None and factory.usd > 0:
        parts.append(f"today ${factory.usd:.2f} of ${factory.limit:.0f}")
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
    return parts


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
    summary = summary_for(deps, ctx, person, vis, raw, now)
    waiting = waiting_lines(summary, person, now, base)
    counts, still_open = _counts(deps, ctx, vis, now)
    counted = _counts_line(counts, still_open)
    extras = _budget_extras(deps, ctx, now, idle) if vis.everything and person.acts else []
    spend = spend_line(summary, extras)
    progress = progress_lines(summary, base)
    nxt = next_line(summary, person, now, base)
    said = lead_line(summary, base)
    retro = _retro_line(deps, ctx, vis, now, base)
    if not waiting.count and counted is None and retro is None and idle is None:
        return None
    local = datetime.fromtimestamp(now, tz=prefs_mod.zone(person.prefs))
    head = (
        f"*{esc(ctx.name, 80)} · daily digest · {local.strftime('%a')} {local.day} "
        f"{local.strftime('%b')}*"
        f"  ({local.strftime('%H:%M')} {prefs_mod.tz_name(person.prefs)})"
    )
    top = [head]
    if summary is not None:
        # The cockpit's verdict first (the same fact-built sentence as its top bar).
        sentence = (summary.get("verdict") or {}).get("sentence")
        if sentence:
            top.append(f"*{esc(sentence, 160)}*")
        top.append(esc(summary["headline"], 200))
    # Each heading starts a section of its own (sections split between lines,
    # never inside a link), so a busy factory's digest keeps every heading.
    if waiting.count:
        waits = [f"*Waiting on you ({waiting.count})*", *waiting.lines]
    else:
        waits = ["*Waiting on you:* nothing"]
    doing = []
    if progress:
        doing = [f"*In progress ({len((summary or {}).get('working') or [])})*", *progress]
    day = [f"*Last 24 h*  {counted or 'nothing new'}"]
    if spend:
        day.append(f"*Spend*  {spend}")
    if nxt:
        day.append(f"*Next*  {nxt}")
    notes = []
    if said and summary is not None:
        notes.append(f"*{esc(summary['lead']['who'], 40)} said*  {said}")
    if retro:
        notes.append(f"*Sentinel*  {retro}")
    settings = link(
        rules.factory_path(ctx, "/settings/notifications"), "Notification settings", base
    )
    text = esc(f"{ctx.name} daily digest: {waiting.count} waiting on you", 150)
    blocks = message(top, settings, groups=(waits, doing, day, notes))
    return Digest(text=text, blocks=blocks, waiting=waiting.count)


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
    """The card's Waiting on you for *person* (:func:`waiting_lines`), off the event loop."""
    vis = await visibility_for(deps, raw, person.user_id)
    summary = await asyncio.to_thread(summary_for, deps, ctx, person, vis, raw, now)
    return waiting_lines(summary, person, now, base)


__all__ = [
    "Digest",
    "Visibility",
    "Waiting",
    "bot_test_message",
    "build_digest",
    "code_message",
    "lead_line",
    "next_line",
    "progress_lines",
    "settings_line",
    "spend_line",
    "summary_for",
    "test_message",
    "visibility_for",
    "waiting_async",
    "waiting_line",
    "waiting_lines",
    "welcome_message",
]
