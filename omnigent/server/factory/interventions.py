"""Interventions: an operator's levers on any agent, sub-agents included.

``POST /v1/factory/factories/{fid}/control`` hands these actions here (the
existing controls keep ``top_level_only`` for pause, stop, restart and
relaunch):

============  =============================  =====================================
action        target                         mechanism
============  =============================  =====================================
message       ``member:<any session>``       attributed, spoof-quoted user message
interrupt     ``member:<any session>``       the session interrupt path
redispatch    ``task:<slug>/<key>``, ``T-n``  interrupt, then PL ``REDISPATCH``
reassign      ``task:…`` + ``options.vendor``  interrupt, then PL ``REASSIGN``
rerun_demo    ``pr:<id|number>``              PL ``DEMO pr=#<n>``
rereview      ``pr:<id|number>``              PL ``REVIEW pr=#<n>``
============  =============================  =====================================

Every one needs EDIT on the agent it acts on (operators; a sub-agent inherits
its PL's grants), emits ``control.<action>`` with ``by``, ``by_role`` and
``target_role``, and tells the PL, so its plan stays the record.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryPr, FactoryTask
from omnigent.factory import LABEL_PROJECT, ROLE_IC, ROLE_PROJECT_LEAD
from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import events, people
from omnigent.server.factory.actor import parse_ref, task_ref
from omnigent.server.factory.attribution import human_header
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import (
    NOTE_MAX_CHARS,
    TARGET_MEMBER_PREFIX,
    CockpitError,
    ControlResult,
    clip,
    control_header,
    find_project_pl,
    get_conv,
    human_label,
    is_closed,
    load_factory_root,
    require,
    same_id,
    send_text,
)
from omnigent.server.factory.envelope import display_title, quote_spoofed_lines
from omnigent.server.factory.mailer import MailItem, get_mailer, new_mail_id
from omnigent.server.factory.membership import (
    is_top_level,
    resolve_factory_id,
    resolve_role,
    root_conversation,
)
from omnigent.stores.factory_store import norm_id
from omnigent.util.session_lifecycle import title_without_closed_marker

_logger = logging.getLogger(__name__)

ACTION_MESSAGE: Final = "message"
ACTION_INTERRUPT: Final = "interrupt"
ACTION_REDISPATCH: Final = "redispatch"
ACTION_REASSIGN: Final = "reassign"
ACTION_RERUN_DEMO: Final = "rerun_demo"
ACTION_REREVIEW: Final = "rereview"
INTERVENTION_ACTIONS: Final = (
    ACTION_MESSAGE,
    ACTION_INTERRUPT,
    ACTION_REDISPATCH,
    ACTION_REASSIGN,
    ACTION_RERUN_DEMO,
    ACTION_REREVIEW,
)
ACKED_KINDS: Final = frozenset(
    f"control.{a}"
    for a in (ACTION_REDISPATCH, ACTION_REASSIGN, ACTION_RERUN_DEMO, ACTION_REREVIEW)
)
"""Interventions that send the PL a control it replies to."""
ACKED_KINDS_ACTIONS: Final = frozenset(k.removeprefix("control.") for k in ACKED_KINDS)

TARGET_TASK_PREFIX: Final = "task:"
TARGET_PR_PREFIX: Final = "pr:"
VENDORS: Final[dict[str, str]] = {
    "ic": "ic",
    "claude": "ic",
    "ic_codex": "ic_codex",
    "codex": "ic_codex",
}
"""``options.vendor`` spellings → the PL's worker agent."""
NOTICE_NOTE_MAX: Final = 200
INTERRUPT_TIMEOUT_S: Final = 5.0
_CONTROL_KIND: Final = "request"
_NOTICE_KIND: Final = "info"
_REPLY: Final = 'reply once: sys_session_post(to="parent", kind="reply")'
_WORKER_TITLE_RE: Final = re.compile(r"^(?:[a-z_]+:)?(?P<name>[a-z0-9][a-z0-9-]*)$")
_RETRY_RE: Final = re.compile(r"^(?P<key>.+?)-r(?P<k>[0-9]{1,3})$")


def handles(action: str) -> bool:
    """:returns: Whether *action* is an intervention (planned here, not in ``control``)."""
    return action in INTERVENTION_ACTIONS


@dataclass
class InterventionPlan:
    """
    A validated, authorized intervention.

    :param target: The agent acted on (message, interrupt, the worker of a task).
    :param pl: The top-level member that is told (the PL, or a lead for its sub-agents).
    :param task_label: The task's ref, e.g. ``"T-4"``, or its key without a task row.
    :param next_title: The title of the fresh worker (task actions).
    """

    root: Conversation
    action: str
    ref: str
    user_id: str | None
    by: str
    by_role: str | None
    note: str | None
    project: str | None
    target: Conversation | None = None
    pl: Conversation | None = None
    task: FactoryTask | None = None
    task_key: str | None = None
    task_label: str | None = None
    next_title: str | None = None
    vendor: str | None = None
    pr: FactoryPr | None = None
    options: dict[str, Any] = field(default_factory=dict)


# ── Resolution ─────────────────────────────────────────────────


async def _member_target(deps: FactoryDeps, root: Conversation, ref: str) -> Conversation:
    sid = ref[len(TARGET_MEMBER_PREFIX) :].strip()
    conv = await get_conv(deps, sid)
    store = deps.conversation_store
    if conv is None or not same_id(
        await asyncio.to_thread(resolve_factory_id, conv, store), root.id
    ):
        raise CockpitError(404, "unknown_member", f"{sid!r} is not in this factory")
    if is_closed(conv):
        raise CockpitError(
            409, "session_closed", f"{display_title(conv.title, conv.id)} is closed"
        )
    return conv


async def _owner_member(deps: FactoryDeps, conv: Conversation) -> Conversation:
    """The top-level member a session belongs to (itself when top-level)."""
    if is_top_level(conv):
        return conv
    return await asyncio.to_thread(root_conversation, conv, deps.conversation_store)


def _worker_name(conv: Conversation) -> str | None:
    # A closed worker keeps its name before the ``:closed:<id>`` suffix.
    title = title_without_closed_marker(conv.title) or ""
    match = _WORKER_TITLE_RE.match(title.strip())
    return match.group("name") if match else None


def _attempt_of(name: str, key: str) -> int | None:
    """:returns: ``0`` for ``<key>``, ``k`` for ``<key>-r<k>``, else ``None``."""
    if name == key:
        return 0
    match = _RETRY_RE.match(name)
    if match and match.group("key") == key:
        return int(match.group("k"))
    return None


def _workers(
    deps: FactoryDeps, pl: Conversation, key: str, task: FactoryTask | None
) -> list[tuple[int, Conversation]]:
    """Sub-agents of *pl* bound to task *key*, ``(attempt, session)`` oldest attempt first."""
    page = deps.conversation_store.list_conversations(
        limit=200, kind="sub_agent", parent_conversation_id=pl.id, order="desc"
    )
    bound = set(task.session_ids) if task is not None else set()
    out: list[tuple[int, Conversation]] = []
    for child in page.data:
        name = _worker_name(child) or ""
        attempt = _attempt_of(name, key)
        if attempt is None and any(same_id(child.id, s) for s in bound):
            attempt = 0
        if attempt is not None:
            out.append((attempt, child))
    out.sort(key=lambda pair: (pair[0], pair[1].created_at))
    return out


async def _task_target(
    deps: FactoryDeps, root: Conversation, ref: str
) -> tuple[str, str, FactoryTask | None]:
    """``task:<slug>/<key>`` or ``task:T-<n>`` → ``(project, key, task row or None)``."""
    body = ref[len(TARGET_TASK_PREFIX) :].strip()
    store = deps.factory_store
    parsed = parse_ref(body) or parse_ref(ref)
    task: FactoryTask | None = None
    if parsed is not None and parsed.kind == "task" and parsed.number is not None:
        task = await asyncio.to_thread(store.get_task_by_number, root.id, parsed.number)
        if task is None:
            raise CockpitError(404, "unknown_task", f"no task T-{parsed.number} in this factory")
        return task.project_slug, task.key, task
    if parsed is not None and parsed.kind == "task_key" and parsed.slug and parsed.key:
        task = await asyncio.to_thread(store.get_task_by_key, root.id, parsed.slug, parsed.key)
        return parsed.slug, parsed.key, task
    raise CockpitError(
        400,
        "invalid_target",
        "a task target is 'task:<slug>/<key>' or 'task:T-<n>'",
        field="target",
    )


async def _pr_target(deps: FactoryDeps, root: Conversation, ref: str) -> FactoryPr:
    """``pr:<row id>``, ``pr:<number>`` or ``pr:<owner>/<repo>#<number>``."""
    body = ref[len(TARGET_PR_PREFIX) :].strip().lstrip("!")
    store = deps.factory_store
    pr: FactoryPr | None = None
    try:
        pr = await asyncio.to_thread(store.get_pr, norm_id(body))
    except ValueError:
        pr = None
    if pr is None:
        repo, _, number = body.rpartition("#")
        if number.isdigit():
            prs = await asyncio.to_thread(store.list_prs, root.id)
            matches = [p for p in prs if p.number == int(number) and (not repo or p.repo == repo)]
            pr = max(matches, key=lambda p: p.created_at) if matches else None
    if pr is None or not same_id(pr.factory_id, root.id):
        raise CockpitError(404, "unknown_pr", "no such PR in this factory")
    if pr.state != "open":
        raise CockpitError(409, "pr_not_open", f"PR #{pr.number} is {pr.state}")
    return pr


async def _project_pl(deps: FactoryDeps, root: Conversation, slug: str | None) -> Conversation:
    pl = await asyncio.to_thread(find_project_pl, deps, root.id, slug)
    if pl is None:
        raise CockpitError(409, "no_project_lead", f"project {slug!r} has no PL to tell")
    return pl


# ── Planning ───────────────────────────────────────────────────


async def plan_intervention(
    app_state: Any,
    factory_id: str,
    *,
    user_id: str | None,
    action: str,
    target: str | None,
    note: str | None = None,
    options: Mapping[str, Any] | None = None,
) -> InterventionPlan:
    """
    Validate and authorize an intervention without side effects.

    :raises CockpitError: On an invalid target, option or note; 409 when a PR
        is not open or a project has no PL.
    :raises OmnigentError: 401/403/404 from the access checks.
    """
    deps = factory_deps(app_state)
    root = await load_factory_root(deps, user_id, factory_id)
    if note is not None and len(note) > NOTE_MAX_CHARS:
        raise CockpitError(
            400, "note_too_long", f"note exceeds {NOTE_MAX_CHARS} chars", field="note"
        )
    ref = (target or "").strip()
    opts = dict(options or {})
    by = await human_label(deps, root.id, user_id)
    by_role = await asyncio.to_thread(people.role_of, deps, root.id, user_id)
    plan = InterventionPlan(
        root=root,
        action=action,
        ref=ref,
        user_id=user_id,
        by=by,
        by_role=by_role,
        note=note.strip() if note and note.strip() else None,
        project=None,
    )
    if action in (ACTION_MESSAGE, ACTION_INTERRUPT):
        if not ref.startswith(TARGET_MEMBER_PREFIX):
            raise CockpitError(
                400, "invalid_target", f"{action} targets 'member:<session id>'", field="target"
            )
        if action == ACTION_MESSAGE and plan.note is None:
            raise CockpitError(
                400, "note_required", "message needs the text in note", field="note"
            )
        conv = await _member_target(deps, root, ref)
        owner = await _owner_member(deps, conv)
        await require(deps, user_id, conv.id, LEVEL_EDIT)
        plan.target, plan.pl = conv, owner
        plan.project = owner.labels.get(LABEL_PROJECT) or None
    elif action in (ACTION_REDISPATCH, ACTION_REASSIGN):
        if not ref.startswith(TARGET_TASK_PREFIX):
            raise CockpitError(
                400, "invalid_target", f"{action} targets 'task:<slug>/<key>'", field="target"
            )
        slug, key, task = await _task_target(deps, root, ref)
        pl = await _project_pl(deps, root, slug)
        await require(deps, user_id, pl.id, LEVEL_EDIT)
        if action == ACTION_REASSIGN:
            vendor = VENDORS.get(str(opts.get("vendor") or "").strip().lower())
            if vendor is None:
                raise CockpitError(
                    400,
                    "invalid_option",
                    "options.vendor must be 'ic' or 'ic_codex'",
                    field="options.vendor",
                )
            plan.vendor = vendor
            opts = {"vendor": vendor}
        workers = await asyncio.to_thread(_workers, deps, pl, key, task)
        attempt = max([a for a, _ in workers], default=-1)
        if task is not None:
            attempt = max(attempt, task.attempts - 1)
        plan.pl, plan.project, plan.task, plan.task_key = pl, slug, task, key
        live = [c for _, c in workers if not is_closed(c)]
        plan.target = live[-1] if live else (workers[-1][1] if workers else None)
        plan.next_title = f"{key}-r{attempt + 1}" if attempt >= 0 else key
        plan.task_label = task_ref(task.number) if task is not None else key
    elif action in (ACTION_RERUN_DEMO, ACTION_REREVIEW):
        if not ref.startswith(TARGET_PR_PREFIX):
            raise CockpitError(
                400, "invalid_target", f"{action} targets 'pr:<id>'", field="target"
            )
        pr = await _pr_target(deps, root, ref)
        pl = await _project_pl(deps, root, pr.project_slug)
        await require(deps, user_id, pl.id, LEVEL_EDIT)
        plan.pr, plan.pl, plan.project = pr, pl, pr.project_slug
        plan.target = pl
    else:
        raise CockpitError(
            400, "invalid_action", f"unknown intervention {action!r}", field="action"
        )
    plan.options = opts
    return plan


# ── Texts ──────────────────────────────────────────────────────


def agent_label(conv: Conversation | None) -> str:
    """:returns: How texts name an agent: its title, e.g. ``"ic:route-r1"``."""
    return display_title(conv.title, conv.id) if conv is not None else "agent"


def _note_tail(note: str | None) -> str:
    return f"\n{quote_spoofed_lines(note)}" if note else ""


def message_text(user_id: str | None, role: str | None, note: str) -> str:
    """:returns: ``[Factory human · <name> (<role>)]`` + the spoof-quoted message."""
    return f"{human_header(user_id or 'human', role)}\n{quote_spoofed_lines(note)}"


def notice_text(plan: InterventionPlan) -> str:
    """:returns: ``[Factory notice · <human> → <agent> · <action> · "<note>"]`` for the PL."""
    note = ""
    if plan.note:
        flat = clip(plan.note, NOTICE_NOTE_MAX).translate(
            str.maketrans({'"': "'", "[": "(", "]": ")"})
        )
        note = f' · "{flat}"'
    tail = (
        " Record it in your plan and reconcile; do not undo it."
        if plan.pl is not None
        and plan.target is not None
        and not same_id(plan.pl.id, plan.target.id)
        else ""
    )
    return f"[Factory notice · {plan.by} → {agent_label(plan.target)} · {plan.action}{note}]{tail}"


def _worker_clause(plan: InterventionPlan) -> str:
    task = plan.task_label if plan.task is None else f"{plan.task_label} ({plan.task_key})"
    if plan.target is None:
        return f"Task {task}: no worker of it is open."
    return (
        f'Task {task}: close its current worker "{agent_label(plan.target)}" ({plan.target.id}).'
    )


def worker_agent(plan: InterventionPlan) -> str:
    """:returns: The agent a redispatched task runs on: its last worker's, else the card's."""
    workers = set(VENDORS.values())
    name = plan.target.sub_agent_name if plan.target is not None else None
    if name in workers:
        return str(name)
    harness = (plan.task.card or {}).get("harness") if plan.task is not None else None
    return harness if isinstance(harness, str) and harness in workers else ROLE_IC


def control_text(plan: InterventionPlan) -> str:
    """:returns: The ``[Factory control · …]`` text the PL gets for a task or PR action."""
    pr = plan.pr
    if plan.action == ACTION_REDISPATCH:
        label = f"REDISPATCH · {plan.task_label}"
        body = (
            f"{_worker_clause(plan)} Dispatch a fresh {worker_agent(plan)} titled "
            f'"{plan.next_title}" '
            "from the task card and its branch's NOTES.md (attempt +1), record it in plan.md, "
            f"then {_REPLY}."
        )
    elif plan.action == ACTION_REASSIGN:
        label = f"REASSIGN · {plan.task_label} → {plan.vendor}"
        body = (
            f"{_worker_clause(plan)} Dispatch the task to {plan.vendor} titled "
            f'"{plan.next_title}" with a fresh context from the task card and NOTES.md '
            f"(attempt +1), record it in plan.md, then {_REPLY}."
        )
    elif plan.action == ACTION_RERUN_DEMO and pr is not None:
        head = pr.head_sha[:7] if pr.head_sha else "its head"
        label = f"DEMO pr=#{pr.number}"
        body = (
            f"Re-run the demo of PR #{pr.number} at {head}: with its preview ready at that head, "
            "dispatch the demo agent as LOOP does; otherwise send env_keeper "
            f"PREVIEW pr={pr.number} head={pr.head_sha or '<sha>'} base=<base> first; "
            f"then {_REPLY}."
        )
    elif plan.action == ACTION_REREVIEW and pr is not None:
        head = pr.head_sha[:7] if pr.head_sha else "its head"
        label = f"REVIEW pr=#{pr.number}"
        body = (
            f"Re-run the cross-vendor review of PR #{pr.number} at {head} as LOOP does after "
            f"READY_FOR_REVIEW; then {_REPLY}."
        )
    else:
        raise ValueError(f"{plan.action} sends no control text")
    return f"{control_header(label, plan.by)} {body}{_note_tail(plan.note)}"


def summary_of(plan: InterventionPlan) -> str:
    """:returns: The event summary, e.g. ``mia (operator) interrupted "ic:route-r1"``."""
    agent = f'"{agent_label(plan.target)}"'
    if plan.action == ACTION_MESSAGE:
        return f"{plan.by} messaged {agent}"
    if plan.action == ACTION_INTERRUPT:
        return f"{plan.by} interrupted {agent}"
    if plan.action == ACTION_REDISPATCH:
        return f"{plan.by} redispatched {plan.task_label} as {plan.next_title}"
    if plan.action == ACTION_REASSIGN:
        return f"{plan.by} reassigned {plan.task_label} to {plan.vendor}"
    number = plan.pr.number if plan.pr is not None else "?"
    if plan.action == ACTION_RERUN_DEMO:
        return f"{plan.by} asked for a new demo of PR #{number}"
    return f"{plan.by} asked for a new review of PR #{number}"


# ── Execution ──────────────────────────────────────────────────


async def interrupt_session(app_state: Any, session_id: str) -> bool:
    """
    Interrupt *session_id*'s current turn, as the session interrupt event does.

    :returns: Whether the runner accepted the interrupt.
    """
    from omnigent.server.routes._sessions import helpers
    from omnigent.server.routes._sessions.common import _interrupt_fenced_sessions

    helpers._publish_interrupted(session_id)
    # Fence the cancelled turn's trailing output, like the route.
    _interrupt_fenced_sessions.add(session_id)
    delivered = False
    client = await helpers._get_runner_client(
        session_id, getattr(app_state, "runner_router", None)
    )
    if client is not None:
        try:
            resp = await client.post(
                f"/v1/sessions/{session_id}/events",
                json={"type": "interrupt"},
                timeout=INTERRUPT_TIMEOUT_S,
            )
            delivered = resp.status_code < 400
        except (httpx.HTTPError, ConnectionError):
            _logger.warning("factory: interrupt of %s failed", session_id, exc_info=True)
    if not delivered:
        _interrupt_fenced_sessions.discard(session_id)
    return delivered


def _running(conv: Conversation) -> bool:
    from omnigent.server.routes._sessions.helpers import _session_status_from_cache

    return _session_status_from_cache(conv.id, conv.live_status) == "running"


async def execute_intervention(app_state: Any, plan: InterventionPlan) -> ControlResult:
    """
    Run a planned intervention: interrupt, mail, PL text, audit event.

    :returns: The ``control.<action>`` event and the queued mail ids.
    """
    deps = factory_deps(app_state)
    mail_ids: dict[str, str] = {}
    notice_mail_id: str | None = None
    interrupted: bool | None = None
    target = plan.target
    action = plan.action
    if action == ACTION_INTERRUPT and target is not None:
        interrupted = await interrupt_session(app_state, target.id)
    elif (
        action in (ACTION_REDISPATCH, ACTION_REASSIGN)
        and target is not None
        and not is_closed(target)
    ):
        if _running(target):
            interrupted = await interrupt_session(app_state, target.id)
    if action == ACTION_MESSAGE and target is not None and plan.note is not None:
        mail_id = new_mail_id()
        get_mailer(app_state).submit(
            MailItem(
                mail_id=mail_id,
                sender_id=None,
                target_id=target.id,
                target_title=None,
                text=message_text(plan.user_id, plan.by_role, plan.note),
                kind=_CONTROL_KIND,
                factory_id=plan.root.id,
                project=plan.project,
                created_by=plan.user_id,
            )
        )
        mail_ids[target.id] = mail_id
    pl = plan.pl
    if action in ACKED_KINDS_ACTIONS and pl is not None:
        mail_ids[pl.id] = send_text(app_state, pl.id, control_text(plan), kind=_CONTROL_KIND)
    elif pl is not None and not same_id(pl.id, target.id if target else None):
        # No notice to the agent acted on itself: it would start the turn an interrupt stopped.
        notice_mail_id = send_text(app_state, pl.id, notice_text(plan), kind=_NOTICE_KIND)
    target_role = (
        await asyncio.to_thread(resolve_role, target, deps) if target is not None else None
    )
    if action in (ACTION_RERUN_DEMO, ACTION_REREVIEW):
        target_role = ROLE_PROJECT_LEAD
    data: dict[str, Any] = {
        "from": plan.by,
        "by": plan.by,
        "by_kind": "human",
        "by_role": plan.by_role,
        "by_user": plan.user_id,
        "action": action,
        "target": plan.ref,
        "target_role": target_role,
        "target_session_id": target.id if target is not None else None,
        "note": plan.note,
        "options": plan.options,
        "mail_ids": mail_ids,
        "notice_mail_id": notice_mail_id,
    }
    if interrupted is not None:
        data["interrupted"] = interrupted
    if action in ACKED_KINDS_ACTIONS and pl is not None:
        data["recipients"] = [norm_id(pl.id)]
    if plan.task is not None:
        data["task_id"] = plan.task.id
        data["task"] = task_ref(plan.task.number)
    if plan.task_key is not None:
        data["task_key"] = plan.task_key
        data["next_title"] = plan.next_title
    if plan.pr is not None:
        data["pr_id"] = plan.pr.id
    subject = f"pr:{plan.pr.repo}#{plan.pr.number}" if plan.pr is not None else plan.ref
    event = await events.emit(
        app_state,
        plan.root.id,
        f"control.{action}",
        severity="notice",
        project=plan.project,
        to_id=target.id if target is not None else (pl.id if pl is not None else None),
        subject=subject,
        summary=summary_of(plan),
        data=data,
    )
    return ControlResult(event=event, mail_ids=mail_ids, notice_mail_id=notice_mail_id)


async def apply_intervention(
    app_state: Any,
    factory_id: str,
    *,
    user_id: str | None,
    action: str,
    target: str | None,
    note: str | None = None,
    options: Mapping[str, Any] | None = None,
) -> ControlResult:
    """Plan, authorize and run one intervention (the control route's new actions)."""
    plan = await plan_intervention(
        app_state,
        factory_id,
        user_id=user_id,
        action=action,
        target=target,
        note=note,
        options=options,
    )
    return await execute_intervention(app_state, plan)


__all__ = [
    "ACKED_KINDS",
    "ACTION_INTERRUPT",
    "ACTION_MESSAGE",
    "ACTION_REASSIGN",
    "ACTION_REDISPATCH",
    "ACTION_REREVIEW",
    "ACTION_RERUN_DEMO",
    "INTERVENTION_ACTIONS",
    "VENDORS",
    "InterventionPlan",
    "agent_label",
    "apply_intervention",
    "control_text",
    "execute_intervention",
    "handles",
    "interrupt_session",
    "message_text",
    "notice_text",
    "plan_intervention",
    "summary_of",
]
