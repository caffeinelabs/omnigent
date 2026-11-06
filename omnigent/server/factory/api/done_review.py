"""Done reviews: the reviewer's verdict and a person's "is this done?".

- ``POST /v1/factory/agent/reviews`` (runner, ``sys_factory_review``): the
  independent reviewer's verdict on a feature, ``{initiative, review?, verdict,
  summary, gaps[], accepted[]}``. Leads only, never the factory root (it plans
  the work) nor a lead that planned a wave of it (403 ``not_independent``);
  409 ``review_closed`` when no review waits for a verdict.
- ``POST /v1/factory/factories/{fid}/n/{ref}/review`` (people, EDIT, human
  origin): "is this done?" on a feature, preview-free since it only reads:
  a review now, or the one in progress. Returns ``{review, line, message,
  result: NodePage}``.

See :mod:`omnigent.server.factory.done_review`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import control, done_review, done_review_verbs, node_verbs, your_move
from omnigent.server.factory.agent_scope import authorize_agent
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.done_review_view import review_state
from omnigent.server.factory.initiative_view import plan_of
from omnigent.server.factory.origin import HUMAN_ORIGIN

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_T = TypeVar("_T")


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


@router.post("/agent/reviews", response_model=None)
async def post_review(
    request: Request, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """``sys_factory_review``: the independent reviewer's verdict."""

    async def run() -> dict[str, Any]:
        body = dict(payload) if isinstance(payload, dict) else {}
        sender = str(body.pop("from_session_id", "") or "")
        caller = await authorize_agent(request, sender)
        return await done_review.submit(request.app.state, caller, body)

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/n/{ref}/review", response_model=None, dependencies=HUMAN_ORIGIN
)
async def ask_review(request: Request, factory_id: str, ref: str) -> dict[str, Any] | JSONResponse:
    """A person asks "is this done?" on a feature: a done review now (it only reads)."""

    async def run() -> dict[str, Any]:
        env = await node_verbs.env_for(request, factory_id)
        await control.require(env.deps, env.user_id, env.fid, LEVEL_EDIT)
        tree = await env.tree(fresh=True)
        node = tree.lookup(ref)
        row = (
            next((r for r in tree.facts.initiatives if r.ref == node.ref), None)
            if node is not None and node.level == "feature"
            else None
        )
        if row is None:
            raise CockpitError(
                422, "not_here", "ask whether a feature is done on the feature itself"
            )
        review = await done_review_verbs.ask_now(env, row)
        fresh = await env.tree(fresh=True)
        page = fresh.page(row.ref, can_edit=True, viewer=your_move.Viewer(env.user_id, env.level))
        stored = next((r for r in fresh.facts.initiatives if r.ref == row.ref), row)
        line = review_state(plan_of(stored)).line
        who = review.get("by_title") or review.get("requested_of_title") or "the server"
        message = (
            f"{who} reviews {row.title} now: is it truly done?"
            if review.get("status") == "requested"
            else f"Review {review['n']}: {line}"
        )
        return {
            "review": {
                "n": review["n"],
                "status": review.get("status"),
                "reviewer": review.get("requested_of_title"),
                "verdict": review.get("verdict"),
            },
            "line": line,
            "message": message,
            "result": page,
        }

    return await _guard(run)
