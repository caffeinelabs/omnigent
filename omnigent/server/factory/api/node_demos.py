"""
A node's recorded demos (calm rule 10, "Proof is never hidden").

``GET /v1/factory/factories/{fid}/n/{ref}/demos`` lists the demos of the
node's subtree, newest first, ``limit`` a page (``cursor``: a page's
``next``), each with its verdict, short title, length, poster and video (the
demo file routes) and its PR (:mod:`omnigent.server.factory.node_proof`). The
node page carries the one it shows by default (``proof.latest``) and the
count; this is what "n more demos" opens. A viewer reads what they may read of
the PRs: demos of a project whose lead they cannot read are left out, and so
are demos whose files belong to a session they cannot read. 404
``unknown_node`` for a ref the factory has no node for; 400 ``bad_cursor``
for a cursor no page handed out.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from omnigent.server.factory import node_proof, node_views, nodes
from omnigent.server.factory.api.agents import fleet_context

router = APIRouter(prefix="/v1/factory", tags=["factory"])


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@router.get("/factories/{factory_id}/n/{ref}/demos", response_model=None)
async def get_node_demos(
    request: Request,
    factory_id: str,
    ref: str,
    cursor: str | None = None,
    limit: int = Query(node_proof.PAGE_SIZE, ge=1, le=node_proof.PAGE_MAX),
) -> dict[str, Any] | JSONResponse:
    """The node's subtree's recorded demos, newest first."""
    ctx = await fleet_context(request, factory_id)
    tree = await nodes.tree_for(
        ctx.deps, ctx.root, ctx.raw, ctx.readable, app_state=request.app.state
    )
    found = tree.lookup(ref)
    if found is None:
        return _error(404, "unknown_node", f"no node {ref!r} in this factory")
    try:
        view = node_proof.demos_page(tree, tree.fold(found), cursor=cursor, limit=limit)
    except ValueError as exc:
        return _error(400, "bad_cursor", str(exc))
    return node_views.validate(node_views.NodeDemosView, view)
