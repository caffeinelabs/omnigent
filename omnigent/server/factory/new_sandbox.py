"""Relaunch a factory member on a new sandbox.

Restart and "Relaunch with spec mode" keep a member's sandbox: when that
sandbox never came up, or is wedged, neither can help. This drops the
member's managed sandbox (when it has one) and provisions a fresh one for the
same session, so the project, charter, role, labels, conversation and the
server's records (tasks, PRs, decisions) all stay. Files that lived only in
the old sandbox are gone: once the new sandbox is up, the launch watch
(:mod:`.launch_watch`) tells the member to rebuild from the server
(:func:`send_follow_up`), or sends its brief again when it never got it.

The launch watch also runs it once on its own after a failed launch; owners
run it from the project menu or a decision (``new_sandbox``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Final

from omnigent.entities import Conversation
from omnigent.factory import (
    LABEL_ID,
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
    from omnigent.server.factory import definition, spawn

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
    The member's ``launch_tries`` label counts this launch; the launch watch
    sends the follow-up text once the sandbox is up.

    :param by: Who asked, e.g. ``"alice@example.com (owner)"`` or ``"server"``.
    :param auto: The launch watch retried a failed launch on its own.
    :param note: A human note the member reads with the ``NEW SANDBOX`` text
        (kept in the event until then).
    :param reason: Why, e.g. the failed launch's error (logged in the event).
    :returns: The :class:`NewSandbox`.
    :raises CockpitError: When it cannot run now (see :func:`check`).
    """
    from omnigent.server.auth import RESERVED_USER_LOCAL
    from omnigent.server.routes._auth_helpers import get_session_owner_id
    from omnigent.server.routes._sessions.orchestration import _kick_managed_reprovision

    await check(app_state, conv)
    managed = _managed(app_state)
    assert managed is not None  # check() refused otherwise
    host_store, sandbox_config, tracker = managed
    deps = factory_deps(app_state)
    store = deps.conversation_store
    owner = await asyncio.to_thread(get_session_owner_id, conv.id, deps.permission_store)
    old_host_id = await _drop_old_sandbox(app_state, conv)
    fresh = await asyncio.to_thread(store.get_conversation, conv.id) or conv
    # A concurrent relaunch (the watch's retry, an owner's click) may have begun
    # meanwhile; the check and the claim run with no await between them.
    if launch_in_flight(app_state, conv.id):
        raise CockpitError(409, "launch_in_flight", "a new sandbox is already starting")
    _kick_managed_reprovision(
        session_id=conv.id,
        conv=fresh,
        owner=owner or RESERVED_USER_LOCAL,
        sandbox_config=sandbox_config,
        tracker=tracker,
        conversation_store=store,
        host_store=host_store,
        app_state=app_state,
    )
    tries = launch_tries(fresh) + 1
    await asyncio.to_thread(store.set_labels, conv.id, {LABEL_LAUNCH_TRIES: str(tries)})
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
        summary=f'{who} relaunched "{title}" on a new sandbox (try {tries})',
        data=data,
    )
    _logger.info("factory: %s relaunched %s on a new sandbox (try %d)", who, conv.id, tries)
    return NewSandbox(session_id=conv.id, tries=tries, old_host_id=old_host_id)


__all__ = [
    "BRIEF_DELIVERED",
    "BRIEF_NONE",
    "BRIEF_PENDING",
    "EVENT_KIND",
    "TEXT_LABEL",
    "NewSandbox",
    "brief_state",
    "check",
    "last_relaunch",
    "launch_in_flight",
    "launch_tries",
    "new_sandbox_text",
    "relaunch_on_new_sandbox",
    "send_follow_up",
]
