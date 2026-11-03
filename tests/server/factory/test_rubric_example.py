"""The rank formula is code, its numbers are rubric fields: ``mechanism.md`` §6.2's example.

O-12 (sample): 23 distinct accounts in 90 days from 2 source types (repo
issues 14, web 9), all "say", plus 1 team hunch; most signals describe a
workaround; under G-1, the only goal, in the focus segment.

reach 23 × 25 = 575 a month of 1,000 → reach_s 0.76; intensity 0.6; fit 1.0;
confidence 0.5 (moderate): rank 0.76 × 0.6 × 1.0 × 0.5 = 0.23 → Now.
"""

from __future__ import annotations

import itertools

from omnigent.entities.factory_discovery import FactoryOpportunity, FactorySignal
from omnigent.server.factory.discovery import rubric
from omnigent.server.factory.discovery.evidence import Linked, measure

NOW = 1_800_000_000
DAY = 86400
STRATEGY = {
    "goals": [{"id": "G-1", "statement": "More new users publish in week one (sample)"}],
    "focus_segments": ["new_user"],
    "non_goals": ["Enterprise single sign-on (sample)"],
}
_numbers = itertools.count(1)


def sig(
    source: str, kind: str, *, intensity: str | None = "workaround", signal_kind: str = "request"
) -> FactorySignal:
    number = next(_numbers)
    return FactorySignal(
        factory_id="f" * 32,
        number=number,
        source=source,
        source_kind=kind,
        kind=signal_kind,
        excerpt=f"sample {number}",
        observed_at=NOW - 2 * DAY,
        ingested_at=NOW,
        account=f"acct_{number}",
        segments=["new_user"],
        intensity=intensity,
    )


def example() -> tuple[FactoryOpportunity, list[Linked]]:
    signals = [sig("repo-issues", "repo") for _ in range(14)]
    signals += [sig("web", "web") for _ in range(9)]
    signals[0] = sig("repo-issues", "repo", intensity="blocker")
    signals[1] = sig("repo-issues", "repo", intensity="annoyance")
    hunch = sig("team", "team", intensity=None, signal_kind="hunch")
    row = FactoryOpportunity(
        factory_id="f" * 32,
        number=12,
        title="Show a draft before it goes live (sample)",
        statement="When I finish a draft, I want to show it to someone before it goes live, "
        "so I can get feedback without risk (sample)",
        created_by="scout",
        goal="G-1",
        segments=["new_user"],
    )
    return row, [Linked(s) for s in [*signals, hunch]]


def test_the_worked_example_ranks_023_in_now() -> None:
    row, items = example()
    meter = measure(items, None, now=NOW)
    score = rubric.score(row, meter, items, strategy=STRATEGY, rubric=None, now=NOW)

    assert (meter.level, meter.accounts, meter.hunches) == ("moderate", 23, 1)
    parts = {p.name: round(p.value, 2) for p in score.parts}
    assert parts == {"reach": 0.76, "intensity": 0.6, "fit": 1.0, "confidence": 0.5}
    assert round(score.rank, 2) == 0.23
    assert score.band == "now"
    view = score.to_dict()
    assert view["line"] == "reach 0.76 × intensity 0.60 × fit 1.00 × confidence 0.50 = 0.23"
    reach = next(p for p in view["parts"] if p["name"] == "reach")
    assert reach["input"] == "23 accts × 25 = 575/mo of 1000"
    intensity = next(p for p in view["parts"] if p["name"] == "intensity")
    assert intensity["input"] == "most say workaround"


def test_every_number_is_a_rubric_field() -> None:
    row, items = example()
    meter = measure(items, None, now=NOW)
    tuned = {"reach_ref": 4000, "bands": {"now": 0.3, "next": 0.1}}

    score = rubric.score(row, meter, items, strategy=STRATEGY, rubric=tuned, now=NOW)

    # sqrt(575 / 4000) = 0.38 → 0.38 × 0.6 × 1 × 0.5 = 0.11 → Next.
    assert round(score.part("reach"), 2) == 0.38
    assert round(score.rank, 2) == 0.11
    assert score.band == "next"


def test_fit_halves_outside_the_focus_and_is_zero_under_a_non_goal() -> None:
    row, items = example()
    meter = measure(items, None, now=NOW)
    outside = FactoryOpportunity(**{**row.to_dict(), "segments": ["teams"]})
    parked = FactoryOpportunity(
        **{**row.to_dict(), "non_goal": "Enterprise single sign-on (sample)"}
    )
    unassigned = FactoryOpportunity(**{**row.to_dict(), "goal": None})

    def fit(opp: FactoryOpportunity) -> float:
        return rubric.score(opp, meter, items, strategy=STRATEGY, rubric=None, now=NOW).part("fit")

    assert fit(outside) == 0.5
    assert fit(parked) == 0.0
    assert fit(unassigned) == 0.5
    assert rubric.score(row, meter, items, strategy={}, rubric=None, now=NOW).part("fit") == 1.0


def test_goal_weights_scale_fit_against_the_heaviest_goal() -> None:
    row, items = example()
    meter = measure(items, None, now=NOW)
    strategy = {
        **STRATEGY,
        "goals": [
            {"id": "G-1", "statement": "a (sample)", "weight": 0.5},
            {"id": "G-2", "statement": "b (sample)", "weight": 2.0},
        ],
    }

    score = rubric.score(row, meter, items, strategy=strategy, rubric=None, now=NOW)

    assert score.part("fit") == 0.25


def test_unlabelled_signals_count_as_nice_to_have() -> None:
    row, items = example()
    bare = [Linked(FactorySignal(**{**i.signal.to_dict(), "intensity": None})) for i in items]
    meter = measure(bare, None, now=NOW)

    score = rubric.score(row, meter, bare, strategy=STRATEGY, rubric=None, now=NOW)

    assert score.part("intensity") == 0.1
    assert next(p for p in score.parts if p.name == "intensity").input == "no signal labelled yet"


def test_value_and_confidence_place_it_on_the_map() -> None:
    assert rubric.quadrant_of(0.5, 0.8, None) == "bet"
    assert rubric.quadrant_of(0.5, 0.25, None) == "test_first"
    assert rubric.quadrant_of(0.1, 0.8, None) == "quick_win"
    assert rubric.quadrant_of(0.1, 0.1, None) == "wait"
    assert rubric.band_of(0.199, None) == "now"  # shown as 0.20, so it reads as Now
    assert rubric.band_of(0.09, None) == "later"
