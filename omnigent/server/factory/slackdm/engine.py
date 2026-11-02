"""The Slack DM engine: collect items, hold them, group them, send them, record them.

One :meth:`Engine.tick` (every 30 s):

1. Factories with a working bot token and at least one linked person.
2. Candidates: open decisions (E1-E4, level-triggered), events since the
   cursor (E5-E8, E10, E11), idle spend (E9), digests due (E12).
3. Per recipient: the event group is on, the item is visible to them, it is
   not in the ledger, and it became due after they linked Slack.
4. Queue with a 30 s gather; hold 10 min while they are active in the cockpit;
   hold until quiet hours end (critical incidents may break through).
5. Send at most one DM per person per minute (everything due is grouped),
   at most 30 a day (last calls and critical incidents still go); re-check
   relevance and the stored ledger first; record one ``notify.slack`` event
   per item and recipient: the ledger that keeps a restart from sending
   anything twice.

Queues, the cursor, counters and spend samples live in memory; the ledger is
rebuilt from every ``notify.slack`` event of the last 30 days (read in pages)
and edge events are replayed from the last 12 hours, so a server roll loses or
repeats nothing.
"""

from __future__ import annotations

import asyncio
import logging
import secrets as pysecrets
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import LABEL_NAME, LABEL_STATE, STATE_DONE
from omnigent.server.factory import events
from omnigent.server.factory import presence as factory_presence
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.slackdm import digest as digest_mod
from omnigent.server.factory.slackdm import prefs as prefs_mod
from omnigent.server.factory.slackdm import render, rules
from omnigent.server.factory.slackdm import secrets as bot_secrets
from omnigent.server.factory.slackdm.client import SlackClient, SlackError
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryEvent

_logger = logging.getLogger(__name__)

LEDGER_KIND: Final = "notify.slack"
GATHER_S: Final = 30.0
HOLD_S: Final = 600.0
HOLD_MAX_S: Final = 3600.0
MIN_GAP_S: Final = 60.0
DAILY_CAP: Final = 30
BACKOFF_S: Final = (5.0, 20.0, 80.0, 300.0, 1200.0)
LEDGER_DAYS: Final = 30
REPLAY_S: Final = rules.EDGE_MAX_AGE_S
EVENTS_PER_TICK: Final = 1000
IDLE_SAMPLE_S: Final = 300.0
DIGEST_LATE_S: Final = 12 * 3600
LEDGER_PAGE: Final = 5000
RECHECK_ROWS: Final = 1000
PACE_S: Final = 1.0
"""At most one post per second and token (Slack allows about one per channel and second)."""
_SEVERITY_RANK: Final = {"info": 0, "notice": 1, "warning": 2, "critical": 3}

ClientFactory = Callable[[str], SlackClient]
CostFn = Callable[[Any, Any], Awaitable[float | None]]


def _fid(value: str) -> str:
    try:
        return norm_id(value)
    except ValueError:
        return value


@dataclass
class Pending:
    """One queued item for one person."""

    item: rules.Item
    first_seen: float
    not_before: float
    attempts: int = 0
    held_since: float | None = None
    quiet_held: bool = False


@dataclass
class Outcome:
    """What one send did (tests and logs)."""

    factory_id: str
    user_id: str
    keys: list[str]
    status: str
    error: str | None = None


@dataclass
class TickReport:
    """What one tick did."""

    sent: list[Outcome] = field(default_factory=list)
    queued: int = 0
    digests: list[str] = field(default_factory=list)


# ── Shared helpers (the routes use them too) ───────────────────


async def factory_ctx(app_state: Any, root: Any) -> rules.FactoryCtx:
    """The rules' view of one factory: roster with settings, proposals, titles, owners."""
    from omnigent.server.factory import autonomy, people
    from omnigent.server.factory.decision_routing import human_owner_of
    from omnigent.server.factory.envelope import display_title
    from omnigent.server.factory.membership import factory_members

    deps: FactoryDeps = getattr(app_state, FACTORY_STATE_ATTR)

    def _load() -> rules.FactoryCtx:
        rows = people.list_people(deps, root.id)
        name = root.labels.get(LABEL_NAME) or display_title(root.title, root.id)
        ctx = rules.FactoryCtx(root.id, name, rules.build_people(rows))
        ctx.autonomy = autonomy.factory_autonomy(deps, root)
        ctx.proposals = rules.load_proposals(deps, root.id)
        for member in factory_members(root.id, deps.conversation_store):
            ctx.titles[member.id] = display_title(member.title, member.id)
        for project in deps.factory_store.list_projects(root.id):
            ctx.human_owners[project.slug] = human_owner_of(deps, root.id, project.slug)
        return ctx

    return await asyncio.to_thread(_load)


async def record(
    app_state: Any,
    factory_id: str,
    user_id: str,
    item_key: str,
    *,
    subject: str,
    stage: str,
    title: str,
    status: str,
    project: str | None = None,
    error: str | None = None,
    batch: int = 1,
) -> None:
    """Append one ``notify.slack`` ledger event (no member ids, channels or tokens)."""
    word = {"sent": "", "failed": " failed", "muted": " muted (daily limit)"}.get(status, "")
    summary = f"Slack DM to {user_id}: {title}{word}" + (f" ({error})" if error else "")
    await events.emit(
        app_state,
        factory_id,
        LEDGER_KIND,
        severity="notice" if status == "failed" else "info",
        project=project,
        subject=subject,
        summary=summary,
        data={
            "to_user": user_id,
            "key": item_key,
            "stage": stage,
            "status": status,
            "error": error,
            "batch": batch,
        },
    )


async def ensure_channel(
    deps: FactoryDeps, factory_id: str, user_id: str, client: SlackClient, *, fresh: bool = False
) -> str:
    """:returns: The person's DM channel, opened (and stored) when unknown or *fresh*."""
    person = await asyncio.to_thread(deps.factory_store.get_person, factory_id, user_id)
    link = prefs_mod.for_person(person).slack if person is not None else None
    if link is None or not link.user_id:
        raise SlackError("user_not_found", "conversations.open")
    if link.dm_channel and not fresh:
        return link.dm_channel
    channel = await client.open_dm(link.user_id)
    await asyncio.to_thread(
        prefs_mod.update_notify,
        deps,
        factory_id,
        user_id,
        lambda raw: prefs_mod.set_link(raw, dm_channel=channel),
    )
    return channel


async def post_to_person(
    deps: FactoryDeps,
    factory_id: str,
    user_id: str,
    client: SlackClient,
    text: str,
    blocks: list[dict[str, Any]],
) -> str:
    """
    Post one DM to a linked person; a vanished DM channel is re-opened once, and
    blocks Slack refuses are retried once as plain text.

    :returns: The message ``ts``.
    :raises SlackError: When it could not be sent.
    """
    channel = await ensure_channel(deps, factory_id, user_id, client)
    try:
        return await client.post_message(channel, text, blocks)
    except SlackError as exc:
        if exc.code == "channel_not_found":
            channel = await ensure_channel(deps, factory_id, user_id, client, fresh=True)
            return await client.post_message(channel, text, blocks)
        if exc.kind == "message":
            return await client.post_message(channel, render.plain(blocks, text))
        raise


async def mark_person_error(deps: FactoryDeps, factory_id: str, user_id: str, code: str) -> None:
    """The person's Slack account cannot get DMs: their link shows the error."""
    await asyncio.to_thread(
        prefs_mod.update_notify,
        deps,
        factory_id,
        user_id,
        lambda raw: prefs_mod.set_link(raw, status="error", error=code, dm_channel=None),
    )


def new_code() -> str:
    """:returns: A random 6-digit verification code."""
    return f"{pysecrets.randbelow(1_000_000):06d}"


def ledger_rows(store: Any, factory_id: str | None, since_ms: int) -> list[FactoryEvent]:
    """
    Every ``notify.slack`` row since *since_ms*, oldest first (synchronous).

    Read in pages, so however many rows there are, none is cut off.
    """
    page = LEDGER_PAGE
    out: list[FactoryEvent] = []
    cursor = since_ms
    edge: set[str] = set()
    while True:
        rows = store.find_events(factory_id, LEDGER_KIND, cursor, limit=page)
        out += [r for r in rows if r.id not in edge]
        if len(rows) < page:
            return out
        last = rows[-1].ts
        if last == cursor:
            # A whole page within one millisecond: step past it.
            cursor, edge = last + 1, set()
        else:
            cursor, edge = last, {r.id for r in rows if r.ts == last}


def ledger_newest(store: Any, factory_id: str, since_ms: int) -> Iterator[FactoryEvent]:
    """One factory's ``notify.slack`` rows since *since_ms*, newest first (paged, synchronous)."""
    page = LEDGER_PAGE
    before_ms: int | None = None
    before_id: str | None = None
    while True:
        rows = store.list_events(
            factory_id, before_ms, page, kinds=[LEDGER_KIND], before_id=before_id
        )
        for row in rows:
            if row.ts < since_ms:
                return
            yield row
        if len(rows) < page:
            return
        before_ms, before_id = rows[-1].ts, rows[-1].id


# ── The engine ─────────────────────────────────────────────────


async def snapshot_cost(app_state: Any, root: Any) -> float | None:
    """:returns: The factory's total cost so far (the snapshot's ``cost_usd``)."""
    from omnigent.server.factory.snapshot import get_snapshot

    raw = await get_snapshot(app_state, root)
    value = (raw.body.get("factory") or {}).get("cost_usd")
    return float(value) if isinstance(value, int | float) else None


class Engine:
    """
    The Slack DM engine for one server process.

    :param app_state: ``app.state`` carrying the factory deps.
    :param clock: Wall clock in epoch seconds (tests pass a fake one).
    :param client_factory: Token -> client (tests may wrap it).
    :param cost_fn: ``(app_state, root) -> total $`` for the idle-spend rule.
    """

    def __init__(
        self,
        app_state: Any,
        *,
        clock: Callable[[], float] = time.time,
        client_factory: ClientFactory = SlackClient,
        cost_fn: CostFn = snapshot_cost,
    ) -> None:
        self.app_state = app_state
        self.clock = clock
        self.client_factory = client_factory
        self.cost_fn = cost_fn
        self.ledger: dict[tuple[str, str, str], float] = {}
        self.queues: dict[tuple[str, str], dict[str, Pending]] = {}
        self.next_dm_at: dict[tuple[str, str], float] = {}
        self.daily: dict[tuple[str, str, str], int] = {}
        self.digest_skipped: set[tuple[str, str, str]] = set()
        self.samples: dict[str, deque[tuple[float, float]]] = {}
        self.cursor_ms: int | None = None
        self._cursor_ids: set[str] = set()
        self._loaded = False
        self.pace_s = PACE_S
        self.sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
        self._last_post: dict[str, float] = {}

    @property
    def deps(self) -> FactoryDeps | None:
        """:returns: The factory deps, or ``None`` before the routes are registered."""
        deps = getattr(self.app_state, FACTORY_STATE_ATTR, None)
        return deps if isinstance(deps, FactoryDeps) else None

    # ── Ledger ──

    async def load(self) -> None:
        """Rebuild the ledger from 30 days of ``notify.slack`` events; replay 12 h of edges."""
        deps = self.deps
        if deps is None:
            return
        now = self.clock()
        since = int((now - LEDGER_DAYS * 86400) * 1000)
        rows = await asyncio.to_thread(ledger_rows, deps.factory_store, None, since)
        for event in rows:
            self._remember(event.factory_id, event.data or {}, event.ts / 1000)
        self.cursor_ms = int((now - REPLAY_S) * 1000)
        self._loaded = True

    def _remember(self, factory_id: str, data: Mapping[str, Any], at: float) -> None:
        user, key = data.get("to_user"), data.get("key")
        if isinstance(user, str) and isinstance(key, str):
            self.ledger[(_fid(factory_id), user, key)] = at

    def in_ledger(self, factory_id: str, user_id: str, key: str) -> bool:
        """:returns: Whether *key* was already sent (or failed, or muted) to *user_id*."""
        return (_fid(factory_id), user_id, key) in self.ledger

    async def _recorded(
        self, deps: FactoryDeps, factory_id: str, user_id: str, items: list[rules.Item], now: float
    ) -> set[str]:
        """
        Keys of *items* the store already records for *user_id*, right before a post.

        Covers another server process (a rollout) sending them meanwhile; the keys
        found join the in-memory ledger.
        """
        since = int((now - LEDGER_DAYS * 86400) * 1000)
        keys = {i.key for i in items}
        subjects = {events.stored_subject(i.subject) for i in items}

        def _load() -> dict[str, float]:
            found: dict[str, float] = {}
            for subject in subjects:
                rows = deps.factory_store.find_events(
                    factory_id, LEDGER_KIND, since, subject, limit=RECHECK_ROWS, newest_first=True
                )
                for event in rows:
                    data = event.data or {}
                    key = data.get("key")
                    if data.get("to_user") == user_id and key in keys:
                        found.setdefault(str(key), event.ts / 1000)
            return found

        found = await asyncio.to_thread(_load)
        for key, at in found.items():
            self.ledger[(_fid(factory_id), user_id, key)] = at
        return set(found)

    def _recent_issue_dm(self, fid: str, user_id: str, issue_id: str, now: float) -> bool:
        prefix = f"issue:{issue_id}:"
        return any(
            at > now - rules.INCIDENT_REPOST_S
            for (f, u, k), at in self.ledger.items()
            if f == fid and u == user_id and k.startswith(prefix)
        ) or any(
            p.item.key.startswith(prefix) for p in self.queues.get((fid, user_id), {}).values()
        )

    def _prune(self, now: float) -> None:
        cutoff = now - LEDGER_DAYS * 86400
        for key in [k for k, at in self.ledger.items() if at < cutoff]:
            del self.ledger[key]
        oldest = prefs_mod.utc_day(now - 2 * 86400)
        for key in [k for k in self.daily if k[2] < oldest]:
            del self.daily[key]
        self.digest_skipped = {k for k in self.digest_skipped if k[2] >= oldest}

    # ── The tick ──

    async def tick(self) -> TickReport:
        """One pass over every factory. Never raises."""
        report = TickReport()
        deps = self.deps
        if deps is None:
            return report
        if not self._loaded:
            await self.load()
        now = self.clock()
        try:
            edge = await self._read_events(deps)
        except Exception:  # noqa: BLE001 - the next tick reads again
            _logger.warning("factory slack: could not read events", exc_info=True)
            edge = {}
        from omnigent.server.factory.snapshot import list_factory_roots

        roots = await asyncio.to_thread(list_factory_roots, deps.conversation_store)
        for root in roots:
            if root.labels.get(LABEL_STATE) == STATE_DONE:
                continue
            try:
                await self._factory(deps, root, edge.get(_fid(root.id), []), now, report)
            except Exception:  # noqa: BLE001 - one factory never stops the others
                _logger.warning("factory slack: tick failed for %s", root.id, exc_info=True)
        self._prune(now)
        return report

    async def _read_events(self, deps: FactoryDeps) -> dict[str, list[Any]]:
        since = self.cursor_ms if self.cursor_ms is not None else int(self.clock() * 1000)
        rows = await asyncio.to_thread(
            lambda: deps.factory_store.find_events(
                None, list(rules.EDGE_KINDS), since, limit=EVENTS_PER_TICK
            )
        )
        out: dict[str, list[Any]] = {}
        fresh = [e for e in rows if e.id not in self._cursor_ids]
        for event in fresh:
            out.setdefault(_fid(event.factory_id), []).append(event)
        if rows:
            last = rows[-1].ts
            if last != self.cursor_ms:
                self._cursor_ids = set()
            self.cursor_ms = last
            self._cursor_ids |= {e.id for e in rows if e.ts == last}
        return out

    async def _factory(
        self, deps: FactoryDeps, root: Any, edge: list[Any], now: float, report: TickReport
    ) -> None:
        if not await asyncio.to_thread(bot_secrets.is_active, deps, root.id):
            return
        token = await asyncio.to_thread(bot_secrets.bot_token, deps, root.id)
        if token is None:
            return
        ctx = await factory_ctx(self.app_state, root)
        linked = {u: p for u, p in ctx.people.items() if p.prefs.reachable}
        if not linked:
            return
        fid = _fid(root.id)
        candidates = await self._candidates(deps, ctx, root, edge, now)
        base = render.base_url()
        raw = None
        vis: dict[str, digest_mod.Visibility] = {}

        async def visibility(user_id: str) -> digest_mod.Visibility:
            nonlocal raw
            if user_id not in vis:
                if raw is None and deps.permission_store is not None:
                    from omnigent.server.factory.snapshot import get_snapshot

                    raw = await get_snapshot(self.app_state, root)
                vis[user_id] = await digest_mod.visibility_for(deps, raw, user_id)
            return vis[user_id]

        for user_id, item in candidates:
            person = linked.get(user_id)
            if person is None or not self._wanted(person, item):
                continue
            if self.in_ledger(fid, user_id, item.key):
                continue
            queue = self.queues.setdefault((fid, user_id), {})
            if item.key in queue:
                continue
            linked_at = person.prefs.slack.linked_at or 0
            if item.mark < linked_at:
                continue
            if item.ref.get("type") == "issue" and self._recent_issue_dm(
                fid, user_id, item.ref["id"], now
            ):
                continue
            if not (await visibility(user_id)).item(item):
                continue
            queue[item.key] = Pending(item, now, now + GATHER_S)
            report.queued += 1
        client = self.client_factory(token)
        for user_id, person in linked.items():
            try:
                if await self._digest(deps, ctx, root, person, client, now, base, visibility):
                    report.digests.append(user_id)
                outcome = await self._send(deps, ctx, root, person, client, now, base)
            except _BotDown:
                return
            if outcome is not None:
                report.sent.append(outcome)

    def _wanted(self, person: rules.Person, item: rules.Item) -> bool:
        if not person.prefs.wants(item.group):
            return False
        if item.group == "incidents":
            need = _SEVERITY_RANK.get(person.prefs.incident_min, 2)
            return _SEVERITY_RANK.get(item.severity or "info", 0) >= need
        return True

    async def _candidates(
        self, deps: FactoryDeps, ctx: rules.FactoryCtx, root: Any, edge: list[Any], now: float
    ) -> list[tuple[str, rules.Item]]:
        fid = _fid(root.id)
        out: list[tuple[str, rules.Item]] = []
        decisions = await asyncio.to_thread(
            lambda: deps.factory_store.list_decisions(factory_id=root.id, status="open", limit=500)
        )

        def quiet_start_of(person: rules.Person, at: float) -> float | None:
            if not prefs_mod.in_quiet(person.prefs, at):
                return None
            return prefs_mod.quiet_start(person.prefs, at)

        first: dict[str, dict[str, float]] = {}
        for (f, user, key), at in self.ledger.items():
            if f == fid and key.startswith("decision:") and key.endswith(":new"):
                first.setdefault(key.split(":")[1], {})[user] = at
        for decision in decisions:
            out += rules.decision_items(
                ctx,
                decision,
                now,
                first_sent=first.get(decision.id, {}),
                quiet_start_of=quiet_start_of,
            )
        for event in edge:
            try:
                out += await self._edge_items(deps, ctx, event)
            except Exception:  # noqa: BLE001 - one odd event never stops the rest
                _logger.warning("factory slack: skipped event %s", event.kind, exc_info=True)
        out += await self._idle(deps, ctx, root, now)
        return out

    async def _edge_items(
        self, deps: FactoryDeps, ctx: rules.FactoryCtx, event: Any
    ) -> list[tuple[str, rules.Item]]:
        store = deps.factory_store
        data = event.data or {}
        kind = event.kind
        if kind == "issue.opened":
            issue_id = data.get("issue_id")
            issue = await asyncio.to_thread(store.get_issue, issue_id) if issue_id else None
            return rules.incident_items(ctx, event, issue) if issue is not None else []
        if kind == "pr.ready":
            pr_id = data.get("pr_id")
            pr = await asyncio.to_thread(store.get_pr, pr_id) if pr_id else None
            return rules.pr_items(ctx, event, pr, render.base_url()) if pr is not None else []
        if kind.startswith("control."):
            return rules.control_items(ctx, event)
        if kind == "factory.warning":
            return rules.warning_items(ctx, event)
        if kind.startswith("budget."):
            return rules.budget_items(ctx, event)
        if kind == "chat.posted":
            return rules.mention_items(ctx, event)
        if kind == "retro.added":
            issue_id = data.get("issue_id")
            issue = await asyncio.to_thread(store.get_issue, issue_id) if issue_id else None
            return rules.retro_items(ctx, event, issue)
        return []

    # ── Idle spend (E9) ──

    def idle_state(self, factory_id: str, now: float) -> tuple[float, float] | None:
        """:returns: ``($ spent, window s)`` over the idle window, when samples cover it."""
        samples = self.samples.get(_fid(factory_id))
        if not samples:
            return None
        start = now - rules.IDLE_WINDOW_S
        older = [s for s in samples if s[0] <= start + IDLE_SAMPLE_S]
        if not older:
            return None
        first = older[-1]
        last = samples[-1]
        return max(0.0, last[1] - first[1]), last[0] - first[0]

    async def _idle(
        self, deps: FactoryDeps, ctx: rules.FactoryCtx, root: Any, now: float
    ) -> list[tuple[str, rules.Item]]:
        fid = _fid(root.id)
        samples = self.samples.setdefault(fid, deque())
        if not samples or now - samples[-1][0] >= IDLE_SAMPLE_S:
            try:
                cost = await self.cost_fn(self.app_state, root)
            except Exception:  # noqa: BLE001 - no sample this time
                cost = None
            if cost is not None:
                samples.append((now, cost))
        while samples and samples[0][0] < now - rules.IDLE_WINDOW_S - 2 * IDLE_SAMPLE_S:
            samples.popleft()
        state = self.idle_state(fid, now)
        if state is None or state[0] < rules.IDLE_USD:
            return []
        if root.labels.get(LABEL_STATE) in ("paused", "stopped"):
            return []
        progress = await asyncio.to_thread(
            lambda: deps.factory_store.find_events(
                root.id, list(rules.PROGRESS_KINDS), int((now - state[1]) * 1000), limit=1
            )
        )
        if progress:
            return []
        from omnigent.server.factory import detectors

        return rules.idle_items(
            ctx, spent=state[0], window_s=state[1], now=now, rate=detectors.spend_rate(root.id)
        )

    def is_idle(self, factory_id: str, now: float) -> tuple[float, float] | None:
        """:returns: The idle state when the E9 rule holds (for the digest)."""
        state = self.idle_state(factory_id, now)
        return state if state is not None and state[0] >= rules.IDLE_USD else None

    # ── Sending ──

    async def _send(
        self,
        deps: FactoryDeps,
        ctx: rules.FactoryCtx,
        root: Any,
        person: rules.Person,
        client: SlackClient,
        now: float,
        base: str | None,
    ) -> Outcome | None:
        fid = _fid(root.id)
        key = (fid, person.user_id)
        queue = self.queues.get(key)
        if not queue or now < self.next_dm_at.get(key, 0.0):
            return None
        prefs = person.prefs
        quiet = prefs_mod.in_quiet(prefs, now)
        present = any(
            e.user_id == person.user_id and not e.idle
            for e in factory_presence.registry.entries(root.id)
        )
        due: list[Pending] = []
        for pending in list(queue.values()):
            if pending.not_before > now:
                continue
            critical = pending.item.critical and prefs.quiet.critical_through
            if quiet and not critical:
                pending.not_before = prefs_mod.quiet_end(prefs, now)
                pending.quiet_held = True
                continue
            if prefs.hold_while_active and present and not pending.item.critical:
                pending.held_since = pending.held_since or now
                if now - pending.held_since < HOLD_MAX_S:
                    pending.not_before = now + HOLD_S
                    continue
            due.append(pending)
        if not due:
            return None
        relevant: list[Pending] = []
        state = root.labels.get(LABEL_STATE)
        for pending in due:
            ok = await asyncio.to_thread(
                rules.still_relevant,
                deps,
                ctx,
                person.user_id,
                pending.item,
                now,
                root_state=state,
                held=pending.quiet_held,
            )
            if ok:
                relevant.append(pending)
            else:
                queue.pop(pending.item.key, None)
                _logger.debug("factory slack: dropped stale %s", pending.item.key)
        if relevant:
            done = await self._recorded(
                deps, root.id, person.user_id, [p.item for p in relevant], now
            )
            for pending in [p for p in relevant if p.item.key in done]:
                queue.pop(pending.item.key, None)
            relevant = [p for p in relevant if p.item.key not in done]
        if not relevant:
            return None
        day = prefs_mod.local_day(prefs, now).isoformat()
        count = self.daily.get((fid, person.user_id, day), 0)
        if count >= DAILY_CAP:
            # Last calls and critical incidents still go out; the rest waits for the digest.
            muted = [p for p in relevant if not _past_cap(p.item)]
            for pending in muted:
                await self._record(root.id, person.user_id, pending.item, "muted", now)
                queue.pop(pending.item.key, None)
            relevant = [p for p in relevant if _past_cap(p.item)]
            if not relevant:
                return Outcome(root.id, person.user_id, [p.item.key for p in muted], "muted")
        header = None
        if any(p.quiet_held for p in relevant):
            header = f"While you were away ({prefs.quiet.start}-{prefs.quiet.end})"
        footer = None
        if count == DAILY_CAP - 1:
            footer = f"That's {DAILY_CAP} DMs today: the rest waits for tomorrow's digest."
        items = [p.item for p in relevant]
        text, blocks = render.render_group(
            items,
            ctx.name,
            prefs=prefs,
            base=base,
            header=header,
            inbox_path=rules.factory_path(ctx, "/decisions"),
            footer=footer,
        )
        keys = [i.key for i in items]
        try:
            await self._pace(fid)
            await post_to_person(deps, root.id, person.user_id, client, text, blocks)
        except SlackError as exc:
            return await self._failed(deps, root, person, relevant, exc, now, client)
        self.next_dm_at[key] = now + MIN_GAP_S
        self.daily[(fid, person.user_id, day)] = count + 1
        for pending in relevant:
            queue.pop(pending.item.key, None)
            await self._record(
                root.id, person.user_id, pending.item, "sent", now, batch=len(items)
            )
        return Outcome(root.id, person.user_id, keys, "sent")

    async def _record(
        self,
        factory_id: str,
        user_id: str,
        item: rules.Item,
        status: str,
        now: float,
        *,
        error: str | None = None,
        batch: int = 1,
    ) -> None:
        self.ledger[(_fid(factory_id), user_id, item.key)] = now
        await record(
            self.app_state,
            factory_id,
            user_id,
            item.key,
            subject=item.subject,
            stage=item.stage,
            title=item.title,
            status=status,
            project=item.project,
            error=error,
            batch=batch,
        )

    async def _failed(
        self,
        deps: FactoryDeps,
        root: Any,
        person: rules.Person,
        pendings: list[Pending],
        exc: SlackError,
        now: float,
        client: SlackClient,
    ) -> Outcome:
        fid = _fid(root.id)
        queue = self.queues.get((fid, person.user_id), {})
        keys = [p.item.key for p in pendings]
        if exc.kind == "bot":
            await bot_secrets.mark_invalid(self.app_state, root.id, exc.code, token=client.token)
            raise _BotDown(exc.code)
        if exc.kind in ("retryable", "other"):
            for pending in pendings:
                pending.attempts += 1
                if pending.attempts >= len(BACKOFF_S):
                    queue.pop(pending.item.key, None)
                    await self._record(
                        root.id, person.user_id, pending.item, "failed", now, error=exc.code
                    )
                    continue
                wait = BACKOFF_S[pending.attempts - 1]
                pending.not_before = now + max(wait, exc.retry_after or 0.0)
            return Outcome(root.id, person.user_id, keys, "retry", exc.code)
        if exc.kind == "person":
            await mark_person_error(deps, root.id, person.user_id, exc.code)
        for pending in pendings:
            queue.pop(pending.item.key, None)
            await self._record(
                root.id, person.user_id, pending.item, "failed", now, error=exc.code
            )
        return Outcome(root.id, person.user_id, keys, "failed", exc.code)

    async def _pace(self, factory_id: str) -> None:
        """Wait until a second has passed since this factory's token last posted."""
        if self.pace_s > 0:
            last = self._last_post.get(factory_id)
            if last is not None:
                wait = self.pace_s - (time.monotonic() - last)
                if wait > 0:
                    await self.sleep(wait)
        self._last_post[factory_id] = time.monotonic()

    # ── The daily digest (E12) ──

    async def _digest(
        self,
        deps: FactoryDeps,
        ctx: rules.FactoryCtx,
        root: Any,
        person: rules.Person,
        client: SlackClient,
        now: float,
        base: str | None,
        visibility: Callable[[str], Awaitable[digest_mod.Visibility]],
    ) -> bool:
        prefs = person.prefs
        if not prefs.digest.enabled:
            return False
        fid = _fid(root.id)
        day = prefs_mod.local_day(prefs, now)
        at = prefs_mod.digest_time(prefs, day)
        key = f"digest:{day.isoformat()}"
        if not prefs_mod.digest_day_on(prefs, day) or now < at or now - at > DIGEST_LATE_S:
            return False
        if at < (prefs.slack.linked_at or 0) or self.in_ledger(fid, person.user_id, key):
            return False
        if (fid, person.user_id, day.isoformat()) in self.digest_skipped:
            return False
        if prefs_mod.in_quiet(prefs, now) or now < self.next_dm_at.get((fid, person.user_id), 0):
            return False
        from omnigent.server.factory.snapshot import get_snapshot

        raw = await get_snapshot(self.app_state, root)
        vis = await visibility(person.user_id)
        built = await asyncio.to_thread(
            digest_mod.build_digest,
            deps,
            ctx,
            person,
            vis,
            raw,
            now,
            base,
            idle=self.is_idle(root.id, now),
        )
        if built is None:
            self.digest_skipped.add((fid, person.user_id, day.isoformat()))
            return False
        item = rules.Item(
            key=key,
            subject="digest",
            group="digest",
            stage="digest",
            title=f"daily digest ({built.waiting} waiting)",
            text="",
            why="",
            path="",
            link_label="",
            emoji="",
            mark=at,
            ref={"type": "digest"},
        )
        if await self._recorded(deps, root.id, person.user_id, [item], now):
            return False
        try:
            await self._pace(fid)
            await post_to_person(deps, root.id, person.user_id, client, built.text, built.blocks)
        except SlackError as exc:
            if exc.kind == "bot":
                await bot_secrets.mark_invalid(
                    self.app_state, root.id, exc.code, token=client.token
                )
                raise _BotDown(exc.code) from None
            if exc.kind == "person":
                await mark_person_error(deps, root.id, person.user_id, exc.code)
            if exc.kind != "retryable":
                await self._record(root.id, person.user_id, item, "failed", now, error=exc.code)
            return False
        self.next_dm_at[(fid, person.user_id)] = now + MIN_GAP_S
        await self._record(root.id, person.user_id, item, "sent", now)
        return True


def _past_cap(item: rules.Item) -> bool:
    """:returns: Whether *item* goes out even past the daily cap (a last call, a critical)."""
    return item.stage == "deadline" or item.critical


class _BotDown(Exception):
    """The bot token failed: stop this factory's sends until a new one is saved."""


__all__ = [
    "BACKOFF_S",
    "DAILY_CAP",
    "GATHER_S",
    "HOLD_S",
    "LEDGER_KIND",
    "MIN_GAP_S",
    "Engine",
    "Outcome",
    "Pending",
    "TickReport",
    "ensure_channel",
    "factory_ctx",
    "ledger_newest",
    "ledger_rows",
    "mark_person_error",
    "new_code",
    "post_to_person",
    "record",
    "snapshot_cost",
]
