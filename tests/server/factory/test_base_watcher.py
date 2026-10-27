"""The base watcher: the PR poller reads base heads and acts once per move.

GitHub is the read-only fake behind the real client (``github_fakes``); a
move of ``temp-main`` must emit one ``base.moved``, mail the integration PL to
redeploy and every work PL with an open PR on that base to sync, survive a
server restart (the head is recorded on the root) and run the hooks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.factory import base, live, nudge, status
from omnigent.server.factory.background.pr_poller import BASE_WATCH_INTERVAL_S, PrPoller
from omnigent.server.factory.charter import normalize_charter
from omnigent.server.factory.mailer import MAILER_STATE_ATTR, Mailer
from omnigent.server.factory.prs import refresh_query
from tests.server.factory.conftest import FactoryWorld, FakeClock, FakeDeliver
from tests.server.factory.github_fakes import (
    FakeConnection,
    FakeGitHub,
    FakeGithubStore,
    FakePr,
    iso,
    sha,
)

REPO = "caffeinelabs/app"
BASE = "temp-main"
POLL_S = 60.0


@dataclass
class Watching:
    world: FactoryWorld
    gh: FakeGitHub
    poller: PrPoller
    clock: FakeClock
    deliver: FakeDeliver
    mailer: Mailer

    @property
    def store(self) -> Any:
        return self.world.deps.factory_store

    @property
    def fid(self) -> str:
        return self.world.id("lead_a")

    async def poll(self, advance: float = POLL_S + 1, poller: PrPoller | None = None) -> None:
        self.clock.advance(advance)
        await (poller or self.poller).run_once()
        await self.mailer.drain()

    def moves(self) -> list[Any]:
        found = self.store.list_events(self.fid, limit=100, kinds=["base.moved"])
        return list(reversed(found))

    def texts(self, key: str, needle: str = "") -> list[str]:
        return [t for t in self.deliver.texts_to(self.world.id(key)) if needle in t]

    def head(self) -> base.BaseHead | None:
        return base.read_head(self.world.get("lead_a").labels, REPO, BASE)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live, "announce_factory_changed", lambda *_a, **_k: None)
    status.reset()
    nudge.reset()
    base.reset()


def _pl(world: FactoryWorld, key: str, slug: str) -> str:
    fid = world.id("lead_a")
    conv = world.make_session(
        key,
        agent="pl",
        title=f"PL · {slug}",
        labels={
            F.LABEL_ID: fid,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: slug,
            F.LABEL_PARENT: fid,
            F.LABEL_STATE: "active",
        },
    )
    return conv.id


@pytest_asyncio.fixture()
async def watching(
    world: FactoryWorld, fake_deliver: FakeDeliver, no_sleep: Callable[[float], Any]
) -> AsyncIterator[Watching]:
    store = world.deps.factory_store
    fid = world.id("lead_a")
    world.store.set_labels(fid, {F.LABEL_DEFAULT_REPO: REPO, F.LABEL_DEFAULT_BASE: BASE})
    world.sessions["lead_a"] = world.get("lead_a")
    store.upsert_project(
        fid,
        "api-version",
        title="API version",
        repo=REPO,
        base_branch=BASE,
        pl_session_id=world.id("pl"),
    )
    store.upsert_project(
        fid,
        "api-status",
        title="API status",
        repo=REPO,
        base_branch=BASE,
        pl_session_id=_pl(world, "pl_status", "api-status"),
    )
    store.upsert_project(
        fid,
        "infra-docs",
        title="Infra docs",
        repo="caffeinelabs/infra",
        base_branch="main",
        pl_session_id=_pl(world, "pl_infra", "infra-docs"),
    )
    store.upsert_project(
        fid,
        F.INTEGRATION_ENV_SLUG,
        title=f"Base env · {BASE}",
        repo=REPO,
        base_branch=BASE,
        charter=normalize_charter({"kind": "integration", "goal": "keep it live"}),
        pl_session_id=_pl(world, "pl_env", F.INTEGRATION_ENV_SLUG),
    )
    gh = FakeGitHub()
    gh.branches[(REPO, BASE)] = sha("a")
    gh.branches[("caffeinelabs/infra", "main")] = sha("i")
    clock = FakeClock()
    state = world.app_state
    state.github_client = gh.client()
    state.github_store = FakeGithubStore({"local": FakeConnection()})
    mailer = Mailer(state, deliver=fake_deliver, sleep=no_sleep)
    setattr(state, MAILER_STATE_ATTR, mailer)
    poller = PrPoller(state, clock=clock, poll_s=POLL_S)
    yield Watching(world, gh, poller, clock, fake_deliver, mailer)
    await mailer.close()


def _open_pr(w: Watching, number: int = 9640) -> FakePr:
    created = w.clock.now + 5
    pr = FakePr(REPO, number, "factory/api-version/x", sha(f"h{number}"), created, base_ref=BASE)
    pr.commits.append((pr.head_sha, iso(created - 1)))
    return w.gh.add(pr)


def test_the_refresh_query_reads_base_heads_after_the_prs() -> None:
    query = refresh_query([9630], ["temp-main", "release/v1"])
    assert 'base0: ref(qualifiedName: "refs/heads/temp-main") { target { oid } }' in query
    assert 'base1: ref(qualifiedName: "refs/heads/release/v1") { target { oid } }' in query
    assert query.index("pr9630:") < query.index("base0:")
    assert "pullRequest(" not in refresh_query([], ["temp-main"])


async def test_a_move_is_announced_once_to_the_right_pls(watching: Watching) -> None:
    w = watching
    _open_pr(w)

    await w.poll()
    # The first sighting is the baseline: recorded, not announced.
    assert w.head() == base.BaseHead(sha("a"), None)
    assert w.moves() == []
    assert w.texts("pl_env") == [] and w.texts("pl", "base") == []
    assert len(w.gh.base_queries) >= 1

    w.gh.branches[(REPO, BASE)] = sha("b")
    await w.poll()
    [move] = w.moves()
    assert move.subject == f"base:{REPO}#{BASE}"
    assert move.summary == f"base {BASE} moved {sha('a')[:7]} → {sha('b')[:7]}"
    assert move.data["from"] == sha("a") and move.data["to"] == sha("b")
    assert move.data["repo"] == REPO and move.data["base"] == BASE
    assert w.head() == base.BaseHead(sha("b"), int(w.clock.now))
    [redeploy] = w.texts("pl_env")
    assert redeploy.startswith(
        f"[Factory notice · base {BASE} moved {sha('a')[:7]} → {sha('b')[:7]}]"
    )
    [sync] = w.texts("pl", "base")
    assert sync.startswith(
        f"[Factory notice · base {BASE} moved to {sha('b')[:7]} · sync open branches: "
        f"git merge origin/{BASE}]"
    )
    # No open PR on the base, another repo, or the lead: nothing.
    assert w.texts("pl_status") == [] and w.texts("pl_infra") == []
    assert w.texts("lead_a", "base") == []
    assert set(move.data["notified"]) == {w.world.id("pl_env"), w.world.id("pl")}

    # The same head again: nothing new.
    await w.poll()
    await w.poll(advance=BASE_WATCH_INTERVAL_S)
    assert len(w.moves()) == 1
    assert len(w.texts("pl_env")) == 1


async def test_sync_notices_are_spaced_but_the_env_always_redeploys(watching: Watching) -> None:
    w = watching
    _open_pr(w)
    await w.poll()
    for seed in ("b", "c"):
        w.gh.branches[(REPO, BASE)] = sha(seed)
        await w.poll()
    assert len(w.moves()) == 2
    assert len(w.texts("pl_env")) == 2
    assert len(w.texts("pl", "sync open branches")) == 1

    w.clock.advance(base.SYNC_NOTICE_MIN_GAP_S)
    w.gh.branches[(REPO, BASE)] = sha("d")
    await w.poll()
    assert len(w.texts("pl", "sync open branches")) == 2


async def test_heads_are_read_on_their_own_when_no_pr_is_due(watching: Watching) -> None:
    w = watching
    await w.poll()
    queries = len(w.gh.base_queries)
    assert queries == 2  # one per watched repo (app and infra), no PR query
    assert w.gh.pr_queries == []

    await w.poll()
    assert len(w.gh.base_queries) == queries  # not due yet
    await w.poll(advance=BASE_WATCH_INTERVAL_S)
    assert len(w.gh.base_queries) == queries + 2


async def test_a_move_while_the_server_was_down_is_caught(watching: Watching) -> None:
    w = watching
    await w.poll()
    w.gh.branches[(REPO, BASE)] = sha("b")
    restarted = PrPoller(w.world.app_state, clock=w.clock, poll_s=POLL_S)

    await w.poll(poller=restarted)

    [move] = w.moves()
    assert move.data["from"] == sha("a") and move.data["to"] == sha("b")


async def test_hooks_get_the_move_and_the_merged_pr(watching: Watching) -> None:
    w = watching
    seen: list[base.BaseMove] = []
    remove = base.on_base_moved(lambda _state, move: seen.append(move))
    broken = base.on_base_moved(lambda _state, _move: 1 / 0)
    await w.poll()
    merged = w.store.upsert_pr(w.fid, REPO, 9650, source="discovered", project_slug="api-version")
    w.store.update_pr(
        merged[0].id, state="merged", merged_sha=sha("m"), merged_at=int(w.clock.now)
    )
    w.gh.branches[(REPO, BASE)] = sha("m")

    await w.poll()

    [move] = seen
    assert (move.repo, move.base, move.from_sha, move.to_sha) == (REPO, BASE, sha("a"), sha("m"))
    assert move.pr == 9650 and move.factory_id == w.fid
    [event] = w.moves()
    assert event.data["pr"] == 9650
    assert event.summary.endswith("(#9650)")
    remove()
    broken()


async def test_a_missing_branch_is_ignored(watching: Watching) -> None:
    w = watching
    del w.gh.branches[(REPO, BASE)]
    await w.poll()
    assert w.head() is None and w.moves() == []


def test_base_view_reads_the_default_base_and_the_live_env(world: FactoryWorld) -> None:
    fid = world.id("lead_a")
    store = world.deps.factory_store
    world.store.set_labels(
        fid,
        {
            F.LABEL_DEFAULT_REPO: REPO,
            F.LABEL_DEFAULT_BASE: BASE,
            base.head_label(REPO, BASE): f"{sha('b')} 1700000000 {REPO}#{BASE}",
        },
    )
    env_row = store.upsert_project(
        fid,
        "base-env",
        repo=REPO,
        base_branch=BASE,
        charter=normalize_charter({"kind": "integration"}),
    )
    env = {"reported": None, "probe": None}

    view = base.base_view(world.get("lead_a"), [env_row], {"base-env": env})

    assert view == {
        "repo": REPO,
        "branch": BASE,
        "head_sha": sha("b"),
        "moved_at": 1700000000,
        "env": env,
        "env_project": "base-env",
    }
    plain = world.get("other_lead")
    assert base.base_view(plain, [], {}) is None


def test_a_recorded_head_of_another_pair_is_not_read(world: FactoryWorld) -> None:
    labels = {base.head_label(REPO, BASE): f"{sha('b')} 0 other/repo#{BASE}"}
    assert base.read_head(labels, REPO, BASE) is None
    labels = {base.head_label(REPO, BASE): f"{sha('b')} 0 {REPO}#{BASE}"}
    assert base.read_head(labels, REPO, BASE) == base.BaseHead(sha("b"), None)


async def test_a_pr_batch_reads_the_watched_bases_not_the_prs_bases(watching: Watching) -> None:
    """A due PR against another branch still carries the watched base in its query."""
    w = watching
    created = w.clock.now + 5
    pr = FakePr(REPO, 9641, "factory/api-version/y", sha("y"), created, base_ref="main")
    pr.commits.append((pr.head_sha, iso(created - 1)))
    w.gh.add(pr)
    w.gh.branches[(REPO, "main")] = sha("z")
    await w.poll()
    assert w.head() == base.BaseHead(sha("a"), None)
    assert base.read_head(w.world.get("lead_a").labels, REPO, "main") is None

    w.gh.branches[(REPO, BASE)] = sha("b")
    await w.poll()
    [move] = w.moves()
    assert move.data["to"] == sha("b")
    queries = [r.content.decode() for r in w.gh.pr_queries]
    assert all("refs/heads/temp-main" in q and 'refs/heads/main"' not in q for q in queries)
