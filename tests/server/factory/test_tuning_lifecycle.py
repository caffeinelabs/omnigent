"""Tier-1 proposals: tuning, rules and code, from proposal to trial, keep and revert.

Sentinel (``lead_b``) and Atlas (``lead_a``) propose through
``POST /v1/factory/agent/proposals`` (``sys_factory_propose``); a class-A change
runs once the other lead co-signs; class B waits for an owner's decision; a
person's ``PUT …/definition`` applies at once. Every applied version mails the
members, rule changes send RULES UPDATE to the roles they concern, and a revert
re-pins the parent version.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.factory import definition, tasks, tuning
from omnigent.stores.factory_store import now_s
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import header_cockpit

EVIDENCE = ["F-9", "D-2"]
EFFECT = {"metric": "heartbeat_share", "direction": "down"}


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in header_cockpit(world, db_uri, monkeypatch):
        restore = definition.install(value.app.state)
        try:
            yield value
        finally:
            restore()


async def _propose(cockpit: Cockpit, key: str = "lead_b", **body: Any) -> httpx.Response:
    payload = {
        "from_session_id": cockpit.world.id(key),
        "kind": "tuning",
        "rationale": "61% of beats were skipped with no change (7d)",
        "evidence": EVIDENCE,
        "expected_effect": EFFECT,
        **body,
    }
    return await cockpit.client.post(
        "/v1/factory/agent/proposals", json=payload, headers={"X-Test-User": "alice"}
    )


def _cadence(value: int) -> list[dict[str, Any]]:
    return [{"op": "replace", "path": "cadence.demo_project_lead", "value": value}]


def _row(cockpit: Cockpit, number: int = 1) -> Any:
    row = cockpit.store.get_proposal_by_number(cockpit.fid, number)
    assert row is not None
    return row


def _texts(cockpit: Cockpit, key: str) -> list[str]:
    return cockpit.mailer.texts_to(cockpit.world.id(key))


# ── Refusals ───────────────────────────────────────────────────


async def test_a_tier0_path_is_refused_with_422(cockpit: Cockpit) -> None:
    resp = await _propose(
        cockpit, changes=[{"op": "replace", "path": "gates.review", "value": "off"}]
    )

    assert resp.status_code == 422, resp.text
    assert error_code(resp) == "tier0"
    assert cockpit.store.list_proposals(cockpit.fid, kind=["tuning"]) == []


async def test_a_proposal_without_evidence_is_refused(cockpit: Cockpit) -> None:
    resp = await _propose(cockpit, evidence=[], changes=_cadence(1200))
    assert resp.status_code == 422 and error_code(resp) == "evidence_required"


async def test_a_pl_proposes_rules_for_its_project_only(cockpit: Cockpit) -> None:
    tuning_resp = await _propose(cockpit, key="pl", changes=_cadence(1200))
    rule = await _propose(
        cockpit,
        key="pl",
        kind="rules",
        rule={"role": "ic", "project": "other", "text": "Run the linter first."},
        expected_effect={"metric": "review_rounds", "direction": "down"},
    )

    assert tuning_resp.status_code == 403 and error_code(tuning_resp) == "not_allowed"
    assert rule.status_code == 200, rule.text
    # The server scopes it to the PL's own project whatever it asked.
    assert _row(cockpit).ops[0]["value"]["project"] == "api-version"
    assert rule.json()["class"] == "B" and rule.json()["decision"]


async def test_a_daily_quota_per_agent(cockpit: Cockpit) -> None:
    assert (await _propose(cockpit, changes=_cadence(1200))).status_code == 200
    second = await _propose(
        cockpit, changes=[{"op": "replace", "path": "loops.review.rounds", "value": 2}]
    )
    third = await _propose(
        cockpit, changes=[{"op": "replace", "path": "loops.verify.per_head", "value": 1}]
    )

    assert second.status_code == 200
    assert third.status_code == 429 and error_code(third) == "daily_quota"


# ── Class A: peer co-sign, trial, keep ─────────────────────────


async def test_cosign_rules(cockpit: Cockpit) -> None:
    filed = await _propose(cockpit, changes=_cadence(1200))
    assert filed.status_code == 200, filed.text
    body = filed.json()
    assert (body["class"], body["status"]) == ("A", "awaiting_cosign")
    assert body["cosign_from"] == "Atlas"
    [request] = _texts(cockpit, "lead_a")
    assert request.startswith("[Factory proposal · C-1 · co-sign requested by Lead B · Sentinel]")

    own = await _propose(cockpit, op="cosign", id="C-1")
    pl = await _propose(cockpit, key="pl", op="cosign", id="C-1")

    assert own.status_code == 403 and error_code(own) == "self_cosign"
    assert pl.status_code == 403 and error_code(pl) == "not_allowed"
    assert _row(cockpit).status == "awaiting_cosign"


async def test_class_a_runs_as_a_trial_once_the_peer_cosigns(cockpit: Cockpit) -> None:
    await _propose(cockpit, changes=_cadence(1200))

    resp = await _propose(cockpit, key="lead_a", op="cosign", id="C-1")

    assert resp.status_code == 200, resp.text
    row = _row(cockpit)
    assert row.status == "trial" and row.cosigned_by == cockpit.world.id("lead_a")
    assert (row.def_version_from, row.def_version_to) == (1, 2)
    assert row.trial_until == row.applied_at + definition.TRIAL_H_DEFAULT * 3600
    assert definition.value(cockpit.deps, cockpit.fid, "cadence.demo_project_lead") == 1200
    # The PL ran on the old default: the supervisor now beats it at the new cadence.
    assert cockpit.world.get("pl").labels[F.LABEL_HEARTBEAT_S] == "1200"
    head = "[Factory definition · v2 · C-1 by Lead B · Sentinel, co-signed by Atlas · trial 48h]"
    assert any(t.startswith(head) for t in _texts(cockpit, "pl"))
    assert "cadence.demo_project_lead: 600 → 1200" in _texts(cockpit, "pl")[-1]
    kinds = [e.kind for e in cockpit.events()]
    assert {
        "proposal.opened",
        "proposal.cosigned",
        "proposal.applied",
        "definition.pinned",
    } <= set(kinds)
    again = await _propose(cockpit, key="lead_a", op="cosign", id="C-1")
    assert again.status_code == 409


async def test_an_owner_keeps_a_trial(cockpit: Cockpit) -> None:
    await _propose(cockpit, changes=_cadence(1200))
    await _propose(cockpit, key="lead_a", op="cosign", id="C-1")

    carol = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/keep", {}, user="carol"
    )
    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/keep", {})

    assert carol.status_code == 403
    assert resp.status_code == 200, resp.text
    assert _row(cockpit).status == "kept"
    assert definition.value(cockpit.deps, cockpit.fid, "cadence.demo_project_lead") == 1200


# ── Class B: an owners decision ────────────────────────────────


async def test_a_rule_waits_for_an_owner_then_reaches_the_pl_and_its_cards(
    cockpit: Cockpit,
) -> None:
    resp = await _propose(
        cockpit,
        kind="rules",
        rule={"role": "ic", "text": "Run the package's unit tests before every push."},
        expected_effect={"metric": "review_rounds", "direction": "down"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["class"], body["status"], body["approval"]) == ("B", "pending", "owner")
    decision = cockpit.store.get_decision(_row(cockpit).decision_id)
    assert (
        decision is not None
        and decision.assignee == "owners"
        and decision.thread == "improvements"
    )
    assert tasks.role_rules(cockpit.deps, cockpit.fid, "ic", "api-version") == []

    carol = await cockpit.post(
        f"/v1/factory/decisions/{decision.id}/answer", {"option_id": "apply"}, user="carol"
    )
    assert carol.status_code == 403
    answered = await cockpit.post(
        f"/v1/factory/decisions/{decision.id}/answer", {"option_id": "apply"}
    )

    assert answered.status_code == 200, answered.text
    row = _row(cockpit)
    assert row.status == "trial"
    assert tasks.role_rules(cockpit.deps, cockpit.fid, "ic", "api-version") == [
        "R-1: Run the package's unit tests before every push."
    ]
    [update] = [t for t in _texts(cockpit, "pl") if "RULES UPDATE" in t]
    assert update.startswith("[Factory control · RULES UPDATE · C-1 · by Lead B · Sentinel]")
    assert '+ R-1 · ic · "Run the package' in update
    # Leads get no RULES UPDATE for an IC rule.
    assert not [t for t in _texts(cockpit, "lead_a") if "RULES UPDATE" in t]
    rules = await cockpit.client.get(
        "/v1/factory/agent/rules",
        params={"from_session_id": cockpit.world.id("ic")},
        headers={"X-Test-User": "alice"},
    )
    assert rules.status_code == 200, rules.text
    assert [r["id"] for r in rules.json()["rules"]] == ["R-1"]
    assert rules.json()["def_version"] == 2


async def test_an_owner_rejects_a_rule(cockpit: Cockpit) -> None:
    await _propose(
        cockpit,
        kind="rules",
        rule={"role": "project_lead", "text": "Ask less."},
        expected_effect={"metric": "decision_latency", "direction": "down"},
    )

    resp = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/reject", {"note": "too vague"}
    )

    assert resp.status_code == 200, resp.text
    assert _row(cockpit).status == "rejected"
    assert definition.definition_for(cockpit.deps, cockpit.fid).version == 1  # type: ignore[union-attr]
    assert any(
        t.startswith("[Factory proposal · C-1 rejected by alice")
        for t in _texts(cockpit, "lead_b")
    )


async def test_money_and_engine_changes_need_owners(cockpit: Cockpit) -> None:
    resp = await _propose(
        cockpit,
        changes=[{"op": "replace", "path": "budgets.project_usd", "value": 100}],
        expected_effect={"metric": "cost_usd", "direction": "down"},
    )
    assert resp.status_code == 200 and resp.json()["class"] == "B"


# ── People ─────────────────────────────────────────────────────


async def test_a_human_edit_applies_at_once(cockpit: Cockpit) -> None:
    path = f"/v1/factory/factories/{cockpit.fid}/definition"
    edit = {
        "changes": [{"op": "replace", "path": "loops.review.rounds", "value": 2}],
        "note": "less churn",
    }

    carol = await cockpit.client.put(path, json=edit, headers={"X-Test-User": "carol"})
    agent = await cockpit.client.put(
        path, json=edit, headers={"X-Test-User": "alice", RUNNER_TUNNEL_TOKEN_HEADER: "tok"}
    )
    tier0 = await cockpit.client.put(
        path,
        json={"changes": [{"op": "replace", "path": "alerts.slack_webhook", "value": "x"}]},
        headers={"X-Test-User": "alice"},
    )
    resp = await cockpit.client.put(path, json=edit, headers={"X-Test-User": "alice"})

    assert carol.status_code == 403
    assert agent.status_code == 403 and error_code(agent) == "human_only"
    assert tier0.status_code == 422 and error_code(tier0) == "tier0"
    assert resp.status_code == 200, resp.text
    row = _row(cockpit)
    assert (row.class_, row.author_kind, row.status, row.author) == (
        "human",
        "human",
        "applied",
        "alice",
    )
    assert definition.value(cockpit.deps, cockpit.fid, "loops.review.rounds") == 2
    got = (await cockpit.client.get(path, headers={"X-Test-User": "bob"})).status_code
    assert got == 404
    view = (await cockpit.client.get(path, headers={"X-Test-User": "carol"})).json()
    assert view["def_version"] == 2 and view["effective"]["loops"]["review"]["rounds"] == 2
    assert view["tier2"]["enabled"] is False
    versions = (
        await cockpit.client.get(f"{path}/versions", headers={"X-Test-User": "carol"})
    ).json()
    assert [v["version"] for v in versions["data"]] == [2, 1] and versions["pinned"] == 2
    assert versions["data"][0]["proposal_ref"] == "C-1"
    diff = (
        await cockpit.client.get(f"{path}/diff?from=1&to=2", headers={"X-Test-User": "carol"})
    ).json()
    assert diff["changes"] == [{"path": "loops.review.rounds", "before": 3, "after": 2}]
    stale = await cockpit.client.put(
        path, json={**edit, "base_version": 1}, headers={"X-Test-User": "alice"}
    )
    assert stale.status_code == 409 and error_code(stale) == "stale_definition"


async def test_revert_restores_the_parent_and_sends_rules_update(cockpit: Cockpit) -> None:
    path = f"/v1/factory/factories/{cockpit.fid}/definition"
    rule = {"role": "project_lead", "text": "Decide reversible naming choices yourself."}
    resp = await cockpit.client.put(
        path,
        json={"changes": [{"op": "add", "path": "rules", "value": rule}], "trial": True},
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 200, resp.text
    assert _row(cockpit).status == "trial"
    before = len([t for t in _texts(cockpit, "pl") if "RULES UPDATE" in t])

    reverted = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/revert", {"note": "noise"}
    )

    assert reverted.status_code == 200, reverted.text
    row = _row(cockpit)
    assert row.status == "reverted" and row.reverted_by == "alice"
    assert definition.definition_for(cockpit.deps, cockpit.fid).version == 1  # type: ignore[union-attr]
    assert tasks.role_rules(cockpit.deps, cockpit.fid, "project_lead", "api-version") == []
    updates = [t for t in _texts(cockpit, "pl") if "RULES UPDATE" in t]
    assert len(updates) == before + 1 and "- R-1 · project_lead" in updates[-1]
    assert "proposal.reverted" in [e.kind for e in cockpit.events()]


async def test_reverting_an_older_change_undoes_only_it(cockpit: Cockpit) -> None:
    path = f"/v1/factory/factories/{cockpit.fid}/definition"
    for change in (
        {"op": "replace", "path": "loops.review.rounds", "value": 2},
        {"op": "replace", "path": "loops.verify.per_head", "value": 1},
    ):
        await cockpit.client.put(
            path, json={"changes": [change]}, headers={"X-Test-User": "alice"}
        )

    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/revert", {})

    assert resp.status_code == 200, resp.text
    resolved = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)
    assert resolved is not None and resolved.version == 4 and resolved.created_via == "revert"
    assert resolved.doc["loops"]["review"]["rounds"] == 3
    assert resolved.doc["loops"]["verify"]["per_head"] == 1


async def test_pin_puts_an_old_version_back(cockpit: Cockpit) -> None:
    path = f"/v1/factory/factories/{cockpit.fid}/definition"
    await cockpit.client.put(
        path,
        json={"changes": [{"op": "replace", "path": "loops.review.rounds", "value": 2}]},
        headers={"X-Test-User": "alice"},
    )

    resp = await cockpit.post(f"{path}/pin", {"version": 1})

    assert resp.status_code == 200 and resp.json() == {
        "def_version": 1,
        "changed": True,
        "from": 2,
    }
    assert definition.value(cockpit.deps, cockpit.fid, "loops.review.rounds") == 3
    assert any(
        t.startswith("[Factory definition · v1 pinned by alice") for t in _texts(cockpit, "pl")
    )


# ── Code (Tier 2 off) and expiry ───────────────────────────────


async def test_code_proposals_are_recorded_and_never_run(cockpit: Cockpit) -> None:
    resp = await _propose(
        cockpit,
        key="lead_a",
        kind="code",
        title="Promote rule R-1 into the PL playbook",
        expected_effect=None,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tier2"] == {"enabled": False, "reason": "OMNIGENT_FACTORY_SMITH is not 1"}
    row = _row(cockpit)
    assert (row.kind, row.status, row.decision_id) == ("code", "pending", None)
    assert row.warnings == ["Tier 2 disabled: OMNIGENT_FACTORY_SMITH is not 1"]


async def test_open_proposals_expire_after_72_hours(cockpit: Cockpit) -> None:
    await _propose(cockpit, changes=_cadence(1200))

    expired = await tuning.expire_due(
        cockpit.app.state, cockpit.fid, now=now_s() + tuning.EXPIRY_S + 1
    )

    assert expired == ["C-1"]
    assert _row(cockpit).status == "expired"
