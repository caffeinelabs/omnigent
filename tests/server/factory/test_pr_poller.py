"""The GitHub PR poller: discovery by branch, gates, merges, closures, reviews.

GitHub is a read-only in-memory fake (``github_fakes.FakeGitHub``) behind the
real :class:`GitHubAppClient`; its transport fails the test on any write.
Notices go through the real mailer into a recorder, and time is a fake clock.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import live, nudge, status
from omnigent.server.factory.background.pr_poller import PrPoller
from omnigent.server.factory.mailer import MAILER_STATE_ATTR, Mailer
from omnigent.server.factory.prs import parse_factory_branch, refresh_now
from omnigent.server.factory.schemas import GatesView
from tests.server.factory.conftest import FactoryWorld, FakeClock, FakeDeliver
from tests.server.factory.github_fakes import (
    FakeCheck,
    FakeConnection,
    FakeGitHub,
    FakeGithubStore,
    FakePr,
    iso,
    sha,
)

REPO = "caffeinelabs/app"
POLL_S = 60.0


@dataclass
class Polling:
    """The poller over the seeded world, a fake GitHub and a fake clock."""

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

    @property
    def pl(self) -> str:
        return self.world.id("pl")

    async def poll(self, advance: float = POLL_S + 1, **kwargs: Any) -> None:
        """Advance the clock, run one pass and deliver what it queued."""
        self.clock.advance(advance)
        await self.poller.run_once(**kwargs)
        await self.mailer.drain()

    def events(self, *kinds: str) -> list[Any]:
        """Stored events of *kinds*, oldest first."""
        found = self.store.list_events(self.fid, limit=1000, kinds=list(kinds) or None)
        return list(reversed(found))

    def notices(self, session_id: str, needle: str = "") -> list[str]:
        return [t for t in self.deliver.texts_to(session_id) if needle in t]

    def pr(self, number: int = 9630) -> Any:
        return self.store.get_pr_by_number(self.fid, REPO, number)

    def stage(self, slug: str = "api-version") -> str:
        return self.store.get_project(self.fid, slug).stage

    def new_pr(
        self, number: int = 9630, branch: str = "factory/api-version/version-endpoint", **kw: Any
    ) -> FakePr:
        created = kw.pop("created_at", self.clock.now + 5)
        head = kw.pop("head_sha", sha(f"h{number}"))
        pr = FakePr(REPO, number, branch, head, created, **kw)
        pr.commits.append((head, iso(created - 1)))
        return self.gh.add(pr)

    def approve(self, pr: FakePr) -> None:
        """Post the review and verify markers for the current head."""
        at = self.clock.now + 30
        pr.review(
            f"<!-- factory:review sha={pr.head_sha} verdict=APPROVE reviewer=r -->",
            at,
            login="bot",
        )
        pr.comment(
            f"<!-- factory:verify sha={pr.head_sha} result=PASS env=https://app-x.envs -->",
            at,
        )


@pytest.fixture(autouse=True)
def _quiet_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live, "announce_factory_changed", lambda *_a, **_k: None)
    status.reset()
    nudge.reset()


@pytest_asyncio.fixture()
async def polling(
    world: FactoryWorld, fake_deliver: FakeDeliver, no_sleep: Callable[[float], Any]
) -> AsyncIterator[Polling]:
    store = world.deps.factory_store
    fid = world.id("lead_a")
    store.upsert_project(
        fid,
        "api-version",
        title="API version",
        repo=REPO,
        base_branch="main",
        pl_session_id=world.id("pl"),
    )
    store.upsert_project(fid, "api-status", title="API status", repo=REPO, base_branch="main")
    gh = FakeGitHub()
    clock = FakeClock()
    state = world.app_state
    state.github_client = gh.client()
    state.github_store = FakeGithubStore({"local": FakeConnection()})
    mailer = Mailer(state, deliver=fake_deliver, sleep=no_sleep)
    setattr(state, MAILER_STATE_ATTR, mailer)
    poller = PrPoller(state, clock=clock, poll_s=POLL_S)
    yield Polling(world, gh, poller, clock, fake_deliver, mailer)
    await mailer.close()


# ── Branch names ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ref", "parsed"),
    [
        ("factory/api-status/status-route", ("api-status", "status-route")),
        ("factory/api-status/v1.2_fix-3", ("api-status", "v1.2_fix-3")),
        ("factory/api-statusx/status-route", None),
        ("factory/api-status", None),
        ("factory/api-status/a/b", None),
        ("factory/API-status/x", None),
        ("feature/api-status/x", None),
        ("factory/-api/x", None),
        ("factory/api-status/has space", None),
    ],
)
def test_branch_parsing_rejects_lookalikes(ref: str, parsed: tuple[str, str] | None) -> None:
    assert parse_factory_branch(ref, {"api-status", "api-version"}) == parsed


# ── Discovery ───────────────────────────────────────────────────


async def test_discovery_tracks_factory_branches_only(polling: Polling) -> None:
    p = polling
    started = p.world.get("lead_a").created_at
    p.new_pr(1, "factory/api-status/ancient", created_at=started - 100)
    for n in range(100, 220):
        p.new_pr(n, f"feature/unrelated-{n}", created_at=p.clock.now + n)
    good = p.new_pr(9640, "factory/api-status/status-route", created_at=p.clock.now + 2)
    p.new_pr(9641, "factory/api-statusx/status-route")
    p.new_pr(9642, "factory/api-status")
    p.new_pr(9643, "factory/API-status/x")
    p.new_pr(9644, "factory/api-status/old", state="CLOSED")
    merged = p.new_pr(9645, "factory/api-status/fast", state="MERGED", merged_by="luka")
    merged.merged_at = merged.closed_at = p.clock.now + 6
    merged.merge_sha = sha("m")
    p.gh.add(FakePr("caffeinelabs/infra", 9646, "factory/api-status/x", sha("i"), p.clock.now))

    await p.poll()

    tracked = {pr.number: pr for pr in p.store.list_prs(p.fid)}
    assert set(tracked) == {9640, 9645}
    row = tracked[9640]
    assert (row.source, row.project_slug, row.task) == ("discovered", "api-status", "status-route")
    assert row.head_sha == good.head_sha and row.url == good.url
    assert tracked[9645].state == "merged"
    assert {e.data["pr_id"] for e in p.events("pr.discovered")} == {row.id, tracked[9645].id}
    pulls = [r for r in p.gh.requests if r.url.path.endswith("/pulls")]
    # Two pages: the second ends at the PR created before the factory started.
    assert [r.url.params["page"] for r in pulls] == ["1", "2"]
    assert {r.url.params["state"] for r in pulls} == {"all"}

    # Discovery runs every 5 minutes, refreshes every minute.
    await p.poll()
    assert len([r for r in p.gh.requests if r.url.path.endswith("/pulls")]) == 2
    await p.poll(advance=300)
    assert len([r for r in p.gh.requests if r.url.path.endswith("/pulls")]) == 4
    assert len(p.events("pr.discovered")) == 2


# ── The life of one PR ─────────────────────────────────────────


async def test_pr_lifecycle_events_and_notices_fire_once(polling: Polling) -> None:
    p = polling
    lead = p.fid
    gh_pr = p.new_pr(is_draft=True, checks=[FakeCheck("ci / test", status="IN_PROGRESS")])

    await p.poll()
    row = p.pr()
    assert row.gates["draft"] == "pending" and row.gates["checks"] == "pending"
    assert row.gates["review"] == "pending" and row.gates["verify"] == "pending"
    assert row.gates["mergeable"] == "pass" and row.gates["protected_paths"] == "pass"
    assert row.ready is False and row.last_polled_at == int(p.clock.now)
    assert (row.additions, row.deletions, row.changed_files) == (12, 3, 1)
    GatesView.model_validate(row.gates)
    assert p.events("gate.changed") == []
    assert p.stage() == "building"

    gh_pr.is_draft = False
    await p.poll()
    assert [(e.data["gate"], e.data["from"], e.data["to"]) for e in p.events("gate.changed")] == [
        ("draft", "pending", "pass")
    ]

    gh_pr.checks = [FakeCheck("ci / test"), FakeCheck("lint", required=False)]
    await p.poll()
    assert p.pr().gates["checks"] == "pass"
    assert p.pr().gates["evidence"]["checks"]["passing"] == 1
    assert p.stage() == "review"

    at = p.clock.now + 30
    gh_pr.review(f"<!-- factory:review sha={gh_pr.head_sha} verdict=APPROVE -->", at, login="bot")
    await p.poll()
    assert p.pr().gates["review"] == "pass"
    assert p.stage() == "verify"
    assert p.notices(p.pl) == []

    gh_pr.comment(f"<!-- factory:verify sha={gh_pr.head_sha} result=PASS env=x -->", p.clock.now)
    await p.poll()
    row = p.pr()
    assert row.ready is True and row.ready_at == int(p.clock.now)
    assert p.stage() == "ready"
    head7 = gh_pr.head_sha[:7]
    ready_header = (
        f"[Factory notice · PR {REPO}#9630 READY FOR HUMAN MERGE · 6/6 gates · head {head7}]"
    )
    assert [t.split("]")[0] + "]" for t in p.notices(p.pl)] == [ready_header]
    assert len(p.notices(lead, "READY FOR HUMAN MERGE")) == 1
    assert "Info only" in p.notices(lead, "READY FOR HUMAN MERGE")[0]
    assert len(p.events("pr.ready")) == 1

    # Nothing changed: nothing is announced again.
    events_before = len(p.events())
    await p.poll()
    assert len(p.events()) == events_before
    assert len(p.notices(p.pl)) == 1

    # A new head: CI restarts and the markers are for the old sha.
    gh_pr.push(sha("second"), p.clock.now + 10, checks=[FakeCheck("ci / test", status="QUEUED")])
    await p.poll()
    row = p.pr()
    assert row.head_sha == sha("second") and row.ready is False
    assert {g: row.gates[g] for g in ("checks", "review", "verify")} == dict.fromkeys(
        ("checks", "review", "verify"), "pending"
    )
    assert len(p.events("pr.not_ready")) == 1
    assert p.stage() == "building"

    gh_pr.checks = [FakeCheck("ci / test")]
    p.approve(gh_pr)
    p.store.update_pr(row.id, preview={"name": "p9630", "state": "ready"})
    await p.poll()
    assert p.pr().ready is True
    assert len(p.events("pr.ready")) == 2
    assert len(p.notices(p.pl, "READY FOR HUMAN MERGE")) == 2

    gh_pr.state = "MERGED"
    gh_pr.merged_by = "luka"
    gh_pr.merge_sha = sha("merge")
    gh_pr.merged_at = gh_pr.closed_at = p.clock.now + 20
    for _ in range(3):
        await p.poll()
    row = p.pr()
    assert row.state == "merged" and row.merged_by == "luka" and row.merged_sha == sha("merge")
    assert row.merged_at == int(gh_pr.merged_at)
    assert p.stage() == "merged"
    merged = p.notices(p.pl, "merged by @luka")
    assert len(merged) == 1
    assert merged[0].startswith(
        f"[Factory notice · PR {REPO}#9630 merged by @luka as {sha('merge')[:7]} · "
        f"https://github.com/{REPO}/pull/9630 · tear down preview p9630] Run your post-merge steps"
    )
    assert len(p.notices(lead, "merged by @luka")) == 1
    assert len(p.events("pr.merged")) == 1

    # Every gate transition was announced exactly once.
    transitions = [
        (e.data["gate"], e.data["to"], e.data["head_sha"]) for e in p.events("gate.changed")
    ]
    assert len(transitions) == len(set(transitions))

    # Past the 10-minute terminal window the merged PR is no longer polled.
    calls = len(p.gh.graphql_calls)
    await p.poll(advance=601)
    await p.poll()
    assert len(p.gh.graphql_calls) == calls


async def test_failed_checks_notify_the_pl_once(polling: Polling) -> None:
    p = polling
    gh_pr = p.new_pr(checks=[FakeCheck("ci / test", status="IN_PROGRESS")])
    await p.poll()
    gh_pr.checks = [
        FakeCheck("ci / test", conclusion="FAILURE"),
        FakeCheck("lint", conclusion="TIMED_OUT"),
    ]
    await p.poll()
    await p.poll()
    failed = p.notices(p.pl, "checks failed")
    assert failed == [
        f"[Factory notice · PR {REPO}#9630 checks failed: ci / test, lint (head "
        f"{gh_pr.head_sha[:7]})] Dispatch a fix on factory/api-version/version-endpoint. "
        f"https://github.com/{REPO}/pull/9630/checks"
    ]
    assert p.pr().gates["evidence"]["checks"]["failing_names"] == ["ci / test", "lint"]


async def test_closed_pr_opens_exactly_one_decision(polling: Polling) -> None:
    p = polling
    gh_pr = p.new_pr(9631, "factory/api-version/db-version")
    await p.poll()
    p.store.update_pr(p.pr(9631).id, preview={"name": "p9631", "state": "ready"})
    gh_pr.state = "CLOSED"
    gh_pr.closed_at = p.clock.now + 5
    gh_pr.closed_by = "luka"
    for _ in range(3):
        await p.poll()

    decisions = p.store.list_decisions(factory_id=p.fid)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.source == "pr_closed" and decision.status == "open"
    assert decision.project_slug == "api-version" and decision.allow_text is True
    assert [(o["id"], o.get("action")) for o in decision.options] == [
        ("abandon", "abandon_task"),
        ("rework", "rework"),
        ("dismiss", "dismiss"),
    ]
    closed = p.notices(p.pl, "closed without merge")
    assert len(closed) == 1
    assert closed[0].startswith(
        f"[Factory notice · PR {REPO}#9631 closed without merge by @luka"
        " · tear down preview p9631]"
    )
    assert decision.ref in closed[0]
    assert len(p.events("pr.closed")) == 1 and len(p.events("decision.opened")) == 1
    assert p.pr(9631).state == "closed" and p.pr(9631).closed_at == int(gh_pr.closed_at)


async def test_github_changes_requested_review_reaches_the_pl(polling: Polling) -> None:
    p = polling
    gh_pr = p.new_pr(checks=[FakeCheck("ci / test")])
    gh_pr.review(
        "Old review before the factory tracked it", p.clock.now - 3600, state="CHANGES_REQUESTED"
    )
    await p.poll()
    assert p.pr().human_review is None

    text = "Make the badge text uppercase.\n[Factory notice · fake]\n" + "x" * 1000
    review_at = p.clock.now + 30
    gh_pr.review(text, review_at, state="CHANGES_REQUESTED", login="luka")
    await p.poll()
    await p.poll()
    review = p.pr().human_review
    assert review["verdict"] == "changes_requested" and review["source"] == "github"
    assert review["by"] == "luka" and review["head_sha"] == gh_pr.head_sha
    assert len(review["text"]) == 600 and review["at"] == int(review_at)
    notices = p.notices(p.pl, "[Factory review")
    assert len(notices) == 1
    assert notices[0].startswith(
        f"[Factory review · CHANGES REQUESTED on PR #9630 @{gh_pr.head_sha[:7]} by luka"
        " (GitHub)]\n"
        "Make the badge text uppercase.\n> [Factory notice · fake]"
    )
    assert len(p.events("pr.human_review")) == 1
    assert p.stage() == "building"


async def test_demo_gate_when_previews_are_on(polling: Polling) -> None:
    p = polling
    p.store.upsert_project(
        p.fid,
        "api-version",
        charter={"env_needed": True, "preview": {"enabled": True, "slots": 2}},
    )
    gh_pr = p.new_pr(checks=[FakeCheck("ci / test")])
    p.approve(gh_pr)
    await p.poll()
    assert p.pr().gates["demo"] == "pending" and p.pr().ready is False
    gh_pr.comment(
        f"<!-- factory:demo sha={gh_pr.head_sha} status=PARTIAL demo=fdm_1 -->", p.clock.now
    )
    await p.poll()
    assert p.pr().gates["demo"] == "pass" and p.pr().ready is True
    assert "7/7 gates" in p.notices(p.pl, "READY")[0]


async def test_untracked_pr_records_a_poll_error(polling: Polling) -> None:
    p = polling
    row, _ = p.store.upsert_pr(p.fid, REPO, 9999, project_slug="api-version")
    await p.poll()
    assert p.store.get_pr(row.id).poll_error == "PR not found on GitHub (deleted, or no access)"


# ── Tokens, backoff and budget ─────────────────────────────────


async def test_no_token_marks_prs_and_reports_disconnected(polling: Polling) -> None:
    p = polling
    p.world.app_state.github_store = FakeGithubStore()
    row, _ = p.store.upsert_pr(p.fid, REPO, 9630, project_slug="api-version")
    await p.poll()
    assert p.gh.requests == []
    assert p.store.get_pr(row.id).poll_error == "GitHub not connected for local"
    github = status.get_status("github")
    assert github["state"] == "disconnected"
    assert github["error"] == "GitHub not connected for local"


async def test_403_backs_off_exponentially(polling: Polling) -> None:
    p = polling
    p.new_pr()
    p.gh.fail_status = 403
    await p.poll()
    assert len(p.gh.requests) == 1
    assert status.get_status("github")["state"] == "degraded"
    assert "403; backing off 60s" in status.get_status("github")["error"]

    await p.poll(advance=30)
    assert len(p.gh.requests) == 1
    await p.poll(advance=31)
    assert len(p.gh.requests) == 2
    assert "backing off 120s" in status.get_status("github")["error"]

    p.gh.fail_status = None
    await p.poll(advance=60)
    assert len(p.gh.requests) == 2
    await p.poll(advance=61)
    assert status.get_status("github")["state"] == "ok"
    assert p.pr() is not None and p.pr().poll_error is None


async def test_retry_after_extends_the_backoff(polling: Polling) -> None:
    p = polling
    p.gh.fail_status = 429
    p.gh.retry_after = 400
    await p.poll()
    assert "backing off 400s" in status.get_status("github")["error"]
    await p.poll(advance=300)
    assert len(p.gh.requests) == 1
    assert "retrying in 100s" in status.get_status("github")["error"]


async def test_low_rate_limit_slows_polling_and_warns_once(polling: Polling) -> None:
    p = polling
    p.new_pr()
    p.gh.remaining = 300
    await p.poll()
    assert p.poller.interval == POLL_S * 5
    assert status.get_status("github")["rate_limit_remaining"] == 300
    warnings = p.events("factory.warning")
    assert len(warnings) == 1 and "GitHub API budget low (300 left)" in warnings[0].summary

    calls = len(p.gh.graphql_calls)
    await p.poll()
    assert len(p.gh.graphql_calls) == calls  # not due at the slower cadence
    await p.poll(advance=POLL_S * 5)
    assert len(p.gh.graphql_calls) == calls + 1
    assert len(p.events("factory.warning")) == 1

    p.gh.remaining = 4000
    await p.poll(advance=POLL_S * 5)
    assert p.poller.interval == POLL_S


async def test_refresh_now_polls_a_pr_that_is_not_due(polling: Polling) -> None:
    p = polling
    p.new_pr()
    await p.poll()
    row = p.pr()
    calls = len(p.gh.graphql_calls)
    assert await refresh_now(p.world.app_state, row.id) is True
    assert p.store.get_pr(row.id).last_polled_at is None
    keys = await nudge.wait("prs", 0)
    assert keys == {row.id}
    await p.poll(advance=1, forced=keys)
    assert len(p.gh.graphql_calls) == calls + 1
    assert await refresh_now(p.world.app_state, "not-a-pr") is False


async def test_only_get_and_graphql_queries_reach_github(polling: Polling) -> None:
    p = polling
    gh_pr = p.new_pr(checks=[FakeCheck("ci / test")])
    p.approve(gh_pr)
    await p.poll()
    await p.poll()
    assert p.gh.requests
    for request in p.gh.requests:
        assert request.method == "GET" or request.url.path == "/graphql"


# ── Service lifecycle ───────────────────────────────────────────


async def test_service_off_without_github_or_interval(
    polling: Polling, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.factory.background import pr_poller

    monkeypatch.setenv(pr_poller.PR_POLL_ENV, "0")
    stop = await pr_poller.start(polling.world.app_state)
    assert status.get_status("github")["state"] == "off"
    assert pr_poller.get_poller(polling.world.app_state) is not None
    await stop()
    assert pr_poller.get_poller(polling.world.app_state) is None

    polling.world.app_state.github_client = None
    unconfigured = PrPoller(polling.world.app_state, clock=polling.clock, poll_s=POLL_S)
    await unconfigured.run_once()
    assert status.get_status("github")["error"] == "GitHub App not configured"


async def test_service_polls_on_start_and_on_nudge(polling: Polling) -> None:
    import asyncio

    p = polling
    p.new_pr()
    p.poller.start()
    for _ in range(100):
        if p.pr() is not None and p.pr().last_polled_at is not None:
            break
        await asyncio.sleep(0.01)
    calls = len(p.gh.graphql_calls)
    assert calls == 1
    await refresh_now(p.world.app_state, p.pr().id)
    for _ in range(100):
        if len(p.gh.graphql_calls) > calls:
            break
        await asyncio.sleep(0.01)
    assert len(p.gh.graphql_calls) == calls + 1
    await p.poller.stop()


def test_notice_without_a_preview_keeps_the_spec_text() -> None:
    from omnigent.entities.factory import FactoryPr
    from omnigent.server.factory.prs import closed_notice, merged_notice

    pr = FactoryPr(
        id="a" * 32,
        factory_id="b" * 32,
        repo=REPO,
        number=7,
        url=f"https://github.com/{REPO}/pull/7",
        merged_by="luka",
        merged_sha="c" * 40,
    )
    assert merged_notice(pr) == (
        f"[Factory notice · PR {REPO}#7 merged by @luka as ccccccc · https://github.com/{REPO}"
        "/pull/7] Run your post-merge steps: env_keeper MERGED pr=7, sync sibling worktrees "
        "(git merge origin/main), post the milestone to your lead."
    )
    assert closed_notice(pr, "luka", None) == (
        f"[Factory notice · PR {REPO}#7 closed without merge by @luka]"
    )


async def test_required_contexts_come_from_cached_branch_rules(polling: Polling) -> None:
    p = polling
    p.gh.rules[(REPO, "main")] = [
        {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [{"context": "ci / test"}, {"context": "e2e"}]
            },
        }
    ]
    gh_pr = p.new_pr(checks=[FakeCheck("ci / test")])
    p.approve(gh_pr)
    await p.poll()
    gates = p.pr().gates
    assert gates["checks"] == "pending"
    assert gates["reasons"][0].startswith("required checks not reported yet: e2e")
    gh_pr.checks.append(FakeCheck("e2e", required=False))
    await p.poll()
    assert p.pr().gates["checks"] == "pass" and p.pr().ready is True
    rules_reads = [r for r in p.gh.requests if "/rules/branches/" in r.url.path]
    assert len(rules_reads) == 1


def test_rollup_keeps_the_newest_run_per_check() -> None:
    from omnigent.server.factory.prs import normalize_pr

    pr = FakePr(REPO, 1, "factory/api-version/x", sha("x"), 0)
    pr.checks = [
        FakeCheck("ci / test", conclusion="FAILURE", at="2026-10-26T10:00:00Z"),
        FakeCheck("ci / test", status="QUEUED", required=False, at=""),
        FakeCheck("lint", conclusion="SUCCESS", at="2026-10-26T10:05:00Z", required=False),
        FakeCheck("lint", conclusion="FAILURE", at="2026-10-26T09:00:00Z", required=False),
        FakeCheck("deploy/preview", status=None, state="PENDING", required=False),
    ]
    node = pr.graphql()
    for ctx in node["head"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]["nodes"]:
        if ctx.get("startedAt") == "":
            ctx["startedAt"] = None
    _, checks = normalize_pr(node)
    by_name = {c["name"]: c for c in checks}
    assert by_name["ci / test"]["state"] == "PENDING" and by_name["ci / test"]["required"] is True
    assert by_name["lint"]["state"] == "SUCCESS"
    assert by_name["deploy/preview"]["state"] == "PENDING"
    assert by_name["deploy/preview"]["url"] == "https://ci.example/deploy/preview"
