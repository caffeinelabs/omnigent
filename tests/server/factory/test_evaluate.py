"""Trial evaluation on synthetic data: kept, auto-revert on a guard regression,
inconclusive at small n (an owners decision, never auto-kept), the unattended
revert one window after the trial ended, and idle trials: extended twice, then
an owners decision whose default is Keep when nothing got worse, unless an
incident or finding names the change.

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

from omnigent.entities.factory import EventDraft
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
    _merged(cockpit, 9400, applied + H)  # some work, just not enough to judge
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


# ── Idle trials ────────────────────────────────────────────────


def _improvements_lines(cockpit: Cockpit) -> list[str]:
    return [m.body for m in cockpit.store.list_chat(cockpit.fid, "improvements", limit=200)]


async def _idle_until_decided(cockpit: Cockpit) -> Any:
    """Run an idle trial through both extensions to its owners decision."""
    row, applied = await _trial(cockpit)
    outcomes = []
    for _ in range(evaluate.MAX_EXTENSIONS + 1):
        outcomes += await evaluate.tick(
            cockpit.app.state, cockpit.fid, now=_row(cockpit).trial_until + 1
        )
    assert outcomes == ["C-1:extended", "C-1:extended", "C-1:inconclusive"]
    row = _row(cockpit)
    assert row.trial_until == applied + 3 * WINDOW
    return row


async def test_an_idle_trial_is_extended_by_its_window_and_says_so(cockpit: Cockpit) -> None:
    row, applied = await _trial(cockpit)
    _merged(cockpit, 9500, applied - 5 * H)  # before the trial: not its activity

    done = await evaluate.tick(cockpit.app.state, cockpit.fid, now=row.trial_until + 1)

    assert done == ["C-1:extended"]
    row = _row(cockpit)
    assert row.status == "trial" and row.trial_until == applied + 2 * WINDOW
    assert row.result["trial_extensions"] == 1 and row.result["trial_window_s"] == WINDOW
    assert "evaluation" not in row.result and "keep_decision_id" not in row.result
    assert cockpit.store.list_decisions(status="open", factory_id=cockpit.fid) == []
    [event] = cockpit.events(evaluate.EXTENDED_KIND)
    line = "C-1 trial extended: no merged PRs since it started — the factory was idle"
    assert event.summary == line and event.severity == "info"
    assert event.data["activity"] == {"merged_prs": 0, "tasks_done": 0, "runs_ended": 0}
    assert cockpit.events("proposal.trial_ended") == []
    # One plain line in #improvements, not another copy of the proposal card.
    assert line in _improvements_lines(cockpit)
    [message] = [m for m in cockpit.store.list_chat(cockpit.fid, "improvements") if m.body == line]
    assert message.card["type"] == "system" and message.card["ref"] == "C-1"
    # Not due again until the extension ends.
    assert await evaluate.tick(cockpit.app.state, cockpit.fid, now=row.trial_until - 60) == []


async def test_finished_work_without_a_merge_is_not_idle(cockpit: Cockpit) -> None:
    row, applied = await _trial(cockpit)
    cockpit.store.append_event(
        EventDraft(
            factory_id=cockpit.fid,
            kind="task.done",
            summary="ic:task-1 finished",
            project_slug="api-version",
            ts=(applied + 2 * H) * 1000,
        )
    )

    done = await evaluate.tick(cockpit.app.state, cockpit.fid, now=row.trial_until + 1)

    assert done == ["C-1:inconclusive"]
    row = _row(cockpit)
    assert row.result["evaluation"]["idle"] is False
    assert row.result["evaluation"]["activity"]["runs_ended"] == 1
    decision = cockpit.store.get_decision(row.result["keep_decision_id"])
    assert next(o for o in decision.options if o.get("recommended"))["id"] == "revert"
    assert "default" not in row.result


async def test_extensions_stop_at_two_then_owners_decide_with_keep_as_default(
    cockpit: Cockpit,
) -> None:
    row = await _idle_until_decided(cockpit)

    assert row.status == "trial" and row.result["trial_extensions"] == 2
    assert row.result["evaluation"]["idle"] is True
    assert row.result["default"] == evaluate.DEFAULT_KEEP
    decision = cockpit.store.get_decision(row.result["keep_decision_id"])
    assert decision is not None and decision.assignee == "owners"
    assert row.result["default_at"] == row.result["evaluation"]["evaluated_at"] + 24 * H
    assert next(o for o in decision.options if o.get("recommended"))["id"] == "keep"
    assert "If nobody answers in 24 h this stays applied because nothing got worse" in (
        decision.question
    )
    assert "Extended 2× by its original length" in decision.context
    lines = _improvements_lines(cockpit)
    assert "C-1 trial extended again: no merged PRs since it started — the factory was idle" in (
        lines
    )


async def test_an_idle_trial_nobody_answers_stays_applied_after_24h(cockpit: Cockpit) -> None:
    row = await _idle_until_decided(cockpit)
    due = row.result["default_at"]
    decision_id = row.result["keep_decision_id"]

    early = await evaluate.tick(cockpit.app.state, cockpit.fid, now=due - 60)
    late = await evaluate.tick(cockpit.app.state, cockpit.fid, now=due + 1)

    assert early == [] and late == ["C-1:kept_unanswered"]
    row = _row(cockpit)
    assert row.status == "kept" and row.decided_by == "server"
    decision = cockpit.store.get_decision(decision_id)
    assert decision is not None and decision.status == "expired"
    [kept] = cockpit.events("proposal.kept")
    assert kept.summary == "C-1 kept by server: nobody answered in 24 h and nothing got worse"
    resolved = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)
    assert resolved is not None and resolved.doc["rules"][0]["state"] == "active"


async def test_an_incident_naming_an_idle_trial_keeps_revert_as_the_default(
    cockpit: Cockpit,
) -> None:
    await _trial(cockpit)
    for _ in range(evaluate.MAX_EXTENSIONS):
        await evaluate.tick(cockpit.app.state, cockpit.fid, now=_row(cockpit).trial_until + 1)
    await issues.raise_issue(
        cockpit.app.state,
        cockpit.fid,
        rule="member.dead",
        target="member:x",
        summary="PL relaunch failed since C-1",
        evidence={"refs": ["C-1"]},
    )

    done = await evaluate.tick(cockpit.app.state, cockpit.fid, now=_row(cockpit).trial_until + 1)

    assert done == ["C-1:inconclusive"]
    row = _row(cockpit)
    assert "default" not in row.result
    decision = cockpit.store.get_decision(row.result["keep_decision_id"])
    assert next(o for o in decision.options if o.get("recommended"))["id"] == "revert"
    # Today's path: unattended, it reverts one original window after the trial ended.
    ended = _row(cockpit).trial_until
    early = await evaluate.tick(cockpit.app.state, cockpit.fid, now=ended + WINDOW - 60)
    late = await evaluate.tick(cockpit.app.state, cockpit.fid, now=ended + WINDOW + 1)
    assert early == [] and late == ["C-1:unattended"] and _row(cockpit).status == "reverted"


async def test_an_incident_in_the_first_idle_window_skips_the_extensions(
    cockpit: Cockpit,
) -> None:
    row, applied = await _trial(cockpit)
    incident = await issues.raise_issue(
        cockpit.app.state,
        cockpit.fid,
        rule="agent.repeat",
        target="proposal:C-1",
        summary="agents loop on the C-1 rule",
    )
    assert incident is not None

    done = await evaluate.tick(cockpit.app.state, cockpit.fid, now=row.trial_until + 1)

    assert done == ["C-1:inconclusive"]
    row = _row(cockpit)
    assert "trial_extensions" not in row.result and "default" not in row.result
    assert row.trial_until == applied + WINDOW
    assert cockpit.events(evaluate.EXTENDED_KIND) == []
    decision = cockpit.store.get_decision(row.result["keep_decision_id"])
    assert next(o for o in decision.options if o.get("recommended"))["id"] == "revert"
    assert decision.context.startswith(f"C-1 is named in {incident.ref}.")
    # Unattended, it reverts as early as before: twice its window.
    late = await evaluate.tick(cockpit.app.state, cockpit.fid, now=applied + 2 * WINDOW + 1)
    assert late == ["C-1:unattended"] and _row(cockpit).status == "reverted"


async def test_an_extended_trial_that_saw_some_work_reverts_one_window_after_it_ended(
    cockpit: Cockpit,
) -> None:
    row, applied = await _trial(cockpit)
    assert await evaluate.tick(cockpit.app.state, cockpit.fid, now=row.trial_until + 1) == [
        "C-1:extended"
    ]
    _merged(cockpit, 9900, applied + WINDOW + H)  # some work, not enough to judge
    ended = _row(cockpit).trial_until
    assert await evaluate.tick(cockpit.app.state, cockpit.fid, now=ended + 1) == [
        "C-1:inconclusive"
    ]

    early = await evaluate.tick(cockpit.app.state, cockpit.fid, now=ended + WINDOW - 60)
    late = await evaluate.tick(cockpit.app.state, cockpit.fid, now=ended + WINDOW + 1)

    assert early == [] and late == ["C-1:unattended"]
    assert _row(cockpit).status == "reverted"


async def test_only_an_explicit_mention_by_an_incident_human_or_sentinel_counts_as_harm(
    cockpit: Cockpit,
) -> None:
    row, _applied = await _trial(cockpit)
    store = cockpit.store

    def finding(summary: str, source: str, **fields: Any) -> None:
        store.upsert_issue(
            cockpit.fid,
            class_="finding",
            rule="finding.general",
            target="factory",
            summary=summary,
            source=source,
            **fields,
        )

    finding("C-1 broke the review loop", "human")
    finding("since C-1 PLs stop early", "sentinel")
    finding("C-1 looks fine to me", "agent")  # a PL's own note
    finding("not related to C-10", "human")
    finding("the fix for this is a proposal", "human", proposal_id=row.id)

    harm = evaluate.attributed_incidents(cockpit.deps, row, int(time.time()) + 60)

    assert harm == ["F-2", "F-1"]


async def test_an_incident_naming_it_before_the_default_reverts_instead(
    cockpit: Cockpit,
) -> None:
    row = await _idle_until_decided(cockpit)
    await issues.raise_issue(
        cockpit.app.state,
        cockpit.fid,
        rule="agent.repeat",
        target="proposal:C-1",
        summary="agents loop on the C-1 rule",
    )

    done = await evaluate.tick(cockpit.app.state, cockpit.fid, now=row.result["default_at"] + 1)

    assert done == ["C-1:unattended"]
    row = _row(cockpit)
    assert row.status == "reverted" and row.result["revert_reason"].startswith("unattended: INC-")


async def test_an_idle_trial_with_a_regressed_guard_still_reverts_at_once(
    cockpit: Cockpit,
) -> None:
    row, _applied = await _trial(cockpit)
    for rule in ("member.dead", "agent.repeat", "agent.quiet"):
        await issues.raise_issue(
            cockpit.app.state, cockpit.fid, rule=rule, target="member:x", summary=rule
        )

    done = await evaluate.tick(cockpit.app.state, cockpit.fid, now=row.trial_until + 1)

    assert done == ["C-1:reverted"]
    row = _row(cockpit)
    assert row.status == "reverted" and "trial_extensions" not in row.result
    assert row.result["revert_reason"].startswith("auto-revert: guard regressed: incidents")


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
