"""Insights, metrics, feedback and the agents' insights and retro routes.

``alice`` owns the factory, ``carol`` operates it (EDIT on Atlas and the
api-version PL, not on the docs PL), ``bob`` has no access. Projects whose PL a
viewer cannot read are left out of every number; per-person numbers are for
owners only.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import definition, feedback
from omnigent.server.factory.telemetry import metrics
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import REPO, header_cockpit

H = 3600


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in header_cockpit(world, db_uri, monkeypatch):
        restore = definition.install(value.app.state)
        metrics.reset_cache()
        _seed(value)
        try:
            yield value
        finally:
            restore()


def _seed(cockpit: Cockpit) -> None:
    from omnigent import factory as F
    from omnigent.server.auth import LEVEL_OWNER

    world, store, fid, now = cockpit.world, cockpit.store, cockpit.fid, int(time.time())
    docs_pl = world.make_session(
        "pl_docs",
        agent="pl",
        title="PL · docs",
        labels={
            F.LABEL_ID: fid,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: "docs",
            F.LABEL_PARENT: fid,
            F.LABEL_STATE: "active",
        },
    )
    cockpit.permissions.grant("alice", docs_pl.id, LEVEL_OWNER)
    store.upsert_project(fid, "docs", title="Docs", repo=REPO, pl_session_id=docs_pl.id)
    store.upsert_pr(
        fid,
        REPO,
        1,
        state="merged",
        project_slug="api-version",
        opened_at=now - 5 * H,
        first_ready_at=now - 4 * H,
        merged_at=now - 3 * H,
    )
    store.upsert_pr(fid, REPO, 2, state="closed", project_slug="docs", closed_at=now - 2 * H)
    store.upsert_pr(fid, REPO, 3, state="open", project_slug="api-version", head_sha="a" * 40)
    store.append_chat(fid, thread="factory", author_kind="human", author="alice", body="go")


def _kpis(body: dict[str, Any]) -> dict[str, Any]:
    return body["kpis"]


async def _get(cockpit: Cockpit, path: str, user: str = "alice", **params: Any) -> Any:
    return await cockpit.client.get(path, params=params, headers={"X-Test-User": user})


# ── Insights ───────────────────────────────────────────────────


async def test_insights_return_every_kpi_tile_with_n(cockpit: Cockpit) -> None:
    resp = await _get(cockpit, f"/v1/factory/factories/{cockpit.fid}/insights", window="7d")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(_kpis(body)) == set(metrics.KPIS)
    for name, tile in _kpis(body).items():
        assert isinstance(tile["n"], int), name
        assert "delta" in tile and "previous" in tile, name
    assert _kpis(body)["merge_acceptance"]["n"] == 2
    assert body["window"]["label"] == "7d"
    assert body["def_version"] == 1
    assert {"findings", "incidents_open", "retros", "experiments", "catalog"} <= set(body)
    assert len(body["catalog"]) == len(metrics.CATALOG)


async def test_viewers_do_not_see_projects_whose_pl_they_cannot_read(cockpit: Cockpit) -> None:
    path = f"/v1/factory/factories/{cockpit.fid}/insights"

    carol = (await _get(cockpit, path, user="carol")).json()
    bob = await _get(cockpit, path, user="bob")

    assert bob.status_code == 404
    # The closed docs PR is hidden from carol: one merged PR out of one.
    assert _kpis(carol)["merge_acceptance"]["n"] == 1
    assert _kpis(carol)["merge_acceptance"]["value"] == 1.0
    hidden = await _get(cockpit, path, user="carol", project="docs")
    assert hidden.status_code == 404


async def test_per_person_numbers_are_for_owners(cockpit: Cockpit) -> None:
    path = f"/v1/factory/factories/{cockpit.fid}"

    owner = (await _get(cockpit, f"{path}/insights")).json()
    operator = (await _get(cockpit, f"{path}/insights", user="carol")).json()
    refused = await _get(cockpit, f"{path}/metrics", user="carol", group_by="person")
    allowed = await _get(
        cockpit, f"{path}/metrics", names="human_prompts_per_day", group_by="person"
    )

    assert owner["people"]["alice"]["chat_posts"] == 1
    assert "people" not in operator
    assert refused.status_code == 403 and error_code(refused) == "owners_only"
    assert allowed.status_code == 200
    assert set(allowed.json()["metrics"]["human_prompts_per_day"]["groups"]) == {"alice"}


async def test_metrics_series_and_validation(cockpit: Cockpit) -> None:
    path = f"/v1/factory/factories/{cockpit.fid}/metrics"

    grouped = await _get(cockpit, path, names="merge_acceptance,cost_usd", group_by="project")
    unknown = await _get(cockpit, path, names="nope")
    bad_group = await _get(cockpit, path, group_by="planet")
    bad_window = await _get(cockpit, path, window="7y")

    assert grouped.status_code == 200, grouped.text
    values = grouped.json()["metrics"]
    assert set(values) == {"merge_acceptance", "cost_usd"}
    assert set(values["merge_acceptance"]["groups"]) == {"api-version", "docs"}
    assert unknown.status_code == 400 and error_code(unknown) == "unknown_metric"
    assert bad_group.status_code == 400 and error_code(bad_group) == "invalid_group_by"
    assert bad_window.status_code == 400 and error_code(bad_window) == "invalid_window"


async def test_the_fleet_view(cockpit: Cockpit) -> None:
    resp = await _get(cockpit, "/v1/factory/insights")
    nobody = await _get(cockpit, "/v1/factory/insights", user="bob")

    assert resp.status_code == 200, resp.text
    [row] = [r for r in resp.json()["data"] if r["factory_id"] == cockpit.fid]
    assert set(row["kpis"]) == {
        "merge_acceptance",
        "lead_time",
        "human_prompts_per_day",
        "cost_per_merged_pr",
        "incidents",
    }
    assert all(r["factory_id"] != cockpit.fid for r in nobody.json()["data"])


# ── Feedback ───────────────────────────────────────────────────


async def test_feedback_is_stored_and_validated(cockpit: Cockpit) -> None:
    body = {
        "factory_id": cockpit.fid,
        "target_kind": "demo",
        "target_id": "demo1",
        "kind": "useful",
        "rating": 1,
    }

    ok = await cockpit.post("/v1/factory/feedback", body, user="carol")
    bad = await cockpit.post("/v1/factory/feedback", {**body, "kind": "rating", "rating": 9})
    closure = await cockpit.post(
        "/v1/factory/feedback", {**body, "kind": "closure", "rating": None, "tags": ["because"]}
    )
    outsider = await cockpit.post("/v1/factory/feedback", body, user="bob")

    assert ok.status_code == 201, ok.text
    assert ok.json()["feedback"]["author"] == "carol"
    assert bad.status_code == 400 and error_code(bad) == "invalid_rating"
    assert closure.status_code == 400 and error_code(closure) == "invalid_tag"
    assert outsider.status_code in (403, 404)
    [row] = cockpit.store.list_feedback(cockpit.fid)
    assert (row.kind, row.rating, row.def_version) == ("useful", 1, 1)
    assert "feedback.added" in [e.kind for e in cockpit.events()]


async def test_a_cockpit_review_can_carry_a_rating(cockpit: Cockpit) -> None:
    pr = cockpit.store.get_pr_by_number(cockpit.fid, REPO, 3)
    assert pr is not None

    resp = await cockpit.post(
        f"/v1/factory/prs/{pr.id}/feedback",
        {"verdict": "approved", "rating": 5, "tags": ["clean", "tested"]},
    )

    assert resp.status_code == 200, resp.text
    [row] = cockpit.store.list_feedback(cockpit.fid, kinds=["rating"])
    assert (row.target_id, row.rating, row.tags, row.head_sha) == (
        pr.id,
        5,
        ["clean", "tested"],
        "a" * 40,
    )


async def test_a_closed_pr_decision_needs_a_closure_reason(cockpit: Cockpit) -> None:
    decision, _ = cockpit.store.create_decision(
        cockpit.fid,
        dedupe_key="pr_closed:pr-x",
        question="PR closed without merge. Why, and what next?",
        source="pr_closed",
        options=feedback.closure_options(
            [{"id": "abandon", "label": "Abandon task", "action": "dismiss"}]
        ),
    )
    path = f"/v1/factory/decisions/{decision.id}/answer"

    missing = await cockpit.post(path, {"option_id": "abandon"})
    wrong = await cockpit.post(path, {"option_id": "abandon", "reason": "meh"})
    resp = await cockpit.post(path, {"option_id": "abandon", "reason": "test_run"})

    assert missing.status_code == 400 and error_code(missing) == "closure_reason_required"
    assert wrong.status_code == 400 and error_code(wrong) == "invalid_tag"
    assert resp.status_code == 200, resp.text
    [row] = cockpit.store.list_feedback(cockpit.fid, kinds=["closure"])
    assert (row.target_kind, row.target_id, row.tags) == ("pr", "pr-x", ["test_run"])
    view = resp.json()["decision"]
    assert view["options"][0]["reasons"][0] == "wrong_scope"


# ── Agents ─────────────────────────────────────────────────────


async def _agent_get(cockpit: Cockpit, key: str, **params: Any) -> Any:
    params["from_session_id"] = cockpit.world.id(key)
    return await _get(cockpit, "/v1/factory/agent/insights", **params)


async def test_agent_insights_by_role(cockpit: Cockpit) -> None:
    lead = await _agent_get(cockpit, "lead_a", kind="kpis")
    pl = await _agent_get(cockpit, "pl", kind="kpis")
    ic = await _agent_get(cockpit, "ic", kind="kpis")
    loops = await _agent_get(cockpit, "pl", kind="loops")
    bad = await _agent_get(cockpit, "lead_a", kind="gossip")

    assert lead.status_code == 200, lead.text
    assert set(lead.json()["kpis"]) == set(metrics.KPIS) and "project_kpis" not in lead.json()
    assert pl.json()["project"] == "api-version"
    assert pl.json()["project_kpis"]["merge_acceptance"]["n"] == 1
    assert ic.status_code == 403
    assert loops.status_code == 200 and loops.json()["loops"] == []
    assert bad.status_code == 400 and error_code(bad) == "invalid_kind"


async def test_precedents_match_answered_decisions(cockpit: Cockpit) -> None:
    for i, question in enumerate(("Name the endpoint /version?", "Use port 8080?")):
        decision, _ = cockpit.store.create_decision(
            cockpit.fid,
            dedupe_key=f"q{i}",
            question=question,
            options=[{"id": "yes", "label": "Yes"}],
        )
        cockpit.store.answer_decision(decision.id, option_id="yes", text=None, by="alice")

    resp = await _agent_get(cockpit, "pl", kind="precedents", q="endpoint")

    [hit] = resp.json()["precedents"]
    assert hit["question"] == "Name the endpoint /version?" and hit["answer"] == "Yes"


async def test_a_retro_without_evidence_is_refused(cockpit: Cockpit) -> None:
    async def retro(key: str, **body: Any) -> Any:
        return await cockpit.client.post(
            "/v1/factory/agent/retros",
            json={"from_session_id": cockpit.world.id(key), **body},
            headers={"X-Test-User": "alice"},
        )

    cause = {"category": "env_flaky", "text": "Tilt timed out twice", "evidence": []}
    none = await retro("pl", target="pr:1", slowed="env", root_causes=[cause])
    unknown = await retro(
        "pl", target="pr:1", root_causes=[{**cause, "category": "gremlins", "evidence": ["D-1"]}]
    )
    other = await retro("pl", target="project:docs", root_causes=[{**cause, "evidence": ["D-1"]}])
    ok = await retro(
        "pl",
        target="!1",
        went_well="small card",
        slowed="env",
        root_causes=[{**cause, "evidence": ["D-1", "T-1"]}],
    )
    finding = await retro(
        "lead_b", kind="finding", target="factory", evidence=["F-1"], summary="env flakes"
    )

    assert none.status_code == 422 and error_code(none) == "evidence_required"
    assert unknown.status_code == 400 and error_code(unknown) == "unknown_category"
    assert other.status_code == 403 and error_code(other) == "not_your_project"
    assert ok.status_code == 200, ok.text
    issue = cockpit.store.get_issue(ok.json()["id"])
    assert issue is not None
    assert (issue.class_, issue.target, issue.category, issue.source) == (
        "retro",
        "pr:1",
        "env_flaky",
        "agent",
    )
    assert issue.evidence["refs"] == ["D-1", "T-1"]
    assert finding.status_code == 200 and finding.json()["issue"] == "F-1"
    assert cockpit.store.get_issue(finding.json()["id"]).source == "sentinel"  # type: ignore[union-attr]
    assert "retro.added" in [e.kind for e in cockpit.events()]
