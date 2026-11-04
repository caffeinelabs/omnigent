"""
The server's merger with auto-merge per subtree, end to end over a fake GitHub.

No factory-wide target: the owner turned auto-merge on for one outcome
(``I-1``) into its tryout base (``tryout-base``). The PR poller marks PRs ready
as in production; the merger merges the outcome's ready, green PRs (also of
a project filed under it later) and names the scope it used, while a PR of a
project outside it, into ``main``, waits for a person. Sample data only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import chat_cards, definition, live, nudge, product_profile, status
from omnigent.server.factory.background.auto_merger import (
    EVENT_KIND,
    MERGER_STATE_ATTR,
    AutoMerger,
)
from omnigent.server.factory.background.pr_poller import PrPoller
from omnigent.server.factory.mailer import MAILER_STATE_ATTR, Mailer
from tests.server.factory.conftest import FactoryWorld, FakeClock, FakeDeliver
from tests.server.factory.github_fakes import FakeConnection, FakeGitHub, FakeGithubStore
from tests.server.factory.test_auto_merge import POLL_S, REPO, Merging

TRYOUT = "tryout-base"
SCOPES = "product.delivery.auto_merge_scopes"


@pytest.fixture(autouse=True)
def _quiet_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live, "announce_factory_changed", lambda *_a, **_k: None)
    status.reset()
    nudge.reset()


@pytest_asyncio.fixture()
async def merging(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Merging]:
    monkeypatch.delenv(product_profile.DELIVERY_SEED_ENV, raising=False)
    restore = definition.install(world.app_state)
    store = world.deps.factory_store
    fid = world.id("lead_a")
    outcome = store.create_initiative(
        fid, title="Agent helper (sample)", ask="An in-app helper", plan={"projects": []}
    )
    store.upsert_project(
        fid, "api-version", title="API version", repo=REPO, base_branch=TRYOUT,
        pl_session_id=world.id("pl"), feature=outcome.ref,
    )  # fmt: skip
    store.upsert_project(fid, "api-status", title="API status", repo=REPO, base_branch="main")
    scope = [{"ref": outcome.ref, "repo": REPO, "base": TRYOUT}]
    definition.set_values(world.deps, fid, {SCOPES: scope}, created_by="alice")
    gh = FakeGitHub(allow_merge=True)
    clock = FakeClock()
    state = world.app_state
    state.github_client = gh.client()
    state.github_store = FakeGithubStore({"local": FakeConnection()})
    mailer = Mailer(state, deliver=fake_deliver, sleep=no_sleep)
    setattr(state, MAILER_STATE_ATTR, mailer)
    poller = PrPoller(state, clock=clock, poll_s=POLL_S)
    merger = AutoMerger(state, clock=clock, interval_s=POLL_S)
    setattr(state, MERGER_STATE_ATTR, merger)
    unmirror = chat_cards.register()
    try:
        yield Merging(world, gh, poller, merger, clock, fake_deliver, mailer)
    finally:
        setattr(state, MERGER_STATE_ATTR, None)
        await mailer.close()
        restore()
        if unmirror is not None:
            unmirror()


async def test_the_outcome_merges_itself_and_main_waits_for_a_person(merging: Merging) -> None:
    m = merging
    m.new_pr(9630, base=TRYOUT)
    m.new_pr(9640, slug="api-status", base="main")
    await m.poll()
    assert m.pr(9630).ready is True and m.pr(9640).ready is True
    views = {v["number"]: v for v in m.pr_views()}
    assert views[9630]["auto_merge"] is True and views[9640]["auto_merge"] is False

    merged = await m.merger.run_once()

    assert merged == [m.pr(9630).id]
    assert [c["number"] for c in m.merges()] == [9630]
    [auto] = m.events(EVENT_KIND)
    assert auto.data["scope"] == "I-1" and auto.data["target"] == {"repo": REPO, "base": TRYOUT}
    assert "auto-merge for I-1 and everything under it" in auto.summary
    # main is outside the outcome: a person merges it, as before.
    assert [a["kind"] for a in m.asks()["asks"]] == ["merge"]
    assert await m.merger.run_once() == []


async def test_a_project_filed_under_the_outcome_later_is_covered(merging: Merging) -> None:
    m = merging
    m.store.upsert_project(
        m.fid, "api-docs", title="API docs", repo=m.store.get_project(m.fid, "api-version").repo,
        base_branch=TRYOUT, feature="I-1",
    )  # fmt: skip
    m.new_pr(9650, slug="api-docs", base=TRYOUT)
    await m.poll()

    assert await m.merger.run_once() == [m.pr(9650).id]


async def test_turning_the_scope_off_stops_the_merges(merging: Merging) -> None:
    m = merging
    m.new_pr(9630, base=TRYOUT)
    await m.poll()
    definition.set_values(
        m.world.deps, m.fid, {SCOPES: [{"ref": "I-1", "merge": False}]}, created_by="alice"
    )

    assert await m.merger.run_once() == []
    assert m.merges() == []
    assert m.merger.waiting == {}
