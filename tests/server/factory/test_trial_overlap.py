"""No two trials on one block; strategy and the product profile never run as a trial.

A trial on a block of the definition (``briefs``, ``role_defaults``, and
``discovery`` once it trials) refuses a second trial there with 409
``trial_overlap`` naming the running one: two trials on one block confound
each other's guards. Applying directly stays possible. Today's settings and
rules trial side by side, as before. Strategy and profile changes are judged
on outcomes: asking for a trial is 422 ``not_trialable``. So are discovery
and Scout's brief until the discovery guards exist.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import definition, evaluate
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import (
    answer,
    definition_cockpit,
    propose,
    put,
    row,
    version,
)

BRIEF_IC = {"op": "replace", "path": "briefs.ic", "value": "Run the tests first (sample)."}
BRIEF_REVIEWER = {"op": "replace", "path": "briefs.reviewer", "value": "Quote the line (sample)."}
ROLE = {"op": "replace", "path": "role_defaults.ic.model", "value": "sonnet"}
ROUNDS = {"op": "replace", "path": "loops.review.rounds", "value": 2}
ATTEMPTS = {"op": "replace", "path": "loops.implement.attempts", "value": 4}
QUIET = {"op": "replace", "path": "alerts.quiet_m", "value": 20}


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


async def _operator_proposes(cockpit: Cockpit, change: dict[str, Any]) -> dict[str, Any]:
    resp = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/definition/proposals",
        {"changes": [change], "rationale": "clearer reviews (sample)"},
        user="carol",
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_a_second_trial_on_a_block_is_refused_with_the_running_one(
    cockpit: Cockpit,
) -> None:
    assert (await put(cockpit, BRIEF_IC, trial=True)).status_code == 200

    second = await put(cockpit, BRIEF_REVIEWER, trial=True)

    assert second.status_code == 409 and error_code(second) == "trial_overlap"
    body = second.json()
    assert (body["trial"], body["block"], body["day"], body["of"]) == ("C-1", "briefs", 1, 2)
    assert "apply it directly, or wait for C-1 to end" in body["error"]["message"]
    # Refused up front: no version, no proposal.
    assert version(cockpit) == 2 and cockpit.store.get_proposal_by_number(cockpit.fid, 2) is None


async def test_another_block_and_a_direct_apply_are_fine(cockpit: Cockpit) -> None:
    assert (await put(cockpit, BRIEF_IC, trial=True)).status_code == 200

    other = await put(cockpit, ROLE, trial=True)
    direct = await put(cockpit, BRIEF_REVIEWER)

    assert other.status_code == 200, other.text
    assert direct.status_code == 200, direct.text
    assert [row(cockpit, n).status for n in (1, 2, 3)] == ["trial", "trial", "applied"]


async def test_today_s_settings_trial_side_by_side_as_before(cockpit: Cockpit) -> None:
    lead = {"op": "replace", "path": "cadence.lead", "value": 7200}
    project_lead = {"op": "replace", "path": "cadence.project_lead", "value": 10800}
    for change in (ROUNDS, ATTEMPTS, QUIET, project_lead, lead):
        resp = await put(cockpit, change, trial=True)
        assert resp.status_code == 200, (change, resp.text)
    assert [row(cockpit, n).status for n in range(1, 6)] == ["trial"] * 5


async def test_rules_trial_one_by_one(cockpit: Cockpit) -> None:
    for text in ("Lint first (sample).", "Test first (sample)."):
        rule = {"op": "add", "path": "rules", "value": {"role": "ic", "text": text}}
        resp = await put(cockpit, rule, trial=True)
        assert resp.status_code == 200, resp.text
    assert [row(cockpit, n).status for n in (1, 2)] == ["trial", "trial"]


async def test_strategy_and_the_profile_are_not_trialable(cockpit: Cockpit) -> None:
    focus = {"op": "replace", "path": "strategy.non_goals", "value": ["Billing (sample)"]}
    name = {"op": "replace", "path": "product.name", "value": "Sample"}

    trial = await put(cockpit, focus, name, trial=True)
    applied = await put(cockpit, focus, name)

    assert trial.status_code == 422 and error_code(trial) == "not_trialable"
    assert trial.json()["reason"] == "judged on outcomes"
    assert "product profile and strategy changes" in trial.json()["error"]["message"]
    assert applied.status_code == 200, applied.text
    assert row(cockpit).status == "applied"


@pytest.mark.parametrize(
    "change",
    [
        {"op": "replace", "path": "discovery.rubric.off_focus_factor", "value": 0.4},
        {"op": "replace", "path": "discovery.thresholds.min_accounts", "value": 3},
        {"op": "replace", "path": "briefs.scout", "value": "Quote at most twice (sample)."},
    ],
)
async def test_discovery_waits_for_its_own_guards(
    cockpit: Cockpit, change: dict[str, Any]
) -> None:
    trial = await put(cockpit, change, trial=True)
    applied = await put(cockpit, change)

    # Delivery guards over 48 h would judge it on the wrong things: it applies as a version.
    assert trial.status_code == 422 and error_code(trial) == "not_trialable", trial.text
    assert trial.json()["reason"] == "needs discovery guards"
    assert "discovery guards" in trial.json()["error"]["message"]
    assert applied.status_code == 200, applied.text
    assert row(cockpit).status == "applied"


async def test_an_owner_approval_waits_while_the_block_is_on_trial(cockpit: Cockpit) -> None:
    filed = await _operator_proposes(cockpit, BRIEF_REVIEWER)
    assert filed["proposal"]["warnings"] == []
    assert (await put(cockpit, BRIEF_IC, trial=True)).status_code == 200

    refused = await answer(cockpit, 1)

    assert refused.status_code == 409 and error_code(refused) == "trial_overlap", refused.text
    assert refused.json()["trial"] == "C-2"
    assert row(cockpit, 1).status == "pending"
    decision = cockpit.store.get_decision(row(cockpit, 1).decision_id)
    assert decision is not None and decision.status == "open"

    kept = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-2/keep", {})
    assert kept.status_code == 200, kept.text
    approved = await answer(cockpit, 1)
    assert approved.status_code == 200, approved.text
    assert row(cockpit, 1).status == "trial"


async def test_a_proposal_filed_while_its_block_trials_says_so(cockpit: Cockpit) -> None:
    assert (await put(cockpit, BRIEF_IC, trial=True)).status_code == 200
    assert (await put(cockpit, ROUNDS, trial=True)).status_code == 200

    operator = await _operator_proposes(cockpit, BRIEF_REVIEWER)
    agent = await propose(cockpit, changes=[ATTEMPTS])

    assert operator["proposal"]["warnings"] == [
        "C-1 is trialing briefs: approving this waits until C-1 ends"
    ]
    # Today's settings hold no block: an agent's proposal next to a trial waits for nothing.
    assert agent.status_code == 200, agent.text
    assert agent.json()["warnings"] == []


async def test_a_cosign_of_today_s_settings_is_not_held_by_a_trial(cockpit: Cockpit) -> None:
    cadence = {"op": "replace", "path": "cadence.demo_project_lead", "value": 1200}
    assert (await propose(cockpit, changes=[cadence])).json()["status"] == "awaiting_cosign"
    held = {"op": "replace", "path": "cadence.project_lead", "value": 10800}
    assert (await put(cockpit, held, trial=True)).status_code == 200

    cosigned = await propose(cockpit, key="lead_a", op="cosign", id="C-1")

    assert cosigned.status_code == 200, cosigned.text
    assert row(cockpit, 1).status == "trial"


async def test_a_trial_that_lost_the_race_for_its_block_is_refused(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await put(cockpit, BRIEF_IC, trial=True)).status_code == 200
    real = evaluate.overlapping_trial
    calls: list[str] = []

    def racing(*args: Any, **kwargs: Any) -> Any:
        # The up-front check ran before C-1 held the block; the check under the lock sees it.
        calls.append("check")
        return None if len(calls) == 1 else real(*args, **kwargs)

    monkeypatch.setattr(evaluate, "overlapping_trial", racing)

    lost = await put(cockpit, BRIEF_REVIEWER, trial=True)

    assert lost.status_code == 409 and error_code(lost) == "trial_overlap", lost.text
    assert len(calls) == 2
    refused = row(cockpit, 2)
    assert refused.status == "refused" and (refused.result or {})["reason"] == "trial_overlap"
    assert refused.def_version_to is None and version(cockpit) == 2


async def test_two_trials_racing_for_one_block_leave_one_running(cockpit: Cockpit) -> None:
    first, second = await asyncio.gather(
        put(cockpit, BRIEF_IC, trial=True), put(cockpit, BRIEF_REVIEWER, trial=True)
    )

    codes = sorted([first.status_code, second.status_code])
    assert codes == [200, 409], (first.text, second.text)
    loser = first if first.status_code == 409 else second
    assert error_code(loser) == "trial_overlap"
    trials = cockpit.store.list_proposals(cockpit.fid, status="trial")
    assert len(trials) == 1 and version(cockpit) == 2


def test_the_trial_day_counts_from_the_start(cockpit: Cockpit) -> None:
    found = cockpit.store.create_proposal(
        cockpit.fid,
        kind="tuning",
        title="x",
        author_kind="human",
        author="alice",
        status="trial",
        applied_at=1_000_000,
        trial_until=1_000_000 + 14 * 86400,
        after={"discovery.rubric.window_days": 60},
    )[0]

    assert evaluate.trial_day(found, 1_000_000 + 3 * 86400 + 5) == (4, 14)
    assert evaluate.overlapping_trial(cockpit.deps, cockpit.fid, ["discovery.enabled"]) == (
        found,
        "discovery",
    )
    assert evaluate.overlapping_trial(cockpit.deps, cockpit.fid, ["rules.R-1"]) is None
    assert evaluate.overlapping_trial(cockpit.deps, cockpit.fid, ["loops.review.rounds"]) is None
    assert definition.trial_blocks(found.after) == ["discovery"]
