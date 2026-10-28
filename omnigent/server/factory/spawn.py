"""The server spawns a project's PL itself once its project is applied.

It sends the exact managed create ``sys_factory_spawn`` sends
(:func:`omnigent.factory_spawn.member_create_body`): ``POST /v1/sessions``
in process, through ``httpx.ASGITransport``, authenticated as the factory's
creator with a short-lived runner token. So the PL is provisioned, granted
and labelled like any lead-spawned PL. Then the people roster is mirrored
onto it, its charter goes out as a ``[Factory brief`` through the mailer,
``member.spawned`` is logged and Atlas gets an info notice.

Guards: the factory's ``factory_max_pods`` (over the cap, owners get a
decision and the next pass retries) and the duplicate check (a live PL of the
project is adopted, never doubled). A create that crashed half-way is
finished by the next pass: the PL is adopted and its brief, sent under a
deterministic mail id, goes out once.

Fallback (``spawn=lead``): after 2 failed creates, when no runner token can be
minted (header auth), or when the root label says so, Atlas is mailed the
exact ``sys_factory_spawn(...)`` call as before (``start.add_project_text``).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import httpx

from omnigent.factory import (
    CADENCE_DEMO,
    CADENCE_HEARTBEATS,
    LABEL_CADENCE,
    LABEL_DEF_VERSION,
    LABEL_PREFIX,
    LABEL_ROLE,
    LABEL_STATE,
    MESSAGE_MAX_CHARS,
    PROJECT_KIND_INTEGRATION,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    STATE_DONE,
)
from omnigent.factory_spawn import member_create_body, member_labels
from omnigent.server.factory import events
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.charter import (
    SPAWN_LEAD,
    SPAWN_SERVER,
    normalize_charter,
    project_base_branch,
    project_kind,
    render_pl_charter,
    spawn_owner,
)
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.envelope import quote_spoofed_lines
from omnigent.server.factory.mailer import MailItem, get_mailer, new_mail_id
from omnigent.server.factory.membership import factory_members, load_session_spec
from omnigent.stores.factory_store import now_ms

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryProject

_logger = logging.getLogger(__name__)

PL_AGENT: Final = "factory-pl"
LABEL_ENGINE_SPAWN: Final = f"{LABEL_PREFIX}engine_spawn"
"""Root label ``server`` (default) or ``lead`` until the definition holds ``engine.spawn``."""
ASGI_APP_ATTR: Final = "factory_asgi_app"
"""``app.state`` attribute holding the app, for in-process creates."""
MAX_SERVER_FAILURES: Final = 2
RUNNER_TOKEN_TTL_S: Final = 300
DEFAULT_MAX_PODS: Final = 12
FINISH_WINDOW_S: Final = 24 * 3600
"""A PL older than this is never re-briefed (its spawn events may be pruned)."""
_CREATE_TIMEOUT_S: Final = 30.0
_INTERNAL_BASE_URL: Final = "http://omnigent.internal"
POD_CAP_KEY_PREFIX: Final = "pod_cap:"

ClientFactory = Callable[[Any, str], httpx.AsyncClient | None]

_inflight: set[tuple[str, str]] = set()
_tasks: set[asyncio.Task[Any]] = set()
_locks: dict[tuple[int, str], asyncio.Lock] = {}


@dataclass(frozen=True)
class SpawnOutcome:
    """
    What one launch did.

    :param path: ``server`` (created or queued), ``lead`` (mailed Atlas),
        ``adopted`` (a live PL existed), ``capped`` (pod cap), ``failed``,
        ``busy`` (already in flight) or ``skipped`` (no longer active).
    :param session_id: The PL, when known.
    :param mail_id: The brief (server) or Atlas's request (lead).
    :param reason: Why it failed, capped or fell back.
    """

    path: str
    session_id: str | None = None
    mail_id: str | None = None
    reason: str | None = None


# ── Small helpers ──────────────────────────────────────────────


def in_flight(factory_id: str, slug: str) -> bool:
    """:returns: Whether a spawn of *slug* runs in this process now."""
    return (factory_id, slug) in _inflight


def brief_mail_id(session_id: str) -> str:
    """:returns: The deterministic id of a server-spawned PL's brief (replays dedupe on it)."""
    return f"mail_{hashlib.sha256(f'brief:{session_id}'.encode()).hexdigest()[:16]}"


def _member_subject(session_id: str) -> str:
    return f"member:{session_id}"


def _project_subject(slug: str) -> str:
    return f"project:{slug}"


def _lock(factory_id: str) -> asyncio.Lock:
    """One spawn at a time per factory, so the pod and duplicate checks see each other."""
    key = (id(asyncio.get_running_loop()), factory_id)
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock


def _default_client(app_state: Any, token: str) -> httpx.AsyncClient | None:
    app = getattr(app_state, ASGI_APP_ATTR, None)
    if app is None:
        return None
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=_INTERNAL_BASE_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=_CREATE_TIMEOUT_S,
    )


_client_factory: ClientFactory = _default_client


def creator_of(deps: FactoryDeps, root: Conversation) -> str | None:
    """:returns: The user who owns the factory root (the spawns run as them)."""
    from omnigent.server.routes._auth_helpers import get_session_owner_id

    return get_session_owner_id(root.id, deps.permission_store)


def mint_token(deps: FactoryDeps, user_id: str | None) -> str | None:
    """:returns: A runner bearer for *user_id*, or ``None`` (header auth, no owner)."""
    provider = deps.auth_provider
    mint = getattr(provider, "mint_runner_token", None)
    if not user_id or mint is None:
        return None
    try:
        return mint(user_id, RUNNER_TOKEN_TTL_S)
    except Exception:  # noqa: BLE001 - no token means the lead fallback
        _logger.warning("factory: could not mint a runner token for spawns", exc_info=True)
        return None


def _max_pods(deps: FactoryDeps, root: Conversation) -> int:
    spec = load_session_spec(root, deps)
    config = getattr(getattr(spec, "executor", None), "config", None)
    raw = config.get("factory_max_pods") if isinstance(config, dict) else None
    if isinstance(raw, str) and raw.strip().isdigit():
        raw = int(raw.strip())
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return DEFAULT_MAX_PODS


def _live_members(deps: FactoryDeps, factory_id: str) -> list[Conversation]:
    from omnigent.server.factory.control import is_closed

    members = factory_members(factory_id, deps.conversation_store)
    return [m for m in members if not is_closed(m) and m.labels.get(LABEL_STATE) != STATE_DONE]


def _supervisor(root: Conversation, members: list[Conversation]) -> str:
    """The peer lead supervises a PL, as ``sys_factory_spawn`` defaults it; else the root."""
    for member in members:
        if member.labels.get(LABEL_ROLE) == ROLE_LEAD and member.id != root.id:
            return member.id
    return root.id


def _failures(deps: FactoryDeps, factory_id: str, project: FactoryProject) -> int:
    since = max(0, project.created_at - 60) * 1000
    rows = deps.factory_store.find_events(
        factory_id, "member.spawn_failed", since, _project_subject(project.slug)
    )
    return len(rows)


def needs_finishing(deps: FactoryDeps, factory_id: str, pl: Conversation) -> bool:
    """
    Whether a PL of a server-owned project still lacks its spawn record (and brief).

    True for a session the server created but a restart cut off before the brief.
    """
    if time.time() - pl.created_at > FINISH_WINDOW_S:
        return False
    rows = deps.factory_store.find_events(
        factory_id, "member.spawned", 0, _member_subject(pl.id), limit=1
    )
    return not rows


def _version_labels(deps: FactoryDeps, root: Conversation, agent_id: str) -> dict[str, str]:
    """``def_version`` from the root, plus the platform ``version`` and ``bundle`` labels."""
    from omnigent.server.factory.telemetry import versions

    labels: dict[str, str] = {}
    if root.labels.get(LABEL_DEF_VERSION):
        labels[LABEL_DEF_VERSION] = root.labels[LABEL_DEF_VERSION]
    try:
        labels.update(versions.labels_for_agent(deps.agent_store, agent_id))
    except Exception:  # noqa: BLE001 - the labels only group telemetry by version
        _logger.warning("factory: no version labels for a new PL", exc_info=True)
    return labels


def _heartbeat(root: Conversation, project: FactoryProject) -> int:
    if project_kind(project) == PROJECT_KIND_INTEGRATION:
        return 0
    cadence = root.labels.get(LABEL_CADENCE) or CADENCE_DEMO
    beats = CADENCE_HEARTBEATS.get(cadence, CADENCE_HEARTBEATS[CADENCE_DEMO])
    return beats[ROLE_PROJECT_LEAD]


def create_body(
    root: Conversation,
    project: FactoryProject,
    *,
    agent_id: str,
    supervisor: str,
    extra_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    The ``POST /v1/sessions`` body of a PL, as ``sys_factory_spawn`` builds it for Atlas.

    :param root: The factory root (Atlas): the PL's parent.
    :param supervisor: The peer lead (Sentinel), else the root.
    """
    labels = member_labels(
        factory_id=root.id,
        role=ROLE_PROJECT_LEAD,
        parent=root.id,
        supervisor=supervisor,
        heartbeat_s=_heartbeat(root, project),
        project=project.slug,
        extra=extra_labels,
    )
    workspaces = (
        [f"https://github.com/{project.repo}#{project_base_branch(project)}"]
        if project.repo
        else []
    )
    return member_create_body(
        agent_id=agent_id, title=f"PL · {project.slug}", labels=labels, workspaces=workspaces
    )


def _inherited_prs(deps: FactoryDeps, factory_id: str, slug: str) -> list[int]:
    prs = deps.factory_store.list_prs(factory_id, ["open"])
    return sorted(p.number for p in prs if p.project_slug == slug)


def brief_text(
    project: FactoryProject,
    *,
    by: str,
    via: str,
    mail_id: str,
    inherited: list[int] | None = None,
) -> str:
    """
    The brief a server-spawned PL starts from: header, the inherited PRs, the charter.

    :param by: Who made the project, e.g. ``"bob"`` or ``"Atlas"``.
    :param via: ``C-4`` or ``cockpit``.
    """
    head = (
        f"[Factory brief · from {by} via {via} · id={mail_id}] The server spawned you as the "
        f"PL of project {project.slug}. Start: your charter follows."
    )
    parts = [head]
    if inherited:
        refs = ", ".join(f"#{n}" for n in inherited)
        parts.append(
            f"Inherited PRs {refs}: they are yours now; run PREVIEW for them (previews died "
            "with their old pod) and track them in plan.md."
        )
    # Goal, constraints and demo focus may come from an agent: quote notice-like lines.
    charter = quote_spoofed_lines(render_pl_charter(project))
    text = "\n\n".join([*parts, charter])
    return text if len(text) <= MESSAGE_MAX_CHARS else text[:MESSAGE_MAX_CHARS]


def spawned_notice(project: FactoryProject, session_id: str, via: str) -> str:
    """:returns: Atlas's ``[Factory notice · PL·<slug> spawned by the server · C-4]``."""
    return (
        f"[Factory notice · PL·{project.slug} spawned by the server · {via}] Info only: "
        f'"PL · {project.slug}" ({session_id}) is provisioning with its charter. Record it in '
        "the ledger; do not spawn it."
    )


async def _mirror_people(app_state: Any, pl: Conversation | None) -> None:
    """Copy the factory's people roster onto the new PL (idempotent; never raises)."""
    from omnigent.server.factory.people import mirror_member_on_create

    if pl is not None:
        await mirror_member_on_create(app_state, pl)


# ── The lead fallback ──────────────────────────────────────────


async def lead_fallback(
    app_state: Any,
    root: Conversation,
    project: FactoryProject,
    *,
    by: str,
    human: bool,
    reason: str,
) -> SpawnOutcome:
    """Mail Atlas the exact ``sys_factory_spawn`` call and leave the spawn to it."""
    from omnigent.server.factory.start import add_project_text

    deps = factory_deps(app_state)
    charter = normalize_charter(project.charter)
    charter["spawn_owner"] = SPAWN_LEAD
    project = (
        await asyncio.to_thread(deps.factory_store.update_project, project.id, charter=charter)
        or project
    )
    cadence = root.labels.get(LABEL_CADENCE) or CADENCE_DEMO
    mail_id = new_mail_id()
    get_mailer(app_state).submit(
        MailItem(
            mail_id=mail_id,
            sender_id=None,
            target_id=root.id,
            target_title=root.title,
            text=add_project_text(by, mail_id, project, cadence, human=human),
            kind="request",
        )
    )
    _logger.info("factory: %s/%s spawns through the lead (%s)", root.id, project.slug, reason)
    return SpawnOutcome(SPAWN_LEAD, mail_id=mail_id, reason=reason)


# ── Launch ─────────────────────────────────────────────────────


def spawn_mode(app_state: Any, root: Conversation, project: FactoryProject) -> tuple[str, str]:
    """
    Who spawns *project*'s PL now: ``(server|lead, reason)``.

    Lead when the root asks for it, after :data:`MAX_SERVER_FAILURES` failed
    creates, when no runner token can be minted, or without an app to call.
    """
    deps = factory_deps(app_state)
    if (root.labels.get(LABEL_ENGINE_SPAWN) or "").strip() == SPAWN_LEAD:
        return SPAWN_LEAD, "engine_spawn=lead"
    if _failures(deps, root.id, project) >= MAX_SERVER_FAILURES:
        return SPAWN_LEAD, f"{MAX_SERVER_FAILURES} failed creates"
    if mint_token(deps, creator_of(deps, root)) is None:
        return SPAWN_LEAD, "no runner token (header auth)"
    if _client_factory is _default_client and getattr(app_state, ASGI_APP_ATTR, None) is None:
        return SPAWN_LEAD, "no in-process app"
    return SPAWN_SERVER, ""


def _by(actor: Actor | None) -> tuple[str, bool]:
    """:returns: ``(display name, whether a human acted)`` for texts."""
    from omnigent.server.factory.control import human_name

    if actor is None or actor.kind == "server":
        return "server", False
    if actor.kind == "human":
        return human_name(actor.user_id), True
    return actor.title, False


async def launch(
    app_state: Any,
    root: Conversation,
    project: FactoryProject,
    *,
    actor: Actor | None = None,
    proposal_ref: str | None = None,
    trigger: str | None = None,
    background: bool = True,
) -> SpawnOutcome:
    """
    Start *project*'s PL: on the server (in the background), or through the lead.

    :param actor: Who made the project (named in the brief and the fallback mail).
    :param proposal_ref: e.g. ``"C-4"``, named in the brief and Atlas's notice.
    :param trigger: Why now, e.g. ``"pr.merged"`` (logged).
    :param background: Run the create as a task (the request does not wait for it).
    """
    by, human = _by(actor)
    mode, reason = await asyncio.to_thread(spawn_mode, app_state, root, project)
    if mode == SPAWN_LEAD:
        return await lead_fallback(app_state, root, project, by=by, human=human, reason=reason)
    key = (root.id, project.slug)
    if key in _inflight:
        return SpawnOutcome("busy")
    _inflight.add(key)
    via = proposal_ref or "cockpit"
    run = _spawn(app_state, root, project, by=by, via=via, trigger=trigger)
    if not background:
        return await run
    task = asyncio.create_task(run)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return SpawnOutcome(SPAWN_SERVER)


async def drain() -> None:
    """Wait for every background spawn of this process (tests, shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def _spawn(
    app_state: Any,
    root: Conversation,
    project: FactoryProject,
    *,
    by: str,
    via: str,
    trigger: str | None,
) -> SpawnOutcome:
    """The create itself, under the factory's spawn lock; releases the in-flight mark."""
    key = (root.id, project.slug)
    try:
        async with _lock(root.id):
            return await _spawn_locked(app_state, root, project, by=by, via=via)
    except Exception as exc:  # noqa: BLE001 - recorded; the next pass retries
        _logger.warning("factory: spawn of %s failed", project.slug, exc_info=True)
        await _failed(app_state, root, project, f"error: {type(exc).__name__}", trigger=trigger)
        return SpawnOutcome("failed", reason=type(exc).__name__)
    finally:
        _inflight.discard(key)


async def _spawn_locked(
    app_state: Any, root: Conversation, project: FactoryProject, *, by: str, via: str
) -> SpawnOutcome:
    from omnigent.server.factory.control import find_project_pl

    deps = factory_deps(app_state)
    store = deps.factory_store
    fresh = await asyncio.to_thread(store.get_project, root.id, project.slug)
    if fresh is None or fresh.status != "active":
        return SpawnOutcome("skipped", reason="not active")
    if spawn_owner(fresh) == SPAWN_LEAD:
        return SpawnOutcome("skipped", reason="the lead spawns it")
    project = fresh
    existing = await asyncio.to_thread(
        find_project_pl, deps, root.id, project.slug, include_done=True
    )
    if existing is not None:
        return await _adopt(app_state, root, project, existing, by=by, via=via)
    members = await asyncio.to_thread(_live_members, deps, root.id)
    max_pods = await asyncio.to_thread(_max_pods, deps, root)
    if len(members) >= max_pods:
        await _pod_cap_decision(app_state, root, project, len(members), max_pods)
        return SpawnOutcome("capped", reason=f"{len(members)}/{max_pods} pods")
    agent = await asyncio.to_thread(deps.agent_store.get_by_name, PL_AGENT)
    if agent is None:
        await _failed(app_state, root, project, f"agent {PL_AGENT} not found")
        return SpawnOutcome("failed", reason="agent_not_found")
    creator = await asyncio.to_thread(creator_of, deps, root)
    token = mint_token(deps, creator)
    client = _client_factory(app_state, token) if token else None
    if client is None:
        return await lead_fallback(
            app_state, root, project, by=by, human=False, reason="no runner client"
        )
    body = create_body(
        root,
        project,
        agent_id=agent.id,
        supervisor=_supervisor(root, members),
        extra_labels=await asyncio.to_thread(_version_labels, deps, root, agent.id),
    )
    async with client:
        resp = await client.post("/v1/sessions", json=body)
    try:
        created = resp.json()
    except ValueError:
        created = None
    new_id = created.get("id") if isinstance(created, dict) else None
    if resp.status_code >= 400 or not isinstance(new_id, str) or not new_id:
        await _failed(app_state, root, project, f"create returned {resp.status_code}")
        return await _after_failure(app_state, root, project, by=by, status=resp.status_code)
    project = (
        await asyncio.to_thread(store.update_project, project.id, pl_session_id=new_id) or project
    )
    pl = await asyncio.to_thread(deps.conversation_store.get_conversation, new_id)
    return await _finish(app_state, root, project, new_id, pl, by=by, via=via)


async def _after_failure(
    app_state: Any, root: Conversation, project: FactoryProject, *, by: str, status: int
) -> SpawnOutcome:
    deps = factory_deps(app_state)
    if await asyncio.to_thread(_failures, deps, root.id, project) >= MAX_SERVER_FAILURES:
        return await lead_fallback(
            app_state,
            root,
            project,
            by=by,
            human=False,
            reason=f"{MAX_SERVER_FAILURES} failed creates",
        )
    return SpawnOutcome("failed", reason=f"create returned {status}")


async def _adopt(
    app_state: Any,
    root: Conversation,
    project: FactoryProject,
    pl: Conversation,
    *,
    by: str,
    via: str,
) -> SpawnOutcome:
    """A PL of the project exists: link it, and finish its spawn if a restart cut it off."""
    deps = factory_deps(app_state)
    if project.pl_session_id != pl.id:
        project = (
            await asyncio.to_thread(
                deps.factory_store.update_project, project.id, pl_session_id=pl.id
            )
            or project
        )
    if not await asyncio.to_thread(needs_finishing, deps, root.id, pl):
        return SpawnOutcome("adopted", session_id=pl.id)
    return await _finish(app_state, root, project, pl.id, pl, by=by, via=via)


def _brief_queued(deps: FactoryDeps, factory_id: str, mail_id: str) -> bool:
    from omnigent.server.factory.mail_events import KIND_QUEUED, mail_subject

    rows = deps.factory_store.find_events(factory_id, KIND_QUEUED, 0, mail_subject(mail_id))
    return bool(rows)


async def _finish(
    app_state: Any,
    root: Conversation,
    project: FactoryProject,
    session_id: str,
    pl: Conversation | None,
    *,
    by: str,
    via: str,
) -> SpawnOutcome:
    """People grants, the brief (once), ``member.spawned``, Atlas's notice."""
    from omnigent.server.factory import server_decisions
    from omnigent.server.factory.control import send_text

    deps = factory_deps(app_state)
    await _mirror_people(app_state, pl)
    mail_id = brief_mail_id(session_id)
    if not await asyncio.to_thread(_brief_queued, deps, root.id, mail_id):
        inherited = await asyncio.to_thread(_inherited_prs, deps, root.id, project.slug)
        get_mailer(app_state).submit(
            MailItem(
                mail_id=mail_id,
                sender_id=None,
                target_id=session_id,
                target_title=pl.title if pl is not None else None,
                text=brief_text(project, by=by, via=via, mail_id=mail_id, inherited=inherited),
                kind="brief",
                factory_id=root.id,
                project=project.slug,
            )
        )
    await events.emit(
        app_state,
        root.id,
        "member.spawned",
        severity="notice",
        project=project.slug,
        to_id=session_id,
        subject=_member_subject(session_id),
        summary=f"the server spawned PL · {project.slug} ({via})",
        data={
            "session_id": session_id,
            "project": project.slug,
            "via": via,
            "by": by,
            "by_kind": "server",
            "mail_id": mail_id,
        },
    )
    send_text(app_state, root.id, spawned_notice(project, session_id, via), kind="info")
    await server_decisions.expire_by_key(app_state, root.id, f"{POD_CAP_KEY_PREFIX}{project.slug}")
    return SpawnOutcome(SPAWN_SERVER, session_id=session_id, mail_id=mail_id)


async def _failed(
    app_state: Any,
    root: Conversation,
    project: FactoryProject,
    reason: str,
    *,
    trigger: str | None = None,
) -> None:
    await events.emit(
        app_state,
        root.id,
        "member.spawn_failed",
        severity="warning",
        project=project.slug,
        subject=_project_subject(project.slug),
        summary=f"the server could not spawn PL · {project.slug}: {reason}",
        data={"reason": reason, "trigger": trigger, "at_ms": now_ms()},
    )


def _pod_cap_answered(deps: FactoryDeps, factory_id: str, project: FactoryProject) -> bool:
    """Whether owners already answered this project's pod-cap decision (e.g. "wait")."""
    key = f"{POD_CAP_KEY_PREFIX}{project.slug}"
    rows = deps.factory_store.list_decisions(
        factory_id=factory_id, status=["answered", "withdrawn"], limit=500
    )
    return any(r.dedupe_key == key and r.created_at >= project.created_at for r in rows)


async def _pod_cap_decision(
    app_state: Any, root: Conversation, project: FactoryProject, live: int, max_pods: int
) -> None:
    """
    Owners decide what to stop; the next pass spawns once a pod is free.

    Asked once per project: an answered decision is not reopened by later passes.
    """
    from omnigent.server.factory import server_decisions

    deps = factory_deps(app_state)
    if await asyncio.to_thread(_pod_cap_answered, deps, root.id, project):
        return
    # Only a ``proposal:C-n`` subject links a decision to a proposal; this one is ``project:``.
    opened = await server_decisions.open_decision(
        app_state,
        root.id,
        source="proposal",
        dedupe_key=f"{POD_CAP_KEY_PREFIX}{project.slug}",
        question=f"PL · {project.slug} needs a pod: the factory runs {live}/{max_pods}",
        context=(
            "The server spawns it as soon as a member is done or stopped (the factory's "
            "`factory_max_pods`). Stop or finish a project to free a pod."
        ),
        options=[
            {
                "id": "wait",
                "label": "Spawn when a pod frees",
                "action": "dismiss",
                "recommended": True,
            },
            {"id": "open", "label": "Open the factory ↗", "href": f"/factory/{root.id}"},
        ],
        blocking=False,
        project=project.slug,
        asked_by=None,
        assignee="owners",
    )
    if opened is not None and not opened[1]:
        await asyncio.to_thread(
            deps.factory_store.update_decision,
            opened[0].id,
            subject=_project_subject(project.slug),
        )


async def finish_pending(app_state: Any, factory_id: str) -> list[str]:
    """Start or finish this factory's server-owned spawns (the minute sweep calls this)."""
    from omnigent.server.factory import gating

    return await gating.release_ready(app_state, factory_id, trigger="sweep")


__all__ = [
    "ASGI_APP_ATTR",
    "LABEL_ENGINE_SPAWN",
    "MAX_SERVER_FAILURES",
    "PL_AGENT",
    "SpawnOutcome",
    "brief_mail_id",
    "brief_text",
    "create_body",
    "drain",
    "finish_pending",
    "in_flight",
    "launch",
    "lead_fallback",
    "mint_token",
    "needs_finishing",
    "spawn_mode",
    "spawned_notice",
]
