"""Tier 2 (the Factory Smith lane: code and bundle PRs to omnigent) gate status. Ships off.

Tier 2 turns on only when all three prerequisites hold, checked at server start:

1. ``OMNIGENT_FACTORY_SMITH=1``;
2. ``caffeinelabs/omnigent`` ``staging`` is protected: the rulesets that apply
   to it require pull-request review with CODEOWNERS review, and status checks
   (read through the GitHub API);
3. a dedicated factory GitHub identity is configured
   (``OMNIGENT_FACTORY_SMITH_GITHUB_LOGIN`` with its token in
   ``OMNIGENT_FACTORY_SMITH_GITHUB_TOKEN``) and has neither admin nor maintain
   rights on the repo.

While it is off, ``kind=code`` proposals are recorded and never run.
``GET /v1/factory/preflight`` returns ``tier2 {enabled, reason}``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, Final

import httpx

_logger = logging.getLogger(__name__)

SMITH_ENV: Final = "OMNIGENT_FACTORY_SMITH"
LOGIN_ENV: Final = "OMNIGENT_FACTORY_SMITH_GITHUB_LOGIN"
TOKEN_ENV: Final = "OMNIGENT_FACTORY_SMITH_GITHUB_TOKEN"
REPO_ENV: Final = "OMNIGENT_FACTORY_SMITH_REPO"
DEFAULT_REPO: Final = "caffeinelabs/omnigent#staging"
GITHUB_API: Final = "https://api.github.com"
TIMEOUT_S: Final = 10.0
STATE_ATTR: Final = "factory_tier2"
_BYPASS_ROLES: Final = frozenset({"admin", "maintain"})

Transport = httpx.AsyncBaseTransport | None


def _repo_branch() -> tuple[str, str]:
    raw = os.environ.get(REPO_ENV, "").strip() or DEFAULT_REPO
    repo, _, branch = raw.partition("#")
    return repo.strip().lower(), (branch.strip() or "staging")


def _local_reason() -> str | None:
    """The prerequisite missing in this process's config, if any (no network)."""
    if os.environ.get(SMITH_ENV, "").strip() != "1":
        return f"{SMITH_ENV} is not 1"
    if not os.environ.get(LOGIN_ENV, "").strip():
        return f"no factory GitHub identity configured ({LOGIN_ENV})"
    if not os.environ.get(TOKEN_ENV, "").strip():
        return f"the factory identity has no token ({TOKEN_ENV})"
    return None


async def probe(transport: Transport = None) -> dict[str, Any]:
    """
    Check the prerequisites (the GitHub ones only when the local ones hold).

    :returns: ``{enabled, reason, checks: {smith, identity, protected, identity_role}}``.
    """
    repo, branch = _repo_branch()
    checks: dict[str, Any] = {
        "smith": os.environ.get(SMITH_ENV, "").strip() == "1",
        "identity": os.environ.get(LOGIN_ENV, "").strip() or None,
        "repo": f"{repo}#{branch}",
        "protected": None,
        "identity_role": None,
    }
    reason = _local_reason()
    if reason is not None:
        return {"enabled": False, "reason": reason, "checks": checks}
    token = os.environ.get(TOKEN_ENV, "").strip()
    login = os.environ.get(LOGIN_ENV, "").strip()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        async with httpx.AsyncClient(
            base_url=GITHUB_API, headers=headers, timeout=TIMEOUT_S, transport=transport
        ) as client:
            rules = await client.get(f"/repos/{repo}/rules/branches/{branch}")
            role = await client.get(f"/repos/{repo}/collaborators/{login}/permission")
    except httpx.HTTPError as exc:
        return {
            "enabled": False,
            "reason": f"GitHub unreachable ({type(exc).__name__})",
            "checks": checks,
        }
    missing = _protection_gap(_json(rules, []))
    checks["protected"] = missing is None
    body = _json(role, {})
    checks["identity_role"] = (
        (body.get("role_name") or body.get("permission")) if isinstance(body, dict) else None
    )
    if rules.status_code != 200:
        reason = f"could not read the rulesets of {repo}#{branch} (HTTP {rules.status_code})"
    elif missing is not None:
        reason = f"{repo}#{branch} is not protected: its rulesets do not require {missing}"
    elif role.status_code != 200:
        reason = f"could not read {login}'s role on {repo} (HTTP {role.status_code})"
    elif checks["identity_role"] in _BYPASS_ROLES:
        reason = f"the factory identity {login} has {checks['identity_role']} rights"
    return {"enabled": reason is None, "reason": reason, "checks": checks}


def _json(resp: httpx.Response, default: Any) -> Any:
    """:returns: A 200 response's JSON, else *default* (also for a body that is not JSON)."""
    if resp.status_code != 200:
        return default
    try:
        return resp.json()
    except ValueError:
        return default


def _protection_gap(found: Any) -> str | None:
    """:returns: What the branch rules lack (review, CODEOWNERS review, CI), or ``None``."""
    rules = [r for r in found if isinstance(r, dict)] if isinstance(found, list) else []
    reviews = [r for r in rules if r.get("type") == "pull_request"]
    if not reviews:
        return "pull-request review"
    if not any((r.get("parameters") or {}).get("require_code_owner_review") for r in reviews):
        return "CODEOWNERS review"
    if not any(r.get("type") == "required_status_checks" for r in rules):
        return "status checks (CI)"
    return None


def status(app_state: Any = None) -> dict[str, Any]:
    """
    The last probed status (the local prerequisites when no probe ran yet).

    :returns: ``{enabled, reason}`` (plus ``checks`` when probed).
    """
    cached = getattr(app_state, STATE_ATTR, None) if app_state is not None else None
    reason = _local_reason()
    if reason is not None:
        return {"enabled": False, "reason": reason}
    if isinstance(cached, dict):
        return {k: cached[k] for k in ("enabled", "reason", "checks") if k in cached}
    return {"enabled": False, "reason": "staging protection not checked yet"}


def start_probe(app_state: Any, transport: Transport = None) -> Callable[[], Awaitable[None]]:
    """Probe once in the background at server start. :returns: An async stop callable."""
    if _local_reason() is not None:

        async def _noop() -> None:
            return None

        return _noop

    async def _run() -> None:
        result = await probe(transport)
        setattr(app_state, STATE_ATTR, result)
        _logger.info("factory Tier 2: %s", "enabled" if result["enabled"] else result["reason"])

    task = asyncio.get_running_loop().create_task(_run(), name="factory-tier2-probe")

    async def _stop() -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    return _stop


__all__ = ["LOGIN_ENV", "REPO_ENV", "SMITH_ENV", "TOKEN_ENV", "probe", "start_probe", "status"]
