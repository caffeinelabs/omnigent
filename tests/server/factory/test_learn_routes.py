"""The learning loop's cockpit reads and the operators' "Propose change".

``alice`` owns the factory, ``carol`` operates it (EDIT on Atlas and the
api-version PL), ``bob`` has no access. Version markers and bundle drift are
reads for every member; a trial's scorecard is the evaluation the server
would decide on; a person's tuning or rules proposal waits for the owners.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import definition, tuning
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.telemetry import metrics, versions
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import header_cockpit


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in header_cockpit(world, db_uri, monkeypatch):
        restore = definition.install(value.app.state)
        metrics.reset_cache()
        try:
            yield value
        finally:
            restore()


def _base(cockpit: Cockpit) -> str:
    return f"/v1/factory/factories/{cockpit.fid}"


async def _get(cockpit: Cockpit, path: str, user: str = "alice") -> Any:
    return await cockpit.client.get(f"{_base(cockpit)}{path}", headers={"X-Test-User": user})


async def _post(cockpit: Cockpit, path: str, body: dict[str, Any], user: str = "carol") -> Any:
    return await cockpit.client.post(
        f"{_base(cockpit)}{path}", json=body, headers={"X-Test-User": user}
    )


def _change(path: str, value: Any) -> dict[str, Any]:
    return {"op": "replace", "path": path, "value": value}


# ── Version markers ────────────────────────────────────────────


async def test_versions_list_definition_and_platform_markers(cockpit: Cockpit) -> None:
    fid = cockpit.fid
    definition.definition_for(cockpit.deps, fid)
    definition.set_values(cockpit.deps, fid, {"loops.review.rounds": 2}, created_by="alice")
    cockpit.store.upsert_version(
        bundles={}, knobs={}, image_tag="staging-0bb15c1", server_sha="0bb15c1"
    )

    resp = await _get(cockpit, "/versions")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [d["version"] for d in body["definition"]] == [1, 2]
    assert body["definition"][0]["created_via"] == "seed"
    assert body["definition"][1]["pinned"] is True
    assert [p["image_tag"] for p in body["platform"]] == ["staging-0bb15c1"]


async def test_definition_versions_name_the_agent_that_made_them(cockpit: Cockpit) -> None:
    fid = cockpit.fid
    definition.definition_for(cockpit.deps, fid)
    sentinel = cockpit.world.id("lead_b")
    definition.set_values(cockpit.deps, fid, {"loops.review.rounds": 2}, created_by=sentinel)
    definition.set_values(cockpit.deps, fid, {"loops.review.rounds": 3}, created_by="alice")

    resp = await cockpit.client.get(
        f"{_base(cockpit)}/definition/versions", headers={"X-Test-User": "alice"}
    )

    assert resp.status_code == 200, resp.text
    by = {v["version"]: (v["created_by"], v["created_by_title"]) for v in resp.json()["data"]}
    assert by[2] == (sentinel, "Lead B · Sentinel")
    assert by[3] == ("alice", None) and by[1] == ("server", None)


async def test_versions_need_read_access(cockpit: Cockpit) -> None:
    resp = await _get(cockpit, "/versions", user="bob")
    assert resp.status_code in (403, 404)


# ── Bundle drift ───────────────────────────────────────────────


async def test_bundle_drift_names_members_on_an_older_bundle(cockpit: Cockpit) -> None:
    world = cockpit.world
    pl_agent = world.agents["pl"]
    world.store.set_labels(world.id("pl"), {F.LABEL_BUNDLE: f"{pl_agent}/0ld0ld0ld0ld"})
    current = versions.labels_for_agent(world.agent_store, world.agents["lead"])[F.LABEL_BUNDLE]
    world.store.set_labels(world.id("lead_a"), {F.LABEL_BUNDLE: current})

    resp = await _get(cockpit, "/bundles", user="carol")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["drifted"] == 1
    first = body["members"][0]
    assert first["session_id"] == world.id("pl") and first["drift"] is True
    assert first["running"].endswith("0ld0ld0ld0ld") and first["current"] != first["running"]
    atlas = next(m for m in body["members"] if m["session_id"] == world.id("lead_a"))
    assert atlas["drift"] is False
    # Agent ids are opaque; the cockpit names a bundle by its agent.
    assert (first["bundle"], atlas["bundle"]) == ("factory-pl", "factory-lead")


# ── Scorecard ──────────────────────────────────────────────────


async def test_a_trial_has_a_live_scorecard_with_every_guard(cockpit: Cockpit) -> None:
    fid = cockpit.fid
    definition.definition_for(cockpit.deps, fid)
    row, _ = cockpit.store.create_proposal(
        fid,
        kind="tuning",
        title="loops.review.rounds 3→2",
        author_kind="human",
        author="alice",
        approval="owner",
        class_="human",
        ops=[_change("loops.review.rounds", 2)],
        expected_effect={"metric": "review_rounds", "direction": "down"},
        result={"trial_h": 24},
    )
    root = cockpit.world.get("lead_a")
    await tuning.apply(
        cockpit.app.state, root, row, actor=Actor.human("alice", "owner"), trial=True
    )

    resp = await _get(cockpit, "/proposals/C-1/scorecard")

    assert resp.status_code == 200, resp.text
    card = resp.json()["scorecard"]
    assert card["live"] is True
    assert {g["metric"] for g in card["guards"]} == set(metrics.GUARDS)
    assert card["target"]["metric"] == "review_rounds"
    assert card["outcome"] == "inconclusive" and card["min_n"] == 3
    start, end = card["trial_window"]
    assert card["baseline_window"][1] == start and end <= int(time.time()) + 1


async def test_a_live_scorecard_is_reused_for_a_minute(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.factory import evaluate, learn_view

    fid = cockpit.fid
    definition.definition_for(cockpit.deps, fid)
    row, _ = cockpit.store.create_proposal(
        fid,
        kind="tuning",
        title="loops.review.rounds 3→2",
        author_kind="human",
        author="alice",
        approval="owner",
        class_="human",
        ops=[_change("loops.review.rounds", 2)],
        result={"trial_h": 24},
    )
    await tuning.apply(
        cockpit.app.state,
        cockpit.world.get("lead_a"),
        row,
        actor=Actor.human("alice", "owner"),
        trial=True,
    )
    runs: list[str] = []
    real = evaluate.evaluate_trial
    monkeypatch.setattr(
        evaluate, "evaluate_trial", lambda deps, r, now: runs.append(r.id) or real(deps, r, now)
    )
    fresh = cockpit.store.get_proposal(row.id)
    assert fresh is not None
    now = int(time.time())

    first = learn_view.scorecard(cockpit.deps, fresh, now)
    again = learn_view.scorecard(cockpit.deps, fresh, now + 30)
    later = learn_view.scorecard(cockpit.deps, fresh, now + learn_view.SCORECARD_TTL_S + 1)

    assert first == again and later is not None
    assert len(runs) == 2


async def test_a_projects_proposal_has_no_scorecard(cockpit: Cockpit) -> None:
    cockpit.store.create_proposal(
        cockpit.fid, kind="projects", title="x", author_kind="human", author="alice"
    )
    resp = await _get(cockpit, "/proposals/C-1/scorecard")
    assert resp.status_code == 200 and resp.json()["scorecard"] is None
    missing = await _get(cockpit, "/proposals/C-9/scorecard")
    assert missing.status_code == 404 and error_code(missing) == "unknown_proposal"


# ── Propose change (operators) ─────────────────────────────────


async def test_an_operator_proposes_a_change_for_the_owners(cockpit: Cockpit) -> None:
    resp = await _post(
        cockpit,
        "/definition/proposals",
        {
            "changes": [_change("loops.implement.attempts", 4)],
            "rationale": "Three tries ran out on two cards this week.",
            "evidence": ["F-7", "INC-13"],
            "expected_effect": {"metric": "loop_exhaustion_rate", "direction": "down"},
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    proposal = body["proposal"]
    assert proposal["ref"] == "C-1" and proposal["status"] == "pending"
    assert proposal["author_kind"] == "human" and proposal["approval"] == "owner"
    assert proposal["evidence"] == ["F-7", "INC-13"]
    decision = cockpit.store.get_decision(proposal["decision_id"])
    assert decision is not None and decision.assignee == "owners"
    assert decision.ref == body["decision"]
    # Nothing applied: the definition still has 3 tries.
    resolved = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)
    assert resolved is not None and resolved.doc["loops"]["implement"]["attempts"] == 3


async def test_an_owner_approves_it_as_a_trial(cockpit: Cockpit) -> None:
    filed = await _post(
        cockpit,
        "/definition/proposals",
        {
            "kind": "rules",
            "changes": [
                {
                    "op": "add",
                    "path": "rules",
                    "value": {
                        "role": "ic",
                        "text": "Run the package's unit tests before every push.",
                    },
                }
            ],
            "rationale": "Two CI failures were unit tests.",
        },
    )
    assert filed.status_code == 201, filed.text
    # Pending, the rule has no id yet: it gets one when it applies.
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None and list(row.after) == ["rules.new"]
    assert "id" not in row.after["rules.new"] and row.based_on == {"def_version": 1}
    assert row.title.startswith("+ new rule · ic · ")

    operator = await _post(cockpit, "/proposals/C-1/apply", {}, user="carol")
    owner = await _post(cockpit, "/proposals/C-1/apply", {}, user="alice")

    assert operator.status_code == 403, operator.text
    assert owner.status_code == 200, owner.text
    assert owner.json()["proposal"]["status"] == "trial"
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None and list(row.after) == ["rules.R-1"]
    assert row.title.startswith("+ R-1 · ic · ")
    resolved = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)
    assert resolved is not None
    [rule] = resolved.doc["rules"]
    assert rule["role"] == "ic" and rule["state"] == "trial" and rule["origin"] == "C-1"


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"changes": [_change("gates.review", "off")], "rationale": "x"}, 422, "tier0"),
        ({"changes": [_change("loops.implement.attempts", 40)], "rationale": "x"}, 422, None),
        (
            {"changes": [_change("loops.implement.attempts", 3)], "rationale": "x"},
            400,
            "no_change",
        ),
        ({"changes": [], "rationale": "x"}, 400, "invalid_body"),
        (
            {"kind": "rules", "changes": [_change("cadence.lead", 7200)], "rationale": "x"},
            400,
            "invalid_body",
        ),
    ],
)
async def test_bad_changes_are_refused(
    cockpit: Cockpit, body: dict[str, Any], status: int, code: str | None
) -> None:
    resp = await _post(cockpit, "/definition/proposals", body)

    assert resp.status_code == status, resp.text
    if code:
        assert error_code(resp) == code
    assert cockpit.store.list_proposals(cockpit.fid, kind=["tuning", "rules"]) == []


async def test_viewers_and_strangers_cannot_propose(cockpit: Cockpit) -> None:
    body = {"changes": [_change("cadence.lead", 7200)], "rationale": "slower"}
    stranger = await _post(cockpit, "/definition/proposals", body, user="bob")
    assert stranger.status_code in (403, 404)

    cockpit.permissions.grant("dave", cockpit.fid, LEVEL_READ)
    assert (await _get(cockpit, "/versions", user="dave")).status_code == 200
    viewer = await _post(cockpit, "/definition/proposals", body, user="dave")
    assert viewer.status_code == 403, viewer.text
    assert cockpit.store.list_proposals(cockpit.fid, kind=["tuning", "rules"]) == []


# ── Read marks reach the reader's other tabs ───────────────────


async def test_a_read_mark_announces_a_reads_frame(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.factory import live

    frames: list[list[str]] = []
    monkeypatch.setattr(
        live,
        "announce_factory_changed",
        lambda _state, _fid, scopes=("snapshot",): frames.append(list(scopes)),
    )
    cockpit.store.append_chat(
        cockpit.fid, thread="factory", author_kind="human", author="carol", body="hi"
    )

    resp = await cockpit.client.put(
        f"{_base(cockpit)}/chat/reads",
        json={"thread": "factory", "last_seq": 1},
        headers={"X-Test-User": "alice"},
    )

    assert resp.status_code == 200, resp.text
    assert ["reads"] in frames

    # A mark that doesn't move (an older tab catching up) wakes nobody.
    frames.clear()
    stale = await cockpit.client.put(
        f"{_base(cockpit)}/chat/reads",
        json={"thread": "factory", "last_seq": 0},
        headers={"X-Test-User": "alice"},
    )
    assert stale.status_code == 200, stale.text
    assert stale.json()["last_seq"] == 1
    assert frames == []
