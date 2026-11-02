"""No two trials on one block; strategy and the product profile never run as a trial.

A trial on a top-level block (``loops``, ``discovery``, …) refuses a second
trial there with 409 ``trial_overlap`` naming the running one: two trials on
one block confound each other's guards. Applying directly stays possible.
Rules trial one by one, as before. Strategy and profile changes are judged
on outcomes: asking for a trial is 422 ``not_trialable``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

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

ROUNDS = {"op": "replace", "path": "loops.review.rounds", "value": 2}
ATTEMPTS = {"op": "replace", "path": "loops.implement.attempts", "value": 4}
QUIET = {"op": "replace", "path": "alerts.quiet_m", "value": 20}
RULE_EFFECT = {"metric": "review_rounds", "direction": "down"}


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


async def test_a_second_trial_on_a_block_is_refused_with_the_running_one(
    cockpit: Cockpit,
) -> None:
    assert (await put(cockpit, ROUNDS, trial=True)).status_code == 200

    second = await put(cockpit, ATTEMPTS, trial=True)

    assert second.status_code == 409 and error_code(second) == "trial_overlap"
    body = second.json()
    assert (body["trial"], body["block"], body["day"], body["of"]) == ("C-1", "loops", 1, 2)
    assert "apply it directly, or wait for C-1 to end" in body["error"]["message"]
    # Refused up front: no version, no proposal.
    assert version(cockpit) == 2 and cockpit.store.get_proposal_by_number(cockpit.fid, 2) is None


async def test_another_block_and_a_direct_apply_are_fine(cockpit: Cockpit) -> None:
    assert (await put(cockpit, ROUNDS, trial=True)).status_code == 200

    other = await put(cockpit, QUIET, trial=True)
    direct = await put(cockpit, ATTEMPTS)

    assert other.status_code == 200, other.text
    assert direct.status_code == 200, direct.text
    assert [row(cockpit, n).status for n in (1, 2, 3)] == ["trial", "trial", "applied"]


async def test_discovery_is_one_block(cockpit: Cockpit) -> None:
    rubric = {"op": "replace", "path": "discovery.rubric.decay_half_life_days", "value": 45}
    thresholds = {"op": "replace", "path": "discovery.thresholds.min_accounts", "value": 3}
    assert (await put(cockpit, rubric, trial=True)).status_code == 200

    second = await put(cockpit, thresholds, trial=True)

    assert second.status_code == 409 and second.json()["block"] == "discovery"


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


async def test_an_owner_approval_waits_while_the_block_is_on_trial(cockpit: Cockpit) -> None:
    filed = await propose(cockpit, changes=[ATTEMPTS])
    assert filed.status_code == 200, filed.text
    assert filed.json()["warnings"] == []
    assert (await put(cockpit, ROUNDS, trial=True)).status_code == 200

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
    assert (await put(cockpit, ROUNDS, trial=True)).status_code == 200

    filed = await propose(cockpit, changes=[ATTEMPTS])
    per_head = {"op": "replace", "path": "loops.verify.per_head", "value": 1}
    operator = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/definition/proposals",
        {"changes": [per_head], "rationale": "fewer verify runs (sample)"},
        user="carol",
    )

    waits = "C-1 is trialing loops: approving this waits until C-1 ends"
    assert filed.status_code == 200, filed.text
    assert filed.json()["warnings"] == [waits]
    assert operator.status_code == 201, operator.text
    assert operator.json()["proposal"]["warnings"] == [waits]


async def test_a_cosign_waits_while_the_block_is_on_trial(cockpit: Cockpit) -> None:
    cadence = {"op": "replace", "path": "cadence.demo_project_lead", "value": 1200}
    assert (await propose(cockpit, changes=[cadence])).json()["status"] == "awaiting_cosign"
    held = {"op": "replace", "path": "cadence.project_lead", "value": 10800}
    assert (await put(cockpit, held, trial=True)).status_code == 200

    refused = await propose(cockpit, key="lead_a", op="cosign", id="C-1")

    assert refused.status_code == 409 and error_code(refused) == "trial_overlap"
    assert row(cockpit, 1).status == "awaiting_cosign"


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
    assert definition.trial_blocks(found.after) == ["discovery"]
