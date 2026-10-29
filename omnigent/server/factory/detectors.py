"""Detectors: the rules that turn factory facts into incidents and findings.

Three drivers feed them:

- the event listener (:func:`on_event`): relaunch failures, stuck alerts,
  ``loop.*`` from the loop engine, claims, gate flips, answered decisions and
  unplanned tasks;
- the "now" poller (:mod:`.activity_now`): ``agent.repeat`` and ``agent.quiet``
  while someone watches;
- the supervisor tick (:func:`on_tick`): dead members, spend rate, ready PRs
  nobody reviews, waiting decisions, gone previews, stuck mail, bus ping-pong
  and orphans, flapping gates, wasted heartbeats, and every auto-resolve.

Each rule dedupes on ``(rule, target)`` and resolves itself when its condition
clears (``issue.resolved`` by ``server``); facts (``ci.flaky``,
``ask.unneeded``, ``task.unplanned``) stay open for a person or Sentinel.
Thresholds come from :data:`DEFAULT_THRESHOLDS` through :func:`threshold`,
the seam the factory definition plugs into.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    HEALTH_DEAD,
    ISSUE_OPEN_STATUSES,
    LABEL_DEAD_REASON,
    LABEL_HEALTH,
    LABEL_ID,
    LABEL_PROJECT,
    LABEL_STATE,
    STATE_ACTIVE,
    clamp_label_value,
)
from omnigent.server.factory import events, issues
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.loops import CLAIM_EARLY
from omnigent.stores.conversation_store import runner_seen_is_fresh
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryEvent, FactoryIssue

_logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS: Final[dict[str, float]] = {
    "repeat_tool_n": 5,
    "repeat_window_m": 30,
    "quiet_m": 10,
    "ready_unreviewed_h": 4,
    "decision_waiting_m": 30,
    "mail_stuck_m": 10,
    "usd_per_hour_alert": 15.0,
    "spend_window_m": 30,
    "pingpong_n": 6,
    "pingpong_window_m": 30,
    "orphan_h": 2,
    "gate_flaps": 3,
    "gate_flap_window_h": 2,
    "ask_unneeded_n": 3,
    "ask_unneeded_s": 120,
    "heartbeat_waste_usd": 5.0,
    "dead_ticks": 2,
}
"""Code defaults (``definition.alerts.*`` overrides them once the definition lands)."""

ThresholdResolver = Callable[[str, str], Any]
_resolver: ThresholdResolver | None = None


def set_threshold_resolver(resolver: ThresholdResolver | None) -> Callable[[], None]:
    """
    Plug in a per-factory threshold source (the factory definition).

    :param resolver: ``(factory_id, key) -> value``; ``None`` (or a non-number) falls back.
    :returns: A callable that restores the previous resolver.
    """
    global _resolver
    previous, _resolver = _resolver, resolver

    def _restore() -> None:
        global _resolver
        _resolver = previous

    return _restore


def threshold(factory_id: str, key: str) -> float:
    """:returns: The threshold *key* for *factory_id* (resolver, else the code default)."""
    if _resolver is not None:
        try:
            value = _resolver(factory_id, key)
        except Exception:  # noqa: BLE001 - a broken resolver falls back to defaults
            _logger.warning("factory: threshold resolver failed for %s", key, exc_info=True)
            value = None
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            return float(value)
    return DEFAULT_THRESHOLDS[key]


@dataclass(frozen=True)
class Rule:
    """
    One detector rule.

    :param runbook: Action ids the incident page offers, in order.
    :param assignee: Default assignee (``None`` = operators).
    :param auto_resolve: Whether the detector resolves it when the condition clears.
    """

    name: str
    class_: str
    severity: str
    runbook: tuple[str, ...]
    assignee: str | None = None
    auto_resolve: bool = True


_INC = issues.INCIDENT
_FIND = issues.FINDING
_LOOP_RUNBOOK = ("raise_budget", "switch_vendor", "fresh_restart", "abandon")

RULES: Final[dict[str, Rule]] = {
    r.name: r
    for r in (
        Rule(
            "member.dead",
            _INC,
            "critical",
            ("restart", "relaunch", "pause_project", "open_agent"),
            assignee=issues.ASSIGN_OWNERS,
        ),
        Rule("agent.repeat", _INC, "warning", ("interrupt", "redispatch", "reassign", "message")),
        Rule("agent.quiet", _INC, "warning", ("interrupt", "message")),
        Rule("loop.exhausted", _INC, "warning", _LOOP_RUNBOOK),
        Rule("loop.thrashing", _INC, "warning", _LOOP_RUNBOOK),
        Rule("loop.stalled", _INC, "warning", _LOOP_RUNBOOK),
        Rule(
            "spend.rate",
            _INC,
            "warning",
            ("pause_project", "cadence_normal"),
            assignee=issues.ASSIGN_OWNERS,
        ),
        Rule("pr.ready_unreviewed", _INC, "notice", ("open_pr",)),
        Rule("decision.waiting", _INC, "notice", ("open_decision", "assign")),
        Rule("preview.gone_ready", _INC, "warning", ("redeploy_preview",)),
        Rule("mail.stuck", _INC, "warning", ("open_agent",)),
        Rule("bus.pingpong", _FIND, "notice", ("open_graph",)),
        Rule("bus.orphan", _FIND, "notice", ("open_graph",)),
        Rule("gate.flapping", _FIND, "notice", ("open_pr",)),
        Rule("claim.mismatch", _FIND, "notice", ("open_pr", "message_pl")),
        Rule("ci.flaky", _FIND, "info", (), auto_resolve=False),
        Rule("ask.unneeded", _FIND, "info", ("propose_rule",), auto_resolve=False),
        Rule("task.unplanned", _FIND, "info", ("open_task",), auto_resolve=False),
        Rule("heartbeat.waste", _FIND, "notice", ("propose_cadence",)),
        Rule(issues.HUMAN_RULE, _INC, "notice", ("open_agent",), auto_resolve=False),
    )
}
"""The rules table (spec §13.4)."""

LOOP_BAD: Final = {
    "loop.exhausted": "exhausted",
    "loop.thrashing": "thrashing",
    "loop.stalled": "stalled",
}
LOOP_GOOD: Final = frozenset(
    {"loop.converged", "loop.budget_raised", "loop.iteration", "loop.started", "loop.resumed"}
)
_LOOP_STILL_BAD: Final = frozenset({"exhausted", "thrashing", "stalled", "escalated"})
_PREVIEW_GONE: Final = frozenset({"gone", "failed"})
_STATE_EVENT_SKIP: Final = ("heartbeat.", "message.delivered", "message.bounced", "control.ack")
RERAISE_S: Final = 600.0
"""A tick condition still holding re-raises (count, last_seen, summary) this often."""
ORPHAN_LOOKBACK_S: Final = 24 * 3600


def runbook(rule: str) -> list[str]:
    """:returns: The action ids the incident page offers for *rule*."""
    found = RULES.get(rule)
    return list(found.runbook) if found is not None else []


def _deps(app_state: Any) -> FactoryDeps | None:
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    return deps if isinstance(deps, FactoryDeps) else None


async def _raise(
    app_state: Any,
    factory_id: str,
    rule: str,
    target: str,
    summary: str,
    *,
    project: str | None = None,
    evidence: Any = None,
    severity: str | None = None,
) -> FactoryIssue | None:
    spec = RULES[rule]
    return await issues.raise_issue(
        app_state,
        factory_id,
        rule=rule,
        target=target,
        summary=summary,
        class_=spec.class_,
        severity=severity or spec.severity,
        project=project,
        evidence=evidence,
        assignee=spec.assignee,
    )


async def clear(
    app_state: Any, factory_id: str, rule: str, target: str, reason: str | None = None
) -> None:
    """Auto-resolve *rule* on *target* (a no-op when nothing is open)."""
    if RULES.get(rule) is not None and RULES[rule].auto_resolve:
        await issues.clear_issue(app_state, factory_id, rule, target, reason=reason)


def _member(sid: str) -> str:
    return f"member:{norm_id(sid)}"


def _title(conv: Conversation) -> str:
    return display_title(conv.title, conv.id)


# ── Activity poller hooks ──────────────────────────────────────


async def agent_repeat(
    app_state: Any,
    factory_id: str,
    conv: Conversation,
    *,
    role: str | None,
    project: str | None,
    tool: str,
    arg: str | None,
    count: int,
    first: bool,
) -> None:
    """An agent ran the same failing call *count* times: ``agent.repeat`` and its incident."""
    window = int(threshold(factory_id, "repeat_window_m"))
    what = f"{tool} `{arg}`" if arg else tool
    summary = f"{_title(conv)} repeating · same failing {what} {count}× / {window} min"
    if first:
        await events.emit(
            app_state,
            factory_id,
            "agent.repeat",
            severity="warning",
            project=project,
            from_id=conv.id,
            subject=f"session:{conv.id}",
            summary=summary,
            data={"tool": tool, "arg": arg, "count": count, "window_m": window, "role": role},
        )
    await _raise(
        app_state,
        factory_id,
        "agent.repeat",
        _member(conv.id),
        summary,
        project=project,
        evidence={"tool": tool, "arg": arg, "count": count},
    )


async def agent_quiet(
    app_state: Any, factory_id: str, conv: Conversation, *, project: str | None, idle_s: int
) -> None:
    """A running agent produced nothing for the quiet threshold."""
    minutes = max(1, idle_s // 60)
    await _raise(
        app_state,
        factory_id,
        "agent.quiet",
        _member(conv.id),
        f"{_title(conv)} running with no new activity for {minutes} min",
        project=project,
        evidence={"idle_s": idle_s},
    )


# ── Event listener ─────────────────────────────────────────────


def _data(event: FactoryEvent) -> dict[str, Any]:
    return dict(event.data) if isinstance(event.data, dict) else {}


def _loop_target(event: FactoryEvent) -> str | None:
    data = _data(event)
    loop_id = data.get("loop_id") or data.get("id")
    if isinstance(loop_id, str) and loop_id:
        return f"loop:{loop_id}"
    return event.subject or None


async def on_event(app_state: Any, event: FactoryEvent) -> None:
    """The ``events.add_listener`` driver. Never raises."""
    try:
        await _on_event(app_state, event)
    except Exception:  # noqa: BLE001 - a detector bug must not break the emitter
        _logger.warning("factory: detector failed on %s", event.kind, exc_info=True)


async def _on_event(app_state: Any, event: FactoryEvent) -> None:
    kind = event.kind
    fid = event.factory_id
    data = _data(event)
    if kind == "needs_human" and data.get("reason") == "relaunch_failed" and event.from_id:
        await _raise(
            app_state,
            fid,
            "member.dead",
            _member(event.from_id),
            event.summary,
            project=event.project_slug,
            evidence={"event_id": event.id},
        )
    elif kind == "heartbeat.delivered" and event.to_id:
        await clear(app_state, fid, "member.dead", _member(event.to_id), "answered a heartbeat")
        await _note_beat(app_state, event)
    elif kind == "alert.stuck" and event.from_id:
        await _raise(
            app_state,
            fid,
            "agent.quiet",
            _member(event.from_id),
            event.summary,
            project=event.project_slug,
            evidence={"event_id": event.id},
        )
    elif kind in LOOP_BAD:
        target = _loop_target(event)
        if target:
            await _raise(
                app_state,
                fid,
                kind,
                target,
                event.summary,
                project=event.project_slug,
                evidence={"event_id": event.id, **_pick(data, "kind", "subject")},
            )
    elif kind in LOOP_GOOD:
        target = _loop_target(event)
        for rule in LOOP_BAD if target else ():
            await clear(app_state, fid, rule, target or "", kind)
        if kind == "loop.iteration" and data.get("kind") == "claim" and data.get("late"):
            await _on_late_refutation(app_state, event, data)
    elif kind == "pr.claim" and data.get("claim") == "ready_for_human_merge":
        await _on_claim(app_state, event, data)
    elif kind == "pr.ready" and isinstance(data.get("pr_id"), str):
        await clear(app_state, fid, "claim.mismatch", f"pr:{data['pr_id']}", "the PR turned ready")
    elif kind == "gate.changed" and data.get("gate") == "checks":
        await _on_checks(app_state, event, data)
    elif kind == "decision.answered":
        await _on_answered(app_state, event, data)
    elif kind == "task.unplanned":
        target = data.get("task") or event.subject or f"event:{event.id}"
        await _raise(
            app_state,
            fid,
            "task.unplanned",
            str(target),
            event.summary,
            project=event.project_slug,
            evidence={"event_id": event.id},
        )


def _pick(data: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {k: data[k] for k in keys if data.get(k) is not None}


async def _on_claim(app_state: Any, event: FactoryEvent, data: Mapping[str, Any]) -> None:
    pr_id = data.get("pr_id")
    if not isinstance(pr_id, str):
        return
    target = f"pr:{pr_id}"
    if data.get("claim_status") == CLAIM_EARLY:
        # Only pending gates disagree: the PL waits, nothing is refuted (or held) yet.
        return
    if data.get("claim_mismatch"):
        await _raise(
            app_state,
            event.factory_id,
            "claim.mismatch",
            target,
            f"{event.summary} · the server's gates say not ready",
            project=event.project_slug,
            evidence={"event_id": event.id},
        )
    else:
        await clear(app_state, event.factory_id, "claim.mismatch", target, "a later claim held")


async def _on_late_refutation(
    app_state: Any, event: FactoryEvent, data: Mapping[str, Any]
) -> None:
    """``claim.mismatch`` for an early READY claim a gate refuted later (claim loop verdict)."""
    pr_id = data.get("pr_id")
    if not isinstance(pr_id, str):
        return
    await _raise(
        app_state,
        event.factory_id,
        "claim.mismatch",
        f"pr:{pr_id}",
        f"{event.summary} · a gate failed after the READY claim",
        project=event.project_slug,
        evidence={"event_id": event.id, **_pick(data, "head", "refuted")},
    )


async def _on_checks(app_state: Any, event: FactoryEvent, data: Mapping[str, Any]) -> None:
    """``ci.flaky``: the checks gate passes at a head where it failed before."""
    head, pr_id = data.get("head_sha"), data.get("pr_id")
    if data.get("to") != "pass" or not head or not isinstance(pr_id, str):
        return
    deps = _deps(app_state)
    if deps is None:
        return
    since = event.ts - 7 * 86400 * 1000
    found = await asyncio.to_thread(
        deps.factory_store.find_events,
        event.factory_id,
        "gate.changed",
        since,
        event.subject,
    )
    failed_here = any(
        (e.data or {}).get("gate") == "checks"
        and (e.data or {}).get("to") == "fail"
        and (e.data or {}).get("head_sha") == head
        and e.id != event.id
        and e.ts <= event.ts
        for e in found
    )
    if failed_here:
        await _raise(
            app_state,
            event.factory_id,
            "ci.flaky",
            f"pr:{pr_id}@{str(head)[:7]}",
            f"CI failed then passed on the same head {str(head)[:7]} ({event.subject})",
            project=event.project_slug,
            evidence={"event_id": event.id, "head_sha": head},
        )


def _recommended(options: Iterable[Mapping[str, Any]]) -> str | None:
    for option in options:
        if option.get("recommended"):
            return str(option.get("id"))
    return None


def _answered_decision(deps: FactoryDeps, event: FactoryEvent, data: Mapping[str, Any]) -> Any:
    """The decision a ``decision.answered`` event names (by id or ``decision:D-n``)."""
    decision_id = data.get("decision_id")
    if isinstance(decision_id, str):
        return deps.factory_store.get_decision(decision_id)
    match = re.fullmatch(r"decision:D-([0-9]+)", event.subject or "")
    if match is None:
        return None
    return deps.factory_store.get_decision_by_number(event.factory_id, int(match.group(1)))


async def _on_answered(app_state: Any, event: FactoryEvent, data: Mapping[str, Any]) -> None:
    """``ask.unneeded``: the recommended option, answered fast, again and again by one asker."""
    deps = _deps(app_state)
    if deps is None:
        return
    fid = event.factory_id
    fast_s = threshold(fid, "ask_unneeded_s")

    def _quick(d: Any) -> bool:
        if not d.asked_by_session_id or d.answered_at is None or not d.answer_option_id:
            return False
        rec = _recommended(d.options or [])
        return rec == d.answer_option_id and d.answered_at - d.created_at < fast_s

    decision = await asyncio.to_thread(_answered_decision, deps, event, data)
    if decision is None or not _quick(decision):
        return
    rows = await asyncio.to_thread(
        lambda: deps.factory_store.list_decisions(factory_id=fid, status="answered", limit=500)
    )
    week = time.time() - 7 * 86400
    quick = [
        d
        for d in rows
        if d.asked_by_session_id == decision.asked_by_session_id
        and (d.answered_at or 0) >= week
        and _quick(d)
    ]
    if len(quick) >= threshold(fid, "ask_unneeded_n"):
        asker = decision.asked_by_session_id or ""
        await _raise(
            app_state,
            fid,
            "ask.unneeded",
            _member(asker),
            f"{len(quick)} questions answered with the recommended option in under "
            f"{int(fast_s)} s",
            project=decision.project_slug,
            evidence={"decisions": [d.id for d in quick][:10]},
        )


# ── Heartbeat waste (in memory) ────────────────────────────────

# member id -> (beat ts ms, own cost before the beat)
_open_beats: dict[str, tuple[int, float]] = {}
# (member id, utc day) -> wasted USD
_waste: dict[tuple[str, str], float] = {}


async def _note_beat(app_state: Any, event: FactoryEvent) -> None:
    from omnigent.server.factory.activity_now import own_cost

    deps = _deps(app_state)
    if deps is None or not event.to_id:
        return
    conv = await asyncio.to_thread(deps.conversation_store.get_conversation, event.to_id)
    cost = own_cost(conv) if conv is not None else None
    if cost is not None:
        _open_beats[event.to_id] = (event.ts, cost)


def _utc_day(epoch_s: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(epoch_s))


# ── Supervisor tick ────────────────────────────────────────────


@dataclass
class _Sampler:
    """Cost samples of one scope (factory or project) for the $/h rate."""

    samples: deque[tuple[float, float]]

    def rate(self, now: float, cost: float, window_s: float) -> float | None:
        if not self.samples or self.samples[-1][1] != cost or now - self.samples[-1][0] >= 60:
            self.samples.append((now, cost))
        while len(self.samples) > 1 and self.samples[0][0] < now - window_s:
            self.samples.popleft()
        first_t, first_c = self.samples[0]
        span = now - first_t
        if span < window_s / 2:
            return None
        return round(max(0.0, cost - first_c) * 3600.0 / span, 2)


_samplers: dict[tuple[str, str], _Sampler] = {}
_rates: dict[str, float | None] = {}
_down_streak: dict[str, int] = {}


def spend_rate(factory_id: str) -> float | None:
    """:returns: The factory's $/h over the spend window at the last tick, if known."""
    try:
        return _rates.get(norm_id(factory_id))
    except ValueError:
        return None


def reset() -> None:
    """Forget in-memory detector state (tests)."""
    _samplers.clear()
    _rates.clear()
    _down_streak.clear()
    _open_beats.clear()
    _waste.clear()


async def on_tick(
    app_state: Any,
    members: Sequence[Conversation],
    children: Sequence[Conversation],
    now: float,
) -> None:
    """The supervisor tick driver: every factory's tick rules and auto-resolves. Never raises."""
    by_factory: dict[str, list[Conversation]] = {}
    for member in members:
        fid = member.labels.get(LABEL_ID)
        if fid:
            by_factory.setdefault(fid, []).append(member)
    parents = {m.id for m in members}
    for fid, own in by_factory.items():
        ids = {m.id for m in own}
        kids = [c for c in children if c.parent_conversation_id in ids and c.id not in parents]
        try:
            await tick_factory(app_state, fid, own, kids, now)
        except Exception:  # noqa: BLE001 - one factory must not stop the others
            _logger.warning("factory: detectors failed for %s", fid, exc_info=True)


async def tick_factory(
    app_state: Any,
    factory_id: str,
    members: Sequence[Conversation],
    children: Sequence[Conversation],
    now: float,
) -> None:
    """Run the tick rules of one factory."""
    deps = _deps(app_state)
    if deps is None:
        return
    await issues.unsnooze_due(app_state, factory_id, now)
    open_rows = await asyncio.to_thread(issues.open_issues, deps, factory_id)
    open_by_rule: dict[str, list[FactoryIssue]] = {}
    for row in open_rows:
        open_by_rule.setdefault(row.rule, []).append(row)
    for step in (
        _dead_members,
        _repeat_cleared,
        _quiet_cleared,
        _loops_cleared,
        _spend,
        _ready_unreviewed,
        _decisions_waiting,
        _previews_gone,
        _mail_stuck,
        _bus,
        _gate_flapping,
        _heartbeat_waste,
    ):
        try:
            await step(app_state, deps, factory_id, members, children, now, open_by_rule)
        except Exception:  # noqa: BLE001 - one rule must not stop the others
            _logger.warning("factory: detector %s failed", step.__name__, exc_info=True)


OpenByRule = dict[str, list["FactoryIssue"]]


async def _sync(
    app_state: Any,
    factory_id: str,
    rule: str,
    hits: Mapping[str, tuple[str, str | None, Any]],
    open_by_rule: OpenByRule,
    reason: str,
) -> None:
    """
    Raise every hit ``target -> (summary, project, evidence)`` and clear the rest.

    A condition that is already open is re-raised at most every :data:`RERAISE_S`.
    """
    fresh = {row.target: row for row in open_by_rule.get(rule, [])}
    now = time.time()
    for target, (summary, project, evidence) in hits.items():
        row = fresh.get(target)
        if row is not None and now - row.last_seen < RERAISE_S:
            continue
        await _raise(
            app_state, factory_id, rule, target, summary, project=project, evidence=evidence
        )
    for target in fresh:
        if target not in hits:
            await clear(app_state, factory_id, rule, target, reason)


def member_down(app_state: Any, conv: Conversation, now: float) -> bool | None:
    """
    Whether a member's sandbox is gone: its runner tunnel is down and its host
    is offline (a live host relaunches on the next message).

    :returns: ``None`` when this server cannot tell (no tunnel registry).
    """
    tunnels = getattr(app_state, "tunnel_registry", None)
    if tunnels is None or not (conv.runner_id or conv.host_id):
        return None
    if conv.runner_id and (
        tunnels.get(conv.runner_id) is not None
        or runner_seen_is_fresh(conv.runner_last_seen, now=int(now))
    ):
        return False
    if conv.host_id:
        hosts = getattr(app_state, "host_registry", None)
        if hosts is not None and hosts.get(conv.host_id) is not None:
            return False
        host_store = getattr(app_state, "host_store", None)
        if host_store is not None and conv.host_id in host_store.online_host_ids([conv.host_id]):
            return False
    return True


async def _dead_members(
    app_state: Any,
    deps: FactoryDeps,
    fid: str,
    members: Sequence[Conversation],
    _children: Sequence[Conversation],
    now: float,
    open_by_rule: OpenByRule,
) -> None:
    from omnigent.server.factory.snapshot import sandbox_stage

    need = int(threshold(fid, "dead_ticks"))
    # Labels as of now: a heartbeat may have marked a member dead during this tick.
    rows = await asyncio.to_thread(
        deps.conversation_store.get_conversations, [m.id for m in members]
    )
    hits: dict[str, tuple[str, str | None, Any]] = {}
    for conv in (rows.get(m.id, m) for m in members):
        target = _member(conv.id)
        project = conv.labels.get(LABEL_PROJECT)
        if conv.labels.get(LABEL_HEALTH) == HEALTH_DEAD:
            reason = conv.labels.get(LABEL_DEAD_REASON)
            summary = f"{_title(conv)} dead · relaunch failed"
            hits[target] = (
                clamp_label_value(
                    f"{summary}: {reason}" if reason else summary, issues.SUMMARY_MAX
                ),
                project,
                {"health": "dead", **({"reason": reason} if reason else {})},
            )
            continue
        down = None
        if conv.labels.get(LABEL_STATE) == STATE_ACTIVE and sandbox_stage(conv.id) is None:
            down = await asyncio.to_thread(member_down, app_state, conv, now)
        if not down:
            _down_streak.pop(conv.id, None)
            continue
        streak = _down_streak.get(conv.id, 0) + 1
        _down_streak[conv.id] = streak
        if streak >= need:
            hits[target] = (
                f"{_title(conv)} dead · runner and host offline for {streak} ticks",
                project,
                {"runner_id": conv.runner_id, "host_id": conv.host_id, "ticks": streak},
            )
    await _sync(app_state, fid, "member.dead", hits, open_by_rule, "member alive")


def _running(conv: Conversation) -> bool:
    from omnigent.server.routes._sessions.helpers import _session_status_from_cache

    return _session_status_from_cache(conv.id, conv.live_status) == "running"


async def _repeat_cleared(
    app_state: Any,
    _factory_deps: FactoryDeps,
    fid: str,
    members: Sequence[Conversation],
    children: Sequence[Conversation],
    now: float,
    open_by_rule: OpenByRule,
) -> None:
    """
    Resolve ``agent.repeat`` the poller can no longer clear: the agent closed,
    or nothing re-raised it for a whole window (no one watching, a restart).
    """
    rows = open_by_rule.get("agent.repeat", [])
    if not rows:
        return
    alive = {_member(c.id) for c in (*members, *children)}
    window_s = threshold(fid, "repeat_window_m") * 60
    for row in rows:
        if row.target not in alive:
            await clear(app_state, fid, "agent.repeat", row.target, "agent closed")
        elif now - row.last_seen >= window_s:
            await clear(app_state, fid, "agent.repeat", row.target, "no repeat in the window")


async def _quiet_cleared(
    app_state: Any,
    _factory_deps: FactoryDeps,
    fid: str,
    members: Sequence[Conversation],
    children: Sequence[Conversation],
    now: float,
    open_by_rule: OpenByRule,
) -> None:
    rows = open_by_rule.get("agent.quiet", [])
    if not rows:
        return
    by_target = {_member(c.id): c for c in (*members, *children)}
    quiet_s = threshold(fid, "quiet_m") * 60
    for row in rows:
        conv = by_target.get(row.target)
        if conv is None or not _running(conv) or now - conv.updated_at < quiet_s:
            await clear(app_state, fid, "agent.quiet", row.target, "activity resumed")


async def _loops_cleared(
    app_state: Any,
    deps: FactoryDeps,
    fid: str,
    _members: Sequence[Conversation],
    _children: Sequence[Conversation],
    _now: float,
    open_by_rule: OpenByRule,
) -> None:
    for rule in LOOP_BAD:
        for row in open_by_rule.get(rule, []):
            if not row.target.startswith("loop:"):
                continue
            try:
                loop = await asyncio.to_thread(deps.factory_store.get_loop, row.target[5:])
            except ValueError:
                loop = None
            if loop is None or loop.state not in _LOOP_STILL_BAD:
                await clear(
                    app_state, fid, rule, row.target, f"loop {loop.state if loop else 'gone'}"
                )


async def _spend(
    app_state: Any,
    deps: FactoryDeps,
    fid: str,
    members: Sequence[Conversation],
    _children: Sequence[Conversation],
    now: float,
    open_by_rule: OpenByRule,
) -> None:
    from omnigent.server.factory.snapshot import subtree_cost

    store = deps.conversation_store
    costs = await asyncio.to_thread(lambda: {m.id: subtree_cost(m, store) for m in members})
    scopes: dict[str, float] = {}
    priced = [c for c in costs.values() if c is not None]
    if priced:
        scopes["factory"] = sum(priced)
    for member in members:
        slug = member.labels.get(LABEL_PROJECT)
        cost = costs.get(member.id)
        if slug and cost is not None:
            scopes[f"project:{slug}"] = scopes.get(f"project:{slug}", 0.0) + cost
    window_s = threshold(fid, "spend_window_m") * 60
    limit = threshold(fid, "usd_per_hour_alert")
    key = norm_id(fid)
    hits: dict[str, tuple[str, str | None, Any]] = {}
    for scope, cost in scopes.items():
        sampler = _samplers.setdefault((key, scope), _Sampler(deque()))
        rate = sampler.rate(now, cost, window_s)
        if scope == "factory":
            _rates[key] = rate
        if rate is not None and rate > limit:
            project = scope.removeprefix("project:") if scope != "factory" else None
            what = "the factory" if project is None else f"project {project}"
            hits[scope] = (
                f"{what} spends ${rate:.2f}/h (alert above ${limit:.0f}/h)",
                project,
                {"usd_per_h": rate, "limit": limit},
            )
    await _sync(
        app_state, fid, "spend.rate", hits, open_by_rule, "spend rate back under the limit"
    )


async def _ready_unreviewed(
    app_state: Any,
    deps: FactoryDeps,
    fid: str,
    _members: Sequence[Conversation],
    _children: Sequence[Conversation],
    now: float,
    open_by_rule: OpenByRule,
) -> None:
    after_s = threshold(fid, "ready_unreviewed_h") * 3600
    prs = await asyncio.to_thread(deps.factory_store.list_prs, fid, ["open"])
    hits: dict[str, tuple[str, str | None, Any]] = {}
    for pr in prs:
        ready_at = pr.ready_at
        review_at = (pr.human_review or {}).get("at") or 0
        if pr.ready and ready_at and now - ready_at >= after_s and review_at < ready_at:
            hours = (now - ready_at) / 3600
            hits[f"pr:{pr.id}"] = (
                f"PR #{pr.number} ready for {hours:.1f} h with no human review",
                pr.project_slug,
                {"pr_id": pr.id, "ready_at": ready_at},
            )
    await _sync(
        app_state, fid, "pr.ready_unreviewed", hits, open_by_rule, "reviewed or no longer ready"
    )


async def _decisions_waiting(
    app_state: Any,
    deps: FactoryDeps,
    fid: str,
    _members: Sequence[Conversation],
    _children: Sequence[Conversation],
    now: float,
    open_by_rule: OpenByRule,
) -> None:
    after_s = threshold(fid, "decision_waiting_m") * 60
    rows = await asyncio.to_thread(
        lambda: deps.factory_store.list_decisions(factory_id=fid, status="open", limit=500)
    )
    hits: dict[str, tuple[str, str | None, Any]] = {}
    for d in rows:
        if d.blocking and now - d.created_at >= after_s:
            minutes = int((now - d.created_at) // 60)
            hits[f"decision:{d.id}"] = (
                f"D-{d.number} waiting {minutes} min: {d.question[:120]}",
                d.project_slug,
                {"decision_id": d.id, "assignee": d.assignee},
            )
    await _sync(app_state, fid, "decision.waiting", hits, open_by_rule, "answered or closed")


async def _previews_gone(
    app_state: Any,
    deps: FactoryDeps,
    fid: str,
    _members: Sequence[Conversation],
    _children: Sequence[Conversation],
    _now: float,
    open_by_rule: OpenByRule,
) -> None:
    from omnigent.server.factory.previews import preview_name, preview_state

    prs = await asyncio.to_thread(deps.factory_store.list_prs, fid, ["open"])
    hits: dict[str, tuple[str, str | None, Any]] = {}
    for pr in prs:
        state = preview_state(pr.preview)
        if pr.ready and state in _PREVIEW_GONE:
            hits[f"pr:{pr.id}"] = (
                f"preview {preview_name(pr)} of ready PR #{pr.number} is {state}",
                pr.project_slug,
                {"pr_id": pr.id, "state": state},
            )
    await _sync(
        app_state, fid, "preview.gone_ready", hits, open_by_rule, "preview back or PR not ready"
    )


async def _mail_stuck(
    app_state: Any,
    deps: FactoryDeps,
    fid: str,
    _members: Sequence[Conversation],
    _children: Sequence[Conversation],
    now: float,
    open_by_rule: OpenByRule,
) -> None:
    from omnigent.server.factory.mailer import get_mailer

    after_s = threshold(fid, "mail_stuck_m") * 60
    since = int((now - 6 * 3600) * 1000)
    rows = await asyncio.to_thread(
        deps.factory_store.find_events,
        fid,
        ["message.queued", "message.delivered", "message.bounced"],
        since,
        limit=5000,
    )
    closed = {e.subject for e in rows if e.kind != "message.queued"}
    mailer = get_mailer(app_state)
    stuck: dict[str, list[Any]] = {}
    for e in rows:
        mail_id = (e.data or {}).get("mail_id")
        if (
            e.kind == "message.queued"
            and e.to_id
            and e.subject not in closed
            and now - e.ts / 1000 >= after_s
            and isinstance(mail_id, str)
            and mailer.is_queued(mail_id)
        ):
            stuck.setdefault(e.to_id, []).append(e)
    hits: dict[str, tuple[str, str | None, Any]] = {}
    for to_id, queued in stuck.items():
        oldest = int((now - min(e.ts for e in queued) / 1000) // 60)
        hits[_member(to_id)] = (
            f"{len(queued)} message(s) queued for {oldest} min, not delivered",
            queued[0].project_slug,
            {"mail_ids": [(e.data or {}).get("mail_id") for e in queued][:10]},
        )
    await _sync(app_state, fid, "mail.stuck", hits, open_by_rule, "delivered")


async def _bus(
    app_state: Any,
    deps: FactoryDeps,
    fid: str,
    _members: Sequence[Conversation],
    _children: Sequence[Conversation],
    now: float,
    open_by_rule: OpenByRule,
) -> None:
    pp_window = threshold(fid, "pingpong_window_m") * 60
    orphan_s = threshold(fid, "orphan_h") * 3600
    since = int((now - max(pp_window, ORPHAN_LOOKBACK_S)) * 1000)
    rows = await asyncio.to_thread(
        deps.factory_store.find_events,
        fid,
        ["message.queued", "stage.changed", "task.state"],
        since,
        limit=5000,
    )
    mail = [e for e in rows if e.kind == "message.queued" and e.from_id and e.to_id]
    progress = [e for e in rows if e.kind != "message.queued"]
    pp_since = (now - pp_window) * 1000
    pairs: dict[tuple[str, str], list[Any]] = {}
    for e in mail:
        if e.ts >= pp_since:
            a, b = sorted((e.from_id or "", e.to_id or ""))
            pairs.setdefault((a, b), []).append(e)
    pp_hits: dict[str, tuple[str, str | None, Any]] = {}
    for (a, b), msgs in pairs.items():
        projects = {m.project_slug for m in msgs}
        moved = any(p.ts >= pp_since and p.project_slug in projects for p in progress)
        if len(msgs) > threshold(fid, "pingpong_n") and not moved:
            pp_hits[f"bus:{norm_id(a)}~{norm_id(b)}"] = (
                f"{len(msgs)} messages back and forth in {int(pp_window // 60)} min "
                "with no stage or task change",
                msgs[-1].project_slug,
                {"count": len(msgs), "sessions": [a, b]},
            )
    await _sync(app_state, fid, "bus.pingpong", pp_hits, open_by_rule, "the exchange settled")
    # The newest message on each (from, to) leg answers every older one the other way.
    newest: dict[tuple[str | None, str | None], int] = {}
    for r in mail:
        leg = (r.from_id, r.to_id)
        newest[leg] = max(newest.get(leg, r.ts), r.ts)
    orphan_hits: dict[str, tuple[str, str | None, Any]] = {}
    for e in mail:
        if (e.data or {}).get("kind") != "request" or now - e.ts / 1000 < orphan_s:
            continue
        answered = newest.get((e.to_id, e.from_id), e.ts) > e.ts
        if not answered:
            key = f"bus:{norm_id(e.from_id or '')}>{norm_id(e.to_id or '')}"
            hours = (now - e.ts / 1000) / 3600
            orphan_hits[key] = (
                f"request unanswered for {hours:.1f} h: {e.summary[:120]}",
                e.project_slug,
                {"event_id": e.id, "mail_id": (e.data or {}).get("mail_id")},
            )
    await _sync(app_state, fid, "bus.orphan", orphan_hits, open_by_rule, "answered")


async def _gate_flapping(
    app_state: Any,
    deps: FactoryDeps,
    fid: str,
    _members: Sequence[Conversation],
    _children: Sequence[Conversation],
    now: float,
    open_by_rule: OpenByRule,
) -> None:
    window_s = threshold(fid, "gate_flap_window_h") * 3600
    rows = await asyncio.to_thread(
        deps.factory_store.find_events,
        fid,
        ["pr.ready", "pr.not_ready"],
        int((now - window_s) * 1000),
    )
    flips: dict[str, list[Any]] = {}
    for e in rows:
        pr_id = (e.data or {}).get("pr_id")
        if isinstance(pr_id, str):
            flips.setdefault(pr_id, []).append(e)
    hits: dict[str, tuple[str, str | None, Any]] = {}
    for pr_id, evs in flips.items():
        if len(evs) >= threshold(fid, "gate_flaps"):
            hits[f"pr:{pr_id}"] = (
                f"{evs[-1].subject or 'PR'} flipped ready ↔ not ready {len(evs)}× in "
                f"{int(window_s // 3600)} h",
                evs[-1].project_slug,
                {"events": [e.id for e in evs][:10]},
            )
    await _sync(app_state, fid, "gate.flapping", hits, open_by_rule, "gates settled")


async def _heartbeat_waste(
    app_state: Any,
    deps: FactoryDeps,
    fid: str,
    members: Sequence[Conversation],
    _children: Sequence[Conversation],
    now: float,
    open_by_rule: OpenByRule,
) -> None:
    from omnigent.server.factory.activity_now import own_cost

    day = _utc_day(now)
    for conv in members:
        beat = _open_beats.get(conv.id)
        if beat is None or _running(conv):
            continue
        del _open_beats[conv.id]
        cost = own_cost(conv)
        if cost is None:
            continue
        found = await asyncio.to_thread(
            deps.factory_store.list_events, fid, None, 50, None, None, [conv.id]
        )
        changed = any(
            e.ts > beat[0] and e.from_id == conv.id and not e.kind.startswith(_STATE_EVENT_SKIP)
            for e in found
        )
        if not changed:
            _waste[(conv.id, day)] = _waste.get((conv.id, day), 0.0) + max(0.0, cost - beat[1])
    limit = threshold(fid, "heartbeat_waste_usd")
    hits: dict[str, tuple[str, str | None, Any]] = {}
    for conv in members:
        wasted = _waste.get((conv.id, day), 0.0)
        if wasted > limit:
            hits[_member(conv.id)] = (
                f"{_title(conv)} spent ${wasted:.2f} today on heartbeats that changed nothing",
                conv.labels.get(LABEL_PROJECT),
                {"usd": round(wasted, 2), "day": day},
            )
    for key in [k for k in _waste if k[1] != day]:
        del _waste[key]
    await _sync(app_state, fid, "heartbeat.waste", hits, open_by_rule, "a new day")


def install() -> Callable[[], None]:
    """Run the event-listener rules after every stored event. :returns: The uninstaller."""
    return events.add_listener(on_event)


def is_open(issue: FactoryIssue) -> bool:
    """:returns: Whether *issue* still needs someone."""
    return issue.status in ISSUE_OPEN_STATUSES


__all__ = [
    "DEFAULT_THRESHOLDS",
    "LOOP_BAD",
    "LOOP_GOOD",
    "RULES",
    "Rule",
    "agent_quiet",
    "agent_repeat",
    "clear",
    "install",
    "member_down",
    "on_event",
    "on_tick",
    "reset",
    "runbook",
    "set_threshold_resolver",
    "spend_rate",
    "threshold",
    "tick_factory",
]
