"""The PR pipeline as a graph: NEXT hints and READY waves wake project leads on events.

:func:`next_step` is a pure function over the edges
``implement → ci → review → verify → demo → sentinel → ready → human``: it
returns what a PR needs next (``FIX_CI``, ``REVIEW``, ``PREVIEW``, ``DEMO``,
``ASK_SENTINEL``, ``HANDOVER``, ``WAIT_HUMAN`` or ``NONE``). When the step of a
head changes, the engine (``assist`` and ``enforce``) mails the PL a hint::

    [Factory engine · NEXT · PR #9631 @5e1c2d4 · review APPROVE → PREVIEW pr=9631
    head=<sha> base=temp-main]

Hints wait a short grace (the PL may act on the same news in its own turn),
are re-checked before they go, are coalesced to one mail per PL per
``engine.wake_coalesce_s`` (120 s), and the same step is never sent twice
for the same head. ``FIX_CI``, ``HANDOVER`` and ``ASK_SENTINEL`` are not mailed:
the PR poller's notices already carry them. READY waves tell a PL with startable tasks and free IC
slots to fetch its cards::

    [Factory engine · READY · db-version · wave 2 · T-14 db-row, T-16 docs] call
    sys_factory_task(op="ready") for the cards

Every mail emits ``engine.next`` or ``engine.ready_wave``; the share of PL
wakes they replace (vs ``heartbeat.delivered``) is the engine's win.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.entities.factory import FactoryLoop, FactoryPr
from omnigent.factory import LABEL_ID, LABEL_STATE, STATE_DONE, STATE_PAUSED
from omnigent.server.factory import control, events, tasks
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryEvent

_logger = logging.getLogger(__name__)

FIX_CI: Final = "FIX_CI"
REVIEW: Final = "REVIEW"
PREVIEW: Final = "PREVIEW"
DEMO: Final = "DEMO"
ASK_SENTINEL: Final = "ASK_SENTINEL"
HANDOVER: Final = "HANDOVER"
WAIT_HUMAN: Final = "WAIT_HUMAN"
NONE: Final = "NONE"
STEPS: Final = (FIX_CI, REVIEW, PREVIEW, DEMO, ASK_SENTINEL, HANDOVER, WAIT_HUMAN, NONE)
MAILED: Final = frozenset({REVIEW, PREVIEW, DEMO})
"""
Steps the PL is mailed. ``FIX_CI`` and ``HANDOVER`` already reach it as the
PR poller's ``checks failed`` and ``READY FOR HUMAN MERGE`` notices, and the
lead gets the ready notice (``ASK_SENTINEL``).
"""

WAKE_COALESCE_S: Final = 120.0
"""At most one NEXT mail per PL in this window (``engine.wake_coalesce_s``)."""
HINT_GRACE_S: Final = 30.0
"""A hint waits this long, then is re-checked: the PL may act on the same news itself."""
READY_GRACE_S: Final = 90.0
READY_REPEAT_S: Final = 1800.0
"""The same READY wave is not repeated within this window."""
_BAD: Final = frozenset({"exhausted", "stalled", "thrashing"})
_HUMAN_LOOPS: Final = frozenset({"implement", "review", "demo", "claim"})
_LIVE_PREVIEW: Final = frozenset({"deploying", "starting"})
_OBSERVED: Final = frozenset(
    {
        "gate.changed",
        "pr.ready",
        "pr.not_ready",
        "pr.human_review",
        "pr.claim",
        "preview.reported",
        "preview.parked",
        "demo.published",
        "task.state",
    }
)


# ── The pure step function ─────────────────────────────────────


@dataclass(frozen=True)
class Step:
    """
    What a PR needs next.

    :param name: One of :data:`STEPS`.
    :param cause: What made it the next step, e.g. ``"review APPROVE"``.
    :param args: The step's arguments, e.g. ``{"checks": "lint, test"}``.
    """

    name: str
    head: str = ""
    cause: str = ""
    args: Mapping[str, Any] = field(default_factory=dict)


def _sha_matches(full: str, value: Any) -> bool:
    text = str(value or "").lower()
    return len(text) >= 7 and full.lower().startswith(text)


def _evidence(gates: Mapping[str, Any], gate: str) -> Mapping[str, Any]:
    found = (gates.get("evidence") or {}).get(gate) if isinstance(gates, Mapping) else None
    return found if isinstance(found, Mapping) else {}


def next_step(
    pr: FactoryPr,
    gates: Mapping[str, Any],
    loops: Sequence[FactoryLoop],
    preview: Mapping[str, Any] | None,
    demos: Sequence[Mapping[str, Any]],
    sentinel: Mapping[str, Any] | None,
) -> Step:
    """
    The next step of a PR's pipeline (pure).

    :param pr: The PR row (state, draft, head, number, url, ready).
    :param gates: Its gates JSON (``pass``/``pending``/``fail``/``skip`` + evidence).
    :param loops: Its loops (and its task's implement loop).
    :param preview: Its factory preview (``state``, ``deployed.sha``, ``urls``).
    :param demos: Its recorded demos as ``{head_sha, status}``.
    :param sentinel: Sentinel's verdict ``{verdict, head_sha}``.
    """
    head = (pr.head_sha or "").lower()
    if pr.state != "open" or not head:
        return Step(NONE, head, "the PR is not open")
    for loop in loops:
        if loop.kind in _HUMAN_LOOPS and loop.state in _BAD:
            return Step(WAIT_HUMAN, head, f"{loop.kind} {loop.state}")
    if pr.is_draft or gates.get("draft") == "pending":
        return Step(NONE, head, "a draft: its author is still coding")
    checks = gates.get("checks")
    if checks == "fail":
        names = list(_evidence(gates, "checks").get("failing_names") or [])[:6]
        return Step(FIX_CI, head, "CI failed", {"checks": ", ".join(map(str, names)) or "?"})
    if checks != "pass":
        return Step(NONE, head, "CI is running")
    review = gates.get("review")
    if review == "pending":
        return Step(REVIEW, head, "CI green")
    if review != "pass":
        return Step(NONE, head, "review CHANGES: the author reworks")
    state = str((preview or {}).get("state") or "")
    deployed = ((preview or {}).get("deployed") or {}).get("sha")
    at_head = _sha_matches(head, deployed)
    ready_at_head = state == "ready" and at_head
    verify = gates.get("verify")
    if verify == "pending":
        if (
            ready_at_head
            or (at_head and state in _LIVE_PREVIEW)
            or (at_head and state == "failed")
        ):
            return Step(NONE, head, "env_keeper deploys and verifies the head")
        return Step(PREVIEW, head, "review APPROVE", {"pr": pr.number})
    if verify == "fail":
        return Step(NONE, head, "verify FAIL: the author fixes the head")
    demo = gates.get("demo")
    if demo == "pending":
        recorded = any(
            _sha_matches(head, d.get("head_sha")) and str(d.get("status")).upper() == "ERROR"
            for d in demos
        )
        if not ready_at_head:
            if at_head and state in _LIVE_PREVIEW:
                return Step(NONE, head, "the preview is deploying")
            return Step(
                PREVIEW, head, "verify PASS · the preview is not at the head", {"pr": pr.number}
            )
        urls = (preview or {}).get("urls") or {}
        cause = (
            "demo ERROR" if recorded else "verify PASS" if verify == "pass" else "review APPROVE"
        )
        return Step(
            DEMO,
            head,
            cause,
            {"pr": pr.url, "app": urls.get("app") or "?", "api": urls.get("api") or "?"},
        )
    if demo == "fail":
        return Step(NONE, head, "demo FAIL: the author fixes the head")
    if not pr.ready:
        return Step(NONE, head, "a gate is pending or failing")
    if (
        sentinel
        and sentinel.get("verdict") == "verified"
        and _sha_matches(head, sentinel.get("head_sha"))
    ):
        return Step(WAIT_HUMAN, head, "Sentinel verified: a human merges")
    claimed = any(
        loop.kind == "claim" and _sha_matches(head, (loop.spent or {}).get("claimed_head"))
        for loop in loops
    )
    if claimed:
        return Step(ASK_SENTINEL, head, "claimed READY")
    return Step(HANDOVER, head, "all gates pass")


# ── Texts ──────────────────────────────────────────────────────


def hint_text(pr: FactoryPr, step: Step, *, base: str) -> str:
    """
    One NEXT hint line.

    e.g. ``[Factory engine · NEXT · PR #9631 @5e1c2d4 · CI green → REVIEW head=<sha>]``.

    :param base: The project's base branch.
    """
    head = step.head
    action = step.name
    if step.name == FIX_CI:
        action += f" checks={step.args.get('checks')}"
    elif step.name in (REVIEW, HANDOVER):
        action += f" head={head}"
    elif step.name == PREVIEW:
        action += f" pr={pr.number} head={head} base={base}"
    elif step.name == DEMO:
        action += (
            f" pr={step.args.get('pr')} sha={head} app={step.args.get('app')} "
            f"api={step.args.get('api')}"
        )
    return f"[Factory engine · NEXT · PR #{pr.number} @{head[:7]} · {step.cause} → {action}]"


def ready_text(slug: str, wave: int, items: Sequence[tuple[str, str]]) -> str:
    """
    The READY wave mail.

    :param items: ``(T-n, key)`` of the startable tasks.
    """
    names = ", ".join(f"{ref} {key}" for ref, key in items)
    return (
        f"[Factory engine · READY · {slug} · wave {wave} · {names}] "
        'call sys_factory_task(op="ready") for the cards'
    )


# ── The engine: hints, coalescing, dedupe ──────────────────────


@dataclass
class Hint:
    """A queued NEXT hint for one PR head."""

    pr_id: str
    number: int
    head: str
    step: str
    text: str
    queued_at: float


@dataclass
class _State:
    """The in-process engine state (single replica)."""

    sent: dict[tuple[str, str, str], float] = field(default_factory=dict)
    queue: dict[str, dict[str, Hint]] = field(default_factory=dict)
    last_mail: dict[str, float] = field(default_factory=dict)
    ready_due: dict[tuple[str, str], float] = field(default_factory=dict)
    ready_sent: dict[str, tuple[float, tuple[str, ...]]] = field(default_factory=dict)
    timer: asyncio.TimerHandle | None = None
    timer_at: float | None = None


_state = _State()
_flushes: set[asyncio.Task[int]] = set()
auto_flush = True
"""Schedule flushes on the event loop (tests drive :func:`flush_due` themselves)."""
_SENT_MAX: Final = 20000


def _deps(app_state: Any) -> FactoryDeps | None:
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    return deps if isinstance(deps, FactoryDeps) else None


@dataclass
class PrFacts:
    """What :func:`next_step` reads for one PR, plus where its hints go."""

    pr: FactoryPr
    loops: list[FactoryLoop]
    demos: list[dict[str, Any]]
    pl_id: str | None
    base: str
    busy: frozenset[str] = frozenset()
    """Work the PR's task has a worker running for now: ``review`` / ``demo``."""


def read_pr(deps: FactoryDeps, factory_id: str, pr_id: str) -> PrFacts | None:
    """Load a PR's step inputs (synchronous); ``None`` for an unknown or foreign PR."""
    from omnigent.server.factory.charter import project_base_branch

    store = deps.factory_store
    try:
        pr = store.get_pr(pr_id)
    except ValueError:
        return None
    if pr is None or norm_id(pr.factory_id) != norm_id(factory_id):
        return None
    loops = store.list_loops(factory_id, pr_id=pr.id)
    if pr.task_id:
        loops += [
            loop
            for loop in store.list_loops(factory_id, kinds=["implement"], task_id=pr.task_id)
            if loop.pr_id is None or norm_id(loop.pr_id) != norm_id(pr.id)
        ]
    demos = [
        {"head_sha": d.head_sha, "status": d.status, "created_at": d.created_at}
        for d in store.list_demos(factory_id, pr.number, repo=pr.repo, limit=20)
    ]
    project = store.get_project(factory_id, pr.project_slug) if pr.project_slug else None
    pl = control.find_project_pl(deps, factory_id, pr.project_slug)
    base = project_base_branch(project) if project is not None else (pr.base_ref or "main")
    return PrFacts(
        pr=pr,
        loops=loops,
        demos=demos,
        pl_id=pl.id if pl else None,
        base=base,
        busy=_busy(deps, pr),
    )


def _busy(deps: FactoryDeps, pr: FactoryPr) -> frozenset[str]:
    """``review`` / ``demo`` when a worker of that kind runs for the PR's task now."""
    from omnigent.server.factory.binder import task_title
    from omnigent.server.routes._sessions.helpers import _session_status_from_cache

    task = deps.factory_store.get_task(pr.task_id) if pr.task_id else None
    found: set[str] = set()
    for session_id in (task.session_ids if task is not None else None) or []:
        conv = deps.conversation_store.get_conversation(session_id)
        if conv is None or _session_status_from_cache(conv.id, conv.live_status) != "running":
            continue
        title = task_title(conv.title, conv.sub_agent_name)
        for kind in ("review", "demo"):
            if title.startswith(f"{kind}-"):
                found.add(kind)
    return frozenset(found)


_BUSY_STEP: Final = {REVIEW: "review", DEMO: "demo"}


def step_of(facts: PrFacts) -> Step:
    """:returns: :func:`next_step` over the loaded facts (a step already running is ``NONE``)."""
    pr = facts.pr
    step = next_step(pr, pr.gates or {}, facts.loops, pr.preview, facts.demos, pr.sentinel)
    if _BUSY_STEP.get(step.name) in facts.busy:
        return Step(NONE, step.head, f"a {_BUSY_STEP[step.name]} is running")
    return step


def offer(facts: PrFacts, step: Step, now: float) -> Hint | None:
    """
    Queue a hint for the PR's PL unless that step was already sent for that head.

    :returns: The queued hint, or ``None`` when deduped or not mailed.
    """
    if step.name not in MAILED or facts.pl_id is None:
        return None
    pr = facts.pr
    key = (norm_id(pr.id), step.head, step.name)
    if key in _state.sent:
        return None
    hint = Hint(
        pr_id=norm_id(pr.id),
        number=pr.number,
        head=step.head,
        step=step.name,
        text=hint_text(pr, step, base=facts.base),
        queued_at=now,
    )
    queued = _state.queue.setdefault(norm_id(facts.pl_id), {})
    existing = queued.get(hint.pr_id)
    if existing is not None and (existing.head, existing.step) == (hint.head, hint.step):
        return existing
    queued[hint.pr_id] = hint
    return hint


def due_at(pl_id: str) -> float | None:
    """:returns: When the PL's queued hints may go (grace and coalescing), or ``None``."""
    queued = _state.queue.get(norm_id(pl_id))
    if not queued:
        return None
    first = min(h.queued_at for h in queued.values()) + HINT_GRACE_S
    last = _state.last_mail.get(norm_id(pl_id))
    return max(first, last + WAKE_COALESCE_S) if last is not None else first


async def flush_due(app_state: Any, now: float | None = None) -> int:
    """
    Send every PL's due hints as one mail each (re-checked first). Never raises.

    :returns: Mails sent.
    """
    deps = _deps(app_state)
    if deps is None:
        return 0
    at = time.time() if now is None else now
    sent = 0
    for pl_id in list(_state.queue):
        when = due_at(pl_id)
        if when is None or when > at:
            continue
        hints = list(_state.queue.pop(pl_id, {}).values())
        try:
            sent += await _send_hints(app_state, deps, pl_id, hints, at)
        except Exception:  # noqa: BLE001 - a lost hint is a heartbeat's job
            _logger.warning("factory engine: could not send NEXT to %s", pl_id, exc_info=True)
    await _ready_due(app_state, deps, at)
    _schedule(app_state)
    return sent


def _held(deps: FactoryDeps, pl_id: str) -> str | None:
    """
    The PL's factory id, or ``None`` when its hints must not go (synchronous).

    A paused or done PL, or a paused factory, gets no NEXT: a hint would wake
    it and tell it to dispatch, undoing the pause. Its hints are dropped; the
    next gate change after the resume recomputes them.
    """
    store = deps.conversation_store
    conv = store.get_conversation(pl_id)
    fid = conv.labels.get(LABEL_ID) if conv is not None else None
    if conv is None or not fid or conv.labels.get(LABEL_STATE) in (STATE_PAUSED, STATE_DONE):
        return None
    root = store.get_conversation(fid)
    if root is not None and root.labels.get(LABEL_STATE) == STATE_PAUSED:
        return None
    return fid


async def _send_hints(
    app_state: Any, deps: FactoryDeps, pl_id: str, hints: list[Hint], now: float
) -> int:
    fid = await asyncio.to_thread(_held, deps, pl_id)
    if fid is None:
        return 0
    live: list[tuple[Hint, PrFacts]] = []
    for hint in hints:
        facts = await asyncio.to_thread(read_pr, deps, fid, hint.pr_id)
        if facts is None:
            continue
        step = step_of(facts)
        # Re-checked: the PL may already have taken the step itself.
        if (step.head, step.name) != (hint.head, hint.step):
            continue
        live.append((hint, facts))
    if not live:
        return 0
    fid = norm_id(live[0][1].pr.factory_id)
    mode = await asyncio.to_thread(tasks.engine_mode, deps, fid)
    if mode == "observe":
        return 0
    text = "\n".join(h.text for h, _ in live)
    mail_id = control.send_text(app_state, pl_id, text, kind="info")
    _state.last_mail[norm_id(pl_id)] = now
    for hint, _ in live:
        _state.sent[(hint.pr_id, hint.head, hint.step)] = now
    if len(_state.sent) > _SENT_MAX:
        for key in list(_state.sent)[: _SENT_MAX // 2]:
            _state.sent.pop(key, None)
    first = live[0][1].pr
    await events.emit(
        app_state,
        fid,
        "engine.next",
        project=first.project_slug,
        to_id=pl_id,
        subject=f"pr:{first.repo}#{first.number}",
        summary=" · ".join(f"PR #{h.number} → {h.step}" for h, _ in live),
        data={
            "mail_id": mail_id,
            "mode": mode,
            "coalesced": len(live),
            "hints": [
                {"pr_id": h.pr_id, "number": h.number, "head_sha": h.head, "step": h.step}
                for h, _ in live
            ],
            "by": "server",
            "by_kind": "server",
        },
        ts=int(now * 1000),
    )
    return 1


async def consider_pr(
    app_state: Any, factory_id: str, pr_id: str, now: float | None = None
) -> Step | None:
    """Recompute one PR's step; queue a hint when it is new. Never raises."""
    deps = _deps(app_state)
    if deps is None:
        return None
    try:
        facts = await asyncio.to_thread(read_pr, deps, factory_id, pr_id)
        if facts is None:
            return None
        step = step_of(facts)
        offer(facts, step, time.time() if now is None else now)
        _schedule(app_state)
        return step
    except Exception:  # noqa: BLE001 - the next event recomputes it
        _logger.warning("factory engine: no step for %s", pr_id, exc_info=True)
        return None


# ── READY waves ────────────────────────────────────────────────


def queue_ready(factory_id: str, slug: str, now: float) -> None:
    """Check *slug*'s ready wave after the grace (the PL may be dispatching it now)."""
    key = (norm_id(factory_id), slug)
    _state.ready_due.setdefault(key, now + READY_GRACE_S)


def ready_wave(
    deps: FactoryDeps, factory_id: str, slug: str
) -> tuple[int, list[tuple[str, str]]] | None:
    """
    The wave a PL could dispatch now (synchronous).

    :returns: ``(wave number, [(T-n, key)])``, or ``None`` when nothing is
        startable or no IC slot is free.
    """
    ctx = tasks.load_ctx(deps, factory_id, slug)
    if ctx.paused or not ctx.active or ctx.slots.ics_free <= 0:
        return None
    analysis = tasks.analyze(ctx)
    wave = list(analysis.plan.now)
    if not wave:
        return None
    by_key = ctx.by_key()
    number = min(analysis.layers.get(k, 1) for k in wave)
    return number, [(by_key[k].ref, k) for k in wave if k in by_key]


async def _ready_due(app_state: Any, deps: FactoryDeps, now: float) -> None:
    for key, when in list(_state.ready_due.items()):
        # A concurrent flush (the timer and the tick) may have taken it already.
        if when > now or _state.ready_due.pop(key, None) is None:
            continue
        factory_id, slug = key
        try:
            await _send_ready(app_state, deps, factory_id, slug, now)
        except Exception:  # noqa: BLE001 - the next task change re-checks it
            _logger.warning("factory engine: no READY for %s", slug, exc_info=True)


def _newest_change(deps: FactoryDeps, factory_id: str, slug: str, keys: list[str]) -> float:
    rows = deps.factory_store.list_tasks(factory_id, project_slug=slug)
    return float(max((t.updated_at for t in rows if t.key in keys), default=0))


async def _send_ready(
    app_state: Any, deps: FactoryDeps, factory_id: str, slug: str, now: float
) -> bool:
    mode = await asyncio.to_thread(tasks.engine_mode, deps, factory_id)
    if mode == "observe":
        return False
    found = await asyncio.to_thread(ready_wave, deps, factory_id, slug)
    if found is None:
        return False
    pl = await asyncio.to_thread(control.find_project_pl, deps, factory_id, slug)
    if pl is None:
        return False
    number, items = found
    newest = await asyncio.to_thread(_newest_change, deps, factory_id, slug, [k for _, k in items])
    if newest > now - READY_GRACE_S:
        # A task just became ready: the PL may be dispatching it in this very turn.
        _state.ready_due.setdefault((norm_id(factory_id), slug), newest + READY_GRACE_S)
        return False
    keys = tuple(sorted(k for _, k in items))
    last = _state.ready_sent.get(norm_id(pl.id))
    if last is not None:
        at, previous = last
        if now - at < WAKE_COALESCE_S or (previous == keys and now - at < READY_REPEAT_S):
            return False
    text = ready_text(slug, number, items)
    mail_id = control.send_text(app_state, pl.id, text, kind="info")
    _state.ready_sent[norm_id(pl.id)] = (now, keys)
    await events.emit(
        app_state,
        factory_id,
        "engine.ready_wave",
        project=slug,
        to_id=pl.id,
        subject=f"project:{slug}",
        summary=f"{slug} wave {number}: {', '.join(f'{r} {k}' for r, k in items)}",
        data={
            "mail_id": mail_id,
            "mode": mode,
            "wave": number,
            "tasks": [k for _, k in items],
            "by": "server",
            "by_kind": "server",
        },
        ts=int(now * 1000),
    )
    return True


# ── Drivers ────────────────────────────────────────────────────


def _schedule(app_state: Any) -> None:
    """Wake :func:`flush_due` at the earliest due hint or READY check."""
    if not auto_flush:
        return
    dues = [w for pl in list(_state.queue) if (w := due_at(pl)) is not None]
    dues += list(_state.ready_due.values())
    if not dues:
        return
    at = min(dues)
    if _state.timer is not None and _state.timer_at is not None and _state.timer_at <= at:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _state.timer is not None:
        _state.timer.cancel()
    delay = max(0.0, at - time.time())

    def _fire() -> None:
        _state.timer, _state.timer_at = None, None
        task = loop.create_task(flush_due(app_state))
        _flushes.add(task)
        task.add_done_callback(_flushes.discard)

    _state.timer = loop.call_later(delay, _fire)
    _state.timer_at = at


async def on_event(app_state: Any, event: FactoryEvent) -> None:
    """The ``events.add_listener`` hook: PR facts recompute steps; task moves queue READY."""
    if event.kind not in _OBSERVED:
        return
    data = event.data if isinstance(event.data, dict) else {}
    try:
        if event.kind == "task.state" and event.project_slug:
            queue_ready(event.factory_id, event.project_slug, time.time())
            _schedule(app_state)
            return
        pr_id = data.get("pr_id")
        if not isinstance(pr_id, str) or not pr_id:
            from omnigent.server.factory.loops import pr_from_subject

            deps = _deps(app_state)
            found = (
                await asyncio.to_thread(
                    pr_from_subject, deps.factory_store, event.factory_id, event.subject
                )
                if deps is not None
                else None
            )
            pr_id = found.id if found is not None else None
        if pr_id:
            await consider_pr(app_state, norm_id(event.factory_id), pr_id)
    except Exception:  # noqa: BLE001 - the event log swallows it too
        _logger.warning("factory engine: could not handle %s", event.kind, exc_info=True)


async def on_loops(app_state: Any, factory_id: str, changed: Sequence[FactoryLoop]) -> None:
    """Loops changed: recompute the steps of their PRs (a bad loop means WAIT_HUMAN)."""
    for pr_id in sorted({norm_id(loop.pr_id) for loop in changed if loop.pr_id}):
        await consider_pr(app_state, factory_id, pr_id)


async def on_tick(app_state: Any, factory_id: str, *, now: float | None = None) -> None:
    """The loop engine's tick: queue a READY check per project and flush what is due."""
    deps = _deps(app_state)
    if deps is None:
        return
    at = time.time() if now is None else now
    rows = await asyncio.to_thread(
        deps.factory_store.list_tasks, factory_id, states=["planned", "ready"]
    )
    for slug in sorted({t.project_slug for t in rows}):
        _state.ready_due.setdefault((norm_id(factory_id), slug), at + READY_GRACE_S)
    await flush_due(app_state, at)


def install() -> Callable[[], None]:
    """
    Register :func:`on_event` on the event log.

    :returns: The uninstaller, which also cancels a pending flush timer.
    """
    remove = events.add_listener(on_event)

    def _uninstall() -> None:
        remove()
        reset()

    return _uninstall


def reset() -> None:
    """Forget sent hints, queues and timers (tests)."""
    global _state
    if _state.timer is not None:
        _state.timer.cancel()
    _state = _State()


__all__ = [
    "ASK_SENTINEL",
    "DEMO",
    "FIX_CI",
    "HANDOVER",
    "MAILED",
    "NONE",
    "PREVIEW",
    "REVIEW",
    "STEPS",
    "WAIT_HUMAN",
    "WAKE_COALESCE_S",
    "Hint",
    "PrFacts",
    "Step",
    "consider_pr",
    "due_at",
    "flush_due",
    "hint_text",
    "install",
    "next_step",
    "offer",
    "on_event",
    "on_loops",
    "on_tick",
    "queue_ready",
    "read_pr",
    "ready_text",
    "ready_wave",
    "reset",
    "step_of",
]
