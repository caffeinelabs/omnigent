"""
Proof on the node pages (``node_proof``, calm rule 10: "Proof is never hidden").

Over the 13:40 sample: a change and its task show the demo of the PR's current
head (not a newer one at an older commit), a project its newest across its PRs,
a feature its acceptance run's recording; Home's feature rows carry a "▶" chip;
"n more demos" lists the subtree newest first, in pages; PARTIAL and FAIL say
what did not pass in one line. Over a real store: the page and the Demos list
come from the route, and a viewer gets only the demos of the PRs they may read.
Every name is sample data.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import events, initiatives, node_proof, nodes, snapshot
from omnigent.server.factory.node_views import NodeDemosView, validate, validate_page
from omnigent.server.factory.nodes import Tree, assert_levels
from omnigent.stores.factory_store import sqlalchemy_store as sql_store
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.node_fixtures import (
    AT_0655,
    AT_1322,
    CORE_HEAD,
    FID,
    demos_1340,
    factory_1340,
    hex_id,
)
from tests.server.factory.projects_helpers import REPO, header_cockpit, project, propose


@pytest.fixture()
def tree() -> Tree:
    return assert_levels(Tree(factory_1340()))


def _proof(tree: Tree, ref: str) -> dict[str, Any] | None:
    page = tree.page(ref)
    assert page is not None, ref
    return validate_page(page).get("proof")


# ── Which demo a page shows ────────────────────────────────────


def test_a_change_and_its_task_show_the_demo_of_the_current_head(tree: Tree) -> None:
    change = _proof(tree, "PR-9876")
    task = _proof(tree, "T-15")

    assert change is not None and task == change
    latest = change["latest"]
    # The FAIL at an earlier commit is older history; the PASS ran at the head that merged.
    assert (latest["verdict"], latest["duration"], latest["pr_tag"]) == ("PASS", "0:14", "#9876")
    assert latest["title"] == "Sessions, tool registry, flagged chat API"
    assert latest["steps"] == {"passed": 5, "total": 5} and "partial_note" not in latest
    assert latest["poster_url"] == f"/v1/factory/demos/{hex_id(906)}/files/poster6"
    assert latest["video_url"] == f"/v1/factory/demos/{hex_id(906)}/files/video6"
    assert change["count"] == 2


def test_a_newer_demo_at_an_older_commit_does_not_hide_the_current_one() -> None:
    facts = factory_1340()
    newer_old_head = dataclasses.replace(
        next(d for d in facts.demos if d.status == "FAIL"), created_at=AT_0655 + 60
    )
    demos = sorted(
        [*(d for d in facts.demos if d.id != newer_old_head.id), newer_old_head],
        key=lambda d: d.created_at,
        reverse=True,
    )
    tree = Tree(dataclasses.replace(facts, demos=demos))

    latest = (_proof(tree, "PR-9876") or {})["latest"]

    assert (latest["verdict"], latest.get("stale")) == ("PASS", None)


def test_a_change_with_no_demo_at_its_head_shows_its_newest_as_stale() -> None:
    facts = factory_1340()
    prs = [dataclasses.replace(p, head_sha="f" * 40) if p.number == 9876 else p for p in facts.prs]
    tree = Tree(dataclasses.replace(facts, prs=prs))

    latest = (_proof(tree, "PR-9876") or {})["latest"]

    assert (latest["verdict"], latest["stale"]) == ("PASS", True)


def test_a_project_shows_its_newest_across_its_prs(tree: Tree) -> None:
    drafts = _proof(tree, "build-tier-drafts-3")
    core = _proof(tree, "assistant-core")

    assert drafts is not None and core is not None
    assert (drafts["latest"]["pr_number"], drafts["latest"]["title"], drafts["count"]) == (
        9857,
        "Part 2",
        2,
    )
    assert core["latest"]["pr_number"] == 9876 and core["latest"]["verdict"] == "PASS"


def test_a_feature_shows_its_acceptance_run_and_what_was_partial(tree: Tree) -> None:
    feature = _proof(tree, "I-1")

    assert feature is not None
    latest = feature["latest"]
    assert latest["title"] == "Acceptance run 1" and latest["acceptance_run"] == 1
    assert (latest["verdict"], latest["duration"], latest["ref"]) == ("PARTIAL", "0:42", "I-1")
    assert latest["partial_note"] == "Step 6: live search fails on the base env"
    assert "pr_number" not in latest
    # The feature's demos: its acceptance run and its projects' (assistant-core's two,
    # connectors-core's and skill-discovery's).
    assert feature["count"] == 5


def test_a_feature_with_no_acceptance_run_shows_its_newest_project_demo() -> None:
    facts = factory_1340()
    tree = Tree(dataclasses.replace(facts, demos=[d for d in facts.demos if d.pr_number]))

    latest = (_proof(tree, "I-1") or {})["latest"]

    assert (latest["pr_number"], latest["title"]) == (9882, "Connector core")


def test_levels_above_the_feature_show_no_row_but_count_their_demos(tree: Tree) -> None:
    product = _proof(tree, "product")
    plain = Tree(dataclasses.replace(factory_1340(), demos=[]))

    assert product == {"count": len(demos_1340())}
    assert _proof(tree, "fx") is None
    assert _proof(plain, "I-1") is None and _proof(plain, "PR-9876") is None


def test_home_rows_carry_one_play_chip_per_feature(tree: Tree) -> None:
    home = tree.level_view("product", "feature")
    assert home is not None
    chips = {i["ref"]: i.get("demo") for g in home["groups"] for i in g["items"]}
    feature = tree.page("I-1")
    assert feature is not None

    assert chips["I-1"] == {"id": hex_id(901), "verdict": "PARTIAL", "duration": "0:42"}
    # A project shown in place of its folded implicit feature carries its own.
    assert chips["build-tier-drafts-3"] == {
        "id": hex_id(904),
        "verdict": "PASS",
        "duration": "0:11",
    }
    assert chips["feature:from-C-13"] is None
    # Rows below a feature stay one line: no chip on projects.
    assert all("demo" not in k for k in feature["children"]["items"])


# ── The Demos list ─────────────────────────────────────────────


def test_the_demos_list_is_the_subtree_newest_first_in_pages(tree: Tree) -> None:
    node = tree.lookup("I-1")
    assert node is not None

    first = validate(NodeDemosView, node_proof.demos_page(tree, node, limit=2))
    rest = validate(NodeDemosView, node_proof.demos_page(tree, node, cursor=first["next"]))

    assert first["total"] == 5 and first["latest"] == hex_id(901)
    assert [d["id"] for d in first["items"]] == [hex_id(901), hex_id(902)]
    assert [d["id"] for d in rest["items"]] == [hex_id(903), hex_id(906), hex_id(907)]
    assert "next" not in rest
    assert rest["items"][-1]["partial_note"] == "Step 4: 404 on /chat"
    with pytest.raises(ValueError, match="cursor"):
        node_proof.demos_page(tree, node, cursor="99")


def test_a_partial_note_names_what_did_not_pass() -> None:
    demo = demos_1340()[0]
    skipped = dataclasses.replace(
        demo,
        status="PARTIAL",
        manifest={
            "steps": [
                {"n": 1, "title": "Open", "status": "pass"},
                {"n": 2, "title": "Pay", "status": "skipped", "note": "no test card"},
            ]
        },
    )
    bare = dataclasses.replace(demo, status="FAIL", manifest={}, summary="Login broke. Rest ok.")

    assert node_proof.partial_note(skipped) == "Step 2 could not run: no test card"
    assert node_proof.partial_note(bare) == "Login broke"
    assert node_proof.partial_note(dataclasses.replace(demo, status="PASS")) is None
    assert node_proof.duration_words(74.6) == "1:15"


def test_demos_of_projects_the_viewer_cannot_read_are_left_out() -> None:
    tree = Tree(factory_1340(), hidden={"assistant-core"})

    feature = _proof(tree, "I-1")

    assert feature is not None and feature["count"] == 3
    assert tree.lookup("PR-9876") is None


def test_only_demos_whose_files_the_viewer_may_read_are_loaded() -> None:
    mine = dataclasses.replace(demos_1340()[1], session_id=hex_id(701))
    theirs = dataclasses.replace(demos_1340()[2], session_id=hex_id(702))
    root = demos_1340()[3]

    kept = node_proof.readable_demos([theirs, root, mine], [hex_id(701)], FID)

    assert [d.id for d in kept] == [mine.id, root.id]
    assert len(node_proof.readable_demos([theirs, root, mine], None, FID)) == 3


# ── Over a real store ──────────────────────────────────────────


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    initiatives.register()
    nodes.reset_cache()
    snapshot.reset_cache()
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value
    nodes.reset_cache()
    snapshot.reset_cache()


async def _get(cockpit: Cockpit, path: str, user: str = "alice", **params: Any) -> httpx.Response:
    return await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/n/{path}",
        params=params,
        headers={"X-Test-User": user},
    )


def _demo(cockpit: Cockpit, at: int, number: int, slug: str, **extra: Any) -> str:
    """Record a demo at epoch second *at* (the list orders by when each was recorded)."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sql_store, "now_s", lambda: at)
        row = cockpit.store.create_demo(
            factory_id=cockpit.fid,
            project_slug=slug,
            repo=REPO,
            pr_number=number,
            head_sha=extra.pop("head_sha", "a" * 40),
            session_id=extra.pop("session_id", cockpit.fid),
            created_by_session=cockpit.world.id("lead_a"),
            files=[
                {"file_id": "p1", "name": "poster.png", "kind": "poster", "bytes": 1},
                {"file_id": "v1", "name": "demo.webm", "kind": "video", "bytes": 1},
            ],
            **extra,
        )
    return row.id


async def test_the_page_and_the_demos_list_over_a_real_store(cockpit: Cockpit) -> None:
    message = cockpit.store.append_chat(
        cockpit.fid,
        thread="factory",
        author_kind="human",
        author="alice",
        author_role="owner",
        body="Build an in-app helper (sample).",
    )
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("core")},
        reply_to=f"chat_{message.id}",
        thread="factory",
        analysis={
            "initiative": {
                "title": "Sample helper",
                "acceptance": [{"step": "Open the panel", "expect": "it opens", "owner": "core"}],
                "skeleton": "core",
                "user_facing": ["core"],
            }
        },
    )
    assert resp.json()["status"] == "applied", resp.text
    pr, _ = cockpit.store.upsert_pr(
        cockpit.fid, REPO, 9001, project_slug="core", state="open", title="Core API"
    )
    cockpit.store.update_pr(pr.id, head_sha="a" * 40)
    now = int(time.time())
    old = _demo(cockpit, now - 400, 9001, "core", status="FAIL", title="Core API, first try")
    new = _demo(
        cockpit,
        now - 300,
        9001,
        "core",
        title="Core API answers",
        manifest={"duration_s": 14.2},
        pr_id=pr.id,
    )
    run = _demo(
        cockpit,
        now - 200,
        0,
        "core",
        status="PARTIAL",
        title="Acceptance I-1 run 1",
        manifest={
            "acceptance": {"initiative": "I-1", "run": 1},
            "steps": [{"n": 1, "title": "Open the panel", "status": "fail"}],
        },
    )
    hidden = _demo(
        cockpit,
        now - 100,
        9002,
        "api-version",
        title="Version route",
        session_id=cockpit.world.id("pl"),
    )
    cockpit.store.upsert_pr(cockpit.fid, REPO, 9002, project_slug="api-version")
    nodes.reset_cache()

    core = (await _get(cockpit, "core")).json()
    feature = validate_page((await _get(cockpit, "I-1")).json())
    listed = await _get(cockpit, "product/demos", limit=2)
    more = await _get(cockpit, "product/demos", cursor=listed.json().get("next"))
    missing = await _get(cockpit, "T-404/demos")
    bad = await _get(cockpit, "product/demos", cursor="x")
    cockpit.permissions.ensure_user("dave")
    cockpit.permissions.grant("dave", cockpit.fid, LEVEL_READ)
    reader = await _get(cockpit, "product/demos", user="dave")

    assert validate_page(core)["proof"]["latest"]["id"] == new
    assert core["proof"]["latest"]["duration"] == "0:14" and core["proof"]["count"] == 2
    assert feature["proof"]["latest"]["id"] == run
    assert feature["proof"]["latest"]["partial_note"] == "Step 1: Open the panel"
    assert listed.status_code == 200, listed.text
    page = validate(NodeDemosView, listed.json())
    assert [d["id"] for d in page["items"]] == [hidden, run] and page["total"] == 4
    assert [d["id"] for d in validate(NodeDemosView, more.json())["items"]] == [new, old]
    assert (missing.status_code, error_code(missing)) == (404, "unknown_node")
    assert (bad.status_code, error_code(bad)) == (400, "bad_cursor")
    # The reader cannot read api-version's lead: its PRs and their demos are left out.
    assert reader.status_code == 200
    assert hidden not in [d["id"] for d in reader.json()["items"]]
    assert reader.json()["total"] == 3


async def test_a_new_demo_shows_on_the_next_read(cockpit: Cockpit) -> None:
    cockpit.store.upsert_pr(cockpit.fid, REPO, 9003, project_slug="api-version", title="Route")
    before = validate_page((await _get(cockpit, "api-version")).json())
    _demo(cockpit, int(time.time()), 9003, "api-version", title="Route answers")
    await events.emit(
        cockpit.app.state, cockpit.fid, "demo.published", subject="pr:9003", summary="demo PASS"
    )
    after = validate_page((await _get(cockpit, "api-version")).json())

    assert "proof" not in before
    assert after["proof"]["latest"]["title"] == "Route answers"


def test_the_sample_heads_are_what_the_fixture_says() -> None:
    facts = factory_1340()
    core = next(p for p in facts.prs if p.number == 9876)

    assert core.head_sha == CORE_HEAD
    assert max(d.created_at for d in facts.demos) == AT_1322
