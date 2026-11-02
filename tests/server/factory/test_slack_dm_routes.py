"""Slack DM routes: the owners' token (Tier 0, never read back), each person's own
link (lookup, code DM, verify, welcome), settings, the team view and redaction."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.factory import issues
from omnigent.server.factory.api import slack_dm as slack_routes
from omnigent.server.factory.slackdm import secrets
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.slack_fake import OTHER_TEAM_TOKEN, TOKEN, FakeSlack, installed
from tests.server.factory.slack_helpers import (
    base,
    install_bot,
    link,
    notify_of,
    slack_cockpit,
)


@pytest.fixture()
def fake_slack() -> Iterator[FakeSlack]:
    with installed() as fake:
        yield fake


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Cockpit]:
    async for value in slack_cockpit(world, db_uri, monkeypatch, tmp_path):
        yield value


async def _get(cockpit: Cockpit, path: str, user: str = "alice") -> httpx.Response:
    return await cockpit.client.get(path, headers={"X-Test-User": user})


async def _put(cockpit: Cockpit, path: str, body: object, user: str = "alice") -> httpx.Response:
    return await cockpit.client.put(path, json=body, headers={"X-Test-User": user})


async def _delete(cockpit: Cockpit, path: str, user: str = "alice") -> httpx.Response:
    return await cockpit.client.delete(path, headers={"X-Test-User": user})


# ── The owners' token ──────────────────────────────────────────


async def test_the_bot_token_is_owner_set_human_only_and_never_read_back(
    cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    route = f"{base(cockpit)}/alerts/slack-bot"

    operator = await _put(cockpit, route, {"token": TOKEN}, user="carol")
    stranger = await _put(cockpit, route, {"token": TOKEN}, user="bob")
    agent = await cockpit.client.put(
        route,
        json={"token": TOKEN},
        headers={"X-Test-User": "alice", RUNNER_TUNNEL_TOKEN_HEADER: "rt_x.y"},
    )
    assert operator.status_code == 403
    assert stranger.status_code == 404
    assert agent.status_code == 403 and error_code(agent) == "human_only"

    user_token = await _put(cockpit, route, {"token": "xoxp-" + "1" * 40})
    assert error_code(user_token) == "invalid_token" and "user token" in user_token.text
    webhook = await _put(cockpit, route, {"token": "https://hooks.slack.com/services/x"})
    assert error_code(webhook) == "invalid_token"
    refused = await _put(
        cockpit, route, {"token": "-".join(["xoxb", "0000000000", "0000000000000", "unknown"])}
    )
    assert error_code(refused) == "invalid_token" and "invalid_auth" in refused.text

    ok = await _put(cockpit, route, {"token": TOKEN})
    assert ok.status_code == 200, ok.text
    assert ok.json()["slack_dm"] == {
        "configured": True,
        "team": "Caffeine Labs",
        "bot": "omnigent-factory",
        "status": "ok",
        "error": None,
        "missing_scopes": [],
        "set_by": "alice",
        "set_at": ok.json()["slack_dm"]["set_at"],
    }
    assert TOKEN not in ok.text

    fid = cockpit.fid
    paths = [
        f"/v1/factory/factories/{fid}",
        f"/v1/factory/factories/{fid}/alerts/routing",
        f"/v1/factory/factories/{fid}/events",
        f"/v1/factory/factories/{fid}/people",
        f"/v1/factory/factories/{fid}/notify/me",
        f"/v1/factory/factories/{fid}/notify/team",
        f"/v1/factory/factories/{fid}/issues",
        "/v1/factory/factories",
    ]
    for user in ("alice", "carol", "vic"):
        for path in paths:
            resp = await _get(cockpit, path, user)
            assert resp.status_code in (200, 403, 404), (path, resp.status_code)
            assert "xoxb-" not in resp.text, path
    routing = await _get(cockpit, paths[1], "vic")
    assert routing.json()["slack_dm"]["configured"] is True
    assert all("xoxb" not in str(e.data) + e.summary for e in cockpit.events())
    [event] = cockpit.events("alerts.routing")
    assert event.summary == "alice (creator) set the Slack app (Caffeine Labs, @omnigent-factory)"

    removed = await _put(cockpit, route, {"token": None})
    assert removed.json()["slack_dm"]["configured"] is False
    assert secrets.bot_token(cockpit.deps, fid) is None


async def test_missing_scopes_are_reported_but_the_token_is_kept(
    cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    fake_slack.scopes = "chat:write,users:read"

    view = await install_bot(cockpit)

    assert view["missing_scopes"] == ["im:write", "users:read.email"]
    assert secrets.bot_token(cockpit.deps, cockpit.fid) == TOKEN


async def test_a_token_of_another_workspace_resets_every_link(
    cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    await install_bot(cockpit)
    link(cockpit, "carol")
    await install_bot(cockpit)  # same workspace: links stay
    assert notify_of(cockpit, "carol")["slack"]["status"] == "verified"

    await install_bot(cockpit, OTHER_TEAM_TOKEN)

    slack = notify_of(cockpit, "carol")["slack"]
    assert slack["status"] == "unlinked" and slack["error"] == "Slack workspace changed"
    assert "user_id" not in slack


async def test_the_owner_test_checks_the_token_and_dms_a_linked_owner(
    cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    route = f"{base(cockpit)}/alerts/slack-bot/test"
    assert error_code(await cockpit.post(route)) == "slack_not_set_up"
    await install_bot(cockpit)

    unlinked = await cockpit.post(route)
    link(cockpit, "alice")
    linked = await cockpit.post(route)
    operator = await cockpit.post(route, user="carol")

    assert unlinked.json()["dm"] == "not_linked" and unlinked.json()["ok"] is True
    assert linked.json() == {
        "ok": True,
        "team": "Caffeine Labs",
        "bot": "omnigent-factory",
        "missing_scopes": [],
        "dm": "sent",
    }
    assert "The Slack app works" in fake_slack.texts_to("U0ALICE")[0]
    assert operator.status_code == 403


# ── A person's own link ────────────────────────────────────────


async def test_link_find_send_code_verify_and_welcome(
    cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    me = f"{base(cockpit)}/notify/me"
    assert error_code(
        await cockpit.post(f"{me}/slack", {"member_id": "U0CAROL"}, user="carol")
    ) == ("slack_not_set_up")
    await install_bot(cockpit)

    found = await cockpit.post(f"{me}/slack", {"email": "carol@slack.example"}, user="carol")
    assert found.status_code == 200, found.text
    assert found.json() == {
        "account": {
            "user_id": "U0CAROL",
            "team_id": "T0CAFE",
            "name": "Carol Operator",
            "handle": "carol",
            "email": "carol@slack.example",
            "tz": "UTC",
        },
        "status": "found",
    }
    assert fake_slack.posts == []  # finding sends nothing

    sent = await cockpit.post(
        f"{me}/slack", {"member_id": "U0CAROL", "send_code": True}, user="carol"
    )
    assert sent.json()["status"] == "pending"
    [code_dm] = fake_slack.texts_to("U0CAROL")
    code = code_dm.split("notifications is ")[1][:6]
    assert "carol (operator) entered your Slack account in factory Atlas" in code_dm
    stored = notify_of(cockpit, "carol")["slack"]
    assert stored["status"] == "pending" and code not in str(stored)

    pending = await _get(cockpit, me, "carol")
    assert pending.json()["slack"]["status"] == "pending"
    assert "verify" not in pending.text and "hash" not in pending.text

    wrong = await cockpit.post(f"{me}/slack/verify", {"code": "000000"}, user="carol")
    assert error_code(wrong) == "wrong_code" and wrong.json()["tries_left"] == 4

    ok = await cockpit.post(f"{me}/slack/verify", {"code": code}, user="carol")
    assert ok.status_code == 200, ok.text
    view = ok.json()
    assert view["slack"]["status"] == "verified" and view["slack"]["user_id"] == "U0CAROL"
    assert view["settings"]["tz"] == "UTC"  # prefilled from Slack
    welcome = fake_slack.texts_to("U0CAROL")[-1]
    assert "Slack DMs are on" in welcome and "You'll get: decisions" in welcome
    assert view["last_dm"]["summary"] == "Slack DM to carol: welcome DM"

    unlinked = await _delete(cockpit, f"{me}/slack", user="carol")
    assert unlinked.json()["slack"]["status"] == "unlinked"


async def test_lookups_refuse_other_workspaces_bots_and_strangers(
    cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    await install_bot(cockpit)
    fake_slack.add_user("U0OUT", "Out Sider", "out@x.example", team="T0OTHER")
    fake_slack.add_user("U0GONE", "Gone Person", "gone@x.example", deleted=True)
    fake_slack.add_user("U0BOTX", "Some Bot", "bot@x.example", is_bot=True)
    route = f"{base(cockpit)}/notify/me/slack"

    cases = {
        "U0OUT": "wrong_workspace",
        "U0GONE": "deactivated",
        "U0BOTX": "is_bot",
        "U0NOBODY": "slack_user_not_found",
    }
    for member, code in cases.items():
        resp = await cockpit.post(route, {"member_id": member}, user="alice")
        assert error_code(resp) == code, member
    assert error_code(await cockpit.post(route, {"member_id": "bad id"})) == "invalid_member_id"
    assert error_code(await cockpit.post(route, {})) == "invalid_body"

    bob = await cockpit.post(route, {"member_id": "U0CAROL"}, user="bob")
    assert bob.status_code == 404  # no access at all
    cockpit.permissions.grant("bob", cockpit.fid, 1)
    off_roster = await cockpit.post(route, {"member_id": "U0CAROL"}, user="bob")
    assert error_code(off_roster) == "not_on_roster"


async def test_codes_expire_burn_after_five_tries_and_lookups_are_rate_limited(
    cockpit: Cockpit, fake_slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    await install_bot(cockpit)
    route = f"{base(cockpit)}/notify/me/slack"
    await cockpit.post(route, {"member_id": "U0VIC", "send_code": True}, user="vic")
    for _ in range(slack_routes.CODE_TRIES):
        await cockpit.post(f"{route}/verify", {"code": "111111"}, user="vic")
    burnt = await cockpit.post(f"{route}/verify", {"code": "111111"}, user="vic")
    assert error_code(burnt) == "code_expired"

    await cockpit.post(route, {"member_id": "U0VIC", "send_code": True}, user="vic")
    code = fake_slack.texts_to("U0VIC")[-1].split("notifications is ")[1][:6]
    real = time.time
    monkeypatch.setattr(slack_routes.time, "time", lambda: real() + slack_routes.CODE_TTL_S + 1)
    expired = await cockpit.post(f"{route}/verify", {"code": code}, user="vic")
    assert error_code(expired) == "code_expired"
    monkeypatch.setattr(slack_routes.time, "time", real)

    for _ in range(slack_routes.LOOKUPS_PER_WINDOW - 2):
        await cockpit.post(route, {"member_id": "U0VIC"}, user="vic")
    limited = await cockpit.post(route, {"member_id": "U0VIC"}, user="vic")
    assert limited.status_code == 429 and error_code(limited) == "rate_limited"
    assert limited.json()["retry_after"] > 0


async def test_settings_are_your_own_and_validated(
    cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    me = f"{base(cockpit)}/notify/me"
    viewer = (await _get(cockpit, me, "vic")).json()
    assert viewer["role"] == "viewer"
    assert viewer["settings"]["events"]["mentions"] is True
    assert viewer["settings"]["events"]["decisions"] is False
    assert viewer["bot"]["configured"] is False

    bad = await _put(cockpit, me, {"tz": "Nowhere/Land"}, user="carol")
    assert bad.status_code == 422
    agent = await cockpit.client.put(
        me,
        json={"tz": "UTC"},
        headers={"X-Test-User": "carol", RUNNER_TUNNEL_TOKEN_HEADER: "rt_x.y"},
    )
    assert agent.status_code == 403

    saved = await _put(
        cockpit,
        me,
        {
            "tz": "Europe/Zagreb",
            "incident_min": "critical",
            "quiet": {"enabled": True, "start": "21:00", "end": "07:30", "critical_through": True},
            "digest": {"enabled": True, "hour": 8, "days": "weekdays"},
        },
        user="carol",
    )
    assert saved.status_code == 200, saved.text
    settings = saved.json()["settings"]
    assert settings["tz"] == "Europe/Zagreb" and settings["incident_min"] == "critical"
    assert settings["quiet"]["start"] == "21:00" and settings["digest"]["days"] == "weekdays"
    assert notify_of(cockpit, "alice") == {}  # nobody else's settings moved


async def test_the_roster_shows_only_link_status_and_owners_see_reachability(
    cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    await install_bot(cockpit)
    link(cockpit, "carol")
    issues_text = (await _get(cockpit, f"{base(cockpit)}/people", "vic")).text

    assert "U0CAROL" not in issues_text and "DU0CAROL" not in issues_text
    people = {
        p["user_id"]: p for p in (await _get(cockpit, f"{base(cockpit)}/people")).json()["data"]
    }
    assert people["carol"]["notify"] == {"slack": "verified", "enabled": True}
    assert people["vic"]["notify"] == {"slack": "unlinked", "enabled": True}

    team = await _get(cockpit, f"{base(cockpit)}/notify/team")
    operator = await _get(cockpit, f"{base(cockpit)}/notify/team", "carol")
    assert operator.status_code == 403
    rows = {r["user_id"]: r for r in team.json()["data"]}
    assert team.json()["reachable"] == 1 and team.json()["count"] == 3
    assert rows["carol"]["reachable"] and rows["carol"]["role"] == "operator"
    assert rows["alice"]["role"] == "owner" and not rows["alice"]["reachable"]
    assert "U0CAROL" not in team.text


async def test_test_dm_needs_a_link_and_reports_person_errors(
    cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    await install_bot(cockpit)
    route = f"{base(cockpit)}/notify/me/test"
    assert error_code(await cockpit.post(route, user="carol")) == "not_linked"
    link(cockpit, "carol", tz="Europe/Zagreb", quiet={"start": "22:00", "end": "08:00"})

    ok = await cockpit.post(route, user="carol")
    assert ok.json() == {"dm": "sent"}
    assert "Quiet 22:00-08:00 Europe/Zagreb" in fake_slack.texts_to("U0CAROL")[-1]

    fake_slack.fail_next("chat.postMessage", error="messages_tab_disabled")
    failed = await cockpit.post(route, user="carol")
    assert failed.status_code == 502 and failed.json()["slack_error"] == "messages_tab_disabled"
    slack = notify_of(cockpit, "carol")["slack"]
    assert slack["status"] == "error" and slack["error"] == "messages_tab_disabled"


async def test_a_dead_token_found_by_a_route_raises_the_incident(
    cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    await install_bot(cockpit)
    link(cockpit, "carol")
    fake_slack.tokens.pop(TOKEN)  # revoked in Slack

    resp = await cockpit.post(f"{base(cockpit)}/notify/me/test", user="carol")

    assert resp.status_code == 502
    meta = secrets.bot_meta(cockpit.deps, cockpit.fid)
    assert meta is not None and meta["status"] == "invalid" and meta["error"] == "invalid_auth"
    incident = issues.open_issue_for(cockpit.deps, cockpit.fid, "slack.bot", "factory")
    assert incident is not None and incident.assignee == "owners"

    fake_slack.tokens[TOKEN] = "T0CAFE"
    await install_bot(cockpit)
    assert issues.open_issue_for(cockpit.deps, cockpit.fid, "slack.bot", "factory") is None
