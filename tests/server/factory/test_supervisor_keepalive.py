"""S1 keepalive: a zero-token ``GET /health`` through active members' runner tunnels."""

from __future__ import annotations

import httpx
import pytest

from omnigent import factory as F
from omnigent.server.factory.supervisor import KEEPALIVE_TIMEOUT_S
from tests.server.factory.conftest import Supervision


async def test_pings_active_members_with_a_live_runner(supervision: Supervision) -> None:
    w = supervision.world
    pl = supervision.runners.connect(w.id("pl"))
    lead_a = supervision.runners.connect(w.id("lead_a"))
    # Another factory's lead carries no state label: not active, so not kept alive.
    other = supervision.runners.connect(w.id("other_lead"))
    # Sub-agents ride their root's runner; only top-level members are pinged.
    ic = supervision.runners.connect(w.id("ic"))

    await supervision.tick()

    assert pl.calls == [("/health", KEEPALIVE_TIMEOUT_S)]
    assert lead_a.calls == [("/health", KEEPALIVE_TIMEOUT_S)]
    assert other.calls == []
    assert ic.calls == []
    # Lead B is active but has no live runner: nothing to ping, nothing to wake.
    assert supervision.deliver.calls == []


async def test_interval_is_respected(supervision: Supervision) -> None:
    pl = supervision.runners.connect(supervision.world.id("pl"))

    await supervision.tick()
    supervision.clock.advance(60)
    await supervision.tick()
    supervision.clock.advance(480)
    await supervision.tick()
    assert len(pl.calls) == 1

    supervision.clock.advance(60)  # 600 s since the first keepalive
    await supervision.tick()
    assert len(pl.calls) == 2


async def test_a_runner_that_comes_online_is_pinged_on_the_next_tick(
    supervision: Supervision,
) -> None:
    await supervision.tick()
    pl = supervision.runners.connect(supervision.world.id("pl"))
    supervision.clock.advance(60)

    await supervision.tick()

    assert len(pl.calls) == 1


async def test_paused_and_done_members_are_skipped(supervision: Supervision) -> None:
    w = supervision.world
    pl = supervision.runners.connect(w.id("pl"))
    lead_b = supervision.runners.connect(w.id("lead_b"))
    w.store.set_labels(w.id("pl"), {F.LABEL_STATE: F.STATE_PAUSED})
    w.store.set_labels(w.id("lead_b"), {F.LABEL_STATE: F.STATE_DONE})

    await supervision.tick()
    assert pl.calls == []
    assert lead_b.calls == []

    w.store.set_labels(w.id("pl"), {F.LABEL_STATE: F.STATE_ACTIVE})
    supervision.clock.advance(60)
    await supervision.tick()
    assert pl.calls == [("/health", KEEPALIVE_TIMEOUT_S)]


async def test_a_failing_keepalive_does_not_stop_the_tick(supervision: Supervision) -> None:
    w = supervision.world
    lead_a = supervision.runners.connect(w.id("lead_a"))
    lead_a.error = httpx.ReadTimeout("tunnel stalled")
    pl = supervision.runners.connect(w.id("pl"))

    await supervision.tick()

    assert len(lead_a.calls) == 1
    assert len(pl.calls) == 1
    # A failed attempt still waits a full interval before the next one.
    supervision.clock.advance(60)
    await supervision.tick()
    assert len(lead_a.calls) == 1


# ── K1: a paused or done PL is kept alive while its preview is live ─


def _preview_pr(
    supervision: Supervision,
    *,
    number: int = 9630,
    state: str = "open",
    preview_state: str = "ready",
    project: str = "api-version",
) -> None:
    w = supervision.world
    w.app_state.factory.factory_store.upsert_pr(
        w.id("lead_a"),
        "caffeinelabs/app",
        number,
        project_slug=project,
        state=state,
        preview={"name": f"p{number}", "state": preview_state},
    )


@pytest.mark.parametrize("member_state", [F.STATE_PAUSED, F.STATE_DONE])
async def test_a_paused_or_done_pl_with_a_live_preview_is_kept_alive(
    supervision: Supervision, member_state: str
) -> None:
    w = supervision.world
    pl = supervision.runners.connect(w.id("pl"))
    w.store.set_labels(w.id("pl"), {F.LABEL_STATE: member_state})
    _preview_pr(supervision)

    await supervision.tick()

    assert pl.calls == [("/health", KEEPALIVE_TIMEOUT_S)]
    # It is not beaten, though: only the pod is held.
    supervision.clock.advance(7 * 86400)
    await supervision.tick()
    assert supervision.deliver.texts_to(w.id("pl")) == []


async def test_keep_previews_zero_lets_a_paused_pl_idle_out(supervision: Supervision) -> None:
    w = supervision.world
    pl = supervision.runners.connect(w.id("pl"))
    w.store.set_labels(w.id("pl"), {F.LABEL_STATE: F.STATE_PAUSED, F.LABEL_KEEP_PREVIEWS: "0"})
    _preview_pr(supervision)

    await supervision.tick()

    assert pl.calls == []


@pytest.mark.parametrize(
    ("pr_state", "preview_state", "project"),
    [
        ("merged", "ready", "api-version"),  # merged: the preview is torn down
        ("open", "parked", "api-version"),
        ("open", "down", "api-version"),
        ("open", "ready", "db-version"),  # another project's preview
    ],
)
async def test_no_keepalive_without_a_live_preview_of_an_open_pr(
    supervision: Supervision, pr_state: str, preview_state: str, project: str
) -> None:
    w = supervision.world
    pl = supervision.runners.connect(w.id("pl"))
    w.store.set_labels(w.id("pl"), {F.LABEL_STATE: F.STATE_PAUSED})
    _preview_pr(supervision, state=pr_state, preview_state=preview_state, project=project)

    await supervision.tick()

    assert pl.calls == []


@pytest.mark.parametrize("preview_state", ["deploying", "stale", "starting"])
async def test_deploying_and_stale_previews_also_hold_the_pod(
    supervision: Supervision, preview_state: str
) -> None:
    w = supervision.world
    pl = supervision.runners.connect(w.id("pl"))
    w.store.set_labels(w.id("pl"), {F.LABEL_STATE: F.STATE_PAUSED})
    _preview_pr(supervision, preview_state=preview_state)

    await supervision.tick()

    assert len(pl.calls) == 1
