"""Pre-sandbox model options — what a harness could launch a Databricks sandbox
with, before the sandbox (and thus its host) exists.

The New-chat composer picks a model before a managed host exists, so it cannot
probe a host the way :mod:`omnigent.server.routes.hosts` does for a connected
one. This browser-facing route answers from the caller's brokered Databricks
credential instead: it mints the user's workspace token and enumerates the
serving endpoints into the selected harness's picker vocabulary
(:mod:`omnigent.sandbox_model_options`).

Returns ``200 {"connected": false, "models": []}`` (never an error) when the
caller has not linked Databricks or discovery fails, so the composer falls back
to its static vocabulary cleanly — mirroring the host-facing broker's
``{"connected": false}`` contract.
"""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import APIRouter, Request, Response

from omnigent.sandbox_model_options import resolve_sandbox_model_options
from omnigent.server.auth import RESERVED_USER_LOCAL, AuthProvider
from omnigent.server.databricks_identity import resolve_databricks_token
from omnigent.server.routes._auth_helpers import require_user

_logger = logging.getLogger(__name__)

# The deployment's pinned gateway endpoint (marked isDefault when it appears in
# the list). Mirrors the runner's OMNIGENT_DATABRICKS_GATEWAY_MODEL.
_DEFAULT_MODEL_ENV = "OMNIGENT_DATABRICKS_GATEWAY_MODEL"


def create_sandbox_model_options_router(auth_provider: AuthProvider | None) -> APIRouter:
    """Build the browser-facing pre-sandbox model-options router.

    :param auth_provider: Identity resolution, or ``None`` (single-user/local).
    :returns: A router exposing ``GET /sandbox/model-options``.
    """
    router = APIRouter()

    @router.get("/sandbox/model-options")
    async def sandbox_model_options(
        request: Request,
        response: Response,
        harness: str,
    ) -> dict[str, object]:
        """Model-picker rows for *harness* on this user's Databricks workspace.

        Authenticated as the browser session (like the connection status route).
        ``{"connected": false}`` when the user has not linked Databricks, so the
        composer degrades to its static vocabulary.
        """
        # A vended catalog is per-user and token-derived; never let it be cached.
        response.headers["Cache-Control"] = "no-store"
        user_id = require_user(request, auth_provider)
        if user_id is None:
            user_id = RESERVED_USER_LOCAL
        store = getattr(request.app.state, "databricks_store", None)
        client = getattr(request.app.state, "databricks_client", None)
        if store is None or client is None:
            return {"connected": False, "models": []}
        try:
            resolved = await resolve_databricks_token(user_id, store=store, client=client)
        except Exception:  # noqa: BLE001 - a broker fault must degrade, not 500
            _logger.warning("databricks token resolve failed for sandbox models", exc_info=True)
            return {"connected": False, "models": []}
        if resolved is None:
            return {"connected": False, "models": []}
        token, workspace_host = resolved
        default_model = os.environ.get(_DEFAULT_MODEL_ENV) or None
        models = await asyncio.to_thread(
            resolve_sandbox_model_options,
            harness,
            workspace_host,
            token,
            default_model=default_model,
        )
        return {"connected": True, "models": models}

    return router
