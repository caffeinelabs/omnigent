"""Slack DM routes: the owners' Slack app token and each person's own settings.

All under ``/v1/factory/factories/{fid}``; every write carries
:data:`~omnigent.server.factory.origin.HUMAN_ORIGIN` (agents get 403 ``human_only``).

- ``PUT alerts/slack-bot`` ``{token | null}``: owners set (after ``auth.test``) or
  remove the bot token. It is never returned.
- ``POST alerts/slack-bot/test``: owners check the token and get a test DM.
- ``GET notify/me`` / ``PUT notify/me``: the caller's settings, link and last DM.
- ``POST notify/me/slack`` ``{member_id | email, send_code}``: find the caller's
  Slack account, then DM it a 6-digit code; ``POST notify/me/slack/verify``
  ``{code}`` links it; ``DELETE notify/me/slack`` unlinks.
- ``POST notify/me/test``: a test DM with the caller's effective settings.
- ``GET notify/team``: owners see who is reachable (no member ids).

A person can only link their own account. Someone with access but no roster
row gets 409 ``not_on_roster``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Final, TypeVar

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from omnigent.server.auth import LEVEL_MANAGE
from omnigent.server.factory import control, issues, people
from omnigent.server.factory import presence as factory_presence
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.factory.slackdm import digest as digest_mod
from omnigent.server.factory.slackdm import engine as engine_mod
from omnigent.server.factory.slackdm import prefs as prefs_mod
from omnigent.server.factory.slackdm import render
from omnigent.server.factory.slackdm import secrets as bot_secrets
from omnigent.server.factory.slackdm.client import SlackClient, SlackError, SlackUser
from omnigent.server.routes._auth_helpers import get_user_id
from omnigent.stores.factory_store import norm_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])

CODE_TTL_S: Final = 15 * 60
CODE_TRIES: Final = 5
WINDOW_S: Final = 600.0
LOOKUPS_PER_WINDOW: Final = 5
TESTS_PER_WINDOW: Final = 5
LAST_DM_LOOKBACK_S: Final = 30 * 86400
_MEMBER_RE: Final = re.compile(r"^[UW][A-Z0-9]{2,20}$")
_EMAIL_RE: Final = re.compile(r"^[^\s@<>|]{1,64}@[^\s@<>|]{1,190}\.[^\s@<>|]{2,}$")
_CODE_RE: Final = re.compile(r"^[0-9]{6}$")
_T = TypeVar("_T")


class BotTokenBody(BaseModel):
    """``PUT …/alerts/slack-bot``: the ``xoxb-`` token, or ``null`` to remove it."""

    model_config = ConfigDict(extra="forbid")

    token: str | None = None


class LinkBody(BaseModel):
    """``POST …/notify/me/slack``: a member id or a Slack email."""

    model_config = ConfigDict(extra="forbid")

    member_id: str | None = None
    email: str | None = None
    send_code: bool = False


class VerifyBody(BaseModel):
    """``POST …/notify/me/slack/verify``."""

    model_config = ConfigDict(extra="forbid")

    code: str


async def _run(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


def _caller(request: Request) -> tuple[FactoryDeps, str | None]:
    deps = factory_deps(request.app.state)
    return deps, get_user_id(request, deps.auth_provider)


# ── Rate limits (per factory and person, in process) ───────────

_slots: dict[tuple[str, str, str], deque[float]] = {}


def _take_slot(factory_id: str, user_id: str, kind: str, limit: int) -> None:
    """:raises CockpitError: 429 ``rate_limited`` past *limit* per 10 minutes."""
    now = time.monotonic()
    window = _slots.setdefault((norm_id(factory_id), user_id, kind), deque())
    while window and now - window[0] >= WINDOW_S:
        window.popleft()
    if len(window) >= limit:
        retry = int(WINDOW_S - (now - window[0])) + 1
        raise CockpitError(
            429,
            "rate_limited",
            f"at most {limit} per 10 minutes; try again in {retry} s",
            retry_after=retry,
        )
    window.append(now)


def reset() -> None:
    """Forget rate-limit windows (tests)."""
    _slots.clear()


# ── The caller and their roster row ────────────────────────────


async def _me(
    deps: FactoryDeps, user_id: str | None, factory_id: str
) -> tuple[Any, Any, prefs_mod.NotifyPrefs]:
    """:returns: ``(root, roster row, settings)`` of the caller."""
    root = await control.load_factory_root(deps, user_id, factory_id)
    if user_id is None:
        raise CockpitError(
            409, "not_on_roster", "Slack DMs are for signed-in people on the factory's roster"
        )
    rows = await asyncio.to_thread(people.list_people, deps, root.id)
    person = next((p for p in rows if p.user_id == user_id), None)
    if person is None:
        raise CockpitError(
            409, "not_on_roster", "you are not on this factory's roster; ask an owner to add you"
        )
    return root, person, prefs_mod.for_person(person)


def _last_dm(deps: FactoryDeps, factory_id: str, user_id: str) -> dict[str, Any] | None:
    since = int((time.time() - LAST_DM_LOOKBACK_S) * 1000)
    rows = deps.factory_store.find_events(factory_id, engine_mod.LEDGER_KIND, since, limit=5000)
    mine = [e for e in rows if (e.data or {}).get("to_user") == user_id]
    if not mine:
        return None
    last = mine[-1]
    data = last.data or {}
    return {
        "at": last.ts // 1000,
        "summary": last.summary,
        "subject": last.subject,
        "status": data.get("status"),
        "error": data.get("error"),
    }


def _me_view(
    deps: FactoryDeps, root: Any, person: Any, prefs: prefs_mod.NotifyPrefs
) -> dict[str, Any]:
    role = prefs_mod.role_key("creator" if person.is_creator else person.role)
    now = time.time()
    quiet = prefs_mod.in_quiet(prefs, now)
    present = any(
        e.user_id == person.user_id and not e.idle
        for e in factory_presence.registry.entries(root.id)
    )
    return {
        "user_id": person.user_id,
        "role": role,
        "settings": prefs_mod.settings_view(prefs),
        "defaults": prefs_mod.role_defaults(role),
        "slack": prefs_mod.link_view(prefs),
        "bot": bot_secrets.meta_view(bot_secrets.bot_meta(deps, root.id)),
        "last_dm": _last_dm(deps, root.id, person.user_id),
        "now": {
            "quiet": quiet,
            "quiet_until": int(prefs_mod.quiet_end(prefs, now)) if quiet else None,
            "held_while_active": bool(prefs.hold_while_active and present),
            "tz": prefs_mod.tz_name(prefs),
        },
    }


async def _view(deps: FactoryDeps, root: Any, user_id: str) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        person = deps.factory_store.get_person(root.id, user_id)
        assert person is not None
        return _me_view(deps, root, person, prefs_mod.for_person(person))

    return await asyncio.to_thread(_load)


async def _client(deps: FactoryDeps, root: Any) -> tuple[SlackClient, dict[str, Any]]:
    """:returns: A client with the factory's token and its metadata. :raises CockpitError: 409."""
    token = await asyncio.to_thread(bot_secrets.bot_token, deps, root.id)
    meta = await asyncio.to_thread(bot_secrets.bot_meta, deps, root.id)
    if token is None or meta is None:
        raise CockpitError(
            409, "slack_not_set_up", "no Slack app yet: an owner adds it under Settings > Alerts"
        )
    if meta.get("status") != bot_secrets.STATUS_OK:
        raise CockpitError(
            409,
            "slack_app_broken",
            f"the Slack app token fails ({meta.get('error')}); an owner replaces it",
        )
    return SlackClient(token), meta


async def _slack_failed(app_state: Any, root: Any, exc: SlackError) -> CockpitError:
    if exc.kind == "bot":
        await bot_secrets.mark_invalid(app_state, root.id, exc.code)
        if exc.code == "missing_scope":
            return CockpitError(
                409, "missing_scope", "the Slack app lacks a scope; reinstall it from the manifest"
            )
    if exc.code in ("user_not_found", "users_not_found"):
        return CockpitError(404, "slack_user_not_found", "Slack has no such account here")
    if exc.kind == "retryable":
        return CockpitError(
            503, "slack_unavailable", f"Slack did not answer ({exc.code}); try again"
        )
    return CockpitError(502, "slack_error", f"Slack refused it ({exc.code})", slack_error=exc.code)


def _account(user: SlackUser) -> dict[str, Any]:
    return {
        "user_id": user.id,
        "team_id": user.team_id,
        "name": user.name,
        "handle": user.handle,
        "email": user.email,
        "tz": user.tz,
    }


def _hash(factory_id: str, user_id: str, slack_user: str, code: str) -> str:
    raw = f"{norm_id(factory_id)}|{user_id}|{slack_user}|{code}".encode()
    return hashlib.sha256(raw).hexdigest()


# ── Owners: the Slack app ──────────────────────────────────────


@router.put(
    "/factories/{factory_id}/alerts/slack-bot", response_model=None, dependencies=HUMAN_ORIGIN
)
async def put_slack_bot(
    request: Request, factory_id: str, body: BotTokenBody
) -> dict[str, Any] | JSONResponse:
    """Set or remove the Slack app's bot token (owners). The token is never returned."""
    _, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        view = await bot_secrets.set_bot_token(
            request.app.state, factory_id, user_id=user_id, token=body.token
        )
        return {"slack_dm": view}

    return await _run(run)


@router.post(
    "/factories/{factory_id}/alerts/slack-bot/test",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def test_slack_bot(request: Request, factory_id: str) -> dict[str, Any] | JSONResponse:
    """Check the token (``auth.test``) and DM the owner who pressed Test, when linked."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        await control.require(deps, user_id, root.id, LEVEL_MANAGE)
        token = await asyncio.to_thread(bot_secrets.bot_token, deps, root.id)
        if token is None:
            raise CockpitError(409, "slack_not_set_up", "no Slack app token is set")
        _take_slot(root.id, user_id or "", "test", TESTS_PER_WINDOW)
        client = SlackClient(token)
        try:
            info = await client.auth_test()
        except SlackError as exc:
            if exc.kind == "bot":
                await bot_secrets.mark_invalid(request.app.state, root.id, exc.code)
            return {"ok": False, "error": exc.code, "dm": None}
        meta = await asyncio.to_thread(bot_secrets.bot_meta, deps, root.id) or {}
        if meta.get("status") != bot_secrets.STATUS_OK:
            # The token works again (a transient failure): DMs resume.
            meta.update(status=bot_secrets.STATUS_OK, error=None, scopes=info.scopes)
            await asyncio.to_thread(bot_secrets.write_meta, deps, root.id, meta)
            await issues.clear_issue(
                request.app.state,
                root.id,
                bot_secrets.INCIDENT_RULE,
                bot_secrets.INCIDENT_TARGET,
                reason="the Slack app token works again",
            )
        dm = "not_linked"
        person = await asyncio.to_thread(deps.factory_store.get_person, root.id, user_id or "")
        if person is not None and prefs_mod.for_person(person).reachable:
            ctx = await engine_mod.factory_ctx(request.app.state, root)
            by = await control.human_label(deps, root.id, user_id)
            text, blocks = digest_mod.bot_test_message(ctx.name, by)
            try:
                await engine_mod.post_to_person(
                    deps, root.id, person.user_id, client, text, blocks
                )
                dm = "sent"
                await _record_direct(request.app.state, root.id, person.user_id, "test", "sent")
            except SlackError as exc:
                dm = exc.code
        return {
            "ok": True,
            "team": info.team,
            "bot": info.user,
            "missing_scopes": bot_secrets.missing_scopes(info.scopes),
            "dm": dm,
        }

    return await _run(run)


async def _record_direct(
    app_state: Any,
    factory_id: str,
    user_id: str,
    stage: str,
    status: str,
    error: str | None = None,
) -> None:
    titles = {
        "test": "test DM",
        "verify": "verification code",
        "welcome": "welcome DM",
    }
    await engine_mod.record(
        app_state,
        factory_id,
        user_id,
        f"{stage}:{int(time.time() * 1000)}",
        subject="notify:me",
        stage=stage,
        title=titles.get(stage, stage),
        status=status,
        error=error,
    )


# ── People: their own settings and link ────────────────────────


@router.get("/factories/{factory_id}/notify/me", response_model=None)
async def get_notify_me(request: Request, factory_id: str) -> dict[str, Any] | JSONResponse:
    """The caller's Slack DM settings (with role defaults), link, the app and their last DM."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        root, person, prefs = await _me(deps, user_id, factory_id)
        return await asyncio.to_thread(_me_view, deps, root, person, prefs)

    return await _run(run)


@router.put("/factories/{factory_id}/notify/me", response_model=None, dependencies=HUMAN_ORIGIN)
async def put_notify_me(
    request: Request, factory_id: str, body: prefs_mod.PrefsUpdate
) -> dict[str, Any] | JSONResponse:
    """Change the caller's own settings (events, incident minimum, digest, quiet hours, tz)."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        root, person, _ = await _me(deps, user_id, factory_id)
        await asyncio.to_thread(
            prefs_mod.update_notify,
            deps,
            root.id,
            person.user_id,
            lambda raw: prefs_mod.apply_update(raw, body),
        )
        return await _view(deps, root, person.user_id)

    return await _run(run)


@router.post(
    "/factories/{factory_id}/notify/me/slack", response_model=None, dependencies=HUMAN_ORIGIN
)
async def link_slack(
    request: Request, factory_id: str, body: LinkBody
) -> dict[str, Any] | JSONResponse:
    """Find the caller's Slack account; with ``send_code``, DM it a verification code."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        root, person, _ = await _me(deps, user_id, factory_id)
        member = (body.member_id or "").strip().upper()
        email = (body.email or "").strip()
        if bool(member) == bool(email):
            raise CockpitError(400, "invalid_body", "give a Slack member id or a Slack email")
        if member and not _MEMBER_RE.fullmatch(member):
            raise CockpitError(
                400, "invalid_member_id", "a member id looks like U0123ABCD", field="member_id"
            )
        if email and not _EMAIL_RE.fullmatch(email):
            raise CockpitError(400, "invalid_email", "that is not an email", field="email")
        client, meta = await _client(deps, root)
        _take_slot(root.id, person.user_id, "lookup", LOOKUPS_PER_WINDOW)
        try:
            found = await (client.users_info(member) if member else client.lookup_by_email(email))
        except SlackError as exc:
            raise await _slack_failed(request.app.state, root, exc) from None
        if found.team_id and meta.get("team_id") and found.team_id != meta.get("team_id"):
            raise CockpitError(
                400, "wrong_workspace", "that account is in another Slack workspace"
            )
        if found.deleted:
            raise CockpitError(400, "deactivated", "that Slack account is deactivated")
        if found.is_bot:
            raise CockpitError(400, "is_bot", "that is a bot, not a person")
        account = _account(found)
        if not body.send_code:
            return {"account": account, "status": "found"}
        code = engine_mod.new_code()
        by = await control.human_label(deps, root.id, person.user_id)
        ctx = await engine_mod.factory_ctx(request.app.state, root)
        text, blocks = digest_mod.code_message(ctx.name, code, by)
        try:
            channel = await client.open_dm(found.id)
            await client.post_message(channel, text, blocks)
        except SlackError as exc:
            raise await _slack_failed(request.app.state, root, exc) from None
        expires = int(time.time()) + CODE_TTL_S
        verify = {
            "hash": _hash(root.id, person.user_id, found.id, code),
            "expires_at": expires,
            "tries": 0,
            "by": person.user_id,
        }

        def _pending(raw: dict[str, Any]) -> dict[str, Any]:
            enabled = (raw.get("slack") or {}).get("enabled", True)
            raw["slack"] = {
                "enabled": enabled,
                "status": "pending",
                "user_id": found.id,
                "team_id": found.team_id or meta.get("team_id"),
                "name": found.name,
                "handle": found.handle,
                "email": found.email,
                "tz": found.tz,
                "dm_channel": channel,
                "error": None,
                "verify": verify,
            }
            return raw

        await asyncio.to_thread(prefs_mod.update_notify, deps, root.id, person.user_id, _pending)
        await _record_direct(request.app.state, root.id, person.user_id, "verify", "sent")
        return {"account": account, "status": "pending", "code_expires_at": expires}

    return await _run(run)


@router.post(
    "/factories/{factory_id}/notify/me/slack/verify",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def verify_slack(
    request: Request, factory_id: str, body: VerifyBody
) -> dict[str, Any] | JSONResponse:
    """Type back the code: the link is verified and a welcome DM lists what waits now."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        root, person, prefs = await _me(deps, user_id, factory_id)
        link = prefs.slack
        if link.status != "pending" or link.verify is None or not link.user_id:
            raise CockpitError(409, "no_code", "send a code first")
        code = body.code.strip()
        now = int(time.time())
        if link.verify.expires_at < now or link.verify.tries >= CODE_TRIES:
            raise CockpitError(410, "code_expired", "that code expired; send a new one")
        expected = link.verify.hash
        given = (
            _hash(root.id, person.user_id, link.user_id, code) if _CODE_RE.fullmatch(code) else ""
        )
        if not hmac.compare_digest(expected, given):
            tries = link.verify.tries + 1

            def _miss(raw: dict[str, Any]) -> dict[str, Any]:
                verify = dict((raw.get("slack") or {}).get("verify") or {})
                verify["tries"] = tries
                return prefs_mod.set_link(raw, verify=verify)

            await asyncio.to_thread(prefs_mod.update_notify, deps, root.id, person.user_id, _miss)
            left = max(0, CODE_TRIES - tries)
            raise CockpitError(
                400,
                "wrong_code",
                "wrong code" + (f"; {left} tries left" if left else "; send a new one"),
                field="code",
                tries_left=left,
            )

        def _verified(raw: dict[str, Any]) -> dict[str, Any]:
            slack = dict(raw.get("slack") or {})
            slack.update(status="verified", linked_at=now, error=None)
            slack.pop("verify", None)
            raw["slack"] = slack
            if not raw.get("tz") and slack.get("tz"):
                with contextlib.suppress(ValueError):
                    raw["tz"] = prefs_mod.valid_tz(slack["tz"])
            return raw

        await asyncio.to_thread(prefs_mod.update_notify, deps, root.id, person.user_id, _verified)
        await _welcome(request.app.state, deps, root, person.user_id)
        return await _view(deps, root, person.user_id)

    return await _run(run)


async def _welcome(app_state: Any, deps: FactoryDeps, root: Any, user_id: str) -> None:
    """The welcome DM (best effort: a failure leaves the link verified)."""
    from omnigent.server.factory.snapshot import get_snapshot

    try:
        token = await asyncio.to_thread(bot_secrets.bot_token, deps, root.id)
        if token is None:
            return
        ctx = await engine_mod.factory_ctx(app_state, root)
        person = ctx.people.get(user_id)
        if person is None:
            return
        raw = await get_snapshot(app_state, root) if deps.permission_store is not None else None
        base = render.base_url()
        waiting = await digest_mod.waiting_async(deps, ctx, person, raw, time.time(), base)
        text, blocks = digest_mod.welcome_message(ctx, person.prefs, waiting, base)
        await engine_mod.post_to_person(deps, root.id, user_id, SlackClient(token), text, blocks)
        await _record_direct(app_state, root.id, user_id, "welcome", "sent")
    except SlackError as exc:
        await _record_direct(app_state, root.id, user_id, "welcome", "failed", exc.code)
    except Exception:  # noqa: BLE001 - the link itself already worked
        import logging

        logging.getLogger(__name__).warning("factory slack: welcome DM failed", exc_info=True)


@router.delete(
    "/factories/{factory_id}/notify/me/slack", response_model=None, dependencies=HUMAN_ORIGIN
)
async def unlink_slack(request: Request, factory_id: str) -> dict[str, Any] | JSONResponse:
    """Unlink the caller's Slack account (their settings stay)."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        root, person, _ = await _me(deps, user_id, factory_id)
        await asyncio.to_thread(
            prefs_mod.update_notify, deps, root.id, person.user_id, prefs_mod.unlinked
        )
        return await _view(deps, root, person.user_id)

    return await _run(run)


@router.post(
    "/factories/{factory_id}/notify/me/test", response_model=None, dependencies=HUMAN_ORIGIN
)
async def test_notify_me(request: Request, factory_id: str) -> dict[str, Any] | JSONResponse:
    """DM the caller a test showing their effective settings."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        root, person, prefs = await _me(deps, user_id, factory_id)
        if prefs.slack.status != "verified":
            raise CockpitError(409, "not_linked", "link your Slack account first")
        client, _ = await _client(deps, root)
        _take_slot(root.id, person.user_id, "test", TESTS_PER_WINDOW)
        ctx = await engine_mod.factory_ctx(request.app.state, root)
        text, blocks = digest_mod.test_message(ctx.name, prefs)
        try:
            await engine_mod.post_to_person(deps, root.id, person.user_id, client, text, blocks)
        except SlackError as exc:
            if exc.kind == "person":
                await engine_mod.mark_person_error(deps, root.id, person.user_id, exc.code)
            await _record_direct(
                request.app.state, root.id, person.user_id, "test", "failed", exc.code
            )
            raise await _slack_failed(request.app.state, root, exc) from None
        await _record_direct(request.app.state, root.id, person.user_id, "test", "sent")
        return {"dm": "sent"}

    return await _run(run)


@router.get("/factories/{factory_id}/notify/team", response_model=None)
async def get_notify_team(request: Request, factory_id: str) -> dict[str, Any] | JSONResponse:
    """Who Slack DMs reach (owners). No member ids, channels or codes."""
    deps, user_id = _caller(request)

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        await control.require(deps, user_id, root.id, LEVEL_MANAGE)

        def _load() -> dict[str, Any]:
            now = time.time()
            since = int((now - LAST_DM_LOOKBACK_S) * 1000)
            ledger = deps.factory_store.find_events(
                root.id, engine_mod.LEDGER_KIND, since, limit=5000
            )
            last: dict[str, Any] = {}
            for event in ledger:
                last[(event.data or {}).get("to_user", "")] = event
            rows = []
            for person in people.list_people(deps, root.id):
                prefs = prefs_mod.for_person(person)
                seen = last.get(person.user_id)
                data = (seen.data or {}) if seen is not None else {}
                rows.append(
                    {
                        "user_id": person.user_id,
                        "role": prefs_mod.role_key(
                            "creator" if person.is_creator else person.role
                        ),
                        "slack_status": prefs.slack.status,
                        "error": prefs.slack.error,
                        "enabled": prefs.slack.enabled,
                        "reachable": prefs.reachable,
                        "groups_on": [g for g in prefs_mod.EVENT_GROUPS if prefs.wants(g)],
                        "digest": prefs.digest.enabled,
                        "quiet_now": prefs_mod.in_quiet(prefs, now),
                        "last_dm_at": seen.ts // 1000 if seen is not None else None,
                        "last_error": data.get("error")
                        if data.get("status") == "failed"
                        else None,
                    }
                )
            meta = bot_secrets.bot_meta(deps, root.id)
            return {
                "data": rows,
                "reachable": sum(1 for r in rows if r["reachable"]),
                "count": len(rows),
                "bot": bot_secrets.meta_view(meta),
            }

        return await asyncio.to_thread(_load)

    return await _run(run)


__all__ = ["reset", "router"]
