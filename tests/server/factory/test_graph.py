"""The work-graph computations (``server/factory/graph.py``): pure functions over DAG fixtures.

Ready set, waves (layered toposort with IC and preview caps and overlap
serialization), critical path with observed weights, cycle and unknown-dep
validation, scope warnings, and the bus, flow and org aggregations.
"""

from __future__ import annotations

from omnigent.server.factory import graph
from omnigent.server.factory.graph import MailFact, Slots, SpanFact, TaskNode


def _n(key: str, number: int, *deps: str, **kw: object) -> TaskNode:
    return TaskNode(key=key, number=number, deps=tuple(deps), **kw)  # type: ignore[arg-type]


# A diamond: schema → (route, tests) → settings, plus an independent docs task.
DIAMOND = [
    _n("schema", 1, estimate_min=30),
    _n("route", 2, "schema", estimate_min=90),
    _n("tests", 3, "schema", estimate_min=20),
    _n("settings", 4, "route", "tests", estimate_min=40),
    _n("docs", 5, estimate_min=10),
]
WIDE = Slots(ics_free=10, budget_ics=10, previews_free=10, preview_slots=10)


def _state(nodes: list[TaskNode], **states: str) -> list[TaskNode]:
    return [TaskNode(**{**n.__dict__, "state": states.get(n.key, n.state)}) for n in nodes]


# ── Validation ─────────────────────────────────────────────────


def test_acyclic_graph_has_no_cycle() -> None:
    assert graph.find_cycle(DIAMOND) is None


def test_cycle_is_reported_as_a_closed_path() -> None:
    nodes = [_n("a", 1, "c"), _n("b", 2, "a"), _n("c", 3, "b"), _n("d", 4)]
    cycle = graph.find_cycle(nodes)
    assert cycle is not None
    assert cycle[0] == cycle[-1]
    assert set(cycle) == {"a", "b", "c"}


def test_self_dependency_is_a_cycle() -> None:
    assert graph.find_cycle([_n("a", 1, "a")]) == ["a", "a"]


def test_unknown_deps_name_task_project_and_decision() -> None:
    nodes = [
        _n("a", 1, "ghost", "project:api", "project:nope", "decision:D-7", "decision:D-9"),
    ]
    found = graph.unknown_deps(nodes, projects=["api"], decisions=["D-7"])
    assert found == [("a", "ghost"), ("a", "project:nope"), ("a", "decision:D-9")]


# ── Ready set and waves ────────────────────────────────────────


def test_ready_set_is_planned_tasks_with_met_deps() -> None:
    nodes = _state(DIAMOND, schema="done")
    assert graph.ready_set(nodes, {}) == ["route", "tests", "docs"]


def test_ready_set_honours_external_deps_pause_and_activity() -> None:
    nodes = [_n("a", 1, "decision:D-7"), _n("b", 2, "project:api"), _n("c", 3)]
    assert graph.ready_set(nodes, {"decision:D-7": True}) == ["a", "c"]
    assert graph.ready_set(nodes, {}, paused=True) == []
    assert graph.ready_set(nodes, {}, active=False) == []


def test_waves_are_a_layered_toposort() -> None:
    plan = graph.plan_waves(DIAMOND, {}, WIDE)
    assert plan.waves == (("schema", "docs"), ("route", "tests"), ("settings",))
    assert plan.wave_of()["settings"] == 3
    assert not plan.blocked and not plan.serial


def test_running_dependencies_push_dependents_to_wave_two() -> None:
    nodes = _state(DIAMOND, schema="running")
    plan = graph.plan_waves(nodes, {}, WIDE)
    assert plan.waves[0] == ("docs",)
    assert plan.waves[1] == ("route", "tests")


def test_wave_one_is_capped_by_free_ic_slots() -> None:
    nodes = [_n(k, i + 1) for i, k in enumerate("abcde")]
    plan = graph.plan_waves(nodes, {}, Slots(ics_free=2, budget_ics=3, previews_free=2))
    assert plan.waves == (("a", "b"), ("c", "d", "e"))


def test_no_free_ic_slot_leaves_wave_one_empty() -> None:
    nodes = [_n("a", 1), _n("b", 2)]
    plan = graph.plan_waves(nodes, {}, Slots(ics_free=0, budget_ics=1))
    assert plan.now == ()
    assert plan.nonempty() == [["a"], ["b"]]


def test_paused_projects_start_nothing_now() -> None:
    plan = graph.plan_waves([_n("a", 1)], {}, WIDE, paused=True)
    assert plan.now == () and plan.nonempty() == [["a"]]


def test_waves_are_capped_by_preview_slots() -> None:
    nodes = [_n(k, i + 1, preview=True) for i, k in enumerate("abc")] + [_n("d", 4)]
    slots = Slots(ics_free=5, budget_ics=5, previews_free=1, preview_slots=2)
    plan = graph.plan_waves(nodes, {}, slots)
    assert plan.waves == (("a", "d"), ("b", "c"))


def test_previews_off_caps_nothing() -> None:
    nodes = [_n(k, i + 1, preview=True) for i, k in enumerate("abc")]
    slots = Slots(ics_free=5, budget_ics=5, previews_free=0, preview_slots=0, previews_on=False)
    assert graph.plan_waves(nodes, {}, slots).now == ("a", "b", "c")


def test_overlapping_scopes_in_one_wave_are_serialized() -> None:
    nodes = [
        _n("api", 1, scope=("services/api/**",)),
        _n("api-tests", 2, scope=("services/api/test/**",)),
        _n("web", 3, scope=("web/**",)),
    ]
    plan = graph.plan_waves(nodes, {}, WIDE)
    assert plan.waves == (("api", "web"), ("api-tests",))
    assert plan.serial == (("api", "api-tests"),)


def test_a_running_task_serializes_an_overlapping_ready_one() -> None:
    nodes = [
        _n("api", 1, scope=("services/api/**",), state="running"),
        _n("api-docs", 2, scope=("services/api/README.md",)),
    ]
    plan = graph.plan_waves(nodes, {}, WIDE)
    assert plan.waves == ((), ("api-docs",))
    assert plan.serial == (("api", "api-docs"),)


def test_priority_orders_a_capped_wave() -> None:
    nodes = [_n("low", 1, priority=3), _n("high", 2, priority=0)]
    plan = graph.plan_waves(nodes, {}, Slots(ics_free=1, budget_ics=1))
    assert plan.waves == (("high",), ("low",))


def test_unmet_external_and_dead_deps_block_transitively() -> None:
    nodes = [
        _n("a", 1, "decision:D-7"),
        _n("b", 2, "a"),
        _n("c", 3, "gone"),
        _n("d", 4, "x"),
        _n("x", 5, state="failed"),
        _n("e", 6),
    ]
    plan = graph.plan_waves(nodes, {"decision:D-7": False}, WIDE)
    assert plan.waves == (("e",),)
    assert plan.blocked == {
        "a": ["decision:D-7"],
        "b": ["a"],
        "c": ["gone (unknown)"],
        "d": ["x (failed)"],
    }


def test_waiting_on_names_unmet_deps() -> None:
    nodes = {n.key: n for n in _state(DIAMOND, schema="done", route="running")}
    assert graph.waiting_on(nodes["settings"], nodes, {}) == ["route", "tests"]
    assert graph.waiting_on(nodes["tests"], nodes, {}) == []


def test_layers_keep_done_tasks_in_their_column() -> None:
    nodes = _state(DIAMOND, schema="done", route="running")
    assert graph.layers(nodes) == {"schema": 1, "route": 2, "tests": 2, "settings": 3, "docs": 1}


# ── Critical path ──────────────────────────────────────────────


def test_critical_path_uses_estimates_then_the_default() -> None:
    path, total = graph.critical_path(DIAMOND, lambda n: graph.weight_min(n, {}))
    assert path == ["schema", "route", "settings"]
    assert total == 160.0
    unestimated = [_n("a", 1), _n("b", 2, "a")]
    assert graph.critical_path(unestimated, lambda n: graph.weight_min(n, {})) == (
        ["a", "b"],
        120.0,
    )


def test_critical_path_prefers_observed_p50_per_kind() -> None:
    nodes = [
        _n("schema", 1, estimate_min=30),
        _n("route", 2, "schema", estimate_min=90),
        _n("docs", 3, "schema", kind="docs", estimate_min=10),
    ]
    observed = graph.observed_p50({"docs": [200, 210, 220, 230, 240], "implement": [5, 6]})
    assert observed == {"docs": 220.0}
    path, total = graph.critical_path(nodes, lambda n: graph.weight_min(n, observed))
    assert path == ["schema", "docs"]
    assert total == 250.0


def test_done_tasks_weigh_nothing_and_leave_the_path() -> None:
    nodes = _state(DIAMOND, schema="done", route="done")
    path, total = graph.critical_path(nodes, lambda n: graph.weight_min(n, {}))
    assert path == ["tests", "settings"]
    assert total == 60.0


def test_no_critical_path_when_finished_or_cyclic() -> None:
    assert graph.critical_path(
        _state(DIAMOND, **dict.fromkeys(("schema", "route", "tests", "settings", "docs"), "done")),
        lambda n: 1.0,
    ) == ([], 0.0)
    assert graph.critical_path([_n("a", 1, "b"), _n("b", 2, "a")], lambda n: 1.0) == ([], 0.0)


# ── Scope overlap ──────────────────────────────────────────────


def test_glob_overlap_prefix_heuristic() -> None:
    assert graph.globs_overlap("services/api/**", "services/api/src/x.ts")
    assert graph.globs_overlap("services/**", "services/api/**")
    assert graph.globs_overlap("./services/api", "services/api/x.ts")
    assert graph.globs_overlap("services/api*", "services/api-gw/**")
    assert not graph.globs_overlap("services/api", "services/api-gw/**")
    assert not graph.globs_overlap("web/**", "services/**")
    assert graph.globs_overlap("**", "anything")


def test_overlap_warnings_skip_ordered_pairs_and_flag_hub_files() -> None:
    nodes = [
        _n("route", 1, scope=("services/api/src/routes/**",)),
        _n("tests", 2, scope=("services/api/src/routes/version.test.ts",)),
        _n("ordered", 3, "route", scope=("services/api/src/routes/**",)),
        _n("index", 4, scope=("services/api/src/index.ts",)),
        _n("router", 5, scope=("services/api/src/routes/index.ts",)),
    ]
    found = graph.overlap_warnings(nodes, hub_files=["services/api/src/routes/index.ts"])
    overlaps = [w["tasks"] for w in found if w["code"] == "scope_overlap"]
    assert ["route", "tests"] in overlaps
    assert ["tests", "ordered"] in overlaps
    assert ["route", "ordered"] not in overlaps
    hub = next(w for w in found if w["code"] == "hub_file")
    assert hub["paths"] == ["services/api/src/routes/index.ts"]
    assert hub["tasks"] == ["route", "ordered", "router"]
    # Tasks that only touch the hub in order (one depends on the other) never warn.
    chain = [_n("a", 1, scope=("x/**",)), _n("b", 2, "a", scope=("x/**",))]
    assert graph.overlap_warnings(chain, hub_files=["x/index.ts"]) == []


# ── Bus, flow and org ──────────────────────────────────────────


def test_bus_edges_aggregate_messages_replies_and_bounces() -> None:
    a, b = "pl", "lead"
    facts = [
        MailFact(1_000, "message.queued", a, b, "request", "m1"),
        MailFact(1_100, "message.delivered", a, b, "request", "m1"),
        MailFact(61_000, "message.queued", b, a, "reply", "m2"),
        MailFact(70_000, "message.queued", a, b, "request", "m3"),
        MailFact(71_000, "message.bounced", a, b, "request", "m3"),
        MailFact(80_000, "message.queued", None, a, "info", "m4"),
    ]
    edges = {(e["from"], e["to"]): e for e in graph.bus_edges(facts)}
    forward = edges[(a, b)]
    assert forward["count"] == 2
    assert forward["kinds"] == {"request": 2}
    assert forward["delivered"] == 1 and forward["bounced"] == 1
    assert forward["requests"] == 2 and forward["unanswered"] == 1
    assert forward["p50_reply_s"] == 60.0
    assert edges[(b, a)]["kinds"] == {"reply": 1}
    assert edges[("server", a)]["count"] == 1


def test_flow_counts_back_edges_dwell_and_dollars() -> None:
    m = 60_000
    spans = [
        SpanFact("stage", "building", 0, 30 * m, "ok", "db"),
        SpanFact("stage", "building", 0, 50 * m, "ok", "db"),
        SpanFact("stage", "review", 0, 10 * m, "ok", "db"),
        SpanFact("gate", "review", 0, m, "fail", "db", "pr1"),
        SpanFact("gate", "review", 0, m, "fail", "db", "pr1"),
        SpanFact("gate", "review", 0, m, "fail", "db", "pr1"),
        SpanFact("gate", "verify", 0, m, "fail", "db", "pr1"),
        SpanFact("gate", "checks", 0, m, "fail", "db", "pr1"),
        SpanFact("gate", "checks", 0, m, "fail", "db", "pr1"),
        SpanFact("gate", "demo", 0, 4 * m, "ok", "db", "pr1"),
        SpanFact("pr_round", "r1", 0, m, "superseded", "db", "pr1"),
        SpanFact("pr_round", "r2", 0, m, "superseded", "db", "pr1"),
        SpanFact("pr_round", "r3", 0, None, "open", "db", "pr1"),
        SpanFact("loop_iter", "verify", 0, m, "fail", "db", attrs={"reason": "env: no pods"}),
        SpanFact("task", "ic:route", 0, m, "ok", "db", role="ic", cost_usd=2.5),
        SpanFact("task", "reviewer:review-route", 0, m, "ok", "db", role="reviewer", cost_usd=1.0),
        SpanFact("task", "demo:demo-route", 0, m, "ok", "db", role="demo", cost_usd=0.5),
    ]
    flow = graph.flow_graph(spans, now_counts={"build": 1, "review": 2})
    nodes = {n["id"]: n for n in flow["nodes"]}
    assert [n["id"] for n in flow["nodes"]] == list(graph.FLOW_STAGES)
    assert nodes["build"]["p50_dwell_s"] == 2400.0 and nodes["build"]["n"] == 2
    assert nodes["build"]["count"] == 1 and nodes["review"]["count"] == 2
    assert nodes["build"]["usd"] == 2.5 and nodes["review"]["usd"] == 1.0
    assert nodes["demo"]["p50_dwell_s"] == 240.0 and nodes["demo"]["usd"] == 0.5
    back = {(e["from"], e["label"]): e["count"] for e in flow["back_edges"]}
    assert back[("review", "changes")] == 3
    assert back[("verify", "FAIL")] == 1
    assert back[("build", "CI fail")] == 2
    assert back[("verify", "FAIL(env)")] == 1
    assert back[("ready", "new head")] == 2


def test_org_graph_edges_and_health_groups() -> None:
    rows = [
        {
            "id": "atlas",
            "project": None,
            "parent": None,
            "supervisor": "sentinel",
            "is_top_level": True,
            "health": "ok",
        },
        {
            "id": "sentinel",
            "project": None,
            "parent": "atlas",
            "supervisor": "atlas",
            "is_top_level": True,
            "health": "ok",
        },
        {
            "id": "pl",
            "project": "db",
            "parent": "atlas",
            "supervisor": "sentinel",
            "is_top_level": True,
            "health": "degraded",
        },
        {"id": "ic", "project": "db", "parent": "pl", "is_top_level": False, "health": "dead"},
        {"id": "helper", "project": "db", "parent": "ic", "is_top_level": False},
    ]
    org = graph.org_graph(rows)
    edges = {(e["from"], e["to"], e["kind"]) for e in org["edges"]}
    assert ("atlas", "pl", "spawn") in edges
    assert ("sentinel", "pl", "supervise") in edges
    assert ("ic", "helper", "spawn") in edges
    groups = {g["project"]: g for g in org["groups"]}
    assert groups["db"]["health"] == "dead"
    assert groups["db"]["by_health"] == {"degraded": 1, "dead": 1, "ok": 1}
    assert groups[None]["members"] == ["atlas", "sentinel"]
