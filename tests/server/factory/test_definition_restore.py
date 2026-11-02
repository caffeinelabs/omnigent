"""Restore and Undo: history stays linear.

``POST …/definition/restore {version}`` stores an old version's document as
the next version (a human ``C-n``); re-pinning a fresh version's parent within
10 minutes is that version's Undo and stamps ``undone_by`` on it. Undoing a
restore is another restore, or that same Undo.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.factory import definition
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import (
    definition_cockpit,
    put,
    row,
    texts,
    url,
    version,
)

ROUNDS = {"op": "replace", "path": "loops.review.rounds", "value": 2}
PER_HEAD = {"op": "replace", "path": "loops.verify.per_head", "value": 1}
GOAL = {"op": "add", "path": "strategy.goals", "value": {"statement": "Faster setup (sample)"}}


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


async def _restore(cockpit: Cockpit, user: str = "alice", **body: Any) -> httpx.Response:
    return await cockpit.client.post(
        url(cockpit, "/restore"), json=body, headers={"X-Test-User": user}
    )


async def _versions(cockpit: Cockpit) -> list[dict[str, Any]]:
    resp = await cockpit.client.get(url(cockpit, "/versions"), headers={"X-Test-User": "carol"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _doc(cockpit: Cockpit, number: int) -> dict[str, Any]:
    found = cockpit.store.get_definition(cockpit.fid, number)
    assert found is not None
    return found.doc


async def test_a_restore_stores_the_old_document_as_the_next_version(cockpit: Cockpit) -> None:
    for change in (ROUNDS, PER_HEAD, GOAL):
        assert (await put(cockpit, change)).status_code == 200

    resp = await _restore(cockpit, version=2, note="the goal was premature (sample)")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "proposal": "C-4",
        "proposal_id": row(cockpit, 4).id,
        "status": "applied",
        "def_version": 5,
        "restored": 2,
        "from": 4,
        "ended": [],
    }
    # Linear: v5 is new, its parent is v4, and it says what v2 said.
    assert version(cockpit) == 5
    assert definition.diff(_doc(cockpit, 2), _doc(cockpit, 5)) == []
    stored = cockpit.store.get_definition(cockpit.fid, 5)
    assert stored is not None and (stored.parent_version, stored.created_via) == (4, "human")
    restore = row(cockpit, 4)
    assert (restore.class_, restore.author, restore.result["restore_of"]) == ("human", "alice", 2)
    assert set(restore.after) == {"loops.verify.per_head", "strategy.goals.G-1"}
    assert restore.title.startswith("Restore v2: ")
    # A goal number is never handed out again, even after a restore took the goal out.
    assert _doc(cockpit, 5)["goal_seq"] == 1
    versions = await _versions(cockpit)
    assert [v["version"] for v in versions] == [5, 4, 3, 2, 1]
    assert versions[0]["restored_from"] == 2 and versions[0]["proposal_ref"] == "C-4"
    assert all(v["restored_from"] is None for v in versions[1:])
    assert any("C-4 restores v2" in t for t in texts(cockpit, "pl"))
    assert [e.data["restored"] for e in cockpit.events("definition.restored")] == [2]


async def test_undo_of_a_restore_is_another_restore(cockpit: Cockpit) -> None:
    assert (await put(cockpit, ROUNDS)).status_code == 200
    assert (await _restore(cockpit, version=1)).status_code == 200

    again = await _restore(cockpit, version=2)

    assert again.status_code == 200, again.text
    assert again.json()["def_version"] == 4 and version(cockpit) == 4
    assert definition.value(cockpit.deps, cockpit.fid, "loops.review.rounds") == 2
    assert [v["version"] for v in await _versions(cockpit)] == [4, 3, 2, 1]


async def test_a_ten_minute_undo_re_pins_the_parent_and_stamps_undone_by(
    cockpit: Cockpit,
) -> None:
    assert (await put(cockpit, ROUNDS)).status_code == 200
    assert (await _restore(cockpit, version=1)).status_code == 200

    undo = await cockpit.post(url(cockpit, "/pin"), {"version": 2})

    assert undo.status_code == 200, undo.text
    assert version(cockpit) == 2
    versions = {v["version"]: v for v in await _versions(cockpit)}
    assert versions[3]["undone_by"] == "alice" and versions[3]["undone_at"]
    assert versions[2]["undone_by"] is None and versions[2]["pinned"] is True
    # The restore it undid reads as reverted, with why.
    restore = row(cockpit, 2)
    assert (restore.status, restore.result["revert_reason"]) == ("reverted", "undone by alice")
    assert [e.data["version"] for e in cockpit.events("definition.undone")] == [3]


async def test_a_late_or_unrelated_pin_is_not_an_undo(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await put(cockpit, ROUNDS)).status_code == 200
    assert (await put(cockpit, PER_HEAD)).status_code == 200
    # v1 is not v3's parent, and v1 has no parent: plain pins.
    assert (await cockpit.post(url(cockpit, "/pin"), {"version": 1})).status_code == 200
    assert (await cockpit.post(url(cockpit, "/pin"), {"version": 2})).status_code == 200
    # v2's parent is v1, but v2 is older than the Undo window.
    original = cockpit.store.get_definition

    def aged(factory_id: str, number: int) -> Any:
        found = original(factory_id, number)
        if found is None or number != 2:
            return found
        return replace(found, created_at=found.created_at - definition.UNDO_WINDOW_S - 1)

    monkeypatch.setattr(cockpit.store, "get_definition", aged)

    late = await cockpit.post(url(cockpit, "/pin"), {"version": 1})

    assert late.status_code == 200, late.text
    assert version(cockpit) == 1
    assert cockpit.events("definition.undone") == []
    assert all(v["undone_by"] is None for v in await _versions(cockpit))
    assert row(cockpit, 1).status == "applied"


async def test_a_restore_ends_the_trials_it_takes_out_and_keeps_the_rest(
    cockpit: Cockpit,
) -> None:
    quiet = {"op": "replace", "path": "alerts.quiet_m", "value": 20}
    assert (await put(cockpit, ROUNDS, trial=True)).status_code == 200
    assert (await put(cockpit, quiet, trial=True)).status_code == 200

    # v2 still has C-2's alert out of force but C-1's rounds in it.
    resp = await _restore(cockpit, version=2)

    assert resp.status_code == 200, resp.text
    assert resp.json()["ended"] == ["C-2"]
    assert row(cockpit, 1).status == "trial"
    ended = row(cockpit, 2)
    assert ended.status == "reverted"
    assert ended.result["revert_reason"] == "v2 restored by alice as v4"


@pytest.mark.parametrize(
    ("body", "user", "headers", "status", "code"),
    [
        ({"version": 1}, "alice", {}, 400, "no_change"),
        ({"version": 9}, "alice", {}, 404, "unknown_version"),
        ({"version": 1}, "carol", {}, 403, None),
        ({"version": 1}, "alice", {RUNNER_TUNNEL_TOKEN_HEADER: "tok"}, 403, "human_only"),
        ({"version": 1, "base_version": 5}, "alice", {}, 409, "stale_definition"),
        ({}, "alice", {}, 400, "invalid_body"),
    ],
)
async def test_the_restore_route_is_checked(
    cockpit: Cockpit,
    body: dict[str, Any],
    user: str,
    headers: dict[str, str],
    status: int,
    code: str | None,
) -> None:
    if body.get("version") != 1 or "base_version" in body:
        assert (await put(cockpit, ROUNDS)).status_code == 200
    resp = await cockpit.client.post(
        url(cockpit, "/restore"), json=body, headers={"X-Test-User": user, **headers}
    )
    assert resp.status_code == status, resp.text
    if code is not None:
        assert error_code(resp) == code
