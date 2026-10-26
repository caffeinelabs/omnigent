"""K3: a ready preview no human looked at for the TTL gets one park notice per window."""

from __future__ import annotations

from typing import Any

import pytest

from omnigent import factory as F
from omnigent.server.factory import previews
from omnigent.server.factory.supervisor import (
    PREVIEW_TTL_CHECK_S,
    FactorySupervisor,
    SupervisorConfig,
)
from tests.server.factory.conftest import Supervision

TTL_S = 72 * 3600
NOTICE = (
    "[Factory notice · preview p9630 idle 72h · park it] No human has looked at the preview "
    "of PR caffeinelabs/app#9630 for 72h. Send env_keeper PREVIEW_DOWN pr=9630 reason=ttl. "
    "The demo stays reviewable; the cockpit can redeploy the preview later."
)


@pytest.fixture(autouse=True)
def _quiet(supervision: Supervision) -> None:
    for key in ("lead_a", "pl"):
        supervision.world.store.set_labels(supervision.world.id(key), {F.LABEL_HEARTBEAT_S: "0"})


def _store(s: Supervision) -> Any:
    return s.world.app_state.factory.factory_store


def _ready_preview(s: Supervision, deployed_at: float, **fields: Any) -> Any:
    pr, _ = _store(s).upsert_pr(
        s.world.id("lead_a"),
        "caffeinelabs/app",
        9630,
        project_slug="api-version",
        preview={"name": "p9630", "state": "ready", "deployed": {"at": int(deployed_at)}},
        **fields,
    )
    return pr


async def test_an_idle_ready_preview_gets_one_notice_per_window(supervision: Supervision) -> None:
    s, clock = supervision, supervision.clock
    pl = s.world.id("pl")
    _ready_preview(s, clock.now)

    clock.advance(TTL_S - 60)
    await s.tick()
    assert s.deliver.texts_to(pl) == []

    clock.advance(PREVIEW_TTL_CHECK_S)
    await s.tick()
    assert s.deliver.texts_to(pl) == [NOTICE]

    # Later checks in the same window stay quiet, also after a server roll.
    clock.advance(PREVIEW_TTL_CHECK_S)
    await s.tick()
    restarted = FactorySupervisor(
        s.world.app_state, config=SupervisorConfig(), deliver=s.deliver, clock=clock
    )
    await restarted.tick()
    await s.settle()
    assert s.deliver.texts_to(pl) == [NOTICE]

    # Still idle a whole window later: one more notice.
    clock.advance(TTL_S)
    await s.tick()
    assert s.deliver.texts_to(pl) == [NOTICE, NOTICE]

    [first, _] = _store(s).find_events(s.world.id("lead_a"), previews.TTL_EVENT_KIND, 0)
    assert first.to_id == pl and first.subject == "pr:caffeinelabs/app#9630"
    assert first.severity == "notice" and first.project_slug == "api-version"


async def test_human_attention_restarts_the_window(supervision: Supervision) -> None:
    s, clock = supervision, supervision.clock
    deployed = clock.now
    reviewed = deployed + TTL_S / 2
    _ready_preview(
        s,
        deployed,
        human_review={"verdict": "approved", "by": "luka", "at": int(reviewed)},
    )

    clock.advance(TTL_S + PREVIEW_TTL_CHECK_S)
    await s.tick()
    assert s.deliver.texts_to(s.world.id("pl")) == []

    clock.now = reviewed + TTL_S + 1
    await s.tick()
    assert len(s.deliver.texts_to(s.world.id("pl"))) == 1


async def test_an_answered_project_decision_counts_as_attention(
    supervision: Supervision,
) -> None:
    s, clock = supervision, supervision.clock
    # Deployed a whole TTL ago, but the human just answered a question about the project.
    _ready_preview(s, clock.now - TTL_S)
    decision, _ = _store(s).create_decision(
        s.world.id("lead_a"),
        dedupe_key="k",
        question="Header or footer?",
        project_slug="api-version",
    )
    _store(s).answer_decision(decision.id, option_id=None, text="header", by="luka")

    clock.advance(PREVIEW_TTL_CHECK_S)
    await s.tick()

    assert s.deliver.texts_to(s.world.id("pl")) == []


@pytest.mark.parametrize("state", ["stale", "parked", "deploying"])
async def test_only_ready_previews_are_parked(supervision: Supervision, state: str) -> None:
    s, clock = supervision, supervision.clock
    pr = _ready_preview(s, clock.now)
    _store(s).update_pr(pr.id, preview={**pr.preview, "state": state})

    clock.advance(TTL_S + PREVIEW_TTL_CHECK_S)
    await s.tick()

    assert s.deliver.texts_to(s.world.id("pl")) == []


async def test_parking_is_off_without_a_ttl(supervision: Supervision) -> None:
    s, clock = supervision, supervision.clock
    _ready_preview(s, clock.now)
    off = FactorySupervisor(
        s.world.app_state,
        config=SupervisorConfig(preview_ttl_h=None),
        deliver=s.deliver,
        clock=clock,
    )

    clock.advance(10 * TTL_S)
    await off.tick()
    await s.settle()

    assert s.deliver.texts_to(s.world.id("pl")) == []


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, 72.0), ("24", 24.0), ("0", None), ("soon", 72.0)]
)
def test_ttl_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: float | None
) -> None:
    if raw is None:
        monkeypatch.delenv(previews.PREVIEW_TTL_ENV, raising=False)
    else:
        monkeypatch.setenv(previews.PREVIEW_TTL_ENV, raw)

    assert previews.preview_ttl_h() == expected
    assert SupervisorConfig.from_env().preview_ttl_h == expected
