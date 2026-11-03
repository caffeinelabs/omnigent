"""Status lines, roll-ups and closed alarms on the sample factory at 13:40 (S5, S6, S7).

Every node says in one line why it is or isn't moving, ending at the person
who must act (``node_status``): the cases are the prototype's (assistant-core
held for C-16, the panel not started for 7 h, the product's top blocker, the
same factory after D-45 is answered, and at scale a waiting First-column task
never hides an ask). The roll-ups carry cost against budget, burn, an ETA only
from observed durations, feature progress from acceptance and what is in the
base env by ancestry; the alarms the server closed sit under Health with
their causes. Lines are read as text the way the web reads them
(``statusModel.ts``). Every name is sample data.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from omnigent.entities.factory import FactoryDecision, FactoryLoop, FactoryTask
from omnigent.server.factory import alarms, node_status
from omnigent.server.factory.node_views import validate_page
from omnigent.server.factory.nodes import Tree, assert_levels
from tests.server.factory.node_fixtures import (
    AT_1325,
    FID,
    HEAD_AT,
    NOW,
    SHORTLIST,
    factory_1340,
    hex_id,
    scale_factory,
)

HELD = "T-16 → held for C-16 → needs you: Apply C-16 (D-45)"


@pytest.fixture()
def tree() -> Tree:
    return assert_levels(Tree(factory_1340()))


def _page(tree: Tree, ref: str) -> dict[str, Any]:
    page = tree.page(ref)
    assert page is not None, ref
    return validate_page(page)


def _person(decision: dict[str, Any]) -> str:
    named = decision.get("grouped") or decision["id"] in decision["verb"]
    return f"needs you: {decision['verb']}" + ("" if named else f" ({decision['id']})")


def full(line: dict[str, Any]) -> str:
    """A header's line as text (``statusText`` in the web's ``statusModel.ts``)."""
    parts: list[str] = []
    for i, seg in enumerate(line["segments"]):
        if i:
            parts.append(f" {seg['sep']} ")
        if seg.get("person"):
            alone = i == 0 and line["label"].lower().startswith(("needs you", "waiting"))
            parts.append(seg["decision"]["verb"] if alone else _person(seg["decision"]))
            continue
        label, ref = seg.get("label") or "", seg.get("ref")
        parts.append(f"{label} " if label and ref else label)
        if ref:
            parts.append(seg.get("title") or ref)
    if line.get("also_holds"):
        parts.append(" · also holds " + ", ".join(h["title"] for h in line["also_holds"]))
    if line.get("detail"):
        parts.append((" · " if line["segments"] else "") + line["detail"])
    return " ".join(f"{line['label']} {''.join(parts)}".split())


def compact(line: dict[str, Any], above: str | None = None) -> str:
    """A tile's line as text: the end of the path, "the ask above" for the header's ask."""
    verb_only = line["label"].lower().startswith(("needs you", "waiting"))
    is_above = bool(above and (line.get("decision") or {}).get("id") == above)
    out: list[str] = []
    via: list[str] = []
    person = None
    for seg in line["segments"]:
        if seg.get("person"):
            person = seg["decision"]
        elif seg["sep"] == "›":
            via.append(seg.get("title") or seg["ref"])
        else:
            text = f"{seg['label']} " if seg.get("label") and seg.get("ref") else seg["label"]
            out.append(text + (seg.get("title") or seg.get("ref") or ""))
    if person is not None:
        if verb_only:
            out.append(person["verb"])
        else:
            out.append("the ask above" if is_above else _person(person))
    text = " · ".join(o.strip() for o in out if o.strip())
    if via:
        text += f" · via {' › '.join(via)}"
    if is_above and verb_only:
        text += " · the ask above"
    if line.get("detail"):
        text += f" · {line['detail']}"
    return f"{line['label']} {text}".strip()


def _kid(page: dict[str, Any], ref: str) -> dict[str, Any]:
    return next(k for k in page["children"]["items"] if k["ref"] == ref)


# ── S5: the blocker path ───────────────────────────────────────


def test_assistant_core_is_held_for_c16(tree: Tree) -> None:
    page = _page(tree, "assistant-core")
    line = page["status"]

    assert full(line) == f"Not moving: {HELD}"
    assert line["ends"] == "person"
    assert line["decision"]["id"] == "D-45" and line["decision"]["subject"] == "C-16"
    assert [a["label"] for a in line["decision"]["actions"]] == ["Apply", "Edit & apply", "Reject"]
    assert line["decision"]["asker"] == "Atlas"
    assert line["decision"]["expires"] == "2d 16h"
    assert line["why"][-1]["tone"] == "you"
    assert line["why_title"] == "Why assistant-core is not moving"
    # The stall alarm on T-16 is not this page's business: it is closed, with a cause.
    assert '"D-48"' not in json.dumps(page)
    assert "also_holds" not in line  # the panel is the "needed by" chip instead
    assert compact(_kid(page, "T-16")["status_compact"], above="D-45") == (
        "Held for C-16: the ask above"
    )


def test_the_panel_has_not_started_for_7_hours(tree: Tree) -> None:
    line = _page(tree, "assistant-panel-ux")["status"]

    assert full(line) == f"Not started for 7 h: waits on assistant-core › {HELD}"
    assert line["segments"][0] == {
        "sep": "→",
        "label": "waits on",
        "ref": "assistant-core",
        "title": "assistant-core",
        "reason": "needs the session API and per-turn debit",
    }
    assert line["why_title"] == "Why assistant-panel-ux has not started"
    assert [w.get("tone") for w in line["why"]] == [None, None, None, None, "you"]
    assert "because it needs the session API and per-turn debit" in line["why"][1]["html"]
    # The three met links Atlas added without a reason are named once.
    assert line["note"].startswith("It also waited on 3 projects linked without a reason")
    assert all(slug in line["note"] for slug in SHORTLIST[:3])


def test_the_product_names_its_top_blocker_and_what_else_it_holds(tree: Tree) -> None:
    line = _page(tree, "product")["status"]

    # The need above the home level (features) is a step the line leaves out.
    assert full(line) == (
        f"Not moving: Agent assistant (sample) › assistant-core › {HELD}"
        " · also holds assistant-panel-ux"
    )
    assert line["also_holds"] == [{"ref": "assistant-panel-ux", "title": "assistant-panel-ux"}]
    assert line["why"][0]["html"] == "Sample app (sample) is not moving."


def test_tiles_show_only_the_end_of_the_path(tree: Tree) -> None:
    feature = _page(tree, "I-1")
    core = _kid(feature, "assistant-core")["status_compact"]
    panel = _kid(feature, "assistant-panel-ux")["status_compact"]

    assert full(feature["status"]) == (
        f"Not moving: assistant-core › {HELD} · also holds assistant-panel-ux"
    )
    assert compact(core, above="D-45") == "Needs you: Apply C-16 · via T-16 · the ask above"
    assert compact(panel, above="D-45") == "Waits on assistant-core · held for C-16"
    # The panel's tile ends at the header's ask too, so it carries no second button.
    assert panel["decision"]["id"] == "D-45" and "why" not in panel
    assert compact(_kid(_page(tree, "product"), f"ask:{'a' * 31}1")["status_compact"]) == (
        "Needs you: Apply C-16 · via Agent assistant (sample) › T-16"
    )


def test_a_task_held_on_purpose(tree: Tree) -> None:
    line = _page(tree, "T-16")["status"]

    assert full(line) == "Held on purpose: held for C-16 → needs you: Apply C-16 (D-45)"


def test_merges_a_person_makes_on_one_base_are_one_ask(tree: Tree) -> None:
    page = _page(tree, "build-tier-drafts-3")
    line = page["status"]

    assert full(line) == (
        "Needs you: Merge #9856 and #9857 · Ready to merge into main. "
        "A person merges main, so the factory does not."
    )
    ask = line["decision"]
    assert (ask["id"], ask["grouped"], ask["external_url"]) == (
        "merge:main",
        True,
        "https://github.com/sample-org/sample-app/pull/9856",
    )
    assert [a["label"] for a in ask["actions"]] == ["Review and merge"]
    for row in page["children"]["items"]:
        assert compact(row["status_compact"], above="merge:main") == (
            "Needs you: Merge #9856 and #9857 · the ask above"
        )


def test_a_hold_on_another_task_hops_there() -> None:
    facts = factory_1340()
    facts.decisions = [d for d in facts.decisions if d.ref != "D-45"]
    facts.proposals = [p for p in facts.proposals if p.ref != "C-16"]
    ledger = dataclasses.replace(
        facts.tasks[1],
        id=hex_id(219),
        number=19,
        key="ledger",
        title="Credit ledger",
        deps=[],
        hold=None,
        session_ids=[],
        cost_usd=None,
    )
    facts.tasks = [_hold(t, "T-19", "task") if t.ref == "T-16" else t for t in facts.tasks]
    facts.tasks = [*facts.tasks, ledger]
    tree = assert_levels(Tree(facts))

    line = _page(tree, "T-16")["status"]

    assert full(line) == "Waiting: held for T-19 · not started"
    assert line["why"][1]["html"] == 'It is held for <a data-ref="T-19">T-19</a>.'
    row = _kid(_page(tree, "assistant-core"), "T-16")["status_compact"]
    assert compact(row) == "Held for T-19 · not started"


def _after_d45(facts: Any) -> Any:
    """C-16 applied: D-45 is answered, T-16 opens its PR, the panel still waits on core."""
    facts.decisions = [d for d in facts.decisions if d.ref != "D-45"]
    facts.proposals = [
        dataclasses.replace(p, status="applied") if p.ref == "C-16" else p for p in facts.proposals
    ]
    facts.tasks = [
        dataclasses.replace(t, state="running", hold=None) if t.ref == "T-16" else t
        for t in facts.tasks
    ]
    views = dict(facts.views)
    views["assistant-core"] = {**views["assistant-core"], "waiting_on": []}
    facts.views = views
    return facts


def test_after_d45_the_feature_ends_at_d49_and_the_root_at_the_merges() -> None:
    tree = assert_levels(Tree(_after_d45(factory_1340())))

    panel = _page(tree, "assistant-panel-ux")["status"]
    feature = _page(tree, "I-1")["status"]
    root = _page(tree, "product")["status"]

    assert full(panel) == "Waiting: waits on assistant-core · moving (T-16: Luz building)"
    assert panel["ends"] == "moving"
    assert feature["decision"]["id"] == "D-49" and feature["label"] == "Needs you:"
    # Work moves again (T-16), so the root's line is its top blocker: the merges on main.
    assert full(root) == "Top blocker: build-tier-drafts-3 → needs you: Merge #9856 and #9857"
    core = _page(tree, "assistant-core")["status"]
    assert core["kind"] == "moving" and core["moving"]["ref"] == "T-16"
    assert core["why"] == [
        {"html": 'Moving now: <a data-ref="T-16">T-16</a> · Luz building', "tone": "ok"}
    ]


def test_a_dependency_cycle_ends_the_path() -> None:
    facts = factory_1340()
    views = dict(facts.views)
    loop = {"kind": "dependency", "who": "a lead", "person": False, "label": "waiting"}
    views["assistant-core"] = {
        **views["assistant-core"],
        "waiting_on": [{**loop, "ref": "assistant-panel-ux"}],
    }
    facts.views = views
    tree = assert_levels(Tree(facts))

    line = _page(tree, "assistant-panel-ux")["status"]

    assert line["ends"] == "cycle"
    assert [s.get("ref") for s in line["segments"]] == ["assistant-core", "assistant-panel-ux"]


def _hold(task: FactoryTask, on: str, kind: str) -> FactoryTask:
    return dataclasses.replace(task, hold={"on": on, "kind": kind, "ref": on, "until": NOW + 3600})


def test_at_scale_a_waiting_first_column_task_never_hides_an_ask() -> None:
    """Holds on running tasks (nobody owes anything) and on open asks, across the tree."""
    facts = scale_factory()
    running = [t for t in facts.tasks if t.state == "running"]
    asks: list[FactoryDecision] = []
    tasks: list[FactoryTask] = []
    for task in facts.tasks:
        if task.state != "ready":
            tasks.append(task)
        elif task.number % 4 == 0:
            other = next(r for r in running if r.project_slug != task.project_slug)
            tasks.append(_hold(task, other.ref, "task"))
        elif task.number % 3 == 0:
            ask = FactoryDecision(
                id=hex_id(60_000 + task.number),
                factory_id=FID,
                number=1000 + task.number,
                question=f"Which way for {task.ref}?",
                source="agent",
                blocking=True,
                subject=f"task:{task.ref}",
                project_slug=task.project_slug,
                created_at=NOW - 600,
                updated_at=NOW - 600,
            )
            asks.append(ask)
            tasks.append(_hold(task, ask.ref, "decision"))
        else:
            tasks.append(task)
    facts.tasks = tasks
    facts.decisions = asks
    tree = assert_levels(Tree(facts))
    paths = tree.paths

    checked = 0
    for node in list(tree.nodes.values()):
        if tree.state(node.ref) in ("done", "later", "ghost", "moving"):
            continue
        kids = [
            k
            for k in tree.shown_children(node.ref)
            if not k.ghost and not k.later and tree.state(k.ref) in node_status.BLOCKED
        ]
        if not any(paths.path(k.ref).end == "person" for k in kids):
            continue
        checked += 1
        found = paths.path(node.ref)
        assert found.end == "person", (node.ref, found)
        _page(tree, node.ref)
    assert checked > 20


# ── S6: roll-ups, results, the base env ────────────────────────


def test_cost_against_budget_and_burn(tree: Tree) -> None:
    core = _page(tree, "assistant-core")["rollup"]
    feature = _page(tree, "I-1")
    product = _page(tree, "product")["rollup"]

    assert (core["cost"], core["budget"], core["burn"]) == (25.13, 150.0, 2.5)
    # The feature's cap is its projects' caps; its burn is theirs.
    assert (feature["rollup"]["cost"], feature["rollup"]["budget"]) == (51.13, 300.0)
    assert feature["rollup"]["burn"] == 2.5
    assert _kid(feature, "assistant-core")["burn"] == 2.5
    assert product["burn"] == 9.37
    assert "eta" not in core and "eta" not in feature["rollup"]  # nothing observed yet


def test_feature_progress_is_acceptance_on_the_base_env_then_build(tree: Tree) -> None:
    feature = _page(tree, "I-1")

    assert feature["rollup"]["progress"]["label"] == "2/6"
    assert feature["rollup"]["progress"]["what"] == "acceptance"
    assert round(feature["rollup"]["progress"]["secondary"] * 100) == 67
    assert feature["node"]["try_it"] == [
        {
            "text": "Ask how the app is doing: the answer uses analytics",
            "acceptance_step": 2,
            "how": "API only: assistant-panel-ux not built yet",
            "api": True,
        },
        {
            "text": "A flag-off user gets no assistant",
            "acceptance_step": 3,
            "how": "API only: assistant-panel-ux not built yet",
            "api": True,
        },
    ]
    assert feature["rollup"]["in_base_env"] == {"sha": "817480d", "try_it": True}
    # The project that owns those steps offers them; one that owns none does not.
    assert [i["acceptance_step"] for i in _page(tree, "assistant-core")["node"]["try_it"]] == [
        2,
        3,
    ]
    assert "try_it" not in _page(tree, "connectors-core")["node"]
    assert _kid(feature, "assistant-core")["try_ready"] is True


def test_eta_only_from_observed_durations() -> None:
    facts = factory_1340()
    facts.observed = {"implement": 45.0}
    tree = assert_levels(Tree(facts))

    assert _page(tree, "assistant-core")["rollup"]["eta"] == "~45 min"
    # The panel has no tasks to observe yet, so its feature shows no ETA at all.
    assert "eta" not in _page(tree, "I-1")["rollup"]
    facts.observed = {"docs": 20.0}
    assert "eta" not in _page(assert_levels(Tree(facts)), "assistant-core")["rollup"]


def test_a_feature_eta_sums_the_slowest_project_of_each_column() -> None:
    facts = scale_factory(features=1, projects=3, tasks=12)
    facts.observed = {"implement": 30.0}
    tree = assert_levels(Tree(facts))

    projects = [tree.eta_min(tree.nodes[p]) for p in ("proj-00", "proj-01", "proj-02")]
    columns = [tree.nodes[p].col for p in ("proj-00", "proj-01", "proj-02")]

    assert columns == [0, 1, 2] and all(p for p in projects)
    assert tree.eta_min(tree.nodes["I-1"]) == sum(projects)


def test_results_and_the_base_env_on_the_root(tree: Tree) -> None:
    page = _page(tree, "product")

    assert (page["results"]["merged"], page["results"]["in_base_env"]) == (7, 7)
    assert page["node"]["env"] == {
        "name": "Base env",
        "branch": "sample-main",
        "sha": "817480d",
        "deployed_at": "2026-11-03T13:27:00Z",
        "state": "ready",
        "smoke": {"ok": True},
        "app_url": "https://app.sample.test",
        "api_url": "https://api.sample.test",
        "ref": "base-env",
    }
    assert _page(tree, "skill-discovery")["status"]["detail"] == "#9881 merged · in base env"
    # The server's probe, when it ran, says whether it answers.
    facts = factory_1340()
    probe = {"health": "ok", "app": {"code": 200}, "api": {"code": 200}}
    facts.base_env = {**facts.base_env, "env": {**facts.base_env["env"], "probe": probe}}
    env = _page(assert_levels(Tree(facts)), "product")["node"]["env"]
    assert env["smoke"] == {"ok": True, "checks": "app 200 · api 200"}


def test_a_merge_after_the_deployed_head_is_not_yet_in_the_base_env() -> None:
    facts = factory_1340()
    late = dataclasses.replace(
        facts.prs[1],
        id=hex_id(9890),
        number=9890,
        task_id=None,
        merged_at=HEAD_AT + 200,
        merged_sha="9890000" + "0" * 33,
        title="After the deploy",
    )
    on_main = dataclasses.replace(
        facts.prs[2], id=hex_id(9891), number=9891, task_id=None, base_ref="main"
    )
    facts.prs = [*facts.prs, late, on_main]
    tree = assert_levels(Tree(facts))

    assert _page(tree, "PR-9882")["node"]["in_base_env"] is True  # merged 13:25, head 13:25:30
    assert "in_base_env" not in _page(tree, "PR-9890")["node"]
    assert "in_base_env" not in _page(tree, "PR-9891")["node"]  # main is another base
    assert _page(tree, "connectors-core")["status"]["detail"] == (
        "#9882, #9890 merged · not yet in base env"
    )
    product = _page(tree, "product")
    assert (product["results"]["merged"], product["results"]["in_base_env"]) == (9, 7)
    assert "in_base_env" not in product["rollup"]
    # Without an observed move the env's first report at that commit bounds it.
    facts.base_env = {**facts.base_env, "deployed": None}
    tree = assert_levels(Tree(facts))
    assert _page(tree, "PR-9876")["node"]["in_base_env"] is True
    assert "in_base_env" not in _page(tree, "PR-9890")["node"]
    assert AT_1325 < HEAD_AT < HEAD_AT + 200


# ── S7: alarms closed with a cause ─────────────────────────────


def test_the_closed_alarms_sit_under_health_with_their_causes(tree: Tree) -> None:
    health = _page(tree, "fx:health")
    product = _page(tree, "product")

    assert product["rollup"]["alarms_closed"] == {"count": 3, "ref": "fx:health"}
    assert [i["ref"] for i in health["children"]["items"]] == ["D-47", "D-46", "D-48"]
    assert health["node"]["closed_alarms"] == [
        {
            "id": "D-47",
            "was": "PR #9881: claim 1/3 stalled - what next?",
            "cause": "PR #9881 merged",
            "ref": "skill-discovery",
        },
        {
            "id": "D-46",
            "was": "T-18 Discovery skill with live search: implement 2/3 stalled - what next?",
            "cause": "PR #9881 merged",
            "ref": "skill-discovery",
        },
        {
            "id": "D-48",
            "was": "T-16 Debit each turn: implement 1/3 stalled - what next?",
            "cause": "T-16 is held on purpose for C-16; a held task is not stalled",
            "real_ask": "D-45",
            "ref": "T-16",
        },
    ]
    alarm = _page(tree, "D-48")
    assert [c["ref"] for c in alarm["breadcrumb"]] == ["fx", "fx:health"]
    assert alarm["status"]["detail"] == (
        "closed by the server: T-16 is held on purpose for C-16; a held task is not stalled."
        " The real ask is D-45."
    )
    assert health["status"]["detail"] == "3 alarms closed by the server today, each with its cause"
    # Each alarm is one chip on the node it was about.
    assert _page(tree, "T-16")["chips"] == [
        {
            "kind": "alarm closed",
            "ref": "D-48",
            "title": "D-48",
            "state": "T-16 is held on purpose for C-16; a held task is not stalled",
        }
    ]
    [chip] = [c for c in _page(tree, "skill-discovery")["chips"] if c.get("kind")]
    assert (chip["kind"], chip["title"], chip["state"]) == (
        "alarms closed",
        "D-47, D-46",
        "PR #9881 merged",
    )


def test_an_open_stall_alarm_on_a_task_held_on_purpose_is_no_ask() -> None:
    """Before the tick closes it, D-48 colours nothing and is counted nowhere."""
    facts = factory_1340()
    facts.closed_alarms = []
    alarm = FactoryDecision(
        id=hex_id(548),
        factory_id=FID,
        number=48,
        question="T-16 Debit each turn: implement 1/3 stalled - what next?",
        source="loop",
        blocking=True,
        project_slug="assistant-core",
        created_at=NOW - 3600,
        updated_at=NOW - 3600,
    )
    facts.decisions = [*facts.decisions, alarm]
    facts.loops = [
        FactoryLoop(
            id=hex_id(1048),
            factory_id=FID,
            kind="implement",
            subject=f"task:{hex_id(216)}",
            project_slug="assistant-core",
            task_id=hex_id(216),
            state="stalled",
            decision_id=alarm.id,
        )
    ]
    tree = assert_levels(Tree(facts))

    core = _page(tree, "assistant-core")
    assert core["node"]["needs"] == {"open": 1, "blocking": 1}
    assert '"D-48"' not in json.dumps(core)
    assert full(core["status"]) == f"Not moving: {HELD}"
    # A stall on a task that is not held is a real ask.
    facts.tasks = [dataclasses.replace(t, hold=None) for t in facts.tasks]
    core = _page(assert_levels(Tree(facts)), "assistant-core")
    assert core["node"]["needs"] == {"open": 2, "blocking": 2}


def test_alarms_read_back_from_the_closing_events() -> None:
    from omnigent.entities.factory import FactoryEvent

    def event(n: int, kind: str, data: dict[str, Any], summary: str = "") -> FactoryEvent:
        return FactoryEvent(
            id=hex_id(n),
            factory_id=FID,
            ts=(NOW - 100 + n) * 1000,
            kind=kind,
            summary=summary,
            project_slug="skill-discovery",
            subject=f"decision:D-{n}",
            data=data,
        )

    found = alarms.read(
        [
            event(
                46,
                "decision.answered",
                {"source": "loop", "by_kind": "server", "reason": "PR #9881 merged"},
                "D-46 closed by the server: PR #9881 merged",
            ),
            event(47, "decision.expired", {"source": "loop"}, "D-47 expired: the loop runs again"),
            event(
                48,
                "decision.answered",
                {
                    "source": "loop",
                    "by_kind": "server",
                    "cause": "T-16 is held on purpose for C-16; a held task is not stalled",
                    "real_ask": "D-45",
                    "about": "task:T-16",
                    "was": "T-16 stalled - what next?",
                },
            ),
            event(49, "decision.answered", {"source": "loop", "by_kind": "human"}),
            event(50, "decision.answered", {"source": "proposal", "by_kind": "server"}),
        ]
    )

    assert [(a.ref, a.cause, a.real_ask, a.about) for a in found] == [
        ("D-48", "T-16 is held on purpose for C-16; a held task is not stalled", "D-45", "T-16"),
        ("D-47", "the loop runs again", None, "skill-discovery"),
        ("D-46", "PR #9881 merged", None, "skill-discovery"),
    ]
    assert alarms.visible(found, {"skill-discovery"}) == []
