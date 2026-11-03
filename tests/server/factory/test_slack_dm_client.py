"""The Slack Web API client against a fake Slack: answers, error classes, secrecy."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from omnigent.server.factory.slackdm.client import SlackClient, SlackError, classify
from tests.server.factory.slack_fake import TOKEN, FakeSlack, installed


@pytest.fixture()
def fake_slack() -> Iterator[FakeSlack]:
    with installed() as fake:
        yield fake


async def test_auth_test_reads_the_team_bot_and_granted_scopes(fake_slack: FakeSlack) -> None:
    info = await SlackClient(TOKEN).auth_test()

    assert (info.team_id, info.team, info.bot_user_id, info.user) == (
        "T0SAMPLE",
        "Sample Org",
        "UBOT",
        "omnigent-factory",
    )
    assert info.scopes == ["chat:write", "im:write", "users:read", "users:read.email"]
    assert fake_slack.auth_headers == [f"Bearer {TOKEN}"]


async def test_users_resolve_by_member_id_and_email(fake_slack: FakeSlack) -> None:
    client = SlackClient(TOKEN)

    by_id = await client.users_info("U0CAROL")
    by_email = await client.lookup_by_email("alice@slack.example")

    assert (by_id.id, by_id.name, by_id.handle, by_id.email, by_id.tz) == (
        "U0CAROL",
        "Carol Operator",
        "carol",
        "carol@slack.example",
        "UTC",
    )
    assert by_email.id == "U0ALICE" and by_email.team_id == "T0SAMPLE"


async def test_a_dm_is_never_parsed_or_unfurled(fake_slack: FakeSlack) -> None:
    client = SlackClient(TOKEN)
    channel = await client.open_dm("U0CAROL")

    ts = await client.post_message(channel, "hello", [{"type": "section"}])

    assert channel == "DU0CAROL" and ts
    [post] = fake_slack.posts
    assert post["parse"] == "none"
    assert post["unfurl_links"] is False and post["unfurl_media"] is False
    assert post["text"] == "hello" and post["blocks"] == [{"type": "section"}]


async def test_rate_limit_429_carries_retry_after(fake_slack: FakeSlack) -> None:
    fake_slack.fail_next("chat.postMessage", status=429, retry_after=42)

    with pytest.raises(SlackError) as caught:
        await SlackClient(TOKEN).post_message("DU0CAROL", "hi")

    assert caught.value.code == "ratelimited"
    assert caught.value.kind == "retryable"
    assert caught.value.retry_after == 42


@pytest.mark.parametrize(
    ("status", "error", "network", "code", "kind"),
    [
        (200, "invalid_auth", False, "invalid_auth", "bot"),
        (200, "token_revoked", False, "token_revoked", "bot"),
        (200, "user_not_found", False, "user_not_found", "person"),
        (200, "messages_tab_disabled", False, "messages_tab_disabled", "person"),
        (200, "msg_too_long", False, "msg_too_long", "message"),
        (200, "ratelimited", False, "ratelimited", "retryable"),
        (503, None, False, "http_error", "retryable"),
        (200, None, True, "network_error", "retryable"),
    ],
)
async def test_every_failure_is_classified(
    fake_slack: FakeSlack,
    status: int,
    error: str | None,
    network: bool,
    code: str,
    kind: str,
) -> None:
    fake_slack.fail_next("chat.postMessage", status=status, error=error, raise_network=network)

    with pytest.raises(SlackError) as caught:
        await SlackClient(TOKEN).post_message("DU0CAROL", "hi")

    assert (caught.value.code, caught.value.kind) == (code, kind)


async def test_an_unknown_token_is_invalid_auth(fake_slack: FakeSlack) -> None:
    with pytest.raises(SlackError) as caught:
        await SlackClient("-".join(["xoxb", "0000000000", "0000000000000", "nope"])).auth_test()

    assert caught.value.kind == "bot"


async def test_logs_name_the_method_and_code_never_the_token_or_text(
    fake_slack: FakeSlack, caplog: pytest.LogCaptureFixture
) -> None:
    fake_slack.fail_next("chat.postMessage", error="invalid_blocks")
    fake_slack.fail_next("chat.postMessage", raise_network=True)
    client = SlackClient(TOKEN)
    with caplog.at_level(logging.DEBUG, logger="omnigent.server.factory.slackdm"):
        for _ in range(2):
            with pytest.raises(SlackError):
                await client.post_message("DU0CAROL", "secret agent text <!channel>")

    logged = caplog.text
    assert "chat.postMessage" in logged and "invalid_blocks" in logged
    assert TOKEN not in logged and "xoxb-" not in logged
    assert "secret agent text" not in logged


def test_classify_falls_back_to_other() -> None:
    assert classify("something_new") == "other"
