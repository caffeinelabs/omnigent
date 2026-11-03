"""The Slack daily digest tells the story the cockpit's Now card tells.

The digest's headline, Waiting on you, In progress, Spend and Next are built
from the card's own builder for the same person and snapshot: these tests
read the card through its route and check the digest says exactly that,
for a creator who reads everything and for a reader who may not see a
project, and that a ready PR waits on its project's human owner in both.
"""

from __future__ import annotations

import dataclasses
import re
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.auth import LEVEL_OWNER, LEVEL_READ
from omnigent.server.factory import snapshot
from omnigent.server.factory.slackdm import digest as digest_mod
from omnigent.server.factory.slackdm import engine as engine_mod
from omnigent.server.factory.slackdm import prefs as prefs_mod
from omnigent.server.factory.slackdm import render
from omnigent.server.factory.slackdm.render import SECTION_MAX
from omnigent.server.factory.status_summary import spend_text, utc_day
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.slack_fake import FakeSlack, installed
from tests.server.factory.slack_helpers import base, install_bot, link, slack_cockpit

DOMAIN = "omnigent.test"
BASE = f"https://{DOMAIN}"
_LINK = re.compile(r"<[^<>|]+\|([^<>]*)>")


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
        "sample-org/sample-app",
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
    # The card's headline, word for word (the verdict line is only for the new cockpit).
    assert card["headline"] == "2 things wait on you · nothing in progress yet · 1 project queued"
    assert card["headline"] in text.splitlines()
    assert f"*{card['verdict']['sentence']}*" not in text.splitlines()
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


async def test_someone_on_the_new_cockpit_gets_its_verdict_above_the_headline(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(cockpit)
    link(cockpit, "alice")
    card = await _card(cockpit, "alice")
    now = time.time()
    root = cockpit.world.store.get_conversation(cockpit.fid)
    ctx = await engine_mod.factory_ctx(cockpit.app.state, root)
    raw = await snapshot.get_snapshot(cockpit.app.state, root)
    vis = await digest_mod.visibility_for(cockpit.deps, raw, "alice")
    person = ctx.people["alice"]
    chose = dataclasses.replace(
        person, prefs=person.prefs.model_copy(update={"ui": prefs_mod.UiPrefs(cockpit="v2")})
    )
    verdict = f"*{card['verdict']['sentence']}*"
    # The ready PR and the question: the two rows of Needs you.
    assert card["verdict"]["sentence"] == "Running, 2 things need you"

    def lines(who: Any) -> list[str]:
        built = digest_mod.build_digest(cockpit.deps, ctx, who, vis, raw, now, BASE)
        assert built is not None
        return _text(built).splitlines()

    # The server does not offer the new cockpit: nobody's digest changes.
    monkeypatch.delenv(F.COCKPIT_V2_ENV, raising=False)
    assert verdict not in lines(chose)
    # Offered: only the person who switched gets the top bar's sentence, right above the headline.
    monkeypatch.setenv(F.COCKPIT_V2_ENV, "1")
    assert verdict not in lines(person)
    mine = lines(chose)
    assert mine.index(verdict) == mine.index(card["headline"]) - 1


async def test_the_new_cockpits_digest_has_learned_this_week_while_discovery_runs(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The discovery section is Home's "Learned this week": ids and titles, each linked."""
    from omnigent.server.factory import definition
    from omnigent.server.factory.definition_blocks import DISCOVERY_ENV
    from tests.server.factory.discovery_helpers import ingest, label, make_opportunity, turn_on

    _seed(cockpit)
    link(cockpit, "alice")
    monkeypatch.setenv(F.COCKPIT_V2_ENV, "1")
    root = cockpit.world.store.get_conversation(cockpit.fid)
    ctx = await engine_mod.factory_ctx(cockpit.app.state, root)
    raw = await snapshot.get_snapshot(cockpit.app.state, root)
    vis = await digest_mod.visibility_for(cockpit.deps, raw, "alice")
    person = ctx.people["alice"]
    chose = dataclasses.replace(
        person, prefs=person.prefs.model_copy(update={"ui": prefs_mod.UiPrefs(cockpit="v2")})
    )

    def lines(who: Any) -> list[str]:
        built = digest_mod.build_digest(cockpit.deps, ctx, who, vis, raw, time.time(), BASE)
        assert built is not None
        return _text(built).splitlines()

    # Discovery off: no section.
    assert "*Learned this week*" not in lines(chose)
    restore = definition.install(cockpit.app.state)
    monkeypatch.setenv(DISCOVERY_ENV, "1")
    try:
        turn_on(cockpit)
        signals = ingest(
            cockpit, "repo-issues", [f"I need a draft link {i} (sample)" for i in range(3)]
        )
        label(cockpit, signals, "workaround")
        make_opportunity(cockpit, signals)
        mine = lines(chose)
        assert "*Learned this week*" not in lines(person)
    finally:
        restore()
    section = _section("\n".join(mine), "Learned this week")
    assert section == [
        "• New opportunity O-1: Show a draft before it goes live (sample) · "
        f"<{BASE}/factory/{cockpit.fid}/discovery?peek=O-1|O-1>"
    ]


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


def _working(cockpit: Cockpit, slug: str, pr: int, task: str) -> None:
    """*slug* in Building: its PL is live, PR #*pr* open (not ready), *task* running."""
    world = cockpit.world
    pl = world.make_session(
        f"pl-{slug}",
        agent="pl",
        title=f"PL · {slug}",
        labels={
            F.LABEL_ID: cockpit.fid,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: slug,
            F.LABEL_PARENT: cockpit.fid,
            F.LABEL_SUPERVISOR: world.id("lead_b"),
            F.LABEL_STATE: "active",
        },
    )
    cockpit.permissions.grant("alice", pl.id, LEVEL_OWNER)
    store = cockpit.store
    store.upsert_project(cockpit.fid, slug, title=slug, pl_session_id=pl.id)
    store.upsert_pr(cockpit.fid, "sample-org/sample-app", pr, title="Build it", project_slug=slug)
    store.upsert_task(cockpit.fid, slug, task, title=task, state="running")


def _unlinked(text: str) -> str:
    """*text* with each mrkdwn ``<url|label>`` replaced by its label."""
    return _LINK.sub(lambda m: m.group(1), text)


async def test_in_progress_lists_what_the_card_lists_under_working_on(cockpit: Cockpit) -> None:
    _seed(cockpit)
    _working(cockpit, "search", 9901, "Index the docs")
    link(cockpit, "alice")

    card = await _card(cockpit, "alice")
    built, summary, _ = await _digest(cockpit, "alice", time.time())

    _same(summary, card)
    assert built is not None
    [work] = card["working"]
    assert work["project"] == "search"
    assert "working on T-1: Index the docs" in work["status"]
    lines = _section(_text(built), "In progress (1)")
    assert lines == digest_mod.progress_lines(card, BASE)
    assert lines[0].startswith(f"• <{BASE}{work['href']}|search> (Building) — PR #9901")


async def test_atlas_is_quoted_word_for_word_with_the_prs_it_names_linked(
    cockpit: Cockpit,
) -> None:
    _seed(cockpit)
    store = cockpit.store
    for number in (9827, 9828):
        store.upsert_pr(cockpit.fid, "sample-org/sample-app", number, title=f"PR {number}")
    note = "3 PRs ready · #9826 #9827 #9828 verified, waiting for <@here> & a merge"
    cockpit.world.store.set_labels(cockpit.fid, {F.LABEL_SUMMARY: note})
    link(cockpit, "alice")

    card = await _card(cockpit, "alice")
    built, _, _ = await _digest(cockpit, "alice", time.time())

    lead = card["lead"]
    assert [p["text"] for p in lead["parts"] if p.get("href")] == ["#9826", "#9827", "#9828"]
    assert built is not None
    said = next(line for line in _text(built).splitlines() if line.startswith("*Atlas said*"))
    quote = said.removeprefix("*Atlas said*  ")
    assert quote == digest_mod.lead_line(card, BASE)
    # Linked ids keep the spaces around them; the rest is escaped, not dropped.
    unescaped = _unlinked(quote).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    assert unescaped == f"“{lead['text']}”"
    assert "<@here>" not in quote


async def test_a_busy_factorys_digest_keeps_every_heading_and_link_whole(
    cockpit: Cockpit,
) -> None:
    """8 waits, 7 in progress and 14 queued with real-length names: nothing is cut."""
    store, now = cockpit.store, time.time()
    for i in range(8):
        slug = f"validate-build-tier-{i}"
        store.upsert_project(cockpit.fid, slug, title=slug)
        store.upsert_pr(
            cockpit.fid,
            "sample-org/sample-app",
            9800 + i,
            title=f"Validate the build tier on a preview environment, part {i} " + "x" * 60,
            project_slug=slug,
            ready=True,
            ready_at=int(now) - 3600 * (i + 1),
            head_sha="a" * 40,
        )
    for i in range(6):
        _working(cockpit, f"preview-build-pipeline-{i}", 9900 + i, "Wire it " + "y" * 60)
    for i in range(6):
        store.upsert_project(cockpit.fid, f"queued-follow-up-project-{i}", title=f"Q{i}")
    store.add_cost_daily(
        cockpit.fid,
        project_slug="",
        role="lead",
        agent_name="Atlas",
        model="opus",
        day_utc=utc_day(now),
        cost_usd=12.5,
    )
    cockpit.world.store.set_labels(
        cockpit.fid, {F.LABEL_SUMMARY: "8 PRs ready · #9800 #9801 #9802 #9803 to merge first"}
    )
    link(cockpit, "alice")

    built, summary, _ = await _digest(cockpit, "alice", now)

    assert built is not None and summary is not None
    sections = [b["text"]["text"] for b in built.blocks if b["type"] == "section"]
    assert sum(len(text) for text in sections) > SECTION_MAX  # one section would be cut
    for text in sections:
        assert len(text) <= SECTION_MAX
        assert text.count("<") == text.count(">")
        assert not text.endswith("…")
    text = _text(built)
    lines = text.splitlines()
    for heading in ("*Waiting on you (8)*", "*In progress (", "*Last 24 h*", "*Spend*", "*Next*"):
        assert any(line.startswith(heading) for line in lines), heading
    assert any(line.startswith("*Atlas said*") for line in lines)
    assert len(_section(text, "Waiting on you (8)")) == 8
    progress = _section(text, "In progress")
    assert progress == digest_mod.progress_lines(summary, BASE)
    assert progress[-1].startswith("…and ")


def test_long_messages_split_between_lines_and_never_inside_a_link() -> None:
    url = "https://omnigent.test/factory/f/projects/" + "p" * 60
    lines = [f"• {render.link(url, f'project-{i}')} — " + "z" * 200 for i in range(40)]

    blocks = render.message(lines[:1], "settings", groups=(lines[1:],))

    texts = [b["text"]["text"] for b in blocks if b["type"] == "section"]
    assert len(texts) > 1 and all(len(t) <= SECTION_MAX for t in texts)
    assert [line for t in texts for line in t.splitlines()] == lines  # every line, whole
    assert blocks[-1]["type"] == "context"
    # One line longer than a section is cut before a link it would break.
    long = "a" * (SECTION_MAX - 10) + render.link(url, "the end")
    assert render.cut(long, SECTION_MAX) == "a" * (SECTION_MAX - 10) + "…"
    # The plain-text fallback keeps whole lines too.
    flat = render.plain(render.message(lines), "fallback")
    assert len(flat) <= render.PLAIN_MAX and flat.endswith("\n…")
    assert flat.count("<") == flat.count(">")
