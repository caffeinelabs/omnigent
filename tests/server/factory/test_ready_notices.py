"""READY announcements once per PR head and gate states (pure rules and their readers).

The poller-level cases (a ready PR polled through base moves, re-runs and the
reminder interval) live in ``test_pr_poller.py``.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.entities.factory import FactoryEvent, FactoryPr
from omnigent.factory_gates import GateReport
from omnigent.server.factory import chat_cards, ready_notices
from omnigent.server.factory.prs import MERGEABLE_HELD_NOTE, hold_mergeable
from omnigent.server.factory.ready_notices import (
    NEW,
    READY_REMINDER_ENV,
    REMINDER,
    REPEAT,
    plan_ready,
    ready_key,
)
from omnigent.server.factory.slackdm import digest

HEAD = "a" * 40
GATES = {
    "draft": "pass",
    "base": "pass",
    "checks": "pass",
    "review": "pass",
    "verify": "pass",
    "mergeable": "pass",
    "protected_paths": "pass",
    "demo": None,
    "reasons": [],
    "notes": ["n"],
    "evidence": {"checks": {"passing": 2}},
}
DAY = 24 * 3600.0


def _key(**kw: Any) -> str:
    return ready_key(
        kw.pop("head", HEAD), kw.pop("gates", GATES), auto=kw.pop("auto", False), **kw
    )


def test_the_key_names_the_head_the_gate_states_the_merger_and_the_human() -> None:
    assert _key() == _key(gates={**GATES, "notes": [], "evidence": {}})
    assert _key() != _key(head="b" * 40)
    assert _key() != _key(gates={**GATES, "verify": "skip"})
    assert _key() != _key(gates={**GATES, "demo": "pass"})
    assert _key() != _key(auto=True)
    approved = {"verdict": "approved", "at": 5, "by": "lena"}
    assert _key() != _key(human_review=approved)
    assert _key(human_review=approved) != _key(human_review={**approved, "at": 9})


def test_plan_new_repeat_and_reminder() -> None:
    now = 1_000_000.0
    key = _key()
    assert plan_ready(key, None, None, now=now, reminder=DAY) == ready_notices.ReadyPlan(
        NEW, int(now)
    )
    announced = {"ready_key": key, "announced_at": int(now) - 600}
    repeat = plan_ready(key, announced, now - 600, now=now, reminder=DAY)
    assert (repeat.kind, repeat.announced_at) == (REPEAT, int(now) - 600)
    assert not repeat.mails_agents and not repeat.shows_in_room
    assert repeat.event_data(key) == {
        "ready_key": key,
        "announced_at": int(now) - 600,
        "repeat": True,
    }
    # A repeat carries the announcement it repeats, so the interval runs from there.
    later = plan_ready(key, repeat.event_data(key), now - 60, now=now + DAY - 600, reminder=DAY)
    assert later.kind == REMINDER and later.announced_at == int(now + DAY - 600)
    assert later.shows_in_room and not later.mails_agents
    assert later.event_data(key)["reminder"] is True
    changed = plan_ready(_key(head="c" * 40), announced, now - 600, now=now, reminder=DAY)
    assert changed.kind == NEW and changed.mails_agents


def test_an_event_from_before_the_key_is_announced_once_more() -> None:
    now = 1_000_000.0
    legacy = {"pr_id": "p" * 32, "auto_merge": False}
    assert plan_ready(_key(), legacy, now - 60, now=now, reminder=DAY).kind == NEW


def test_a_repeat_without_its_announcement_time_counts_from_the_event() -> None:
    now = 1_000_000.0
    data = {"ready_key": _key()}
    assert plan_ready(_key(), data, now - 60, now=now, reminder=DAY).kind == REPEAT
    assert plan_ready(_key(), data, now - DAY, now=now, reminder=DAY).kind == REMINDER


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [("", DAY), ("7200", 7200.0), ("60", 3600.0), ("soon", DAY)],
)
def test_the_reminder_interval_is_configurable(
    monkeypatch: pytest.MonkeyPatch, raw: str, seconds: float
) -> None:
    monkeypatch.setenv(READY_REMINDER_ENV, raw)
    assert ready_notices.reminder_s() == seconds


def test_quiet_flags() -> None:
    assert ready_notices.quiet_for_agents({"repeat": True})
    assert ready_notices.quiet_for_agents({"reminder": True})
    assert not ready_notices.quiet_for_agents({"repeat": "yes"})
    assert not ready_notices.quiet_for_agents(None)
    assert ready_notices.is_repeat({"repeat": True}) and not ready_notices.is_repeat({})
    assert ready_notices.is_reminder({"reminder": True})


# ── Readers: the Room and the digest ───────────────────────────


def _event(n: int, data: dict[str, Any], kind: str = "pr.ready") -> FactoryEvent:
    return FactoryEvent(
        id=f"{n:032x}",
        factory_id="f" * 32,
        ts=int(time.time() * 1000) - n,
        kind=kind,
        summary="PR #9945 ready for human merge",
        severity="notice",
        subject="pr:sample-org/sample-app#9945",
        project_slug="build-tier",
        data=data,
    )


def _pr() -> FactoryPr:
    return FactoryPr(
        id="d" * 32,
        factory_id="f" * 32,
        repo="sample-org/sample-app",
        number=9945,
        url="https://github.com/sample-org/sample-app/pull/9945",
        project_slug="build-tier",
    )


def test_the_room_skips_a_repeat_and_shows_a_reminder() -> None:
    pr = _pr()
    store = SimpleNamespace(get_pr=lambda _id: pr, get_pr_by_number=lambda *_a: pr)
    deps = SimpleNamespace(factory_store=store)
    first = chat_cards.plan_cards(deps, _event(1, {"pr_id": pr.id}))  # type: ignore[arg-type]
    assert [r.thread for r in first] == [f"pr:{pr.id}", "project:build-tier"]
    repeat = _event(2, {"pr_id": pr.id, "repeat": True})
    assert chat_cards.plan_cards(deps, repeat) == []  # type: ignore[arg-type]
    reminder = _event(3, {"pr_id": pr.id, "reminder": True})
    assert len(chat_cards.plan_cards(deps, reminder)) == 2  # type: ignore[arg-type]


def test_the_digest_counts_a_ready_pr_once() -> None:
    found = [
        _event(1, {"pr_id": "d" * 32}),
        *(_event(n, {"pr_id": "d" * 32, "repeat": True}) for n in range(2, 12)),
        _event(12, {"pr_id": "d" * 32, "reminder": True}),
        _event(13, {"pr_id": "d" * 32}, kind="pr.merged"),
    ]
    store = SimpleNamespace(
        find_events=lambda *_a, **_k: found,
        list_issues=lambda *_a, **_k: [],
    )
    counts, _ = digest._counts(
        SimpleNamespace(factory_store=store),  # type: ignore[arg-type]
        SimpleNamespace(factory_id="f" * 32),  # type: ignore[arg-type]
        digest.Visibility(),
        time.time(),
    )
    assert counts == {"pr.ready": 1, "pr.merged": 1}


# ── mergeable=UNKNOWN after a base move ────────────────────────


def _report(mergeable: str, evidence: str, **gates: str) -> GateReport:
    states = {k: v for k, v in GATES.items() if isinstance(v, str)}
    states.update(gates, mergeable=mergeable)
    reasons = {"mergeable": "mergeable=UNKNOWN (GitHub is still computing it; retry shortly)"}
    gate_reasons = {k: v for k, v in reasons.items() if states.get(k) != "pass"}
    return GateReport(
        gates=states,
        reasons=list(gate_reasons.values()),
        notes=[],
        ready=all(v in ("pass", "skip") for v in states.values()),
        gate_reasons=gate_reasons,
        evidence={"mergeable": evidence},
    )


def _row(mergeable_evidence: str = "MERGEABLE", **fields: Any) -> FactoryPr:
    base = {
        "id": "d" * 32,
        "factory_id": "f" * 32,
        "repo": "sample-org/sample-app",
        "number": 9945,
        "url": "u",
        "head_sha": HEAD,
        "state": "open",
        "ready": True,
        "gates": {**GATES, "evidence": {"mergeable": mergeable_evidence}},
    }
    return FactoryPr(**{**base, **fields})


def test_one_unknown_read_at_the_same_head_keeps_mergeable() -> None:
    held = hold_mergeable(_row(), _report("pending", "UNKNOWN"), HEAD, "open")
    assert held.ready is True and held.gates["mergeable"] == "pass"
    assert held.reasons == [] and held.gate_reasons == {}
    assert held.notes == [MERGEABLE_HELD_NOTE]
    assert held.evidence["mergeable"] == "UNKNOWN"
    # A not-ready PR keeps it too (no gate churn), but stays not ready.
    other = hold_mergeable(
        _row(ready=False), _report("pending", "UNKNOWN", checks="pending"), HEAD, "open"
    )
    assert other.gates["mergeable"] == "pass" and other.ready is False


@pytest.mark.parametrize(
    ("row", "report", "head", "state"),
    [
        (_row("UNKNOWN"), _report("pending", "UNKNOWN"), HEAD, "open"),
        (_row(), _report("pending", "UNKNOWN"), "b" * 40, "open"),
        (_row(), _report("fail", "CONFLICTING"), HEAD, "open"),
        (_row(), _report("pending", "UNKNOWN"), HEAD, "merged"),
        (_row(state="closed"), _report("pending", "UNKNOWN"), HEAD, "open"),
        (
            _row(gates={**GATES, "mergeable": "pending", "evidence": {"mergeable": "MERGEABLE"}}),
            _report("pending", "UNKNOWN"),
            HEAD,
            "open",
        ),
    ],
)
def test_anything_else_is_left_as_github_reports_it(
    row: FactoryPr, report: GateReport, head: str, state: str
) -> None:
    assert hold_mergeable(row, report, head, state) is report
