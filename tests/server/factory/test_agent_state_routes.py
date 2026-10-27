"""Runner-facing cockpit routes: decisions, PRs, envs, stage claims and charters.

Agents report through these routes; the cockpit renders what they write. The
tests seed the shared factory world (Atlas + Sentinel, one PL with its team)
with a project row for ``api-version`` on caffeinelabs/app, then check that
each route writes the right row, scopes writes to the caller's factory and
project, and refuses what it should with a stable error code.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.factory import nudge, schemas, status
from omnigent.server.factory.api import agent_state
from omnigent.server.factory.context import factory_deps
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.factory_store import FactoryStore
from tests.server.factory.conftest import FactoryApi, FactoryWorld, _native, _register_agent
from tests.server.helpers import build_agent_bundle

PROJECT = "api-version"
REPO = "caffeinelabs/app"
PR_URL = f"https://github.com/{REPO}/pull/9630"
SHA = "7767f0d1" + "b" * 32
APP = "https://app-1a2b-p9630.envs.sandbox.caffeinelabs.io"
API = "https://api-1a2b-p9630.envs.sandbox.caffeinelabs.io"


@dataclass
class Cockpit:
    """The factory routes over the seeded world, plus the cockpit store."""

    api: FactoryApi
    world: FactoryWorld
    store: FactoryStore

    @property
    def factory_id(self) -> str:
        return self.world.id("lead_a")

    def id(self, key: str) -> str:
        return self.world.id(key)

    async def ask(self, key: str, question: str, **extra: Any) -> Any:
        return await self.api.client.post(
            "/v1/factory/agent/decisions",
            json={"from_session_id": self.id(key), "question": question, **extra},
        )

    async def pr(self, key: str, url: str = PR_URL, **extra: Any) -> Any:
        return await self.api.client.post(
            "/v1/factory/agent/prs", json={"from_session_id": self.id(key), "url": url, **extra}
        )

    async def env(self, key: str, state: str = "ready", **extra: Any) -> Any:
        body = {"from_session_id": self.id(key), "state": state, "env_id": "1a2b", **extra}
        return await self.api.client.put("/v1/factory/agent/envs", json=body)

    async def stage(self, key: str, target: str, stage: str) -> Any:
        return await self.api.client.patch(
            f"/v1/factory/sessions/{self.id(target)}",
            json={"from_session_id": self.id(key), "stage": stage},
        )

    def kinds(self) -> list[str]:
        return [e.kind for e in reversed(self.store.list_events(self.factory_id, limit=500))]


def code(resp: Any) -> str:
    return resp.json()["error"]["code"]


def add_team_roles(world: FactoryWorld, tmp_path: Path) -> None:
    """Register a PL bundle with env_keeper and demo sub-agents, and spawn both."""
    world.agents["pl_full"] = _register_agent(
        world.agent_store,
        LocalArtifactStore(str(tmp_path / "artifacts")),
        "factory-pl-full",
        build_agent_bundle(
            "factory-pl-full",
            executor=_native("project_lead"),
            include_llm=False,
            sub_agents=[
                {"name": "env_keeper", "description": "env", "executor": _native("env_keeper")},
                {"name": "demo", "description": "demo", "executor": _native("demo")},
            ],
        ),
    )
    for name in ("env_keeper", "demo"):
        world.make_session(
            name, agent="pl_full", title=f"{name}:t1", parent="pl", sub_agent_name=name
        )


@pytest_asyncio.fixture()
async def cockpit(world: FactoryWorld, api: FactoryApi, tmp_path: Path) -> AsyncIterator[Cockpit]:
    """The seeded world with a stored project row and the full PL team."""
    add_team_roles(world, tmp_path)
    store = factory_deps(api.app.state).factory_store
    store.upsert_project(
        world.id("lead_a"),
        PROJECT,
        title="API version endpoint",
        repo=REPO,
        base_branch="main",
        charter={"goal": "GET /version returns the sha", "dod": ["curl /version"]},
        pl_session_id=world.id("pl"),
    )
    nudge.reset()
    status.reset()
    yield Cockpit(api=api, world=world, store=store)
    nudge.reset()
    status.reset()


# ── Caller binding ─────────────────────────────────────────────


async def test_a_foreign_tunnel_token_is_sender_not_bound(cockpit: Cockpit) -> None:
    headers = {RUNNER_TUNNEL_TOKEN_HEADER: "tok-someone-else"}
    pl = cockpit.id("pl")
    client = cockpit.api.client
    calls = [
        client.post(
            "/v1/factory/agent/decisions",
            json={"from_session_id": pl, "question": "q"},
            headers=headers,
        ),
        client.post(
            "/v1/factory/agent/prs", json={"from_session_id": pl, "url": PR_URL}, headers=headers
        ),
        client.put(
            "/v1/factory/agent/envs",
            json={"from_session_id": pl, "state": "ready", "env_id": "x"},
            headers=headers,
        ),
        client.get(
            "/v1/factory/agent/charter",
            params={"from_session_id": pl, "project": PROJECT},
            headers=headers,
        ),
        client.post(
            "/v1/factory/agent/decisions/D-1/withdraw",
            json={"from_session_id": pl},
            headers=headers,
        ),
    ]
    for resp in await asyncio.gather(*calls):
        assert resp.status_code == 403, resp.text
        assert code(resp) == "sender_not_bound"
    assert cockpit.store.list_decisions(factory_id=cockpit.factory_id) == []
    assert cockpit.store.list_prs(cockpit.factory_id) == []


async def test_non_members_are_out_of_factory(cockpit: Cockpit) -> None:
    resp = await cockpit.ask("plain", "who am I?")
    assert resp.status_code == 403
    assert code(resp) == "session_out_of_factory"


# ── Decisions ──────────────────────────────────────────────────


async def test_ask_opens_a_decision_with_options_and_an_event(cockpit: Cockpit) -> None:
    resp = await cockpit.ask(
        "pl",
        "Where should the ENV badge go?",
        options=[
            {"label": "Header right", "recommended": True},
            {"label": "Footer", "description": "quieter"},
        ],
        context="The header is busy.",
        blocking=True,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "D-1"
    assert body["status"] == "open"
    assert body["deduped"] is False
    row = cockpit.store.get_decision(body["id"])
    assert row is not None
    assert row.project_slug == PROJECT
    assert row.asked_by_session_id == cockpit.id("pl")
    assert row.options == [
        {"id": "1", "label": "Header right", "recommended": True},
        {"id": "2", "label": "Footer", "description": "quieter"},
    ]
    assert row.context == "The header is busy."
    assert row.source == "agent"
    assert cockpit.kinds() == ["decision.opened"]
    [event] = cockpit.store.list_events(cockpit.factory_id)
    assert event.severity == "warning"
    assert event.subject == "decision:D-1"


async def test_ask_dedupes_while_open(cockpit: Cockpit) -> None:
    first = (await cockpit.ask("pl", "Header or footer?")).json()
    again = (await cockpit.ask("pl", "  header   OR footer? ")).json()
    keyed = (await cockpit.ask("pl", "Header or footer?", dedupe_key="badge-v2")).json()
    keyed_again = (await cockpit.ask("ic", "Different words", dedupe_key="badge-v2")).json()

    assert (first["decision"], first["deduped"]) == ("D-1", False)
    assert (again["decision"], again["deduped"]) == ("D-1", True)
    assert (keyed["decision"], keyed["deduped"]) == ("D-2", False)
    assert (keyed_again["decision"], keyed_again["deduped"]) == ("D-2", True)
    assert cockpit.kinds().count("decision.opened") == 2
    # Once answered or withdrawn, the same question opens a new decision.
    await cockpit.api.client.post(
        "/v1/factory/agent/decisions/D-1/withdraw", json={"from_session_id": cockpit.id("pl")}
    )
    reopened = (await cockpit.ask("pl", "Header or footer?")).json()
    assert (reopened["decision"], reopened["deduped"]) == ("D-3", False)


async def test_ask_limits_open_decisions_per_asker(cockpit: Cockpit) -> None:
    for n in range(F.DECISION_MAX_OPEN_PER_ASKER):
        assert (await cockpit.ask("ic", f"question {n}")).status_code == 200
    over = await cockpit.ask("ic", "one too many")
    repeat = await cockpit.ask("ic", "question 3")
    other = await cockpit.ask("reviewer", "my own question")

    assert over.status_code == 409
    assert code(over) == "too_many_open_decisions"
    assert repeat.status_code == 200 and repeat.json()["deduped"] is True
    assert other.status_code == 200


@pytest.mark.parametrize(
    "extra",
    [
        {"options": [{"label": str(i)} for i in range(6)]},
        {"options": [{"label": "x" * 61}]},
        {"options": [], "allow_text": False},
    ],
)
async def test_ask_rejects_bad_options(cockpit: Cockpit, extra: dict[str, Any]) -> None:
    resp = await cockpit.ask("pl", "pick one", **extra)
    assert resp.status_code == 400
    assert code(resp) == "invalid_options"


async def test_only_the_asker_withdraws(cockpit: Cockpit) -> None:
    await cockpit.ask("ic", "Can I skip the flaky test?")
    client = cockpit.api.client

    def withdraw(key: str, ref: str = "D-1") -> Any:
        return client.post(
            f"/v1/factory/agent/decisions/{ref}/withdraw",
            json={"from_session_id": cockpit.id(key), "reason": "fixed it"},
        )

    by_pl = await withdraw("pl")
    by_ic = await withdraw("ic")
    twice = await withdraw("ic")
    unknown = await withdraw("ic", "D-99")

    assert by_pl.status_code == 403 and code(by_pl) == "not_allowed"
    assert by_ic.status_code == 200 and by_ic.json()["status"] == "withdrawn"
    assert twice.status_code == 409 and code(twice) == "decision_not_open"
    assert unknown.status_code == 404
    assert "decision.withdrawn" in cockpit.kinds()


# ── PRs ────────────────────────────────────────────────────────


async def test_pr_registration_upserts_the_row_and_records_claims(cockpit: Cockpit) -> None:
    resp = await cockpit.pr("ic", claim="opened", task="version-endpoint")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    row = cockpit.store.get_pr(body["pr_id"])
    assert row is not None
    assert (row.repo, row.number, row.url) == (REPO, 9630, PR_URL)
    assert row.source == "tool"
    assert row.project_slug == PROJECT
    assert row.task == "version-endpoint"
    assert row.last_polled_at is None
    assert body["ready"] is False
    assert body["claim_mismatch"] is False
    assert body["refreshed"] is False
    assert cockpit.kinds() == ["pr.discovered", "pr.claim"]
    assert await nudge.wait("prs", 0.01) == {row.id}

    again = (await cockpit.pr("pl", url=PR_URL + "/", claim="ready_for_human_merge")).json()
    assert again["pr_id"] == row.id
    assert again["claim_mismatch"] is True
    assert len(cockpit.store.list_prs(cockpit.factory_id)) == 1


async def test_the_claim_event_carries_claim_mismatch(cockpit: Cockpit) -> None:
    def claims() -> list[dict[str, Any]]:
        found = cockpit.store.find_events(cockpit.factory_id, "pr.claim", 0)
        return [e.data or {} for e in found]

    first = (await cockpit.pr("pl", claim="ready_for_human_merge")).json()
    assert first["claim_mismatch"] is True
    cockpit.store.update_pr(first["pr_id"], ready=True)
    second = (await cockpit.pr("pl", claim="ready_for_human_merge")).json()
    assert second["claim_mismatch"] is False
    await cockpit.pr("ic", claim="opened")

    assert [(d["claim"], d["claim_mismatch"], d["server_ready"]) for d in claims()] == [
        ("ready_for_human_merge", True, False),
        ("ready_for_human_merge", False, True),
        ("opened", False, True),
    ]


async def test_pr_registration_returns_the_review_links(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (await cockpit.pr("pl")).json()
    assert body["review_url"] == f"http://test/factory/{cockpit.factory_id}?review=9630"
    assert body["preview_url"] == f"http://test/v1/factory/prs/{body['pr_id']}/preview"

    monkeypatch.setenv("OMNIGENT_DOMAIN", "omnigent.example.com")
    body = (await cockpit.pr("pl")).json()
    assert body["review_url"].startswith("https://omnigent.example.com/factory/")
    assert body["preview_url"].startswith("https://omnigent.example.com/v1/factory/prs/")


async def test_pr_registration_returns_the_projects_base_branch(cockpit: Cockpit) -> None:
    """Sentinel compares the PR's base with ``project_base`` from the same reply."""
    body = (await cockpit.pr("pl")).json()
    assert (body["project_base"], body["base_ref"]) == ("main", None)

    cockpit.store.upsert_project(cockpit.factory_id, PROJECT, base_branch="temp-main")
    cockpit.store.update_pr(body["pr_id"], base_ref="main")
    body = (await cockpit.pr("pl")).json()
    assert (body["project_base"], body["base_ref"]) == ("temp-main", "main")


async def test_a_pr_on_a_foreign_repo_is_refused(cockpit: Cockpit) -> None:
    foreign = await cockpit.pr("pl", url="https://github.com/caffeinelabs/infra/pull/12")
    not_pr = await cockpit.pr("pl", url="https://github.com/caffeinelabs/app/issues/12")

    assert foreign.status_code == 403 and code(foreign) == "pr_out_of_factory"
    assert not_pr.status_code == 400 and code(not_pr) == "not_a_pr_url"
    assert cockpit.store.list_prs(cockpit.factory_id) == []


async def test_a_launch_workspace_counts_before_project_rows_exist(
    world: FactoryWorld, api: FactoryApi
) -> None:
    world.store.set_labels(
        world.id("pl"), {"omnigent.sandbox.repo.0": "https://github.com/Caffeinelabs/App#main"}
    )
    resp = await api.client.post(
        "/v1/factory/agent/prs", json={"from_session_id": world.id("pl"), "url": PR_URL}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["project"] == PROJECT


async def test_sentinel_verdict_is_lead_only(cockpit: Cockpit) -> None:
    by_pl = await cockpit.pr("pl", sentinel_verdict="verified", head_sha=SHA)
    by_lead = await cockpit.pr(
        "lead_b", sentinel_verdict="verified", head_sha=SHA, note="curl /version → sha matches"
    )

    assert by_pl.status_code == 403 and code(by_pl) == "sentinel_lead_only"
    assert by_lead.status_code == 200, by_lead.text
    row = cockpit.store.get_pr(by_lead.json()["pr_id"])
    assert row is not None and row.sentinel is not None
    assert {k: row.sentinel[k] for k in ("verdict", "head_sha", "note", "by")} == {
        "verdict": "verified",
        "head_sha": SHA,
        "note": "curl /version → sha matches",
        "by": "Lead B · Sentinel",
    }
    assert isinstance(row.sentinel["at"], int)
    # The lead has no project of its own; the repo's only project owns the PR.
    assert row.project_slug == PROJECT


@pytest.mark.parametrize("extra", [{}, {"head_sha": SHA[:7]}])
async def test_a_sentinel_verdict_needs_the_full_head_sha(
    cockpit: Cockpit, extra: dict[str, Any]
) -> None:
    resp = await cockpit.pr("lead_b", sentinel_verdict="verified", **extra)
    assert resp.status_code == 400 and code(resp) == "invalid_sha"
    assert cockpit.store.list_prs(cockpit.factory_id) == []


async def test_registration_waits_for_a_running_poller(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_state, "PR_REFRESH_WAIT_S", 2.0)
    status.set_status("github", state="ok")
    gates = {"checks": "fail", "reasons": ["checks failing: lint"]}

    async def poller() -> None:
        keys = await nudge.wait("prs", 2.0)
        assert keys
        for pr_id in keys:
            cockpit.store.update_pr(pr_id, last_polled_at=1, gates=gates, ready=False)

    task = asyncio.create_task(poller())
    resp = await cockpit.pr("pl", claim="ready_for_human_merge")
    await task
    body = resp.json()
    assert body["refreshed"] is True
    assert body["gates"] == gates
    assert body["reasons"] == ["checks failing: lint"]
    assert body["claim_mismatch"] is True
    # The stored claim is judged against the refreshed gates too.
    (claim,) = cockpit.store.find_events(cockpit.factory_id, "pr.claim", 0)
    assert claim.data is not None
    assert (claim.data["claim_mismatch"], claim.data["refreshed"]) == (True, True)


# ── Envs and previews ──────────────────────────────────────────


async def test_dev_env_report_writes_the_project_env(cockpit: Cockpit) -> None:
    resp = await cockpit.env(
        "env_keeper",
        urls={
            "app": "https://app-1a2b.envs.sandbox.caffeinelabs.io",
            "tilt_ui": "http://localhost:10350",
        },
        deployed={"sha": SHA, "ref": "origin/main"},
        resources=[{"name": "caffeine-api", "status": "ok"}],
        note="UP",
    )
    assert resp.status_code == 200, resp.text
    project = cockpit.store.get_project(cockpit.factory_id, PROJECT)
    assert project is not None and project.env is not None
    reported = project.env["reported"]
    assert reported["state"] == "ready"
    assert reported["env_id"] == "1a2b"
    assert reported["urls"]["tilt_ui"] == "http://localhost:10350"
    # A dev env stores DeployedRef ({sha, ref}); preview-only keys are dropped.
    assert reported["deployed"] == {"sha": SHA, "ref": "origin/main"}
    assert reported["resources"] == [{"name": "caffeine-api", "status": "ok"}]
    schemas.EnvView.model_validate(project.env)
    assert cockpit.store.list_prs(cockpit.factory_id) == []
    assert cockpit.kinds() == ["env.reported"]

    again = await cockpit.env(
        "env_keeper", deployed={"sha": SHA, "ref": "origin/main", "tooling_overlay": "abc1234"}
    )
    assert again.status_code == 200, again.text
    project = cockpit.store.get_project(cockpit.factory_id, PROJECT)
    assert project is not None and project.env is not None
    assert project.env["reported"]["deployed"] == {"sha": SHA, "ref": "origin/main"}
    schemas.EnvView.model_validate(project.env)


async def test_preview_report_upserts_the_pr_and_writes_its_preview(cockpit: Cockpit) -> None:
    resp = await cockpit.env(
        "env_keeper",
        state="deploying",
        env_id="1a2b-p9630",
        pr=9630,
        slot=1,
        namespace="env-1a2b-p9630",
        urls={"app": APP, "api": API},
        deployed={"sha": SHA, "ref": "factory/api-version/version", "tooling_overlay": "abc1234"},
        resources=[{"name": "caffeine-api", "ready": False, "restarts": 0}],
    )
    assert resp.status_code == 200, resp.text
    row = cockpit.store.get_pr_by_number(cockpit.factory_id, REPO, 9630)
    assert row is not None and row.preview is not None
    assert row.project_slug == PROJECT
    preview = row.preview
    assert preview["name"] == "p9630"
    assert preview["state"] == "deploying"
    assert preview["slot"] == 1
    assert preview["namespace"] == "env-1a2b-p9630"
    assert preview["urls"] == {"app": APP, "api": API}
    assert preview["deployed"]["sha"] == SHA
    assert preview["deployed"]["tooling_overlay"] == "abc1234"
    deployed_at = preview["deployed"]["at"]
    schemas.PreviewView.model_validate(preview)
    project = cockpit.store.get_project(cockpit.factory_id, PROJECT)
    assert project is not None and project.env is None

    ready = await cockpit.env(
        "env_keeper", state="ready", env_id="1a2b-p9630", pr=9630, deployed={"sha": SHA}
    )
    assert ready.status_code == 200
    row = cockpit.store.get_pr(row.id)
    assert row is not None and row.preview is not None
    assert row.preview["state"] == "ready"
    assert row.preview["urls"] == {"app": APP, "api": API}  # kept from the first report
    assert row.preview["deployed"]["at"] == deployed_at  # same sha, same deploy
    schemas.PreviewView.model_validate(row.preview)

    parked = await cockpit.env("pl", state="parked", env_id="1a2b-p9630", pr=9630)
    assert parked.status_code == 200
    row = cockpit.store.get_pr(row.id)
    assert row is not None and row.preview is not None
    schemas.PreviewView.model_validate(row.preview)
    assert cockpit.kinds() == [
        "pr.discovered",
        "preview.reported",
        "preview.reported",
        "preview.parked",
    ]


@pytest.mark.parametrize("key", ["ic", "reviewer", "demo", "lead_b"])
async def test_only_env_keeper_and_the_pl_report_envs(cockpit: Cockpit, key: str) -> None:
    resp = await cockpit.env(key)
    assert resp.status_code == 403


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        ({"urls": {"app": "http://app-1a2b.envs.sandbox.caffeinelabs.io"}}, "invalid_url"),
        ({"urls": {"api": "https://api.example.com"}}, "invalid_url"),
        ({"urls": {"app": "https://a.b.envs.sandbox.caffeinelabs.io"}}, "invalid_url"),
        ({"urls": {"tilt_ui": "javascript:alert(1)"}}, "invalid_url"),
        ({"urls": {"grafana": "https://g.envs.sandbox.caffeinelabs.io"}}, "invalid_url"),
        ({"state": "deploying"}, "invalid_state"),
        ({"state": "degraded", "pr": 9630}, "invalid_state"),
        ({"deployed": {"sha": "not-a-sha"}}, "invalid_sha"),
        ({"deployed": {"sha": "not-a-sha"}, "pr": 9631, "state": "deploying"}, "invalid_sha"),
        ({"deployed": {"tooling_overlay": "zz"}, "pr": 9631, "state": "ready"}, "invalid_sha"),
    ],
)
async def test_env_reports_are_validated(
    cockpit: Cockpit, extra: dict[str, Any], error: str
) -> None:
    resp = await cockpit.env("env_keeper", **extra)
    assert resp.status_code == 400, resp.text
    assert code(resp) == error
    # A refused report tracks no PR (it would have no pr.discovered event).
    assert cockpit.store.list_prs(cockpit.factory_id) == []
    assert cockpit.kinds() == []


async def test_preview_cap_names_park_candidates(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(agent_state.MAX_PREVIEWS_ENV, "2")
    store, fid = cockpit.store, cockpit.factory_id
    store.upsert_pr(
        fid, REPO, 1, ready=True, preview={"name": "p1", "state": "ready", "reported_at": 10}
    )
    store.upsert_pr(
        fid, REPO, 2, ready=False, preview={"name": "p2", "state": "stale", "reported_at": 20}
    )
    store.upsert_pr(fid, REPO, 3, preview={"name": "p3", "state": "parked", "reported_at": 5})
    store.upsert_pr(fid, REPO, 4, state="merged", preview={"name": "p4", "state": "ready"})

    capped = await cockpit.env("env_keeper", state="deploying", env_id="x-p9630", pr=9630)
    redeploy = await cockpit.env("env_keeper", state="deploying", env_id="x-p1", pr=1)

    assert capped.status_code == 409
    body = capped.json()
    assert body["error"]["code"] == "preview_cap"
    assert body["max_previews"] == 2
    assert [c["pr"] for c in body["park_candidates"]] == [2, 1]
    assert body["park_candidates"][0]["preview"] == "p2"
    # Refused before the PR row is created, so nothing is tracked half-way.
    assert store.get_pr_by_number(fid, REPO, 9630) is None
    assert "pr.discovered" not in cockpit.kinds()
    assert redeploy.status_code == 200


# ── Stage claims ───────────────────────────────────────────────


async def test_only_the_pl_claims_its_stage(cockpit: Cockpit) -> None:
    ok = await cockpit.stage("pl", "pl", "review")
    assert ok.status_code == 200, ok.text
    assert ok.json()["agent_stage"] == "review"
    project = cockpit.store.get_project(cockpit.factory_id, PROJECT)
    assert project is not None
    assert project.agent_stage == "review"
    assert isinstance(project.agent_stage_at, int)

    for key, target in (("lead_a", "pl"), ("ic", "ic"), ("lead_a", "lead_a"), ("pl", "ic")):
        resp = await cockpit.stage(key, target, "ready")
        assert resp.status_code == 403, (key, target)
        assert code(resp) == "not_your_project"
    bad = await cockpit.stage("pl", "pl", "done")
    assert bad.status_code == 400 and code(bad) == "invalid_stage"


async def test_a_replaced_pl_no_longer_claims_the_stage(cockpit: Cockpit) -> None:
    world = cockpit.world
    world.store.set_labels(world.id("pl"), {F.LABEL_STATE: F.STATE_PAUSED})
    world.make_session(
        "pl2",
        agent="pl",
        title="PL · api-version (2)",
        labels={
            F.LABEL_ID: cockpit.factory_id,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: PROJECT,
            F.LABEL_STATE: F.STATE_ACTIVE,
        },
    )
    old = await cockpit.stage("pl", "pl", "building")
    new = await cockpit.stage("pl2", "pl2", "building")
    assert old.status_code == 403 and code(old) == "not_your_project"
    assert new.status_code == 200


# ── Charters ───────────────────────────────────────────────────


async def test_charter_route_is_for_leads(cockpit: Cockpit) -> None:
    client = cockpit.api.client

    def get(key: str, project: str = PROJECT) -> Any:
        return client.get(
            "/v1/factory/agent/charter",
            params={"from_session_id": cockpit.id(key), "project": project},
        )

    cockpit.store.upsert_project(cockpit.factory_id, "no-charter", title="No charter")
    lead, sentinel, pl, missing, empty = await asyncio.gather(
        get("lead_a"), get("lead_b"), get("pl"), get("lead_a", "nope"), get("lead_a", "no-charter")
    )
    assert lead.status_code == 200
    assert lead.json()["text"].startswith("CHARTER · api-version")
    assert "Goal: GET /version returns the sha" in lead.json()["text"]
    assert sentinel.status_code == 200
    assert pl.status_code == 403 and code(pl) == "not_allowed"
    assert missing.status_code == 404 and code(missing) == "charter_not_found"
    assert empty.status_code == 404
