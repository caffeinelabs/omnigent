"""The evidence meter: computed by the server from attached signals, never typed by an agent.

Loud users count once (the per-account cap), evidence ages (the half-life),
counter-evidence lowers the level, and simulations, suspected injections,
votes and pins never count. Every signal below is sample data.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from omnigent.entities.factory_discovery import FactorySignal
from omnigent.server.factory.discovery.evidence import Linked, measure

NOW = 1_800_000_000
DAY = 86400
_numbers = itertools.count(1)


def sig(
    *,
    source: str = "repo-issues",
    source_kind: str = "repo",
    account: str | None = None,
    age_days: float = 1,
    kind: str | None = "request",
    say_do: str = "say",
    flags: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> FactorySignal:
    number = next(_numbers)
    return FactorySignal(
        factory_id="f" * 32,
        number=number,
        source=source,
        source_kind=source_kind,
        kind=kind,
        say_do=say_do,
        excerpt=f"sample words {number}",
        observed_at=NOW - int(age_days * DAY),
        ingested_at=NOW,
        account=account if account is not None else f"acct_{number}",
        flags=flags or [],
        meta=meta or {},
    )


def linked(*signals: FactorySignal, counter: bool = False) -> list[Linked]:
    return [Linked(signal=s, counter=counter) for s in signals]


def many(n: int, **kw: Any) -> list[FactorySignal]:
    return [sig(**kw) for _ in range(n)]


def test_no_signals_is_none_and_only_hunches_is_hunch() -> None:
    assert measure([], None, now=NOW).level == "none"
    assert measure([], None, now=NOW).confidence == 0.0

    meter = measure(linked(sig(kind="hunch", source="team", source_kind="team")), None, now=NOW)

    assert (meter.level, meter.confidence, meter.hunches, meter.accounts) == ("hunch", 0.1, 1, 0)
    assert meter.reason == "1 team hunch, no user evidence yet"


def test_moderate_needs_five_accounts_from_two_source_kinds() -> None:
    repo = many(3)
    web = many(2, source="web", source_kind="web")

    meter = measure(linked(*repo, *web), None, now=NOW)

    assert (meter.level, meter.confidence) == ("moderate", 0.5)
    assert meter.accounts == 5 and meter.source_kinds == ("repo", "web")
    assert meter.by_source == {"repo-issues": 3, "web": 2}
    assert meter.reason.startswith("5 accounts · 2 source types · no behaviour data yet")


def test_one_source_kind_stays_weak_and_says_why() -> None:
    meter = measure(linked(*many(12)), None, now=NOW)

    assert (meter.level, meter.confidence) == ("weak", 0.25)
    assert "moderate needs 2 source types" in meter.reason


def test_a_loud_account_counts_once() -> None:
    loud = many(30, account="acct_loud")
    other = sig(source="web", source_kind="web")

    meter = measure(linked(*loud, other), None, now=NOW)

    assert meter.accounts == 2
    assert meter.level == "weak"
    assert meter.weight == pytest.approx(2.0, abs=0.05)


def test_the_cap_is_a_rubric_field() -> None:
    loud = many(4, account="acct_loud")
    web = sig(source="web", source_kind="web")

    capped = measure(linked(*loud, web), {"per_account_cap": 1}, now=NOW)
    wider = measure(linked(*loud, web), {"per_account_cap": 4}, now=NOW)

    assert capped.level == "weak"
    assert wider.level == "moderate"


def test_evidence_decays_with_the_half_life() -> None:
    fresh = [*many(3), *many(3, source="web", source_kind="web")]
    old = [*many(3, age_days=80), *many(3, age_days=80, source="web", source_kind="web")]

    assert measure(linked(*fresh), None, now=NOW).level == "moderate"
    aged = measure(linked(*old), None, now=NOW)
    # Six accounts 80 days old weigh 6 × 0.5^(80/60) ≈ 2.4: two accounts' worth, not five.
    assert aged.level == "weak"
    assert "(2 now)" in aged.reason
    assert aged.weight == pytest.approx(6 * 0.5 ** (80 / 60), abs=0.01)
    # A longer half-life keeps them moderate.
    assert measure(linked(*old), {"decay_half_life_days": 600}, now=NOW).level == "moderate"


def test_evidence_outside_the_window_does_not_count() -> None:
    old = [*many(3, age_days=120), *many(3, age_days=120, source="web", source_kind="web")]

    meter = measure(linked(*old), None, now=NOW)

    assert meter.level == "none" and meter.accounts == 0


def test_counter_evidence_lowers_the_level_by_one() -> None:
    support = [*many(4), *many(3, source="web", source_kind="web")]

    clean = measure(linked(*support), None, now=NOW)
    against = measure(linked(*support) + linked(*many(3), counter=True), None, now=NOW)
    little = measure(linked(*support) + linked(*many(1), counter=True), None, now=NOW)

    assert clean.level == "moderate"
    assert against.level == "weak" and against.counter == 3
    assert "counter-evidence" in against.reason
    assert little.level == "moderate"


def test_do_evidence_makes_moderate_say_evidence_strong() -> None:
    say = [*many(3), *many(3, source="web", source_kind="web")]
    behaviour = sig(
        source="analytics", source_kind="analytics", kind="behavior", say_do="do", account=None
    )

    meter = measure(linked(*say, behaviour), None, now=NOW)

    assert (meter.level, meter.confidence) == ("strong", 0.8)
    assert meter.do == 1


def test_a_won_readout_proves_it_and_a_lost_one_drops_it_to_weak() -> None:
    say = [*many(3), *many(3, source="web", source_kind="web")]
    won = sig(
        source="factory",
        source_kind="factory",
        kind="test_result",
        say_do="do",
        meta={"verdict": "won"},
    )
    lost = sig(
        source="factory",
        source_kind="factory",
        kind="test_result",
        say_do="do",
        meta={"verdict": "lost"},
    )

    assert measure(linked(*say, won), None, now=NOW).level == "proven"
    assert measure(linked(*say, lost), None, now=NOW).level == "weak"


def test_simulations_and_suspected_injections_never_count() -> None:
    simulated = many(6, kind="simulation")
    injected = many(6, flags=["injection_suspect"], source="web", source_kind="web")

    meter = measure(linked(*simulated, *injected), None, now=NOW)

    assert meter.level == "none"
    assert meter.excluded == 12 and meter.accounts == 0


def test_a_newest_signal_two_half_lives_old_is_stale() -> None:
    meter = measure(linked(sig(age_days=130)), {"window_days": 365}, now=NOW)
    fresh = measure(linked(sig(age_days=3)), None, now=NOW)

    assert meter.stale is True
    assert fresh.stale is False


def test_the_ladder_is_a_rubric_field() -> None:
    support = [*many(3), *many(3, source="web", source_kind="web")]

    meter = measure(linked(*support), {"ladder": {"moderate": 0.6}}, now=NOW)

    assert (meter.level, meter.confidence) == ("moderate", 0.6)
