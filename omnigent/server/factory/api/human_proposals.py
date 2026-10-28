"""``POST /v1/factory/factories/{fid}/proposals``: a person files a projects proposal.

EDIT on the root and human origin. The proposal opens pending, mirrored as a
decision in its thread (``#factory`` by default), and is applied from its card.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.human_proposals import propose_human
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])


@router.post(
    "/factories/{factory_id}/proposals",
    status_code=201,
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def post_human_proposal(
    request: Request, factory_id: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """File a projects proposal (e.g. a dependency edited on the Graph)."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    try:
        return await propose_human(request.app.state, factory_id, user_id, payload)
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


__all__ = ["router"]
