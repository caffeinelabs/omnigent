"""A thin Slack Web API client for the DM notifications (plain ``httpx``).

One call is one HTTP request: retries and backoff belong to the caller (the
engine reschedules an item; a person route answers with the error). Every
failure is a :class:`SlackError` classified as ``retryable``, ``bot`` (the
token is unusable: stop sending for the factory), ``person`` (this Slack
account cannot get DMs), ``message`` (the message itself was refused) or
``other``.

Logs carry the method and Slack's error code only: never the token, and never
message text (it may carry agent text).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import httpx

_logger = logging.getLogger(__name__)

API_ENV: Final = "OMNIGENT_FACTORY_SLACK_API"
"""Overrides the API base URL (tests point it at a fake)."""
DEFAULT_API: Final = "https://slack.com/api/"
CONNECT_TIMEOUT_S: Final = 5.0
TOTAL_TIMEOUT_S: Final = 10.0

ErrorKind = Literal["retryable", "bot", "person", "message", "other"]

RETRYABLE_CODES: Final = frozenset(
    {
        "ratelimited",
        "rate_limited",
        "internal_error",
        "fatal_error",
        "service_unavailable",
        "request_timeout",
        "network_error",
        "http_error",
    }
)
BOT_CODES: Final = frozenset(
    {
        "invalid_auth",
        "not_authed",
        "account_inactive",
        "token_revoked",
        "token_expired",
        "missing_scope",
        "no_permission",
        "team_access_not_granted",
        "ekm_access_denied",
        "org_login_required",
        "not_allowed_token_type",
    }
)
PERSON_CODES: Final = frozenset(
    {
        "user_not_found",
        "users_not_found",
        "cannot_dm_bot",
        "user_disabled",
        "messages_tab_disabled",
        "is_archived",
        "channel_not_found",
        "user_is_bot",
        "wrong_workspace",
    }
)
MESSAGE_CODES: Final = frozenset(
    {"msg_too_long", "msg_blocks_too_long", "invalid_blocks", "no_text", "invalid_arguments"}
)


def classify(code: str) -> ErrorKind:
    """:returns: The class of a Slack error code (``other`` when unknown)."""
    if code in RETRYABLE_CODES:
        return "retryable"
    if code in BOT_CODES:
        return "bot"
    if code in PERSON_CODES:
        return "person"
    if code in MESSAGE_CODES:
        return "message"
    return "other"


class SlackError(Exception):
    """
    A failed Slack call.

    :param code: Slack's ``error`` (e.g. ``"invalid_auth"``), or ``network_error`` /
        ``http_error`` / ``ratelimited`` for transport failures.
    :param method: The API method, e.g. ``"chat.postMessage"``.
    :param retry_after: Seconds Slack asked us to wait (HTTP 429), if any.
    """

    def __init__(self, code: str, method: str, *, retry_after: float | None = None) -> None:
        super().__init__(f"{method}: {code}")
        self.code = code
        self.method = method
        self.retry_after = retry_after

    @property
    def kind(self) -> ErrorKind:
        """:returns: :func:`classify` of :attr:`code`."""
        return classify(self.code)


@dataclass(frozen=True)
class AuthInfo:
    """What ``auth.test`` says about a bot token."""

    team_id: str
    team: str
    bot_user_id: str
    bot_id: str | None
    user: str | None
    scopes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SlackUser:
    """A resolved Slack account (``users.info`` / ``users.lookupByEmail``)."""

    id: str
    team_id: str
    name: str
    handle: str
    email: str | None = None
    tz: str | None = None
    deleted: bool = False
    is_bot: bool = False


def _base_url() -> str:
    base = os.environ.get(API_ENV, "").strip() or DEFAULT_API
    return base if base.endswith("/") else base + "/"


_transport: httpx.AsyncBaseTransport | None = None
"""A transport every client uses instead of the network (tests set it)."""


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Route every Slack call through *transport* (``None`` restores the network)."""
    global _transport
    _transport = transport


def _scopes(headers: Mapping[str, str]) -> list[str]:
    raw = headers.get("x-oauth-scopes") or ""
    return sorted({s.strip() for s in raw.split(",") if s.strip()})


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    try:
        return max(0.0, float(raw)) if raw is not None else None
    except ValueError:
        return None


def _user(raw: Mapping[str, Any]) -> SlackUser:
    profile = raw.get("profile") or {}
    handle = str(raw.get("name") or "")
    name = (
        profile.get("real_name")
        or raw.get("real_name")
        or profile.get("display_name")
        or handle
        or str(raw.get("id") or "")
    )
    return SlackUser(
        id=str(raw.get("id") or ""),
        team_id=str(raw.get("team_id") or profile.get("team") or ""),
        name=str(name),
        handle=str(profile.get("display_name") or handle),
        email=profile.get("email") if isinstance(profile.get("email"), str) else None,
        tz=raw.get("tz") if isinstance(raw.get("tz"), str) else None,
        deleted=bool(raw.get("deleted")),
        is_bot=bool(raw.get("is_bot")) or raw.get("id") == "USLACKBOT",
    )


class SlackClient:
    """
    Slack Web API calls with one bot token.

    :param token: The ``xoxb-`` token (never logged).
    """

    def __init__(self, token: str) -> None:
        self._token = token

    async def call(
        self,
        method: str,
        *,
        form: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], httpx.Headers]:
        """
        One API call.

        :param form: Form fields (most read methods).
        :param payload: A JSON body (``chat.postMessage``).
        :returns: ``(body, headers)`` of an ``ok`` answer.
        :raises SlackError: On any failure.
        """
        timeout = httpx.Timeout(TOTAL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with httpx.AsyncClient(
                base_url=_base_url(),
                timeout=timeout,
                follow_redirects=False,
                transport=_transport,
            ) as client:
                if payload is not None:
                    headers["Content-Type"] = "application/json; charset=utf-8"
                    resp = await client.post(method, json=dict(payload), headers=headers)
                else:
                    resp = await client.post(method, data=dict(form or {}), headers=headers)
        except httpx.HTTPError as exc:
            _logger.warning("factory slack: %s failed: %s", method, type(exc).__name__)
            raise SlackError("network_error", method) from None
        if resp.status_code == 429:
            raise SlackError("ratelimited", method, retry_after=_retry_after(resp))
        if resp.status_code >= 500:
            raise SlackError("http_error", method, retry_after=_retry_after(resp))
        try:
            body = resp.json()
        except ValueError:
            raise SlackError("http_error", method) from None
        if not isinstance(body, dict) or not body.get("ok"):
            code = str((body or {}).get("error") or f"http_{resp.status_code}")
            _logger.info("factory slack: %s refused: %s", method, code)
            raise SlackError(code, method, retry_after=_retry_after(resp))
        return body, resp.headers

    async def auth_test(self) -> AuthInfo:
        """``auth.test``: team, bot user and granted scopes (``x-oauth-scopes``)."""
        body, headers = await self.call("auth.test")
        return AuthInfo(
            team_id=str(body.get("team_id") or ""),
            team=str(body.get("team") or ""),
            bot_user_id=str(body.get("user_id") or ""),
            bot_id=body.get("bot_id") if isinstance(body.get("bot_id"), str) else None,
            user=body.get("user") if isinstance(body.get("user"), str) else None,
            scopes=_scopes(headers),
        )

    async def users_info(self, user_id: str) -> SlackUser:
        """``users.info``: one account by member id."""
        body, _ = await self.call("users.info", form={"user": user_id})
        return _user(body.get("user") or {})

    async def lookup_by_email(self, email: str) -> SlackUser:
        """``users.lookupByEmail`` (needs ``users:read.email``)."""
        body, _ = await self.call("users.lookupByEmail", form={"email": email})
        return _user(body.get("user") or {})

    async def open_dm(self, user_id: str) -> str:
        """``conversations.open``: the app's DM channel (``D…``) with *user_id*."""
        body, _ = await self.call("conversations.open", form={"users": user_id})
        channel = (body.get("channel") or {}).get("id")
        if not isinstance(channel, str) or not channel:
            raise SlackError("channel_not_found", "conversations.open")
        return channel

    async def post_message(
        self, channel: str, text: str, blocks: Sequence[Mapping[str, Any]] | None = None
    ) -> str:
        """
        ``chat.postMessage`` into a DM: never parsed (no mentions or links from
        text), never unfurled.

        :returns: The message ``ts``.
        """
        payload: dict[str, Any] = {
            "channel": channel,
            "text": text,
            "parse": "none",
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if blocks:
            payload["blocks"] = list(blocks)
        body, _ = await self.call("chat.postMessage", payload=payload)
        return str(body.get("ts") or "")


__all__ = [
    "API_ENV",
    "BOT_CODES",
    "MESSAGE_CODES",
    "PERSON_CODES",
    "RETRYABLE_CODES",
    "AuthInfo",
    "ErrorKind",
    "SlackClient",
    "SlackError",
    "SlackUser",
    "classify",
    "set_transport",
]
