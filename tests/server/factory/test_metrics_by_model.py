"""Metric samples carry the vendor and model the work ran on, so every metric groups by them.

A worker's task span is stamped with its vendor and model at dispatch (and
again when its cost is attached); a PR's samples take its builder's. The cost
ledger books every turn with its model already.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import EventDraft
from omnigent.server.factory import snapshot
from omnigent.server.factory.telemetry import metrics, spans
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FactoryWorld

H = 3600
REPO = "acme/widgets"
SONNET = "claude-sonnet-4-5-20250929"
CODEX = "gpt-5-codex"


@dataclass
class Seeded:
    """The sample (seeded) factory: builders on two vendors, a reviewer and a demo agent."""

    world: FactoryWorld
    fid: str
    now: float
    sessions: dict[str, str]


def _worker(world: FactoryWorld, key: str, agent: str) -> str:
    return world.make_session(
        key, agent="pl", title=f"{agent}:{key}", parent="pl", sub_agent_name=agent
    ).id


def _stamp(
    store: Any, fid: str, sid: str, role: str, vendor: str, model: str | None, at: float
) -> None:
    spans.write_span(
        store,
        fid,
        kind="task",
        name=f"(sample) {role}",
        dedupe_key=spans.task_key(sid),
        started_at=int(at * 1000),
        project_slug="api-version",
        session_id=sid,
        role=role,
        vendor=vendor,
        model=model,
    )


def _merged_pr(
    store: Any, fid: str, number: int, now: float, workers: list[str], *, first_pass: bool
) -> Any:
    pr, _ = store.upsert_pr(
        fid,
        REPO,
        number,
        state="merged",
        project_slug="api-version",
        opened_at=int(now - 6 * H),
        first_ready_at=int(now - 4 * H),
        merged_at=int(now - 2 * H),
    )
    store.upsert_task(fid, "api-version", f"t{number}", pr_id=pr.id, session_ids=workers)
    store.insert_span(
        fid,
        kind="gate",
        name="review",
        trace_key=f"pr:{pr.id}",
        started_at=int((now - 5 * H) * 1000),
        ended_at=int((now - 5 * H + 60) * 1000),
        pr_id=pr.id,
        project_slug="api-version",
        outcome="ok" if first_pass else "fail",
        attrs={"first_pass": first_pass},
    )
    return pr


def seed(world: FactoryWorld) -> Seeded:
    """Three claude builder PRs (two first-pass), two codex ones (both), ledger cost by model."""
    store, fid, now = world.deps.factory_store, world.id("lead_a"), time.time()
    metrics.reset_cache()
    store.upsert_project(fid, "api-version", title="(sample) API", repo=REPO)
    sessions = {
        "claude": _worker(world, "b1", "ic"),
        "codex": _worker(world, "b2", "ic_codex"),
        "rev": _worker(world, "r1", "reviewer"),
        "demo": _worker(world, "d1", "demo"),
    }
    _stamp(store, fid, sessions["claude"], "ic", "claude", SONNET, now - 7 * H)
    _stamp(store, fid, sessions["codex"], "ic", "codex", CODEX, now - 7 * H)
    _stamp(store, fid, sessions["rev"], "reviewer", "codex", CODEX, now - 7 * H)
    _stamp(store, fid, sessions["demo"], "demo", "claude", "sonnet", now - 7 * H)
    prs = {
        n: _merged_pr(store, fid, n, now, [sessions["claude"], sessions["rev"]], first_pass=ok)
        for n, ok in ((1, True), (2, True), (3, False))
    }
    for n in (4, 5):
        _merged_pr(store, fid, n, now, [sessions["codex"], sessions["demo"]], first_pass=True)
    day = metrics._day(now)
    for agent, role, model, usd in (
        ("ic", "ic", SONNET, 6.0),
        ("ic_codex", "ic", CODEX, 4.0),
        ("reviewer", "reviewer", CODEX, 1.5),
        ("PL · api-version", "project_lead", "claude-opus-4-1", 9.0),
    ):
        store.add_cost_daily(
            fid,
            project_slug="api-version",
            role=role,
            agent_name=agent,
            model=model,
            day_utc=day,
            cost_usd=usd,
        )
    store.append_event(
        EventDraft(
            factory_id=fid,
            kind="pr.human_review",
            summary="(sample) changes requested",
            ts=int((now - 3 * H) * 1000),
            data={"verdict": "changes_requested", "pr_id": prs[1].id},
        )
    )
    return Seeded(world, fid, now, sessions)


@pytest.fixture()
def seeded(world: FactoryWorld) -> Seeded:
    return seed(world)


def _groups(seeded: Seeded, name: str, by: str) -> dict[str, Any]:
    result = metrics.compute(
        seeded.world.deps,
        seeded.fid,
        [name],
        window=metrics.parse_window("7d"),
        group_by=by,
        use_cache=False,
    )
    return result[name]["groups"]


def test_vendor_and_model_are_group_keys() -> None:
    assert {"vendor", "model"} <= set(metrics.GROUP_KEYS)


def test_first_pass_review_groups_by_the_builders_model(seeded: Seeded) -> None:
    groups = _groups(seeded, "first_pass_yield", "model")

    assert groups["sonnet"]["by"]["review"] == {"value": pytest.approx(0.6667, 1e-3), "n": 3}
    assert groups["gpt-5-codex"]["by"]["review"] == {"value": 1.0, "n": 2}


def test_pr_metrics_take_the_builders_vendor(seeded: Seeded) -> None:
    groups = _groups(seeded, "merge_acceptance", "vendor")

    assert groups["claude"]["n"] == 3 and groups["codex"]["n"] == 2


def test_cost_groups_by_the_ledger_model(seeded: Seeded) -> None:
    by_model = _groups(seeded, "cost_usd", "model")
    by_vendor = _groups(seeded, "cost_usd", "vendor")

    assert by_model["sonnet"]["value"] == 6.0
    assert by_model["gpt-5-codex"]["value"] == 5.5
    assert by_model["opus"]["value"] == 9.0
    assert by_vendor["codex"]["value"] == 5.5 and by_vendor["claude"]["value"] == 15.0


def test_unstamped_work_has_no_vendor(world: FactoryWorld) -> None:
    store, fid, now = world.deps.factory_store, world.id("lead_a"), time.time()
    metrics.reset_cache()
    store.upsert_project(fid, "api-version", title="(sample) API", repo=REPO)
    _merged_pr(store, fid, 9, now, [world.id("ic")], first_pass=True)

    facts = metrics.load_facts(world.deps, fid, metrics.parse_window("7d"))
    [sample] = metrics.CATALOG["merge_acceptance"].samples(facts, metrics.parse_window("7d"))

    assert (sample.vendor, sample.model) == (None, None)


# ── Stamping at write time ─────────────────────────────────────


def test_write_span_keeps_the_first_vendor_and_model(world: FactoryWorld) -> None:
    store, fid, sid = world.deps.factory_store, world.id("lead_a"), world.id("ic")
    first = spans.write_span(
        store,
        fid,
        kind="task",
        name="x",
        dedupe_key=spans.task_key(sid),
        started_at=1_000,
        session_id=sid,
        vendor="claude",
    )
    later = spans.write_span(
        store,
        fid,
        kind="task",
        name="x",
        dedupe_key=spans.task_key(sid),
        started_at=1_000,
        session_id=sid,
        vendor="codex",
        model="sonnet",
    )

    assert first.attrs == {"vendor": "claude"}
    assert later.attrs == {"vendor": "claude", "model": "sonnet"}


def test_attaching_task_cost_stamps_the_model(world: FactoryWorld) -> None:
    sid, fid = world.id("ic"), world.id("lead_a")
    world.store.set_session_usage(sid, {"total_cost_usd": 0.5})

    span = spans.attach_task_cost_sync(world.deps, fid, world.get("ic"))

    assert span is not None and span.attrs["vendor"] == "claude"


# ── Through the route ──────────────────────────────────────────


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    snapshot.reset_cache()
    async for value in build_cockpit(world, db_uri, monkeypatch):
        yield value
    snapshot.reset_cache()


async def test_the_metrics_route_groups_by_model(cockpit: Cockpit) -> None:
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/metrics?names=cost_usd&group_by=model",
        headers={"X-Test-User": "alice"},
    )
    bad = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/metrics?names=cost_usd&group_by=planet",
        headers={"X-Test-User": "alice"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["group_by"] == "model"
    assert bad.status_code == 400 and "vendor" in bad.text
