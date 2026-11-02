"""The factory's Slack bot token: a Tier-0 secret, set by owners, never read back.

Stored like the incident webhook (:mod:`omnigent.server.factory.issues`): the
token at ``factory/secrets/{fid}/slack_bot_token`` and non-secret metadata
(workspace, bot user, scopes, status) at ``…/slack_bot_meta.json``, both in the
server's artifact store under a prefix no route serves, both cached for a
minute. No response, event, log, snapshot or agent route ever carries the token.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Final

from omnigent.server.auth import LEVEL_MANAGE
from omnigent.server.factory import events, issues
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import (
    CockpitError,
    human_label,
    load_factory_root,
    require,
)
from omnigent.server.factory.slackdm.client import SlackClient, SlackError
from omnigent.stores.factory_store import norm_id

_logger = logging.getLogger(__name__)

TOKEN_KEY_TEMPLATE: Final = "factory/secrets/{fid}/slack_bot_token"
META_KEY_TEMPLATE: Final = "factory/secrets/{fid}/slack_bot_meta.json"
TOKEN_RE: Final = re.compile(r"^xoxb-[A-Za-z0-9-]{20,250}$")
REQUIRED_SCOPES: Final = ("chat:write", "im:write", "users:read", "users:read.email")
CACHE_S: Final = 60.0
STATUS_OK: Final = "ok"
STATUS_INVALID: Final = "invalid"
INCIDENT_RULE: Final = "slack.bot"
INCIDENT_TARGET: Final = "factory"

_token_cache: dict[str, tuple[float, str | None]] = {}
_meta_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _store(deps: FactoryDeps) -> Any:
    return deps.artifact_store or deps.factory_artifact_store


def _key(template: str, factory_id: str) -> str:
    return template.format(fid=norm_id(factory_id))


def _read(deps: FactoryDeps, key: str) -> bytes | None:
    store = _store(deps)
    if store is None:
        return None
    try:
        return store.get(key) if store.exists(key) else None
    except Exception:  # noqa: BLE001 - DMs pause; the cockpit still works
        _logger.warning("factory slack: could not read a Slack secret")
        return None


def bot_token(deps: FactoryDeps, factory_id: str) -> str | None:
    """:returns: The factory's bot token (server side only), cached for a minute."""
    fid = norm_id(factory_id)
    cached = _token_cache.get(fid)
    if cached is not None and time.monotonic() - cached[0] < CACHE_S:
        return cached[1]
    raw = _read(deps, _key(TOKEN_KEY_TEMPLATE, fid))
    value = raw.decode().strip() or None if raw is not None else None
    _token_cache[fid] = (time.monotonic(), value)
    return value


def bot_meta(deps: FactoryDeps, factory_id: str) -> dict[str, Any] | None:
    """:returns: The token's metadata (workspace, bot, scopes, status), cached for a minute."""
    fid = norm_id(factory_id)
    cached = _meta_cache.get(fid)
    if cached is not None and time.monotonic() - cached[0] < CACHE_S:
        return dict(cached[1]) if cached[1] is not None else None
    raw = _read(deps, _key(META_KEY_TEMPLATE, fid))
    meta: dict[str, Any] | None = None
    if raw is not None:
        try:
            loaded = json.loads(raw.decode())
            meta = loaded if isinstance(loaded, dict) else None
        except ValueError:
            meta = None
    _meta_cache[fid] = (time.monotonic(), meta)
    return dict(meta) if meta is not None else None


def write_meta(deps: FactoryDeps, factory_id: str, meta: dict[str, Any]) -> None:
    """Store the token metadata (synchronous)."""
    store = _store(deps)
    if store is None:
        return
    fid = norm_id(factory_id)
    store.put(_key(META_KEY_TEMPLATE, fid), json.dumps(meta, sort_keys=True).encode())
    _meta_cache[fid] = (time.monotonic(), dict(meta))


def is_active(deps: FactoryDeps, factory_id: str) -> bool:
    """:returns: Whether DMs can go out: a token is set and was last seen working."""
    meta = bot_meta(deps, factory_id)
    return (
        meta is not None
        and meta.get("status") == STATUS_OK
        and bot_token(deps, factory_id) is not None
    )


def missing_scopes(scopes: list[str] | None) -> list[str]:
    """:returns: The scopes v1 needs that *scopes* lacks."""
    have = set(scopes or [])
    return [s for s in REQUIRED_SCOPES if s not in have]


def meta_view(meta: dict[str, Any] | None) -> dict[str, Any]:
    """:returns: The ``slack_dm`` block of the routing view (no token, no member ids)."""
    if meta is None:
        return {
            "configured": False,
            "team": None,
            "bot": None,
            "status": None,
            "error": None,
            "missing_scopes": [],
            "set_by": None,
            "set_at": None,
        }
    return {
        "configured": True,
        "team": meta.get("team"),
        "bot": meta.get("bot_name"),
        "status": meta.get("status"),
        "error": meta.get("error"),
        "missing_scopes": missing_scopes(meta.get("scopes")),
        "set_by": meta.get("set_by"),
        "set_at": meta.get("set_at"),
    }


def valid_token(token: str) -> bool:
    """:returns: Whether *token* looks like a bot token (``xoxb-…``)."""
    return isinstance(token, str) and TOKEN_RE.fullmatch(token) is not None


def _refusal(token: str) -> CockpitError:
    if token.startswith("xoxp-"):
        why = "that is a user token (xoxp-); paste the app's Bot User OAuth Token (xoxb-)"
    elif token.startswith(("xapp-", "xoxa-")):
        why = "that is an app-level token; paste the app's Bot User OAuth Token (xoxb-)"
    elif token.startswith("https://"):
        why = "that is a webhook URL; webhooks go under Slack (incidents), the bot token here"
    else:
        why = "paste the app's Bot User OAuth Token: it starts with xoxb-"
    return CockpitError(400, "invalid_token", why, field="token")


async def _emit_routing(app_state: Any, factory_id: str, summary: str, **data: Any) -> None:
    await events.emit(
        app_state,
        factory_id,
        "alerts.routing",
        severity="notice",
        subject="alerts:slack-bot",
        summary=summary,
        data=data,
    )


async def set_bot_token(
    app_state: Any, factory_id: str, *, user_id: str | None, token: str | None
) -> dict[str, Any]:
    """
    Set (after ``auth.test``) or remove the factory's bot token (MANAGE on the root).

    A token of a different workspace resets every Slack link made in another one.

    :returns: The routing view's ``slack_dm`` block.
    :raises CockpitError: 400 ``invalid_token``; 409 without a secret store.
    """
    from omnigent.server.factory.slackdm import prefs

    deps = factory_deps(app_state)
    root = await load_factory_root(deps, user_id, factory_id)
    await require(deps, user_id, root.id, LEVEL_MANAGE)
    token = (token or "").strip() or None
    store = _store(deps)
    if store is None:
        raise CockpitError(409, "no_secret_store", "this server has no artifact store")
    by = await human_label(deps, root.id, user_id)
    fid = norm_id(root.id)
    token_key = _key(TOKEN_KEY_TEMPLATE, fid)
    meta_key = _key(META_KEY_TEMPLATE, fid)
    if token is None:
        for key in (token_key, meta_key):
            if await asyncio.to_thread(store.exists, key):
                await asyncio.to_thread(store.delete, key)
        reset()
        await issues.clear_issue(
            app_state, root.id, INCIDENT_RULE, INCIDENT_TARGET, reason="the Slack app was removed"
        )
        await _emit_routing(
            app_state, root.id, f"{by} removed the Slack app", by=by, by_kind="human"
        )
        return meta_view(None)
    if not valid_token(token):
        raise _refusal(token)
    try:
        info = await SlackClient(token).auth_test()
    except SlackError as exc:
        raise CockpitError(
            400, "invalid_token", f"Slack refused the token ({exc.code})", field="token"
        ) from None
    now = int(time.time())
    meta = {
        "team_id": info.team_id,
        "team": info.team,
        "bot_user_id": info.bot_user_id,
        "bot_id": info.bot_id,
        "bot_name": info.user,
        "scopes": info.scopes,
        "set_by": user_id,
        "set_at": now,
        "status": STATUS_OK,
        "status_at": now,
        "error": None,
    }
    await asyncio.to_thread(store.put, token_key, token.encode())
    await asyncio.to_thread(write_meta, deps, root.id, meta)
    _token_cache[fid] = (time.monotonic(), token)
    # Links made in another workspace (also one whose token was removed since) go.
    await asyncio.to_thread(
        prefs.reset_links, deps, root.id, "Slack workspace changed", keep_team=info.team_id
    )
    await issues.clear_issue(
        app_state, root.id, INCIDENT_RULE, INCIDENT_TARGET, reason="a working Slack token"
    )
    bot = f"@{info.user}" if info.user else "its bot"
    await _emit_routing(
        app_state,
        root.id,
        f"{by} set the Slack app ({info.team or info.team_id}, {bot})",
        by=by,
        by_kind="human",
        team=info.team,
        missing_scopes=missing_scopes(info.scopes),
    )
    return meta_view(meta)


async def mark_invalid(
    app_state: Any, factory_id: str, code: str, *, token: str | None = None
) -> None:
    """
    The token stopped working (a ``bot`` error): stop sending and tell the owners.

    Raises the ``slack.bot`` incident (warning, owners); saving a working token
    resolves it.

    :param token: The token that failed; when an owner saved another one since,
        nothing is marked (the new token is not judged by the old one's failure).
    """
    deps = factory_deps(app_state)
    if token is not None and await asyncio.to_thread(bot_token, deps, factory_id) != token:
        return
    meta = await asyncio.to_thread(bot_meta, deps, factory_id) or {}
    if meta.get("status") == STATUS_INVALID and meta.get("error") == code:
        return
    meta.update(status=STATUS_INVALID, status_at=int(time.time()), error=code)
    try:
        await asyncio.to_thread(write_meta, deps, factory_id, meta)
    except Exception:  # noqa: BLE001 - the in-memory cache still stops the sends
        _logger.warning("factory slack: could not store the token status", exc_info=True)
        _meta_cache[norm_id(factory_id)] = (time.monotonic(), meta)
    await issues.raise_issue(
        app_state,
        factory_id,
        rule=INCIDENT_RULE,
        target=INCIDENT_TARGET,
        summary=f"Slack DMs stopped: the Slack app token fails ({code})",
        severity="warning",
        assignee=issues.ASSIGN_OWNERS,
        body=(
            "Slack refused the factory's bot token, so no DMs go out. An owner pastes a "
            "working token under Settings > Alerts > Slack app."
        ),
    )
    await _emit_routing(
        app_state, factory_id, f"Slack DMs stopped: the Slack app token fails ({code})", error=code
    )


def reset() -> None:
    """Forget cached tokens and metadata (tests, and after a removal)."""
    _token_cache.clear()
    _meta_cache.clear()


__all__ = [
    "INCIDENT_RULE",
    "INCIDENT_TARGET",
    "REQUIRED_SCOPES",
    "STATUS_INVALID",
    "STATUS_OK",
    "bot_meta",
    "bot_token",
    "is_active",
    "mark_invalid",
    "meta_view",
    "missing_scopes",
    "reset",
    "set_bot_token",
    "valid_token",
    "write_meta",
]
