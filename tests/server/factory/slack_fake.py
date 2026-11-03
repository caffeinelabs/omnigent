"""A fake Slack Web API behind ``httpx.MockTransport`` for the Slack DM tests.

It knows a workspace, its people and one valid bot token; it records every
call (method, form or JSON body) and every DM posted, and can be scripted to
fail the next call of a method (an HTTP status, a Slack error, ``Retry-After``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl

import httpx

from omnigent.server.factory.slackdm import client as slack_client

# Built at runtime so secret scanners do not mistake the fakes for real tokens.
TOKEN = "-".join(["xoxb", "1111111111", "2222222222222", "FakeTokenForTestsOnlyAAAA"])
OTHER_TEAM_TOKEN = "-".join(["xoxb", "9999999999", "8888888888888", "FakeTokenForTestsOnlyBBBB"])
TEAM = "T0SAMPLE"
SCOPES = "chat:write,im:write,users:read,users:read.email"


@dataclass
class Failure:
    """A scripted answer to the next call of one method."""

    status: int = 200
    error: str | None = None
    retry_after: float | None = None
    raise_network: bool = False


@dataclass
class FakeSlack:
    """The workspace: people by member id, scripted failures and recorded calls."""

    team_id: str = TEAM
    team: str = "Sample Org"
    scopes: str = SCOPES
    tokens: dict[str, str] = field(
        default_factory=lambda: {TOKEN: TEAM, OTHER_TEAM_TOKEN: "T0OTHER"}
    )
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    posts: list[dict[str, Any]] = field(default_factory=list)
    failures: dict[str, list[Failure]] = field(default_factory=dict)
    auth_headers: list[str] = field(default_factory=list)

    def add_user(
        self,
        user_id: str,
        name: str,
        email: str,
        *,
        team: str | None = None,
        tz: str = "Europe/Zagreb",
        deleted: bool = False,
        is_bot: bool = False,
    ) -> None:
        """Put a person in the workspace."""
        self.users[user_id] = {
            "id": user_id,
            "team_id": team or self.team_id,
            "name": name.split()[0].lower(),
            "real_name": name,
            "tz": tz,
            "deleted": deleted,
            "is_bot": is_bot,
            "profile": {
                "real_name": name,
                "display_name": name.split()[0].lower(),
                "email": email,
            },
        }

    def fail_next(self, method: str, **kwargs: Any) -> None:
        """Answer the next call of *method* with a :class:`Failure`."""
        self.failures.setdefault(method, []).append(Failure(**kwargs))

    def dms_to(self, user_id: str) -> list[dict[str, Any]]:
        """:returns: ``chat.postMessage`` payloads into the DM channel of *user_id*."""
        return [p for p in self.posts if p["channel"] == f"D{user_id}"]

    def texts_to(self, user_id: str) -> list[str]:
        """:returns: Every DM to *user_id* as text plus its block text."""
        out = []
        for post in self.dms_to(user_id):
            parts = [post.get("text", "")]
            for block in post.get("blocks") or []:
                inner = block.get("text") or {}
                parts.append(inner.get("text", ""))
                parts += [e.get("text", "") for e in block.get("elements") or []]
            out.append("\n".join(parts))
        return out

    def methods(self) -> list[str]:
        """:returns: The methods called, in order."""
        return [m for m, _ in self.calls]

    # ── The transport ──

    def _ok(self, body: dict[str, Any], headers: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, **body}, headers=headers)

    def _err(self, error: str, status: int = 200) -> httpx.Response:
        return httpx.Response(status, json={"ok": False, "error": error})

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if request.headers.get("content-type", "").startswith("application/json"):
            body = json.loads(request.content or b"{}")
        else:
            body = dict(parse_qsl(request.content.decode()))
        self.calls.append((method, body))
        self.auth_headers.append(request.headers.get("authorization", ""))
        scripted = self.failures.get(method)
        if scripted:
            failure = scripted.pop(0)
            if failure.raise_network:
                raise httpx.ConnectError("boom", request=request)
            headers = {}
            if failure.retry_after is not None:
                headers["Retry-After"] = str(int(failure.retry_after))
            if failure.status != 200:
                return httpx.Response(failure.status, headers=headers, text="slow down")
            return httpx.Response(200, json={"ok": False, "error": failure.error}, headers=headers)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        team = self.tokens.get(token)
        if team is None:
            return self._err("invalid_auth")
        if method == "auth.test":
            return self._ok(
                {
                    "team": self.team if team == self.team_id else "Other",
                    "team_id": team,
                    "user_id": "UBOT",
                    "bot_id": "B0BOT",
                    "user": "omnigent-factory",
                },
                {"x-oauth-scopes": self.scopes},
            )
        if method == "users.info":
            user = self.users.get(body.get("user", ""))
            return self._ok({"user": user}) if user else self._err("user_not_found")
        if method == "users.lookupByEmail":
            user = next(
                (u for u in self.users.values() if u["profile"]["email"] == body.get("email")),
                None,
            )
            return self._ok({"user": user}) if user else self._err("users_not_found")
        if method == "conversations.open":
            user_id = body.get("users", "")
            if user_id not in self.users:
                return self._err("user_not_found")
            return self._ok({"channel": {"id": f"D{user_id}"}})
        if method == "chat.postMessage":
            self.posts.append(body)
            return self._ok({"ts": f"{len(self.posts)}.000100", "channel": body.get("channel")})
        return self._err("unknown_method")

    @property
    def transport(self) -> httpx.MockTransport:
        """:returns: The transport every Slack client should use."""
        return httpx.MockTransport(self.handler)


@contextmanager
def installed() -> Iterator[FakeSlack]:
    """A fake Slack workspace every Slack client talks to (alice, carol and vic are in it)."""
    fake = FakeSlack()
    fake.add_user("U0ALICE", "Alice Owner", "alice@slack.example", tz="UTC")
    fake.add_user("U0CAROL", "Carol Operator", "carol@slack.example", tz="UTC")
    fake.add_user("U0VIC", "Vic Viewer", "vic@slack.example", tz="UTC")
    slack_client.set_transport(fake.transport)
    try:
        yield fake
    finally:
        slack_client.set_transport(None)
