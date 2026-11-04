"""Change direction from a checkpoint: preview, apply, Undo.

From any decided or answered checkpoint (``K-n``, ``D-n``, ``C-n``) a person
picks another option or writes a new direction:

``POST /v1/factory/factories/{fid}/checkpoints/{ref}/preview`` ``{option_id? | direction?}``
    What would be taken back (each step in plain words), who re-plans and what
    it costs, and a ``preview_id`` over exactly those facts. Stores nothing.
``POST …/checkpoints/{ref}/apply`` ``{option_id? | direction?, preview_id}``
    Re-plans over fresh facts (409 ``stale`` when they moved since the
    preview), then:

    1. records the person's checkpoint, linked to the one it overrides (that
       one reads ``changed`` from then on);
    2. takes back what can be taken back, through the existing cores:
       a proposal still in its Undo window is undone; past it, the projects it
       created are cancelled (a person's projects proposal, applied at once)
       and the charters it changed are put back; a project that already merged
       code gets a revert task instead, never a force-push; a proposal still
       pending that followed the old choice is rejected;
    3. tells the deciding agent (else the project's lead, else the factory's
       lead) to re-plan from this checkpoint, with the direction quoted;

    and returns an Undo token for ``POST …/undo/{token}`` (10 minutes, the
    person who applied it or an owner): it puts the old checkpoint back,
    re-files what was undone, cancels the revert tasks and tells the agent.

A step that fails while applying is listed with why; the change of direction
stands. People only (human origin); agents never call these routes.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import logging
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ConfigDict, Field

from omnigent.factory import TASK_KEY_RE
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE
from omnigent.server.factory import chat_cards, checkpoints, control, events, node_verbs
from omnigent.server.factory.actor import proposal_ref
from omnigent.server.factory.chat import derived_id
from omnigent.server.factory.control import CockpitError, clip
from omnigent.server.factory.envelope import display_title, quote_spoofed_lines
from omnigent.server.factory.notify import iso_utc
from omnigent.server.factory.start import parse_body
from omnigent.stores.factory_store import CheckpointConflict, now_s

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryCheckpoint, FactoryProposal

_logger = logging.getLogger(__name__)

VERB: Final = "direction"
UNDO_WINDOW_S: Final = node_verbs.UNDO_WINDOW_S
DIRECTION_MAX: Final = 2000
_PROPOSAL_OPEN: Final = ("pending",)
_CHANGEABLE: Final = ("decided", "answered")


class DirectionIn(BaseModel):
    """``POST …/checkpoints/{ref}/preview`` and ``…/apply``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    option_id: str | None = Field(None, max_length=16)
    direction: str | None = Field(None, max_length=DIRECTION_MAX)
    based_on: str | None = Field(None, max_length=64)
    preview_id: str | None = Field(None, max_length=64)


@dataclass
class Step:
    """
    One thing taken back.

    :param kind: ``undo`` (a proposal's Undo), ``cancel`` (a created project),
        ``restore`` (a charter put back), ``revert_task`` (merged code),
        ``reject`` (a pending proposal) or ``keep`` (nothing to do: why).
    """

    kind: str
    ref: str
    text: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    """A change of direction planned over the facts, before anything is written."""

    item: checkpoints.Item
    option_id: str | None
    label: str | None
    direction: str | None
    steps: list[Step]
    to: Conversation | None
    to_name: str
    warnings: list[str]
    level: int = LEVEL_EDIT

    def digest(self) -> str:
        """The ``preview_id``: this plan over the facts it reads."""
        body = json.dumps(
            [
                self.item.ref,
                self.item.status,
                self.option_id,
                self.direction,
                [(s.kind, s.ref, sorted(s.data.items(), key=str)) for s in self.steps],
                self.to.id if self.to is not None else None,
            ],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(body.encode()).hexdigest()[:32]


def _err(status: int, code: str, message: str, **extra: Any) -> CockpitError:
    return CockpitError(status, code, message, **extra)


# ── Planning ───────────────────────────────────────────────────


def _followers(deps: Any, fid: str, item: checkpoints.Item) -> list[FactoryProposal]:
    """The projects proposals that carry the checkpoint out (or are it)."""
    if item.proposal_row is not None:
        return [item.proposal_row]
    names = {item.ref}
    if item.decision:
        names.add(item.decision)
    if item.row is not None and item.row.decision_id:
        decision = deps.factory_store.get_decision(item.row.decision_id)
        if decision is not None:
            names.add(decision.ref)
    out: list[FactoryProposal] = []
    for row in deps.factory_store.list_proposals(fid, kind="projects", limit=500):
        cited = {str(e) for e in row.evidence or []}
        authority = (row.result or {}).get("authority")
        if isinstance(authority, Mapping) and authority.get("decision"):
            cited.add(str(authority["decision"]))
        if cited & names and row.status in ("applied", *_PROPOSAL_OPEN):
            out.append(row)
    return out


def _merged_prs(deps: Any, fid: str, slug: str) -> list[Any]:
    return [p for p in deps.factory_store.list_prs(fid, ["merged"]) if p.project_slug == slug]


async def _steps_for(env: node_verbs.Env, row: FactoryProposal, warnings: list[str]) -> list[Step]:
    """What taking *row* back takes: its Undo, else a revert through the cores."""
    from omnigent.server.factory import proposals

    deps, fid = env.deps, env.fid
    store = deps.factory_store
    ref = proposal_ref(row.number)
    if row.status in _PROPOSAL_OPEN:
        return [Step("reject", ref, f"Reject {ref} (it followed the old choice).")]
    blocker = await proposals.undo_blocker(deps, env.root, row)
    created = [str(s) for s in (row.result or {}).get("created") or []]
    if blocker is None:
        what = f" (drops {', '.join(created)})" if created else ""
        return [Step("undo", ref, f"Undo {ref}{what}.", {"ops": row.ops})]
    steps: list[Step] = []
    for slug in created:
        project = await asyncio.to_thread(store.get_project, fid, slug)
        if project is None or project.status != "active":
            continue
        merged = await asyncio.to_thread(_merged_prs, deps, fid, slug)
        if merged:
            for pr in merged:
                steps.append(
                    Step(
                        "revert_task",
                        slug,
                        f"Add a task to {slug} that reverts #{pr.number} (merged; "
                        "never a force-push).",
                        {"pr": pr.number, "url": pr.url or ""},
                    )
                )
        else:
            steps.append(Step("cancel", slug, f"Cancel {slug} ({ref} created it)."))
    for op in row.ops or []:
        if op.get("op") in ("split", "merge", "cancel"):
            warnings.append(f"{ref}'s {op.get('op')} cannot be taken back; its lead is told.")
    for slug, before in (row.before or {}).items():
        if not isinstance(before, Mapping) or "charter" not in before:
            continue
        current = await asyncio.to_thread(store.get_project, fid, slug)
        if current is None or current.status != "active":
            continue
        if proposals.changed_since(row, slug, current):
            warnings.append(f"{slug} changed since {ref}; its charter stays as it is now.")
            continue
        steps.append(Step("restore", slug, f"Put {slug}'s charter back as before {ref}."))
    if not steps:
        steps.append(Step("keep", ref, f"Nothing of {ref} is left to take back."))
    return steps


async def _target(env: node_verbs.Env, item: checkpoints.Item) -> tuple[Conversation | None, str]:
    """Who re-plans: the deciding agent, else the project's lead, else the factory's lead."""
    deps = env.deps
    for session in (item.asker, item.row.asked_by_session_id if item.row else None):
        conv = await control.get_conv(deps, session) if session else None
        if conv is not None and not control.is_gone(conv):
            return conv, display_title(conv.title, conv.id)
    if item.project_slug:
        pl = await asyncio.to_thread(control.find_project_pl, deps, env.fid, item.project_slug)
        if pl is not None:
            return pl, display_title(pl.title, pl.id)
    root = env.root
    return (
        (None, "nobody") if control.is_gone(root) else (root, display_title(root.title, root.id))
    )


async def plan_change(
    env: node_verbs.Env, book: checkpoints.Book, ref: str, body: DirectionIn
) -> Plan:
    """
    Plan a change of direction from *ref* (nothing is written).

    :raises CockpitError: 404 ``unknown_checkpoint``, 409 ``still_open`` /
        ``already_changed``, 400 ``choose_something`` / ``unknown_option`` / ``same_choice``.
    """
    item = book.get(ref)
    if item is None:
        raise _err(404, "unknown_checkpoint", f"no checkpoint {ref!r} in this factory")
    if item.status == "asked":
        raise _err(409, "still_open", f"{item.ref} waits for an answer: answer it instead")
    if item.status == "changed":
        raise _err(
            409,
            "already_changed",
            f"{item.ref} was changed by {item.changed_by}; change direction from there",
            changed_by=item.changed_by,
        )
    option = body.option_id or None
    direction = " ".join((body.direction or "").split()) or None
    if option is None and direction is None:
        raise _err(400, "choose_something", "pick another option or write a direction")
    label = None
    if option is not None:
        found = next((o for o in item.options if str(o.get("id")) == option), None)
        if found is None:
            raise _err(400, "unknown_option", f"{item.ref} has no option {option!r}")
        if option == item.choice_id and direction is None:
            raise _err(400, "same_choice", f"{item.ref} already chose that")
        label = str(found.get("label") or option)
    warnings: list[str] = []
    steps: list[Step] = []
    rows = await asyncio.to_thread(_followers, env.deps, env.fid, item)
    # The same choice with more direction only re-plans: nothing it did is taken back.
    if option is None or option != item.choice_id:
        for row in rows:
            steps += await _steps_for(env, row, warnings)
    to, name = await _target(env, item)
    level = LEVEL_MANAGE if any(r.approval == "owner" for r in rows) else LEVEL_EDIT
    return Plan(item, option, label, direction, steps, to, name, warnings, level)


def _cost(plan: Plan) -> str:
    tasks = sum(1 for s in plan.steps if s.kind == "revert_task")
    parts = [f"one turn of {plan.to_name}" if plan.to is not None else "no agent turn"]
    if tasks:
        parts.append(f"{tasks} revert task{'s' if tasks != 1 else ''} in their projects")
    return ", ".join(parts)


def preview_view(env: node_verbs.Env, plan: Plan) -> dict[str, Any]:
    """The ``POST …/checkpoints/{ref}/preview`` answer."""
    item = plan.item
    what = plan.label or "a new direction"
    can = env.level >= plan.level
    effects = [s.text for s in plan.steps if s.kind != "keep"]
    out: dict[str, Any] = {
        "preview_id": plan.digest(),
        "ref": item.ref,
        "checkpoint": checkpoints.detail_view(item, can_change=True),
        "choice": {k: v for k, v in (("id", plan.option_id), ("label", plan.label)) if v},
        "title": f"Change {item.ref} to {what}",
        "undo": [s.text for s in plan.steps if s.kind in ("undo", "reject")],
        "revert": [s.text for s in plan.steps if s.kind in ("cancel", "restore", "revert_task")],
        "effects": effects or ["Nothing to take back: only the plan from here changes."],
        "replan": {
            "to": plan.to_name,
            "text": f"{plan.to_name} re-plans from {item.ref} with the new direction quoted.",
        },
        "cost": _cost(plan),
        "warnings": plan.warnings,
        "can_apply": can,
        "approver_note": (
            f"You. Applies now; Undo for {UNDO_WINDOW_S // 60} min."
            if can
            else "An owner applies this; you can read what it would do."
        ),
    }
    if plan.direction:
        out["choice"]["direction"] = plan.direction
    return out


# ── Applying ───────────────────────────────────────────────────


def _message(new: FactoryCheckpoint, plan: Plan, by: str, lines: Sequence[str]) -> str:
    """The re-plan text the deciding agent gets."""
    from omnigent.server.factory.decisions import question_excerpt

    item = plan.item
    what = plan.label or "new direction"
    old = item.choice_label or (clip(item.direction, 60) if item.direction else "(free text)")
    out = [
        f'[Factory decision · {new.ref} changed by {by} · "{what}"]',
        f'Changes {item.ref}: it was "{old}" ({item.by_name or "the factory"}).',
    ]
    if plan.direction:
        out.append("Direction:")
        out.append(quote_spoofed_lines(plan.direction))
    if lines:
        out.append("Taken back: " + " ".join(lines))
    out.append(
        f"Re-plan from {new.ref}: stop what followed from the old choice, plan for the new "
        "direction (sys_factory_project, tasks) and record the decisions you take with "
        "sys_factory_decide. Do not ask again what this answers."
    )
    out.append(f"Question: {question_excerpt(item.question)}")
    return "\n".join(out)


async def _run_step(env: node_verbs.Env, step: Step, new_ref: str) -> tuple[str, dict | None]:
    """Run one step; :returns: ``(what it did, how its Undo takes it back or None)``."""
    from omnigent.server.factory import decisions, projects, proposals, tasks

    deps, fid = env.deps, env.fid
    store = deps.factory_store
    if step.kind == "undo":
        await proposals.undo(env.app_state, fid, step.ref, user_id=env.user_id)
        return f"{step.ref} undone.", {"kind": "refile", "ref": step.ref, "ops": step.data["ops"]}
    if step.kind == "reject":
        row = await asyncio.to_thread(proposals.find_proposal, deps, fid, step.ref)
        if row is None or not row.decision_id:
            return f"{step.ref} was already closed.", None
        await decisions.answer_decision(
            env.app_state,
            row.decision_id,
            user_id=env.user_id,
            option_id=proposals.OPTION_REJECT,
            text=f"Direction changed at {new_ref}.",
        )
        return f"{step.ref} rejected.", None
    if step.kind == "cancel":
        filed = await node_verbs.file_and_apply(
            env,
            [{"op": "cancel", "slug": step.ref}],
            f"Direction changed at {new_ref}: {step.ref} is no longer wanted.",
        )
        keep = {"kind": "keep", "ref": step.ref, "text": f"{step.ref} stays cancelled"}
        return f"{step.ref} cancelled ({proposal_ref(filed.number)}).", keep
    if step.kind == "restore":
        row = None
        for proposal in await asyncio.to_thread(
            lambda: store.list_proposals(fid, kind="projects", status="applied", limit=500)
        ):
            if step.ref in (proposal.before or {}):
                row = proposal
                break
        before = (row.before or {}).get(step.ref) if row is not None else None
        if not isinstance(before, Mapping):
            return f"{step.ref}'s charter could not be found to put back.", None
        await projects.restore_project_core(
            env.app_state,
            env.root,
            step.ref,
            before.get("charter"),
            before.get("priority"),
            env.actor,
            via=f"direction changed at {new_ref}",
        )
        return f"{step.ref}'s charter is back as it was.", None
    if step.kind == "revert_task":
        number = int(step.data.get("pr") or 0)
        key = f"revert-{number}"[:48]
        if not TASK_KEY_RE.match(key):
            key = "revert-change"
        body = tasks.TaskAddIn.model_validate(
            {
                "project": step.ref,
                "key": key,
                "title": f"Revert #{number}: direction changed at {new_ref}",
                "do": (
                    f"Direction changed at {new_ref}. Open a PR that reverts #{number} "
                    f"({step.data.get('url') or 'its merge'}) with `git revert` of its merge "
                    "commit; never force-push or rewrite history."
                ),
                "kind": "implement",
            }
        )
        made = await tasks.add_task(env.app_state, fid, body, env.actor, env.by)
        ref = str((made.get("task") or {}).get("ref") or key)
        return f"{ref} in {step.ref} reverts #{number}.", {"kind": "task", "ref": ref}
    return step.text, None


async def apply(
    env: node_verbs.Env, book: checkpoints.Book, ref: str, payload: Any
) -> dict[str, Any]:
    """
    ``POST …/checkpoints/{ref}/apply``: change direction exactly as previewed.

    :raises CockpitError: 400 ``preview_required``, 409 ``stale`` (with the
        fresh ``preview``), 403 ``authority``, and :func:`plan_change`'s errors.
    """
    body = parse_body(DirectionIn, payload if payload is not None else {})
    if not body.preview_id:
        raise _err(400, "preview_required", "preview it first and apply its preview_id")
    await control.require(env.deps, env.user_id, env.fid, LEVEL_EDIT)
    async with node_verbs._lock(env.fid):
        plan = await plan_change(env, book, ref, body)
        if plan.digest() != body.preview_id:
            raise _err(
                409,
                "stale",
                "what this changes moved since the preview; look at the new preview "
                "and apply that",
                preview=preview_view(env, plan),
            )
        if env.level < plan.level:
            raise _err(403, "authority", "an owner applies this", who="an owner")
        return await _apply(env, plan)


async def _apply(env: node_verbs.Env, plan: Plan) -> dict[str, Any]:
    deps, fid = env.deps, env.fid
    store = deps.factory_store
    item = plan.item
    nodes = item.nodes or ([item.node_ref] if item.node_ref else [])
    new = await asyncio.to_thread(
        functools.partial(
            store.create_checkpoint,
            fid,
            question=item.question,
            status="answered",
            decided_by_kind="person",
            decided_by=env.user_id or "human",
            decided_by_name=env.by,
            node_ref=item.node_ref or (nodes[0] if nodes else None),
            project_slug=item.project_slug,
            options=item.options,
            choice_id=plan.option_id,
            choice_label=plan.label,
            direction=plan.direction,
            why=clip(f"changed direction from {item.ref}", 300),
            decision_class=item.decision_class,
            policy={"who": "person", "reasons": ["a person changed direction"]},
            facts={"at": now_s(), "overrides_status": item.status},
            overrides=item.ref,
            asked_by_session_id=item.asker,
        )
    )
    if item.row is not None:
        try:
            await asyncio.to_thread(
                functools.partial(
                    store.update_checkpoint,
                    item.row.id,
                    expect_status=list(_CHANGEABLE),
                    status="changed",
                    changed_by=new.ref,
                )
            )
        except CheckpointConflict:
            plan.warnings.append(f"{item.ref} changed meanwhile.")
    lines: list[str] = []
    backs: list[dict[str, Any]] = []
    for step in plan.steps:
        if step.kind == "keep":
            continue
        try:
            line, back = await _run_step(env, step, new.ref)
        except CockpitError as exc:
            line, back = f"{step.text.rstrip('.')} failed: {exc.message}", None
        except Exception as exc:  # noqa: BLE001 - one step failing does not undo the rest
            _logger.warning("direction: step %s %s failed", step.kind, step.ref, exc_info=True)
            line, back = f"{step.text.rstrip('.')} failed ({type(exc).__name__}).", None
        lines.append(line)
        if back is not None:
            backs.append(back)
    effects = [
        {"ref": s.ref, "kind": s.kind, "text": t}
        for s, t in zip([s for s in plan.steps if s.kind != "keep"], lines, strict=True)
    ]
    new = (
        await asyncio.to_thread(
            functools.partial(store.update_checkpoint, new.id, effects=effects)
        )
        or new
    )
    mail_id = None
    if plan.to is not None:
        text = _message(new, plan, env.by, lines)
        mail_id = control.send_text(env.app_state, plan.to.id, text, kind="request")
    what = plan.label or "a new direction"
    line = f"{env.by} changed {item.ref} to {what} ({new.ref})"
    if lines:
        line += ": " + " ".join(lines)
    await events.emit(
        env.app_state,
        fid,
        checkpoints.KIND_CHANGED,
        severity="notice",
        project=item.project_slug,
        to_id=plan.to.id if plan.to is not None else None,
        subject=item.node_ref or (nodes[0] if nodes else None),
        summary=clip(line, 300),
        data={
            **env.actor.event_data(),
            "checkpoint": new.ref,
            "overrides": item.ref,
            "option_id": plan.option_id,
            "label": plan.label,
            "direction": clip(plan.direction, 300) if plan.direction else None,
            "steps": lines,
            "mail_id": mail_id,
        },
    )
    token = secrets.token_hex(12)
    expires = now_s() + UNDO_WINDOW_S
    record = {
        "kind": VERB,
        "checkpoint": new.id,
        "overrides": item.ref,
        "stored": item.row.id if item.row is not None else None,
        "status": item.status,
        "backs": backs,
        "to": plan.to.id if plan.to is not None else None,
    }
    node = nodes[0] if nodes else "product"
    await events.emit(
        env.app_state,
        fid,
        node_verbs.KIND_APPLIED,
        severity="notice",
        subject=f"{node_verbs.UNDO_SUBJECT}{token}",
        summary=clip(line, 300),
        data={
            **env.actor.event_data(),
            "verb": VERB,
            "ref": node,
            "args": {"checkpoint": item.ref, "option_id": plan.option_id},
            "undo": record,
            "token": token,
            "expires_at": expires,
            "by_user": env.user_id,
        },
    )
    meta: dict[str, Any] = {
        "verb": VERB,
        "label": f"Change {item.ref} to {what}",
        "by": env.by,
        "undo_token": token,
        "undo_until": expires,
    }
    if env.user_id:
        meta["by_user"] = env.user_id
    await chat_cards.post_node_card(
        env.app_state,
        fid,
        node,
        "preview_applied",
        clip(line, 300),
        meta=meta,
        message_id=derived_id(fid, "applied", token),
    )
    fresh = await asyncio.to_thread(checkpoints.load, deps, fid)
    shown = fresh.get(new.ref)
    return {
        "checkpoint": checkpoints.detail_view(shown, can_change=True) if shown else None,
        "changed": item.ref,
        "steps": lines,
        "warnings": plan.warnings,
        "replan": {"to": plan.to_name, "mail_id": mail_id},
        "undo": {"token": token, "expires_at": iso_utc(expires)},
        "line": line,
        "message": line,
    }


# ── Undo ───────────────────────────────────────────────────────


async def undo(env: node_verbs.Env, record: Mapping[str, Any]) -> str:
    """
    Take a change of direction back (``POST …/undo/{token}``, via :mod:`.node_verbs`):
    the old checkpoint stands again, what was undone is filed again, the revert
    tasks are cancelled, and the agent is told. :returns: What it did, in one line.
    """
    from omnigent.server.factory import tasks

    deps, fid = env.deps, env.fid
    store = deps.factory_store
    new = await asyncio.to_thread(store.get_checkpoint, str(record.get("checkpoint")))
    if new is None:
        raise _err(409, "undo_closed", "the change of direction is gone")
    old_ref = str(record.get("overrides") or "")
    done: list[str] = []
    for back in record.get("backs") or []:
        kind = back.get("kind")
        try:
            if kind == "refile":
                filed = await node_verbs.file_and_apply(
                    env,
                    [dict(op) for op in back.get("ops") or []],
                    f"Undo of the change of direction at {new.ref}: {back.get('ref')} again.",
                )
                done.append(f"{back.get('ref')} filed again as {proposal_ref(filed.number)}")
            elif kind == "task":
                await tasks.edit_task(
                    env.app_state,
                    fid,
                    str(back.get("ref")),
                    tasks.TaskEditIn(cancel=True),
                    env.actor,
                    env.by,
                )
                done.append(f"{back.get('ref')} cancelled")
            elif kind == "keep":
                done.append(str(back.get("text") or back.get("ref")))
        except CockpitError as exc:
            done.append(f"{back.get('ref')} not put back: {exc.message}")
    stored = record.get("stored")
    if stored:
        with contextlib.suppress(CheckpointConflict):
            await asyncio.to_thread(
                functools.partial(
                    store.update_checkpoint,
                    str(stored),
                    expect_status="changed",
                    status=str(record.get("status") or "decided"),
                    changed_by=None,
                )
            )
    # The person's checkpoint no longer overrides anything; it reads as their changed mind.
    await asyncio.to_thread(
        functools.partial(
            store.update_checkpoint,
            new.id,
            status="changed",
            overrides=None,
            why=clip(f"undone by {env.by}; {old_ref} stands", 300),
        )
    )
    to = record.get("to")
    if isinstance(to, str) and to:
        text = (
            f"[Factory decision · {new.ref} undone by {env.by}]\n"
            f"The change of direction from {old_ref} is taken back: carry on as {old_ref} "
            "decided." + (f" Put back: {'; '.join(done)}." if done else "")
        )
        control.send_text(env.app_state, to, text, kind="request")
    await events.emit(
        env.app_state,
        fid,
        checkpoints.KIND_CHANGED,
        severity="notice",
        subject=new.node_ref,
        summary=f"{env.by} undid {new.ref}: {old_ref} stands again",
        data={**env.actor.event_data(), "checkpoint": new.ref, "restored": old_ref},
    )
    return f"{old_ref} stands again" + (f" ({'; '.join(done)})" if done else "")


__all__ = [
    "VERB",
    "DirectionIn",
    "Plan",
    "Step",
    "apply",
    "plan_change",
    "preview_view",
    "undo",
]
