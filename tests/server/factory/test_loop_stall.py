"""The implement loop's stall clock reads the task's people, not only its task events.

Reproduces two false "stalled - what next?" decisions from the live factory
(validate-build-tier, T-5): one raised while the task only waited on two
blocking questions its PL had asked a person, one raised while the IC was
busy running long builds without a factory task event. A dead IC still stalls
and recommends switching vendors (closing it, or the PL's chatter about it,
is not progress); after "Keep waiting" the loop is not asked about again
within the new budget unless its IC dies; a stall does not reopen on a ping
and a one-line reply.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities import Conversation
from omnigent.entities.conversation import NewConversationItem, parse_item_data
from omnigent.entities.factory import FactoryDecision, FactoryLoop
from omnigent.server.factory import (
    budgets,
    decisions,
    loop_escalation,
    loops,
    pipeline,
    task_pulse,
    tasks,
)
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

    def close(self, session_id: str) -> None:
        """Close a session as ``sys_session_close`` does: a PATCH that bumps its row."""
        self.cockpit.world.store.update_conversation(
            session_id, title=f"ic:{KEY}:closed:{session_id}", archived=True
        )

    def say(self, session_id: str, role: str, text: str) -> None:
        """A message in (``user``: the PL or the server) or out (``assistant``)."""
        kind = "output_text" if role == "assistant" else "input_text"
        data: dict[str, Any] = {"role": role, "content": [{"type": kind, "text": text}]}
        if role == "assistant":
            data["agent"] = "pl" if session_id == self.cockpit.world.id("pl") else "ic"
        self.item(session_id, "message", data)

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
                base_branch="develop",
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
        "count": 2,
        "kind": "decision",
        "label": (
            f"waiting on {first.ref} (Which build tier should validate-p9774 target?, "
            "needs an operator)"
        ),
        "who": "an operator",
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
    assert _recommended(decision) == ["Nudge the lead"]
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


async def test_a_lead_question_about_another_task_does_not_hold_this_one(stall: Stall) -> None:
    """D-1 is about T-9's docs: T-1, with an idle IC, still stalls and says nothing of D-1."""
    stall.status(stall.ic.id, "idle")
    stall.ask("Should T-9 docs use the new logo?")
    w = stall.cockpit.world
    stall.cockpit.store.create_decision(
        stall.cockpit.fid,
        dedupe_key="about-t9",
        question="Ship it without the migration?",
        source="agent",
        blocking=True,
        project_slug=SLUG,
        asked_by_session_id=w.id("pl"),
        subject="task:T-9",
    )
    row = await stall.ticks(30 * MIN)
    assert row.state == "running" and loops.loop_view(row)["held_by"] is None
    row = await stall.ticks(35 * MIN)
    assert row.state == "stalled"


async def test_a_hold_does_not_hide_a_dead_ic_from_the_wall_budget(stall: Stall) -> None:
    """With nobody working on the task, a question the PL waits on is shown but time is charged."""
    cockpit = stall.cockpit
    row = implement(cockpit, KEY)
    cockpit.store.update_loop(row.id, spec={**row.spec, "wall_h": 2})
    stall.close(stall.ic.id)
    asked = stall.ask("May I reset the preview database?")
    row = await stall.ticks(110 * MIN, every=10 * MIN)
    assert row.state == "running"
    assert loops.loop_view(row)["held_by"]["refs"] == [asked.ref]
    assert not row.spent.get("held_s")
    row = await stall.ticks(10 * MIN, every=10 * MIN)
    assert row.state == "stalled" and row.last_reason == "running 2 h (wall budget 2 h)"


async def test_the_hold_counts_every_open_question(stall: Stall) -> None:
    stall.status(stall.ic.id, "idle")
    asked = [stall.ask(f"Question {n} about the preview?") for n in range(7)]
    row = await stall.tick(MIN)
    held = loops.loop_view(row)["held_by"]
    assert held["count"] == 7
    assert held["refs"] == [d.ref for d in asked[: task_pulse.HOLD_REFS_MAX]]


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
    pl, fid = cockpit.world.id("pl"), cockpit.fid
    assert (decision.context or "").splitlines() == [
        "Why: running 1 h (wall budget 1 h)",
        "Last verdict: DISPATCHED",
        "Last doing: it is running pnpm -F web build",
        "Waiting on: nothing the server knows of (no open decision, proposal, dependency, "
        "merge or hold)",
        f"Look: [IC session](/c/{stall.ic.id}) · "
        f"[IC in the agent panel](/factory/{fid}/agents/{stall.ic.id}) · [its lead](/c/{pl})",
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
            "arguments": json.dumps({"title": KEY, "message": "rebase on develop"}),
            "call_id": "pl-1",
        },
    )
    row = await stall.ticks(40 * MIN)
    assert row.state == "running"
    # Later replies, even one naming the task, and the server's own mail do not count.
    stall.say(w.id("pl"), "assistant", "Docs look fine.")
    stall.say(w.id("pl"), "assistant", "Heartbeat: T-1 is still on its first try.")
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
    """Closing the IC bumps its row; the clock still runs from its last real activity."""
    stall.status(stall.ic.id, "idle")
    row = await stall.ticks(50 * MIN)
    assert row.state == "running"
    stall.close(stall.ic.id)
    row = await stall.ticks(10 * MIN)
    assert row.state == "stalled" and row.last_reason == "no progress for 60 min"
    (decision,) = stall.stall_decisions()
    assert _recommended(decision) == ["Switch vendor → ic_codex"]
    assert _why(decision) == (
        "The IC's session was closed 10 minutes ago; nobody is working on the task."
    )
    wait = next(o for o in decision.options if o["id"] == "wait")
    assert wait["description"] == (
        "Nobody works on it now: asks again in 60 minutes unless an IC starts"
    )


async def test_closing_the_ic_of_a_stalled_task_keeps_it_stalled(stall: Stall) -> None:
    stall.status(stall.ic.id, "idle")
    row = await stall.ticks(65 * MIN)
    assert row.state == "stalled"
    (decision,) = stall.stall_decisions()
    stall.close(stall.ic.id)
    row = await stall.ticks(30 * MIN)
    assert row.state == "stalled"
    assert stall.cockpit.store.get_decision(decision.id).status == "open"


async def test_the_pls_chatter_does_not_keep_a_dead_task_running(stall: Stall) -> None:
    """With its IC closed, the PL's replies and tool calls naming the task are not progress."""
    w = stall.cockpit.world
    stall.cockpit.world.store.set_labels(stall.ic.id, {CLOSED_LABEL_KEY: CLOSED_LABEL_VALUE})
    for n in range(5):
        row = await stall.tick(10 * MIN)
        assert row.state == "running", n
        stall.say(w.id("pl"), "assistant", "Heartbeat: T-1 still has no IC; nothing to do.")
        stall.item(
            w.id("pl"),
            "function_call",
            {
                "agent": "pl",
                "name": "sys_factory_task",
                "arguments": json.dumps({"op": "get", "key": KEY}),
                "call_id": f"pl-{n}",
            },
        )
    row = await stall.tick(10 * MIN)
    assert row.state == "stalled" and row.last_reason == "no progress for 60 min"
    (decision,) = stall.stall_decisions()
    assert _recommended(decision) == ["Switch vendor → ic_codex"]


async def test_keep_waiting_on_a_dead_ic_asks_again_in_an_hour(stall: Stall) -> None:
    cockpit = stall.cockpit
    stall.close(stall.ic.id)
    await stall.ticks(60 * MIN)
    (first,) = stall.stall_decisions()
    await decisions.answer_decision(cockpit.app.state, first.id, user_id="carol", option_id="wait")
    row = await stall.ticks(55 * MIN)
    assert row.state == "running"
    row = await stall.ticks(5 * MIN)
    assert row.state == "stalled"
    second = next(d for d in stall.stall_decisions() if d.id != first.id)
    assert _recommended(second) == ["Switch vendor → ic_codex"]


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


async def test_a_ping_and_a_one_line_reply_do_not_reopen_a_stall(stall: Stall) -> None:
    """The STALLED mail makes the PL ask the IC for status; the idle IC answers in a line."""
    w = stall.cockpit.world
    stall.status(stall.ic.id, "idle")
    row = await stall.ticks(65 * MIN)
    assert row.state == "stalled"
    (decision,) = stall.stall_decisions()
    stall.say(stall.ic.id, "user", "status?")
    stall.say(stall.ic.id, "assistant", "Waiting for the next step.")
    stall.say(w.id("pl"), "assistant", "Asked T-1's IC for status.")
    row = await stall.ticks(65 * MIN)
    assert row.state == "stalled"
    assert [d.id for d in stall.stall_decisions()] == [decision.id]
    assert stall.cockpit.store.get_decision(decision.id).status == "open"


async def test_a_stall_that_clears_itself_waits_twice_as_long_next_time(stall: Stall) -> None:
    stall.status(stall.ic.id, "idle")
    await stall.ticks(65 * MIN)
    (first,) = stall.stall_decisions()
    # The IC runs one short turn: real work, the stall clears.
    stall.status(stall.ic.id, "running")
    stall.say(stall.ic.id, "assistant", "Checking the build.")
    row = await stall.tick(MIN)
    assert row.state == "running" and row.spent["idle_clears"] == 1
    assert stall.cockpit.store.get_decision(first.id).status == "expired"
    stall.status(stall.ic.id, "idle")
    row = await stall.ticks(65 * MIN)
    assert row.state == "running"
    row = await stall.ticks(60 * MIN)
    assert row.state == "stalled" and row.last_reason.startswith("no progress for 12")
    second = next(d for d in stall.stall_decisions() if d.id != first.id)
    # A new try gets the full budget again.
    await decisions.answer_decision(
        stall.cockpit.app.state, second.id, user_id="carol", option_id="switch"
    )
    await start(stall.cockpit, KEY)
    row = implement(stall.cockpit, KEY)
    assert row.iteration == 2 and "idle_clears" not in row.spent
    assert loops.idle_budget_s(row) == HOUR


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


async def test_an_answer_and_the_tick_do_not_overwrite_each_other(stall: Stall) -> None:
    """The answer writes the loop under the engine's lock, from a fresh read."""
    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    await stall.ticks(65 * MIN)
    (first,) = stall.stall_decisions()
    seen = implement(cockpit, KEY)  # what a tick read before the answer
    async with loops.lock(cockpit.fid):
        answering = asyncio.create_task(
            decisions.answer_decision(
                cockpit.app.state, first.id, user_id="carol", option_id="wait"
            )
        )
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert not answering.done()
        # The tick writes what it read, as wake() would.
        cockpit.store.update_loop(seen.id, spent={**seen.spent, "idle_clears": 1})
    await asyncio.wait_for(answering, timeout=10)
    row = implement(cockpit, KEY)
    assert row.state == "running"
    assert row.spent["kept_waiting"]["iteration"] == row.iteration
    assert row.spent["idle_clears"] == 1


async def test_a_stall_asked_about_again_later_says_why_as_of_then(stall: Stall) -> None:
    cockpit = stall.cockpit
    row = implement(cockpit, KEY)
    cockpit.store.update_loop(row.id, spec={**row.spec, "wall_h": 1})
    stall.status(stall.ic.id, "running")
    for n in range(13):
        stall.tool_call(stall.ic.id, "pnpm -F web build", f"call-{n}")
        row = await stall.tick(5 * MIN)
    assert row.state == "stalled"
    assert row.spent["stall"]["line"].startswith("The IC worked 5 minutes ago")
    # Hours later (the project was paused from the decision), the IC has gone quiet.
    stall.status(stall.ic.id, "idle")
    at = row.spent["stall"]["at"]
    assert loops.reword_stall(cockpit.deps, row, at + MIN) == row  # fresh: as it stalled
    row = loops.reword_stall(cockpit.deps, row, stall.clock.advance(3 * HOUR))
    assert row.spent["stall"]["line"] == (
        "The IC is alive but has been idle for 3 hours; its lead may owe it a next step."
    )
    facts = loop_escalation.read_facts(cockpit.deps, row)
    question, *_ = loop_escalation.decision_for(facts, "stalled")
    assert question.endswith("its lead may owe it a next step.")


async def test_a_wall_stall_is_not_read_each_tick(
    stall: Stall, monkeypatch: pytest.MonkeyPatch
) -> None:
    cockpit = stall.cockpit
    row = implement(cockpit, KEY)
    cockpit.store.update_loop(row.id, spec={**row.spec, "wall_h": 1})
    stall.status(stall.ic.id, "idle")
    row = await stall.ticks(60 * MIN, every=30 * MIN)
    assert row.state == "stalled" and row.spent["stall"]["kind"] == "wall"
    reads: list[str] = []
    real = loops._watch

    def counted(reader: task_pulse.Reader, task: Any) -> Any:
        reads.append(task.key)
        return real(reader, task)

    monkeypatch.setattr(loops, "_watch", counted)
    await stall.ticks(30 * MIN)
    assert reads == []


async def test_the_ics_last_error_is_quoted_without_secrets(stall: Stall) -> None:
    stall.status(stall.ic.id, "idle")
    stall.item(
        stall.ic.id,
        "error",
        {
            "source": "tool",
            "code": "push_failed",
            "message": "git push https://x-access-token:ghs_secretsecret@github.com/o/r failed",
        },
    )
    row = await stall.ticks(65 * MIN)
    assert row.state == "stalled"
    (decision,) = stall.stall_decisions()
    assert "ghs_secretsecret" not in decision.question
    assert _why(decision).startswith("The IC's last run failed 60 minutes ago: git push https://")
    assert "[REDACTED]" in decision.question
    assert _recommended(decision) == ["Switch vendor → ic_codex"]


@pytest.mark.parametrize(
    ("name", "arg", "words"),
    [
        ("Bash", "pnpm -F web build", "running pnpm -F web build"),
        ("bash", None, "running a command"),
        ("mcp__preview__exec", "pnpm e2e", "running pnpm e2e"),
        ("Read", "src/app.ts", "reading files"),
        ("apply_patch", "src/app.ts", "editing files"),
        ("mcp__linear__get_issue", "ENG-1", None),
    ],
)
def test_a_running_call_reads_in_plain_words(
    name: str, arg: str | None, words: str | None
) -> None:
    assert task_pulse.doing_text(name, arg) == words


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
            task_pulse.WorkerLook(tool="running pnpm -F web build"),
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
            "nudge",
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


# ── Holds, honest alerts and stale decisions (the live tryout) ─


def _proposal(stall: Stall, *, slug: str | None = None, owners: bool = True) -> tuple[Any, Any]:
    """A pending projects proposal ``C-n`` and the decision a person approves it on."""
    cockpit = stall.cockpit
    row, _ = cockpit.store.create_proposal(
        cockpit.fid,
        kind="projects",
        title="Narrow the scope of the API",
        author_kind="agent",
        author=cockpit.world.id("lead_a"),
        ops=[{"op": "update", "slug": slug or "other-project", "patch": {"goal": "narrower"}}],
        approval="owner" if owners else "operator",
    )
    decision, _ = cockpit.store.create_decision(
        cockpit.fid,
        dedupe_key=f"proposal:{row.id}"[:64],
        question=f"Atlas proposes C-{row.number}: {row.title}",
        source="proposal",
        options=[{"id": "apply", "label": "Apply", "action": "apply_proposal"}],
        blocking=False,
        project_slug=slug,
        asked_by_session_id=cockpit.world.id("lead_a"),
        subject=f"proposal:C-{row.number}",
        assignee="owners" if owners else "operators",
    )
    return row, decision


def _pl_actor(stall: Stall) -> Any:
    from omnigent.server.factory.actor import Actor

    return Actor.agent(stall.cockpit.world.id("pl"), "project_lead", title="PL · api-version")


async def _project_view(stall: Stall) -> dict[str, Any]:
    from omnigent.server.factory import snapshot

    raw = await snapshot.build_snapshot(stall.cockpit.app.state, stall.cockpit.world.get("lead_a"))
    return next(p for p in raw.body["projects"] if p["slug"] == SLUG)


async def test_a_pl_holding_its_task_for_an_open_proposal_is_not_asked_about(
    stall: Stall,
) -> None:
    """The live T-16: finished locally, held until C-16 applies (D-45): no D-48, no INC-47."""
    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    proposal, decision = _proposal(stall)  # about another project: only the hold links it
    out = await tasks.hold_task(
        cockpit.app.state,
        cockpit.fid,
        task(cockpit, KEY),
        tasks.HoldIn(on=f"C-{proposal.number}", note="Atlas: wait until the scope applies"),
        _pl_actor(stall),
    )
    words = (
        f"T-1 held: waiting on {decision.ref} (Approve C-{proposal.number}, needs an owner)"
        " · Atlas: wait until the scope applies"
    )
    assert out["hold"]["active"] is True and out["hold"]["label"] == words
    assert out["task"]["hold"]["on"] == f"C-{proposal.number}"
    [held] = cockpit.events("task.held")
    assert held.data["by"] == cockpit.world.id("pl") and held.data["by_kind"] == "agent"
    row = await stall.ticks(3 * HOUR)
    assert row.state == "running"
    assert stall.stall_decisions() == []
    assert cockpit.events("loop.stalled") == []
    assert loops.held_by(row) is not None
    assert loops.held_by(row)["label"] == words
    assert loops.held_by(row)["kind"] == "hold"
    view = await _project_view(stall)
    assert view["waiting_on"][0]["label"] == words
    assert view["waiting_on"][0]["who"] == "an owner"
    # C-16 applies: the hold lapses by itself and the clock runs again.
    cockpit.store.update_proposal(proposal.id, status="applied")
    cockpit.store.set_decision_status(decision.id, "expired")
    row = await stall.ticks(70 * MIN)
    assert row.state == "stalled"
    (asked,) = stall.stall_decisions()
    assert _recommended(asked) == ["Nudge the lead"]


async def test_a_stall_then_held_on_purpose_is_closed_with_its_cause_and_the_real_ask(
    stall: Stall,
) -> None:
    """D-48: the alarm fired, then the lead held T-1 for C-n. The server answers it."""
    import time

    from omnigent.server.factory import alarms

    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    row = await stall.ticks(65 * MIN)
    (asked,) = stall.stall_decisions()
    proposal, decision = _proposal(stall)
    await tasks.hold_task(
        cockpit.app.state,
        cockpit.fid,
        task(cockpit, KEY),
        tasks.HoldIn(on=f"C-{proposal.number}"),
        _pl_actor(stall),
    )
    row = await stall.tick(MIN)

    assert row.state == "running"
    closed = cockpit.store.get_decision(asked.id)
    assert closed is not None and (closed.status, closed.answered_by) == ("answered", "server")
    cause = f"T-1 is held on purpose for C-{proposal.number}; a held task is not stalled"
    verb = f"Apply C-{proposal.number}"
    assert (
        closed.answer_text
        == f"Closed by the server: {cause}. The real ask is {decision.ref} ({verb})."
    )
    [event] = [
        e for e in cockpit.events("decision.answered") if e.data.get("decision_id") == asked.id
    ]
    assert (event.data["cause"], event.data["real_ask"], event.data["about"]) == (
        cause,
        decision.ref,
        "T-1",
    )
    assert event.data["real_ask_verb"] == verb
    assert event.data["was"] == asked.question
    # Health reads it back; the lead hears nothing (the hold stands).
    found = alarms.load(cockpit.store, cockpit.fid, time.time())
    assert [(a.ref, a.cause, a.real_ask, a.about) for a in found] == [
        (asked.ref, cause, decision.ref, "T-1")
    ]
    assert not [t for t in cockpit.mailer.texts_to(cockpit.world.id("pl")) if "RESUMED" in t]


async def test_a_stall_closed_on_its_merge_records_its_cause_for_health(stall: Stall) -> None:
    """D-46 / D-47: closed when the work merged, read back with the node it was about."""
    import time

    from omnigent.server.factory import alarms
    from tests.server.factory.test_loops import make_pr

    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    await stall.ticks(65 * MIN)
    (asked,) = stall.stall_decisions()
    pr = make_pr(cockpit, KEY)
    cockpit.store.update_pr(pr.id, state="merged", merged_at=int(stall.clock()))
    await stall.tick(MIN)

    [found] = alarms.load(cockpit.store, cockpit.fid, time.time())
    assert (found.ref, found.cause, found.real_ask, found.about) == (
        asked.ref,
        f"PR #{pr.number} merged",
        None,
        "T-1",
    )
    assert found.was == asked.question


async def test_an_open_proposal_about_the_project_holds_its_tasks_without_a_hold(
    stall: Stall,
) -> None:
    """Before a host bump the PL cannot hold yet: the server infers the hold."""
    stall.status(stall.ic.id, "idle")
    proposal, decision = _proposal(stall, slug=SLUG, owners=False)
    row = await stall.ticks(2 * HOUR)
    assert (row.state, stall.stall_decisions()) == ("running", [])
    assert loops.held_by(row)["label"] == (
        f"waiting on {decision.ref} (Approve C-{proposal.number}, needs an operator)"
    )


async def test_the_leads_update_naming_what_the_task_waits_on_holds_it(stall: Stall) -> None:
    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    asked = stall.ask("Which tier should it target?", asker=cockpit.world.id("lead_a"))
    row = cockpit.store.get_project(cockpit.fid, SLUG)
    assert row is not None
    cockpit.store.update_project(
        row.id,
        lead_update={"health": "at_risk", "blocker": f"T-1 waits on {asked.ref}", "at": 1},
    )
    loop = await stall.ticks(2 * HOUR)
    assert (loop.state, stall.stall_decisions()) == ("running", [])
    assert loops.held_by(loop)["decision_ref"] == asked.ref


async def test_an_idle_ic_with_nothing_to_wait_on_recommends_nudging_its_lead(
    stall: Stall,
) -> None:
    """D-48's honest version: what the IC last did, that nothing holds it, where to look."""
    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    stall.tool_call(stall.ic.id, "pnpm -F web test", "call-1")
    stall.item(
        stall.ic.id,
        "function_call_output",
        {"call_id": "call-1", "output": "Tests: 12 passed"},
    )
    stall.say(stall.ic.id, "assistant", "Done: the route and its tests pass. Waiting for review.")
    row = await stall.ticks(65 * MIN)
    assert row.state == "stalled"
    (asked,) = stall.stall_decisions()
    assert _why(asked) == (
        "The IC is alive but has been idle for 60 minutes; its lead may owe it a next step."
    )
    assert [o["label"] for o in asked.options][:2] == ["Nudge the lead", "Keep waiting"]
    assert _recommended(asked) == ["Nudge the lead"]
    context = asked.context or ""
    assert "Last doing: its last call was running pnpm -F web test" in context
    assert 'its last reply: "Done: the route and its tests pass. Waiting for review."' in context
    assert "Waiting on: nothing the server knows of" in context
    assert "Idle for: 60 minutes (the no-progress budget is 60 minutes)" in context
    assert f"[IC session](/c/{stall.ic.id})" in context
    assert f"/factory/{cockpit.fid}/agents/{stall.ic.id}" in context
    assert f"[its lead](/c/{cockpit.world.id('pl')})" in context
    # Its incident says the same, not just "stalled".
    from omnigent.server.factory import detectors

    [event] = cockpit.events("loop.stalled")
    await detectors.on_event(cockpit.app.state, event)
    [incident] = [
        i for i in cockpit.store.list_issues(cockpit.fid, limit=50) if i.rule == "loop.stalled"
    ]
    assert (incident.body or "").splitlines() == [
        "The IC is alive but has been idle for 60 minutes; its lead may owe it a next step.",
        *context.splitlines()[2:],
    ]
    # Nudge the lead: the PL hears what is owed, and the loop keeps waiting.
    await decisions.answer_decision(
        cockpit.app.state, asked.id, user_id="alice", option_id="nudge"
    )
    assert implement(cockpit, KEY).state == "running"
    text = cockpit.mailer.texts_to(cockpit.world.id("pl"))[-1]
    assert text.startswith("[Factory loop · NUDGE · T-1 · by ")
    assert f'sys_factory_task(op="hold", key="{KEY}"' in text


async def test_a_stall_decision_closes_itself_once_the_tasks_pr_merged(stall: Stall) -> None:
    """D-47: the PR merged and the project shipped, yet "what next?" stayed open."""
    from tests.server.factory.test_loops import make_pr

    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    row = await stall.ticks(65 * MIN)
    (asked,) = stall.stall_decisions()
    pr = make_pr(cockpit, KEY)
    cockpit.store.update_pr(pr.id, state="merged", merged_at=int(stall.clock()))
    row = await stall.tick(MIN)
    assert (row.state, row.last_reason) == ("converged", f"PR #{pr.number} merged")
    closed = cockpit.store.get_decision(asked.id)
    assert closed is not None and closed.status == "answered"
    assert closed.answered_by == "server"
    assert closed.answer_text == f"Closed by the server: PR #{pr.number} merged"
    [event] = [e for e in cockpit.events("decision.answered") if e.data.get("auto")]
    assert event.data["reason"] == f"PR #{pr.number} merged"
    # The next ticks have nothing left to ask.
    await stall.ticks(2 * HOUR)
    assert [d.status for d in stall.stall_decisions()] == ["answered"]


async def test_a_stall_decision_left_open_after_its_loop_ended_closes_on_the_tick(
    stall: Stall,
) -> None:
    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    row = await stall.ticks(65 * MIN)
    (asked,) = stall.stall_decisions()
    # The loop ended on a path that did not settle its decision.
    cockpit.store.update_loop(row.id, state="converged", last_reason="T-1 is done")
    await stall.tick(MIN)
    closed = cockpit.store.get_decision(asked.id)
    assert closed is not None and (closed.status, closed.answered_by) == ("answered", "server")
    assert closed.answer_text == "Closed by the server: T-1 is done"


async def test_a_task_held_on_purpose_needs_no_ic_to_stay_held(stall: Stall) -> None:
    """Finished locally and held: its lead closed the IC; the wall budget waits too."""
    cockpit = stall.cockpit
    row = implement(cockpit, KEY)
    cockpit.store.update_loop(row.id, spec={**row.spec, "wall_h": 2})
    stall.close(stall.ic.id)
    proposal, _decision = _proposal(stall)
    await tasks.hold_task(
        cockpit.app.state,
        cockpit.fid,
        task(cockpit, KEY),
        tasks.HoldIn(on=f"C-{proposal.number}"),
        _pl_actor(stall),
    )
    row = await stall.ticks(3 * HOUR, every=10 * MIN)
    assert (row.state, stall.stall_decisions()) == ("running", [])
    assert row.spent.get("held_s")


async def test_a_budget_decision_is_not_a_stale_loop_decision(stall: Stall) -> None:
    """A project's $ cap asks with the loop source but acts on the project: it stays."""
    cockpit = stall.cockpit
    budget, _ = cockpit.store.create_decision(
        cockpit.fid,
        dedupe_key="budget:project:api-version:day",
        question="api-version spent $160 of $150. Pause or raise?",
        source="loop",
        options=[
            {"id": "pause", "label": "Pause", "action": "pause", "target": f"project:{SLUG}"},
            {
                "id": "raise",
                "label": "Raise",
                "action": "raise_budget",
                "target": f"budget:project:{SLUG}",
            },
        ],
        blocking=True,
        project_slug=SLUG,
    )
    assert loop_escalation.stale_loop_decisions(cockpit.deps, cockpit.fid) == []
    await stall.tick(MIN)
    current = cockpit.store.get_decision(budget.id)
    assert current is not None and current.status == "open"


# ── Holds that must not hide a real stall ──────────────────────


async def test_a_blocker_naming_its_own_pr_in_the_works_does_not_hide_a_dead_ic(
    stall: Stall,
) -> None:
    """ "CI is red on PR #9631": nobody else owes it anything, so a closed IC still stalls."""
    from tests.server.factory.test_loops import make_pr

    cockpit = stall.cockpit
    pr = make_pr(cockpit, KEY)  # open, not ready
    stall.close(stall.ic.id)
    row = cockpit.store.get_project(cockpit.fid, SLUG)
    assert row is not None
    cockpit.store.update_project(
        row.id,
        lead_update={"health": "at_risk", "blocker": f"CI is red on PR #{pr.number}", "at": 1},
    )
    loop = await stall.ticks(70 * MIN, every=10 * MIN)
    assert (loop.state, loop.last_reason) == ("stalled", "no progress for 60 min")
    assert loops.held_by(loop) is None


async def test_a_hold_on_a_task_nobody_starts_does_not_hide_a_dead_ic(stall: Stall) -> None:
    """Nobody must act on T-2: the wall budget still runs with the IC closed."""
    cockpit = stall.cockpit
    await plan(cockpit, "other-step")
    stall.close(stall.ic.id)
    out = await tasks.hold_task(
        cockpit.app.state,
        cockpit.fid,
        task(cockpit, KEY),
        tasks.HoldIn(on="T-2"),
        _pl_actor(stall),
    )
    assert out["hold"]["active"] is True and out["hold"]["kind"] == "task"
    loop = await stall.ticks(9 * HOUR, every=30 * MIN)
    assert loop.state == "stalled" and loop.last_reason.startswith("running 8 h")
    (asked,) = stall.stall_decisions()
    waiting = [line for line in (asked.context or "").splitlines() if line.startswith("Waiting")]
    assert waiting == ["Waiting on: T-1 held: waiting on T-2 (Task other-step, ready)"]


async def test_a_hold_lapses_at_its_limit_and_its_lead_is_told(stall: Stall) -> None:
    cockpit = stall.cockpit
    await plan(cockpit, "other-step")
    stall.status(stall.ic.id, "idle")
    await tasks.hold_task(
        cockpit.app.state,
        cockpit.fid,
        task(cockpit, KEY),
        tasks.HoldIn(on="T-2", until="2h"),
        _pl_actor(stall),
    )
    loop = await stall.ticks(110 * MIN, every=10 * MIN)
    assert loop.state == "running" and loops.held_by(loop) is not None
    assert task(cockpit, KEY).hold is not None
    loop = await stall.ticks(20 * MIN, every=10 * MIN)
    assert task(cockpit, KEY).hold is None
    [released] = [e for e in cockpit.events("task.released") if e.data.get("reason") == "lapsed"]
    assert released.data["why"] == "its time limit passed"
    told = [t for t in cockpit.mailer.texts_to(cockpit.world.id("pl")) if "HOLD LAPSED" in t]
    assert len(told) == 1
    assert told[0].startswith("[Factory notice · HOLD LAPSED · T-1 · its time limit passed]")
    # The clock runs again: an hour without progress stalls it.
    loop = await stall.ticks(70 * MIN, every=10 * MIN)
    assert loop.state == "stalled"
    assert [d.status for d in stall.stall_decisions()] == ["open"]


async def test_a_hold_on_an_answered_decision_is_cleared_on_the_tick(stall: Stall) -> None:
    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    asked = stall.ask("Keep the v1 route?", asker=cockpit.world.id("lead_a"))
    await tasks.hold_task(
        cockpit.app.state,
        cockpit.fid,
        task(cockpit, KEY),
        tasks.HoldIn(on=f"T-1 waits on {asked.ref}"),
        _pl_actor(stall),
    )
    assert task(cockpit, KEY).hold["ref"] == asked.ref
    await stall.tick(5 * MIN)
    cockpit.store.answer_decision(asked.id, option_id="yes", text=None, by="carol")
    await stall.tick(5 * MIN)
    assert task(cockpit, KEY).hold is None
    [text] = [t for t in cockpit.mailer.texts_to(cockpit.world.id("pl")) if "HOLD LAPSED" in t]
    assert text.startswith(f"[Factory notice · HOLD LAPSED · T-1 · {asked.ref} closed]")


async def test_held_by_carries_only_decisions_as_refs(stall: Stall) -> None:
    """Its own ready PR waits on a merge: no Decisions link, the label says what."""
    from tests.server.factory.test_loops import make_pr

    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    pr = make_pr(cockpit, KEY)
    cockpit.store.update_pr(pr.id, ready=True, ready_at=int(stall.clock()))
    loop = await stall.ticks(2 * HOUR)
    held = loops.held_by(loop)
    assert held is not None
    assert held["refs"] == [] and held["decision_id"] is None
    assert held["kind"] == "merge"
    assert held["label"] == held["decision_ref"] == f"waiting on a person to merge PR #{pr.number}"


async def test_a_decision_a_sibling_loop_still_rides_on_stays_open(stall: Stall) -> None:
    cockpit = stall.cockpit
    stall.status(stall.ic.id, "idle")
    row = await stall.ticks(65 * MIN)
    (asked,) = stall.stall_decisions()
    review, _ = cockpit.store.upsert_loop(
        cockpit.fid,
        "review",
        "pr:sibling",
        state="stalled",
        task_id=row.task_id,
        project_slug=SLUG,
        decision_id=asked.id,
    )
    cockpit.store.update_loop(row.id, state="converged", last_reason="T-1 is done")
    assert loop_escalation.stale_loop_decisions(cockpit.deps, cockpit.fid) == []
    await stall.tick(MIN)
    current = cockpit.store.get_decision(asked.id)
    assert current is not None and current.status == "open"
    cockpit.store.update_loop(review.id, state="converged")
    await stall.tick(MIN)
    current = cockpit.store.get_decision(asked.id)
    assert current is not None and (current.status, current.answered_by) == ("answered", "server")
