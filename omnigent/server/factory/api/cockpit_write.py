"""The cockpit's human write API: every lever is a deterministic server action.

Routes (user JWT; access per route, see each module):

- ``POST /v1/factory/decisions/{id}/answer`` / ``…/withdraw`` (:mod:`..decisions`)
- ``POST /v1/factory/factories/{fid}/control`` (:mod:`..control`)
- ``POST /v1/factory/prs/{id}/refresh`` / ``…/feedback`` (:mod:`..control`)
- ``POST /v1/factory/factories``, ``POST …/{fid}/projects``,
  ``PATCH …/{fid}/projects/{slug}`` (:mod:`..start`)

Refusals render as ``{"error": {"code", "message", "field"?}, ...}``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from omnigent.entities.factory import FactoryEvent
from omnigent.server.factory import control, decisions, start
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.envelope import display_title
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_T = TypeVar("_T")


class AnswerBody(BaseModel):
    """``POST /v1/factory/decisions/{id}/answer``."""

    model_config = ConfigDict(extra="forbid")

    option_id: str | None = None
    text: str | None = None


class WithdrawBody(BaseModel):
    """``POST /v1/factory/decisions/{id}/withdraw``."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class ControlBody(BaseModel):
    """``POST /v1/factory/factories/{fid}/control``."""

    model_config = ConfigDict(extra="forbid")

    action: str
    target: str = control.TARGET_FACTORY
    note: str | None = None
    options: dict[str, Any] | None = None


class FeedbackBody(BaseModel):
    """``POST /v1/factory/prs/{id}/feedback``."""

    model_config = ConfigDict(extra="forbid")

    verdict: str
    text: str | None = None


def _caller(request: Request) -> tuple[FactoryDeps, str | None]:
    deps = factory_deps(request.app.state)
    return deps, get_user_id(request, deps.auth_provider)


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        headers = (
            {"Retry-After": str(exc.extra["retry_after"])} if "retry_after" in exc.extra else None
        )
        return JSONResponse(status_code=exc.status_code, content=exc.body(), headers=headers)


def _event_json(event: FactoryEvent | None) -> list[dict[str, Any]]:
    if event is None:
        return []
    row = event.to_dict()
    row.pop("workspace_id", None)
    return [row]


@router.post("/decisions/{decision_id}/answer", response_model=None)
async def answer_decision(
    request: Request, decision_id: str, body: AnswerBody
) -> dict[str, Any] | JSONResponse:
    """Answer an open decision; 409 ``{answered_by, answered_at}`` when someone else did."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        result = await decisions.answer_decision(
            request.app.state,
            decision_id,
            user_id=user_id,
            option_id=body.option_id,
            text=body.text,
        )
        to = result.delivered_to
        view = await asyncio.to_thread(
            decisions.decision_view,
            deps,
            result.decision,
            delivered_to_title=display_title(to.title, to.id) if to is not None else None,
        )
        return {"decision": view}

    return await _guard(run)


@router.post("/decisions/{decision_id}/withdraw", response_model=None)
async def withdraw_decision(
    request: Request, decision_id: str, body: WithdrawBody | None = None
) -> dict[str, Any] | JSONResponse:
    """Withdraw an open decision (the human dismisses it)."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        result = await decisions.withdraw_decision(
            request.app.state,
            decision_id,
            user_id=user_id,
            reason=body.reason if body is not None else None,
        )
        view = await asyncio.to_thread(decisions.decision_view, deps, result.decision)
        return {"decision": view}

    return await _guard(run)


@router.post("/factories/{factory_id}/control", status_code=202, response_model=None)
async def post_control(
    request: Request, factory_id: str, body: ControlBody
) -> dict[str, Any] | JSONResponse:
    """Run one control (pause, resume, stop, restart, relaunch, cadence, preview, env_status)."""
    _, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        result = await control.apply_control(
            request.app.state,
            factory_id,
            user_id=user_id,
            action=body.action,
            target=body.target,
            note=body.note,
            options=body.options,
        )
        return {"events": _event_json(result.event), "mail_ids": result.mail_ids}

    return await _guard(run)


@router.post("/prs/{pr_id}/refresh", status_code=202, response_model=None)
async def refresh_pr(request: Request, pr_id: str) -> dict[str, Any] | JSONResponse:
    """Ask the GitHub poller to refresh one PR now (1 per PR per 10 s)."""
    _, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        pr = await control.request_pr_refresh(request.app.state, pr_id, user_id=user_id)
        return {"pr_id": pr.id, "status": "queued"}

    return await _guard(run)


@router.post("/prs/{pr_id}/feedback", response_model=None)
async def pr_feedback(
    request: Request, pr_id: str, body: FeedbackBody
) -> dict[str, Any] | JSONResponse:
    """Approve or request changes from the cockpit; the PL is told, GitHub is not."""
    _, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        pr, mail_id = await control.submit_pr_feedback(
            request.app.state, pr_id, user_id=user_id, verdict=body.verdict, text=body.text
        )
        return {"pr_id": pr.id, "human_review": pr.human_review, "mail_id": mail_id}

    return await _guard(run)


@router.post("/factories", response_model=None)
async def start_factory(request: Request, payload: Annotated[Any, Body()]) -> JSONResponse:
    """Start a factory on an existing root lead session (201, or 200 when already started)."""
    _, user_id = _caller(request)

    async def run() -> JSONResponse:
        status, content = await start.start_factory(request.app.state, payload, user_id=user_id)
        return JSONResponse(status_code=status, content=content)

    return await _guard(run)


@router.post("/factories/{factory_id}/projects", status_code=201, response_model=None)
async def add_project(
    request: Request, factory_id: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Add a project; the root lead is asked to spawn its PL."""
    _, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        row = await start.add_project(request.app.state, factory_id, payload, user_id=user_id)
        return {"project": row.to_dict()}

    return await _guard(run)


@router.patch("/factories/{factory_id}/projects/{slug}", response_model=None)
async def update_charter(
    request: Request, factory_id: str, slug: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Edit a project's charter; its PL gets a CHARTER UPDATE with the diff."""
    _, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        row, changed, mail_id = await start.update_charter(
            request.app.state, factory_id, slug, payload, user_id=user_id
        )
        return {"project": row.to_dict(), "changed": changed, "mail_id": mail_id}

    return await _guard(run)


__all__ = ["router"]
