"""Deterministic human controls: one path for every cockpit lever.

A cockpit button never becomes a chat message to an LLM. :func:`apply_control`
writes the factory labels the supervisor reads (state, keep_previews,
heartbeat, cadence), queues a server-stamped ``[Factory control · …]`` text
through the mailer, records a ``control.<action>`` event and tells the root
lead with an info notice. :func:`plan_control` validates and authorizes first,
so callers (the decision answer route) can refuse before writing anything.

PR review feedback and "refresh now" live here too: both are human levers on
a tracked PR, and neither ever calls GitHub.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryEvent, FactoryPr
from omnigent.factory import (
    CADENCE_HEARTBEATS,
    CADENCES,
    LABEL_CADENCE,
    LABEL_HEARTBEAT_S,
    LABEL_ID,
    LABEL_KEEP_PREVIEWS,
    LABEL_PROJECT,
    LABEL_ROLE,
    LABEL_STATE,
    LABEL_SUPERVISOR,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    STATE_ACTIVE,
    STATE_DONE,
    STATE_PAUSED,
)
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE, LEVEL_READ, RESERVED_USER_LOCAL
from omnigent.server.factory import events, nudge
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.envelope import display_title, quote_spoofed_lines
from omnigent.server.factory.launch import spec_launch_args
from omnigent.server.factory.mailer import MailItem, get_mailer, new_mail_id
from omnigent.server.factory.membership import (
    factory_members,
    is_session_id,
    is_top_level,
    resolve_factory_id,
)
from omnigent.server.routes._auth_helpers import require_access
from omnigent.stores.factory_store import norm_id
from omnigent.util.session_lifecycle import is_session_closed

_logger = logging.getLogger(__name__)

ACTION_PAUSE: Final = "pause"
ACTION_RESUME: Final = "resume"
ACTION_STOP: Final = "stop"
ACTION_RESTART: Final = "restart"
ACTION_RELAUNCH: Final = "relaunch"
ACTION_CADENCE: Final = "cadence"
ACTION_PREVIEW: Final = "preview"
ACTION_ENV_STATUS: Final = "env_status"
CONTROL_ACTIONS: Final = (
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_STOP,
    ACTION_RESTART,
    ACTION_RELAUNCH,
    ACTION_CADENCE,
    ACTION_PREVIEW,
    ACTION_ENV_STATUS,
)

TARGET_FACTORY: Final = "factory"
TARGET_PROJECT_PREFIX: Final = "project:"
TARGET_MEMBER_PREFIX: Final = "member:"

PREVIEW_UP: Final = "up"
PREVIEW_DOWN: Final = "down"

NOTE_MAX_CHARS: Final = 2000
REVIEW_TEXT_MAX_CHARS: Final = 2000
REFRESH_INTERVAL_S: Final = 10.0
ACK_WINDOW_MS: Final = 24 * 3600 * 1000
"""How far back a reply looks for the control it acknowledges."""

VERDICT_APPROVED: Final = "approved"
VERDICT_CHANGES: Final = "changes_requested"
VERDICTS: Final = frozenset({VERDICT_APPROVED, VERDICT_CHANGES})

# The mail kind of control texts; their recipients answer with kind=reply.
_CONTROL_KIND = "request"
_NOTICE_KIND = "info"
_ACKED_KINDS = frozenset(
    {f"control.{a}" for a in CONTROL_ACTIONS if a != ACTION_CADENCE} | {"control.charter_update"}
)


class CockpitError(Exception):
    """
    A refused cockpit write, rendered as ``{"error": {code, message, field?}, ...extra}``.

    :param status_code: HTTP status, e.g. ``409``.
    :param code: Machine-readable code, e.g. ``"top_level_only"``.
    :param message: Human-readable detail.
    :param field: Offending body field, e.g. ``"projects[1].slug"``.
    :param extra: Top-level keys added to the body, e.g. ``retry_after``.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        field: str | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field = field
        self.extra = extra

    def body(self) -> dict[str, Any]:
        """:returns: The JSON response body."""
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        out: dict[str, Any] = {"error": error, **self.extra}
        if self.field is not None:
            error["field"] = self.field
            out["field"] = self.field
            out["message"] = self.message
        return out


# ── Shared helpers (decisions and start use them too) ───────────


def human_name(user_id: str | None, role: str | None = None) -> str:
    """
    The name server-stamped texts show for a human, e.g. ``"alice@example.com (operator)"``.

    :param user_id: Authenticated user id, or ``None`` in single-user mode.
    :param role: Their factory role, appended when given (see
        :func:`~omnigent.server.factory.people.human_label`).
    :returns: A one-line name without quotes or brackets; ``"human"`` when
        there is no named user.
    """
    if not user_id or user_id == RESERVED_USER_LOCAL:
        return "human"
    name = display_title(user_id, "human")
    return f"{name} ({role})" if role else name


async def human_label(deps: FactoryDeps, factory_id: str, user_id: str | None) -> str:
    """:returns: :func:`human_name` with the person's role once the factory has several."""
    from omnigent.server.factory.people import human_label as label

    return await asyncio.to_thread(label, deps, factory_id, user_id)


def clip(text: str, limit: int) -> str:
    """:returns: *text* on one line, cut to *limit* chars with an ellipsis."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def same_id(a: str | None, b: str | None) -> bool:
    """:returns: Whether two session/row ids name the same uuid (any spelling)."""
    if not a or not b:
        return False
    try:
        return norm_id(a) == norm_id(b)
    except ValueError:
        return a == b


def is_closed(conv: Conversation | None) -> bool:
    """:returns: Whether *conv* is missing, closed or archived (a done member is not)."""
    return conv is None or conv.archived or is_session_closed(conv.labels, conv.title)


def is_gone(conv: Conversation | None) -> bool:
    """:returns: Whether *conv* can no longer act: missing, closed, archived or done."""
    if conv is None or is_closed(conv):
        return True
    return is_top_level(conv) and conv.labels.get(LABEL_STATE) == STATE_DONE


async def get_conv(deps: FactoryDeps, session_id: str | None) -> Conversation | None:
    """:returns: The session row, or ``None`` for a missing or malformed id."""
    if not session_id or not is_session_id(session_id):
        return None
    return await asyncio.to_thread(deps.conversation_store.get_conversation, session_id)


async def require(deps: FactoryDeps, user_id: str | None, session_id: str, level: int) -> None:
    """Raise the route's 401/403/404 unless *user_id* holds *level* on *session_id*."""
    await require_access(
        user_id, session_id, level, deps.permission_store, deps.conversation_store
    )


async def load_factory_root(
    deps: FactoryDeps, user_id: str | None, factory_id: str
) -> Conversation:
    """
    Load a factory root the caller can read.

    :raises OmnigentError: 401/404 from the access check.
    :raises CockpitError: 404 ``not_a_factory`` when *factory_id* is no root.
    """
    if not is_session_id(factory_id):
        raise CockpitError(404, "not_a_factory", f"{factory_id!r} is not a factory")
    await require(deps, user_id, factory_id, LEVEL_READ)
    root = await get_conv(deps, factory_id)
    if root is None or not is_top_level(root) or not same_id(root.labels.get(LABEL_ID), root.id):
        raise CockpitError(404, "not_a_factory", f"{factory_id!r} is not a factory")
    return root


def _replacement_rank(conv: Conversation) -> tuple[bool, int]:
    return conv.labels.get(LABEL_STATE) != STATE_ACTIVE, -conv.created_at


def find_project_pl(
    deps: FactoryDeps, factory_id: str, slug: str | None, *, include_done: bool = False
) -> Conversation | None:
    """
    The project's current PL: the row's ``pl_session_id``, else by label.

    A replaced PL (done or closed) loses to a live one; among live ones an
    active PL wins, then the newest.

    :param deps: Factory stores.
    :param factory_id: Root session id.
    :param slug: Project slug.
    :param include_done: Also accept a stopped (done) PL, e.g. to resume it.
    :returns: The PL row, or ``None`` when the project has none that can act.
    """
    if not slug:
        return None
    gone = is_closed if include_done else is_gone
    store = deps.conversation_store
    project = deps.factory_store.get_project(factory_id, slug)
    if project is not None and project.pl_session_id:
        conv = store.get_conversation(project.pl_session_id)
        if conv is not None and not gone(conv):
            return conv
    candidates = [
        m
        for m in factory_members(factory_id, store)
        if m.labels.get(LABEL_PROJECT) == slug
        and m.labels.get(LABEL_ROLE) == ROLE_PROJECT_LEAD
        and not gone(m)
    ]
    candidates.sort(key=_replacement_rank)
    return candidates[0] if candidates else None


def send_text(
    app_state: Any,
    target_id: str,
    text: str,
    *,
    kind: str,
    restart: bool = False,
    mail_id: str | None = None,
) -> str:
    """
    Queue a server-originated message (no sender to bounce to).

    :param mail_id: A pre-assigned id (recorded before sending), else a new one.
    :returns: The mail id.
    """
    mail_id = mail_id or new_mail_id()
    get_mailer(app_state).submit(
        MailItem(
            mail_id=mail_id,
            sender_id=None,
            target_id=target_id,
            target_title=None,
            text=text,
            kind=kind,
            restart=restart,
        )
    )
    return mail_id


def _quoted_note(note: str | None) -> str:
    return f"\n{quote_spoofed_lines(note.strip())}" if note and note.strip() else ""


# ── Targets and plans ──────────────────────────────────────────


@dataclass(frozen=True)
class Target:
    """
    What a control acts on.

    :param kind: ``factory`` / ``project`` / ``member``.
    :param ref: The request's target string, e.g. ``"project:api-version"``.
    :param project: The project slug, when known.
    :param members: Top-level sessions acted on, in delivery order.
    """

    kind: str
    ref: str
    project: str | None
    members: tuple[Conversation, ...]


@dataclass
class ControlPlan:
    """
    A validated, authorized control ready for :func:`execute_control`.

    :param root: The factory root session.
    :param action: One of :data:`CONTROL_ACTIONS`.
    :param target: Resolved target.
    :param by: Human name for texts and events.
    :param note: Optional human note (≤ 2000 chars).
    :param options: Normalized action options.
    :param recipients: Members whose labels change and/or who get the text.
    :param pr: The PR of a ``preview`` control.
    :param launch_args: Spec launch args per member id (``relaunch``).
    :param mail_ids: Pre-assigned mail ids per member id (others get new ones).
    :param root_reply_to: Where the root replies (``supervisor`` / ``peer_lead``);
        ``None`` before a second lead exists, so its next post of any kind acks.
    """

    root: Conversation
    action: str
    target: Target
    by: str
    note: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    recipients: list[Conversation] = field(default_factory=list)
    pr: FactoryPr | None = None
    launch_args: dict[str, list[str]] = field(default_factory=dict)
    mail_ids: dict[str, str] = field(default_factory=dict)
    root_reply_to: str | None = None

    @property
    def factory_id(self) -> str:
        """:returns: The root session id."""
        return self.root.id


@dataclass(frozen=True)
class ControlResult:
    """
    What :func:`execute_control` did.

    :param event: The ``control.<action>`` event (``None`` if it could not be stored).
    :param mail_ids: Mail queued per recipient id, in delivery order.
    :param notice_mail_id: The root's info notice, when one was sent.
    """

    event: FactoryEvent | None
    mail_ids: dict[str, str]
    notice_mail_id: str | None = None


def factory_order(members: Sequence[Conversation], root_id: str) -> list[Conversation]:
    """
    Order top-level members for a factory-wide action: PLs, other leads, the root.

    :param members: The factory's top-level members (oldest first).
    :param root_id: The root lead's id.
    :returns: A new list in pause order (resume uses the reverse).
    """

    def rank(conv: Conversation) -> int:
        if same_id(conv.id, root_id):
            return 2
        return 1 if conv.labels.get(LABEL_ROLE) == ROLE_LEAD else 0

    return sorted(members, key=rank)


async def resolve_target(
    deps: FactoryDeps, root: Conversation, ref: str, *, revive: bool = False
) -> Target:
    """
    Resolve ``factory`` / ``project:<slug>`` / ``member:<id>`` within *root*'s factory.

    :param revive: Let ``project:<slug>`` name a stopped PL when no live one exists.
    :raises CockpitError: 400 on a malformed or sub-agent target, 404 on an
        unknown one, 409 when a project has no PL to act on.
    """
    ref = (ref or "").strip()
    store = deps.conversation_store
    if ref == TARGET_FACTORY:
        members = await asyncio.to_thread(factory_members, root.id, store)
        live = [m for m in members if not (m.archived or is_session_closed(m.labels, m.title))]
        return Target("factory", ref, None, tuple(factory_order(live, root.id)))
    if ref.startswith(TARGET_PROJECT_PREFIX):
        slug = ref[len(TARGET_PROJECT_PREFIX) :].strip()
        pl = await asyncio.to_thread(find_project_pl, deps, root.id, slug)
        if pl is None and revive:
            pl = await asyncio.to_thread(find_project_pl, deps, root.id, slug, include_done=True)
        if pl is None:
            row = await asyncio.to_thread(deps.factory_store.get_project, root.id, slug)
            if row is None:
                raise CockpitError(404, "unknown_project", f"no project {slug!r} in this factory")
            raise CockpitError(409, "no_project_lead", f"project {slug!r} has no PL yet")
        return Target("project", ref, slug, (pl,))
    if ref.startswith(TARGET_MEMBER_PREFIX):
        member_id = ref[len(TARGET_MEMBER_PREFIX) :].strip()
        conv = await get_conv(deps, member_id)
        if conv is None or not same_id(
            await asyncio.to_thread(resolve_factory_id, conv, store), root.id
        ):
            raise CockpitError(404, "unknown_member", f"{member_id!r} is not in this factory")
        if not is_top_level(conv):
            raise CockpitError(
                400, "top_level_only", "controls act on leads and PLs; a PL manages its sub-agents"
            )
        return Target("member", ref, conv.labels.get(LABEL_PROJECT) or None, (conv,))
    raise CockpitError(
        400,
        "invalid_target",
        "target must be 'factory', 'project:<slug>' or 'member:<session id>'",
        field="target",
    )


def _bool_option(options: Mapping[str, Any], key: str, default: bool) -> bool:
    value = options.get(key)
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off")
    return bool(value)


async def _preview_pr(
    deps: FactoryDeps, root: Conversation, target: Target, options: Mapping[str, Any]
) -> FactoryPr:
    """The PR a ``preview`` control names: ``options.pr_id`` or ``options.pr`` (number)."""
    store = deps.factory_store
    pr: FactoryPr | None = None
    pr_id = options.get("pr_id")
    number = options.get("pr")
    if isinstance(pr_id, str) and pr_id.strip():
        try:
            pr = await asyncio.to_thread(store.get_pr, pr_id.strip())
        except ValueError:
            pr = None
    elif isinstance(number, str) and is_session_id(number):
        pr = await asyncio.to_thread(store.get_pr, number)
    elif number is not None:
        try:
            wanted = int(number)
        except (TypeError, ValueError):
            raise CockpitError(
                400, "invalid_option", "options.pr must be a PR number", field="options.pr"
            ) from None
        prs = await asyncio.to_thread(store.list_prs, root.id)
        matches = [
            p
            for p in prs
            if p.number == wanted and (target.project is None or p.project_slug == target.project)
        ]
        pr = matches[-1] if matches else None
    else:
        raise CockpitError(
            400, "invalid_option", "preview needs options.pr or options.pr_id", field="options.pr"
        )
    if pr is None or not same_id(pr.factory_id, root.id):
        raise CockpitError(404, "unknown_pr", "no such PR in this factory")
    if target.project is not None and pr.project_slug not in (None, target.project):
        raise CockpitError(
            400, "invalid_option", f"PR #{pr.number} belongs to project {pr.project_slug!r}"
        )
    return pr


def _recipients(action: str, target: Target) -> list[Conversation]:
    """
    Who a control acts on, in delivery order.

    Done members are skipped, except that resuming one project or member
    revives it. Factory-wide, pause skips paused members and resume reaches
    only paused ones, in reverse order.
    """
    wide = target.kind == "factory"

    def state(conv: Conversation) -> str | None:
        return conv.labels.get(LABEL_STATE)

    members = list(target.members)
    if action == ACTION_RESUME:
        if wide:
            return [m for m in reversed(members) if state(m) == STATE_PAUSED]
        return members
    live = [m for m in members if state(m) != STATE_DONE]
    if action == ACTION_PAUSE and wide:
        return [m for m in live if state(m) != STATE_PAUSED]
    return live


def _root_reply_address(deps: FactoryDeps, root: Conversation) -> str | None:
    """
    Where the root lead can reply: ``supervisor`` once mutual supervision set
    it, else ``peer_lead`` when a second lead exists, else ``None``.
    """
    store = deps.conversation_store
    supervisor = root.labels.get(LABEL_SUPERVISOR)
    if (
        supervisor
        and is_session_id(supervisor)
        and not is_gone(store.get_conversation(supervisor))
    ):
        return "supervisor"
    peers = [
        m
        for m in factory_members(root.id, store)
        if m.labels.get(LABEL_ROLE) == ROLE_LEAD and not same_id(m.id, root.id) and not is_gone(m)
    ]
    return "peer_lead" if peers else None


async def plan_control(
    app_state: Any,
    factory_id: str,
    *,
    user_id: str | None,
    action: str,
    target: str | None,
    note: str | None = None,
    options: Mapping[str, Any] | None = None,
) -> ControlPlan:
    """
    Validate and authorize a control without side effects.

    Access: EDIT on every member acted on (and the root for a factory-wide
    action); MANAGE (factory owners) to stop, restart and relaunch, and on the
    root to stop the whole factory. They run as server mail, so an owner needs
    no session ownership. Only ``cadence`` may omit *target* (it is factory-wide).

    :raises CockpitError: On an invalid action, target or option.
    :raises OmnigentError: 401/403/404 from the access checks.
    """
    deps = factory_deps(app_state)
    root = await load_factory_root(deps, user_id, factory_id)
    if action not in CONTROL_ACTIONS:
        raise CockpitError(
            400, "invalid_action", f"action must be one of {list(CONTROL_ACTIONS)}", field="action"
        )
    if note is not None and len(note) > NOTE_MAX_CHARS:
        raise CockpitError(
            400, "note_too_long", f"note exceeds {NOTE_MAX_CHARS} chars", field="note"
        )
    opts = dict(options or {})
    if not (target or "").strip():
        if action != ACTION_CADENCE:
            raise CockpitError(400, "invalid_target", "target is required", field="target")
        target = TARGET_FACTORY
    resolved = await resolve_target(deps, root, target or "", revive=action == ACTION_RESUME)
    by = await human_label(deps, root.id, user_id)
    plan = ControlPlan(root=root, action=action, target=resolved, by=by, note=note)

    if action in (ACTION_RESTART, ACTION_RELAUNCH) and resolved.kind == "factory":
        raise CockpitError(
            400, "invalid_target", f"{action} acts on one project or member", field="target"
        )
    if action == ACTION_CADENCE:
        if resolved.kind != "factory":
            raise CockpitError(
                400, "invalid_target", "cadence applies to the whole factory", field="target"
            )
        cadence = str(opts.get("cadence") or "").strip()
        if cadence not in CADENCES:
            raise CockpitError(
                400,
                "invalid_option",
                f"options.cadence must be one of {sorted(CADENCES)}",
                field="options.cadence",
            )
        opts = {"cadence": cadence}
    if action in (ACTION_PREVIEW, ACTION_ENV_STATUS):
        pl = resolved.members[0] if len(resolved.members) == 1 else None
        if pl is None or pl.labels.get(LABEL_ROLE) != ROLE_PROJECT_LEAD:
            raise CockpitError(
                400, "invalid_target", f"{action} targets a project (its PL)", field="target"
            )
        if not resolved.project:
            resolved = Target(
                resolved.kind, resolved.ref, pl.labels.get(LABEL_PROJECT), resolved.members
            )
            plan.target = resolved
    if action == ACTION_PREVIEW:
        mode = str(opts.get("mode") or PREVIEW_UP).strip().lower()
        if mode not in (PREVIEW_UP, PREVIEW_DOWN):
            raise CockpitError(
                400, "invalid_option", "options.mode must be 'up' or 'down'", field="options.mode"
            )
        plan.pr = await _preview_pr(deps, root, resolved, opts)
        if mode == PREVIEW_UP and plan.pr.state != "open":
            raise CockpitError(409, "pr_not_open", f"PR #{plan.pr.number} is {plan.pr.state}")
        opts = {"mode": mode, "pr_id": plan.pr.id, "pr": plan.pr.number}
    if action == ACTION_PAUSE:
        opts = {"keep_previews": _bool_option(opts, "keep_previews", True)}

    recipients = _recipients(action, resolved)
    if not recipients and action != ACTION_CADENCE:
        raise CockpitError(409, "nothing_to_do", f"no member left to {action}")
    plan.recipients = recipients
    plan.options = opts
    if any(same_id(m.id, root.id) for m in recipients):
        plan.root_reply_to = await asyncio.to_thread(_root_reply_address, deps, root)

    if resolved.kind == "factory":
        level = LEVEL_MANAGE if action == ACTION_STOP else LEVEL_EDIT
        await require(deps, user_id, root.id, level)
    owner_actions = (ACTION_STOP, ACTION_RESTART, ACTION_RELAUNCH)
    member_level = LEVEL_MANAGE if action in owner_actions else LEVEL_EDIT
    for member in recipients:
        await require(deps, user_id, member.id, member_level)

    if action == ACTION_RELAUNCH:
        for member in recipients:
            args = await asyncio.to_thread(spec_launch_args, member, deps)
            if args is None:
                raise CockpitError(
                    409,
                    "no_spec_launch_args",
                    f'the spec of "{display_title(member.title, member.id)}" '
                    "declares no launch args",
                )
            plan.launch_args[member.id] = args
    return plan


# ── Texts (spec §10) ───────────────────────────────────────────


def _reply_hint(plan: ControlPlan, conv: Conversation) -> str | None:
    to = plan.root_reply_to if same_id(conv.id, plan.root.id) else "parent"
    return f'reply once: sys_session_post(to="{to}", kind="reply")' if to else None


def _semi(reply: str | None) -> str:
    return f"; {reply}" if reply else ""


def _comma(reply: str | None) -> str:
    return f", {reply}" if reply else ""


def _and(reply: str | None) -> str:
    return f"{reply} and " if reply else ""


def control_header(action_label: str, by: str) -> str:
    """:returns: ``[Factory control · <ACTION> · by <human>]``."""
    return f"[Factory control · {action_label} · by {by}]"


def control_text(plan: ControlPlan, member: Conversation) -> str | None:
    """
    The ``[Factory control · …]`` text *member* receives, or ``None`` for none.

    :param plan: The control.
    :param member: One recipient.
    """
    pl = member.labels.get(LABEL_ROLE) == ROLE_PROJECT_LEAD
    reply = _reply_hint(plan, member)
    action = plan.action
    pr = plan.pr
    if action == ACTION_PAUSE:
        label = "PAUSE"
        if pl and plan.options.get("keep_previews", True):
            body = (
                "Pause the project: stop dispatching, cancel running ICs, take the dev env DOWN, "
                "keep your factory previews up for human review, checkpoint plan.md, then "
                f"{_and(reply)}wait for RESUME."
            )
        elif pl:
            body = (
                "Pause the project: stop dispatching, cancel running ICs, take the dev env DOWN, "
                "tear down your factory previews (env_keeper PREVIEW_DOWN pr=<n> reason=human "
                "for each), "
                f"checkpoint plan.md, then {_and(reply)}wait for RESUME."
            )
        else:
            body = (
                "Pause: stop spawning and dispatching, checkpoint your ledger, then "
                f"{_and(reply)}wait for RESUME."
            )
    elif action == ACTION_RESUME:
        label = "RESUME"
        if pl:
            body = (
                "Resume where you stopped: re-read plan.md, bring the dev env UP if you "
                f"need it, continue{_semi(reply)}."
            )
        else:
            body = f"Resume where you stopped: re-read your ledger and continue{_semi(reply)}."
    elif action == ACTION_STOP:
        label = "STOP"
        if pl:
            body = (
                "Stop the project: cancel running ICs, env_keeper DOWN and PREVIEW_DOWN pr=<n> "
                "reason=human for every preview, leave the PRs open, post a final summary to your "
                f"lead{_comma(reply)}, then stay idle."
            )
        else:
            body = f"Stop: post a final summary{_comma(reply)}, then stay idle."
    elif action == ACTION_RESTART:
        label = "RESTART"
        body = (
            "You were restarted from the cockpit. Re-read your plan and continue where "
            f"you stopped{_semi(reply)}."
        )
    elif action == ACTION_RELAUNCH:
        label = "RELAUNCHED"
        args = " ".join(plan.launch_args.get(member.id, []))
        body = (
            f"You were relaunched with your spec's launch args ({args}). Re-read your plan and "
            f"continue where you stopped{_semi(reply)}."
        )
    elif action == ACTION_PREVIEW and pr is not None:
        if plan.options.get("mode") == PREVIEW_DOWN:
            label = f"PREVIEW_DOWN pr=#{pr.number}"
            body = (
                f"Take down the factory preview of PR #{pr.number}: send env_keeper PREVIEW_DOWN "
                f"pr={pr.number} reason=human. Its demo stays in the cockpit{_semi(reply)}."
            )
        else:
            label = f"PREVIEW pr=#{pr.number}"
            head = f" head={pr.head_sha}" if pr.head_sha else ""
            at = f"its head {pr.head_sha[:7]}" if pr.head_sha else "its current head"
            body = (
                f"Deploy the factory preview of PR #{pr.number} at {at}: send env_keeper PREVIEW "
                f"pr={pr.number}{head} (it redeploys when one exists); if that head has no "
                f"demo yet, dispatch the demo once the preview is ready{_semi(reply)}."
            )
    elif action == ACTION_ENV_STATUS:
        label = "ENV STATUS"
        body = (
            "Report env status: ask env_keeper STATUS (dev env) and PREVIEW_STATUS, make "
            f"sure each is reported with sys_factory_env, then {reply or 'report'} with one line."
        )
    else:
        return None
    return f"{control_header(label, plan.by)} {body}{_quoted_note(plan.note)}"


def describe_target(plan: ControlPlan) -> str:
    """:returns: e.g. ``project api-version``, ``"PL · api-version"`` or ``the factory``."""
    target = plan.target
    if target.kind == "factory":
        return "the factory"
    if target.kind == "project":
        return f"project {target.project}"
    member = target.members[0]
    return f'"{display_title(member.title, member.id)}"'


def control_summary(plan: ControlPlan) -> str:
    """:returns: e.g. ``alice paused project api-version`` (event summary and notice text)."""
    what = describe_target(plan)
    action = plan.action
    if action == ACTION_PREVIEW and plan.pr is not None:
        if plan.options.get("mode") == PREVIEW_DOWN:
            return f"{plan.by} stopped the preview of PR #{plan.pr.number} in {what}"
        return f"{plan.by} previewed PR #{plan.pr.number} in {what}"
    if action == ACTION_CADENCE:
        return f"{plan.by} updated the cadence of {what} to {plan.options['cadence']}"
    if action == ACTION_ENV_STATUS:
        return f"{plan.by} requested env status of {what}"
    verb = {
        ACTION_PAUSE: "paused",
        ACTION_RESUME: "resumed",
        ACTION_STOP: "stopped",
        ACTION_RESTART: "restarted",
        ACTION_RELAUNCH: "relaunched",
    }[action]
    return f"{plan.by} {verb} {what}"


def notice_text(summary: str, note: str | None) -> str:
    """:returns: ``[Factory notice · <summary> via cockpit]`` plus the quoted note."""
    return f"[Factory notice · {summary} via cockpit]{_quoted_note(note)}"


# ── Execution ──────────────────────────────────────────────────


def _labels_for(plan: ControlPlan, member: Conversation) -> dict[str, str]:
    action = plan.action
    if action == ACTION_PAUSE:
        labels = {LABEL_STATE: STATE_PAUSED}
        if member.labels.get(LABEL_ROLE) == ROLE_PROJECT_LEAD:
            labels[LABEL_KEEP_PREVIEWS] = "1" if plan.options.get("keep_previews", True) else "0"
        return labels
    if action == ACTION_RESUME:
        return {LABEL_STATE: STATE_ACTIVE}
    if action == ACTION_STOP:
        return {LABEL_STATE: STATE_DONE}
    if action == ACTION_CADENCE:
        role = member.labels.get(LABEL_ROLE)
        beat = CADENCE_HEARTBEATS[plan.options["cadence"]].get(role or "")
        return {LABEL_HEARTBEAT_S: str(beat)} if beat else {}
    return {}


async def execute_control(app_state: Any, plan: ControlPlan) -> ControlResult:
    """
    Apply a planned control: labels, launch args, texts, audit event, root notice.

    :param app_state: ``app.state`` carrying the factory deps and mailer.
    :param plan: From :func:`plan_control`.
    :returns: The event and the queued mail ids.
    """
    deps = factory_deps(app_state)
    store = deps.conversation_store
    mail_ids: dict[str, str] = {}
    for member in plan.recipients:
        labels = _labels_for(plan, member)
        if labels:
            await asyncio.to_thread(store.set_labels, member.id, labels)
        if plan.action == ACTION_RELAUNCH:
            await asyncio.to_thread(
                store.update_conversation,
                member.id,
                terminal_launch_args=plan.launch_args[member.id],
            )
        text = control_text(plan, member)
        if text is not None:
            restart = plan.action in (ACTION_RESTART, ACTION_RELAUNCH)
            mail_ids[member.id] = send_text(
                app_state,
                member.id,
                text,
                kind=_CONTROL_KIND,
                restart=restart,
                mail_id=plan.mail_ids.get(member.id),
            )
    if plan.action == ACTION_CADENCE:
        await asyncio.to_thread(
            store.set_labels, plan.root.id, {LABEL_CADENCE: plan.options["cadence"]}
        )

    summary = control_summary(plan)
    notice_mail_id: str | None = None
    if not any(same_id(m, plan.root.id) for m in mail_ids):
        notice_mail_id = send_text(
            app_state, plan.root.id, notice_text(summary, plan.note), kind=_NOTICE_KIND
        )
    single = plan.recipients[0].id if len(plan.recipients) == 1 else None
    data: dict[str, Any] = {
        "from": plan.by,
        "by": plan.by,
        "note": plan.note,
        "action": plan.action,
        "target": plan.target.ref,
        "options": plan.options,
        "recipients": [norm_id(m.id) for m in plan.recipients],
        "mail_ids": mail_ids,
    }
    if plan.root_reply_to is None and any(same_id(m, plan.root.id) for m in mail_ids):
        data["ack_on_any_post"] = [norm_id(plan.root.id)]
    subject = f"pr:{plan.pr.repo}#{plan.pr.number}" if plan.pr is not None else plan.target.ref
    event = await events.emit(
        app_state,
        plan.factory_id,
        f"control.{plan.action}",
        severity="notice",
        project=plan.target.project,
        to_id=single,
        subject=subject,
        summary=summary,
        data=data,
    )
    return ControlResult(event=event, mail_ids=mail_ids, notice_mail_id=notice_mail_id)


async def apply_control(
    app_state: Any,
    factory_id: str,
    *,
    user_id: str | None,
    action: str,
    target: str | None,
    note: str | None = None,
    options: Mapping[str, Any] | None = None,
) -> ControlResult:
    """
    Plan, authorize and run one control (``POST /v1/factory/factories/{fid}/control``).

    :raises CockpitError: On an invalid action, target or option.
    :raises OmnigentError: 401/403/404 from the access checks.
    """
    plan = await plan_control(
        app_state,
        factory_id,
        user_id=user_id,
        action=action,
        target=target,
        note=note,
        options=options,
    )
    return await execute_control(app_state, plan)


# ── Ack detection ──────────────────────────────────────────────


async def _ack_on_reply(app_state: Any, event: FactoryEvent) -> None:
    """
    Emit ``control.ack`` when a control's recipient posts its reply.

    A ``message.queued`` of kind ``reply`` from a session acknowledges every
    control sent to that session in the last :data:`ACK_WINDOW_MS` that it
    has not acknowledged yet. A root lead with no one to reply to yet acks
    with its next post of any kind (``ack_on_any_post``).
    """
    if event.kind != "message.queued":
        return
    data = event.data or {}
    sender = event.from_id or data.get("sender_id")
    if not isinstance(sender, str) or not is_session_id(sender):
        return
    sender = norm_id(sender)
    is_reply = data.get("kind") == "reply"
    if not is_reply and not same_id(sender, event.factory_id):
        return
    deps = factory_deps(app_state)
    found = await asyncio.to_thread(
        deps.factory_store.find_events,
        event.factory_id,
        "control.*",
        event.ts - ACK_WINDOW_MS,
    )
    acked = {
        ((e.data or {}).get("control_event_id"), e.from_id)
        for e in found
        if e.kind == "control.ack"
    }
    for ctl in found:
        ctl_data = ctl.data or {}
        if (
            ctl.kind not in _ACKED_KINDS
            or ctl.ts > event.ts
            or sender not in (ctl_data.get("recipients") or [])
            or (ctl.id, sender) in acked
            or (not is_reply and sender not in (ctl_data.get("ack_on_any_post") or []))
        ):
            continue
        action = ctl.kind.removeprefix("control.")
        await events.emit(
            app_state,
            event.factory_id,
            "control.ack",
            project=ctl.project_slug,
            from_id=sender,
            subject=ctl.subject,
            summary=f"{action} acknowledged",
            data={"control_event_id": ctl.id, "action": action, "mail_id": data.get("mail_id")},
        )


events.add_listener(_ack_on_reply)


# ── PR review feedback and refresh ─────────────────────────────


class RefreshLimiter:
    """
    At most one "refresh now" per PR per :data:`REFRESH_INTERVAL_S` (in memory).

    :param clock: Monotonic seconds (injectable for tests).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}

    def acquire(self, key: str) -> float | None:
        """
        :returns: ``None`` when allowed (and recorded), else seconds to wait.
        """
        now = self.clock()
        with self._lock:
            for stale in [k for k, at in self._last.items() if now - at >= REFRESH_INTERVAL_S]:
                del self._last[stale]
            last = self._last.get(key)
            if last is not None:
                return REFRESH_INTERVAL_S - (now - last)
            self._last[key] = now
            return None

    def reset(self) -> None:
        """Forget every refresh (tests)."""
        with self._lock:
            self._last.clear()


refresh_limiter = RefreshLimiter()


async def _load_pr(
    deps: FactoryDeps, user_id: str | None, pr_id: str
) -> tuple[FactoryPr, Conversation]:
    try:
        pr = await asyncio.to_thread(deps.factory_store.get_pr, pr_id)
    except ValueError:
        pr = None
    if pr is None:
        raise CockpitError(404, "unknown_pr", f"no tracked PR {pr_id!r}")
    try:
        root = await load_factory_root(deps, user_id, pr.factory_id)
    except CockpitError:
        raise CockpitError(404, "unknown_pr", f"no tracked PR {pr_id!r}") from None
    return pr, root


async def request_pr_refresh(app_state: Any, pr_id: str, *, user_id: str | None) -> FactoryPr:
    """
    Make the GitHub poller refresh one PR now ("I merged it").

    Needs EDIT on the factory root; one request per PR per 10 s.

    :raises CockpitError: 404 unknown PR; 429 ``rate_limited`` with ``retry_after``.
    """
    deps = factory_deps(app_state)
    pr, root = await _load_pr(deps, user_id, pr_id)
    await require(deps, user_id, root.id, LEVEL_EDIT)
    wait = refresh_limiter.acquire(pr.id)
    if wait is not None:
        retry_after = max(1, math.ceil(wait))
        raise CockpitError(
            429,
            "rate_limited",
            f"PR #{pr.number} was refreshed moments ago; retry in {retry_after}s",
            retry_after=retry_after,
        )
    updated = await asyncio.to_thread(deps.factory_store.update_pr, pr.id, last_polled_at=None)
    nudge.nudge("prs", pr.id)
    return updated or pr


def review_text(pr: FactoryPr, verdict: str, by: str, text: str | None) -> str:
    """:returns: The ``[Factory review · …]`` text the PL receives (spec §10)."""
    sha7 = pr.head_sha[:7] if pr.head_sha else "unknown"
    note = quote_spoofed_lines(text.strip()) if text and text.strip() else ""
    if verdict == VERDICT_APPROVED:
        head = (
            f"[Factory review · APPROVED PR #{pr.number} @{sha7} by {by} (cockpit)] "
            "The human will merge on GitHub; keep the preview up."
        )
        return f"{head}\n{note}" if note else head
    head = f"[Factory review · CHANGES REQUESTED on PR #{pr.number} @{sha7} by {by} (cockpit)]"
    return f"{head}\n{note}" if note else head


async def submit_pr_feedback(
    app_state: Any,
    pr_id: str,
    *,
    user_id: str | None,
    verdict: str,
    text: str | None = None,
) -> tuple[FactoryPr, str]:
    """
    Record a human review from the cockpit and tell the PR's PL. Never calls GitHub.

    Needs EDIT on the PL session.

    :returns: ``(updated PR, mail id)``.
    :raises CockpitError: 400 on a bad verdict or text, 404 unknown PR, 409
        when the PR is not open or its project has no PL.
    """
    deps = factory_deps(app_state)
    if verdict not in VERDICTS:
        raise CockpitError(
            400, "invalid_verdict", f"verdict must be one of {sorted(VERDICTS)}", field="verdict"
        )
    if text is not None and len(text) > REVIEW_TEXT_MAX_CHARS:
        raise CockpitError(
            400, "text_too_long", f"text exceeds {REVIEW_TEXT_MAX_CHARS} chars", field="text"
        )
    pr, root = await _load_pr(deps, user_id, pr_id)
    pl = await asyncio.to_thread(find_project_pl, deps, root.id, pr.project_slug)
    if pl is None:
        raise CockpitError(409, "no_project_lead", f"PR #{pr.number} has no project lead to tell")
    await require(deps, user_id, pl.id, LEVEL_EDIT)
    if pr.state != "open":
        raise CockpitError(409, "pr_not_open", f"PR #{pr.number} is {pr.state}")
    by = await human_label(deps, root.id, user_id)
    review = {
        "verdict": verdict,
        "text": text.strip() if text and text.strip() else None,
        "by": by,
        "at": int(time.time()),
        "head_sha": pr.head_sha or None,
        "source": "cockpit",
    }
    updated = await asyncio.to_thread(deps.factory_store.update_pr, pr.id, human_review=review)
    mail_id = send_text(app_state, pl.id, review_text(pr, verdict, by, text), kind=_CONTROL_KIND)
    word = "approved" if verdict == VERDICT_APPROVED else "requested changes on"
    await events.emit(
        app_state,
        root.id,
        "pr.human_review",
        severity="notice",
        project=pr.project_slug,
        to_id=pl.id,
        subject=f"pr:{pr.repo}#{pr.number}",
        summary=f"{by} {word} PR #{pr.number} @{(pr.head_sha or 'unknown')[:7]} (cockpit)",
        data={
            "verdict": verdict,
            "from": by,
            "by": by,
            "head_sha": pr.head_sha or None,
            "text": clip(text, 300) if text else None,
            "mail_id": mail_id,
            "source": "cockpit",
        },
    )
    return updated or pr, mail_id


__all__ = [
    "ACTION_CADENCE",
    "ACTION_ENV_STATUS",
    "ACTION_PAUSE",
    "ACTION_PREVIEW",
    "ACTION_RELAUNCH",
    "ACTION_RESTART",
    "ACTION_RESUME",
    "ACTION_STOP",
    "CONTROL_ACTIONS",
    "CockpitError",
    "ControlPlan",
    "ControlResult",
    "Target",
    "apply_control",
    "execute_control",
    "factory_order",
    "find_project_pl",
    "human_label",
    "human_name",
    "load_factory_root",
    "plan_control",
    "refresh_limiter",
    "request_pr_refresh",
    "review_text",
    "send_text",
    "submit_pr_feedback",
]
