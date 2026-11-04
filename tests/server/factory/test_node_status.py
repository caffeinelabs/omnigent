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
import re
from typing import Any

import pytest

from omnigent.entities.factory import FactoryDecision, FactoryLoop, FactoryTask
from omnigent.server.factory import alarms, node_status
from omnigent.server.factory.node_views import validate_page
from omnigent.server.factory.nodes import Tree, assert_levels
from tests.server.factory.node_fixtures import (
    AT_1325,
    DEPLOYED_SHA,
    FID,
    HEAD_AT,
    NOW,
    REPO,
    SHORTLIST,
    factory_1340,
    hex_id,
    scale_factory,
    snapshot_waits,
)

HELD = "T-16 → held for C-16 → needs you: Apply C-16 (D-45)"


@pytest.fixture()
def tree() -> Tree:
    return assert_levels(Tree(factory_1340()))


def build(facts: Any) -> Tree:
    """The tree of *facts* after a test changed its rows: the snapshot's waits read again."""
    return assert_levels(Tree(snapshot_waits(facts)))


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
    tree = build(facts)

    line = _page(tree, "T-16")["status"]

    # The hop ends at who starts T-19: its project's lead.
    assert full(line) == "Waiting: held for T-19 → waits for Juno to start it · not started"
    assert line["why"][1]["html"] == 'It is held for <a data-ref="T-19">T-19</a>.'
    row = _kid(_page(tree, "assistant-core"), "T-16")["status_compact"]
    assert compact(row) == "Held for T-19 · not started"


def test_a_task_nothing_holds_ends_at_the_lead_who_starts_it() -> None:
    facts = factory_1340()
    facts.decisions = [d for d in facts.decisions if d.ref != "D-45"]
    facts.proposals = [
        dataclasses.replace(p, status="applied") if p.ref == "C-16" else p for p in facts.proposals
    ]
    facts.tasks = [
        dataclasses.replace(t, hold=None) if t.ref == "T-16" else t for t in facts.tasks
    ]
    tree = build(snapshot_waits(facts))

    line = _page(tree, "T-16")["status"]

    assert full(line) == "Not started: waits for Juno to start it"
    assert line["ends"] == "other"
    assert line["why"][-1]["html"] == "Nothing holds it: waits for Juno to start it."


def test_the_base_envs_setup_project_says_who_starts_it() -> None:
    facts = factory_1340()
    view = {k: v for k, v in facts.views["base-env"].items() if k != "pl"}
    facts.views = {**facts.views, "base-env": {**view, "stage": "queued"}}
    facts.projects = [
        dataclasses.replace(p, stage="queued") if p.slug == "base-env" else p
        for p in facts.projects
    ]

    line = _page(build(facts), "base-env")["status"]

    assert re.fullmatch(r"Not started for \d+ \w+: waits for Atlas to start it", full(line))
    assert line["ends"] == "other"


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
    return snapshot_waits(facts)


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
    """Core and the panel wait on each other and nothing else holds either: "cycle"."""
    facts = factory_1340()
    facts.decisions = [d for d in facts.decisions if d.ref != "D-45"]
    facts.proposals = [p for p in facts.proposals if p.ref != "C-16"]
    facts.tasks = [dataclasses.replace(t, hold=None) for t in facts.tasks]
    facts.projects = [
        dataclasses.replace(p, charter={**p.charter, "depends_on": ["assistant-panel-ux"]})
        if p.slug == "assistant-core"
        else p
        for p in facts.projects
    ]
    tree = build(facts)

    panel = _page(tree, "assistant-panel-ux")["status"]
    core = _page(tree, "assistant-core")["status"]

    assert (panel["ends"], core["ends"]) == ("cycle", "cycle")
    assert [s.get("ref") for s in panel["segments"]] == ["assistant-core", "assistant-panel-ux"]
    assert [s.get("ref") for s in core["segments"]] == ["assistant-panel-ux", "assistant-core"]


def _project_like(facts: Any, slug: str, **charter: Any) -> Any:
    """A queued project like the panel (created at the same time), with its own view."""
    panel = next(p for p in facts.projects if p.slug == "assistant-panel-ux")
    n = 1100 + len(facts.projects)
    row = dataclasses.replace(panel, id=hex_id(n), slug=slug, title=slug, charter=dict(charter))
    facts.projects = [*facts.projects, row]
    facts.views = {**facts.views, slug: {**facts.views["assistant-panel-ux"], "slug": slug}}
    return row


def test_the_panel_waits_because_the_gate_holds_it_on_unfinished_work() -> None:
    """
    #9876 merged and nothing is open, but T-16 is: the snapshot's own waits
    (``waits.project_waits``) keep the panel on assistant-core, as gating does.
    """
    from omnigent.server.factory import gating

    facts = factory_1340()
    [wait] = facts.views["assistant-panel-ux"]["waiting_on"]
    core = next(p for p in facts.projects if p.slug == "assistant-core")

    assert (wait["kind"], wait["ref"]) == ("dependency", "assistant-core")
    assert not gating.dependency_met(core, facts.prs, "merged", facts.tasks)
    # Without its open task the first merge would have met it.
    assert gating.dependency_met(core, facts.prs, "merged", [])


def test_a_project_nothing_holds_ends_at_who_starts_it() -> None:
    facts = factory_1340()
    _project_like(facts, "lonely-a")
    _project_like(facts, "lonely-b", spawn_owner="server")
    _project_like(facts, "lonely-c")
    _project_like(facts, "lonely-d")
    since = facts.views["assistant-panel-ux"].get("since")
    facts.views["lonely-a"] = {
        **facts.views["lonely-a"],
        "start": {"state": "queued", "phase": "lead", "label": "x", "since": since},
    }
    facts.views["lonely-c"] = {
        **facts.views["lonely-c"],
        "start": {"state": "starting", "phase": "image", "label": "Pulling the image"},
    }
    facts.views["lonely-d"] = {
        **facts.views["lonely-d"],
        "start": {"state": "queued", "phase": "pod_cap", "pod_cap": 12, "ahead": 3},
    }
    tree = build(facts)

    lines = {slug: _page(tree, slug)["status"] for slug in ("lonely-a", "lonely-b", "lonely-c")}

    assert full(lines["lonely-a"]) == "Not started for 7 h: waits for Atlas to start it"
    assert lines["lonely-a"]["ends"] == "other"
    assert full(lines["lonely-b"]) == "Not started for 7 h: the server starts it within a minute"
    assert lines["lonely-b"]["ends"] == "server"
    assert full(lines["lonely-c"]) == "Not started for 7 h: starting: pulling the image"
    assert lines["lonely-c"]["why"][-1]["html"] == "Nothing holds it: starting: pulling the image."
    # A start that waits for a machine is the capacity wait, with how full and how many ahead.
    line = _page(tree, "lonely-d")["status"]
    assert full(line) == (
        "Not started for 7 h: waits for a free machine (all 12 machines in use, 3 ahead)"
    )
    tile = tree.status(tree.nodes["lonely-b"], compact=True)
    assert compact(tile) == "Not started: the server starts it within a minute"


def test_a_node_s_own_ask_ends_its_path_when_its_waits_end_elsewhere() -> None:
    """After D-45 the panel waits on moving work; its own blocking ask still ends the line."""
    facts = _after_d45(factory_1340())
    ask = FactoryDecision(
        id=hex_id(560),
        factory_id=FID,
        number=60,
        question="Reopen the closed panel spike?",
        source="pr_closed",
        blocking=True,
        project_slug="assistant-panel-ux",
        created_at=NOW - 600,
        updated_at=NOW - 600,
    )
    facts.decisions = [*facts.decisions, ask]
    tree = build(facts)

    page = _page(tree, "assistant-panel-ux")

    assert page["node"]["state"] == "needs"
    assert page["node"]["needs"] == {"open": 1, "blocking": 1}
    assert (page["status"]["ends"], page["status"]["decision"]["id"]) == ("person", "D-60")
    assert full(page["status"]) == "Needs you: Answer D-60"


def test_a_long_path_keeps_its_first_steps_and_the_person() -> None:
    """Seven queued projects ahead of assistant-core: the line is cut, the ask stays."""
    facts = factory_1340()
    chain = [f"chain-{i}" for i in range(1, 8)]
    for here, after in zip(chain, [*chain[1:], "assistant-core"], strict=True):
        _project_like(facts, here, depends_on=[after])
    tree = build(facts)

    page = _page(tree, "chain-1")
    line = page["status"]

    assert (line["ends"], line["decision"]["id"]) == ("person", "D-45")
    assert line["label"] == "Not started for 7 h:"
    assert len(line["segments"]) == node_status.SHOWN_MAX
    assert [s.get("ref") for s in line["segments"][:4]] == chain[1:5]
    assert line["segments"][4] == {"sep": "→", "label": node_status.ELIDED}
    assert line["segments"][-1]["person"] is True
    assert "more steps along the same chain" in line["why"][5]["html"]
    tile = tree.status(tree.nodes["chain-1"], compact=True)
    assert (tile["ends"], tile["decision"]["id"]) == ("person", "D-45")
    assert compact(tile) == "Waits on chain-2 · held for C-16"


def _task_ask(number: int, task: str) -> FactoryDecision:
    return FactoryDecision(
        id=hex_id(500 + number),
        factory_id=FID,
        number=number,
        question=f"Which way for {task}?",
        source="agent",
        blocking=True,
        subject=f"task:{task}",
        created_at=NOW - 600,
        updated_at=NOW - 600,
    )


def test_the_ask_that_holds_more_weight_is_the_one_shown() -> None:
    """Two holds in one project, tied on dependents and column: the heavier ask wins."""
    facts = factory_1340()
    facts.decisions = [d for d in facts.decisions if d.ref != "D-45"]
    facts.proposals = [p for p in facts.proposals if p.ref != "C-16"]
    heavy = dataclasses.replace(
        facts.tasks[1],
        id=hex_id(219),
        number=19,
        key="ledger",
        title="Credit ledger",
        estimate_min=240,
        session_ids=[],
    )
    facts.tasks = [
        *(_hold(t, "D-62", "decision") if t.ref == "T-16" else t for t in facts.tasks),
        _hold(heavy, "D-61", "decision"),
    ]
    facts.decisions = [*facts.decisions, _task_ask(61, "T-19"), _task_ask(62, "T-16")]
    tree = build(facts)
    paths = tree.paths

    assert tree.nodes["T-16"].col == tree.nodes["T-19"].col
    assert paths.weight(paths.path("T-19").ask) > paths.weight(paths.path("T-16").ask)
    line = _page(tree, "assistant-core")["status"]
    # T-16 comes first among the children: only the weight puts T-19 ahead.
    assert full(line) == "Not moving: T-19 → held for D-61 → needs you: Answer D-61"


def test_a_ready_pr_into_an_auto_merge_target_waits_on_the_server() -> None:
    facts = factory_1340()
    facts.targets = frozenset({(REPO.lower(), "main")})
    facts.prs = [
        dataclasses.replace(p, head_ref=f"factory/{p.project_slug}/part-{p.number}")
        if p.state == "open"
        else p
        for p in facts.prs
    ]
    tree = build(facts)

    change = _page(tree, "PR-9856")["status"]
    project = _page(tree, "build-tier-drafts-3")["status"]

    assert (change["ends"], full(change)) == (
        "server",
        "Waiting: waits for the server to merge it",
    )
    assert project["ends"] == "server"
    assert full(project) == "Waiting: waits on the server to merge PR #9856"


def test_a_hold_on_a_proposal_being_co_signed_says_who_must_act() -> None:
    facts = factory_1340()
    facts.decisions = [d for d in facts.decisions if d.ref != "D-45"]
    facts.proposals = [
        dataclasses.replace(p, status="awaiting_cosign") if p.ref == "C-16" else p
        for p in facts.proposals
    ]
    tree = build(facts)

    line = _page(tree, "T-16")["status"]

    assert line["ends"] == "other"
    assert full(line) == "Waiting: held for C-16, waiting on the other lead"


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
    # Two of its projects have no cap: $26 of its cost has none to compare with.
    assert feature["rollup"]["cost"] == 51.13 and "budget" not in feature["rollup"]
    assert feature["rollup"]["burn"] == 2.5
    assert _kid(feature, "assistant-core")["burn"] == 2.5
    # The root's rate is the factory's own (it also pays for work no project owns).
    assert product["burn"] == 9.37
    assert "eta" not in core and "eta" not in feature["rollup"]  # nothing observed yet


def test_a_feature_s_cap_is_its_projects_caps_when_each_has_one() -> None:
    facts = factory_1340()
    facts.projects = [
        dataclasses.replace(p, charter={**p.charter, "budgets": {"project_usd": 50}})
        if p.slug in ("connectors-core", "skill-discovery")
        else p
        for p in facts.projects
    ]
    feature = _page(build(facts), "I-1")["rollup"]

    assert (feature["cost"], feature["budget"]) == (51.13, 400.0)


def test_burn_counts_a_turn_still_open() -> None:
    """A 40-minute turn that has not closed spends now: up to its session's cost so far."""
    from omnigent.entities.factory import FactorySpan

    facts = factory_1340()
    builder = hex_id(702)
    facts.members = [
        {**m, "cost_usd": 3.0} if m.get("session_id") == builder else m for m in facts.members
    ]
    facts.turns = [
        *facts.turns,
        FactorySpan(
            id=hex_id(7002),
            factory_id=FID,
            trace_key="state:luz",
            kind="turn",
            name="working",
            started_at=(NOW - 2400) * 1000,
            ended_at=None,
            outcome="open",
            project_slug="assistant-core",
            session_id=builder,
            attrs={"segment": "working", "cost_start": 1.66},
        ),
    ]
    tree = build(facts)

    assert _page(tree, "assistant-core")["rollup"]["burn"] == 3.84  # 2.50 + (3.00 - 1.66)
    # Hidden from a viewer, the root's rate is the sum of what they may read.
    facts.burn = None
    assert _page(build(facts), "product")["rollup"]["burn"] == 3.84


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
        "app_url": "https://app.sample.test",
        "api_url": "https://api.sample.test",
        "ref": "base-env",
    }
    assert _page(tree, "skill-discovery")["status"]["detail"] == "#9881 merged · in base env"
    # No smoke check ran, so none shows; the server's probe, when it ran, says whether it answers.
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
    assert AT_1325 < HEAD_AT < HEAD_AT + 200


def test_a_deployed_commit_the_watcher_never_saw_holds_only_itself() -> None:
    """
    The env reported ready at a commit the watcher never saw become the head:
    a merge that landed while it built (before it reported) is not counted as
    in it; only the merge that is the deployed commit is.
    """
    facts = factory_1340()
    reported = facts.base_env["env"]["reported"]["at"]
    building = dataclasses.replace(
        facts.prs[1],
        id=hex_id(9892),
        number=9892,
        task_id=None,
        merged_at=reported - 60,
        merged_sha="9892000" + "0" * 33,
    )
    itself = dataclasses.replace(
        facts.prs[1],
        id=hex_id(9893),
        number=9893,
        task_id=None,
        merged_at=reported - 120,
        merged_sha=DEPLOYED_SHA,
    )
    facts.prs = [*facts.prs, building, itself]
    facts.base_env = {**facts.base_env, "deployed": None}
    tree = build(facts)

    assert "in_base_env" not in _page(tree, "PR-9892")["node"]
    assert "in_base_env" not in _page(tree, "PR-9876")["node"]
    assert _page(tree, "PR-9893")["node"]["in_base_env"] is True
    assert _page(tree, "connectors-core")["status"]["detail"].endswith("not yet in base env")
    # Placed (the watcher saw it become the head at 13:25:30), every earlier merge is in it.
    facts.base_env = {
        **facts.base_env,
        "deployed": {"sha": DEPLOYED_SHA, "at": reported, "head_at": HEAD_AT},
    }
    assert _page(build(facts), "PR-9876")["node"]["in_base_env"] is True


def test_a_merge_into_the_same_branch_of_another_repo_is_not_in_the_base_env() -> None:
    facts = factory_1340()
    other = dataclasses.replace(
        facts.prs[1], id=hex_id(9894), number=9894, task_id=None, repo="sample-org/other-app"
    )
    facts.prs = [*facts.prs, other]

    assert "in_base_env" not in _page(build(facts), "PR-9894")["node"]


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
            "real_ask_verb": "Apply C-16",
            "ref": "T-16",
        },
    ]
    alarm = _page(tree, "D-48")
    assert [c["ref"] for c in alarm["breadcrumb"]] == ["fx", "fx:health"]
    assert alarm["status"]["detail"] == (
        "closed by the server: T-16 is held on purpose for C-16; a held task is not stalled."
        " The real ask is D-45 (Apply C-16)."
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


def _stall_alarm(facts: Any, stall: str) -> FactoryDecision:
    """D-48 open on T-16's implement loop, stalled for no progress (``idle``) or its wall."""
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
            spec={"wall_h": 6},
            spent={"stall": {"kind": stall}},
            started_at=NOW - 3 * 3600,
        )
    ]
    return alarm


def test_an_open_stall_alarm_on_a_task_held_on_purpose_is_no_ask() -> None:
    """Before the tick closes it, D-48 colours nothing and is counted nowhere."""
    facts = factory_1340()
    _stall_alarm(facts, "idle")
    tree = build(facts)

    core = _page(tree, "assistant-core")
    assert core["node"]["needs"] == {"open": 1, "blocking": 1}
    assert '"D-48"' not in json.dumps(core)
    assert full(core["status"]) == f"Not moving: {HELD}"
    # A stall on a task that is not held is a real ask.
    facts.tasks = [dataclasses.replace(t, hold=None) for t in facts.tasks]
    core = _page(build(facts), "assistant-core")
    assert core["node"]["needs"] == {"open": 2, "blocking": 2}


def test_a_wall_stall_on_a_held_task_stays_an_alarm() -> None:
    """A hold does not give a wall stall its time back: the server will not close it."""
    facts = factory_1340()
    _stall_alarm(facts, "wall")
    tree = build(facts)

    core = _page(tree, "assistant-core")

    assert core["node"]["needs"] == {"open": 2, "blocking": 2}
    assert "D-48" in [d.ref for d in tree.decisions_at["assistant-core"]]
    # The ask T-16's hold waits on still holds the most: the line ends there.
    assert full(core["status"]) == f"Not moving: {HELD}"


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
            event(
                47,
                "decision.expired",
                {"source": "loop", "cause": "T-18 runs again: progress resumed"},
                "D-47 expired: T-18 runs again: progress resumed",
            ),
            # An expiry with no cause, and a "Keep waiting?" a newer alarm replaced.
            event(
                51, "decision.expired", {"source": "loop"}, "D-51 expired: the situation cleared"
            ),
            event(
                52,
                "decision.expired",
                {"source": "loop", "superseded_by": "D-53"},
                "D-52 expired: replaced by D-53",
            ),
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
        ("D-47", "T-18 runs again: progress resumed", None, "skill-discovery"),
        ("D-46", "PR #9881 merged", None, "skill-discovery"),
    ]
    assert alarms.visible(found, {"skill-discovery"}) == []
