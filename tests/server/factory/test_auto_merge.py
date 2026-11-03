"""Owner-set auto-merge targets: the server merges ready, green factory PRs into them.

GitHub is the in-memory fake (``github_fakes.FakeGitHub``) behind the real
client, with its one opt-in write: the merge call. The PR poller marks PRs
ready exactly as in production; the merger then reads GitHub again and
merges only a ready PR into a target whose head is unchanged and whose every
check is green. Agents never set targets. Sample data only.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryProject
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.factory import (
    auto_merge,
    chat_cards,
    definition,
    issues,
    live,
    nudge,
    product_profile,
    status,
    tuning,
)
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.asks import AskInputs, AsksResponse, build_asks
from omnigent.server.factory.background.auto_merger import (
    BACKOFF_START_S,
    EVENT_KIND,
    INCIDENT_RULE,
    AutoMerger,
)
from omnigent.server.factory.background.pr_poller import PrPoller
from omnigent.server.factory.charter import render_pl_charter
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.definition_blocks import DeliveryDef, agent_may_propose
from omnigent.server.factory.mailer import MAILER_STATE_ATTR, Mailer
from omnigent.server.factory.slackdm import rules as dm_rules
from omnigent.server.factory.snapshot import _pr_view
from omnigent.server.factory.status_summary import pr_status
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld, FakeClock, FakeDeliver
from tests.server.factory.definition_helpers import definition_cockpit, propose, put, url
from tests.server.factory.github_fakes import (
    FakeCheck,
    FakeConnection,
    FakeGitHub,
    FakeGithubStore,
    FakePr,
    iso,
    sha,
)

REPO = "sample-org/sample-app"
BASE = "staging"
TARGETS = [{"repo": REPO, "base": BASE}]
PATH = auto_merge.AUTO_MERGE_PATH
POLL_S = 60.0


# ── The setting ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([{"repo": "no-slash", "base": BASE}], "owner/name"),
        ([{"repo": REPO, "base": "release/*"}], "branch name"),
        ([{"repo": REPO, "base": "../main"}], "branch name"),
        ([{"repo": REPO, "base": BASE, "when": "always"}], "Extra inputs"),
        ([{"repo": REPO, "base": BASE}, {"repo": REPO.upper(), "base": BASE}], "twice"),
    ],
)
def test_targets_are_repo_and_base_names_only(value: list[Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        DeliveryDef.model_validate({"auto_merge": value})


def test_no_agent_seat_may_propose_targets() -> None:
    for seat in ("scout", "sentinel", "atlas", "project_lead"):
        assert not agent_may_propose(seat, PATH)
    # Scout still proposes the rest of the profile.
    assert agent_may_propose("scout", "product.delivery.merge_policy")


def test_targets_are_stored_only_once_set() -> None:
    doc = definition.FactoryDefinitionDoc().model_dump(mode="json")
    # A default profile is still left out whole.
    assert "product" not in definition.stored_form(doc)
    doc["product"]["name"] = "Sample app (sample)"
    # A profile with only a name sets no delivery field (the seed still applies to it).
    assert not product_profile._block_set(definition.stored_form(doc), product_profile.DELIVERY)
    doc["product"]["delivery"]["protected_repos"] = ["sample-org/sample-infra"]
    stored = definition.stored_form(doc)
    # A server from before the targets still reads the profile while none is set.
    assert "auto_merge" not in stored["product"]["delivery"]
    assert doc["product"]["delivery"]["auto_merge"] == []
    assert definition.full_doc(stored)["product"]["delivery"]["auto_merge"] == []
    doc["product"]["delivery"]["auto_merge"] = TARGETS
    assert definition.stored_form(doc)["product"]["delivery"]["auto_merge"] == TARGETS


def test_a_deployment_seed_carries_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        product_profile.DELIVERY_SEED_ENV,
        json.dumps({REPO: {"merge_policy": "Owners merge (sample)", "auto_merge": TARGETS}}),
    )
    assert product_profile.seed_values(REPO) == {
        "product.delivery.merge_policy": "Owners merge (sample)",
        f"{PATH}": TARGETS,
    }
    monkeypatch.setenv(
        product_profile.DELIVERY_SEED_ENV, json.dumps({REPO: {"auto_merge": [{"repo": REPO}]}})
    )
    assert product_profile.deployment_seeds() == {}


def _project(**fields: Any) -> FactoryProject:
    base = FactoryProject(
        id="proj_1",
        factory_id="fac_1",
        slug="status",
        title="Status endpoint (sample)",
        repo=REPO,
        base_branch=BASE,
        priority=1,
        status="active",
        charter={"goal": "Add GET /status.", "dod": ["curl -fsS {api}/status → 200"]},
    )
    return replace(base, **fields)


def test_the_charter_says_the_server_merges_a_target_base() -> None:
    delivery = {"merge_policy": "Owners merge (sample)", "auto_merge": TARGETS}
    text = render_pl_charter(_project(), delivery)
    assert (
        f"Merge policy: PRs into {REPO}#{BASE} are merged by the factory's server when ready; "
        "never merge yourself. Report READY FOR HUMAN MERGE <url> to your lead once every gate "
        "is green; never run `gh pr merge` or the merge gate."
    ) in text
    other = render_pl_charter(_project(base_branch="main"), delivery)
    assert "Merge policy: Owners merge (sample). The factory never merges." in other


# ── Owners set them; agents never do ─────────────────────────────────────────


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    monkeypatch.delenv(product_profile.DELIVERY_SEED_ENV, raising=False)
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


async def test_only_an_owner_sets_targets_and_only_as_a_person(cockpit: Cockpit) -> None:
    change = {"op": "replace", "path": PATH, "value": TARGETS}

    editor = await put(cockpit, change, user="carol")
    runner = await cockpit.client.put(
        url(cockpit),
        json={"changes": [change]},
        headers={"X-Test-User": "alice", RUNNER_TUNNEL_TOKEN_HEADER: "tok"},
    )
    owner = await put(cockpit, change)

    assert editor.status_code == 403
    assert (runner.status_code, error_code(runner)) == (403, "human_only")
    assert owner.status_code == 200, owner.text
    assert auto_merge.factory_targets(cockpit.deps, cockpit.fid) == {(REPO, BASE)}
    applied = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert applied is not None and (applied.author_kind, applied.class_) == ("human", "human")
    assert definition.classify(PATH, [], TARGETS) == "B" and definition.owner_only(PATH)


async def test_an_agent_never_proposes_targets(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    change = [{"op": "replace", "path": PATH, "value": TARGETS}]
    sentinel = await propose(cockpit, changes=change)
    atlas = await propose(cockpit, key="lead_a", changes=change)
    monkeypatch.setattr(tuning, "agent_seat", lambda caller: "scout")
    scout = await propose(cockpit, changes=change, expected_effect=None)

    for resp in (sentinel, atlas, scout):
        assert (resp.status_code, error_code(resp)) == (403, "path_not_allowed"), resp.text
        assert "owners in the cockpit" in resp.text
    assert cockpit.store.get_proposal_by_number(cockpit.fid, 1) is None
    assert auto_merge.factory_targets(cockpit.deps, cockpit.fid) == frozenset()


async def test_approving_an_agent_proposal_never_widens_targets(cockpit: Cockpit) -> None:
    # A proposal stored past the propose check (an older server filed it) still never applies.
    row, _ = cockpit.store.create_proposal(
        cockpit.fid,
        kind="tuning",
        title="merge everything (sample)",
        author_kind="agent",
        author=cockpit.world.id("lead_b"),
        approval="owner",
        class_="B",
        ops=[{"op": "replace", "path": PATH, "value": TARGETS}],
        after={PATH: TARGETS},
        before={PATH: []},
        based_on={"def_version": 1},
    )
    root = cockpit.world.get("lead_a")

    with pytest.raises(CockpitError) as refused:
        await tuning.apply(
            cockpit.app.state, root, row, actor=Actor.human("alice", "owner"), trial=False
        )

    assert (refused.value.status_code, refused.value.code) == (403, "path_not_allowed")
    stored = cockpit.store.get_proposal(row.id)
    assert stored is not None and stored.status == "refused"
    assert auto_merge.factory_targets(cockpit.deps, cockpit.fid) == frozenset()


# ── The merger ───────────────────────────────────────────────────────────────


@dataclass
class Merging:
    """The poller and the merger over the seeded world, a fake GitHub and a fake clock."""

    world: FactoryWorld
    gh: FakeGitHub
    poller: PrPoller
    merger: AutoMerger
    clock: FakeClock
    deliver: FakeDeliver
    mailer: Mailer

    @property
    def store(self) -> Any:
        return self.world.deps.factory_store

    @property
    def fid(self) -> str:
        return self.world.id("lead_a")

    @property
    def pl(self) -> str:
        return self.world.id("pl")

    async def poll(self, advance: float = POLL_S + 1) -> None:
        self.clock.advance(advance)
        await self.poller.run_once()
        await self.mailer.drain()

    def events(self, *kinds: str) -> list[Any]:
        found = self.store.list_events(self.fid, limit=1000, kinds=list(kinds) or None)
        return list(reversed(found))

    def pr(self, number: int = 9630) -> Any:
        return self.store.get_pr_by_number(self.fid, REPO, number)

    def new_pr(
        self,
        number: int = 9630,
        *,
        slug: str = "api-version",
        base: str = BASE,
        checks: list[FakeCheck] | None = None,
        **kw: Any,
    ) -> FakePr:
        created = self.clock.now + 5
        head = sha(f"h{number}")
        pr = FakePr(
            REPO,
            number,
            f"factory/{slug}/task-{number}",
            head,
            created,
            base_ref=base,
            checks=[FakeCheck("ci / test")] if checks is None else checks,
            **kw,
        )
        pr.commits.append((head, iso(created - 1)))
        at = created + 30
        pr.review(f"<!-- factory:review sha={head} verdict=APPROVE -->", at, login="bot")
        pr.comment(f"<!-- factory:verify sha={head} result=PASS env=x -->", at)
        return self.gh.add(pr)

    def merges(self) -> list[dict[str, Any]]:
        return list(self.gh.merges)

    def pr_views(self) -> list[dict[str, Any]]:
        """The PR views the snapshot shows (its ``auto_merge`` flag from the targets)."""
        targets = auto_merge.factory_targets(self.world.deps, self.fid)
        return [_pr_view(pr, [], {}, targets) for pr in self.store.list_prs(self.fid)]

    def asks(self) -> dict[str, Any]:
        """The owner's Needs you over those PR views."""
        body = {
            "factory": {"id": self.fid, "name": "Sample factory (sample)"},
            "projects": [],
            "prs": self.pr_views(),
            "decisions": [],
            "members": [],
        }
        built = build_asks(
            AskInputs(snapshot=body, user_id=None, level=LEVEL_OWNER), now=self.clock.now
        )
        AsksResponse.model_validate(built)
        return built


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
    store.upsert_project(
        fid, "api-version", title="API version", repo=REPO, base_branch=BASE,
        pl_session_id=world.id("pl"),
    )  # fmt: skip
    store.upsert_project(fid, "api-status", title="API status", repo=REPO, base_branch="main")
    definition.set_values(world.deps, fid, {PATH: TARGETS}, created_by="alice")
    gh = FakeGitHub(allow_merge=True)
    clock = FakeClock()
    state = world.app_state
    state.github_client = gh.client()
    state.github_store = FakeGithubStore({"local": FakeConnection()})
    mailer = Mailer(state, deliver=fake_deliver, sleep=no_sleep)
    setattr(state, MAILER_STATE_ATTR, mailer)
    poller = PrPoller(state, clock=clock, poll_s=POLL_S)
    merger = AutoMerger(state, clock=clock, interval_s=POLL_S)
    unmirror = chat_cards.register()  # the Room's cards, as the server runs them
    try:
        yield Merging(world, gh, poller, merger, clock, fake_deliver, mailer)
    finally:
        await mailer.close()
        restore()
        if unmirror is not None:
            unmirror()


async def test_a_ready_green_pr_into_a_target_is_merged_at_its_head(merging: Merging) -> None:
    m = merging
    m.new_pr(checks=[FakeCheck("ci / test"), FakeCheck("lint", required=False)])
    await m.poll()
    row = m.pr()
    assert row.ready is True
    # The factory's ready notice says the server merges it; nobody is asked to.
    [ready] = [t for t in m.deliver.texts_to(m.pl) if "READY FOR HUMAN MERGE" in t]
    assert f"{BASE} is an auto-merge target: the factory's server merges it" in ready
    [event] = m.events("pr.ready")
    assert event.data["auto_merge"] is True
    assert dm_rules.pr_items(None, event, row, None) == []  # type: ignore[arg-type]
    asks = m.asks()
    assert asks["asks"] == []
    assert asks["handled"] == ["PR #9630 (factory change) merges automatically when checks pass."]
    [view] = m.pr_views()
    assert view["auto_merge"] is True
    assert pr_status(view) == "PR #9630 merges automatically when checks pass"

    merged = await m.merger.run_once()

    assert merged == [row.id]
    assert m.merges() == [
        {"repo": REPO, "number": 9630, "merge_method": "squash", "sha": row.head_sha}
    ]
    [auto] = m.events(EVENT_KIND)
    assert auto.subject == f"pr:{REPO}#9630" and auto.project_slug == "api-version"
    assert auto.data["by"] == "server" and auto.data["target"] == {"repo": REPO, "base": BASE}
    assert auto.data["merged_sha"] == sha("merge9630") and auto.data["head_sha"] == row.head_sha
    assert auto.data["gates"]["checks"] == "pass" and auto.data["checks"] == 2
    lines = [
        msg.body
        for msg in m.store.list_chat(m.fid, "project:api-version", limit=50)
        if msg.author_kind == "system"
    ]
    assert any("merged by the factory into staging" in line for line in lines)

    # The poller refreshes it at once (no clock wait): the usual merged flow runs.
    await m.poll(advance=0)
    row = m.pr()
    assert row.state == "merged" and row.merged_by == "lena"
    assert len(m.events("pr.merged")) == 1
    assert any("merged by @lena" in t for t in m.deliver.texts_to(m.pl))
    assert m.store.get_project(m.fid, "api-version").stage == "merged"
    assert m.asks()["handled"] == [] and m.asks()["asks"] == []
    # Merged once: a later pass leaves it alone.
    assert await m.merger.run_once() == []
    assert len(m.merges()) == 1


async def test_a_pr_into_another_base_waits_for_a_person(merging: Merging) -> None:
    m = merging
    m.new_pr(9640, slug="api-status", base="main")
    await m.poll()
    assert m.pr(9640).ready is True

    queries = len(m.gh.graphql_calls)

    assert await m.merger.run_once() == []

    # Not a target: never read, never merged, and a person is asked as before.
    assert m.merges() == [] and len(m.gh.graphql_calls) == queries
    assert m.merger.waiting == {}
    assert m.pr_views()[0]["auto_merge"] is False
    assert [a["kind"] for a in m.asks()["asks"]] == ["merge"]
    [ready] = [t for t in m.deliver.texts_to(m.world.id("lead_a")) if "READY FOR" in t]
    assert "A human merges it on GitHub" in ready


async def test_a_pr_not_ready_is_not_read_or_merged(merging: Merging) -> None:
    m = merging
    gh_pr = m.new_pr(checks=[FakeCheck("ci / test", status="IN_PROGRESS")])
    await m.poll()
    assert m.pr().ready is False
    queries = len(m.gh.pr_queries)

    assert await m.merger.run_once() == []

    assert m.merges() == [] and len(m.gh.pr_queries) == queries
    gh_pr.checks = [FakeCheck("ci / test")]
    await m.poll()
    assert await m.merger.run_once() == [m.pr().id]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            lambda pr: setattr(pr, "checks", [FakeCheck("ci / test"), FakeCheck("e2e", "QUEUED")]),
            "checks still running: e2e",
        ),
        (
            lambda pr: setattr(
                pr, "checks", [FakeCheck("ci / test"), FakeCheck("lint", conclusion="FAILURE")]
            ),
            "checks not green: lint (failure)",
        ),
        (
            lambda pr: setattr(
                pr, "checks", [FakeCheck("ci / test"), FakeCheck("e2e", conclusion="CANCELLED")]
            ),
            "checks not green: e2e (cancelled)",
        ),
        (lambda pr: setattr(pr, "head_sha", sha("pushed")), "head moved since ready"),
        (lambda pr: setattr(pr, "is_draft", True), "PR is a draft"),
        (lambda pr: setattr(pr, "mergeable", "UNKNOWN"), "mergeable=UNKNOWN"),
        (
            lambda pr: setattr(pr, "review_decision", "CHANGES_REQUESTED"),
            "changes requested on GitHub",
        ),
    ],
)
async def test_github_must_still_say_green_at_the_same_head(
    merging: Merging, change: Callable[[FakePr], None], reason: str
) -> None:
    m = merging
    gh_pr = m.new_pr(checks=[FakeCheck("ci / test"), FakeCheck("lint", required=False)])
    await m.poll()
    row = m.pr()
    assert row.ready is True
    # GitHub moves on after the poller marked it ready (no new poll yet).
    change(gh_pr)

    assert await m.merger.run_once() == []

    assert m.merges() == []
    assert m.merger.waiting[row.id].startswith(reason)
    # Not green yet is a wait, not a failure: no merge incident.
    assert m.events(EVENT_KIND) == []
    assert issues.open_issues(m.world.deps, m.fid, INCIDENT_RULE) == []


async def test_a_stored_draft_or_a_target_without_checks_never_merges(merging: Merging) -> None:
    m = merging
    m.new_pr(checks=[])
    await m.poll()
    row = m.pr()
    # The factory's gate passes a repo that reports no checks; the merger waits for one.
    assert row.ready is True

    assert await m.merger.run_once() == []
    assert m.merger.waiting[row.id] == "no checks reported on the head yet"
    draft = replace(row, is_draft=True)
    assert auto_merge.local_refusal(draft, frozenset({(REPO, BASE)})) == "PR is a draft"


async def test_a_push_after_the_read_is_refused_by_github(merging: Merging) -> None:
    m = merging
    gh_pr = m.new_pr()
    await m.poll()
    row = m.pr()
    real = m.gh.client().graphql

    async def read_then_push(*args: Any, **kwargs: Any) -> Any:
        result = await real(*args, **kwargs)
        gh_pr.push(sha("late"), m.clock.now, checks=[FakeCheck("ci / test")])
        return result

    client = m.gh.client()
    client.graphql = read_then_push  # type: ignore[method-assign]
    m.world.app_state.github_client = client

    assert await m.merger.run_once() == []

    # The merge named the head it checked; GitHub refused the moved one.
    assert [c["sha"] for c in m.merges()] == [row.head_sha]
    assert gh_pr.state == "OPEN"
    [incident] = issues.open_issues(m.world.deps, m.fid, INCIDENT_RULE)
    assert "409" in incident.summary


async def test_a_github_failure_backs_off_and_raises_one_incident(merging: Merging) -> None:
    m = merging
    m.new_pr()
    await m.poll()
    row = m.pr()
    m.gh.merge_status = 405

    assert await m.merger.run_once() == []

    [incident] = issues.open_issues(m.world.deps, m.fid, INCIDENT_RULE)
    assert incident.target == f"pr:{row.id}" and incident.assignee == issues.ASSIGN_OWNERS
    assert incident.severity == "warning" and incident.project_slug == "api-version"
    assert "405" in incident.summary and "not mergeable (sample)" in incident.summary
    assert len(m.merges()) == 1

    # Backing off: passes inside the window make no GitHub call at all.
    calls = len(m.gh.requests)
    m.clock.advance(BACKOFF_START_S - 10)
    for _ in range(3):
        assert await m.merger.run_once() == []
    assert len(m.gh.requests) == calls and len(m.merges()) == 1

    # After it, one more try fails: the same incident, a longer wait.
    m.clock.advance(20)
    assert await m.merger.run_once() == []
    assert len(m.merges()) == 2
    [again] = issues.open_issues(m.world.deps, m.fid, INCIDENT_RULE)
    assert again.id == incident.id and again.count == 2
    m.clock.advance(BACKOFF_START_S + 10)
    await m.merger.run_once()
    assert len(m.merges()) == 2

    # GitHub recovers: it merges and the incident clears.
    m.gh.merge_status = None
    m.clock.advance(BACKOFF_START_S * 2)
    assert await m.merger.run_once() == [row.id]
    assert issues.open_issues(m.world.deps, m.fid, INCIDENT_RULE) == []
    assert len(m.events(EVENT_KIND)) == 1


async def test_no_targets_no_merge_and_off_means_off(merging: Merging) -> None:
    m = merging
    m.new_pr()
    await m.poll()
    definition.set_values(m.world.deps, m.fid, {PATH: []}, created_by="alice")
    assert await m.merger.run_once() == []
    off = AutoMerger(m.world.app_state, clock=m.clock, interval_s=0)
    definition.set_values(m.world.deps, m.fid, {PATH: TARGETS}, created_by="alice")
    assert await off.run_once() == []
    assert m.merges() == []
    assert await m.merger.run_once() == [m.pr().id]
