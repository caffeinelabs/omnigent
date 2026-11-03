"""The discovery seat: the server seats Scout while discovery runs for a factory.

:func:`reconcile` matches one factory's Scout to its switches, the server env
``OMNIGENT_FACTORY_DISCOVERY`` and the definition's ``discovery.enabled``:

- **on, no live Scout**: a Scout that was stopped earlier (``state=done``, its
  session still there) is woken with ``state=active`` and a fresh brief, so it
  keeps its handle and its history; otherwise the server creates one, the way
  it creates a PL (:mod:`..spawn`): ``POST /v1/sessions`` in process, as the
  factory's creator with a short-lived runner token, running the built-in
  ``factory-scout`` agent on ``discovery.seat.model`` (the session's model
  override) with the factory's default repo checked out read-only for the
  product profile. A runner token may not create a member labelled with a
  seat role (:mod:`..create_guard`), so the create carries no factory label
  and the server stamps them right after.
- **off, a live Scout**: it is stopped (``state=done``); its idle sandbox is
  released by the launch watch and nothing wakes it until discovery is on again.
- A changed ``discovery.seat.model`` is applied to the live Scout the way a
  person's model switch is (``PATCH /v1/sessions/{id}`` as the creator: the
  row, and the running terminal at once); without a runner client it is
  stamped on the row for its next start. Clearing the setting clears the
  override the server set (a person's own choice stands).
- With discovery off and no seat ever started, a pass reads no members.

Scout counts against the factory's pod cap: over the cap, owners get one
decision a day and the next pass retries. Failed creates back off and, after
:data:`MAX_FAILURES` in :data:`FAILURE_WINDOW_S`, open an incident for owners.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    LABEL_DEFAULT_BASE,
    LABEL_DEFAULT_REPO,
    LABEL_ROLE,
    LABEL_STATE,
    ROLE_LEAD,
    ROLE_SCOUT,
    STATE_ACTIVE,
    STATE_DONE,
)
from omnigent.factory_spawn import member_create_body, member_labels
from omnigent.server.factory import definition, events, issues, spawn
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.definition_blocks import DiscoveryBudgetsDef
from omnigent.server.factory.envelope import quote_spoofed_lines
from omnigent.server.factory.mailer import MailItem, get_mailer
from omnigent.server.factory.membership import factory_members
from omnigent.stores.factory_store import now_ms, now_s

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

SCOUT_AGENT: Final = "factory-scout"
SCOUT_TITLE: Final = "Scout"
SUBJECT: Final = "seat:scout"
EVENT_SEAT: Final = "discovery.seat"
EVENT_FAILED: Final = "discovery.seat_failed"
POD_CAP_KEY: Final = "pod_cap:discovery-seat"
INCIDENT_RULE: Final = "discovery.seat"
MAX_FAILURES: Final = 3
FAILURE_WINDOW_S: Final = 6 * 3600
RETRY_AFTER_S: Final = 600
"""After a failed create, the next try waits this long."""
POD_CAP_ASK_S: Final = 86400
_inflight: set[str] = set()


# ── Reading ────────────────────────────────────────────────────


def scouts(deps: FactoryDeps, factory_id: str) -> list[Conversation]:
    """:returns: The factory's Scout sessions (top-level, any state), newest first."""
    rows = [
        m
        for m in factory_members(factory_id, deps.conversation_store)
        if m.labels.get(LABEL_ROLE) == ROLE_SCOUT
    ]
    return sorted(rows, key=lambda c: -c.created_at)


def live_scout(deps: FactoryDeps, factory_id: str) -> Conversation | None:
    """:returns: The Scout that holds a pod (active or paused, not closed), if any."""
    return next((c for c in scouts(deps, factory_id) if spawn.holds_pod(c)), None)


def spent_today(deps: FactoryDeps, factory_id: str, now: int | None = None) -> float:
    """:returns: Scout's spend today (UTC), from the factory's cost ledger."""
    day = time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else now_s()))
    cells = deps.factory_store.list_cost_daily(factory_id, since_day=day, until_day=day)
    return round(sum(c.cost_usd for c in cells if c.role == ROLE_SCOUT), 4)


def daily_cap(raw: object) -> float:
    """:returns: A ``discovery.budgets.usd_per_day`` value as a cap (its default when unset)."""
    default = DiscoveryBudgetsDef().usd_per_day
    if raw is None or isinstance(raw, bool):
        return default
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def budget_stopped(deps: FactoryDeps, factory_id: str, now: int | None = None) -> bool:
    """
    Whether Scout may spend nothing more today (nothing wakes it then): it spent
    ``discovery.budgets.usd_per_day``, or that budget is $0.
    """
    cap = daily_cap(definition.value(deps, factory_id, "discovery.budgets.usd_per_day"))
    return cap <= 0 or spent_today(deps, factory_id, now) >= cap


def awake_scout(deps: FactoryDeps, factory_id: str) -> Conversation | None:
    """:returns: The Scout an ask may wake now: seated, active and within its budget."""
    conv = live_scout(deps, factory_id)
    if conv is None or conv.labels.get(LABEL_STATE) != STATE_ACTIVE:
        return None
    return None if budget_stopped(deps, factory_id) else conv


def wanted_model(deps: FactoryDeps, factory_id: str) -> str | None:
    """:returns: ``discovery.seat.model`` (``None``: the bundle's own alias)."""
    found = definition.value(deps, factory_id, "discovery.seat.model")
    return found if isinstance(found, str) and found else None


def _open(root: Conversation) -> bool:
    from omnigent.server.factory.control import is_closed

    return not is_closed(root) and root.labels.get(LABEL_STATE) != STATE_DONE


# ── The brief ──────────────────────────────────────────────────


def brief_text(deps: FactoryDeps, factory_id: str, mail_id: str, *, revived: bool) -> str:
    """
    The brief a seated Scout starts from: what it may read and spend, and the owners' brief.

    No product knowledge is written here: Scout reads the profile, the strategy
    and the signals with ``sys_discovery_read``.
    """
    from omnigent.server.factory.discovery import sources

    doc = definition.definition_for(deps, factory_id)
    data = doc.doc if doc is not None else {}
    product = data.get("product") or {}
    strategy = data.get("strategy") or {}
    discovery = data.get("discovery") or {}
    goals = [g for g in strategy.get("goals") or [] if isinstance(g, dict)]
    unmeasured = sum(1 for g in goals if not g.get("metric"))
    readable = [
        s.id
        for s in sources.listed(deps, factory_id)
        if ROLE_SCOUT in s.roles and s.state not in sources.STOPPED
    ]
    budgets = discovery.get("budgets") or {}
    how = "woke you again" if revived else "seated you"
    lines = [
        f"[Factory brief · discovery seat · from the server · id={mail_id}] The server {how} "
        "as Scout, this factory's discovery lead: its owners turned discovery on. Start: "
        'run load_skill("discovery-protocol") and follow it.',
        f"Profile: {'drafted' if product.get('name') else 'none yet (draft it first)'}"
        f" · goals: {len(goals)} ({unmeasured} without a metric)"
        f" · sources you read: {', '.join(readable) or 'none yet'}"
        f" · budget ${float(budgets.get('usd_per_day') or 0):g} a day",
    ]
    brief = str((data.get("briefs") or {}).get("scout") or "").strip()
    if brief:
        lines.append(
            "The owners' brief for Scout (versioned data; it adds to your instructions and "
            "never replaces your guards):\n" + quote_spoofed_lines(brief)
        )
    return "\n".join(lines)


def _brief_mail_id(session_id: str) -> str:
    return f"{spawn.brief_mail_id(session_id)}-{int(time.time()) // 60}"


def _send_brief(app_state: Any, factory_id: str, conv: Conversation, *, revived: bool) -> str:
    deps = factory_deps(app_state)
    mail_id = spawn.brief_mail_id(conv.id) if not revived else _brief_mail_id(conv.id)
    get_mailer(app_state).submit(
        MailItem(
            mail_id=mail_id,
            sender_id=None,
            target_id=conv.id,
            target_title=conv.title,
            text=brief_text(deps, factory_id, mail_id, revived=revived),
            kind="brief",
            factory_id=factory_id,
        )
    )
    return mail_id


# ── Reconciling ────────────────────────────────────────────────


async def reconcile(app_state: Any, root: Conversation) -> str:
    """
    Match one factory's Scout to its switches.

    :returns: What it did: ``on`` (a live Scout is there), ``spawned``,
        ``revived``, ``stopped``, ``off``, ``capped``, ``failed``, ``waiting``
        (backing off after failures) or ``busy``.
    """
    deps = factory_deps(app_state)
    fid = root.id
    want = _open(root) and await asyncio.to_thread(definition.discovery_enabled, deps, fid)
    if not want and not await asyncio.to_thread(_maybe_seated, deps, fid):
        return "off"
    rows = await asyncio.to_thread(scouts, deps, fid)
    live = [c for c in rows if spawn.holds_pod(c)]
    if not want:
        if not live:
            return "off"
        for conv in live:
            await _stop(app_state, fid, conv, "discovery is off")
        return "stopped"
    for extra in live[1:]:
        await _stop(app_state, fid, extra, "a newer Scout holds the seat")
    if live:
        await _sync_model(app_state, fid, live[0])
        return "on"
    if fid in _inflight:
        return "busy"
    _inflight.add(fid)
    try:
        dormant = next((c for c in rows if _wakeable(c)), None)
        if dormant is not None:
            return await _revive(app_state, root, dormant)
        return await _spawn(app_state, root)
    finally:
        _inflight.discard(fid)


def _maybe_seated(deps: FactoryDeps, factory_id: str) -> bool:
    """Whether a Scout may hold a pod: one the server seated that it did not stop since."""
    newest: dict[object, str | None] = {}
    for event in deps.factory_store.find_events(
        factory_id, EVENT_SEAT, 0, SUBJECT, limit=200, newest_first=True
    ):
        data = event.data or {}
        newest.setdefault(data.get("session_id"), data.get("state"))
    return any(state != "stopped" for state in newest.values())


def _wakeable(conv: Conversation) -> bool:
    from omnigent.server.factory.control import is_closed

    return not is_closed(conv) and conv.labels.get(LABEL_STATE) == STATE_DONE


async def _stop(app_state: Any, factory_id: str, conv: Conversation, why: str) -> None:
    deps = factory_deps(app_state)
    await asyncio.to_thread(deps.conversation_store.set_labels, conv.id, {LABEL_STATE: STATE_DONE})
    await events.emit(
        app_state,
        factory_id,
        EVENT_SEAT,
        severity="notice",
        to_id=conv.id,
        subject=SUBJECT,
        summary=f"the server stopped Scout: {why}",
        data={"state": "stopped", "session_id": conv.id, "why": why, "by": "server"},
    )


def _server_model(deps: FactoryDeps, factory_id: str, session_id: str) -> str | None:
    """:returns: The model override the server last gave *session_id* (at its seat or since)."""
    rows = deps.factory_store.find_events(
        factory_id, EVENT_SEAT, 0, SUBJECT, limit=50, newest_first=True
    )
    for event in rows:
        data = event.data or {}
        if data.get("session_id") == session_id and data.get("state") in ("spawned", "model"):
            model = data.get("model")
            return model if isinstance(model, str) and model else None
    return None


async def _patch_model(
    app_state: Any, factory_id: str, conv: Conversation, model: str | None
) -> bool:
    """
    Switch the session's model as a person's switch does: the row, and the live terminal.

    :returns: Whether the session route took it (``False``: no runner client, or it failed).
    """
    from omnigent.server.factory.control import get_conv

    deps = factory_deps(app_state)
    root = await get_conv(deps, factory_id)
    creator = await asyncio.to_thread(spawn.creator_of, deps, root) if root else None
    token = spawn.mint_token(deps, creator)
    client = spawn._client_factory(app_state, token) if token else None
    if client is None:
        return False
    try:
        async with client:
            resp = await client.patch(
                f"/v1/sessions/{conv.id}", json={"model_override": model or "default"}
            )
    except Exception:  # noqa: BLE001 - the row is stamped instead
        _logger.warning("factory %s: Scout model switch failed", factory_id, exc_info=True)
        return False
    return resp.status_code < 400


async def _sync_model(app_state: Any, factory_id: str, conv: Conversation) -> None:
    deps = factory_deps(app_state)
    model = await asyncio.to_thread(wanted_model, deps, factory_id)
    current = conv.model_override
    if model == current:
        return
    if model is None and current != await asyncio.to_thread(
        _server_model, deps, factory_id, conv.id
    ):
        return  # A person chose this session's model; the definition names none.
    now = await _patch_model(app_state, factory_id, conv, model)
    if not now:
        await asyncio.to_thread(
            lambda: deps.conversation_store.update_conversation(
                conv.id, model_override=model, _unset_model_override=model is None
            )
        )
    shown = model or "the bundle's default"
    await events.emit(
        app_state,
        factory_id,
        EVENT_SEAT,
        to_id=conv.id,
        subject=SUBJECT,
        summary=f"Scout's model is now {shown}" + ("" if now else " (from its next start)"),
        data={
            "state": "model",
            "session_id": conv.id,
            "model": model,
            "applied": "now" if now else "next_start",
            "by": "server",
        },
    )


async def _revive(app_state: Any, root: Conversation, conv: Conversation) -> str:
    deps = factory_deps(app_state)
    fid = root.id
    members = await asyncio.to_thread(spawn.live_members, fid, deps.conversation_store)
    cap = await asyncio.to_thread(spawn.pod_cap, deps, root) or spawn.DEFAULT_MAX_PODS
    if len(members) >= cap:
        await _pod_cap_decision(app_state, root, len(members), cap)
        return "capped"
    await asyncio.to_thread(
        deps.conversation_store.set_labels, conv.id, {LABEL_STATE: STATE_ACTIVE}
    )
    await _sync_model(app_state, fid, conv)
    mail_id = await asyncio.to_thread(_send_brief, app_state, fid, conv, revived=True)
    await events.emit(
        app_state,
        fid,
        EVENT_SEAT,
        severity="notice",
        to_id=conv.id,
        subject=SUBJECT,
        summary="the server woke Scout: discovery is on",
        data={"state": "revived", "session_id": conv.id, "mail_id": mail_id, "by": "server"},
    )
    return "revived"


def _recent_failures(deps: FactoryDeps, factory_id: str) -> list[Any]:
    since = (now_s() - FAILURE_WINDOW_S) * 1000
    return deps.factory_store.find_events(factory_id, EVENT_FAILED, since, SUBJECT)


async def _failed(app_state: Any, root: Conversation, reason: str) -> str:
    deps = factory_deps(app_state)
    await events.emit(
        app_state,
        root.id,
        EVENT_FAILED,
        severity="warning",
        subject=SUBJECT,
        summary=f"the server could not seat Scout: {reason}",
        data={"reason": reason, "at_ms": now_ms()},
    )
    failures = await asyncio.to_thread(_recent_failures, deps, root.id)
    if len(failures) >= MAX_FAILURES:
        await issues.raise_issue(
            app_state,
            root.id,
            rule=INCIDENT_RULE,
            target=SUBJECT,
            summary=f"Scout could not be seated ({len(failures)} tries): {reason}",
            severity="warning",
            assignee=issues.ASSIGN_OWNERS,
            source="server",
            body=(
                "Discovery is on, but the server could not start its discovery seat. It tries "
                "again every few hours; turn discovery off to stop trying."
            ),
        )
    return "failed"


def _backing_off(deps: FactoryDeps, factory_id: str, now: int) -> bool:
    failures = _recent_failures(deps, factory_id)
    if not failures:
        return False
    last = max(e.ts for e in failures) // 1000
    wait = RETRY_AFTER_S if len(failures) < MAX_FAILURES else FAILURE_WINDOW_S // 2
    return now - last < wait


def _pod_cap_asked(deps: FactoryDeps, factory_id: str, now: int) -> bool:
    rows = deps.factory_store.list_decisions(factory_id=factory_id, limit=500)
    return any(r.dedupe_key == POD_CAP_KEY and r.created_at >= now - POD_CAP_ASK_S for r in rows)


async def _pod_cap_decision(app_state: Any, root: Conversation, live: int, cap: int) -> None:
    from omnigent.server.factory import server_decisions

    deps = factory_deps(app_state)
    if await asyncio.to_thread(_pod_cap_asked, deps, root.id, now_s()):
        return
    await server_decisions.open_decision(
        app_state,
        root.id,
        source="proposal",
        dedupe_key=POD_CAP_KEY,
        question=f"Scout needs a pod: the factory runs {live}/{cap}",
        context=(
            "Discovery is on, so the server seats Scout as soon as a member is done or "
            "stopped (the factory's `factory_max_pods`). Stop or finish a project to free a "
            "pod, or turn discovery off."
        ),
        options=[
            {
                "id": "wait",
                "label": "Seat Scout when a pod frees",
                "action": "dismiss",
                "recommended": True,
            },
            {"id": "open", "label": "Open the factory ↗", "href": f"/factory/{root.id}"},
        ],
        blocking=False,
        project=None,
        asked_by=None,
        assignee="owners",
    )


def _supervisor(deps: FactoryDeps, root: Conversation) -> str:
    """Sentinel (the other lead) supervises Scout; the root when there is none."""
    for member in spawn.live_members(root.id, deps.conversation_store):
        if member.labels.get(LABEL_ROLE) == ROLE_LEAD and member.id != root.id:
            return member.id
    return root.id


def create_body(root: Conversation, *, agent_id: str, model: str | None) -> dict[str, Any]:
    """
    The ``POST /v1/sessions`` body of a new Scout: no factory labels (the server stamps
    them after the create) and the factory's default repo checked out, when it has one.
    """
    repo = root.labels.get(LABEL_DEFAULT_REPO)
    base = root.labels.get(LABEL_DEFAULT_BASE) or "main"
    workspaces = [f"https://github.com/{repo}#{base}"] if repo else []
    body = member_create_body(
        agent_id=agent_id, title=SCOUT_TITLE, labels={}, workspaces=workspaces
    )
    if model:
        body["model_override"] = model
    return body


def seat_labels(deps: FactoryDeps, root: Conversation, agent_id: str) -> dict[str, str]:
    """:returns: The factory labels the server stamps on a new Scout."""
    extra = dict(spawn._server_labels(root))
    extra.update(spawn._version_labels(deps, agent_id))
    return member_labels(
        factory_id=root.id,
        role=ROLE_SCOUT,
        parent=root.id,
        supervisor=_supervisor(deps, root),
        heartbeat_s=0,
        extra=extra,
    )


async def _spawn(app_state: Any, root: Conversation) -> str:
    deps = factory_deps(app_state)
    fid = root.id
    if await asyncio.to_thread(_backing_off, deps, fid, now_s()):
        return "waiting"
    members = await asyncio.to_thread(spawn.live_members, fid, deps.conversation_store)
    cap = await asyncio.to_thread(spawn.pod_cap, deps, root) or spawn.DEFAULT_MAX_PODS
    if len(members) >= cap:
        await _pod_cap_decision(app_state, root, len(members), cap)
        return "capped"
    agent = await asyncio.to_thread(deps.agent_store.get_by_name, SCOUT_AGENT)
    if agent is None:
        return await _failed(app_state, root, f"agent {SCOUT_AGENT} is not seeded")
    creator = await asyncio.to_thread(spawn.creator_of, deps, root)
    token = spawn.mint_token(deps, creator)
    client = spawn._client_factory(app_state, token) if token else None
    if client is None:
        return await _failed(app_state, root, "no runner token or in-process app to create it")
    model = await asyncio.to_thread(wanted_model, deps, fid)
    body = create_body(root, agent_id=agent.id, model=model)
    try:
        async with client:
            resp = await client.post("/v1/sessions", json=body)
    except Exception as exc:  # noqa: BLE001 - recorded; the next pass retries
        _logger.warning("factory %s: Scout create failed", fid, exc_info=True)
        return await _failed(app_state, root, f"error: {type(exc).__name__}")
    try:
        created = resp.json()
    except ValueError:
        created = None
    new_id = created.get("id") if isinstance(created, dict) else None
    if resp.status_code >= 400 or not isinstance(new_id, str) or not new_id:
        return await _failed(app_state, root, f"create returned {resp.status_code}")
    try:
        labels = await asyncio.to_thread(seat_labels, deps, root, agent.id)
        await asyncio.to_thread(deps.conversation_store.set_labels, new_id, labels)
        conv = await asyncio.to_thread(deps.conversation_store.get_conversation, new_id)
        await spawn._mirror_people(app_state, conv)
    except Exception as exc:  # noqa: BLE001 - recorded, and the orphan put away
        _logger.warning("factory %s: could not seat the new Scout", fid, exc_info=True)
        await _put_away(deps, new_id)
        return await _failed(app_state, root, f"error after the create: {type(exc).__name__}")
    if conv is None:
        return await _failed(app_state, root, "the new session is gone")
    mail_id = await asyncio.to_thread(_send_brief, app_state, fid, conv, revived=False)
    await events.emit(
        app_state,
        fid,
        "member.spawned",
        severity="notice",
        to_id=new_id,
        subject=f"member:{new_id}",
        summary="the server seated Scout (discovery is on)",
        data={
            "session_id": new_id,
            "role": ROLE_SCOUT,
            "via": "discovery.enabled",
            "by": "server",
            "by_kind": "server",
            "mail_id": mail_id,
            "model": model,
        },
    )
    await events.emit(
        app_state,
        fid,
        EVENT_SEAT,
        severity="notice",
        to_id=new_id,
        subject=SUBJECT,
        summary="the server seated Scout: discovery is on",
        data={"state": "spawned", "session_id": new_id, "model": model, "by": "server"},
    )
    await issues.clear_issue(app_state, fid, INCIDENT_RULE, SUBJECT, reason="Scout is seated")
    return "spawned"


async def _put_away(deps: FactoryDeps, session_id: str) -> None:
    """Archive a session the server created but could not seat, so no pass counts it."""
    try:
        await asyncio.to_thread(
            deps.conversation_store.update_conversation, session_id, archived=True
        )
    except Exception:  # noqa: BLE001 - the failure is recorded either way
        _logger.warning("factory: could not archive the unseated Scout %s", session_id)


def factory_roots(deps: FactoryDeps) -> list[Conversation]:
    """:returns: Every factory root (the sessions whose factory id is their own)."""
    from omnigent.factory import LABEL_ID
    from omnigent.server.factory.control import same_id
    from omnigent.server.factory.membership import is_top_level

    store = deps.conversation_store
    ids = store.list_conversation_ids_with_label(LABEL_ID)
    rows = store.get_conversations(ids)
    return [
        conv
        for conv in (rows.get(i) for i in ids)
        if conv is not None and is_top_level(conv) and same_id(conv.labels.get(LABEL_ID), conv.id)
    ]


async def reconcile_all(app_state: Any) -> dict[str, str]:
    """:returns: Factory id → what :func:`reconcile` did, for every factory. Never raises."""
    deps = factory_deps(app_state)
    out: dict[str, str] = {}
    for root in await asyncio.to_thread(factory_roots, deps):
        try:
            out[root.id] = await reconcile(app_state, root)
        except Exception:
            _logger.exception("factory %s: discovery seat pass failed", root.id)
            out[root.id] = "error"
    return out


__all__ = [
    "EVENT_FAILED",
    "EVENT_SEAT",
    "SCOUT_AGENT",
    "SUBJECT",
    "awake_scout",
    "brief_text",
    "budget_stopped",
    "create_body",
    "daily_cap",
    "factory_roots",
    "live_scout",
    "reconcile",
    "reconcile_all",
    "scouts",
    "seat_labels",
    "spent_today",
    "wanted_model",
]
