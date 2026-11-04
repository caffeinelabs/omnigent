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
from omnigent.factory import LABEL_STATE, STATE_ACTIVE, STATE_PAUSED
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
    MERGER_STATE_ATTR,
    QUIET_S,
    WAIT_INCIDENT_S,
    WAITING_RULE,
    AutoMerger,
)
from omnigent.server.factory.background.pr_poller import PrPoller
from omnigent.server.factory.charter import render_pl_charter
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.definition_blocks import (
    AUTO_MERGE_KEY,
    DeliveryDef,
    agent_may_propose,
)
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
    assert AUTO_MERGE_KEY not in stored
    assert doc["product"]["delivery"]["auto_merge"] == []
    assert definition.full_doc(stored)["product"]["delivery"]["auto_merge"] == []
    doc["product"]["delivery"]["auto_merge"] = TARGETS
    stored = definition.stored_form(doc)
    # Set, they live under their own key: an older server drops only them on a
    # rollback and still reads the profile (its protected repos guard writes).
    assert stored[AUTO_MERGE_KEY] == TARGETS
    assert "auto_merge" not in stored["product"]["delivery"]
    assert stored["product"]["delivery"]["protected_repos"] == ["sample-org/sample-infra"]
    assert DeliveryDef.model_fields.keys() - {"auto_merge"} >= set(stored["product"]["delivery"])
    full = definition.full_doc(stored)
    assert full["product"]["delivery"]["auto_merge"] == TARGETS
    assert definition.effective(stored)["product"]["delivery"]["auto_merge"] == TARGETS
    assert product_profile._block_set({AUTO_MERGE_KEY: TARGETS}, product_profile.DELIVERY)
    assert {d["path"] for d in definition.diff({}, {AUTO_MERGE_KEY: TARGETS})} == {PATH}


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
    assert auto_merge.factory_targets(cockpit.deps, cockpit.fid).pairs == {(REPO, BASE)}
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
    assert not auto_merge.factory_targets(cockpit.deps, cockpit.fid)


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
    assert not auto_merge.factory_targets(cockpit.deps, cockpit.fid)


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
        """The PR views the snapshot shows (``auto_merge`` set as ``build_snapshot`` sets it)."""
        on = auto_merge.merger_on(self.world.app_state)
        targets = auto_merge.factory_targets(self.world.deps, self.fid) if on else frozenset()
        projects = {p.slug: p for p in self.store.list_projects(self.fid)}
        return [
            _pr_view(
                pr, [], {}, auto_merge.merges_itself(pr, targets, projects.get(pr.project_slug))
            )
            for pr in self.store.list_prs(self.fid)
        ]

    def incidents(self, rule: str = WAITING_RULE) -> list[Any]:
        return issues.open_issues(self.world.deps, self.fid, rule)

    def asks(self) -> dict[str, Any]:
        """The owner's Needs you over those PR views and the open incidents."""
        body = {
            "factory": {"id": self.fid, "name": "Sample factory (sample)"},
            "projects": [],
            "prs": self.pr_views(),
            "decisions": [],
            "members": [],
        }
        built = build_asks(
            AskInputs(
                snapshot=body,
                user_id=None,
                level=LEVEL_OWNER,
                incidents=issues.open_issues(self.world.deps, self.fid),
            ),
            now=self.clock.now,
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
    setattr(state, MERGER_STATE_ATTR, merger)  # the server runs it (see background.auto_merger)
    unmirror = chat_cards.register()  # the Room's cards, as the server runs them
    try:
        yield Merging(world, gh, poller, merger, clock, fake_deliver, mailer)
    finally:
        setattr(state, MERGER_STATE_ATTR, None)
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
    # That does not clear by itself: owners hear of it at once, in Needs you.
    [incident] = m.incidents()
    assert "no checks reported on the head yet" in incident.summary
    assert [a["kind"] for a in m.asks()["asks"]] == ["broken"]
    draft = replace(row, is_draft=True)
    project = m.store.get_project(m.fid, "api-version")
    assert auto_merge.local_refusal(draft, frozenset({(REPO, BASE)}), project) == "PR is a draft"


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


# ── Only factory PRs, never a fork's, never on an outsider's word ────────────


async def test_a_tracked_pr_off_a_factory_branch_waits_for_a_person(merging: Merging) -> None:
    m = merging
    gh_pr = m.new_pr(9650)
    gh_pr.head_ref = "feature/a-humans-work"
    # An agent reported it (sys_factory_pr): tracked, with no branch check.
    m.store.upsert_pr(
        m.fid, REPO, 9650, project_slug="api-version", head_ref=gh_pr.head_ref, source="tool"
    )
    await m.poll()
    row = m.pr(9650)
    assert row.ready is True and row.head_ref == "feature/a-humans-work"
    queries = len(m.gh.pr_queries)

    assert await m.merger.run_once() == []

    assert m.merges() == [] and len(m.gh.pr_queries) == queries
    assert m.pr_views()[0]["auto_merge"] is False
    assert [a["kind"] for a in m.asks()["asks"]] == ["merge"]
    [ready] = [t for t in m.deliver.texts_to(m.pl) if "READY FOR HUMAN MERGE" in t]
    assert "A human merges it on GitHub" in ready


async def test_a_forks_head_never_merges_and_owners_hear_of_it(merging: Merging) -> None:
    m = merging
    m.new_pr(head_repo="someone/sample-app")
    await m.poll()
    row = m.pr()
    assert row.ready is True

    assert await m.merger.run_once() == []

    assert m.merges() == []
    assert "never merges a fork's branch" in m.merger.waiting[row.id]
    [incident] = m.incidents()
    assert incident.target == f"pr:{row.id}" and incident.assignee == issues.ASSIGN_OWNERS
    assert incident.project_slug == "api-version" and "someone/sample-app" in incident.summary
    asks = m.asks()
    assert [a["kind"] for a in asks["asks"]] == ["broken"] and asks["handled"] == []
    # Not read again while its row stays the same; raised once.
    queries = len(m.gh.pr_queries)
    m.clock.advance(POLL_S)
    assert await m.merger.run_once() == []
    assert len(m.gh.pr_queries) == queries and m.incidents()[0].count == 1


async def test_an_outsiders_marker_never_decides_a_merge(merging: Merging) -> None:
    m = merging
    gh_pr = m.new_pr()
    head = gh_pr.head_sha
    # A member's FAIL, then a drive-by PASS: the poller's gate reads the newest.
    gh_pr.comment(f"<!-- factory:verify sha={head} result=FAIL -->", m.clock.now + 40)
    gh_pr.comment(
        f"<!-- factory:verify sha={head} result=PASS -->",
        m.clock.now + 50,
        login="drive-by",
        association="NONE",
    )
    await m.poll()
    row = m.pr()
    assert row.ready is True

    assert await m.merger.run_once() == []

    assert m.merges() == []
    assert "verify marker that passes the head is not by a repo owner" in m.merger.waiting[row.id]
    assert len(m.incidents()) == 1
    # The owner's own login (the one the agents act as) counts whatever GitHub calls it.
    gh_pr.comment(
        f"<!-- factory:verify sha={head} result=PASS -->",
        m.clock.now + 5,
        login="Lena",
        association="CONTRIBUTOR",
    )
    await m.poll()
    assert await m.merger.run_once() == [row.id]
    assert m.incidents() == []


async def test_a_marker_posted_after_the_poll_is_read_before_a_merge(merging: Merging) -> None:
    m = merging
    gh_pr = m.new_pr()
    await m.poll()
    row = m.pr()
    gh_pr.comment(f"<!-- factory:verify sha={gh_pr.head_sha} result=FAIL -->", m.clock.now + 1)

    assert await m.merger.run_once() == []

    assert m.merges() == []
    assert m.merger.waiting[row.id] == "changed on GitHub since the factory last read it"
    # The poller reads it again at once, and it is no longer ready.
    assert m.pr().last_polled_at is None
    await m.poll(advance=2)
    assert m.pr().ready is False
    assert await m.merger.run_once() == []
    assert m.incidents() == []
    # Even when GitHub's updatedAt lags, the gates are re-run on what it reports.
    gh_pr.updated_at = gh_pr.created_at
    assert auto_merge.github_refusal(row, gh_pr.graphql(), frozenset({(REPO, BASE)})) == (
        auto_merge.Refusal(
            "the verify gate is fail on GitHub now: latest verify marker for "
            f"{gh_pr.head_sha} says FAIL",
            refresh=True,
        )
    )


def test_checks_of_another_commit_than_the_head_never_count() -> None:
    head = sha("head")
    gh_pr = FakePr(REPO, 1, "factory/status/t", head, 1_700_000_000.0, base_ref=BASE)
    gh_pr.checks = [FakeCheck("ci / test")]
    node = gh_pr.graphql()
    node["head"]["nodes"][0]["commit"]["oid"] = sha("other")
    row = replace(_row(head), last_polled_at=1_800_000_000)

    refusal = auto_merge.github_refusal(row, node, frozenset({(REPO, BASE)}))

    assert refusal == auto_merge.Refusal(
        "GitHub reported the checks of another commit than the head"
    )


def _row(head: str) -> Any:
    from omnigent.entities.factory import FactoryPr

    return FactoryPr(
        id="pr_1",
        factory_id="fac_1",
        repo=REPO,
        number=1,
        url=f"https://github.com/{REPO}/pull/1",
        head_ref="factory/status/t",
        base_ref=BASE,
        head_sha=head,
        project_slug="status",
        ready=True,
    )


# ── Nothing waits silently ───────────────────────────────────────────────────


async def test_an_optional_failing_check_is_a_needs_you_row_until_it_merges(
    merging: Merging,
) -> None:
    m = merging
    gh_pr = m.new_pr(
        checks=[FakeCheck("ci / test"), FakeCheck("lint", conclusion="FAILURE", required=False)]
    )
    await m.poll()
    row = m.pr()
    # The factory's checks gate counts required checks; the merger counts every one.
    assert row.ready is True
    assert m.asks()["handled"] == [
        "PR #9630 (factory change) merges automatically when checks pass."
    ]

    assert await m.merger.run_once() == []

    [incident] = m.incidents()
    assert "checks not green: lint (failure)" in incident.summary
    assert "merge or close it on GitHub" in incident.summary
    asks = m.asks()
    assert [a["kind"] for a in asks["asks"]] == ["broken"] and asks["handled"] == []
    # Someone re-runs it green: a later pass merges it and the incident clears.
    gh_pr.checks = [FakeCheck("ci / test"), FakeCheck("lint", required=False)]
    m.clock.advance(QUIET_S + 1)
    assert await m.merger.run_once() == [row.id]
    assert m.incidents() == []


async def test_any_long_wait_raises_its_incident_once(merging: Merging) -> None:
    m = merging
    m.new_pr(checks=[FakeCheck("ci / test"), FakeCheck("e2e", "QUEUED", required=False)])
    await m.poll()

    assert await m.merger.run_once() == []
    assert m.incidents() == []  # still running: no alarm yet

    m.clock.advance(WAIT_INCIDENT_S)
    assert await m.merger.run_once() == []
    [incident] = m.incidents()
    assert "checks still running: e2e" in incident.summary
    m.clock.advance(POLL_S)
    assert await m.merger.run_once() == []
    assert m.incidents()[0].count == 1


async def test_github_not_connected_is_an_incident(merging: Merging) -> None:
    m = merging
    m.new_pr()
    await m.poll()
    m.world.app_state.github_store = FakeGithubStore({})

    assert await m.merger.run_once() == []

    [incident] = m.incidents()
    assert "GitHub is not connected for the factory owner" in incident.summary


async def test_with_the_merger_off_a_person_merges_as_before(merging: Merging) -> None:
    m = merging
    off = AutoMerger(m.world.app_state, clock=m.clock, interval_s=0)
    setattr(m.world.app_state, MERGER_STATE_ATTR, off)
    m.new_pr()
    await m.poll()

    [ready] = [t for t in m.deliver.texts_to(m.pl) if "READY FOR HUMAN MERGE" in t]
    assert "A human merges it on GitHub" in ready
    assert m.pr_views()[0]["auto_merge"] is False
    assert [a["kind"] for a in m.asks()["asks"]] == ["merge"]
    assert await off.run_once() == []


async def test_a_merge_incident_clears_once_the_pr_closes(merging: Merging) -> None:
    m = merging
    gh_pr = m.new_pr()
    await m.poll()
    m.gh.merge_status = 409
    assert await m.merger.run_once() == []
    assert len(m.incidents(INCIDENT_RULE)) == 1

    gh_pr.state = "CLOSED"
    gh_pr.closed_at = m.clock.now + 1
    gh_pr.closed_by = "lena"
    await m.poll()

    assert m.pr().state == "closed"
    assert m.incidents(INCIDENT_RULE) == []


async def test_a_merge_incident_clears_once_targets_go_or_the_head_moves(
    merging: Merging,
) -> None:
    m = merging
    gh_pr = m.new_pr()
    await m.poll()
    m.gh.merge_status = 405
    assert await m.merger.run_once() == []
    [failed] = m.incidents(INCIDENT_RULE)

    definition.set_values(m.world.deps, m.fid, {PATH: []}, created_by="alice")
    assert await m.merger.run_once() == []
    assert m.incidents(INCIDENT_RULE) == []
    stored = m.store.get_issue_by_dedupe(m.fid, failed.dedupe_key)
    assert stored.status == "resolved"

    # Back on; it fails again, then a new head is ready: the old failure clears
    # and the new head is tried at once (no back-off carried over).
    definition.set_values(m.world.deps, m.fid, {PATH: TARGETS}, created_by="alice")
    m.clock.advance(BACKOFF_START_S + 1)
    assert await m.merger.run_once() == []
    assert len(m.incidents(INCIDENT_RULE)) == 1
    m.gh.merge_status = None
    new_head = sha("second")
    gh_pr.push(new_head, m.clock.now + 1, checks=[FakeCheck("ci / test")])
    gh_pr.review(f"<!-- factory:review sha={new_head} verdict=APPROVE -->", m.clock.now + 2)
    gh_pr.comment(f"<!-- factory:verify sha={new_head} result=PASS env=x -->", m.clock.now + 2)
    await m.poll()
    assert m.pr().head_sha == new_head and m.pr().ready is True
    assert await m.merger.run_once() == [m.pr().id]
    assert m.incidents(INCIDENT_RULE) == []


# ── The owner's brakes hold merges ───────────────────────────────────────────


@pytest.mark.parametrize("brake", ["factory paused", "project paused", "project cancelled"])
async def test_the_owners_brakes_hold_merges(merging: Merging, brake: str) -> None:
    m = merging
    m.new_pr()
    await m.poll()
    conversations = m.world.deps.conversation_store
    if brake == "factory paused":
        conversations.set_labels(m.fid, {LABEL_STATE: STATE_PAUSED})
    elif brake == "project paused":
        conversations.set_labels(m.pl, {LABEL_STATE: STATE_PAUSED})
    else:
        m.store.upsert_project(m.fid, "api-version", status="cancelled")

    assert await m.merger.run_once() == []

    assert m.merges() == []
    assert m.incidents() == []
    if brake == "project cancelled":
        # Nobody merges a cancelled project's PR automatically: a person decides.
        assert m.pr_views()[0]["auto_merge"] is False
        return
    assert m.merger.waiting[m.pr().id] == brake
    conversations.set_labels(
        m.fid if brake == "factory paused" else m.pl, {LABEL_STATE: STATE_ACTIVE}
    )
    assert await m.merger.run_once() == [m.pr().id]


@pytest.mark.parametrize("change", ["targets cleared", "change request", "factory paused"])
async def test_a_change_during_the_read_stops_the_merge(merging: Merging, change: str) -> None:
    m = merging
    m.new_pr()
    await m.poll()
    row = m.pr()
    real = m.gh.client().graphql

    async def read_then_change(*args: Any, **kwargs: Any) -> Any:
        result = await real(*args, **kwargs)
        if change == "targets cleared":
            definition.set_values(m.world.deps, m.fid, {PATH: []}, created_by="alice")
        elif change == "change request":
            m.store.update_pr(
                row.id,
                human_review={
                    "verdict": "changes_requested",
                    "head_sha": row.head_sha,
                    "by": "alice",
                    "text": "not yet (sample)",
                },
            )
        else:
            m.world.deps.conversation_store.set_labels(m.fid, {LABEL_STATE: STATE_PAUSED})
        return result

    client = m.gh.client()
    client.graphql = read_then_change  # type: ignore[method-assign]
    m.world.app_state.github_client = client

    assert await m.merger.run_once() == []

    assert m.merges() == []


async def test_the_poller_nudges_the_merger_only_when_a_target_pr_turns_ready(
    merging: Merging,
) -> None:
    m = merging
    m.new_pr()
    await m.poll()
    assert await nudge.wait(auto_merge.NUDGE_TOPIC, 0) == {m.pr().id}

    await m.poll()  # nothing changed on GitHub

    assert await nudge.wait(auto_merge.NUDGE_TOPIC, 0) is None
