"""Taking back a person's Apply on a proposal's decision (``POST …/decisions/{id}/undo``).

Undo of an answer puts things back as they were before it: the proposal's own
Undo runs (its charters put back), then the proposal waits for a person again
under a fresh decision, so the work held for it waits again and the ask is in
Needs you again. The answered decision stays answered (history).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import proposals, tasks
from omnigent.stores.factory_store import now_s
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import header_cockpit, propose


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value


V2 = "Add GET /version v2."
"""The goal the proposal sets (an agent's update waits for a person)."""


def _goal(cockpit: Cockpit) -> str:
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None
    return str((row.charter or {}).get("goal"))


def _held_task(cockpit: Cockpit, on: str) -> Any:
    row, _ = cockpit.store.upsert_task(
        cockpit.fid, "api-version", "debit", title="Debit each turn", state="running"
    )
    hold = {"on": on, "kind": "proposal", "ref": on, "until": now_s() + 86400}
    return cockpit.store.update_task(row.id, hold=hold) or row


async def _filed(cockpit: Cockpit) -> Any:
    resp = await propose(cockpit, {"op": "update", "slug": "api-version", "patch": {"goal": V2}})
    assert resp.status_code == 200, resp.text
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None and row.status == "pending" and row.decision_id
    return row


async def _answer(cockpit: Cockpit, decision_id: str, option: str = "apply") -> Any:
    return await cockpit.post(f"/v1/factory/decisions/{decision_id}/answer", {"option_id": option})


async def _undo(cockpit: Cockpit, decision_id: str, user: str = "alice") -> Any:
    return await cockpit.post(f"/v1/factory/decisions/{decision_id}/undo", user=user)


async def test_undo_of_an_apply_puts_the_proposal_and_its_hold_back(cockpit: Cockpit) -> None:
    filed = await _filed(cockpit)
    task = _held_task(cockpit, "C-1")
    answered = await _answer(cockpit, filed.decision_id)
    assert answered.status_code == 200, answered.text
    assert _goal(cockpit) == V2
    # The tick's sweep lets the hold lapse: what it named closed.
    lapsed = await tasks.sweep_holds(cockpit.app.state, cockpit.fid, [task], now=time.time())
    assert lapsed == [task.ref]

    resp = await _undo(cockpit, filed.decision_id)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["proposal"]["status"] == "pending"
    fresh = body["decision"]
    old = cockpit.store.get_decision(filed.decision_id)
    assert old is not None and old.status == "answered"
    assert fresh["ref"] != old.ref and fresh["status"] == "open"
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None and row.decision_id == fresh["id"]
    assert row.undo_until is None and row.applied_at is None
    assert _goal(cockpit) == "Add GET /version."
    held = cockpit.store.get_task(task.id)
    assert held is not None and held.hold is not None and held.hold["ref"] == "C-1"
    told = [t for t in cockpit.mailer.texts_to(cockpit.world.id("pl")) if "HOLD BACK" in t]
    assert told and told[0].startswith(f"[Factory notice · HOLD BACK · {task.ref}]")
    [undone] = cockpit.events("proposal.undone")
    assert undone.data["back_to"] == "pending"
    assert undone.data["decision"] == fresh["ref"]
    assert undone.data["held_again"] == [task.ref]
    back = [
        t for t in cockpit.mailer.texts_to(cockpit.world.id("lead_a")) if "back to pending" in t
    ]
    assert back and f"{fresh['ref']} asks a person again" in back[0]


async def test_the_fresh_ask_applies_again(cockpit: Cockpit) -> None:
    filed = await _filed(cockpit)
    await _answer(cockpit, filed.decision_id)
    fresh = (await _undo(cockpit, filed.decision_id)).json()["decision"]

    again = await _answer(cockpit, fresh["id"])

    assert again.status_code == 200, again.text
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None and row.status == "applied" and row.undo_until is not None
    assert _goal(cockpit) == V2


async def test_only_an_applied_answer_within_its_window_is_taken_back(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    filed = await _filed(cockpit)
    open_one = await _undo(cockpit, filed.decision_id)
    assert (open_one.status_code, error_code(open_one)) == (409, "undo_closed")
    assert (await _undo(cockpit, "nope")).status_code == 404

    await _answer(cockpit, filed.decision_id)
    late = now_s() + proposals.UNDO_WINDOW_S + 1
    monkeypatch.setattr(proposals, "now_s", lambda: late)
    closed = await _undo(cockpit, filed.decision_id)

    assert (closed.status_code, error_code(closed)) == (409, "undo_closed")
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None and row.status == "applied"


async def test_a_reject_or_a_second_undo_is_refused(cockpit: Cockpit) -> None:
    filed = await _filed(cockpit)
    await _answer(cockpit, filed.decision_id)
    first = await _undo(cockpit, filed.decision_id)
    assert first.status_code == 200, first.text

    second = await _undo(cockpit, filed.decision_id)
    fresh = first.json()["decision"]
    await _answer(cockpit, fresh["id"], "reject")
    rejected = await _undo(cockpit, fresh["id"])

    assert (second.status_code, error_code(second)) == (409, "undo_closed")
    assert (rejected.status_code, error_code(rejected)) == (409, "undo_closed")


async def test_someone_without_access_cannot_take_an_answer_back(cockpit: Cockpit) -> None:
    filed = await _filed(cockpit)
    await _answer(cockpit, filed.decision_id)

    resp = await _undo(cockpit, filed.decision_id, user="bob")

    assert resp.status_code in (403, 404), resp.text
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None and row.status == "applied"
