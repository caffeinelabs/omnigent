"""``POST /v1/factory/feedback``: ratings, thumbs, closure reasons and calibration from people.

Anyone who reads the factory may give feedback (viewers too). The body names
the factory, the target (``pr``, ``demo``, ``decision``, ``issue``, …), the
kind (``rating`` 1-5, ``useful`` ±1, ``unneeded``, ``closure``,
``calibration`` ±1 with a corrected category, ``root_cause``) and optional
tags from the seeded taxonomies.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from omnigent.server.factory import feedback
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])


@router.post("/feedback", response_model=None, status_code=201, dependencies=HUMAN_ORIGIN)
async def post_feedback(
    request: Request, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Record one piece of feedback; ``feedback.added`` refreshes Insights."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    try:
        return await feedback.post(request.app.state, user_id, payload)
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


__all__ = ["router"]
