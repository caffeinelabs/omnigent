"""The factory definition (Tier 1): read, edit, pin, keep and revert, and the agents' rules.

People (user JWT; writes carry ``require_human_origin`` and need MANAGE):

- ``GET /v1/factory/factories/{fid}/definition``: the version in force, its
  effective values, bounds, classes and the trials running;
- ``GET …/definition/versions`` (rows carry ``undone_by`` and ``restored_from``),
  ``GET …/definition/diff?from=&to=``;
- ``PUT …/definition`` (a human ``C-n``, applied at once, ``trial`` optional);
- ``POST …/definition/preview``: a dry run of ``PUT`` (or of a restore);
- ``POST …/definition/restore`` ``{version, note?}``: vK's document as a new version;
- ``POST …/definition/pin`` ``{version}`` (re-pinning a fresh version's parent is its Undo);
- ``POST …/proposals/{ref}/keep`` and ``…/revert`` ``{note?}``;
- ``POST …/retro``: run Sentinel's retro now.

Agents (runner tunnel): ``GET /v1/factory/agent/rules`` (every role):
``{def_version, role, project, rules[], product_delivery, environment}`` for
the caller's role and project; ``product_delivery`` is the profile's merge
policy, protected repos and paths that need a demo, ``environment`` how the
product's env runs (:func:`.environment.for_caller`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from omnigent.server.auth import LEVEL_MANAGE, LEVEL_READ
from omnigent.server.factory import (
    control,
    definition,
    definition_changes,
    environment,
    retro,
    schemas,
    tier2,
    tuning,
)
from omnigent.server.factory.actor import proposal_ref
from omnigent.server.factory.agent_scope import authorize_agent
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.membership import is_session_id
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.factory.proposals import proposal_view
from omnigent.server.factory.start import parse_body
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_T = TypeVar("_T")
VERSIONS_MAX = 200


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


class PinBody(BaseModel):
    """``POST …/definition/pin``."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class NoteBody(BaseModel):
    """``POST …/proposals/{ref}/keep|revert``."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(None, max_length=2000)


def _author_title(deps: Any, created_by: str) -> str | None:
    """:returns: The member's title when *created_by* is a session id (an agent's change)."""
    if not is_session_id(created_by):
        return None
    conv = deps.conversation_store.get_conversation(created_by)
    return display_title(conv.title, conv.id) if conv is not None else None


def _undone(deps: Any, factory_id: str) -> dict[int, Any]:
    """:returns: ``{version: its "definition.undone" event}`` (the latest per version)."""
    rows = deps.factory_store.find_events(factory_id, tuning.UNDONE_KIND, 0, limit=1000)
    out: dict[int, Any] = {}
    for event in rows:
        version = (event.data or {}).get("version")
        if isinstance(version, int):
            out[version] = event
    return out


def _view(
    row: Any, pinned: int | None, deps: Any, undone: dict[int, Any] | None = None
) -> dict[str, Any]:
    proposal = deps.factory_store.get_proposal(row.proposal_id) if row.proposal_id else None
    undo = (undone or {}).get(row.version)
    undo_data = (undo.data or {}) if undo is not None else {}
    restored = (proposal.result or {}).get("restore_of") if proposal is not None else None
    return schemas.DefinitionView.model_validate(
        {
            "factory_id": row.factory_id,
            "version": row.version,
            "parent_version": row.parent_version,
            "hash": row.hash,
            "created_by": row.created_by,
            "created_by_title": _author_title(deps, row.created_by),
            "created_via": row.created_via,
            "proposal_id": row.proposal_id,
            "proposal_ref": proposal_ref(proposal.number) if proposal is not None else None,
            "created_at": row.created_at,
            "pinned": row.version == pinned,
            "doc": row.doc or {},
            "undone_by": undo_data.get("by"),
            "undone_at": undo.ts // 1000 if undo is not None else None,
            "restored_from": restored if isinstance(restored, int) else None,
        }
    ).model_dump(mode="json")


async def _root(
    request: Request, factory_id: str, level: int = LEVEL_READ
) -> tuple[Any, Any, Any]:
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    root = await control.load_factory_root(deps, user_id, factory_id)
    if level > LEVEL_READ:
        await control.require(deps, user_id, root.id, level)
    return deps, user_id, root


def _definition_body(deps: Any, root: Any, app_state: Any) -> dict[str, Any]:
    resolved = definition.definition_for(deps, root.id, fresh=True)
    assert resolved is not None
    row = deps.factory_store.get_definition(root.id, resolved.version)
    env = definition.env_defaults()
    trials = deps.factory_store.list_proposals(root.id, status="trial", limit=50)
    return {
        "def_version": resolved.version,
        "version": _view(row, resolved.version, deps) if row is not None else None,
        "effective": resolved.doc,
        "bounds": {
            path: list(definition.bounds(path, env) or definition.BOUNDS[path])
            for path in definition.BOUNDS
        },
        "env_ceilings": sorted(definition.ENV_CEILINGS),
        "classes": [{"paths": p, "class": c} for p, c in definition.CLASS_TABLE],
        "paths": definition.schema_paths(),
        "on_trial": [proposal_view(deps, r) for r in trials],
        "trial_blocks": [
            {
                "proposal": proposal_ref(r.number),
                "blocks": definition.trial_blocks((r.after or {}).keys()),
                "trial_until": r.trial_until,
            }
            for r in trials
        ],
        "lists": {
            path: {"numbered": spec.prefix} for path, spec in definition.blocks.LISTS.items()
        },
        "owner_only": list(definition.OWNER_ONLY_PREFIXES),
        "not_trialable": list(definition.not_trialable_table(root.id)),
        "not_trialable_why": definition.not_trialable_table(root.id),
        "trial_windows_h": {
            definition.GUARDS_DELIVERY: [
                definition.TRIAL_H_MIN,
                definition.TRIAL_H_DEFAULT,
                definition.TRIAL_H_MAX,
            ],
            definition.GUARDS_DISCOVERY: list(definition.DISCOVERY_TRIAL_H),
        },
        "agent_paths": {k: list(v) for k, v in definition.blocks.AGENT_PATHS.items()},
        "discovery_server": definition.blocks.discovery_server_on(),
        "tier2": tier2.status(app_state),
    }


@router.get("/factories/{factory_id}/definition", response_model=None)
async def get_definition(request: Request, factory_id: str) -> dict[str, Any] | JSONResponse:
    """The definition in force (seeded on the first read), with bounds and running trials."""

    async def run() -> dict[str, Any]:
        deps, _user, root = await _root(request, factory_id)
        return await asyncio.to_thread(_definition_body, deps, root, request.app.state)

    return await _guard(run)


@router.get("/factories/{factory_id}/definition/versions", response_model=None)
async def list_definition_versions(
    request: Request, factory_id: str, limit: int = Query(50, ge=1, le=VERSIONS_MAX)
) -> dict[str, Any] | JSONResponse:
    """Every stored version, newest first (``pinned`` marks the one in force)."""

    async def run() -> dict[str, Any]:
        deps, _user, root = await _root(request, factory_id)

        def _read() -> dict[str, Any]:
            resolved = definition.definition_for(deps, root.id)
            pinned = resolved.version if resolved is not None else None
            rows = deps.factory_store.list_definitions(root.id, limit=limit)
            undone = _undone(deps, root.id)
            return {"data": [_view(r, pinned, deps, undone) for r in rows], "pinned": pinned}

        return await asyncio.to_thread(_read)

    return await _guard(run)


@router.get("/factories/{factory_id}/definition/diff", response_model=None)
async def diff_definition(
    request: Request,
    factory_id: str,
    from_: int = Query(..., alias="from", ge=1),
    to: int = Query(..., ge=1),
) -> dict[str, Any] | JSONResponse:
    """``[{path, before, after}]`` between two stored versions."""

    async def run() -> dict[str, Any]:
        deps, _user, root = await _root(request, factory_id)
        a = await asyncio.to_thread(deps.factory_store.get_definition, root.id, from_)
        b = await asyncio.to_thread(deps.factory_store.get_definition, root.id, to)
        if a is None or b is None:
            missing = from_ if a is None else to
            raise CockpitError(404, "unknown_version", f"no definition v{missing}")
        changes = definition.diff(a.doc or {}, b.doc or {})
        return {
            "from": from_,
            "to": to,
            "changes": changes,
            "lines": definition.diff_lines(changes, 50),
        }

    return await _guard(run)


@router.put("/factories/{factory_id}/definition", response_model=None, dependencies=HUMAN_ORIGIN)
async def put_definition(
    request: Request, factory_id: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """A person's edit: ``{changes, note?, trial?, trial_h?, expected_effect?, base_version?}``."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        return await tuning.human_edit(request.app.state, factory_id, user_id, payload)

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/definition/preview", response_model=None, dependencies=HUMAN_ORIGIN
)
async def preview_definition(
    request: Request, factory_id: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """A dry run of ``PUT …/definition`` (or ``{restore: K}``): diff, class, effects, trial."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        return await definition_changes.preview_change(
            request.app.state, factory_id, user_id, payload
        )

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/definition/restore", response_model=None, dependencies=HUMAN_ORIGIN
)
async def restore_definition(
    request: Request, factory_id: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Owners: store version K's document as the next version (history stays linear)."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        return await definition_changes.restore_version(
            request.app.state, factory_id, user_id, payload
        )

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/definition/pin", response_model=None, dependencies=HUMAN_ORIGIN
)
async def pin_definition(
    request: Request, factory_id: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Put a stored version back in force (members get the diff)."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        body = parse_body(PinBody, payload)
        return await tuning.pin_version(request.app.state, factory_id, user_id, body.version)

    return await _guard(run)


async def _keep_or_revert(
    request: Request, factory_id: str, ref: str, payload: Any, action: str
) -> dict[str, Any] | JSONResponse:
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        body = parse_body(NoteBody, payload if payload is not None else {})
        row = await tuning.human_keep_or_revert(
            request.app.state, factory_id, ref, user_id, action, body.note
        )
        return {"proposal": await asyncio.to_thread(proposal_view, deps, row)}

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/proposals/{ref}/keep",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def keep_proposal(
    request: Request, factory_id: str, ref: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """End a trial now and keep the change (owners)."""
    return await _keep_or_revert(request, factory_id, ref, payload, "keep")


@router.post(
    "/factories/{factory_id}/proposals/{ref}/revert",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def revert_proposal(
    request: Request, factory_id: str, ref: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """One-click revert: the parent version goes back in force (owners)."""
    return await _keep_or_revert(request, factory_id, ref, payload, "revert")


@router.post("/factories/{factory_id}/retro", response_model=None, dependencies=HUMAN_ORIGIN)
async def run_retro(request: Request, factory_id: str) -> dict[str, Any] | JSONResponse:
    """Send Sentinel a retro beat now (owners; at most one per hour)."""

    async def run() -> dict[str, Any]:
        _deps, user_id, root = await _root(request, factory_id, LEVEL_MANAGE)
        import time

        subject = f"retro:manual:{int(time.time()) // 3600}"
        sent = await retro.request_retro(
            request.app.state, root.id, subject, by=user_id or "local"
        )
        return {"sent": sent, "subject": subject}

    return await _guard(run)


# ── Agents ─────────────────────────────────────────────────────


@router.get("/agent/rules", response_model=None)
async def get_agent_rules(
    request: Request, from_session_id: str = Query(...)
) -> dict[str, Any] | JSONResponse:
    """The definition's rules for the caller's role and project (``sys_factory_rules``)."""

    async def run() -> dict[str, Any]:
        caller = await authorize_agent(request, from_session_id)
        role = caller.membership.role
        project = caller.membership.project
        # The Codex builder reports role ic; rules for the ic_codex agent are its too.
        agent = caller.session.sub_agent_name

        def _read() -> dict[str, Any]:
            resolved = definition.definition_for(caller.deps, caller.factory_id)
            if resolved is None:
                return {
                    "def_version": None,
                    "role": role,
                    "project": project,
                    "rules": [],
                    "environment": None,
                }
            rules = definition.rules_for(resolved.doc, role, project)
            if agent == "ic_codex" and role != agent:
                extra = definition.rules_for(resolved.doc, agent, project)
                seen = {r["id"] for r in rules}
                rules = sorted(
                    [*rules, *(r for r in extra if r["id"] not in seen)],
                    key=lambda r: int(str(r.get("id", "R-0"))[2:] or 0),
                )
            delivery = resolved.get("product.delivery") or {}
            env = environment.for_caller(caller.deps, caller.factory_id, resolved.doc, project)
            return {
                "def_version": resolved.version,
                "role": role,
                "project": project,
                "rules": [
                    {k: r.get(k) for k in ("id", "role", "project", "text", "state", "origin")}
                    for r in rules
                ],
                # The product profile's delivery lists in full (charters show the first few).
                "product_delivery": {
                    key: delivery.get(key)
                    for key in ("merge_policy", "protected_repos", "demo_paths")
                },
                # How this product's env runs (env_keeper and the demo agent read it here);
                # null when the profile sets none for the caller's project repo.
                "environment": env,
                "note": "Advisory rules: follow them; they never widen a policy or a gate.",
            }

        return await asyncio.to_thread(_read)

    return await _guard(run)


__all__ = ["router"]
