"""The metric catalog on a seeded ledger: every metric computes, with n, grouped and filtered.

The seeded factory has two projects over the last week: merged, closed and
ready PRs, answered decisions, tasks, loops (converged and exhausted), an
incident, feedback, a cost ledger, the event log (heartbeats, NEXT wakes,
dispatches, mail, claims, controls), Room messages and spans.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from omnigent.entities.factory import EventDraft
from omnigent.server.factory.telemetry import metrics
from tests.server.factory.conftest import FactoryWorld

H = 3600
D = 86400
REPO = "caffeinelabs/app"


def _event(store: Any, fid: str, kind: str, ts: float, **kw: Any) -> None:
    store.append_event(
        EventDraft(factory_id=fid, kind=kind, summary=kind, ts=int(ts * 1000), **kw)
    )


def _span(
    store: Any, fid: str, kind: str, name: str, start: float, end: float | None, **kw: Any
) -> None:
    store.insert_span(
        fid,
        kind=kind,
        name=name,
        trace_key=f"{kind}:{name}:{start}",
        started_at=int(start * 1000),
        ended_at=int(end * 1000) if end is not None else None,
        **kw,
    )


@pytest.fixture()
def seeded(world: FactoryWorld) -> tuple[FactoryWorld, float]:
    store, fid, now = world.deps.factory_store, world.id("lead_a"), time.time()
    metrics.reset_cache()
    store.upsert_project(fid, "api-version", title="API", repo=REPO)
    store.upsert_project(fid, "docs", title="Docs", repo=REPO, stage="ready")
    api = store.get_project(fid, "api-version")
    assert api is not None
    created = int(api.created_at)
    pr1, _ = store.upsert_pr(
        fid,
        REPO,
        1,
        state="merged",
        project_slug="api-version",
        opened_at=created + H,
        first_ready_at=created + 2 * H,
        merged_at=created + 3 * H,
    )
    store.upsert_pr(
        fid, REPO, 2, state="closed", project_slug="api-version", closed_at=int(now - 2 * H)
    )
    store.upsert_pr(
        fid,
        REPO,
        3,
        state="merged",
        project_slug="docs",
        opened_at=int(now - 5 * H),
        first_ready_at=int(now - 4 * H),
        merged_at=int(now - 3 * H),
        sentinel={"verdict": "verified", "at": int(now - 4 * H)},
    )
    pr4, _ = store.upsert_pr(
        fid,
        REPO,
        4,
        state="open",
        project_slug="docs",
        first_ready_at=int(now - 2 * H),
        preview={"state": "ready"},
    )
    _event(
        store,
        fid,
        "pr.human_review",
        now - H,
        subject=f"pr:{REPO}#4",
        data={"verdict": "changes_requested", "pr_id": pr4.id, "by": "alice", "by_kind": "human"},
    )
    task, _ = store.upsert_task(
        fid,
        "api-version",
        "route",
        started_at=created,
        pr_id=pr1.id,
        estimate_min=60,
        state="done",
        closed_at=created + 2 * H,
    )
    blocking, _ = store.create_decision(
        fid,
        dedupe_key="d1",
        question="Which name?",
        source="agent",
        options=[{"id": "a", "label": "A", "recommended": True}],
    )
    store.answer_decision(blocking.id, option_id="a", text=None, by="alice")
    store.create_decision(fid, dedupe_key="d2", question="Still open?", source="agent")
    store.upsert_loop(
        fid,
        "implement",
        "task:1",
        state="converged",
        iteration=2,
        project_slug="api-version",
        ended_at=int(now - D),
    )
    store.upsert_loop(
        fid,
        "review",
        "pr:2",
        state="exhausted",
        iteration=3,
        project_slug="api-version",
        spent={"usd": 4.0},
    )
    incident, _ = store.upsert_issue(
        fid,
        class_="incident",
        rule="member.dead",
        target="member:x",
        summary="dead",
        project_slug="api-version",
    )
    store.update_issue(incident.id, status="resolved", resolved_at=incident.first_seen + 600)
    store.add_feedback(
        fid,
        target_kind="pr",
        target_id=pr1.id,
        author_kind="human",
        author="alice",
        kind="rating",
        rating=4,
        project_slug="api-version",
    )
    store.add_feedback(
        fid,
        target_kind="pr",
        target_id="pr2",
        author_kind="human",
        author="alice",
        kind="closure",
        tags=["wrong_scope"],
    )
    store.add_feedback(
        fid,
        target_kind="demo",
        target_id="demo1",
        author_kind="human",
        author="bob",
        kind="useful",
        rating=1,
    )
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    store.add_cost_daily(
        fid,
        project_slug="api-version",
        role="ic",
        agent_name="ic",
        model="sonnet",
        day_utc=day,
        cost_usd=10.0,
        turns=4,
    )
    store.add_cost_daily(
        fid,
        project_slug="",
        role="lead",
        agent_name="Atlas",
        model="opus",
        day_utc=day,
        cost_usd=2.0,
        turns=10,
    )
    pl = world.id("pl")
    for i in range(3):
        _event(store, fid, "heartbeat.delivered", now - (i + 1) * H, to_id=pl)
    _event(store, fid, "engine.next", now - H, to_id=pl)
    _event(store, fid, "dispatch", now - 3 * H, subject="session:a", data={"agent": "ic"})
    _event(store, fid, "dispatch", now - 3 * H, subject="session:b", data={"agent": "ic"})
    _event(store, fid, "task.failed", now - 2 * H, subject="session:b")
    _event(store, fid, "message.queued", now - 2 * H, data={"mail_id": "mail_1"})
    _event(store, fid, "message.delivered", now - 2 * H + 30, data={"mail_id": "mail_1"})
    _event(store, fid, "message.queued", now - 2 * H, data={"mail_id": "mail_2"})
    _event(store, fid, "message.bounced", now - H, data={"mail_id": "mail_2"})
    _event(
        store,
        fid,
        "pr.claim",
        now - 4 * H,
        from_id=pl,
        project_slug="docs",
        data={"claim": "ready_for_human_merge", "claim_mismatch": True},
    )
    _event(
        store,
        fid,
        "pr.claim",
        now - 3 * H,
        from_id=pl,
        project_slug="docs",
        data={"claim": "ready_for_human_merge", "claim_mismatch": False},
    )
    _event(
        store,
        fid,
        "loop.budget_raised",
        now - H,
        project_slug="api-version",
        data={"kind": "review"},
    )
    _event(
        store,
        fid,
        "control.pause",
        now - H,
        project_slug="docs",
        data={"by": "alice", "by_kind": "human"},
    )
    _event(store, fid, "preview.sha_mismatch", now - H, project_slug="docs")
    _event(
        store,
        fid,
        metrics.PROMPT_SCAN_KIND,
        now - D,
        subject="prompts:x",
        data={
            "day": time.strftime("%Y-%m-%d", time.gmtime(now - D)),
            "counts": [
                {"role": "lead", "person": "alice", "project": None, "n": 5},
                {"role": "project_lead", "person": "bob", "project": "docs", "n": 3},
            ],
        },
    )
    store.append_chat(
        fid,
        thread="factory",
        author_kind="human",
        author="alice",
        body="status?",
        created_at=int(now - H),
    )
    store.append_chat(
        fid,
        thread="factory",
        author_kind="agent",
        author=world.id("lead_a"),
        body="All green.",
        created_at=int(now - H + 40),
    )
    _span(store, fid, "stage", "building", now - 5 * H, now - 4 * H, project_slug="docs")
    _span(
        store,
        fid,
        "gate",
        "ci",
        now - 4 * H,
        now - 4 * H + 60,
        attrs={"gate": "ci", "first_pass": True},
        pr_id=pr4.id,
    )
    _span(
        store,
        fid,
        "gate",
        "review",
        now - 4 * H,
        now - 3 * H,
        attrs={"gate": "review", "first_pass": False},
        pr_id=pr4.id,
        outcome="fail",
    )
    _span(
        store,
        fid,
        "task",
        "route",
        now - 6 * H,
        now - 2 * H,
        project_slug="api-version",
        task_id=task.id,
    )
    _span(store, fid, "task", "docs", now - 5 * H, now - 3 * H, project_slug="docs")
    _span(store, fid, "env", "ready", now - 6 * H, now - 3 * H, project_slug="docs")
    _span(store, fid, "env", "degraded", now - 3 * H, now - 2 * H, project_slug="docs")
    return world, now


def test_every_metric_computes_with_n(seeded: tuple[FactoryWorld, float]) -> None:
    world, now = seeded
    window = metrics.parse_window("7d", now + 4 * H)

    values = metrics.compute(world.deps, world.id("lead_a"), window=window, use_cache=False)

    assert set(values) == set(metrics.CATALOG)
    for name, value in values.items():
        assert "error" not in value, name
        assert isinstance(value["n"], int), name
        assert value["definition"] and value["family"], name


def test_metric_values_on_the_seeded_ledger(seeded: tuple[FactoryWorld, float]) -> None:
    world, now = seeded
    v = metrics.compute(
        world.deps,
        world.id("lead_a"),
        window=metrics.parse_window("7d", now + 4 * H),
        use_cache=False,
    )

    assert v["merge_acceptance"]["value"] == pytest.approx(2 / 3, abs=1e-3)
    assert v["merge_acceptance"]["n"] == 3
    assert v["merge_acceptance"]["closure_reasons"] == {"wrong_scope": 1}
    assert v["lead_time"]["n"] == 2 and v["lead_time"]["p50"] is not None
    assert v["escape_rate"]["value"] == pytest.approx(1 / 3, abs=1e-3)
    assert v["sentinel_agreement"] == {**v["sentinel_agreement"], "value": 1.0, "n": 1}
    assert v["claim_mismatch_rate"]["value"] == 0.5 and v["claim_mismatch_rate"]["n"] == 2
    assert v["loop_exhaustion_rate"]["value"] == 0.5
    assert v["loop_exit_mix"]["mix"]["exhausted"]["count"] == 1
    assert v["loop_iterations"]["by"]["implement"]["p50"] == 2
    assert v["wasted_usd"]["value"] == 4.0
    assert v["budget_extensions"]["by"]["review"]["value"] == 1
    assert v["first_pass_yield"]["by"]["ci"]["value"] == 1.0
    assert v["first_pass_yield"]["by"]["review"]["value"] == 0.0
    assert v["incidents"]["value"] == 1 and v["incidents"]["mttr_p50_s"] == 600
    assert v["subagent_failure_rate"]["value"] == 0.5
    assert v["mail_health"]["bounce_rate"] == 0.5 and v["mail_health"]["queue_age_p90_s"] == 30
    assert v["engine_wake_efficiency"]["value"] == 0.25
    assert v["cost_usd"]["value"] == 12.0 and v["cost_usd"]["by_model"] == {
        "opus": 2.0,
        "sonnet": 10.0,
    }
    assert v["cost_per_merged_pr"]["n"] == 2
    assert v["heartbeat_share"]["value"] == pytest.approx(3 / 10)
    assert v["human_ratings"]["value"] == 2.5  # the 4-star PR and the 👍 demo (+1)
    assert v["human_ratings"]["demo_useful"] == 1.0
    assert v["chat_reply_time"]["p50"] == 40
    assert v["human_prompts_per_day"]["total"] == 9  # 1 Room post + 8 scanned
    assert v["human_prompts_per_day"]["band"] == [30, 50]
    assert v["decision_latency"]["n"] == 1
    assert v["unnecessary_questions"]["value"] == 1.0  # recommended option in under 2 min
    assert v["env_availability"]["env_ready_share"] == pytest.approx(0.75)
    assert v["env_availability"]["sha_mismatches"] == 1
    assert v["throughput"]["open_prs"] == 1 and v["throughput"]["live_previews"] == 1
    assert v["parallelism"]["peak"] == 2
    assert v["interventions_per_merged_pr"]["merged"] == 2


def test_group_by_and_filters(seeded: tuple[FactoryWorld, float]) -> None:
    world, now = seeded
    window = metrics.parse_window("7d", now + 4 * H)
    fid = world.id("lead_a")

    grouped = metrics.compute(
        world.deps,
        fid,
        ["merge_acceptance", "cost_usd"],
        window=window,
        group_by="project",
        use_cache=False,
    )
    docs = metrics.compute(
        world.deps,
        fid,
        ["merge_acceptance"],
        window=window,
        filters=metrics.Filters(project="docs"),
        use_cache=False,
    )
    hidden = metrics.compute(
        world.deps,
        fid,
        ["merge_acceptance"],
        window=window,
        filters=metrics.Filters(hidden_projects=frozenset({"docs"})),
        use_cache=False,
    )
    by_person = metrics.compute(
        world.deps,
        fid,
        ["human_prompts_per_day"],
        window=window,
        group_by="person",
        use_cache=False,
    )

    assert set(grouped["merge_acceptance"]["groups"]) == {"api-version", "docs"}
    assert grouped["cost_usd"]["groups"]["api-version"]["value"] == 10.0
    assert docs["merge_acceptance"]["value"] == 1.0 and docs["merge_acceptance"]["n"] == 1
    assert hidden["merge_acceptance"]["n"] == 2
    assert set(by_person["human_prompts_per_day"]["groups"]) == {"alice", "bob"}


def test_extras_follow_the_project_filters(seeded: tuple[FactoryWorld, float]) -> None:
    world, now = seeded
    window = metrics.parse_window("7d", now + 4 * H)
    names = ["throughput", "interventions_per_merged_pr", "merge_acceptance"]

    def run(filters: metrics.Filters) -> dict[str, Any]:
        return metrics.compute(
            world.deps, world.id("lead_a"), names, window=window, filters=filters, use_cache=False
        )

    api = run(metrics.Filters(project="api-version"))
    no_docs = run(metrics.Filters(hidden_projects=frozenset({"docs"})))

    # The open PR, the ready stage and one merged PR are docs'.
    for v in (api, no_docs):
        assert v["throughput"]["open_prs"] == 0
        assert "ready" not in v["throughput"]["projects_by_stage"]
        assert v["interventions_per_merged_pr"]["merged"] == 1
    # The seeded closure names no project: factory-wide, so out of a project's numbers.
    assert api["merge_acceptance"]["closure_reasons"] == {}
    assert no_docs["merge_acceptance"]["closure_reasons"] == {"wrong_scope": 1}


def test_the_previous_window_and_the_cache(seeded: tuple[FactoryWorld, float]) -> None:
    world, now = seeded
    window = metrics.parse_window("24h", now + 1)
    fid = world.id("lead_a")

    first = metrics.compute(world.deps, fid, ["throughput"], window=window, previous=True)
    world.deps.factory_store.upsert_pr(fid, REPO, 5, state="merged", merged_at=int(now))
    cached = metrics.compute(world.deps, fid, ["throughput"], window=window, previous=True)

    assert "previous" in first["throughput"]
    assert cached is first


@pytest.mark.parametrize("raw", ["7x", "0d", "100d", "abc"])
def test_bad_windows(raw: str) -> None:
    with pytest.raises(ValueError):
        metrics.parse_window(raw)


def test_unknown_metric_names_are_refused(world: FactoryWorld) -> None:
    with pytest.raises(KeyError):
        metrics.compute(
            world.deps, world.id("lead_a"), ["nope"], window=metrics.parse_window("1d")
        )


async def test_heartbeat_metrics_survive_the_nightly_prune(world: FactoryWorld) -> None:
    from omnigent.server.factory.background import prune

    store, fid, now = world.deps.factory_store, world.id("lead_a"), time.time()
    metrics.reset_cache()
    for age_days in range(1, 6):
        _event(store, fid, "heartbeat.delivered", now - age_days * D, to_id=world.id("pl"))
    _event(store, fid, "engine.next", now - 2 * D)
    _event(store, fid, "engine.next", now - 4 * D)
    window = metrics.parse_window("7d", now + 1)
    names = ["engine_wake_efficiency", "heartbeat_share"]

    before = metrics.compute(world.deps, fid, names, window=window, use_cache=False)
    await prune.prune_once(world.app_state, now=int(now * 1000))
    after = metrics.compute(world.deps, fid, names, window=window, use_cache=False)

    assert before["engine_wake_efficiency"]["value"] == pytest.approx(2 / 7, abs=1e-3)
    for name in names:
        assert (after[name]["n"], after[name].get("value")) == (
            before[name]["n"],
            before[name].get("value"),
        )


def test_the_prompt_scan_counts_attributed_member_chat(world: FactoryWorld) -> None:
    from omnigent.entities.conversation import MessageData, NewConversationItem
    from omnigent.server.factory.attribution import attributed_content, human_header

    store, fid = world.store, world.id("lead_a")
    day = time.strftime("%Y-%m-%d", time.gmtime())
    content = attributed_content(
        [{"type": "input_text", "text": "status?"}], human_header("bob", "operator")
    )
    store.append(
        world.id("pl"),
        [
            NewConversationItem(
                type="message",
                response_id="r1",
                data=MessageData(role="user", content=content),
                created_by="bob",
            )
        ],
    )

    counts = metrics.scan_human_prompts(world.deps, fid, day)

    assert counts == [{"role": "project_lead", "person": "bob", "project": "api-version", "n": 1}]


def test_the_prompt_scan_counts_human_items_but_not_room_envelopes(world: FactoryWorld) -> None:
    from omnigent.entities.conversation import MessageData, NewConversationItem

    store, fid = world.store, world.id("lead_a")
    day = time.strftime("%Y-%m-%d", time.gmtime())

    def item(text: str, by: str | None) -> NewConversationItem:
        return NewConversationItem(
            type="message",
            response_id="r1",
            data=MessageData(role="user", content=[{"type": "input_text", "text": text}]),
            created_by=by,
        )

    store.append(
        fid,
        [
            item("status?", "alice"),
            item("[Factory chat · factory …] hi", "alice"),
            item("agent text", None),
        ],
    )
    store.append(world.id("pl"), [item("ship it", "bob")])

    counts = metrics.scan_human_prompts(world.deps, fid, day)

    assert counts == [
        {"role": "lead", "person": "alice", "project": None, "n": 1},
        {"role": "project_lead", "person": "bob", "project": "api-version", "n": 1},
    ]
