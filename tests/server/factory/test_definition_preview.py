"""``POST …/definition/preview``: a dry run of ``PUT …/definition`` (or of a restore).

It answers what the change column shows before anything applies: the diff
with each path's class, who can apply it, what it does besides storing a
version, what blocks it (a Tier-0 path, a bound, a moved base version), and
whether it may run as a trial. It stores nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.factory import definition_blocks as blocks
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import definition_cockpit, put, url, version

ROUNDS = {"op": "replace", "path": "loops.review.rounds", "value": 2}


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


async def _preview(cockpit: Cockpit, user: str = "alice", **body: Any) -> httpx.Response:
    return await cockpit.client.post(
        url(cockpit, "/preview"), json=body, headers={"X-Test-User": user}
    )


async def test_a_preview_shows_the_diff_class_and_who_applies_it(cockpit: Cockpit) -> None:
    resp = await _preview(
        cockpit,
        changes=[ROUNDS, {"op": "replace", "path": "cadence.demo_project_lead", "value": 1200}],
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["def_version"], body["version_next"]) == (1, 2)
    assert [(d["path"], d["before"], d["after"], d["class"]) for d in body["diff"]] == [
        ("cadence.demo_project_lead", 600, 1200, "A"),
        ("loops.review.rounds", 3, 2, "A"),
    ]
    assert body["diff"][1]["line"] == "loops.review.rounds: 3 → 2"
    # Today's settings hold no block: they trial side by side, as before.
    assert body["diff"][1]["block"] is None
    assert (body["class"], body["approvers"], body["owner_only"]) == ("A", ["owners"], False)
    assert body["you_can_apply"] is True and body["blocked"] == []
    assert body["who"].startswith("Owners (class A). You are an owner")
    assert body["rerank"] == [] and body["restore"] is None
    assert body["trialable"] == {"ok": True, "trial_h": 48, "blocks": []}
    kinds = [e["kind"] for e in body["effects"]]
    assert kinds == ["mail", "cadence"]
    # A dry run: nothing stored, nothing mailed.
    assert version(cockpit) == 1 and cockpit.store.list_proposals(cockpit.fid) == []
    assert cockpit.events("definition.pinned") == []


async def test_an_operator_sees_it_waits_for_an_owner(cockpit: Cockpit) -> None:
    resp = await _preview(
        cockpit,
        user="carol",
        changes=[{"op": "replace", "path": "strategy.non_goals", "value": ["Billing (sample)"]}],
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["class"] == "B" and body["owner_only"] is True
    assert (body["you_can_apply"], body["you_can_propose"]) == (False, True)
    assert body["who"] == (
        "Owners (class B). It waits for an owner: propose it to them. Agents can only propose it."
    )
    assert body["trialable"]["ok"] is False and body["trialable"]["code"] == "not_trialable"
    assert "judged on outcomes" in body["trialable"]["reason"]
    assert body["trialable"]["why"] == "judged on outcomes"
    assert [e["kind"] for e in body["effects"]] == ["data"]


async def test_the_preview_class_agrees_with_its_entries(cockpit: Cockpit) -> None:
    on = {"op": "replace", "path": "discovery.enabled", "value": True}
    assert (await put(cockpit, on)).status_code == 200

    body = (await _preview(cockpit, changes=[{**on, "value": False}])).json()

    # Turning discovery off is class A; only owners apply it, and an agent's proposal waits.
    assert [(d["path"], d["class"], d["owner_only"]) for d in body["diff"]] == [
        ("discovery.enabled", "A", True)
    ]
    assert (body["class"], body["owner_only"]) == ("A", True)
    assert body["who"].startswith("Owners (class A). You are an owner")
    assert body["who"].endswith("Agents can only propose it.")
    assert (body["trialable"]["code"], body["trialable"]["why"]) == (
        "not_trialable",
        "needs discovery guards",
    )


async def test_blocked_changes_are_listed_and_the_rest_previews(cockpit: Cockpit) -> None:
    resp = await _preview(
        cockpit,
        changes=[
            {"op": "replace", "path": "alerts.slack_webhook", "value": "https://hooks.example"},
            {"op": "replace", "path": "loops.review.rounds", "value": 60},
            {"op": "replace", "path": "discovery.rubric.formula", "value": "reach"},
            ROUNDS,
        ],
        base_version=7,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [(b["index"], b["code"]) for b in body["blocked"]] == [
        (0, "tier0"),
        (1, "out_of_bounds"),
        (2, "tier0"),
        (None, "stale_definition"),
    ]
    assert "formula is code" in body["blocked"][2]["message"]
    assert [d["path"] for d in body["diff"]] == ["loops.review.rounds"]
    assert body["you_can_apply"] is False


async def test_an_invalid_result_is_blocked_not_an_error(cockpit: Cockpit) -> None:
    resp = await _preview(
        cockpit, changes=[{"op": "replace", "path": "strategy.mix.outcome", "value": 90}]
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [b["code"] for b in body["blocked"]] == ["invalid_definition"]
    assert body["diff"] == [] and body["trialable"]["code"] == "no_change"


async def test_effects_name_the_rules_update_and_discovery_switch(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    rule = {"role": "project_lead", "text": "Say which tests ran (sample)."}
    on = {"op": "replace", "path": "discovery.enabled", "value": True}
    monkeypatch.delenv(blocks.DISCOVERY_ENV, raising=False)

    dark = await _preview(cockpit, changes=[{"op": "add", "path": "rules", "value": rule}, on])
    monkeypatch.setenv(blocks.DISCOVERY_ENV, "1")
    lit = (await _preview(cockpit, changes=[on])).json()

    body = dark.json()
    assert [d["path"] for d in body["diff"]] == ["discovery.enabled", "rules.new"]
    effects = {e["kind"]: e for e in body["effects"]}
    assert effects["rules"]["members"] == [cockpit.world.id("pl")]
    assert "stays dark" in effects["discovery"]["text"]
    assert [e["text"] for e in lit["effects"] if e["kind"] == "discovery"] == [
        "Discovery turns on for this factory."
    ]


async def test_a_trial_held_block_is_not_trialable_in_the_preview(cockpit: Cockpit) -> None:
    brief = {"op": "replace", "path": "briefs.ic", "value": "Run the tests first (sample)."}
    held = await put(cockpit, brief, trial=True)
    assert held.status_code == 200, held.text

    body = (
        await _preview(
            cockpit, changes=[{"op": "replace", "path": "briefs.reviewer", "value": "Be kind."}]
        )
    ).json()

    assert body["trialable"]["ok"] is False
    assert (body["trialable"]["code"], body["trialable"]["trial"]) == ("trial_overlap", "C-1")
    assert body["trialable"]["block"] == "briefs"
    assert "C-1 is already trialing briefs (day 1 of 2)" in body["trialable"]["reason"]


async def test_a_restore_preview_shows_its_diff_and_the_trials_it_ends(
    cockpit: Cockpit,
) -> None:
    assert (await put(cockpit, ROUNDS, trial=True)).status_code == 200

    body = (await _preview(cockpit, restore=1)).json()

    assert [(d["path"], d["before"], d["after"]) for d in body["diff"]] == [
        ("loops.review.rounds", 2, 3)
    ]
    assert body["restore"] == {"version": 1, "ends": ["C-1"]}
    assert body["trialable"]["code"] == "restore"
    kinds = [e["kind"] for e in body["effects"]]
    assert kinds[0] == "restore" and "ends_trial" in kinds
    assert version(cockpit) == 2


@pytest.mark.parametrize(
    ("body", "user", "headers", "status", "code"),
    [
        ({}, "alice", {}, 400, "invalid_body"),
        ({"changes": [ROUNDS], "restore": 1}, "alice", {}, 400, "invalid_body"),
        ({"restore": 9}, "alice", {}, 404, "unknown_version"),
        ({"changes": [ROUNDS]}, "bob", {}, 404, None),
        ({"changes": [ROUNDS]}, "alice", {RUNNER_TUNNEL_TOKEN_HEADER: "tok"}, 403, "human_only"),
    ],
)
async def test_the_preview_route_is_checked(
    cockpit: Cockpit,
    body: dict[str, Any],
    user: str,
    headers: dict[str, str],
    status: int,
    code: str | None,
) -> None:
    resp = await cockpit.client.post(
        url(cockpit, "/preview"), json=body, headers={"X-Test-User": user, **headers}
    )
    assert resp.status_code == status, resp.text
    if code is not None:
        assert error_code(resp) == code
