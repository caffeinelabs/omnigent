"""The factory cost ledger: every turn's cost booked to its factory, project, role and agent.

The hook in ``_record_daily_cost`` sees each turn; a sub-agent's turn goes to
its root PL's project with its own spec role, a lead's turn to project ``""``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from omnigent import factory as F
from omnigent.server.factory.telemetry import cost
from omnigent.server.routes._sessions.helpers import _record_daily_cost
from omnigent.server.routes._sessions.orchestration import _persist_native_cumulative_usage
from tests.server.factory.conftest import FactoryWorld

DAY = "2026-10-26"


@pytest.fixture()
def ledger(world: FactoryWorld) -> Iterator[FactoryWorld]:
    cost.reset()
    uninstall = cost.install(world.deps)
    yield world
    uninstall()
    cost.reset()


def _cells(world: FactoryWorld, key: str = "lead_a") -> list[tuple[Any, ...]]:
    rows = world.deps.factory_store.list_cost_daily(world.id(key))
    return [
        (
            c.project_slug,
            c.role,
            c.agent_name,
            c.model,
            c.day_utc,
            round(c.cost_usd, 6),
            c.tokens_in,
            c.tokens_out,
            c.turns,
        )
        for c in rows
    ]


def test_an_ic_turn_books_to_its_project_and_role(ledger: FactoryWorld) -> None:
    w = ledger
    booked = cost.record(w.get("ic"), 0.25, (1200, 300), "gpt-5", day=DAY)
    assert booked == cost.Attribution(w.id("lead_a"), "api-version", "ic", "ic")
    cost.record(w.get("reviewer"), 0.5, (10, 5), "gpt-5", day=DAY)
    cost.record(w.get("ic"), 0.75, (800, 100), "gpt-5", day=DAY)
    assert _cells(w) == [
        ("api-version", "ic", "ic", "gpt-5", DAY, 1.0, 2000, 400, 2),
        ("api-version", "reviewer", "reviewer", "gpt-5", DAY, 0.5, 10, 5, 1),
    ]


def test_a_lead_turn_books_to_project_empty(ledger: FactoryWorld) -> None:
    w = ledger
    cost.record(w.get("lead_a"), 0.1, (5, 5), "opus", day=DAY)
    cost.record(w.get("lead_b"), 0.2, (5, 5), "opus", day=DAY)
    cost.record(w.get("pl"), 0.3, (5, 5), "opus", day=DAY)
    assert _cells(w) == [
        ("", "lead", "Atlas", "opus", DAY, 0.1, 5, 5, 1),
        ("", "lead", "Lead B · Sentinel", "opus", DAY, 0.2, 5, 5, 1),
        ("api-version", "project_lead", "PL · api-version", "opus", DAY, 0.3, 5, 5, 1),
    ]


def test_sessions_outside_a_factory_book_nothing(ledger: FactoryWorld) -> None:
    w = ledger
    assert cost.record(w.get("plain"), 1.0, (1, 1), "opus", day=DAY) is None
    assert cost.record(w.get("ic"), 0.0, (0, 0), "opus", day=DAY) is None
    assert cost.record(None, 1.0) is None
    cost.record(w.get("other_lead"), 0.4, (1, 1), "opus", day=DAY)
    assert _cells(w) == []
    assert _cells(w, "other_lead") == [
        ("", "lead", "Other factory lead", "opus", DAY, 0.4, 1, 1, 1)
    ]


def test_an_unpriced_turn_books_its_tokens_at_zero_cost(ledger: FactoryWorld) -> None:
    w = ledger
    cost.record(w.get("ic"), 0.0, (500, 50), None, day=DAY)
    assert _cells(w) == [("api-version", "ic", "ic", "", DAY, 0.0, 500, 50, 1)]


def test_attribution_is_cached_for_a_minute(ledger: FactoryWorld) -> None:
    w = ledger
    pl = w.get("pl")
    assert cost.attribution(w.deps, w.get("ic")) is not None
    w.store.set_labels(pl.id, {F.LABEL_PROJECT: "renamed"})
    assert cost.attribution(w.deps, w.get("ic")).project == "api-version"  # type: ignore[union-attr]
    cost.reset()
    assert cost.attribution(w.deps, w.get("ic")).project == "renamed"  # type: ignore[union-attr]


def test_nothing_is_booked_before_install(world: FactoryWorld) -> None:
    cost.reset()
    assert cost.record(world.get("ic"), 1.0, (1, 1), "opus", day=DAY) is None
    assert world.deps.factory_store.list_cost_daily(world.id("lead_a")) == []


def test_the_daily_cost_hook_books_factory_turns(ledger: FactoryWorld) -> None:
    w = ledger
    _record_daily_cost(w.get("ic"), 0.5, w.store, tokens=(100, 10), model="gpt-5")
    _record_daily_cost(w.get("lead_a"), 0.0, w.store)
    (cell,) = w.deps.factory_store.list_cost_daily(w.id("lead_a"))
    assert (cell.project_slug, cell.role, cell.cost_usd, cell.tokens_in) == (
        "api-version",
        "ic",
        0.5,
        100,
    )
    assert cell.day_utc == cost.utc_day()


def test_native_cumulative_usage_books_each_reports_growth(ledger: FactoryWorld) -> None:
    w = ledger
    ic = w.get("ic")
    for total, tokens_in, tokens_out in ((0.4, 1000, 100), (1.0, 2500, 400), (1.0, 2500, 400)):
        _persist_native_cumulative_usage(
            ic.id,
            {
                "cumulative_cost_usd": total,
                "cumulative_input_tokens": tokens_in,
                "cumulative_output_tokens": tokens_out,
                "model": "gpt-5",
            },
            w.store,
        )
    (cell,) = w.deps.factory_store.list_cost_daily(w.id("lead_a"))
    # The repeated report added nothing, so it is not a turn either.
    assert (round(cell.cost_usd, 6), cell.tokens_in, cell.tokens_out, cell.turns) == (
        1.0,
        2500,
        400,
        2,
    )
    assert (cell.project_slug, cell.role, cell.model) == ("api-version", "ic", "gpt-5")


def test_token_growth_counts_cache_tokens_as_input() -> None:
    old = {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 10}
    new = {"input_tokens": 120, "cache_read_input_tokens": 80, "output_tokens": 15}
    assert cost.token_growth(old, new) == (50, 5)
    assert cost.token_growth(new, old) == (0, 0)
