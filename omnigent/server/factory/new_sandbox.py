"""Relaunch a factory member on a new sandbox.

Restart and "Relaunch with spec mode" keep a member's sandbox: when that
sandbox never came up, or is wedged, neither can help. This drops the
member's managed sandbox (when it has one) and provisions a fresh one for the
same session, so the project, charter, role, labels, conversation and the
server's records (tasks, PRs, decisions) all stay. Files that lived only in
the old sandbox are gone: once the new sandbox is up, the launch watch
(:mod:`.launch_watch`) tells the member to rebuild from the server
(:func:`send_follow_up`), or sends its brief again when it never got it.

The launch watch also runs it once on its own after a first launch failed
(the member has no sandbox to lose); owners run it from the project menu or a
decision (``new_sandbox``). A member whose own sandbox failed to wake is woken
again instead (:func:`wake_same_sandbox`): the server never deletes a sandbox
on its own.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Final

from omnigent.entities import Conversation
from omnigent.factory import (
    LABEL_ID,
    LABEL_LAUNCH_AUTO,
    LABEL_LAUNCH_NEW,
    LABEL_LAUNCH_TRIES,
    LABEL_PROJECT,
    LABEL_ROLE,
    ROLE_PROJECT_LEAD,
)
from omnigent.server.factory import events
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError, control_header, same_id, send_text
from omnigent.server.factory.envelope import display_title, quote_spoofed_lines
from omnigent.server.factory.mail_events import KIND_BOUNCED, KIND_DELIVERED, KIND_QUEUED
from omnigent.server.factory.mailer import MailItem, get_mailer, new_mail_id

_logger = logging.getLogger(__name__)

TEXT_LABEL: Final = "NEW SANDBOX"
"""The ``[Factory control · …]`` label of the text a relaunched member gets."""
EVENT_KIND: Final = "member.new_sandbox"
WAKE_EVENT_KIND: Final = "member.wake_retried"

_NOT_STARTED: Final = "the server could not start a new sandbox"

BRIEF_DELIVERED: Final = "delivered"
BRIEF_PENDING: Final = "pending"
BRIEF_NONE: Final = "none"


@dataclass(frozen=True)
class NewSandbox:
    """
    What a relaunch on a new sandbox did.

    :param session_id: The member.
    :param tries: Sandbox launches of its current start, this one included.
    :param old_host_id: The managed host it was bound to before, if any.
    """

    session_id: str
    tries: int
    old_host_id: str | None


def launch_tries(conv: Conversation) -> int:
    """:returns: Sandbox launches of *conv*'s current start (``1`` without the label)."""
    raw = (conv.labels.get(LABEL_LAUNCH_TRIES) or "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else 1


def new_sandbox_since(conv: Conversation) -> float | None:
    """:returns: When the current start's latest new sandbox began (epoch s), else ``None``."""
    raw = (conv.labels.get(LABEL_LAUNCH_NEW) or "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return 0.0  # set, but not a time: still a new sandbox


def server_retried(conv: Conversation) -> bool:
    """:returns: Whether the server already retried *conv*'s current start on its own."""
    return conv.labels.get(LABEL_LAUNCH_AUTO) == "1"


def _launch_failed(session_id: str) -> bool:
    """:returns: Whether *session_id*'s last managed launch failed (in this process)."""
    from omnigent.server.routes._sessions.common import _session_sandbox_status_cache

    status = _session_sandbox_status_cache.get(session_id)
    return status is not None and status.stage == "failed"


def _next_try(conv: Conversation, failed: bool) -> int:
    """
    :param failed: Its last launch failed.
    :returns: The number of the launch about to start: the next try of a start
        that has not come up, or ``1`` for a member whose sandbox is up.
    """
    if LABEL_LAUNCH_TRIES in conv.labels:
        return launch_tries(conv) + 1
    return 2 if failed else 1


def _try_suffix(tries: int) -> str:
    return f" (try {tries})" if tries > 1 else ""


def launch_in_flight(app_state: Any, session_id: str) -> bool:
    """:returns: Whether a managed launch of *session_id* runs in this process now."""
    tracker = getattr(app_state, "managed_launches", None)
    launch = tracker.get(session_id) if tracker is not None else None
    return launch is not None and not launch.settled.is_set()


def _managed(app_state: Any) -> tuple[Any, Any, Any] | None:
    """:returns: ``(host_store, sandbox_config, tracker)``; ``None`` without managed sandboxes."""
    host_store = getattr(app_state, "host_store", None)
    config = getattr(app_state, "sandbox_config", None)
    tracker = getattr(app_state, "managed_launches", None)
    if host_store is None or tracker is None or config is None:
        return None
    if not getattr(config, "managed_launch_supported", False):
        return None
    return host_store, config, tracker


async def check(app_state: Any, conv: Conversation) -> None:
    """
    Refuse a relaunch on a new sandbox that cannot run now.

    :raises CockpitError: 409 ``no_managed_sandboxes`` (this server launches
        none), ``launch_in_flight`` (its sandbox is starting) or
        ``not_managed`` (it runs on a host the server did not launch).
    """
    from omnigent.server.managed_host_isolation import is_managed_host

    managed = _managed(app_state)
    if managed is None:
        raise CockpitError(
            409, "no_managed_sandboxes", "this server does not launch sandboxes for its agents"
        )
    title = display_title(conv.title, conv.id)
    if launch_in_flight(app_state, conv.id):
        raise CockpitError(
            409,
            "launch_in_flight",
            f'the sandbox of "{title}" is starting; wait until it is up or fails',
        )
    if conv.host_id:
        host = await asyncio.to_thread(managed[0].get_host, conv.host_id)
        if host is not None and not is_managed_host(host):
            raise CockpitError(
                409,
                "not_managed",
                f'"{title}" runs on a host the server did not launch; restart it instead',
            )


def brief_state(deps: FactoryDeps, factory_id: str, conv: Conversation) -> str:
    """
    Whether *conv* got its brief: ``delivered``, ``pending`` (queued, not
    delivered or bounced yet) or ``none`` (never sent, or bounced).
    """
    since_ms = max(0, int(conv.created_at) - 60) * 1000
    rows = deps.factory_store.find_events(
        factory_id, [KIND_QUEUED, KIND_DELIVERED, KIND_BOUNCED], since_ms
    )
    briefs = [
        e for e in rows if same_id(e.to_id, conv.id) and (e.data or {}).get("kind") == "brief"
    ]
    if any(e.kind == KIND_DELIVERED for e in briefs):
        return BRIEF_DELIVERED
    closed = {e.subject for e in briefs if e.kind == KIND_BOUNCED}
    if any(e.kind == KIND_QUEUED and e.subject not in closed for e in briefs):
        return BRIEF_PENDING
    return BRIEF_NONE


def new_sandbox_text(conv: Conversation, by: str, note: str | None = None) -> str:
    """:returns: The ``[Factory control · NEW SANDBOX · by …]`` text *conv* reads first."""
    if conv.labels.get(LABEL_ROLE) == ROLE_PROJECT_LEAD:
        body = (
            "You run on a new sandbox: files, worktrees, the dev env and previews of the old "
            "one are gone. The server kept your charter, tasks, PRs and decisions. Load your "
            'playbook, rebuild plan.md from sys_factory_task(op="ready") and '
            "sys_factory_roster(), re-drive the tasks that were running, bring the dev env UP "
            "if ICs need it and run PREVIEW for each open PR; reply once: "
            'sys_session_post(to="parent", kind="reply").'
        )
    else:
        body = (
            "You run on a new sandbox: files of the old one are gone. The server kept the "
            "factory's projects, tasks, PRs and decisions. Load your playbook and rebuild your "
            "ledger from sys_factory_roster() and sys_factory_projects()."
        )
    quoted = f"\n{quote_spoofed_lines(note.strip())}" if note and note.strip() else ""
    return f"{control_header(TEXT_LABEL, by)} {body}{quoted}"


async def send_follow_up(
    app_state: Any,
    conv: Conversation,
    *,
    by: str,
    note: str | None = None,
    wait_for_brief: bool = True,
) -> str | None:
    """
    Queue what *conv* reads now that its new sandbox is up.

    A PL that never got its brief gets it again; any other member gets the
    ``NEW SANDBOX`` text.

    :param by: Who relaunched it, named in the text.
    :param note: The owner's note, read after the text.
    :param wait_for_brief: Leave a brief still on its way to arrive (else send it again).
    :returns: The queued mail id, or ``None`` while the brief is on its way.
    """
    from omnigent.server.factory import definition, product_profile, spawn, team

    deps = factory_deps(app_state)
    factory_id = conv.labels.get(LABEL_ID)
    slug = conv.labels.get(LABEL_PROJECT)
    if conv.labels.get(LABEL_ROLE) == ROLE_PROJECT_LEAD and factory_id and slug:
        state = await asyncio.to_thread(brief_state, deps, factory_id, conv)
        if state == BRIEF_PENDING and wait_for_brief:
            return None
        project = await asyncio.to_thread(deps.factory_store.get_project, factory_id, slug)
        if state != BRIEF_DELIVERED and project is not None:
            inherited = await asyncio.to_thread(spawn._inherited_prs, deps, factory_id, slug)
            rules = await asyncio.to_thread(
                definition.rule_texts, deps, factory_id, ROLE_PROJECT_LEAD, slug
            )
            delivery = await asyncio.to_thread(product_profile.delivery, deps, factory_id)
            team_block = await asyncio.to_thread(team.charter_team, deps, factory_id, project)
            mail_id = new_mail_id()
            get_mailer(app_state).submit(
                MailItem(
                    mail_id=mail_id,
                    sender_id=None,
                    target_id=conv.id,
                    target_title=conv.title,
                    text=spawn.brief_text(
                        project,
                        by=by,
                        via="a new sandbox",
                        mail_id=mail_id,
                        inherited=inherited,
                        rules=rules,
                        delivery=delivery,
                        team=team_block,
                    ),
                    kind="brief",
                    factory_id=factory_id,
                    project=slug,
                )
            )
            return mail_id
    return send_text(app_state, conv.id, new_sandbox_text(conv, by, note), kind="request")


def last_relaunch(deps: FactoryDeps, conv: Conversation) -> dict[str, Any]:
    """:returns: The newest ``member.new_sandbox`` data of *conv* (``by``, ``note``), or ``{}``."""
    factory_id = conv.labels.get(LABEL_ID)
    if not factory_id:
        return {}
    rows = deps.factory_store.find_events(
        factory_id,
        EVENT_KIND,
        max(0, int(conv.created_at) - 60) * 1000,
        f"member:{conv.id}",
        limit=1,
        newest_first=True,
    )
    return dict(rows[0].data or {}) if rows else {}


async def _drop_old_sandbox(app_state: Any, conv: Conversation) -> str | None:
    """
    Tear down the managed sandbox *conv* owns and unbind it from its host.

    A sandbox *conv* only borrows (its root's, another session's) is left
    running; the binding is cleared either way.

    :returns: The host id it was bound to, or ``None``.
    """
    from omnigent.server.managed_host_isolation import session_owns_managed_host
    from omnigent.server.managed_hosts import terminate_managed_host

    old = conv.host_id
    if not old:
        return None
    deps = factory_deps(app_state)
    host_store = app_state.host_store
    host = await asyncio.to_thread(host_store.get_host, old)
    if host is not None and await asyncio.to_thread(
        session_owns_managed_host,
        conv,
        host,
        deps.conversation_store,
        scheduled_task_store=getattr(app_state, "scheduled_task_store", None),
    ):
        await terminate_managed_host(host, host_store, app_state.sandbox_config)
    await asyncio.to_thread(deps.conversation_store.clear_host_binding, conv.id)
    return old


async def relaunch_on_new_sandbox(
    app_state: Any,
    conv: Conversation,
    *,
    by: str,
    auto: bool = False,
    note: str | None = None,
    reason: str | None = None,
) -> NewSandbox:
    """
    Provision a fresh managed sandbox for *conv*, dropping the one it has.

    Runs in the background like a managed create: the call returns once the
    launch is scheduled (its progress shows as the member's sandbox stage).
    The launch is claimed before anything else, so a message arriving while
    the old sandbox is deleted waits for the new one instead of waking the
    old one. The member's ``launch_tries`` label counts this launch; the
    launch watch sends the follow-up text once the sandbox is up.

    :param by: Who asked, e.g. ``"alice@example.com (owner)"`` or ``"server"``.
    :param auto: The launch watch retried a failed first launch on its own;
        refused while *conv* is bound to a sandbox (the server never deletes one).
    :param note: A human note the member reads with the ``NEW SANDBOX`` text
        (kept in the event until then).
    :param reason: Why, e.g. the failed launch's error (logged in the event).
    :returns: The :class:`NewSandbox`.
    :raises CockpitError: When it cannot run now (see :func:`check`), or 409
        ``has_sandbox`` on an *auto* retry of a member that has a sandbox.
    """
    from omnigent.server.auth import RESERVED_USER_LOCAL
    from omnigent.server.routes._auth_helpers import get_session_owner_id
    from omnigent.server.routes._sessions.helpers import _publish_sandbox_status
    from omnigent.server.routes._sessions.orchestration import _kick_managed_reprovision

    await check(app_state, conv)
    managed = _managed(app_state)
    assert managed is not None  # check() refused otherwise
    host_store, sandbox_config, tracker = managed
    deps = factory_deps(app_state)
    store = deps.conversation_store
    if auto and conv.host_id and await asyncio.to_thread(host_store.get_host, conv.host_id):
        raise CockpitError(
            409, "has_sandbox", "it has a sandbox; the server does not replace one on its own"
        )
    # Claim the launch with no await since the check: a message from here on
    # waits for this launch instead of waking the sandbox being deleted.
    if launch_in_flight(app_state, conv.id):
        raise CockpitError(409, "launch_in_flight", "a new sandbox is already starting")
    # The watch retries only a launch that failed (or that a restart cut off).
    failed = auto or _launch_failed(conv.id)
    tracker.begin(conv.id)
    _publish_sandbox_status(conv.id, "provisioning")
    try:
        fresh = await asyncio.to_thread(store.get_conversation, conv.id) or conv
        tries = _next_try(fresh, failed)
        labels = {LABEL_LAUNCH_TRIES: str(tries), LABEL_LAUNCH_NEW: str(int(time.time()))}
        if auto:
            labels[LABEL_LAUNCH_AUTO] = "1"
        # Counted before the launch, so a failure of it is never counted as try 1 again.
        await asyncio.to_thread(store.set_labels, conv.id, labels)
        owner = await asyncio.to_thread(get_session_owner_id, conv.id, deps.permission_store)
        old_host_id = await _drop_old_sandbox(app_state, fresh)
        fresh = await asyncio.to_thread(store.get_conversation, conv.id) or fresh
        _kick_managed_reprovision(
            session_id=conv.id,
            conv=fresh,
            owner=owner or RESERVED_USER_LOCAL,
            sandbox_config=sandbox_config,
            tracker=tracker,
            conversation_store=store,
            host_store=host_store,
            app_state=app_state,
            claimed=True,
        )
    except BaseException:
        # Settle the claim: a parked message gets an answer and the watch sees the failure.
        _logger.warning("factory: relaunching %s on a new sandbox failed", conv.id, exc_info=True)
        tracker.fail(conv.id, _NOT_STARTED)
        _publish_sandbox_status(conv.id, "failed", _NOT_STARTED)
        raise
    title = display_title(conv.title, conv.id)
    who = "the server" if auto else by
    data: dict[str, Any] = {
        "tries": tries,
        "auto": auto,
        "by": by,
        "old_host_id": old_host_id,
    }
    if reason:
        data["reason"] = reason
    if note and note.strip():
        data["note"] = note.strip()
    await events.emit(
        app_state,
        conv.labels.get(LABEL_ID) or conv.id,
        EVENT_KIND,
        severity="notice",
        project=conv.labels.get(LABEL_PROJECT),
        to_id=conv.id,
        subject=f"member:{conv.id}",
        summary=f'{who} relaunched "{title}" on a new sandbox{_try_suffix(tries)}',
        data=data,
    )
    _logger.info("factory: %s relaunched %s on a new sandbox (try %d)", who, conv.id, tries)
    return NewSandbox(session_id=conv.id, tries=tries, old_host_id=old_host_id)


async def wake_same_sandbox(
    app_state: Any, conv: Conversation, *, reason: str | None = None
) -> bool:
    """
    Wake *conv*'s own sandbox again: the server's one retry of a failed wake.

    The same path a message takes to a sleeping sandbox (a wake in place, or a
    new generation of its host when the provider cannot resume), so its files
    stay. Counted in ``launch_tries`` before the wake starts.

    :param reason: Why, e.g. the failed wake's error (logged in the event).
    :returns: Whether a wake was started (``False`` when another launch began
        meanwhile, which then stands for the retry, or there is no sandbox).
    """
    from omnigent.server.managed_hosts import host_resume_supported
    from omnigent.server.routes._sessions import orchestration

    managed = _managed(app_state)
    if managed is None or not conv.host_id or launch_in_flight(app_state, conv.id):
        return False
    host_store, sandbox_config, tracker = managed
    host = await asyncio.to_thread(host_store.get_host, conv.host_id)
    if host is None or host.sandbox_provider is None:
        return False
    deps = factory_deps(app_state)
    store = deps.conversation_store
    tries = launch_tries(conv) + 1
    await asyncio.to_thread(
        store.set_labels, conv.id, {LABEL_LAUNCH_TRIES: str(tries), LABEL_LAUNCH_AUTO: "1"}
    )
    fresh = await asyncio.to_thread(store.get_conversation, conv.id) or conv
    if launch_in_flight(app_state, conv.id) or fresh.host_id != host.host_id:
        return False
    kick: dict[str, Any] = {
        "session_id": conv.id,
        "conv": fresh,
        "sandbox_config": sandbox_config,
        "tracker": tracker,
        "conversation_store": store,
        "host_store": host_store,
        "app_state": app_state,
    }
    if host_resume_supported(host, sandbox_config):
        orchestration._kick_managed_wake(**kick)
    else:
        orchestration._kick_managed_relaunch(host=host, **kick)
    title = display_title(conv.title, conv.id)
    data: dict[str, Any] = {"tries": tries, "auto": True, "host_id": host.host_id}
    if reason:
        data["reason"] = reason
    await events.emit(
        app_state,
        conv.labels.get(LABEL_ID) or conv.id,
        WAKE_EVENT_KIND,
        severity="notice",
        project=conv.labels.get(LABEL_PROJECT),
        to_id=conv.id,
        subject=f"member:{conv.id}",
        summary=f'the server is waking "{title}" again on its own sandbox (try {tries})',
        data=data,
    )
    _logger.info("factory: waking %s again on its own sandbox (try %d)", conv.id, tries)
    return True


__all__ = [
    "BRIEF_DELIVERED",
    "BRIEF_NONE",
    "BRIEF_PENDING",
    "EVENT_KIND",
    "TEXT_LABEL",
    "WAKE_EVENT_KIND",
    "NewSandbox",
    "brief_state",
    "check",
    "last_relaunch",
    "launch_in_flight",
    "launch_tries",
    "new_sandbox_since",
    "new_sandbox_text",
    "relaunch_on_new_sandbox",
    "send_follow_up",
    "server_retried",
    "wake_same_sandbox",
]
