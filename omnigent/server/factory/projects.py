"""One write path for projects: create, update, cancel, split and merge.

The cockpit forms, the human routes and applied proposals (agents through
``sys_factory_project``) all call these cores, each with the :class:`Actor`
that acts. Callers authorize; the cores validate state, write rows, tell the
PLs and log events. A new project's PL is started by the server
(:mod:`.spawn`), or held until its ``depends_on`` are met (:mod:`.gating`).

Lifecycle: a project is ``active`` until it is cancelled (its PL is stopped,
its PRs stay open), merged into another (``parent_slug`` names the survivor)
or done. Split and merge move PRs only through an explicit ``pr_map``; an open
PR left on a project that closes refuses the plan with ``unmapped_prs``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryPr, FactoryProject
from omnigent.factory import STAGE_DONE, STAGE_MERGED, STAGE_READY
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE, LEVEL_READ
from omnigent.server.factory import control, events, gating, spawn, start
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.charter import (
    SPAWN_SERVER,
    normalize_charter,
    preview_tooling_ref,
    project_depends_on,
    render_pl_charter,
)
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError, human_name
from omnigent.stores.factory_store import norm_id, now_s

_logger = logging.getLogger(__name__)

STATUS_ACTIVE: Final = "active"
STATUS_CANCELLED: Final = "cancelled"
STATUS_MERGED: Final = "merged"
KEEP_CANCEL: Final = "cancel"
KEEP_UMBRELLA: Final = "umbrella"
Keep = Literal["cancel", "umbrella"]


@dataclass(frozen=True)
class Started:
    """
    How a new project's PL starts.

    :param path: ``server`` (spawning), ``lead`` (Atlas was mailed the spawn
        call), ``held`` (waiting on dependencies) or ``none`` (not started).
    :param mail_id: Atlas's request on the ``lead`` path.
    :param waiting_on: The unmet dependencies of a held project.
    """

    path: str
    mail_id: str | None = None
    waiting_on: tuple[str, ...] = ()


@dataclass
class SplitResult:
    """The children a split created and how each PL starts."""

    children: list[FactoryProject]
    started: dict[str, Started] = field(default_factory=dict)


# ── Actors ─────────────────────────────────────────────────────


def human_role(deps: FactoryDeps, user_id: str | None, root_id: str) -> str | None:
    """:returns: ``owner`` / ``operator`` / ``viewer`` from the user's level on the root."""
    store = deps.permission_store
    if store is None or not user_id:
        return None
    level = store.get_permission_level(user_id, root_id)
    if level is None:
        return None
    if level >= LEVEL_MANAGE:
        return "owner"
    if level >= LEVEL_EDIT:
        return "operator"
    return "viewer" if level >= LEVEL_READ else None


def human_actor(deps: FactoryDeps, user_id: str | None, root_id: str) -> Actor:
    """:returns: The :class:`Actor` of a person acting from the cockpit."""
    name = human_name(user_id)
    return Actor.human(user_id or name, human_role(deps, user_id, root_id), name=name)


def actor_name(actor: Actor) -> str:
    """:returns: How server texts name the actor, e.g. ``"alice"`` or ``"Atlas"``."""
    if actor.kind == "human":
        return human_name(actor.user_id)
    return actor.title if actor.kind == "agent" else "server"


def _event_data(actor: Actor, **extra: Any) -> dict[str, Any]:
    by = actor_name(actor)
    return {"from": by, **actor.event_data(), "by": by, "by_id": actor.id, **extra}


# ── Lookups ────────────────────────────────────────────────────


async def _project(deps: FactoryDeps, root: Conversation, slug: str) -> FactoryProject:
    row = await asyncio.to_thread(deps.factory_store.get_project, root.id, slug)
    if row is None:
        raise CockpitError(404, "unknown_project", f"no project {slug!r} in this factory")
    return row


async def _active(deps: FactoryDeps, root: Conversation, slug: str) -> FactoryProject:
    row = await _project(deps, root, slug)
    if row.status != STATUS_ACTIVE:
        raise CockpitError(
            409, "project_closed", f"project {slug!r} is {row.status}", status=row.status
        )
    return row


def cap_candidates(projects: Sequence[FactoryProject]) -> dict[str, list[str]]:
    """
    Projects a human could close to make room under the project cap.

    :returns: ``{"done_candidates": [...], "park_candidates": [...]}``: merged or
        done work, and work only waiting for a human merge.
    """
    done, park = [], []
    for project in projects:
        if project.status != STATUS_ACTIVE:
            continue
        if project.stage in (STAGE_MERGED, STAGE_DONE):
            done.append(project.slug)
        elif project.stage == STAGE_READY:
            park.append(project.slug)
    return {"done_candidates": done, "park_candidates": park}


def over_caps(active: int, adding: int, projects: Sequence[FactoryProject]) -> None:
    """
    Refuse a plan that would run more projects than the factory's cap.

    :raises CockpitError: 409 ``over_caps`` listing done and park candidates.
    """
    cap = start.max_projects()
    if active + adding <= cap:
        return
    candidates = cap_candidates(projects)
    raise CockpitError(
        409,
        "over_caps",
        f"the factory runs at most {cap} projects; it has {active} and this adds {adding}",
        cap=cap,
        active=active,
        done_candidates=candidates["done_candidates"],
        park_candidates=candidates["park_candidates"],
    )


def check_dependencies(
    rows: Sequence[FactoryProject],
    new: Mapping[str, tuple[Sequence[str], str]],
    *,
    closing: Sequence[str] = (),
) -> None:
    """
    Refuse ``depends_on`` that names no project, or that closes a cycle.

    :param rows: The factory's projects.
    :param new: Slug -> ``(its depends_on, the field to blame)`` for the projects
        being created or re-pointed.
    :param closing: Projects the same plan closes (known, but out of the graph).
    :raises CockpitError: 400 ``unknown_dependency``, 409 ``cycle`` with the path.
    """
    by_slug = {p.slug: p for p in rows}
    known = set(by_slug) | set(new)
    for deps_, at in new.values():
        for dep in deps_:
            if dep not in known:
                raise CockpitError(
                    400, "unknown_dependency", f"depends_on {dep!r} names no project", field=at
                )
    graph: dict[str, list[str]] = {
        p.slug: project_depends_on(p)[0]
        for p in rows
        if p.status == STATUS_ACTIVE and p.slug not in new and p.slug not in closing
    }
    graph.update({slug: list(deps_) for slug, (deps_, _at) in new.items()})

    def alias(slug: str) -> str:
        row = gating.resolve_alias(slug, by_slug)
        return row.slug if row is not None else slug

    cycle = gating.find_cycle({s: [alias(d) for d in ds] for s, ds in graph.items()})
    if cycle is not None:
        field = next(iter(new.values()))[1] if len(new) == 1 else "depends_on"
        raise CockpitError(
            409, "cycle", f"depends_on cycle: {' → '.join(cycle)}", field=field, path=cycle
        )


async def drop_unstarted(
    app_state: Any, root: Conversation, created: Sequence[FactoryProject]
) -> list[str]:
    """
    Take back projects a failed plan created before their PLs started.

    Deleted when no PR points at them, else cancelled, so the spawn sweep
    never picks them up. :returns: The slugs taken back.
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    if not created:
        return []
    prs = await asyncio.to_thread(store.list_prs, root.id)
    owned = {p.project_slug for p in prs}
    dropped: list[str] = []
    for project in created:
        try:
            if project.slug in owned:
                await asyncio.to_thread(
                    store.update_project, project.id, status=STATUS_CANCELLED, closed_at=now_s()
                )
            else:
                await asyncio.to_thread(store.delete_project, project.id)
        except Exception:  # noqa: BLE001 - one row left behind must not hide the first error
            _logger.warning("factory: could not take back project %s", project.slug, exc_info=True)
            continue
        dropped.append(project.slug)
    return dropped


def check_pr_map(
    owner: str,
    prs: Sequence[FactoryPr],
    pr_map: Mapping[str, str] | Mapping[int, str],
    targets: set[str],
    *,
    must_cover: bool,
    field_prefix: str = "pr_map",
) -> dict[int, str]:
    """
    Validate a PR reassignment off project *owner*.

    :param prs: The factory's PRs.
    :param pr_map: PR number -> the project that takes it.
    :param targets: Slugs that may receive PRs.
    :param must_cover: *owner* closes: each of its open PRs must be mapped.
    :returns: ``{number: slug}``.
    :raises CockpitError: 400 ``invalid_body`` or 409 ``unmapped_prs``.
    """
    own = {p.number: p for p in prs if p.project_slug == owner}
    out: dict[int, str] = {}
    for raw, target in pr_map.items():
        try:
            number = int(str(raw).lstrip("#!"))
        except ValueError:
            number = 0
        where = f"{field_prefix}.{raw}"
        if number not in own:
            raise CockpitError(
                400, "invalid_body", f"#{raw} is not a PR of project {owner}", field=where
            )
        if target not in targets:
            raise CockpitError(
                400,
                "invalid_body",
                f"{target!r} cannot take PRs here; one of {sorted(targets)}",
                field=where,
            )
        out[number] = target
    if must_cover:
        missing = sorted(n for n, p in own.items() if p.state == "open" and n not in out)
        if missing:
            raise CockpitError(
                409,
                "unmapped_prs",
                f"open PRs of {owner} need a project in pr_map: "
                + ", ".join(f"#{n}" for n in missing),
                field=field_prefix,
                prs=missing,
            )
    return out


async def _reassign(
    deps: FactoryDeps, root: Conversation, owner: str, pr_map: Mapping[int, str]
) -> dict[str, list[int]]:
    """Move PRs per *pr_map*; :returns: receiving slug -> the PR numbers it got."""
    store = deps.factory_store
    prs = await asyncio.to_thread(store.list_prs, root.id)
    moved: dict[str, list[int]] = {}
    for pr in prs:
        target = pr_map.get(pr.number) if pr.project_slug == owner else None
        if target is None or target == owner:
            continue
        await asyncio.to_thread(store.update_pr, pr.id, project_slug=target)
        if pr.state == "open":
            moved.setdefault(target, []).append(pr.number)
    return moved


# ── Starting a PL ──────────────────────────────────────────────


async def start_project(
    app_state: Any,
    root: Conversation,
    project: FactoryProject,
    *,
    actor: Actor,
    proposal_ref: str | None = None,
) -> Started:
    """Spawn *project*'s PL now (server or lead), or hold it on its dependencies."""
    deps = factory_deps(app_state)
    store = deps.factory_store
    rows, prs = await asyncio.gather(
        asyncio.to_thread(store.list_projects, root.id),
        asyncio.to_thread(store.list_prs, root.id),
    )
    unmet = gating.unmet_dependencies(project, {p.slug: p for p in rows}, prs)
    if unmet:
        await gating.hold(app_state, root, project, unmet, data={"proposal": proposal_ref})
        return Started("held", waiting_on=tuple(unmet))
    outcome = await spawn.launch(app_state, root, project, actor=actor, proposal_ref=proposal_ref)
    return Started(outcome.path, outcome.mail_id)


# ── Create ─────────────────────────────────────────────────────


async def create_project_core(
    app_state: Any,
    root: Conversation,
    project: start.ProjectIn,
    actor: Actor,
    *,
    defaults: start.FactoryDefaults | None = None,
    parent_slug: str | None = None,
    spawn_now: bool = True,
    proposal_ref: str | None = None,
    check_caps: bool = True,
    cap_allowance: int = 0,
    check_depends: bool = True,
) -> tuple[FactoryProject, Started]:
    """
    Store a new project and start its PL (unless *spawn_now* is off).

    :param defaults: The factory's default repo and base (read from the root when ``None``).
    :param parent_slug: The project it was split from.
    :param proposal_ref: The applied proposal, e.g. ``"C-4"``.
    :param check_caps: Off when the caller checked the cap for its whole plan.
    :param cap_allowance: Projects the caller's plan still closes after this create.
    :param check_depends: Off when the caller checked ``depends_on`` for its whole plan.
    :returns: ``(row, how its PL starts)``.
    :raises CockpitError: 409 ``slug_exists`` / ``over_caps`` / ``cycle``, 400
        ``unknown_dependency``.
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    defaults = defaults or start.root_defaults(root)
    async with start.lock(root.id):
        if await asyncio.to_thread(store.get_project, root.id, project.slug) is not None:
            raise CockpitError(
                409, "slug_exists", f"project {project.slug!r} already exists", field="slug"
            )
        rows = await asyncio.to_thread(store.list_projects, root.id)
        if check_depends:
            check_dependencies(rows, {project.slug: (project.depends_on or [], "depends_on")})
        if check_caps:
            active = sum(1 for p in rows if p.status == STATUS_ACTIVE)
            over_caps(active - cap_allowance, 1, rows)
        fields = start.project_fields(project, actor_name(actor), defaults)
        fields["charter"] = {**fields["charter"], "spawn_owner": SPAWN_SERVER}
        fields["created_by"] = actor.id if actor.kind != "human" else actor_name(actor)
        if parent_slug:
            fields["parent_slug"] = parent_slug
        row = await asyncio.to_thread(store.upsert_project, root.id, project.slug, **fields)
    started = Started("none")
    if spawn_now:
        started = await start_project(app_state, root, row, actor=actor, proposal_ref=proposal_ref)
    by = actor_name(actor)
    via = f" via {proposal_ref}" if proposal_ref else " via cockpit"
    await events.emit(
        app_state,
        root.id,
        "project.added",
        severity="notice",
        project=row.slug,
        to_id=root.id,
        subject=f"project:{row.slug}",
        summary=f"{by} added project {row.slug}{via}",
        data=_event_data(
            actor,
            mail_id=started.mail_id,
            proposal=proposal_ref,
            parent_slug=parent_slug,
            start=started.path,
        ),
    )
    return (await asyncio.to_thread(store.get_project, root.id, row.slug)) or row, started


# ── Update ─────────────────────────────────────────────────────


async def patch_project_core(
    app_state: Any,
    root: Conversation,
    slug: str,
    patch: start.CharterPatchIn,
    actor: Actor,
    *,
    via: str | None = None,
    inherited: Sequence[int] = (),
    check_depends: bool = True,
) -> tuple[FactoryProject, str, str | None]:
    """
    Change a project's charter and send its PL a CHARTER UPDATE with the diff.

    A PATCH that changes nothing sends nothing (unless PRs were *inherited*); a
    project without a PL gets the new charter when it is spawned.

    :param via: The proposal applied, e.g. ``"C-4"``.
    :param inherited: PR numbers the project just received (split or merge).
    :param check_depends: Off when the caller checked ``depends_on`` for its whole plan.
    :returns: ``(row, unified diff or "", mail id or None)``.
    :raises CockpitError: 404 ``unknown_project``, 409 ``project_closed`` / ``cycle``,
        400 ``unknown_dependency``.
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    row = await _active(deps, root, slug)
    if check_depends and "depends_on" in patch.model_fields_set:
        if patch.depends_on and slug in patch.depends_on:
            raise CockpitError(
                400, "invalid_body", "a project cannot depend on itself", field="depends_on"
            )
        rows = await asyncio.to_thread(store.list_projects, root.id)
        check_dependencies(rows, {slug: (patch.depends_on or [], "depends_on")})
    fields: dict[str, Any] = {
        "charter": normalize_charter(
            start.merge_charter(row.charter, patch), tooling_ref=preview_tooling_ref()
        )
    }
    if "priority" in patch.model_fields_set and patch.priority is not None:
        fields["priority"] = patch.priority
    updated = await asyncio.to_thread(store.update_project, row.id, **fields) or row
    diff, mail_id = await _announce_charter(
        app_state,
        root,
        row,
        updated,
        actor,
        via=via,
        fields=sorted(patch.model_fields_set),
        inherited=inherited,
    )
    if {"depends_on", "depends_mode"} & patch.model_fields_set:
        await gating.release_ready(app_state, root.id, trigger="charter_update")
    return updated, diff, mail_id


async def restore_project_core(
    app_state: Any,
    root: Conversation,
    slug: str,
    charter: Mapping[str, Any] | None,
    priority: int | None,
    actor: Actor,
    *,
    via: str | None = None,
) -> FactoryProject:
    """Put back a charter and priority (an undo); the PL gets the inverse CHARTER UPDATE."""
    deps = factory_deps(app_state)
    row = await _active(deps, root, slug)
    fields: dict[str, Any] = {"charter": normalize_charter(charter)}
    if priority is not None:
        fields["priority"] = priority
    updated = await asyncio.to_thread(deps.factory_store.update_project, row.id, **fields) or row
    await _announce_charter(app_state, root, row, updated, actor, via=via, fields=["undo"])
    return updated


async def _announce_charter(
    app_state: Any,
    root: Conversation,
    before_row: FactoryProject,
    after_row: FactoryProject,
    actor: Actor,
    *,
    via: str | None,
    fields: Sequence[str],
    inherited: Sequence[int] = (),
) -> tuple[str, str | None]:
    """Tell the PL what changed (and what it inherited); :returns: ``(diff, mail id)``."""
    deps = factory_deps(app_state)
    slug = after_row.slug
    before, after = render_pl_charter(before_row), render_pl_charter(after_row)
    diff = start.charter_diff(before, after) if before != after else ""
    if not diff and not inherited:
        return "", None
    by = actor_name(actor) + (f" ({via})" if via else "")
    pl = await asyncio.to_thread(control.find_project_pl, deps, root.id, slug)
    mail_id = (
        control.send_text(
            app_state, pl.id, start.charter_update_text(by, diff, inherited), kind="request"
        )
        if pl is not None
        else None
    )
    await events.emit(
        app_state,
        root.id,
        "control.charter_update",
        severity="notice",
        project=slug,
        to_id=pl.id if pl is not None else None,
        subject=f"project:{slug}",
        summary=f"{by} updated the charter of project {slug}",
        data=_event_data(
            actor,
            fields=list(fields),
            diff=diff,
            inherited=list(inherited),
            proposal=via,
            recipients=[norm_id(pl.id)] if pl is not None else [],
            mail_ids={pl.id: mail_id} if pl is not None and mail_id else {},
        ),
    )
    return diff, mail_id


# ── Cancel ─────────────────────────────────────────────────────


async def _stop_pl(
    app_state: Any,
    root: Conversation,
    slug: str,
    reason: str | None,
    actor: Actor,
    user_id: str | None,
    *,
    teardown_previews: bool,
) -> dict[str, str]:
    """
    STOP the project's PL (or PAUSE it with previews kept); :returns: its mail ids.

    The caller authorized the cancel (operators may cancel), so the control runs
    with the factory creator's access, which a member STOP needs.
    """
    deps = factory_deps(app_state)
    pl = await asyncio.to_thread(control.find_project_pl, deps, root.id, slug)
    if pl is None:
        return {}
    creator = await asyncio.to_thread(spawn.creator_of, deps, root)
    acting = creator or user_id or actor.user_id
    action = control.ACTION_STOP if teardown_previews else control.ACTION_PAUSE
    try:
        plan = await control.plan_control(
            app_state,
            root.id,
            user_id=acting,
            action=action,
            target=f"{control.TARGET_PROJECT_PREFIX}{slug}",
            note=reason,
            options=None if teardown_previews else {"keep_previews": True},
        )
    except CockpitError as exc:
        if exc.code == "nothing_to_do":
            return {}
        raise
    plan.by = actor_name(actor)
    result = await control.execute_control(app_state, plan)
    return result.mail_ids


async def cancel_project_core(
    app_state: Any,
    root: Conversation,
    slug: str,
    reason: str | None,
    actor: Actor,
    *,
    teardown_previews: bool = True,
    user_id: str | None = None,
    via: str | None = None,
) -> FactoryProject:
    """
    Cancel a project: stop its PL (STOP, or PAUSE keeping previews) and mark it cancelled.

    Its PRs stay open; closing them is a human decision.

    :param user_id: Whose access the stop runs with when the factory has no creator.
    :raises CockpitError: 404 ``unknown_project``, 409 ``project_closed``.
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    row = await _active(deps, root, slug)
    mail_ids = await _stop_pl(
        app_state, root, slug, reason, actor, user_id, teardown_previews=teardown_previews
    )
    updated = (
        await asyncio.to_thread(
            store.update_project, row.id, status=STATUS_CANCELLED, closed_at=now_s()
        )
        or row
    )
    prs = await asyncio.to_thread(store.list_prs, root.id, ["open"])
    left_open = sorted(p.number for p in prs if p.project_slug == slug)
    by = actor_name(actor)
    await events.emit(
        app_state,
        root.id,
        "project.cancelled",
        severity="notice",
        project=slug,
        subject=f"project:{slug}",
        summary=f"{by} cancelled project {slug}" + (f": {reason}" if reason else ""),
        data=_event_data(
            actor,
            reason=reason,
            prs_left_open=left_open,
            teardown_previews=teardown_previews,
            proposal=via,
            mail_ids=mail_ids,
        ),
    )
    return updated


# ── Split ──────────────────────────────────────────────────────


async def split_project_core(
    app_state: Any,
    root: Conversation,
    slug: str,
    into: Sequence[start.ProjectIn],
    keep: Keep,
    pr_map: Mapping[str, str] | Mapping[int, str],
    actor: Actor,
    *,
    reason: str | None = None,
    user_id: str | None = None,
    via: str | None = None,
    spawn_now: bool = True,
    cap_allowance: int = 0,
    check_depends: bool = True,
) -> SplitResult:
    """
    Split a project into new ones; ``keep="cancel"`` closes it, ``"umbrella"`` keeps it.

    The project cap is checked once for the whole split. When a child cannot be
    created, the children already created are taken back.

    :param spawn_now: Off when the caller starts the children's PLs itself.
    :param cap_allowance: Projects the caller's plan still closes after this split.
    :param check_depends: Off when the caller checked ``depends_on`` for its whole plan.
    :raises CockpitError: 409 ``unmapped_prs`` when the original closes with open
        PRs pr_map leaves behind; 409 ``slug_exists`` / ``over_caps`` / ``cycle``.
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    await _active(deps, root, slug)
    prs = await asyncio.to_thread(store.list_prs, root.id)
    targets = {p.slug for p in into} | ({slug} if keep == KEEP_UMBRELLA else set())
    mapping = check_pr_map(slug, prs, pr_map, targets, must_cover=keep == KEEP_CANCEL)
    rows = await asyncio.to_thread(store.list_projects, root.id)
    if check_depends:
        check_dependencies(
            rows,
            {c.slug: (c.depends_on or [], f"into[{i}].depends_on") for i, c in enumerate(into)},
            closing=[slug] if keep == KEEP_CANCEL else [],
        )
    active = sum(1 for p in rows if p.status == STATUS_ACTIVE)
    closing = 1 if keep == KEEP_CANCEL else 0
    over_caps(active - closing - cap_allowance, len(into), rows)
    children: list[FactoryProject] = []
    try:
        for child in into:
            row, _ = await create_project_core(
                app_state,
                root,
                child,
                actor,
                parent_slug=slug,
                spawn_now=False,
                proposal_ref=via,
                check_caps=False,
                check_depends=False,
            )
            children.append(row)
    except Exception:
        await drop_unstarted(app_state, root, children)
        raise
    moved = await _reassign(deps, root, slug, mapping)
    if keep == KEEP_CANCEL:
        await cancel_project_core(
            app_state,
            root,
            slug,
            reason or f"split into {', '.join(p.slug for p in into)}",
            actor,
            user_id=user_id,
            via=via,
        )
    result = SplitResult(children)
    for child in children if spawn_now else []:
        result.started[child.slug] = await start_project(
            app_state, root, child, actor=actor, proposal_ref=via
        )
    by = actor_name(actor)
    await events.emit(
        app_state,
        root.id,
        "project.split",
        severity="notice",
        project=slug,
        subject=f"project:{slug}",
        summary=f"{by} split {slug} into {', '.join(c.slug for c in children)} (keep={keep})",
        data=_event_data(
            actor,
            into=[c.slug for c in children],
            keep=keep,
            pr_map={str(k): v for k, v in mapping.items()},
            moved=moved,
            reason=reason,
            proposal=via,
        ),
    )
    return result


# ── Merge ──────────────────────────────────────────────────────


async def merge_projects_core(
    app_state: Any,
    root: Conversation,
    survivor: str,
    absorbed: Sequence[str],
    patch: start.CharterPatchIn | None,
    pr_map: Mapping[str, str] | Mapping[int, str],
    actor: Actor,
    *,
    reason: str | None = None,
    user_id: str | None = None,
    via: str | None = None,
    check_depends: bool = True,
) -> FactoryProject:
    """
    Merge *absorbed* projects into *survivor*: their PLs stop, their PRs move per pr_map.

    The survivor's PL gets a CHARTER UPDATE naming the inherited PRs (their previews
    died with the old pods).

    :param check_depends: Off when the caller checked ``depends_on`` for its whole plan.
    :raises CockpitError: 400 on a bad plan, 409 ``unmapped_prs`` / ``project_closed`` /
        ``cycle``.
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    if survivor in absorbed or len(set(absorbed)) != len(absorbed) or not absorbed:
        raise CockpitError(
            400, "invalid_body", "from names other projects than into, once each", field="from"
        )
    await _active(deps, root, survivor)
    for slug in absorbed:
        await _active(deps, root, slug)
    prs = await asyncio.to_thread(store.list_prs, root.id)
    rows = await asyncio.to_thread(store.list_projects, root.id)
    targets = {p.slug for p in rows if p.status == STATUS_ACTIVE} - set(absorbed)
    keyed = {str(k).lstrip("#!"): v for k, v in pr_map.items()}
    mappings: dict[str, dict[int, str]] = {}
    for slug in absorbed:
        own = {str(p.number) for p in prs if p.project_slug == slug}
        part = {k: v for k, v in keyed.items() if k in own}
        mappings[slug] = check_pr_map(slug, prs, part, targets, must_cover=True)
    unknown = sorted(set(keyed) - {str(n) for m in mappings.values() for n in m})
    if unknown:
        raise CockpitError(
            400,
            "invalid_body",
            f"#{unknown[0]} is not a PR of the merged projects",
            field=f"pr_map.{unknown[0]}",
        )
    if check_depends and patch is not None and "depends_on" in patch.model_fields_set:
        check_dependencies(
            rows,
            {survivor: (patch.depends_on or [], "patch.depends_on")},
            closing=absorbed,
        )
    moved: dict[str, list[int]] = {}
    for slug in absorbed:
        for target, numbers in (await _reassign(deps, root, slug, mappings[slug])).items():
            moved.setdefault(target, []).extend(numbers)
        await _stop_pl(app_state, root, slug, reason, actor, user_id, teardown_previews=True)
        row = await _project(deps, root, slug)
        await asyncio.to_thread(
            store.update_project,
            row.id,
            status=STATUS_MERGED,
            parent_slug=survivor,
            closed_at=now_s(),
        )
    updated, _diff, _mail = await patch_project_core(
        app_state,
        root,
        survivor,
        patch or start.CharterPatchIn(),
        actor,
        via=via,
        inherited=sorted(moved.get(survivor, [])),
        check_depends=False,
    )
    for target, numbers in moved.items():
        if target != survivor:
            await patch_project_core(
                app_state,
                root,
                target,
                start.CharterPatchIn(),
                actor,
                via=via,
                inherited=sorted(numbers),
            )
    by = actor_name(actor)
    await events.emit(
        app_state,
        root.id,
        "project.merged",
        severity="notice",
        project=survivor,
        subject=f"project:{survivor}",
        summary=f"{by} merged {', '.join(absorbed)} into {survivor}",
        data=_event_data(
            actor,
            into=survivor,
            absorbed=list(absorbed),
            pr_map={str(n): s for m in mappings.values() for n, s in m.items()},
            moved=moved,
            reason=reason,
            proposal=via,
        ),
    )
    await gating.release_ready(app_state, root.id, trigger="project.merged")
    return updated


__all__ = [
    "KEEP_CANCEL",
    "KEEP_UMBRELLA",
    "STATUS_ACTIVE",
    "STATUS_CANCELLED",
    "STATUS_MERGED",
    "SplitResult",
    "Started",
    "actor_name",
    "cancel_project_core",
    "cap_candidates",
    "check_dependencies",
    "check_pr_map",
    "create_project_core",
    "drop_unstarted",
    "human_actor",
    "human_role",
    "merge_projects_core",
    "over_caps",
    "patch_project_core",
    "restore_project_core",
    "split_project_core",
    "start_project",
]
