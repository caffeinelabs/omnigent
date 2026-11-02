"""The implement loop's stall clock reads the task's people, not only its task events.

Reproduces two false "stalled - what next?" decisions from the live factory
(validate-build-tier, T-5): one raised while the task only waited on two
blocking questions its PL had asked a person, one raised while the IC was
busy running long builds without a factory task event. A dead IC still stalls
and recommends switching vendors; after "Keep waiting" the loop is not asked
about again within the new budget unless its IC dies.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities import Conversation
from omnigent.entities.conversation import NewConversationItem, parse_item_data
from omnigent.entities.factory import FactoryDecision, FactoryLoop
from omnigent.server.factory import budgets, decisions, loops, pipeline, task_pulse, tasks
from omnigent.server.routes._sessions.common import _session_status_cache
from omnigent.stores.conversation_store import sqlalchemy_store as conv_store_mod
from omnigent.stores.factory_store import sqlalchemy_store as factory_store_mod
from omnigent.util.session_lifecycle import CLOSED_LABEL_KEY, CLOSED_LABEL_VALUE
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FakeClock
from tests.server.factory.test_loops import (
    REPO,
    SLUG,
    implement,
    loop_decisions,
    plan,
    start,
    task,
)

KEY = "validate-p9774"
MIN = 60
HOUR = 3600


@dataclass
class Stall:
    """The cockpit on a fake clock, with T-1's IC bound and the members a tick reads."""

    cockpit: Cockpit
    clock: FakeClock
    ic: Conversation
    monkeypatch: pytest.MonkeyPatch

    @property
    def members(self) -> list[Conversation]:
        w = self.cockpit.world
        return [w.get("lead_a"), w.get("lead_b"), w.get("pl")]

    async def tick(self, seconds: float = 0) -> FactoryLoop:
        """Advance the clock, run one loop tick, and return T-1's implement loop."""
        now = self.clock.advance(seconds)
        await loops.tick(self.cockpit.app.state, self.members, [], now=now)
        return implement(self.cockpit, KEY)

    async def ticks(self, seconds: float, every: float = 5 * MIN) -> FactoryLoop:
        """Tick every *every* seconds for *seconds*."""
        row = implement(self.cockpit, KEY)
        done = 0.0
        while done < seconds:
            row = await self.tick(every)
            done += every
        return row

    def status(self, session_id: str, value: str) -> None:
        self.monkeypatch.setitem(_session_status_cache, session_id, value)

    def item(self, session_id: str, kind: str, data: dict[str, Any]) -> None:
        """Append one item to a session at the fake clock's time."""
        self.cockpit.world.store.append(
            session_id,
            [NewConversationItem(type=kind, response_id="r1", data=parse_item_data(kind, data))],
        )

    def tool_call(self, session_id: str, command: str, call_id: str) -> None:
        self.item(
            session_id,
            "function_call",
            {
                "agent": "ic",
                "name": "Bash",
                "arguments": json.dumps({"command": command}),
                "call_id": call_id,
            },
        )

    def ask(self, question: str, *, asker: str | None = None, blocking: bool = True) -> Any:
        """The PL (or *asker*) asks a person a question, as ``sys_factory_ask`` stores it."""
        w = self.cockpit.world
        row, _ = self.cockpit.store.create_decision(
            self.cockpit.fid,
            dedupe_key=question[:64],
            question=question,
            source="agent",
            options=[{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}],
            blocking=blocking,
            project_slug=SLUG,
            asked_by_session_id=asker or w.id("pl"),
        )
        return row

    def stall_decisions(self) -> list[FactoryDecision]:
        return [d for d in loop_decisions(self.cockpit) if KEY in d.question]


@pytest_asyncio.fixture()
async def stall(world: Any, db_uri: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Stall]:
    loops.reset()
    tasks.reset()
    budgets.reset()
    pipeline.reset()
    monkeypatch.setattr(pipeline, "auto_flush", False)
    clock = FakeClock()
    # Everything the stores stamp follows the same fake clock.
    monkeypatch.setattr(loops, "_clock", clock)
    monkeypatch.setattr(conv_store_mod, "now_epoch", lambda: int(clock()))
    monkeypatch.setattr(factory_store_mod, "now_s", lambda: int(clock()))
    remove = loops.install()
    try:
        async for cockpit in build_cockpit(world, db_uri, monkeypatch):
            cockpit.store.upsert_project(
                cockpit.fid,
                SLUG,
                title="API version",
                repo=REPO,
                base_branch="temp-main",
                charter={"goal": "GET /version", "budget_ics": 3},
                pl_session_id=world.id("pl"),
            )
            await plan(cockpit, KEY)
            await start(cockpit, KEY)
            ic = world.make_session(
                "ic_t1", agent="pl", title=KEY, parent="pl", sub_agent_name="ic"
            )
            cockpit.store.update_task(task(cockpit, KEY).id, session_ids=[ic.id])
            yield Stall(cockpit, clock, ic, monkeypatch)
    finally:
        remove()
        loops.reset()
        tasks.reset()
        pipeline.reset()


def _why(decision: FactoryDecision) -> str:
    """The plain line the stall's question ends with."""
    head, sep, line = decision.question.partition(" stalled - what next? ")
    assert sep and head.startswith(f"T-1 {KEY}: implement 1/3"), decision.question
    return line


def _recommended(decision: FactoryDecision) -> list[str]:
    return [o["label"] for o in decision.options if o.get("recommended")]


# ── Live case 1: the task only waits on a person ───────────────


async def test_a_task_waiting_on_its_pls_questions_is_not_stalled(stall: Stall) -> None:
    """D-24: an hour of nothing while T-5's PL waited on D-22 and D-23 is not a stall."""
    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    await stall.tick(10 * MIN)
    first = stall.ask("Which build tier should validate-p9774 target?")
    second = stall.ask("May I reset the preview database?")
    unrelated = stall.ask("FYI: the docs moved", blocking=False)
    row = await stall.ticks(3 * HOUR)

    assert row.state == "running"
    assert stall.stall_decisions() == []
    view = loops.loop_view(row)
    assert view["held_by"] == {
        "decision_id": first.id,
        "decision_ref": first.ref,
        "refs": [first.ref, second.ref],
        "since": first.created_at,
    }
    # The task's chip says what it waits on, too.
    chip = next(t for t in tasks.list_view(cockpit.deps, cockpit.fid, SLUG)["tasks"])["loop"]
    assert chip["held_by"]["decision_ref"] == first.ref
    assert unrelated.ref not in view["held_by"]["refs"]

    # Answered: the clock starts again from the answer, and the hold is not charged
    # to the wall budget (2 h here; the task has run over 3 h).
    cockpit.store.update_loop(row.id, spec={**row.spec, "wall_h": 2})
    for decision in (first, second):
        cockpit.store.answer_decision(decision.id, option_id="yes", text=None, by="carol")
    row = await stall.tick(MIN)
    assert row.state == "running" and loops.loop_view(row)["held_by"] is None
    # Held from the first tick that saw the questions (0:15) until the answers (3:11).
    assert row.spent["held_s"] == 2 * HOUR + 56 * MIN
    row = await stall.ticks(55 * MIN)
    assert row.state == "running" and stall.stall_decisions() == []

    # An hour after the answers, with the IC idle all along, it does stall.
    row = await stall.ticks(10 * MIN)
    assert row.state == "stalled" and row.last_reason.startswith("no progress for")
    (decision,) = stall.stall_decisions()
    assert _recommended(decision) == ["Keep waiting"]
    assert _why(decision).startswith("The IC is alive but has been idle for")


async def test_an_ics_own_question_or_one_about_the_task_holds_it(stall: Stall) -> None:
    w = stall.cockpit.world
    stall.status(stall.ic.id, "idle")
    asked = stall.ask("Is the staging cluster up?", asker=stall.ic.id)
    other = stall.ask("Unrelated: rename the repo?", asker=w.id("lead_a"))
    row = await stall.ticks(2 * HOUR)
    assert row.state == "running"
    assert loops.loop_view(row)["held_by"]["refs"] == [asked.ref]
    assert other.ref not in loops.loop_view(row)["held_by"]["refs"]

    stall.cockpit.store.answer_decision(asked.id, option_id="yes", text=None, by="carol")
    about, _ = stall.cockpit.store.create_decision(
        stall.cockpit.fid,
        dedupe_key="about-t1",
        question="Ship T-1 without the migration?",
        source="agent",
        blocking=True,
        project_slug=SLUG,
        asked_by_session_id=w.id("lead_a"),
        subject=f"task:{task(stall.cockpit, KEY).ref}",
    )
    row = await stall.ticks(2 * HOUR)
    assert row.state == "running" and loops.loop_view(row)["held_by"]["refs"] == [about.ref]


# ── Live case 2: the IC works without task events ──────────────


async def test_an_ic_running_long_builds_is_not_stalled(stall: Stall) -> None:
    """D-25: the IC drove long builds for hours, with no factory task event."""
    stall.status(stall.ic.id, "running")
    for n in range(36):  # three hours, a step every five minutes
        stall.tool_call(stall.ic.id, f"pnpm -F web build --run {n}", f"call-{n}")
        row = await stall.tick(5 * MIN)
        assert row.state == "running", n
    assert stall.stall_decisions() == []
    assert row.spent["active_at"] == int(stall.clock()) - 5 * MIN


async def test_one_long_silent_command_counts_until_the_stuck_time(stall: Stall) -> None:
    stall.status(stall.ic.id, "running")
    stall.tool_call(stall.ic.id, "pnpm -F web e2e", "call-1")
    row = await stall.ticks(80 * MIN)
    assert row.state == "running"
    # Silent past the factory's stuck time (90 min): it may be wedged.
    row = await stall.ticks(15 * MIN)
    assert row.state == "stalled"
    (decision,) = stall.stall_decisions()
    assert _recommended(decision) == ["Switch vendor → ic_codex"]
    assert _why(decision).startswith("The IC has been running for")
    assert _why(decision).endswith("without any output; it may be stuck.")


async def test_a_wall_stall_of_a_working_ic_recommends_keep_waiting(stall: Stall) -> None:
    cockpit = stall.cockpit
    row = implement(cockpit, KEY)
    cockpit.store.update_loop(row.id, spec={**row.spec, "wall_h": 1})
    stall.status(stall.ic.id, "running")
    for n in range(13):
        stall.tool_call(stall.ic.id, "pnpm -F web build", f"call-{n}")
        row = await stall.tick(5 * MIN)
    assert row.state == "stalled" and row.last_reason == "running 1 h (wall budget 1 h)"
    (decision,) = stall.stall_decisions()
    assert _recommended(decision) == ["Keep waiting"]
    assert _why(decision) == ("The IC worked 5 minutes ago; it is running pnpm -F web build.")
    wait = next(o for o in decision.options if o["id"] == "wait")
    assert wait["description"] == (
        "Up to 2 h in all; asks again sooner only if the IC stops or its run fails"
    )
    assert (decision.context or "").splitlines() == [
        "Why: running 1 h (wall budget 1 h)",
        "Last verdict: DISPATCHED",
    ]


async def test_the_pls_turns_about_the_task_count_its_other_turns_do_not(stall: Stall) -> None:
    w = stall.cockpit.world
    stall.status(stall.ic.id, "idle")
    await stall.tick(50 * MIN)
    # The PL sends the IC its next step: a tool call naming the task's worker.
    stall.item(
        w.id("pl"),
        "function_call",
        {
            "agent": "pl",
            "name": "sys_session_send",
            "arguments": json.dumps({"title": KEY, "message": "rebase on temp-main"}),
            "call_id": "pl-1",
        },
    )
    row = await stall.ticks(40 * MIN)
    assert row.state == "running"
    # Later turns about something else, and the server's own mail, do not count.
    stall.item(
        w.id("pl"),
        "message",
        {
            "role": "assistant",
            "agent": "pl",
            "content": [{"type": "output_text", "text": "Docs look fine."}],
        },
    )
    stall.item(
        w.id("pl"),
        "message",
        {"role": "user", "content": [{"type": "input_text", "text": f"[Factory] {KEY} idle"}]},
    )
    row = await stall.ticks(30 * MIN)
    assert row.state == "stalled"


# ── A dead IC still stalls, and says so ────────────────────────


async def test_a_dead_ic_stalls_and_recommends_switching_vendors(stall: Stall) -> None:
    stall.status(stall.ic.id, "failed")
    row = await stall.ticks(55 * MIN)
    assert row.state == "running"
    row = await stall.ticks(10 * MIN)
    assert row.state == "stalled"
    (decision,) = stall.stall_decisions()
    assert _recommended(decision) == ["Switch vendor → ic_codex"]
    assert _why(decision) == "The IC's last run failed 60 minutes ago."
    assert stall.cockpit.events("loop.stalled")[-1].data["recommend"] == "switch"


async def test_a_closed_ic_session_is_nobody_working(stall: Stall) -> None:
    stall.cockpit.world.store.set_labels(stall.ic.id, {CLOSED_LABEL_KEY: CLOSED_LABEL_VALUE})
    row = await stall.ticks(65 * MIN)
    assert row.state == "stalled"
    (decision,) = stall.stall_decisions()
    assert _recommended(decision) == ["Switch vendor → ic_codex"]
    assert "nobody is working on the task" in _why(decision)


async def test_a_stalled_task_whose_ic_works_again_runs_again(stall: Stall) -> None:
    stall.status(stall.ic.id, "idle")
    row = await stall.ticks(65 * MIN)
    assert row.state == "stalled"
    (decision,) = stall.stall_decisions()
    stall.status(stall.ic.id, "running")
    stall.tool_call(stall.ic.id, "pnpm test", "call-1")
    row = await stall.tick(MIN)
    assert row.state == "running"
    assert stall.cockpit.store.get_decision(decision.id).status == "expired"


# ── After "Keep waiting" ───────────────────────────────────────


async def test_keep_waiting_is_not_asked_again_unless_the_ic_dies(stall: Stall) -> None:
    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    row = await stall.ticks(65 * MIN)
    assert row.state == "stalled"
    (first,) = stall.stall_decisions()
    await decisions.answer_decision(cockpit.app.state, first.id, user_id="carol", option_id="wait")
    row = implement(cockpit, KEY)
    assert row.state == "running" and row.spec["wall_h"] == 16
    # Hours of nothing within the new budget: no new question.
    row = await stall.ticks(6 * HOUR, every=10 * MIN)
    assert row.state == "running"
    assert [d.id for d in stall.stall_decisions()] == [first.id]
    # The IC dies: that is worse, so the no-progress rule asks again.
    stall.status(stall.ic.id, "failed")
    row = await stall.tick(MIN)
    assert row.state == "stalled"
    second = next(d for d in stall.stall_decisions() if d.id != first.id)
    assert _recommended(second) == ["Switch vendor → ic_codex"]


async def test_keep_waiting_then_a_failed_try_asks_again(stall: Stall) -> None:
    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    await stall.ticks(65 * MIN)
    (first,) = stall.stall_decisions()
    await decisions.answer_decision(cockpit.app.state, first.id, user_id="carol", option_id="wait")
    await stall.ticks(2 * HOUR, every=10 * MIN)
    assert implement(cockpit, KEY).state == "running"
    # The IC reports BLOCKED: its try closed, nobody is working on it now.
    from tests.server.factory.test_loops import emit

    await emit(
        cockpit,
        "task.state",
        task_id=task(cockpit, KEY).id,
        result="BLOCKED",
        note="needs a person",
        **{"from": "running", "to": "running"},
    )
    row = await stall.ticks(65 * MIN)
    assert row.state == "stalled"


async def test_a_wall_stall_after_keep_waiting_with_nothing_done_recommends_switching(
    stall: Stall,
) -> None:
    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    await stall.ticks(65 * MIN)
    (first,) = stall.stall_decisions()
    await decisions.answer_decision(cockpit.app.state, first.id, user_id="carol", option_id="wait")
    row = await stall.ticks(15 * HOUR, every=30 * MIN)
    assert row.state == "stalled" and row.last_reason.startswith("running 16 h")
    second = next(d for d in stall.stall_decisions() if d.id != first.id)
    assert _recommended(second) == ["Switch vendor → ic_codex"]
    assert _why(second).startswith("The IC produced nothing in the")


# ── The words ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pulse", "look", "kept_at", "recommend", "line"),
    [
        (
            task_pulse.Pulse(
                active_at=1000 - 180,
                active_by="IC",
                working=True,
                worker="alive",
                worker_status="running",
                worker_at=1000 - 180,
            ),
            task_pulse.WorkerLook(tool="pnpm -F web build"),
            None,
            "wait",
            "The IC worked 3 minutes ago; it is running pnpm -F web build.",
        ),
        (
            task_pulse.Pulse(
                active_at=1000 - 120, active_by="reviewer", worker="alive", worker_at=100
            ),
            None,
            None,
            "wait",
            "The reviewer worked 2 minutes ago; it is still on it.",
        ),
        (
            task_pulse.Pulse(worker="alive", worker_status="idle", worker_at=1000 - 4320),
            None,
            None,
            "wait",
            "The IC is alive but has been idle for 72 minutes; its lead may owe it a next step.",
        ),
        (
            task_pulse.Pulse(
                held=task_pulse.Hold("d", "D-22", ("D-22",), 0), worker="alive", worker_at=0
            ),
            None,
            None,
            "wait",
            "It waits on D-22 for a person's answer.",
        ),
        (
            task_pulse.Pulse(worker="alive", worker_status="idle", worker_at=1000 - 60),
            task_pulse.WorkerLook(error="rate limited by the provider"),
            None,
            "switch",
            "The IC's last run failed 1 minute ago: rate limited by the provider.",
        ),
        (
            task_pulse.Pulse(worker="gone", worker_at=1000 - 7200),
            None,
            None,
            "switch",
            "The IC's session was closed 2 hours ago; nobody is working on the task.",
        ),
        (
            task_pulse.Pulse(),
            None,
            None,
            "switch",
            "No IC is working on the task.",
        ),
        (
            task_pulse.Pulse(active_at=200, worker="alive", worker_status="idle", worker_at=200),
            None,
            400,
            "switch",
            "The IC produced nothing in the 10 minutes since the last Keep waiting.",
        ),
    ],
)
def test_advice_says_why_in_one_plain_line(
    pulse: task_pulse.Pulse,
    look: task_pulse.WorkerLook | None,
    kept_at: int | None,
    recommend: str,
    line: str,
) -> None:
    advice = task_pulse.advise(pulse, now=1000, look=look, kept_at=kept_at)
    assert (advice.recommend, advice.line) == (recommend, line)


def test_the_task_is_named_by_its_ref_branch_or_key() -> None:
    from omnigent.entities.factory import FactoryTask

    row = FactoryTask(
        id="t" * 32,
        factory_id="f" * 32,
        project_slug=SLUG,
        number=5,
        key="docs",
        title="Docs",
    )
    prose, args = task_pulse.task_patterns(row)
    assert prose.search("T-5 is waiting on review") and not prose.search("T-50 is done")
    assert prose.search("pushed factory/api-version/docs") and not prose.search("the docs")
    assert args.search('{"title": "docs-r1"}') and not args.search('{"command": "ls docs/"}')
