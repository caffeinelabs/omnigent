"""Preview and restore of definition changes (people only; human origin on the routes).

- :func:`preview_change` is a dry run of ``PUT …/definition`` (or of a
  restore): the diff with each path's class, who can apply it, what it would
  do, what blocks it, whether it may run as a trial, and the re-rank of
  opportunities (filled once the discovery ledger exists). Nothing is stored.
- :func:`restore_version` stores an old version's document as the next
  version (a human ``C-n``), so history stays linear: its Undo is a re-pin of
  the version before it within :data:`.definition.UNDO_WINDOW_S`, or another
  restore. Trials whose change the restore takes out end as reverted.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from pydantic import Field, ValidationError

from omnigent.factory import THREAD_IMPROVEMENTS
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE
from omnigent.server.factory import control, definition, evaluate, events, people, tuning
from omnigent.server.factory.actor import proposal_ref
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.start import parse_body
from omnigent.stores.factory_store import now_s

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryDefinition, FactoryProposal
    from omnigent.server.factory.actor import Actor

RESTORED_KIND: Final = "definition.restored"


class PreviewIn(tuning._Body):
    """``POST …/definition/preview``: the body of ``PUT …/definition``, or ``{restore: K}``."""

    changes: list[dict[str, Any]] = Field(default_factory=list, max_length=tuning.MAX_CHANGES)
    restore: int | None = Field(None, ge=1)
    note: str | None = Field(None, max_length=2000)
    trial: bool = False
    trial_h: int | None = Field(None, ge=definition.TRIAL_H_MIN, le=definition.TRIAL_H_MAX)
    expected_effect: tuning.EffectIn | None = None
    base_version: int | None = Field(None, ge=1)


class RestoreIn(tuning._Body):
    """``POST …/definition/restore``."""

    version: int = Field(ge=1)
    note: str | None = Field(None, max_length=2000)
    base_version: int | None = Field(None, ge=1)


@dataclass
class RestorePlan:
    """
    What restoring a version does to the version in force.

    :param after: The document the new version stores (the old one, completed).
    :param ending: Trials whose change the restore takes out.
    """

    after: dict[str, Any]
    diff: list[dict[str, Any]]
    ending: list[FactoryProposal] = field(default_factory=list)


def restore_plan(
    deps: FactoryDeps, factory_id: str, stored: Mapping[str, Any], target: FactoryDefinition
) -> RestorePlan:
    """
    :returns: What restoring *target* over *stored* changes (synchronous).
    :raises CockpitError: 422 ``invalid_definition`` when *target* no longer validates.
    """
    try:
        after = definition.full_doc(target.doc or {})
    except ValidationError as exc:
        raise CockpitError(
            422,
            "invalid_definition",
            f"v{target.version} no longer validates: {str(exc)[:300]}",
        ) from None
    trials = deps.factory_store.list_proposals(
        factory_id, status="trial", kind=["tuning", "rules"], limit=200
    )
    ending = [r for r in trials if definition.moved_paths(r.after or {}, after)]
    ended = {r.id for r in ending}
    running = {proposal_ref(r.number) for r in trials if r.id not in ended}
    after["rules"] = _settled_rules(after.get("rules") or [], stored, running)
    diff = definition.diff(stored, after)
    return RestorePlan(after=after, diff=diff, ending=ending)


def _settled_rules(
    rules: Sequence[Mapping[str, Any]], stored: Mapping[str, Any], running: set[str]
) -> list[dict[str, Any]]:
    """
    *rules* of a restored version with their trial state as it stands now: a
    rule the version in force has unchanged keeps its state there, and a rule
    "on trial" whose trial no longer runs is active (no keep or revert would
    ever settle it).
    """
    current = {
        str(r.get("id")): r
        for r in stored.get("rules") or []
        if isinstance(r, Mapping) and r.get("id")
    }
    out: list[dict[str, Any]] = []
    for rule in rules:
        now = current.get(str(rule.get("id")))
        if now is not None and definition.same_rule(now, rule):
            out.append({**rule, "state": now.get("state", "active"), "origin": now.get("origin")})
        elif rule.get("state") == "trial" and rule.get("origin") not in running:
            out.append({**rule, "state": "active"})
        else:
            out.append(dict(rule))
    return out


# ── Preview ────────────────────────────────────────────────────


def _effects(
    deps: FactoryDeps,
    factory_id: str,
    diff: Sequence[Mapping[str, Any]],
    *,
    ending: Sequence[FactoryProposal] = (),
    restore: int | None = None,
    version: int,
    version_next: int,
) -> list[dict[str, Any]]:
    """What applying *diff* does besides storing a version, in plain words."""
    out: list[dict[str, Any]] = []
    paths = [str(d["path"]) for d in diff]
    quiet = [p for p in paths if definition.blocks.in_blocks(p, definition.QUIET_BLOCKS)]
    rules = [d for d in diff if str(d["path"]).startswith("rules.")]
    mailed = [p for p in paths if p not in quiet and not p.startswith("rules.")]
    members = tuning._members(deps, factory_id) if (mailed or rules) else []
    if restore is not None:
        out.append(
            {
                "kind": "restore",
                "text": f"Stores v{restore}'s settings as v{version_next}; v{version} stays in "
                "the history and can be restored.",
            }
        )
    if mailed:
        out.append(
            {
                "kind": "mail",
                "text": f"{len(members)} member(s) get the new values by mail.",
                "count": len(members),
            }
        )
    if quiet:
        out.append(
            {
                "kind": "data",
                "text": "Product, strategy, discovery, brief and role-default changes are data "
                "the factory reads when it needs them; nobody is mailed.",
                "paths": quiet[:20],
            }
        )
    if rules:
        told = tuning._affected(rules, members)
        names = [display_title(m.title, m.id) for m in told]
        out.append(
            {
                "kind": "rules",
                "text": "RULES UPDATE goes to "
                + (", ".join(names[:6]) if names else "nobody running now")
                + ".",
                "members": [m.id for m in told],
            }
        )
    if any(p.startswith("cadence.") for p in mailed):
        out.append(
            {
                "kind": "cadence",
                "text": "Members still on the old heartbeat default move to the new one.",
            }
        )
    for entry in diff:
        if entry["path"] != "discovery.enabled":
            continue
        if entry.get("after") is True and not definition.blocks.discovery_server_on():
            text = (
                "Discovery stays dark: this server has it off "
                f"({definition.blocks.DISCOVERY_ENV})."
            )
        elif entry.get("after") is True:
            text = "Discovery turns on for this factory."
        else:
            text = "Discovery turns off for this factory."
        out.append({"kind": "discovery", "text": text})
    for row in ending:
        ref = proposal_ref(row.number)
        out.append(
            {
                "kind": "ends_trial",
                "text": f"Ends trial {ref}: its change is taken out.",
                "trial": ref,
            }
        )
    return out


def _trial_info(
    deps: FactoryDeps,
    factory_id: str,
    paths: Sequence[str],
    *,
    restore: int | None,
    trial_h: int | None,
) -> dict[str, Any]:
    if restore is not None:
        return {
            "ok": False,
            "code": "restore",
            "reason": "A restore applies as a version; undo it, or restore again.",
        }
    if not paths:
        return {"ok": False, "code": "no_change", "reason": "Nothing to try."}
    ok, reason = definition.trialable(paths)
    if not ok:
        why = "; ".join(dict.fromkeys(definition.not_trialable(paths).values()))
        return {"ok": False, "code": evaluate.NOT_TRIALABLE, "reason": reason, "why": why}
    found = evaluate.overlapping_trial(deps, factory_id, paths)
    if found is not None:
        error = evaluate.overlap_error(found[0], found[1], now_s())
        return {
            "ok": False,
            "code": evaluate.TRIAL_OVERLAP,
            "reason": error.message,
            "trial": proposal_ref(found[0].number),
            "block": found[1],
        }
    return {
        "ok": True,
        "trial_h": trial_h or definition.TRIAL_H_DEFAULT,
        "blocks": definition.trial_blocks(paths),
    }


def _who(klass: str | None, level: int, owner_only: bool) -> str:
    head = f"Owners (class {klass})." if klass else "Owners."
    if level >= LEVEL_MANAGE:
        you = " You are an owner, so it applies when you press Apply."
    elif level >= LEVEL_EDIT:
        you = " It waits for an owner: propose it to them."
    else:
        you = " You can look, not change."
    agents = " Agents can only propose it." if owner_only else ""
    return f"{head}{you}{agents}"


def _parse_all(
    raw: Sequence[Mapping[str, Any]],
) -> tuple[list[definition.Change], list[dict[str, Any]]]:
    changes: list[definition.Change] = []
    blocked: list[dict[str, Any]] = []
    for i, change in enumerate(raw):
        try:
            changes.append(definition.parse_change(change, i))
        except CockpitError as exc:
            blocked.append(
                {
                    "index": i,
                    "path": str(change.get("path") or ""),
                    "code": exc.code,
                    "message": exc.message,
                }
            )
    return changes, blocked


def _preview(
    deps: FactoryDeps, root: Conversation, user_id: str | None, body: PreviewIn
) -> dict[str, Any]:
    resolved = definition.definition_for(deps, root.id, fresh=True)
    assert resolved is not None
    latest = deps.factory_store.latest_definition(root.id)
    version_next = (latest.version if latest is not None else resolved.version) + 1
    blocked: list[dict[str, Any]] = []
    ending: list[FactoryProposal] = []
    if body.restore is not None:
        target = deps.factory_store.get_definition(root.id, body.restore)
        if target is None:
            raise CockpitError(404, "unknown_version", f"no definition v{body.restore}")
        plan = restore_plan(deps, root.id, resolved.stored, target)
        diff, ending = plan.diff, plan.ending
    else:
        changes, blocked = _parse_all(body.changes)
        after: Mapping[str, Any] = resolved.stored
        try:
            after = definition.apply_changes(resolved.stored, changes)
        except CockpitError as exc:
            blocked.append(
                {
                    "index": None,
                    "path": str(exc.field or exc.extra.get("item") or ""),
                    "code": exc.code,
                    "message": exc.message,
                }
            )
        diff = tuning.preview(definition.diff(resolved.stored, after))
    if body.base_version is not None and body.base_version != resolved.version:
        blocked.append(
            {
                "index": None,
                "path": "",
                "code": "stale_definition",
                "message": f"v{resolved.version} is in force, not v{body.base_version}",
            }
        )
    paths = [str(d["path"]) for d in diff]
    entries = [
        {
            **d,
            "class": definition.classify(str(d["path"]), d.get("before"), d.get("after")),
            "owner_only": definition.owner_only(str(d["path"])),
            "block": definition.trial_block(str(d["path"])),
            "line": definition.diff_lines([d], 1)[0],
        }
        for d in diff
    ]
    klass = tuning.diff_class(diff) if diff else None
    owner_only = any(e["owner_only"] for e in entries)
    level = people.viewer_level(deps, root.id, user_id)
    return {
        "def_version": resolved.version,
        "version_next": version_next,
        "diff": entries,
        "lines": definition.diff_lines(diff, 50),
        "class": klass,
        "approvers": ["owners"],
        "who": _who(klass, level, owner_only),
        "owner_only": owner_only,
        "you_can_apply": level >= LEVEL_MANAGE and bool(diff) and not blocked,
        "you_can_propose": level >= LEVEL_EDIT,
        "blocked": blocked,
        "effects": _effects(
            deps,
            root.id,
            diff,
            ending=ending,
            restore=body.restore,
            version=resolved.version,
            version_next=version_next,
        ),
        "rerank": [],
        "trialable": _trial_info(deps, root.id, paths, restore=body.restore, trial_h=body.trial_h),
        "restore": (
            {"version": body.restore, "ends": [proposal_ref(r.number) for r in ending]}
            if body.restore is not None
            else None
        ),
    }


async def preview_change(
    app_state: Any, factory_id: str, user_id: str | None, payload: Any
) -> dict[str, Any]:
    """
    ``POST …/definition/preview``: what a change (or a restore) would do. Stores nothing.

    :returns: ``{def_version, version_next, diff[], lines[], class, approvers, who,
        owner_only, you_can_apply, you_can_propose, blocked[], effects[], rerank[],
        trialable{ok, code?, reason?, trial?, block?}, restore}``.
    :raises CockpitError: 400 ``invalid_body`` (neither or both of changes and restore);
        404 ``unknown_version``.
    """
    body = parse_body(PreviewIn, payload if payload is not None else {})
    if bool(body.changes) == (body.restore is not None):
        raise CockpitError(400, "invalid_body", "send changes, or the version to restore")
    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    return await asyncio.to_thread(_preview, deps, root, user_id, body)


# ── Restore ────────────────────────────────────────────────────


async def restore_version(
    app_state: Any, factory_id: str, user_id: str | None, payload: Any
) -> dict[str, Any]:
    """
    ``POST …/definition/restore {version, note?}`` (owners): store vK's document as vN+1.

    :returns: ``{proposal, proposal_id, status, def_version, restored, from, ended[]}``.
    :raises CockpitError: 404 ``unknown_version``; 400 ``no_change`` when vK says
        what the version in force says; 409 ``stale_definition`` on a moved ``base_version``.
    """
    from omnigent.server.factory.projects import human_actor

    body = parse_body(RestoreIn, payload if payload is not None else {})
    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_MANAGE)
    # Seeds v1 on a factory nobody has read the definition of yet.
    await asyncio.to_thread(definition.definition_for, deps, root.id)
    target = await asyncio.to_thread(deps.factory_store.get_definition, root.id, body.version)
    if target is None:
        raise CockpitError(404, "unknown_version", f"no definition v{body.version}")
    actor = await asyncio.to_thread(human_actor, deps, user_id, root.id)
    async with tuning.definition_lock(app_state, root.id):
        return await _restore(app_state, root, target, body, actor=actor)


async def _restore(
    app_state: Any,
    root: Conversation,
    target: FactoryDefinition,
    body: RestoreIn,
    *,
    actor: Actor,
) -> dict[str, Any]:
    deps = factory_deps(app_state)
    resolved = await asyncio.to_thread(definition.definition_for, deps, root.id, fresh=True)
    assert resolved is not None
    if body.base_version is not None and body.base_version != resolved.version:
        raise CockpitError(
            409,
            "stale_definition",
            f"v{resolved.version} is in force, not v{body.base_version}; reload and restore again",
            version=resolved.version,
        )
    plan = await asyncio.to_thread(restore_plan, deps, root.id, resolved.stored, target)
    if not plan.diff:
        raise CockpitError(
            400,
            "no_change",
            f"v{target.version} says what v{resolved.version}, the version in force, says",
        )
    kind = "rules" if all(str(d["path"]).startswith("rules.") for d in plan.diff) else "tuning"
    lines = definition.diff_lines(plan.diff, limit=2)
    now = now_s()
    row, _ = await asyncio.to_thread(
        lambda: deps.factory_store.create_proposal(
            root.id,
            kind=kind,
            title=control.clip(f"Restore v{target.version}: " + " · ".join(lines), 256),
            author_kind="human",
            author=actor.id,
            approval="owner",
            class_="human",
            status="applied",
            ops=[{"op": "restore", "version": target.version}],
            before={d["path"]: d["before"] for d in plan.diff},
            after={d["path"]: d["after"] for d in plan.diff},
            based_on={"def_version": resolved.version},
            rationale=body.note or "",
            author_role=actor.role,
            thread=THREAD_IMPROVEMENTS,
            decided_by=actor.id,
            decided_at=now,
            applied_at=now,
            def_version_from=resolved.version,
            result={"restore_of": target.version, "note": body.note, "diff": plan.diff[:30]},
        )
    )
    stored = await asyncio.to_thread(
        lambda: definition.store_version(
            deps,
            root.id,
            plan.after,
            created_by=actor.id,
            created_via="human",
            parent_version=resolved.version,
            proposal_id=row.id,
        )
    )
    row = (
        await asyncio.to_thread(
            deps.factory_store.update_proposal, row.id, def_version_to=stored.version
        )
        or row
    )
    ref = proposal_ref(row.number)
    by = tuning.projects_name(actor)
    await tuning.deliver(
        app_state,
        root,
        ref=ref,
        by=by,
        header=f"v{stored.version} · {ref} restores v{target.version} · by {by}",
        before=resolved.stored,
        after=plan.after,
        version=stored.version,
        actor=actor,
    )
    reason = f"v{target.version} restored by {by} as v{stored.version}"
    ended = [
        found
        for trial in plan.ending
        if (found := await tuning._end_trial(app_state, trial, actor=actor, reason=reason))
    ]
    await tuning._emit(
        app_state,
        row,
        "proposal.applied",
        f"{ref} restored v{target.version} as v{stored.version}",
        actor,
        def_version=stored.version,
        def_version_from=resolved.version,
        restore_of=target.version,
    )
    await events.emit(
        app_state,
        root.id,
        RESTORED_KIND,
        severity="notice",
        subject=f"definition:v{stored.version}",
        summary=f"v{target.version} restored by {by} as v{stored.version}",
        data={
            **actor.event_data(),
            "version": stored.version,
            "restored": target.version,
            "from": resolved.version,
            "ref": ref,
            "ended": ended,
        },
    )
    return {
        "proposal": ref,
        "proposal_id": row.id,
        "status": row.status,
        "def_version": stored.version,
        "restored": target.version,
        "from": resolved.version,
        "ended": ended,
    }


__all__ = [
    "RESTORED_KIND",
    "PreviewIn",
    "RestoreIn",
    "RestorePlan",
    "preview_change",
    "restore_plan",
    "restore_version",
]
