"""The one "Needs you" list is sorted by the cost of waiting, by fixed rules.

Something broken first, then what blocks work, ready merges (more for each
project they hold), deadlines that come soon, then the rest; within one
cost the longest wait first. Sample data only.
"""

from __future__ import annotations

from typing import Any

import pytest

from omnigent.entities.factory import FactoryIssue
from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.factory.asks import AskInputs, AsksResponse, build_asks, cost_of_waiting

FID = "f4f4ba9c77725331b12f0fa7072dc4d3"
ROOT = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"
NOW = 1_793_600_000
HOUR = 3600
ME = "owner@example.com"


def _decision(number: int, age_h: float, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"d{number}",
        "ref": f"D-{number}",
        "status": "open",
        "source": "agent",
        "blocking": False,
        "question": f"Question {number}? (sample)",
        "options": [{"id": "ok", "label": "OK", "recommended": True}],
        "asked_by_session_id": ROOT,
        "asked_by_title": "Lead A · Atlas",
        "asked_by_role": "lead",
        "assignee": None,
        "claimed_by": None,
        "created_at": int(NOW - age_h * HOUR),
        **extra,
    }


def _pr(number: int, slug: str, age_h: float) -> dict[str, Any]:
    return {
        "id": f"{number:032x}",
        "number": number,
        "project_slug": slug,
        "title": f"Change {number} (sample)",
        "state": "open",
        "ready": True,
        "ready_since": int(NOW - age_h * HOUR),
    }


def _snapshot(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "factory": {"id": FID, "name": "Sample factory (sample)", "root_session_id": ROOT},
        "projects": [],
        "prs": [],
        "decisions": [],
        "members": [],
    }
    body.update(extra)
    return body


def _order(body: dict[str, Any], **inputs: Any) -> list[str]:
    built = build_asks(AskInputs(snapshot=body, user_id=ME, level=LEVEL_OWNER, **inputs), now=NOW)
    AsksResponse.model_validate(built)
    return [ask["id"] for ask in built["asks"]]


def test_broken_first_then_blocking_then_merges_then_the_rest() -> None:
    body = _snapshot(
        decisions=[
            _decision(1, 30),  # the oldest, but nothing waits on it
            _decision(2, 1, blocking=True),
            _decision(3, 2, source="relaunch_failed", question='"PL · export" relaunch failed'),
        ],
        prs=[_pr(10, "search", 5), _pr(11, "export", 4)],
    )
    incidents = [
        FactoryIssue(
            id="i1",
            factory_id=FID,
            class_="incident",
            number=1,
            rule="member.dead",
            target="member:b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1",
            summary="Sentinel stopped (sample)",
            severity="critical",
            first_seen=NOW - 600,
        )
    ]

    assert _order(body, incidents=incidents) == ["INC-1", "D-3", "D-2", "merge", "D-1"]


def test_within_one_cost_the_longest_wait_comes_first() -> None:
    body = _snapshot(decisions=[_decision(1, 2), _decision(2, 9), _decision(3, 5)])

    assert _order(body) == ["D-2", "D-3", "D-1"]


def test_a_deadline_that_comes_soon_raises_the_cost() -> None:
    soon = _decision(1, 1, deadline_at=NOW + 30 * 60, deadline_effect="expires (sample)")
    later = _decision(2, 9, deadline_at=NOW + 20 * HOUR, deadline_effect="expires (sample)")
    body = _snapshot(decisions=[later, soon], prs=[_pr(10, "search", 3)])

    built = build_asks(AskInputs(snapshot=body, user_id=ME, level=LEVEL_OWNER), now=NOW)

    assert [a["id"] for a in built["asks"]] == ["D-1", "merge", "D-2"]
    first = built["asks"][0]["cost_of_waiting"]
    assert first == {"score": 60, "level": "medium", "text": "expires (sample)", "holds": 0}


def test_a_merge_costs_more_for_each_project_it_holds() -> None:
    one = _snapshot(prs=[_pr(10, "a", 1)])
    many = _snapshot(prs=[_pr(10 + i, f"p{i}", 1) for i in range(9)])

    def score(body: dict[str, Any]) -> int:
        built = build_asks(AskInputs(snapshot=body, user_id=ME, level=LEVEL_OWNER), now=NOW)
        return built["asks"][0]["cost_of_waiting"]["score"]

    assert score(one) == 55
    assert score(many) == 80  # 6 projects at most count


@pytest.mark.parametrize(
    ("kind", "extra", "score", "level"),
    [
        ("broken", {"critical": True}, 100, "high"),
        ("broken", {}, 90, "high"),
        ("decision", {"blocking": True}, 70, "high"),
        ("bet", {}, 45, "medium"),
        ("source", {}, 35, "low"),
        ("decision", {}, 30, "low"),
        ("decision", {"deadline_at": NOW + 5 * HOUR}, 45, "medium"),
        ("proposal", {"deadline_at": NOW + 23 * HOUR}, 35, "low"),
        ("proposal", {"deadline_at": NOW + 30 * HOUR}, 30, "low"),
    ],
)
def test_cost_of_waiting_follows_fixed_rules(
    kind: str, extra: dict[str, Any], score: int, level: str
) -> None:
    cost = cost_of_waiting(kind, now=NOW, text="(sample)", **extra)

    assert (cost.score, cost.level) == (score, level)
