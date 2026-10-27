"""Human origin: the levers in the cockpit are pulled by people, never by agents.

Agents act with their creator's identity, so an identity check alone cannot
tell a person from the agent working for them. :func:`require_human_origin`
is the FastAPI dependency every human write route carries (Tier 0). It
refuses with 403 ``human_only`` when the request

- carries the runner tunnel token header (a sandbox runner's callback);
- authenticates with a runner bearer (the ``org: runner`` claim that
  :meth:`~omnigent.server.auth.UnifiedAuthProvider.mint_runner_token` stamps);
- has an ``Origin`` that :func:`~omnigent.server.routes._origin.require_trusted_origin`
  would reject (a cross-site browser request).

A person's browser cookie and their own CLI bearer pass. Agent routes live
under ``/v1/factory/agent/*`` and never use this dependency.
"""

from __future__ import annotations

from fastapi import Depends, Request

from omnigent.server.auth import local_single_user_enabled, runner_origin
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.routes import FactoryRouteError
from omnigent.server.ws_origin import origin_allowed, parse_allowed_origins

HUMAN_ONLY = "human_only"


def runner_origin_reason(request: Request) -> str | None:
    """
    Say why *request* does not come from a person, if it does not.

    :param request: The incoming request.
    :returns: e.g. ``"a runner token"``, or ``None`` for a human request.
    """
    reason = runner_origin(request, factory_deps(request.app.state).auth_provider)
    if reason is not None:
        return reason
    if not origin_allowed(
        request.headers.get("origin"),
        local_mode=local_single_user_enabled(),
        extra_allowed=parse_allowed_origins(),
    ):
        return "an untrusted Origin"
    return None


async def require_human_origin(request: Request) -> None:
    """
    FastAPI dependency: refuse agent-originated calls to a human route.

    :param request: The incoming request.
    :raises FactoryRouteError: 403 ``human_only``.
    """
    reason = runner_origin_reason(request)
    if reason is not None:
        raise FactoryRouteError(
            403,
            HUMAN_ONLY,
            f"this action is for people only; the request carries {reason}. Agents use "
            "the /v1/factory/agent/* routes (sys_factory_* tools) or ask a human with "
            "sys_factory_ask.",
        )


# ``dependencies=HUMAN_ORIGIN`` on every human write route.
HUMAN_ORIGIN = [Depends(require_human_origin)]

__all__ = ["HUMAN_ONLY", "HUMAN_ORIGIN", "require_human_origin", "runner_origin_reason"]
