"""How a done review reads: phases, the one line, the row's phrase. Pure."""

from __future__ import annotations

from typing import Any

from omnigent.server.factory.done_review_view import (
    PHRASE_FOLLOW_UPS,
    PHRASE_IN_REVIEW,
    is_current,
    lines,
    node_phrase,
    review_state,
)


def _plan(*reviews: dict[str, Any], history: list[dict[str, Any]] | None = None) -> dict:
    return {
        "history": history or [{"wave": 1, "proposal": "C-1", "added": ["a"], "at": 100}],
        "reviews": list(reviews),
    }


def _review(n: int = 1, **fields: Any) -> dict[str, Any]:
    return {
        "n": n,
        "wave": 1,
        "trigger": "wave_done",
        "status": "returned",
        "requested_at": 200,
        "requested_of_title": "Sentinel",
        "rounds_max": 3,
        **fields,
    }


def _gap(n: int, status: str, **fields: Any) -> dict[str, Any]:
    return {
        "n": n,
        "line": f"gap {n}",
        "severity": "high",
        "kind": "project",
        "status": status,
        **fields,
    }


def test_no_review_says_nothing() -> None:
    state = review_state(_plan())

    assert (state.phase, state.line, state.view()) == ("none", None, None)
    assert node_phrase(_plan(), built=True) == PHRASE_IN_REVIEW
    assert node_phrase(_plan(), built=False) is None


def test_in_progress_names_the_reviewer() -> None:
    state = review_state(_plan(_review(status="requested")))

    assert state.phase == "in_review"
    assert state.line == "Review: in progress (Sentinel)"


def test_follow_ups_running_counts_what_started_what_needs_you_and_the_next_wave() -> None:
    plan = _plan(
        _review(
            verdict="not_done",
            gaps=[
                _gap(1, "started"),
                _gap(2, "started"),
                _gap(3, "asked"),
                _gap(4, "next_wave", wave=2),
                _gap(5, "noted"),
                _gap(6, "dropped"),
            ],
        )
    )

    state = review_state(plan)

    assert state.phase == "follow_ups" and not state.passed
    assert state.line == "Review: not done · 2 follow-ups started · 1 needs you · rest in wave 2"
    assert state.dropped == 1
    assert node_phrase(plan, built=True) == PHRASE_FOLLOW_UPS
    assert [g["live"] for g in state.view()["gaps"]] == [True, True, True, True, False, False]


def test_a_passing_review_reads_done_and_a_stale_one_reads_nothing() -> None:
    passed = _plan(_review(verdict="done"))
    later = _plan(
        _review(verdict="done"),
        history=[
            {"wave": 1, "proposal": "C-1", "added": ["a"], "at": 100},
            {"wave": 1, "proposal": "C-9", "added": ["b"], "at": 300},
        ],
    )

    assert review_state(passed).line == "Review: done" and review_state(passed).passed
    assert not is_current(later, later["reviews"][0])
    assert review_state(later).phase == "none"


def test_its_own_follow_ups_never_make_a_review_stale() -> None:
    review = _review(verdict="not_done", gaps=[_gap(1, "started", refs=["C-9", "fix-a"])])
    plan = _plan(
        review,
        history=[
            {"wave": 1, "proposal": "C-1", "added": ["a"], "at": 100},
            {"wave": 1, "proposal": "C-9", "added": ["fix-a"], "at": 300},
        ],
    )

    assert is_current(plan, review)
    assert review_state(plan).phase == "follow_ups"


def test_done_with_follow_ups_passes_once_they_finish() -> None:
    running = _plan(_review(verdict="done_with_follow_ups", gaps=[_gap(1, "started")]))
    finished = _plan(_review(verdict="done_with_follow_ups", gaps=[_gap(1, "finished")]))

    assert review_state(running).phase == "follow_ups"
    assert review_state(finished).passed
    assert review_state(finished).line == "Review: done with follow-ups · follow-ups finished"


def test_not_done_with_nothing_left_is_reviewed_again_until_the_rounds_are_spent() -> None:
    once = _plan(_review(verdict="not_done", gaps=[_gap(1, "finished")]))
    spent = _plan(*(_review(n, verdict="not_done", gaps=[_gap(1, "finished")]) for n in (1, 2, 3)))

    assert review_state(once).phase == "settled"
    assert review_state(once).line == "Review: not done · follow-ups finished · reviewing again"
    assert review_state(spent).line == "Review: not done after 3 rounds · your call"


def test_a_person_who_says_it_is_done_wins() -> None:
    plan = _plan(
        _review(
            verdict="not_done",
            gaps=[_gap(1, "started")],
            direction={"said": "done", "name": "ana (sample)"},
        )
    )

    state = review_state(plan)

    assert state.passed and state.line == "Review: done (ana (sample) said so)"
    assert node_phrase(plan, built=True) is None


def test_the_history_reads_newest_first() -> None:
    reviews = [_review(1, verdict="not_done"), _review(2, verdict="done", by_title="the server")]

    assert lines(reviews) == [
        "Review 2 (wave 1, the server): done",
        "Review 1 (wave 1, Sentinel): not done",
    ]
