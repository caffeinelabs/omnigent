"""The Slack daily digest tells the story the cockpit's Now card tells.

The digest's headline, Waiting on you, In progress, Spend and Next are built
from the card's own builder for the same person and snapshot: these tests
read the card through its route and check the digest says exactly that,
for a creator who reads everything and for a reader who may not see a
project, and that a ready PR waits on its project's human owner in both.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import snapshot
from omnigent.server.factory.slackdm import digest as digest_mod
from omnigent.server.factory.slackdm import engine as engine_mod
from omnigent.server.factory.status_summary import spend_text, utc_day
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.slack_fake import FakeSlack, installed
from tests.server.factory.slack_helpers import base, install_bot, link, slack_cockpit

DOMAIN = "omnigent.test"
BASE = f"https://{DOMAIN}"


@pytest.fixture()
def fake_slack() -> Iterator[FakeSlack]:
    with installed() as fake:
        yield fake


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_slack: FakeSlack,
) -> AsyncIterator[Cockpit]:
    monkeypatch.setenv("OMNIGENT_DOMAIN", DOMAIN)
    async for value in slack_cockpit(world, db_uri, monkeypatch, tmp_path):
        await install_bot(value)
        yield value


def _seed(cockpit: Cockpit, *, owner: str | None = None) -> None:
    """
    api-version (its PL is live) waits only on the merge of PR #9826; billing
    is queued; Atlas asks the operators something; $12.50 spent today.
    """
    store = cockpit.store
    charter = {"human_owner": owner} if owner else None
    store.upsert_project(cockpit.fid, "api-version", title="API version", charter=charter)
    store.upsert_project(cockpit.fid, "billing", title="Billing")
    store.upsert_pr(
        cockpit.fid,
        "caffeinelabs/app",
        9826,
        title="GET /version",
        project_slug="api-version",
        ready=True,
        ready_at=int(time.time()) - 3600,
        head_sha="a" * 40,
    )
    store.create_decision(
        cockpit.fid,
        dedupe_key="atlas-q",
        question="Start the docs project now?",
        asked_by_session_id=cockpit.fid,
        assignee="operators",
        blocking=False,
    )
    store.add_cost_daily(
        cockpit.fid,
        project_slug="",
        role="lead",
        agent_name="Atlas",
        model="opus",
        day_utc=utc_day(time.time()),
        cost_usd=12.5,
    )


async def _card(cockpit: Cockpit, user: str) -> dict[str, Any]:
    """The Now card's JSON for *user*, as the cockpit reads it."""
    snapshot.reset_cache()
    resp = await cockpit.client.get(
        f"{base(cockpit)}/status-summary", headers={"X-Test-User": user}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _digest(
    cockpit: Cockpit, user: str, now: float
) -> tuple[digest_mod.Digest | None, dict[str, Any] | None, Any]:
    """The digest *user* would get now, the summary it was built from, and their person row."""
    root = cockpit.world.store.get_conversation(cockpit.fid)
    ctx = await engine_mod.factory_ctx(cockpit.app.state, root)
    person = ctx.people[user]
    raw = await snapshot.get_snapshot(cockpit.app.state, root)
    vis = await digest_mod.visibility_for(cockpit.deps, raw, user)
    built = digest_mod.build_digest(cockpit.deps, ctx, person, vis, raw, now, BASE)
    summary = digest_mod.summary_for(cockpit.deps, ctx, person, vis, raw, now)
    return built, summary, person


def _text(built: digest_mod.Digest) -> str:
    return "\n".join(b["text"]["text"] for b in built.blocks if b["type"] == "section")


def _section(text: str, header: str) -> list[str]:
    """The bullet lines under ``*header (n)*`` in a digest."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"*{header}"))
    out = []
    for line in lines[start + 1 :]:
        if line.startswith("*"):
            break
        out.append(line)
    return out


def _same(summary: dict[str, Any] | None, card: dict[str, Any]) -> None:
    assert summary is not None
    assert {**summary, "generated_at": 0} == {**card, "generated_at": 0}


async def test_the_digest_says_what_the_now_card_says(cockpit: Cockpit) -> None:
    _seed(cockpit)
    link(cockpit, "alice")

    card = await _card(cockpit, "alice")
    now = time.time()
    built, summary, person = await _digest(cockpit, "alice", now)

    _same(summary, card)
    assert built is not None
    text = _text(built)
    # The card's headline, word for word.
    assert card["headline"] == "2 things wait on you · nothing in progress yet · 1 project queued"
    assert card["headline"] in text.splitlines()
    # Waiting on you: the card's items, in its order, each linked.
    mine = [item for item in card["waiting"] if item["for_you"]]
    assert [item["ref"] for item in mine] == ["PR #9826", "D-1"]  # the longest wait first
    assert _section(text, "Waiting on you (2)") == [
        digest_mod.waiting_line(item, person, now, BASE) for item in mine
    ]
    assert built.waiting == card["counts"]["waiting_for_you"] == 2
    pr_line = _section(text, "Waiting on you (2)")[0]
    assert pr_line.startswith(f"• <{BASE}{mine[0]['href']}|PR #9826> review and merge")
    assert "api-version: GET /version · waiting 1 h" in pr_line
    # api-version only waits on that merge: it is not also "in progress".
    assert card["working"] == [] and "*In progress" not in text
    # Spend and Next say what the card says.
    spend = next(line for line in text.splitlines() if line.startswith("*Spend*"))
    assert spend.startswith(f"*Spend*  {spend_text(card['spend'])}")
    assert "$12.50 in the last 24 h" in spend
    nxt = next(line for line in text.splitlines() if line.startswith("*Next*"))
    assert nxt == f"*Next*  {digest_mod.next_line(card, person, now, BASE)}"
    [queued] = card["next"]
    assert queued["project"] == "billing" and queued["reason"] in nxt


async def test_a_readers_digest_hides_what_their_card_hides(cockpit: Cockpit) -> None:
    _seed(cockpit)
    cockpit.store.upsert_person(cockpit.fid, "bob", role="viewer", invited_by="alice")
    cockpit.permissions.grant("bob", cockpit.fid, LEVEL_READ)  # the root only, not the PL

    card = await _card(cockpit, "bob")
    now = time.time()
    built, summary, person = await _digest(cockpit, "bob", now)

    _same(summary, card)
    # api-version's PL is hidden from him: neither says a word about it or its PR.
    assert [item["ref"] for item in card["waiting"]] == ["D-1"]
    assert card["waiting"][0]["for_you"] is False
    assert built is None  # nothing waits on him and nothing he may read happened
    lines = digest_mod.waiting_lines(summary, person, now, BASE)
    assert (lines.count, lines.lines) == (0, [])
    assert "api-version" not in str(summary) and "9826" not in str(summary)


async def test_a_ready_pr_waits_on_its_projects_owner_in_both(cockpit: Cockpit) -> None:
    _seed(cockpit, owner="carol")
    link(cockpit, "alice")
    link(cockpit, "carol")

    alice_card = await _card(cockpit, "alice")
    carol_card = await _card(cockpit, "carol")
    now = time.time()
    alice, alice_summary, _ = await _digest(cockpit, "alice", now)
    carol, carol_summary, carol_person = await _digest(cockpit, "carol", now)

    _same(alice_summary, alice_card)
    _same(carol_summary, carol_card)
    pr = next(item for item in alice_card["waiting"] if item["kind"] == "pr")
    assert (pr["who"], pr["for_you"]) == ("carol", False)
    mine = next(item for item in carol_card["waiting"] if item["kind"] == "pr")
    assert (mine["who"], mine["for_you"]) == ("you", True)
    assert alice is not None and "PR #9826" not in _text(alice)
    assert carol is not None
    assert digest_mod.waiting_line(mine, carol_person, now, BASE) in _text(carol)


async def test_the_welcome_dm_lists_the_cards_waiting_items(cockpit: Cockpit) -> None:
    _seed(cockpit)
    root = cockpit.world.store.get_conversation(cockpit.fid)
    ctx = await engine_mod.factory_ctx(cockpit.app.state, root)
    raw = await snapshot.get_snapshot(cockpit.app.state, root)
    now = time.time()

    waiting = await digest_mod.waiting_async(
        cockpit.deps, ctx, ctx.people["carol"], raw, now, BASE
    )

    card = await _card(cockpit, "carol")
    mine = [item for item in card["waiting"] if item["for_you"]]
    assert waiting.count == len(mine) == 2
    assert waiting.lines == [
        digest_mod.waiting_line(item, ctx.people["carol"], now, BASE) for item in mine
    ]
