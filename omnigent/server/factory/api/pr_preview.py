"""``GET /v1/factory/prs/{pr_id}/preview``: the stable link to a PR's factory preview.

Preview hosts change when a PL pod is replaced, so PR bodies and the cockpit
link here instead. It redirects to the preview's current app URL while the
preview is ``ready`` or ``stale``, and otherwise to the PR in the cockpit with
``preview=unavailable``. Only an https origin on the factory env domain is
ever a redirect target (no open redirect through an agent-reported URL).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Final
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from omnigent.entities.factory import FactoryPr
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.env_urls import safe_env_origin
from omnigent.server.routes._auth_helpers import require_access, require_user
from omnigent.stores.factory_store import norm_id

LIVE_PREVIEW_STATES: Final = frozenset({"ready", "stale"})

router = APIRouter(prefix="/v1/factory", tags=["factory"])


def preview_stable_path(pr_id: str) -> str:
    """:returns: The stable preview link of a PR row, e.g. ``/v1/factory/prs/<id>/preview``."""
    return f"/v1/factory/prs/{pr_id}/preview"


def preview_redirect_target(pr: FactoryPr) -> str:
    """
    Where the stable preview link of *pr* points now.

    :returns: The preview app URL (``ready``/``stale``, on the env domain), or
        ``/factory/<factory>?review=<n>&pr=<pr_id>&preview=unavailable``.
    """
    preview = pr.preview if isinstance(pr.preview, Mapping) else {}
    urls = preview.get("urls") if isinstance(preview.get("urls"), Mapping) else {}
    origin = safe_env_origin(urls.get("app"))
    if preview.get("state") in LIVE_PREVIEW_STATES and origin:
        return f"{origin}/"
    query = urlencode({"review": pr.number, "pr": pr.id, "preview": "unavailable"})
    return f"/factory/{pr.factory_id}?{query}"


@router.get("/prs/{pr_id}/preview")
async def open_pr_preview(request: Request, pr_id: str) -> RedirectResponse:
    """Redirect to the PR's live factory preview (READ on the factory)."""
    deps = factory_deps(request.app.state)
    user_id = require_user(request, deps.auth_provider)
    try:
        key = norm_id(pr_id)
    except ValueError:
        key = None
    pr = await asyncio.to_thread(deps.factory_store.get_pr, key) if key else None
    if pr is None:
        raise OmnigentError(f"PR {pr_id!r} not found", code=ErrorCode.NOT_FOUND)
    await require_access(
        user_id, pr.factory_id, LEVEL_READ, deps.permission_store, deps.conversation_store
    )
    return RedirectResponse(
        preview_redirect_target(pr), status_code=302, headers={"Cache-Control": "no-store"}
    )


__all__ = ["preview_redirect_target", "preview_stable_path", "router"]
