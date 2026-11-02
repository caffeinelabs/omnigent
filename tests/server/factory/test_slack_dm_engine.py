"""The Slack DM engine on a fake clock against a fake Slack.

Who gets what (per role and assignment, with the owner fallback), the 30 s
gather and one DM a minute, reminders and the owners' escalation, last calls
before a default, quiet hours (released with a relevance re-check, critical
incidents breaking through), hold while active, the daily cap, the ledger
surviving a restart, the linked-after rule, a dead token and a broken link,
escaping, idle spend and the digest's visibility.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.entities.factory import FactoryDecision, FactoryProposal
from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import events, issues, snapshot
from omnigent.server.factory import presence as factory_presence
from omnigent.server.factory.slackdm import engine as engine_mod
from omnigent.server.factory.slackdm import rules, secrets
from omnigent.server.factory.slackdm.engine import Engine
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld, FakeClock
from tests.server.factory.slack_fake import TOKEN, FakeSlack, installed
from tests.server.factory.slack_helpers import install_bot, link, notify_of, slack_cockpit

DOMAIN = "omnigent.test"
H = 3600.0


@pytest.fixture()
def fake_slack() -> Iterator[FakeSlack]:
    with installed() as fake:
        yield fake


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_slack: FakeSlack,
) -> AsyncIterator[Cockpit]:
    monkeypatch.setenv("OMNIGENT_DOMAIN", DOMAIN)
    async for value in slack_cockpit(world, db_uri, monkeypatch, tmp_path):
        await install_bot(value)
        yield value


class Rig:
    """An engine on a fake clock over the cockpit."""

    def __init__(self, cockpit: Cockpit, fake: FakeSlack, cost: float | None = None) -> None:
        self.cockpit = cockpit
        self.fake = fake
        self.clock = FakeClock()
        self.cost = cost

        async def _cost(_state: Any, _root: Any) -> float | None:
            return self.cost

        self.engine = Engine(cockpit.app.state, clock=self.clock, cost_fn=_cost)
        self.engine.pace_s = 0

    async def tick(self, advance: float = 0.0) -> engine_mod.TickReport:
        self.clock.advance(advance)
        snapshot.reset_cache()
        return await self.engine.tick()

    def restart(self) -> None:
        """A new process: queues and counters gone, the ledger rebuilt from events."""
        self.engine = Engine(self.cockpit.app.state, clock=self.clock, cost_fn=self.engine.cost_fn)
        self.engine.pace_s = 0


@pytest.fixture()
def rig(cockpit: Cockpit, fake_slack: FakeSlack) -> Rig:
    return Rig(cockpit, fake_slack)


def _decision(
    cockpit: Cockpit,
    question: str = "Approve project validate-build-tier?",
    *,
    assignee: str | None = "operators",
    key: str = "d1",
    **fields: Any,
) -> FactoryDecision:
    fields.setdefault("blocking", False)
    row, _ = cockpit.store.create_decision(
        cockpit.fid, dedupe_key=key, question=question, assignee=assignee, **fields
    )
    return row


def _proposal(cockpit: Cockpit, **fields: Any) -> FactoryProposal:
    row, _ = cockpit.store.create_proposal(
        cockpit.fid,
        kind=fields.pop("kind", "projects"),
        title="validate-build-tier",
        author_kind="agent",
        author="atlas",
        **fields,
    )
    return row


def _quiet_now(clock: FakeClock, **extra: Any) -> dict[str, Any]:
    """Quiet hours (UTC) that hold now and end within two hours."""
    hour = datetime.fromtimestamp(clock.now, tz=UTC).hour
    return {
        "tz": "UTC",
        "quiet": {
            "enabled": True,
            "start": f"{(hour - 1) % 24:02d}:00",
            "end": f"{(hour + 1) % 24:02d}:00",
            **extra,
        },
    }


def _ledger(cockpit: Cockpit) -> list[tuple[str, str, str, str]]:
    return [
        (e.data["to_user"], e.data["key"].split(":")[-1], e.subject, e.data["status"])
        for e in cockpit.events("notify.slack")
    ]


# ── E1: decisions reach the people they are for ────────────────


async def test_a_non_blocking_proposal_decision_reaches_its_operator(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol")
    link(cockpit, "alice")
    proposal = _proposal(cockpit)
    decision = _decision(cockpit, source="proposal", subject=f"proposal:C-{proposal.number}")

    first = await rig.tick()
    assert first.queued == 1 and fake_slack.posts == []  # the 30 s gather
    await rig.tick(31)

    [text] = fake_slack.texts_to("U0CAROL")
    assert fake_slack.dms_to("U0ALICE") == []  # an operator is reachable: owners are not DM'd
    assert "D-1 needs you" in text and "Approve project validate-build-tier?" in text
    assert "Why you: assigned to operators" in text
    assert "expires and nothing is started in 2 d 23 h" in text
    fid = snapshot.bare_id(cockpit.fid)
    assert f"<https://{DOMAIN}/factory/{fid}/improvements/C-1|Open C-1>" in text
    assert _ledger(cockpit) == [("carol", "new", "decision:D-1", "sent")]

    await rig.tick(120)
    assert len(fake_slack.posts) == 1  # never twice

    body = (
        await cockpit.client.get(
            f"/v1/factory/factories/{cockpit.fid}", headers={"X-Test-User": "alice"}
        )
    ).json()
    [view] = [d for d in body["decisions"] if d["ref"] == "D-1"]
    assert [(n["user_id"], n["stage"], n["ok"]) for n in view["notified"]] == [
        ("carol", "new", True)
    ]
    assert view["deadline_at"] == proposal.created_at + 72 * 3600
    assert view["deadline_effect"] == "expires and nothing is started"
    assert decision.blocking is False


async def test_operator_decisions_go_to_owners_when_no_operator_is_reachable(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "alice")
    _decision(cockpit, "Raise loop budget?")

    await rig.tick()
    await rig.tick(31)

    [text] = fake_slack.texts_to("U0ALICE")
    assert "no operator gets Slack DMs, so owners get it" in text


async def test_a_person_assignee_and_a_claim_route_to_that_person_only(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol")
    link(cockpit, "alice")
    _decision(cockpit, "For carol", assignee="carol", key="a")
    claimed = _decision(cockpit, "Claimed by alice", assignee="operators", key="b")
    cockpit.store.route_decision(claimed.id, claimed_by="alice", claimed_at=int(time.time()))

    await rig.tick()
    await rig.tick(31)

    assert "For carol" in fake_slack.texts_to("U0CAROL")[0]
    assert "Claimed by alice" not in fake_slack.texts_to("U0CAROL")[0]
    assert "you claimed it" in fake_slack.texts_to("U0ALICE")[0]


def test_recipients_per_assignee_kind() -> None:
    from omnigent.server.factory.slackdm.prefs import load

    def person(user: str, role: str, linked: bool = True) -> rules.Person:
        raw = {"slack": {"status": "verified", "user_id": f"U{user}", "linked_at": 1}}
        return rules.Person(user, role, load(raw if linked else None, role))

    ctx = rules.FactoryCtx(
        "f",
        "F",
        {
            "o": person("o", "owner"),
            "p": person("p", "operator"),
            "v": person("v", "viewer"),
        },
    )
    d = FactoryDecision(id="1", factory_id="f", number=1, question="q", assignee="owners")
    assert [u for u, _ in rules.decision_recipients(ctx, d)] == ["o"]
    d = FactoryDecision(id="1", factory_id="f", number=1, question="q", assignee=None)
    assert [u for u, _ in rules.decision_recipients(ctx, d)] == ["p"]
    d = FactoryDecision(id="1", factory_id="f", number=1, question="q", assignee="v")
    assert [u for u, _ in rules.decision_recipients(ctx, d)] == ["o"]  # a viewer cannot answer
    ctx.people["p"] = person("p", "operator", linked=False)
    d = FactoryDecision(id="1", factory_id="f", number=1, question="q", assignee="operators")
    assert [u for u, _ in rules.decision_recipients(ctx, d)] == ["o"]


# ── E2/E3: reminders and the owners' escalation ───────────────


async def test_reminders_at_4h_and_24h_then_every_owner(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol")
    link(cockpit, "alice")
    _decision(cockpit, "Keep or revert C-4?")

    await rig.tick()
    await rig.tick(31)
    await rig.tick(3 * H)
    assert len(fake_slack.dms_to("U0CAROL")) == 1
    await rig.tick(1 * H)
    await rig.tick(31)
    assert "Reminder: D-1 is still waiting on you" in fake_slack.texts_to("U0CAROL")[-1]
    await rig.tick(20 * H)
    await rig.tick(31)

    assert "Reminder: D-1" in fake_slack.texts_to("U0CAROL")[-1]
    [owner] = fake_slack.texts_to("U0ALICE")
    assert "D-1 is waiting on the owners" in owner and "open 24 h" in owner
    stages = sorted((u, s) for u, s, _, _ in _ledger(cockpit))
    assert stages == [("alice", "owners"), ("carol", "new"), ("carol", "r24h"), ("carol", "r4h")]


async def test_linking_does_not_set_off_old_reminders(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    _decision(cockpit, "Older than the link")
    link(cockpit, "carol", linked_at=time.time() + 60)

    await rig.tick(120)
    await rig.tick(31)
    assert fake_slack.posts == []  # the welcome DM listed it
    await rig.tick(4 * H)
    await rig.tick(31)

    assert "Reminder: D-1" in fake_slack.texts_to("U0CAROL")[0]


# ── E4: last call before something defaults ──────────────────


def test_deadlines_for_expiry_grace_and_trials() -> None:
    d = FactoryDecision(id="d", factory_id="f", number=19, question="q", subject="proposal:C-3")
    pending = FactoryProposal(
        id="p", factory_id="f", number=3, kind="projects", title="t", created_at=1000
    )
    assert rules.deadline_for(d, pending, "confirm") == (
        1000 + 72 * 3600,
        "expires and nothing is started",
    )
    grace = FactoryProposal(
        id="p",
        factory_id="f",
        number=3,
        kind="projects",
        title="t",
        created_at=1000,
        result={"grace_until": 1900},
    )
    assert rules.deadline_for(d, grace, "grace") == (1900, "auto-applies unless someone objects")
    trial = FactoryProposal(
        id="p",
        factory_id="f",
        number=4,
        kind="tuning",
        title="t",
        status="trial",
        applied_at=10_000,
        trial_until=10_000 + 48 * 3600,
        result={"evaluation": {"outcome": "inconclusive"}, "keep_decision_id": "d"},
    )
    assert rules.deadline_for(d, trial, None) == (
        10_000 + 96 * 3600,
        "C-4 reverts on its own",
    )
    extended = FactoryProposal(
        id="p",
        factory_id="f",
        number=4,
        kind="tuning",
        title="t",
        status="trial",
        applied_at=10_000,
        trial_until=10_000 + 3 * 48 * 3600,  # extended twice while idle
        result={
            "evaluation": {"outcome": "inconclusive"},
            "keep_decision_id": "d",
            "trial_window_s": 48 * 3600,
        },
    )
    assert rules.deadline_for(d, extended, None) == (
        10_000 + 4 * 48 * 3600,
        "C-4 reverts on its own",
    )
    kept = FactoryProposal(
        id="p",
        factory_id="f",
        number=4,
        kind="tuning",
        title="t",
        status="trial",
        applied_at=10_000,
        trial_until=10_000 + 3 * 48 * 3600,
        result={
            "evaluation": {"outcome": "inconclusive", "idle": True},
            "keep_decision_id": "d",
            "default": "keep",
            "default_at": 900_000,
        },
    )
    assert rules.deadline_for(d, kept, None) == (900_000, "C-4 stays applied")
    assert rules.deadline_for(d, None, None) is None
    for kind in ("tuning", "rules"):
        for status in ("pending", "awaiting_cosign"):
            tuning = FactoryProposal(
                id="p",
                factory_id="f",
                number=5,
                kind=kind,
                title="t",
                status=status,
                created_at=1000,
            )
            assert rules.deadline_for(d, tuning, "confirm") == (
                1000 + 72 * 3600,
                "expires and is not applied",
            )
    applied = FactoryProposal(
        id="p", factory_id="f", number=5, kind="tuning", title="t", status="applied"
    )
    assert rules.deadline_for(d, applied, None) is None


async def test_the_last_call_reaches_recipients_and_owners_two_hours_before(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol", tz="UTC", quiet={"enabled": False})
    link(cockpit, "alice", tz="UTC", quiet={"enabled": False})
    proposal = _proposal(cockpit)
    _decision(cockpit, source="proposal", subject=f"proposal:C-{proposal.number}")
    for step in (0, 31, 4 * H, 31, 20 * H, 31, 61):
        await rig.tick(step)
    sent = len(fake_slack.posts)
    assert not any("Last call" in p["text"] for p in fake_slack.posts)

    await rig.tick(proposal.created_at + 70 * H + 60 - rig.clock.now)
    await rig.tick(31)

    texts = fake_slack.texts_to("U0CAROL")[-1], fake_slack.texts_to("U0ALICE")[-1]
    assert len(fake_slack.posts) == sent + 2
    for text in texts:
        assert "Last call: D-1" in text
        assert "If nobody answers by" in text and "(UTC), expires and nothing is started" in text


async def test_a_tuning_proposal_gets_its_deadline_and_a_last_call(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "alice", tz="UTC", quiet={"enabled": False})
    proposal = _proposal(cockpit, kind="tuning")
    _decision(
        cockpit,
        assignee="owners",
        source="proposal",
        subject=f"proposal:C-{proposal.number}",
    )
    snapshot.reset_cache()
    body = (
        await cockpit.client.get(
            f"/v1/factory/factories/{cockpit.fid}", headers={"X-Test-User": "alice"}
        )
    ).json()
    [decision] = [d for d in body["decisions"] if d["ref"] == "D-1"]
    assert decision["deadline_at"] == proposal.created_at + 72 * 3600
    assert decision["deadline_effect"] == "expires and is not applied"
    for step in (0, 31, 4 * H, 31, 20 * H, 31, 61):
        await rig.tick(step)
    assert not any("Last call" in p["text"] for p in fake_slack.posts)

    await rig.tick(proposal.created_at + 70 * H + 60 - rig.clock.now)
    await rig.tick(31)

    last = fake_slack.texts_to("U0ALICE")[-1]
    assert "Last call: D-1" in last and "(UTC), expires and is not applied" in last


def test_a_last_call_moves_before_quiet_hours() -> None:
    from omnigent.server.factory.slackdm.prefs import load

    raw = {"slack": {"status": "verified", "user_id": "U1", "linked_at": 1}}
    ctx = rules.FactoryCtx("f", "F", {"o": rules.Person("o", "owner", load(raw, "owner"))})
    ctx.proposals["C-1"] = FactoryProposal(
        id="p", factory_id="f", number=1, kind="projects", title="t", created_at=0
    )
    d = FactoryDecision(
        id="d", factory_id="f", number=1, question="q", assignee="owners", subject="proposal:C-1"
    )
    deadline = 72 * 3600
    quiet_start = deadline - 5 * 3600

    early = rules.decision_items(
        ctx,
        d,
        quiet_start - 15 * 60,
        first_sent={},
        quiet_start_of=lambda _p, _at: quiet_start,
    )
    plain = rules.decision_items(ctx, d, quiet_start - 15 * 60, first_sent={})

    assert [i.stage for _, i in early if i.stage == "deadline"] == ["deadline"]
    assert not any(i.stage == "deadline" for _, i in plain)


# ── Quiet hours, hold while active, critical incidents ────────


async def test_quiet_hours_hold_and_release_only_what_still_matters(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol", **_quiet_now(rig.clock))
    keep = _decision(cockpit, "Still open", key="a")
    gone = _decision(cockpit, "Answered overnight", key="b")

    await rig.tick()
    await rig.tick(31)
    assert fake_slack.posts == []
    cockpit.store.answer_decision(gone.id, option_id=None, text="done", by="alice")
    await rig.tick(2 * H)

    [text] = fake_slack.texts_to("U0CAROL")
    assert "While you were away" in text
    assert "Still open" in text and "Answered overnight" not in text
    assert keep.ref == "D-1"


async def test_critical_incidents_break_through_quiet_hours_when_opted_in(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol", **_quiet_now(rig.clock, critical_through=True))
    link(cockpit, "alice", **_quiet_now(rig.clock))
    await rig.tick()
    target = f"member:{cockpit.world.id('pl')}"
    await issues.raise_issue(
        cockpit.app.state,
        cockpit.fid,
        rule="agent.quiet",
        target=target,
        summary="PL silent",
        severity="critical",
        project="api-version",
    )
    await issues.raise_issue(
        cockpit.app.state,
        cockpit.fid,
        rule="agent.repeat",
        target=target,
        summary="repeating",
        severity="warning",
        project="api-version",
    )
    await issues.raise_issue(
        cockpit.app.state,
        cockpit.fid,
        rule="pr.ready_unreviewed",
        target="pr:x",
        summary="notice only",
        severity="notice",
    )

    await rig.tick(1)
    await rig.tick(31)

    [carol] = fake_slack.texts_to("U0CAROL")
    assert "INC-1 critical" in carol and "repeating" not in carol
    assert fake_slack.dms_to("U0ALICE") == []  # owners get critical ones, but alice is quiet
    await rig.tick(2 * H)
    alice = fake_slack.texts_to("U0ALICE")[0]
    assert "While you were away" in alice and "INC-1" in alice
    assert "notice only" not in "".join(fake_slack.texts_to("U0CAROL"))


async def test_hold_while_active_in_the_cockpit(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol")
    factory_presence.registry.heartbeat(cockpit.fid, "carol", "overview", False)
    _decision(cockpit)

    await rig.tick()
    await rig.tick(31)
    assert fake_slack.posts == []
    factory_presence.registry.heartbeat(cockpit.fid, "carol", "overview", True)  # idle now
    await rig.tick(engine_mod.HOLD_S)

    assert len(fake_slack.dms_to("U0CAROL")) == 1


# ── Grouping, the daily cap, retries ──────────────────────────


async def test_everything_due_goes_into_one_dm_a_minute(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol")
    _decision(cockpit, "First", key="a")
    _decision(cockpit, "Second", key="b")
    await rig.tick()
    await rig.tick(31)
    [grouped] = fake_slack.texts_to("U0CAROL")
    assert "2 updates" in grouped and "First" in grouped and "Second" in grouped

    _decision(cockpit, "Third", key="c")
    await rig.tick(10)
    await rig.tick(31)
    assert len(fake_slack.posts) == 1  # within the minute
    await rig.tick(30)
    assert "Third" in fake_slack.texts_to("U0CAROL")[1]


async def test_the_daily_cap_mutes_the_rest_until_the_digest(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_mod, "DAILY_CAP", 2)
    link(cockpit, "carol", tz="UTC", quiet={"enabled": False})
    for n in range(3):
        _decision(cockpit, f"Q{n}", key=f"k{n}")
        await rig.tick()
        await rig.tick(61)

    texts = fake_slack.texts_to("U0CAROL")
    assert len(texts) == 2
    assert "the rest waits for tomorrow's digest" in texts[1]
    assert _ledger(cockpit)[-1][3] == "muted"


async def test_the_daily_cap_still_lets_last_calls_and_criticals_through(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_mod, "DAILY_CAP", 1)
    link(cockpit, "carol", tz="UTC", quiet={"enabled": False})
    for n in range(2):
        _decision(cockpit, f"Q{n}", key=f"k{n}")
        await rig.tick()
        await rig.tick(61)
    assert len(fake_slack.texts_to("U0CAROL")) == 1
    assert _ledger(cockpit)[-1][3] == "muted"

    await issues.raise_issue(
        cockpit.app.state,
        cockpit.fid,
        rule="agent.quiet",
        target=f"member:{cockpit.world.id('pl')}",
        summary="PL silent",
        severity="critical",
        project="api-version",
    )
    await rig.tick(61)
    await rig.tick(31)

    texts = fake_slack.texts_to("U0CAROL")
    assert len(texts) == 2 and "INC-1 critical" in texts[1]


async def test_a_rate_limit_backs_off_with_retry_after(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol")
    _decision(cockpit)
    fake_slack.fail_next("chat.postMessage", status=429, retry_after=42)

    await rig.tick()
    report = await rig.tick(31)
    assert [o.status for o in report.sent] == ["retry"]
    await rig.tick(20)
    assert len(fake_slack.posts) == 0
    await rig.tick(25)

    assert len(fake_slack.posts) == 1


async def test_refused_blocks_are_retried_once_as_plain_text(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol")
    _decision(cockpit)
    fake_slack.fail_next("chat.postMessage", error="invalid_blocks")

    await rig.tick()
    await rig.tick(31)

    [post] = fake_slack.posts
    assert "blocks" not in post and "D-1 needs you" in post["text"]


async def test_agent_text_cannot_ping_the_channel_or_spoof_links(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol")
    _decision(cockpit, "<!channel> please <https://evil.example|re-login> & <@U0ALICE>")

    await rig.tick()
    await rig.tick(31)

    [text] = fake_slack.texts_to("U0CAROL")
    assert "<!channel>" not in text and "<https://evil" not in text and "<@U0ALICE>" not in text
    assert "&lt;!channel&gt; please &lt;https://evil.example|re-login&gt; &amp;" in text


# ── Durability, broken tokens and links ───────────────────────


async def test_a_restart_never_sends_twice(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol")
    _decision(cockpit)
    pr, _ = cockpit.store.upsert_pr(
        cockpit.fid, "caffeinelabs/app", 9774, title="Build tier", ready=True, head_sha="a" * 40
    )
    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "pr.ready",
        subject="pr:caffeinelabs/app#9774",
        summary="PR #9774 ready for human merge",
        data={"pr_id": pr.id},
    )
    await rig.tick()
    await rig.tick(31)
    assert len(fake_slack.posts) == 1

    rig.restart()
    await rig.tick(120)
    await rig.tick(31)

    assert len(fake_slack.posts) == 1
    keys = [k for _, k, _, _ in _ledger(cockpit)]
    assert len(keys) == 2


async def test_a_restart_reads_the_whole_ledger_however_long(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.entities.factory import EventDraft

    monkeypatch.setattr(engine_mod, "LEDGER_PAGE", 20)
    old = int((time.time() - 10 * 86400) * 1000)
    for i in range(70):  # older receipts than D-1's: several pages of them
        cockpit.store.append_event(
            EventDraft(
                factory_id=cockpit.fid,
                kind="notify.slack",
                summary="old",
                subject=f"decision:D-old{i}",
                data={"to_user": "alice", "key": f"decision:old{i}:new", "status": "sent"},
                ts=old + i,
            )
        )
    link(cockpit, "carol")
    decision = _decision(cockpit)
    await rig.tick()
    await rig.tick(31)
    assert len(fake_slack.dms_to("U0CAROL")) == 1

    rig.restart()
    await rig.engine.load()
    assert rig.engine.in_ledger(cockpit.fid, "carol", f"decision:{decision.id}:new")
    assert sum(1 for k in rig.engine.ledger if k[1] == "alice") == 70

    for step in (120, 31, 31):
        await rig.tick(step)
    assert len(fake_slack.dms_to("U0CAROL")) == 1


def test_ledger_pages_step_over_rows_sharing_a_millisecond(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.entities.factory import EventDraft

    monkeypatch.setattr(engine_mod, "LEDGER_PAGE", 4)
    stamps = [1000, 1001, 1002, 1003, 1003, 1003, 1004, 1005, 1005]
    for i, ts in enumerate(stamps):
        cockpit.store.append_event(
            EventDraft(
                factory_id=cockpit.fid,
                kind="notify.slack",
                summary=str(i),
                data={"to_user": "carol", "key": f"k{i}"},
                ts=ts,
            )
        )

    rows = engine_mod.ledger_rows(cockpit.store, None, 0)
    assert sorted(r.summary for r in rows) == [str(i) for i in range(len(stamps))]
    newest = list(engine_mod.ledger_newest(cockpit.store, cockpit.fid, 1002))
    assert [r.ts for r in newest] == [1005, 1005, 1004, 1003, 1003, 1003, 1002]


async def test_a_second_process_does_not_resend_what_the_first_sent(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    """During a rollout two server processes run engines over the same store."""
    link(cockpit, "carol")
    other = Engine(cockpit.app.state, clock=rig.clock, cost_fn=rig.engine.cost_fn)
    other.pace_s = 0
    await other.load()
    decision = _decision(cockpit)

    await rig.tick()
    await other.tick()
    await rig.tick(31)
    assert len(fake_slack.dms_to("U0CAROL")) == 1
    await other.tick()

    assert len(fake_slack.dms_to("U0CAROL")) == 1
    assert other.in_ledger(cockpit.fid, "carol", f"decision:{decision.id}:new")


async def test_a_dead_token_stops_every_dm_and_raises_the_incident(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol")
    _decision(cockpit)
    fake_slack.tokens.pop(TOKEN)

    await rig.tick()
    await rig.tick(31)
    calls = len(fake_slack.calls)
    await rig.tick(120)

    assert fake_slack.posts == [] and len(fake_slack.calls) == calls
    incident = issues.open_issue_for(cockpit.deps, cockpit.fid, "slack.bot", "factory")
    assert incident is not None and incident.severity == "warning"
    meta = secrets.bot_meta(cockpit.deps, cockpit.fid)
    assert meta is not None and meta["status"] == "invalid"


async def test_a_broken_link_shows_on_the_person(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol")
    _decision(cockpit)
    fake_slack.fail_next("chat.postMessage", error="channel_not_found")
    fake_slack.fail_next("chat.postMessage", error="messages_tab_disabled")

    await rig.tick()
    await rig.tick(31)

    assert fake_slack.methods().count("conversations.open") == 1  # re-opened once
    slack = notify_of(cockpit, "carol")["slack"]
    assert (slack["status"], slack["error"]) == ("error", "messages_tab_disabled")
    assert _ledger(cockpit) == [("carol", "new", "decision:D-1", "failed")]


async def test_the_engine_is_inert_without_a_token(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    await secrets.set_bot_token(cockpit.app.state, cockpit.fid, user_id="alice", token=None)
    link(cockpit, "carol")
    _decision(cockpit)
    calls = len(fake_slack.calls)

    await rig.tick()
    await rig.tick(31)

    assert len(fake_slack.calls) == calls and cockpit.events("notify.slack") == []


# ── PRs, controls, budgets, mentions, idle spend ──────────────


async def test_a_ready_pr_goes_to_the_projects_human_owner(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol")
    link(cockpit, "alice")
    cockpit.store.upsert_project(
        cockpit.fid, "api-version", title="API version", charter={"human_owner": "carol"}
    )
    pr, _ = cockpit.store.upsert_pr(
        cockpit.fid,
        "caffeinelabs/app",
        9781,
        title="ENV badge click-to-copy",
        project_slug="api-version",
        ready=True,
        ready_at=int(time.time()),
        head_sha="b" * 40,
        preview={"state": "ready", "urls": {"app": "https://p9781.env.omnigent.test"}},
    )
    stale, _ = cockpit.store.upsert_pr(
        cockpit.fid, "caffeinelabs/app", 9782, project_slug="api-version", ready=True
    )
    for row in (pr, stale):
        await events.emit(
            cockpit.app.state,
            cockpit.fid,
            "pr.ready",
            project="api-version",
            subject=f"pr:caffeinelabs/app#{row.number}",
            summary="ready",
            data={"pr_id": row.id},
        )
    cockpit.store.update_pr(stale.id, ready=False)

    await rig.tick()
    await rig.tick(31)

    [text] = fake_slack.texts_to("U0CAROL")
    assert "PR #9781 ready for human merge" in text and "you own project api-version" in text
    assert "9782" not in text  # no longer ready when it was due
    assert fake_slack.dms_to("U0ALICE") == []
    assert f"<https://{DOMAIN}/v1/factory/prs/{pr.id}/preview|preview>" in text


async def test_pauses_budgets_and_warnings_reach_owners_but_not_the_actor(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "alice")
    link(cockpit, "carol")
    cockpit.store.upsert_project(
        cockpit.fid, "api-version", title="API version", charter={"human_owner": "carol"}
    )
    await rig.tick()
    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "control.pause",
        project="api-version",
        subject="project:api-version",
        summary="paused",
        data={"by": "alice (creator)", "target": "project:api-version", "action": "pause"},
    )
    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "budget.exceeded",
        severity="warning",
        subject="budget:factory",
        summary="the factory today spent $401.00 of $400 (100 %)",
        data={"scope": "factory", "by": "server"},
    )

    await rig.tick(1)
    await rig.tick(31)

    carol = fake_slack.texts_to("U0CAROL")[0]
    alice = fake_slack.texts_to("U0ALICE")[0]
    assert "Project api-version paused" in carol and "by alice (creator)" in carol
    assert "paused" not in alice  # she did it herself
    assert "Budget exceeded" in alice and "Budget exceeded" not in carol  # operators: no budgets


async def test_task_budgets_stay_with_their_loop(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "alice")
    await rig.tick()
    for scope, summary in (("task", "implement #42: $8.00 of $10.00"), ("project", "#api 80 %")):
        await events.emit(
            cockpit.app.state,
            cockpit.fid,
            "budget.near",
            severity="notice",
            project="api-version",
            subject=f"budget:{scope}",
            summary=summary,
            data={"scope": scope},
        )

    await rig.tick(1)
    await rig.tick(31)

    [alice] = fake_slack.texts_to("U0ALICE")
    assert "#api 80 %" in alice and "implement #42" not in alice


async def test_an_event_held_through_long_quiet_hours_still_arrives(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    hour = datetime.fromtimestamp(rig.clock.now, tz=UTC).hour
    quiet = {
        "enabled": True,
        "start": f"{(hour - 1) % 24:02d}:00",
        "end": f"{(hour + 14) % 24:02d}:00",
    }
    link(cockpit, "alice", tz="UTC", quiet=quiet)
    await rig.tick()
    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "control.stop",
        subject="factory",
        summary="stopped",
        data={"by": "carol (operator)", "target": "factory", "action": "stop"},
    )
    await rig.tick(1)
    await rig.tick(31)
    assert fake_slack.posts == []

    await rig.tick(14 * H)

    [alice] = fake_slack.texts_to("U0ALICE")
    assert "While you were away" in alice and "The factory stopped" in alice


async def test_a_mention_is_dropped_once_the_thread_was_read(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "carol")
    await rig.tick()
    for seq, message in ((5, "a" * 32), (6, "b" * 32)):
        await events.emit(
            cockpit.app.state,
            cockpit.fid,
            "chat.posted",
            subject=f"chat:{message}",
            summary=f"alice in Factory: @carol look #{seq}",
            data={
                "by": "alice",
                "by_kind": "human",
                "message_id": message,
                "thread": "factory",
                "seq": seq,
                "mentions": ["@carol"],
            },
        )
    await rig.tick(1)
    cockpit.store.upsert_chat_read(cockpit.fid, "carol", "factory", 5)
    await rig.tick(31)

    [text] = fake_slack.texts_to("U0CAROL")
    assert "look #6" in text and "look #5" not in text


async def test_a_retro_names_who_filed_it_and_keeps_its_target(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "alice", events={"retros": True})
    await rig.tick()
    issue = await issues.raise_issue(
        cockpit.app.state,
        cockpit.fid,
        rule="retro",
        target=f"member:{cockpit.world.id('pl')}",
        summary="the PL retried a red build five times",
        class_="finding",
        severity="info",
        project="api-version",
    )
    assert issue is not None
    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "retro.added",
        project="api-version",
        subject=f"issue:{issue.ref}",
        summary="Lead B filed a finding on api-version: retried a red build",
        data={"issue_id": issue.id, "class": "finding", "by": cockpit.world.id("lead_b")},
    )

    await rig.tick(1)
    await rig.tick(31)

    [alice] = fake_slack.texts_to("U0ALICE")
    assert "Sentinel filed a finding" in alice
    ctx = await engine_mod.factory_ctx(cockpit.app.state, cockpit.world.get("lead_a"))
    [(_, item)] = [
        pair
        for pair in rules.retro_items(ctx, cockpit.events("retro.added")[-1], issue)
        if pair[0] == "alice"
    ]
    assert item.target == issue.target


async def test_idle_spend_tells_the_owners_once_a_day(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "alice")
    link(cockpit, "carol")
    start = rig.clock.now
    rig.engine.samples[snapshot.bare_id(cockpit.fid)] = deque(
        [(start - 6 * H, 100.0), (start - 3 * H, 108.0)]
    )
    rig.cost = 116.27

    await rig.tick()
    await rig.tick(31)
    await rig.tick(engine_mod.IDLE_SAMPLE_S)
    await rig.tick(31)

    [text] = fake_slack.texts_to("U0ALICE")
    assert "The factory is idling" in text and "$16.27 spent in the last 6 h" in text
    assert fake_slack.dms_to("U0CAROL") == []


async def test_a_paused_factory_is_not_idling(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    link(cockpit, "alice")
    cockpit.world.store.set_labels(cockpit.fid, {F.LABEL_STATE: "paused"})
    start = rig.clock.now
    rig.engine.samples[snapshot.bare_id(cockpit.fid)] = deque([(start - 6 * H, 100.0)])
    rig.cost = 130.0

    await rig.tick()
    await rig.tick(31)

    assert fake_slack.posts == []


# ── The daily digest ──────────────────────────────────────────


async def test_the_digest_lists_what_waits_and_hides_unreadable_projects(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    hour = datetime.fromtimestamp(rig.clock.now, tz=UTC).hour
    digest = {"enabled": True, "hour": hour, "days": "daily"}
    plain = {"tz": "UTC", "quiet": {"enabled": False}, "digest": digest}
    cockpit.store.upsert_person(cockpit.fid, "bob", role="viewer", invited_by="alice")
    cockpit.permissions.grant("bob", cockpit.fid, LEVEL_READ)  # the root only
    from tests.server.factory import slack_helpers

    monkeypatch.setitem(slack_helpers.SLACK_IDS, "bob", "U0BOB")
    fake_slack.add_user("U0BOB", "Bob Viewer", "bob@slack.example", tz="UTC")
    link(cockpit, "alice", linked_at=rig.clock.now - 2 * 86400, **plain)
    link(cockpit, "bob", linked_at=rig.clock.now - 2 * 86400, **plain)
    cockpit.store.upsert_project(cockpit.fid, "api-version", title="API version")
    _decision(
        cockpit,
        "Approve the api-version rollout?",
        assignee="owners",
        project_slug="api-version",
        asked_by_session_id=cockpit.world.id("pl"),
    )
    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "pr.merged",
        project="api-version",
        subject="pr:caffeinelabs/app#1",
        summary="merged",
    )

    report = await rig.tick()

    assert report.digests == ["alice"]
    digest_text = next(t for t in fake_slack.texts_to("U0ALICE") if "daily digest" in t)
    assert (
        "*Waiting on you (1)*" in digest_text and "Approve the api-version rollout?" in digest_text
    )
    assert "1 merged" in digest_text
    assert "*In progress (1)*" in digest_text and "|api-version> (Planning)" in digest_text
    assert fake_slack.dms_to("U0BOB") == []  # nothing he may read happened: skipped
    assert [(u, s, st) for u, _, s, st in _ledger(cockpit)] == [("alice", "digest", "sent")]

    await rig.tick(120)
    assert sum("daily digest" in t for t in fake_slack.texts_to("U0ALICE")) == 1


async def test_posts_are_paced_one_a_second_per_token(
    rig: Rig, cockpit: Cockpit, fake_slack: FakeSlack
) -> None:
    waits: list[float] = []

    async def _sleep(seconds: float) -> None:
        waits.append(seconds)

    rig.engine.sleep = _sleep
    rig.engine.pace_s = 1.0
    link(cockpit, "carol")
    link(cockpit, "alice")
    _decision(cockpit, "For carol", assignee="carol", key="a")
    _decision(cockpit, "For owners", assignee="owners", key="b")

    await rig.tick()
    await rig.tick(31)

    assert len(fake_slack.posts) == 2
    assert len(waits) == 1 and 0 < waits[0] <= 1.0
