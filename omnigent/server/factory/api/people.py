"""The factory People API: the roster, invites, removals, search and access requests.

Routes (user JWT; writes carry :data:`~omnigent.server.factory.origin.HUMAN_ORIGIN`):

- ``GET /v1/factory/factories/{fid}/people``: the roster plus ``runs_as`` (whose
  GitHub and Databricks identity the agents use). READ on the root.
- ``PUT /v1/factory/factories/{fid}/people/{user}`` ``{role}``: invite or change a
  role; answers ``admission: ok|blocked`` (could they sign in). MANAGE.
- ``DELETE /v1/factory/factories/{fid}/people/{user}``: remove someone (MANAGE)
  or leave (yourself, READ).
- ``GET /v1/factory/factories/{fid}/people/search?q=``: known users to invite
  (≥ 3 characters, ≤ 10 results). MANAGE.
- ``POST /v1/factory/factories/{fid}/access-requests`` ``{role?, note?}``: a
  viewer (or operator) asks the owners for more; opens an ``access_request``
  decision whose ``grant_role`` option the server runs. READ.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any, Final, TypeVar

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from omnigent.entities import Conversation
from omnigent.errors import OmnigentError
from omnigent.factory import ACTION_DISMISS, ACTION_GRANT_ROLE, PERSON_ROLES
from omnigent.server.auth import LEVEL_MANAGE, LEVEL_READ
from omnigent.server.factory import control, events, people, schemas, server_decisions
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.decisions import decision_view
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.oidc_access import admission_status
from omnigent.server.routes._auth_helpers import get_user_id
from omnigent.server.routes._sharing import require_sharing_allows

router = APIRouter(prefix="/v1/factory", tags=["factory"])

SEARCH_MIN_CHARS: Final = 3
SEARCH_MAX_RESULTS: Final = 10
ACCESS_NOTE_MAX: Final = 500
_USER_RE: Final = re.compile(r"^[^\s/\\]{1,128}$")
_T = TypeVar("_T")


class PutPersonBody(BaseModel):
    """``PUT /v1/factory/factories/{fid}/people/{user}``."""

    model_config = ConfigDict(extra="forbid")

    role: str


class AccessRequestBody(BaseModel):
    """``POST /v1/factory/factories/{fid}/access-requests``."""

    model_config = ConfigDict(extra="forbid")

    role: str = people.ROLE_OPERATOR
    note: str | None = None


def _caller(request: Request) -> tuple[FactoryDeps, str | None]:
    deps = factory_deps(request.app.state)
    return deps, get_user_id(request, deps.auth_provider)


async def _run(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


def _person_json(person: Any) -> dict[str, Any]:
    return schemas.PersonView.model_validate(people.person_view(person)).model_dump(mode="json")


async def _manage_root(deps: FactoryDeps, user_id: str | None, factory_id: str) -> Conversation:
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_MANAGE)
    return root


def _check_target(deps: FactoryDeps, root: Conversation, target: str) -> str:
    """Refuse reserved users, malformed ids and the creator. :returns: The target id."""
    target = target.strip()
    if target in people.RESERVED_USERS:
        raise CockpitError(400, "reserved_user", f"{target!r} cannot join a factory")
    if not _USER_RE.fullmatch(target):
        raise CockpitError(400, "invalid_user", "user must be a user id such as an email")
    if deps.permission_store is not None and people.creator_of(deps, root.id) == target:
        raise CockpitError(
            409, "creator", "the factory's creator stays its owner; agents run as them"
        )
    return target


@router.get("/factories/{factory_id}/people", response_model=None)
async def list_people(request: Request, factory_id: str) -> dict[str, Any] | JSONResponse:
    """The roster, who agents run as, and the caller's own levers."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        rows = await asyncio.to_thread(people.list_people, deps, root.id)
        creator = next((p.user_id for p in rows if p.is_creator), None)
        viewer = await asyncio.to_thread(people.viewer_block, deps, root.id, user_id)
        return {
            "data": [_person_json(p) for p in rows],
            "runs_as": {"user_id": creator, "name": creator} if creator else None,
            "count": len(rows),
            "viewer": schemas.ViewerBlock.model_validate(viewer).model_dump(mode="json"),
        }

    return await _run(run)


@router.get("/factories/{factory_id}/people/search", response_model=None)
async def search_people(
    request: Request,
    factory_id: str,
    q: str = Query("", description="At least 3 characters of a user id or email."),
) -> dict[str, Any] | JSONResponse:
    """Known users matching *q*, for the invite field (owners only)."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        root = await _manage_root(deps, user_id, factory_id)
        query = q.strip()
        if len(query) < SEARCH_MIN_CHARS:
            raise CockpitError(
                400, "query_too_short", f"type at least {SEARCH_MIN_CHARS} characters"
            )
        perms = deps.permission_store
        users = await asyncio.to_thread(perms.list_users) if perms is not None else []
        wanted = query.casefold()
        ids = sorted(
            u.id for u in users if wanted in u.id.casefold() and u.id not in people.RESERVED_USERS
        )[:SEARCH_MAX_RESULTS]
        roster = {p.user_id: p for p in await asyncio.to_thread(people.list_people, deps, root.id)}
        data = [
            {
                "user_id": uid,
                "name": uid,
                "known": True,
                "role": people.person_role(roster[uid]) if uid in roster else None,
                "admission": admission_status(uid),
            }
            for uid in ids
        ]
        if "@" in query and _USER_RE.fullmatch(query) and query not in ids:
            data.append(
                {
                    "user_id": query,
                    "name": query,
                    "known": False,
                    "role": people.person_role(roster[query]) if query in roster else None,
                    "admission": admission_status(query),
                }
            )
        return {"data": data[:SEARCH_MAX_RESULTS]}

    return await _run(run)


@router.put(
    "/factories/{factory_id}/people/{target}", response_model=None, dependencies=HUMAN_ORIGIN
)
async def put_person(
    request: Request, factory_id: str, target: str, body: PutPersonBody
) -> dict[str, Any] | JSONResponse:
    """Invite *target* as *role*, or change their role; grants follow on every member."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        root = await _manage_root(deps, user_id, factory_id)
        if body.role not in people.ROLE_LEVELS:
            raise CockpitError(
                400, "invalid_role", f"role must be one of {list(PERSON_ROLES)}", field="role"
            )
        person = _check_target(deps, root, target)
        if person == user_id:
            raise CockpitError(403, "cannot_change_self", "ask another owner to change your role")
        level = people.ROLE_LEVELS[body.role]
        sessions = await asyncio.to_thread(people.managed_sessions, deps, root.id)
        for session_id in sessions:
            try:
                await require_sharing_allows(
                    request.app.state, deps.conversation_store, session_id, level
                )
            except OmnigentError as exc:
                raise CockpitError(403, "sharing_refused", exc.message) from None
        change = await people.set_person(
            request.app.state, root.id, person, body.role, by_user=user_id
        )
        assert change.person is not None
        return {
            "person": _person_json(change.person),
            "created": change.created,
            "previous_role": change.previous_role,
            "granted": len(change.granted),
            "admission": admission_status(person),
        }

    return await _run(run)


@router.delete(
    "/factories/{factory_id}/people/{target}", response_model=None, dependencies=HUMAN_ORIGIN
)
async def delete_person(
    request: Request, factory_id: str, target: str
) -> dict[str, Any] | JSONResponse:
    """Remove *target* from the roster (owners), or leave (yourself)."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        person = target.strip()
        leaving = person == user_id
        await control.require(deps, user_id, root.id, LEVEL_READ if leaving else LEVEL_MANAGE)
        _check_target(deps, root, person)
        row = await asyncio.to_thread(deps.factory_store.get_person, root.id, person)
        if row is None:
            raise CockpitError(404, "not_on_roster", f"{person} is not on this factory's roster")
        change = await people.remove_person(request.app.state, root.id, person, by_user=user_id)
        return {
            "removed": person,
            "left": leaving,
            "previous_role": change.previous_role,
            "revoked": len(change.revoked),
        }

    return await _run(run)


@router.post(
    "/factories/{factory_id}/access-requests",
    status_code=201,
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def request_access(
    request: Request, factory_id: str, body: AccessRequestBody | None = None
) -> dict[str, Any] | JSONResponse:
    """Ask the owners for a role; they answer an ``access_request`` decision."""
    deps, user_id = _caller(request)
    body = body or AccessRequestBody()

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        if user_id is None or user_id in people.RESERVED_USERS:
            raise CockpitError(400, "no_user", "sign in to request access")
        if body.role not in (people.ROLE_OPERATOR, people.ROLE_OWNER):
            raise CockpitError(400, "invalid_role", "request 'operator' or 'owner'", field="role")
        level = await asyncio.to_thread(people.viewer_level, deps, root.id, user_id)
        if level >= people.ROLE_LEVELS[body.role]:
            raise CockpitError(409, "already_has_role", f"you already have {body.role} access")
        note = (body.note or "").strip()
        if len(note) > ACCESS_NOTE_MAX:
            raise CockpitError(400, "note_too_long", f"note exceeds {ACCESS_NOTE_MAX} chars")
        opened = await server_decisions.open_decision(
            request.app.state,
            root.id,
            source="access_request",
            dedupe_key=f"access:{user_id}"[:64],
            question=f"{user_id} asks for {body.role} access",
            context=note or None,
            options=[
                {
                    "id": "grant",
                    "label": f"Grant {body.role}",
                    "action": ACTION_GRANT_ROLE,
                    "target": f"person:{user_id}",
                    "role": body.role,
                    "recommended": True,
                },
                {"id": "decline", "label": "Decline", "action": ACTION_DISMISS},
            ],
            blocking=False,
            project=None,
            asked_by=None,
        )
        if opened is None:
            raise CockpitError(503, "not_stored", "the request could not be stored; retry")
        row, deduped = opened
        if not deduped:
            await events.emit(
                request.app.state,
                root.id,
                "access.requested",
                severity="notice",
                subject=f"person:{user_id}",
                summary=f"{user_id} asks for {body.role} access ({row.ref})",
                data={"user": user_id, "role": body.role, "decision_id": row.id, "by": user_id},
            )
        view = await asyncio.to_thread(decision_view, deps, row)
        return {"decision": view, "deduped": deduped}

    return await _run(run)


__all__ = ["router"]
