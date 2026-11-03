"""The ``repo`` kind: issues, discussions and releases read on the server with the owner's GitHub.

Team authors (GitHub's ``OWNER``, ``MEMBER``, ``COLLABORATOR``) and releases
of the factory's own repos become hunches; bots are skipped; a sync resumes
from its cursor; a search never leaves the source's repos; every document is
a read-only query. Every repo, person and text is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.factory.background import discovery_sync
from omnigent.server.factory.discovery import gateway, ledger
from omnigent.server.factory.discovery.kinds import repo as repo_kind
from omnigent.server.factory.discovery.kinds.base import SourceFailed
from omnigent.server.github_app_client import graphql_operations
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import base, discovery_cockpit
from tests.server.factory.source_helpers import (
    OTHER_REPO,
    REPO_SOURCE,
    SAMPLE_REPO,
    FakeGitHub,
    discussion,
    install_backends,
    issue,
    query,
    release,
    set_sources,
    source_events,
)

LATER = "2026-11-02T12:00:00Z"


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        set_sources(value, REPO_SOURCE)
        gateway.reset()
        yield value


def sample_repo() -> dict:
    return {
        "issues": [
            issue(1, "I can't share a draft with my cofounder (sample)", "Publishing early hurts"),
            issue(2, "Team note: we should add sharing (sample)", association="MEMBER"),
            issue(3, "Dependency bump (sample)", login="sample-bot[bot]", bot=True),
            issue(4, "Crash when exporting (sample)", labels=("bug",), login="sample-user-2"),
        ],
        "discussions": [discussion(7, "How do others share drafts? (sample)")],
        "releases": [release("v1.2.0", "Faster exports (sample)")],
    }


async def test_a_sync_stores_user_items_and_team_items_as_hunches(cockpit: Cockpit) -> None:
    fake = FakeGitHub(repos={SAMPLE_REPO: sample_repo()})
    install_backends(cockpit, github=fake)

    done = await gateway.sync_source(cockpit.app.state, cockpit.fid, "repo-issues")

    assert done is not None and done["outcome"] == "ok" and done["new"] == 5
    rows = {
        s.pointer.rsplit("/", 2)[-2] + "/" + s.pointer.rsplit("/", 1)[-1]: s
        for s in cockpit.store.list_signals(cockpit.fid)
    }
    user = rows["issues/1"]
    assert user.kind is None and user.account and user.account.startswith("acct_")
    assert ledger.TEAM_AUTHOR not in user.flags and user.source == "repo-issues"
    team = rows["issues/2"]
    assert team.kind == "hunch" and ledger.TEAM_AUTHOR in team.flags
    assert rows["issues/4"].kind == "bug"
    assert "issues/3" not in rows
    assert rows["discussions/7"].kind is None
    # A release of a repo outside the factory's projects is market evidence.
    shipped = rows["tag/v1.2.0"]
    assert shipped.kind == "market" and ledger.TEAM_AUTHOR not in shipped.flags
    # One read-only document per repo; the event is the sync's.
    ((doc, variables),) = fake.calls
    assert graphql_operations(doc) == ["query"]
    assert variables["owner"] == "sample-org" and variables["n"] == 50
    (event,) = source_events(cockpit, "source.sync")
    assert event.data["by_kind"] == "server" and event.data["new"] == 5
    assert all(s.query_event == event.id for s in rows.values())


async def test_releases_of_the_factorys_own_repos_are_the_teams(cockpit: Cockpit) -> None:
    cockpit.store.upsert_project(cockpit.fid, "sample-project", repo=SAMPLE_REPO)
    install_backends(cockpit, github=FakeGitHub(repos={SAMPLE_REPO: sample_repo()}))

    await gateway.sync_source(cockpit.app.state, cockpit.fid, "repo-issues")

    (ours,) = [s for s in cockpit.store.list_signals(cockpit.fid) if "/releases/" in s.pointer]
    assert ours.kind == "hunch" and ledger.TEAM_AUTHOR in ours.flags


async def test_the_next_sync_resumes_from_its_cursor(cockpit: Cockpit) -> None:
    fake = FakeGitHub(repos={SAMPLE_REPO: sample_repo()})
    install_backends(cockpit, github=fake)
    await gateway.sync_source(cockpit.app.state, cockpit.fid, "repo-issues")

    fake.repos[SAMPLE_REPO]["issues"].append(
        issue(9, "Export to a file please (sample)", updated=LATER, login="sample-user-9")
    )
    fake.repos[SAMPLE_REPO]["discussions"].append(
        discussion(8, "Old talk (sample)", updated="2026-10-01T00:00:00Z")
    )
    done = await gateway.sync_source(cockpit.app.state, cockpit.fid, "repo-issues")

    assert done is not None and done["new"] == 1
    _first, (_doc, second) = fake.calls
    assert second["since"] == "2026-11-02T10:00:00Z"
    numbers = [s.ref for s in cockpit.store.list_signals(cockpit.fid)]
    assert numbers[0] == "S-6" and len(numbers) == 6
    state = cockpit.store.get_source_state(cockpit.fid, "repo-issues")
    assert state is not None and SAMPLE_REPO in (state.cursor or "")


async def test_the_background_sync_runs_due_sources_once_per_interval(cockpit: Cockpit) -> None:
    fake = FakeGitHub(repos={SAMPLE_REPO: sample_repo()})
    install_backends(cockpit, github=fake)

    first = await discovery_sync.run_once(cockpit.app.state)
    again = await discovery_sync.run_once(cockpit.app.state)
    later = await discovery_sync.run_once(
        cockpit.app.state, now=int(__import__("time").time()) + 3601
    )

    assert first == {"synced": 1, "failed": 0}
    assert again == {"synced": 0, "failed": 0}
    assert later == {"synced": 1, "failed": 0}
    assert len(fake.calls) == 2


@pytest.mark.parametrize(
    "labels", [{F.LABEL_STATE: F.STATE_DONE}, {"omnigent.closed": "true"}], ids=["done", "closed"]
)
async def test_a_finished_or_closed_factory_syncs_nothing(
    cockpit: Cockpit, labels: dict[str, str]
) -> None:
    fake = FakeGitHub(repos={SAMPLE_REPO: sample_repo()})
    install_backends(cockpit, github=fake)
    cockpit.world.store.set_labels(cockpit.fid, labels)

    assert await discovery_sync.run_once(cockpit.app.state) == {"synced": 0, "failed": 0}
    assert fake.calls == []


async def test_query_only_sources_never_sync(cockpit: Cockpit) -> None:
    fake = FakeGitHub(repos={SAMPLE_REPO: sample_repo()})
    install_backends(cockpit, github=fake)
    set_sources(cockpit, {**REPO_SOURCE, "interval_min": 0})

    assert await discovery_sync.run_once(cockpit.app.state) == {"synced": 0, "failed": 0}
    assert await gateway.sync_source(cockpit.app.state, cockpit.fid, "repo-issues") is None
    assert fake.calls == []


async def test_search_never_leaves_the_sources_repos(cockpit: Cockpit) -> None:
    other = {"issues": [issue(5, "Elsewhere (sample)", repo=OTHER_REPO)]}
    fake = FakeGitHub(repos={SAMPLE_REPO: sample_repo(), OTHER_REPO: other})
    install_backends(cockpit, github=fake)

    resp = await query(
        cockpit, "repo-issues", "search", {"q": "repo:sample-org/secret org:x share draft"}
    )

    assert resp.status_code == 200, resp.text
    ((doc, variables),) = fake.calls
    assert "search(" in doc and graphql_operations(doc) == ["query"]
    assert variables["q"] == f"repo:{SAMPLE_REPO} is:issue share draft"
    assert variables["type"] == "ISSUE"
    pointers = [s.pointer for s in cockpit.store.list_signals(cockpit.fid)]
    assert pointers and all(SAMPLE_REPO in p for p in pointers)
    (event,) = source_events(cockpit)
    assert event.data["q"] == "share draft"
    # Not even a count of what matched elsewhere comes back.
    assert resp.json()["count"]["returned"] == event.data["returned"] == 5


async def test_a_search_of_only_qualifiers_is_refused(cockpit: Cockpit) -> None:
    install_backends(cockpit, github=FakeGitHub(repos={SAMPLE_REPO: sample_repo()}))

    resp = await query(cockpit, "repo-issues", "search", {"q": "repo:a/b is:open"})

    assert resp.status_code == 400 and error_code(resp) == "invalid_args"


async def test_tools_read_one_repo_or_all_and_count_each_repo(cockpit: Cockpit) -> None:
    fake = FakeGitHub(repos={SAMPLE_REPO: sample_repo(), OTHER_REPO: {"releases": []}})
    install_backends(cockpit, github=fake)
    params = {**REPO_SOURCE["params"], "repos": [SAMPLE_REPO, OTHER_REPO]}
    set_sources(cockpit, {**REPO_SOURCE, "params": params})

    both = await query(cockpit, "repo-issues", "releases")
    one = await query(cockpit, "repo-issues", "discussions", {"repo": SAMPLE_REPO.upper()})

    assert both.status_code == one.status_code == 200
    assert both.json()["calls"] == 2 and one.json()["calls"] == 1
    assert [i["kind"] for i in both.json()["items"]] == ["market"]
    state = cockpit.store.get_source_state(cockpit.fid, "repo-issues")
    assert state is not None and state.calls_today == 3


async def test_without_listed_repos_the_factorys_own_are_read(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeGitHub(repos={SAMPLE_REPO: sample_repo()})
    install_backends(cockpit, github=fake)
    set_sources(cockpit, {**REPO_SOURCE, "params": {}})
    cockpit.store.upsert_project(cockpit.fid, "sample-project", repo=SAMPLE_REPO)
    assert SAMPLE_REPO in repo_kind.own_repos(cockpit.deps, cockpit.fid)
    monkeypatch.setattr(repo_kind, "own_repos", lambda _deps, _fid: [])
    none = await query(cockpit, "repo-issues", "issues")
    monkeypatch.setattr(repo_kind, "own_repos", lambda _deps, _fid: [SAMPLE_REPO])
    own = await query(cockpit, "repo-issues", "issues")

    assert none.status_code == 409 and error_code(none) == "source_invalid"
    assert own.status_code == 200
    assert [c[1]["name"] for c in fake.calls] == ["sample-app"]


async def test_a_repo_github_cannot_read_fails_the_query(cockpit: Cockpit) -> None:
    install_backends(cockpit, github=FakeGitHub(repos={}))

    resp = await query(cockpit, "repo-issues", "issues")

    assert resp.status_code == 502 and error_code(resp) == "source_failed"
    state = cockpit.store.get_source_state(cockpit.fid, "repo-issues")
    assert state is not None and state.failures == 1 and "cannot be read" in state.last_error
    (event,) = source_events(cockpit)
    assert event.data["outcome"] == "error"


def test_every_repo_document_is_a_read_only_query() -> None:
    docs = [
        repo_kind.SYNC_QUERY,
        repo_kind.ISSUES_QUERY,
        repo_kind.DISCUSSIONS_QUERY,
        repo_kind.RELEASES_QUERY,
        repo_kind.SEARCH_QUERY,
        repo_kind.TEST_QUERY,
    ]
    for doc in docs:
        assert graphql_operations(doc) == ["query"], doc


def test_search_words_drop_every_qualifier() -> None:
    assert repo_kind.search_words("repo:x/y user:z -label:bug share\x00 a draft") == (
        "share a draft"
    )
    assert repo_kind.search_words("(repo:x/y) OR (org:z AND share) NOT -(is:open) draft") == (
        "share draft"
    )
    assert repo_kind.search_words("share or draft and not this") == "share draft this"


async def test_the_server_reader_needs_the_owners_github_connection(cockpit: Cockpit) -> None:
    bare = SimpleNamespace(factory=cockpit.deps)
    with pytest.raises(SourceFailed, match="no GitHub App"):
        await repo_kind.AppGitHubReader(bare).graphql(cockpit.fid, repo_kind.TEST_QUERY, {})

    class NoConnection:
        def get(self, *_a: object, **_k: object) -> None:
            return None

    wired = SimpleNamespace(
        factory=cockpit.deps, github_client=object(), github_store=NoConnection()
    )
    with pytest.raises(SourceFailed, match="not connected"):
        await repo_kind.AppGitHubReader(wired).graphql(cockpit.fid, repo_kind.TEST_QUERY, {})


async def test_a_test_checks_each_repo_and_stores_nothing(cockpit: Cockpit) -> None:
    install_backends(cockpit, github=FakeGitHub(repos={SAMPLE_REPO: sample_repo()}))
    params = {**REPO_SOURCE["params"], "repos": [SAMPLE_REPO, OTHER_REPO]}
    set_sources(cockpit, {**REPO_SOURCE, "params": params})

    resp = await cockpit.post(base(cockpit, "/sources/repo-issues/test"), user="carol")
    viewer = await cockpit.post(base(cockpit, "/sources/repo-issues/test"), user="bob")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert [(c["name"], c["ok"]) for c in body["checks"]] == [
        ("repos", True),
        (SAMPLE_REPO, True),
        (OTHER_REPO, False),
    ]
    assert body["source"]["calls_today"] == 2
    assert cockpit.store.list_signals(cockpit.fid) == []
    (event,) = source_events(cockpit)
    assert event.data["mode"] == "test" and event.data["by"] == "carol"
    assert viewer.status_code in (403, 404)
