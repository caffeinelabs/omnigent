"""Trial evaluation on synthetic data: kept, auto-revert on a guard regression,
inconclusive at small n (an owners decision, never auto-kept), and the
unattended revert at 2× the window.

A person's edit with ``trial: true`` starts each trial; the test then moves its
window so that "now" is its last hour, and seeds merged PRs and incidents
around it.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import definition, evaluate, issues
from omnigent.server.factory.telemetry import metrics
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import REPO, header_cockpit

H = 3600
WINDOW = 48 * H


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


async def _trial(cockpit: Cockpit, effect: dict[str, Any] | None = None) -> tuple[Any, int]:
    """A rules change on trial whose 48 h window ends an hour from now."""
    body = {
        "changes": [
            {"op": "add", "path": "rules", "value": {"role": "ic", "text": "Run the tests."}}
        ],
        "trial": True,
        "expected_effect": effect or {"metric": "throughput", "direction": "up"},
    }
    resp = await cockpit.client.put(
        f"/v1/factory/factories/{cockpit.fid}/definition",
        json=body,
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 200, resp.text
    now = int(time.time())
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None and row.status == "trial"
    applied = now - WINDOW + H
    row = cockpit.store.update_proposal(row.id, applied_at=applied, trial_until=applied + WINDOW)
    assert row is not None
    return row, applied


def _merged(cockpit: Cockpit, number: int, at: int) -> None:
    cockpit.store.upsert_pr(
        cockpit.fid,
        REPO,
        number,
        state="merged",
        project_slug="api-version",
        opened_at=at - 2 * H,
        first_ready_at=at - H,
        merged_at=at,
    )


def _row(cockpit: Cockpit) -> Any:
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None
    return row


async def test_target_met_with_enough_n_and_guards_held_is_kept(cockpit: Cockpit) -> None:
    row, applied = await _trial(cockpit)
    _merged(cockpit, 9001, applied - 5 * H)
    for i, at in enumerate((applied + H, applied + 10 * H, applied + 20 * H)):
        _merged(cockpit, 9100 + i, at)

    done = await evaluate.tick(cockpit.app.state, cockpit.fid, now=row.trial_until + 1)

    assert done == ["C-1:kept"]
    row = _row(cockpit)
    assert row.status == "kept"
    ev = row.result["evaluation"]
    assert ev["outcome"] == "kept" and ev["n"] == 3 and ev["min_n"] == evaluate.MIN_MERGED
    assert ev["target"]["met"] is True
    assert {g["metric"] for g in ev["guards"]} == set(metrics.GUARDS)
    assert all(g["status"] != "regressed" for g in ev["guards"])
    # Kept trial rules become active.
    resolved = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)
    assert resolved is not None and resolved.doc["rules"][0]["state"] == "active"
    assert "proposal.trial_ended" in [e.kind for e in cockpit.events()]


async def test_a_guard_regression_reverts_automatically(cockpit: Cockpit) -> None:
    row, applied = await _trial(cockpit)
    for i, at in enumerate((applied + H, applied + 2 * H, applied + 3 * H)):
        _merged(cockpit, 9200 + i, at)
    for rule in ("member.dead", "agent.repeat", "agent.quiet"):
        await issues.raise_issue(
            cockpit.app.state, cockpit.fid, rule=rule, target="member:x", summary=rule
        )

    done = await evaluate.tick(cockpit.app.state, cockpit.fid, now=row.trial_until + 1)

    assert done == ["C-1:reverted"]
    row = _row(cockpit)
    assert row.status == "reverted" and row.reverted_by == "server"
    incidents = next(g for g in row.result["evaluation"]["guards"] if g["metric"] == "incidents")
    assert (incidents["baseline"], incidents["trial"], incidents["status"]) == (0, 3, "regressed")
    assert row.result["revert_reason"].startswith("auto-revert: guard regressed: incidents")
    assert definition.definition_for(cockpit.deps, cockpit.fid, fresh=True).version == 1  # type: ignore[union-attr]
    pl_texts = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    assert any(
        t.startswith("[Factory control · RULES UPDATE · C-1 · by server]") for t in pl_texts
    )


async def test_small_n_is_inconclusive_and_goes_to_the_owners(cockpit: Cockpit) -> None:
    row, applied = await _trial(cockpit)
    _merged(cockpit, 9300, applied + H)

    done = await evaluate.tick(cockpit.app.state, cockpit.fid, now=row.trial_until + 1)

    assert done == ["C-1:inconclusive"]
    row = _row(cockpit)
    assert row.status == "trial"  # never auto-kept
    decision = cockpit.store.get_decision(row.result["keep_decision_id"])
    assert decision is not None and decision.assignee == "owners"
    assert [o["id"] for o in decision.options] == ["keep", "revert"]
    assert next(o for o in decision.options if o.get("recommended"))["id"] == "revert"
    assert "n=1 < 3" in decision.question
    # Evaluated once: the next tick does not ask again.
    assert await evaluate.tick(cockpit.app.state, cockpit.fid, now=row.trial_until + 60) == []

    resp = await cockpit.post(f"/v1/factory/decisions/{decision.id}/answer", {"option_id": "keep"})

    assert resp.status_code == 200, resp.text
    assert _row(cockpit).status == "kept"


async def test_an_unattended_trial_reverts_at_twice_its_window(cockpit: Cockpit) -> None:
    row, applied = await _trial(cockpit)
    await evaluate.tick(cockpit.app.state, cockpit.fid, now=row.trial_until + 1)
    decision_id = _row(cockpit).result["keep_decision_id"]

    early = await evaluate.tick(cockpit.app.state, cockpit.fid, now=applied + 2 * WINDOW - 60)
    late = await evaluate.tick(cockpit.app.state, cockpit.fid, now=applied + 2 * WINDOW + 1)

    assert early == [] and late == ["C-1:unattended"]
    row = _row(cockpit)
    assert row.status == "reverted"
    assert row.result["revert_reason"].startswith("unattended")
    decision = cockpit.store.get_decision(decision_id)
    assert decision is not None and decision.status == "expired"


def test_guard_rules() -> None:
    regressed = evaluate._regressed
    assert regressed("pp_drop", 0.10, 0.80, 0.69) is True
    assert regressed("pp_drop", 0.10, 0.80, 0.71) is False
    assert regressed("pct_rise", 0.10, 10.0, 11.5) is True
    assert regressed("pct_rise", 0.20, 40.0, 47.0) is False
    assert regressed("abs_rise", 1.0, 2.0, 3.0) is False
    assert regressed("abs_rise", 1.0, 2.0, 4.0) is True
    assert regressed("pp_rise", 0.10, 0.1, 0.3) is True
    assert regressed("pp_drop", 0.10, None, 0.5) is None
