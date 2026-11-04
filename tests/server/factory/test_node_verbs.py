"""
Structural verbs at a node: ``POST …/n/{ref}/preview|apply`` and ``POST …/undo/{token}``.

A person's ask planned as one initiative (``I-1``): ``core`` builds (one task
merged as #9001, one still open) and ``panel`` waits on ``core``; a third
project is held for a later wave. Each verb's preview is what its apply does,
Undo takes it back, a write over facts that moved since the preview is
refused, a move skips no level, and Start now relaxes the panel's wait to the
change it needs (``PR-9001 · met``). Every name is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import initiatives, node_verbs, nodes, snapshot
from omnigent.server.factory.charter import normalize_charter
from omnigent.server.factory.node_views import ChildPageView, validate_page
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import REPO, header_cockpit, project, propose

ASK = "Build an in-app helper (sample): a side panel and a core behind it."


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    initiatives.register()
    nodes.reset_cache()
    snapshot.reset_cache()
    check = nodes.check_levels

    def strict(tree: nodes.Tree) -> list[str]:
        """Every projection keeps one parent, one level up (or the test fails)."""
        bad = check(tree)
        assert not bad, bad
        return bad

    monkeypatch.setattr(nodes, "check_levels", strict)
    async for value in header_cockpit(world, db_uri, monkeypatch):
        await _plan(value)
        yield value
    nodes.reset_cache()
    snapshot.reset_cache()


async def _plan(cockpit: Cockpit) -> None:
    message = cockpit.store.append_chat(
        cockpit.fid,
        thread="factory",
        author_kind="human",
        author="alice",
        author_role="owner",
        body=ASK,
    )
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("core")},
        {
            "op": "create",
            "project": project(
                "panel", depends_on=["core"], depends_why={"core": "calls the core's API"}
            ),
        },
        reply_to=f"chat_{message.id}",
        thread="factory",
        analysis={
            "initiative": {
                "title": "Sample helper",
                "acceptance": [{"step": "Ask the core", "expect": "it answers", "owner": "core"}],
                "skeleton": "core",
                "user_facing": ["panel"],
                "why_wait": {"panel": "it shows what the core answers"},
                "waves": [{"n": 2, "goal": "reach ad accounts", "projects": ["ads-sync"]}],
            }
        },
    )
    assert resp.json()["status"] == "applied", resp.text
    done, _ = cockpit.store.upsert_task(cockpit.fid, "core", "api", title="Core API")
    pr, _ = cockpit.store.upsert_pr(
        cockpit.fid, REPO, 9001, project_slug="core", state="merged", task_id=done.id
    )
    cockpit.store.update_task(done.id, state="done", pr_id=pr.id)
    cockpit.store.upsert_task(cockpit.fid, "core", "cache", title="Core cache")


def _url(cockpit: Cockpit, ref: str, what: str) -> str:
    return f"/v1/factory/factories/{cockpit.fid}/n/{ref}/{what}"


async def _page(cockpit: Cockpit, ref: str, user: str = "alice") -> dict[str, Any]:
    nodes.reset_cache()
    snapshot.reset_cache()
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/n/{ref}", headers={"X-Test-User": user}
    )
    assert resp.status_code == 200, resp.text
    return validate_page(resp.json())


async def _preview(
    cockpit: Cockpit, ref: str, verb: str, user: str = "alice", **args: Any
) -> dict[str, Any]:
    resp = await cockpit.post(
        _url(cockpit, ref, "preview"), {"verb": verb, "args": args}, user=user
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _apply(
    cockpit: Cockpit, ref: str, preview: dict[str, Any], user: str = "alice"
) -> dict[str, Any]:
    resp = await cockpit.post(
        _url(cockpit, ref, "apply"),
        {
            "verb": preview["verb"],
            "args": preview["args"],
            "based_on": preview["based_on"],
            "preview_id": preview["preview_id"],
        },
        user=user,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate_page(body["result"])
    return body


async def _undo(cockpit: Cockpit, token: str, user: str = "alice") -> Any:
    return await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/undo/{token}", user=user)


def _charter(cockpit: Cockpit, slug: str) -> dict[str, Any]:
    row = cockpit.store.get_project(cockpit.fid, slug)
    assert row is not None
    return normalize_charter(row.charter)


# ── add ────────────────────────────────────────────────────────


async def test_adding_a_project_previews_its_ghost_and_applies_it(cockpit: Cockpit) -> None:
    preview = await _preview(cockpit, "I-1", "add", title="Usage report")

    ChildPageView.model_validate(preview["columns_after"])
    ghost = preview["columns_after"]["ghosts"][-1]
    assert (ghost["ref"], ghost["state"], ghost["col"]) == ("usage-report", "ghost", 0)
    assert preview["approver"] == "you" and preview["can_apply"] is True
    assert any("Plan Usage report now" in e for e in preview["effects"])
    assert preview["rollup_delta"]["nodes"][0]["items"][0]["after"] == 3

    applied = await _apply(cockpit, "I-1", preview)

    feature = applied["result"]
    assert applied["filed"] and applied["undo"]["token"]
    assert "usage-report" in [i["ref"] for i in feature["children"]["items"]]
    row = cockpit.store.get_project(cockpit.fid, "usage-report")
    assert row is not None and row.feature == "I-1"
    # "Plan Usage report now" went to the feature's thread for its responder.
    said = cockpit.store.list_chat(cockpit.fid, "feature:I-1", limit=20)
    assert any(m.body.startswith("Plan Usage report now") for m in said)
    cards = [m for m in said if (m.card or {}).get("type") == "preview_applied"]
    assert cards and cards[-1].card["meta"]["undo_token"] == applied["undo"]["token"]


async def test_adding_a_held_item_pulls_it_forward_instead_of_copying_it(
    cockpit: Cockpit,
) -> None:
    before = await _page(cockpit, "I-1")
    assert [i["ref"] for i in before["children"]["later"]] == ["ads-sync"]

    preview = await _preview(cockpit, "I-1", "add", title="ads sync")

    assert preview["args"]["slug"] == "ads-sync"
    assert "pulls it forward" in preview["effects"][0]
    assert preview["columns_after"]["later"] == []
    later = next(i for i in preview["rollup_delta"]["nodes"][0]["items"] if i["key"] == "later")
    assert (later["before"], later["after"]) == (1, 0)

    applied = await _apply(cockpit, "I-1", preview)

    assert applied["result"]["children"]["later"] == []
    assert "ads-sync" in [i["ref"] for i in applied["result"]["children"]["items"]]


async def test_holding_an_item_for_later_and_undo(cockpit: Cockpit) -> None:
    preview = await _preview(
        cockpit, "I-1", "add", title="Exports", col="later", held_for="after the panel ships"
    )
    applied = await _apply(cockpit, "I-1", preview)

    assert "exports" in [i["ref"] for i in applied["result"]["children"]["later"]]
    assert cockpit.store.get_project(cockpit.fid, "exports") is None

    undone = await _undo(cockpit, applied["undo"]["token"])
    after = await _page(cockpit, "I-1")

    assert undone.status_code == 200, undone.text
    assert "exports" not in [i["ref"] for i in after["children"]["later"]]


async def test_adding_a_task_and_undo_cancels_it(cockpit: Cockpit) -> None:
    preview = await _preview(cockpit, "core", "add", title="Rate limit")
    applied = await _apply(cockpit, "core", preview)

    task = cockpit.store.get_task_by_key(cockpit.fid, "core", "rate-limit")
    assert task is not None and task.state != "cancelled"
    assert task.ref in [i["ref"] for i in applied["result"]["children"]["items"]]

    undone = await _undo(cockpit, applied["undo"]["token"])

    assert undone.status_code == 200, undone.text
    assert cockpit.store.get_task(task.id).state == "cancelled"


async def test_undo_after_the_lead_started_cancels_the_project(cockpit: Cockpit) -> None:
    preview = await _preview(cockpit, "I-1", "add", title="Usage report")
    applied = await _apply(cockpit, "I-1", preview)

    undone = await _undo(cockpit, applied["undo"]["token"])
    again = await _undo(cockpit, applied["undo"]["token"])

    # Header auth mints no runner token: Atlas was asked to spawn its lead, so it
    # counts as started and Undo cancels it.
    assert undone.status_code == 200, undone.text
    assert "cancelled" in undone.json()["line"]
    row = cockpit.store.get_project(cockpit.fid, "usage-report")
    assert row is not None and row.status == "cancelled"
    assert (again.status_code, error_code(again)) == (409, "undo_closed")


async def test_undo_of_a_held_project_takes_it_back(cockpit: Cockpit) -> None:
    preview = await _preview(cockpit, "I-1", "add", title="Usage report", depends_on=["panel"])
    assert preview["columns_after"]["ghosts"][-1]["col"] == 2  # after panel (Then)

    applied = await _apply(cockpit, "I-1", preview)
    undone = await _undo(cockpit, applied["undo"]["token"])

    assert undone.status_code == 200, undone.text
    assert cockpit.store.get_project(cockpit.fid, "usage-report") is None


# ── start now ──────────────────────────────────────────────────


async def test_start_now_turns_the_panels_wait_into_the_merged_change(cockpit: Cockpit) -> None:
    before = await _page(cockpit, "panel")
    assert any(c.get("ref") == "core" for c in before["chips"])

    preview = await _preview(cockpit, "panel", "start")

    assert preview["args"]["depends_on"] == ["PR-9001"]
    assert any("needs only PR-9001 · merged" in e for e in preview["effects"])
    assert preview["undo"] is True

    applied = await _apply(cockpit, "panel", preview)
    page = await _page(cockpit, "panel")

    assert _charter(cockpit, "panel")["depends_on"] == ["PR-9001"]
    chip = next(c for c in page["chips"] if c.get("title") == "PR-9001")
    assert (chip["kind"], chip["state"], chip["ref"]) == ("depends on", "met", "PR-9001")
    assert not any(c.get("ref") == "core" and c.get("kind") == "depends on" for c in page["chips"])
    assert "core" not in str(page["status"]["segments"])
    assert applied["filed"]

    undone = await _undo(cockpit, applied["undo"]["token"])

    assert undone.status_code == 200, undone.text
    assert _charter(cockpit, "panel")["depends_on"] == ["core"]


# ── stale, levels, authority ───────────────────────────────────


async def test_a_write_over_facts_that_moved_since_the_preview_is_refused(
    cockpit: Cockpit,
) -> None:
    preview = await _preview(cockpit, "I-1", "add", title="Usage report")
    # Someone else adds a project to the same feature meanwhile.
    other = await _preview(cockpit, "I-1", "add", title="Billing")
    await _apply(cockpit, "I-1", other)

    resp = await cockpit.post(
        _url(cockpit, "I-1", "apply"),
        {"verb": "add", "args": preview["args"], "preview_id": preview["preview_id"]},
    )

    assert (resp.status_code, error_code(resp)) == (409, "stale")
    fresh = resp.json()["preview"]
    assert fresh["preview_id"] != preview["preview_id"]
    assert cockpit.store.get_project(cockpit.fid, "usage-report") is None


async def test_apply_needs_a_preview(cockpit: Cockpit) -> None:
    resp = await cockpit.post(
        _url(cockpit, "I-1", "apply"), {"verb": "add", "args": {"title": "X"}}
    )

    assert (resp.status_code, error_code(resp)) == (400, "preview_required")


async def test_a_move_skips_no_level(cockpit: Cockpit) -> None:
    feature = await _page(cockpit, "I-1")
    need = feature["breadcrumb"][-1]["ref"]

    two_up = await cockpit.post(
        _url(cockpit, "core", "preview"), {"verb": "move", "args": {"to": need}}
    )
    root = await cockpit.post(
        _url(cockpit, "core", "preview"), {"verb": "move", "args": {"to": "product"}}
    )
    sideways = await cockpit.post(
        _url(cockpit, "core", "preview"), {"verb": "move", "args": {"to": "panel"}}
    )

    assert (two_up.status_code, error_code(two_up)) == (422, "wrong_level")
    assert two_up.json()["level"] == "feature"
    assert (root.status_code, error_code(root)) == (422, "wrong_level")
    assert (sideways.status_code, error_code(sideways)) == (422, "wrong_level")


async def test_moving_a_project_under_another_feature_and_back(cockpit: Cockpit) -> None:
    other = cockpit.store.create_initiative(
        cockpit.fid, title="Second (sample)", ask="Another outcome", plan={"projects": []}
    )

    preview = await _preview(cockpit, "panel", "move", to=other.ref)
    applied = await _apply(cockpit, "panel", preview)
    moved = await _page(cockpit, "panel")

    assert [c["ref"] for c in moved["breadcrumb"]][-1] == other.ref
    assert cockpit.store.get_project(cockpit.fid, "panel").feature == other.ref
    assert (
        "panel"
        not in initiatives.plan_of(cockpit.store.get_initiative_by_number(cockpit.fid, 1))[
            "projects"
        ]
    )

    undone = await _undo(cockpit, applied["undo"]["token"])
    back = await _page(cockpit, "panel")

    assert undone.status_code == 200, undone.text
    assert [c["ref"] for c in back["breadcrumb"]][-1] == "I-1"


async def test_an_editor_may_not_apply_an_owners_verb(cockpit: Cockpit) -> None:
    feature = await _page(cockpit, "I-1")
    need = feature["breadcrumb"][-1]["ref"]

    preview = await _preview(cockpit, need, "add", user="carol", title="Second outcome")
    resp = await cockpit.post(
        _url(cockpit, need, "apply"),
        {"verb": "add", "args": preview["args"], "preview_id": preview["preview_id"]},
        user="carol",
    )

    assert preview["can_apply"] is False and preview["approver"] == "an owner"
    assert (resp.status_code, error_code(resp)) == (403, "authority")
    assert resp.json()["who"] == "an owner"


async def test_a_reader_previews_but_never_applies(cockpit: Cockpit) -> None:
    cockpit.permissions.ensure_user("dave")
    cockpit.permissions.grant("dave", cockpit.fid, LEVEL_READ)

    preview = await _preview(cockpit, "I-1", "add", user="dave", title="Usage report")
    resp = await cockpit.post(
        _url(cockpit, "I-1", "apply"),
        {"verb": "add", "args": preview["args"], "preview_id": preview["preview_id"]},
        user="dave",
    )

    assert preview["can_apply"] is False
    assert resp.status_code == 403


async def test_only_who_applied_it_or_an_owner_undoes(cockpit: Cockpit) -> None:
    preview = await _preview(cockpit, "core", "add", title="Rate limit")
    applied = await _apply(cockpit, "core", preview)

    other = await _undo(cockpit, applied["undo"]["token"], user="carol")
    unknown = await _undo(cockpit, "0" * 24)

    assert (other.status_code, error_code(other)) == (403, "authority")
    assert (unknown.status_code, error_code(unknown)) == (404, "unknown_undo")


# ── pause, reorder, name ───────────────────────────────────────


async def test_pausing_a_project_and_undo_resumes_it(cockpit: Cockpit) -> None:
    preview = await _preview(cockpit, "api-version", "pause")
    applied = await _apply(cockpit, "api-version", preview)

    assert [e.kind for e in cockpit.events("control.pause")]
    assert any("$0/h" in e or "pause" in e for e in preview["effects"])

    undone = await _undo(cockpit, applied["undo"]["token"])

    assert undone.status_code == 200, undone.text
    assert [e.kind for e in cockpit.events("control.resume")]


async def test_reordering_a_project_and_undo(cockpit: Cockpit) -> None:
    preview = await _preview(cockpit, "panel", "reorder", col=0)

    assert preview["columns_after"]["ghosts"][-1]["ref"] == "panel"
    assert preview["columns_after"]["ghosts"][-1]["col"] == 0

    applied = await _apply(cockpit, "panel", preview)

    assert _charter(cockpit, "panel").get("depends_on") in (None, [])
    undone = await _undo(cockpit, applied["undo"]["token"])
    assert undone.status_code == 200, undone.text
    assert _charter(cockpit, "panel")["depends_on"] == ["core"]


async def test_naming_an_implicit_feature_and_undo(cockpit: Cockpit) -> None:
    seeded = await _page(cockpit, "api-version")
    implicit = next(c for c in seeded["breadcrumb"] if c["level"] == "feature")
    assert implicit["implicit"] is True

    preview = await _preview(cockpit, implicit["ref"], "name", title="Versioning")
    applied = await _apply(cockpit, implicit["ref"], preview)
    named = await _page(cockpit, "api-version")

    feature = next(c for c in named["breadcrumb"] if c["level"] == "feature")
    assert feature["title"] == "Versioning" and not feature.get("implicit")
    assert [c["ref"] for c in named["breadcrumb"]][:2] == [c["ref"] for c in seeded["breadcrumb"]][
        :2
    ]

    undone = await _undo(cockpit, applied["undo"]["token"])
    back = await _page(cockpit, "api-version")

    assert undone.status_code == 200, undone.text
    assert next(c for c in back["breadcrumb"] if c["level"] == "feature")["implicit"] is True


async def test_each_verbs_preview_is_what_its_apply_does(cockpit: Cockpit) -> None:
    """The preview id names the plan: applying it twice over the same facts is one write."""
    preview = await _preview(cockpit, "core", "add", title="Docs")
    again = await _preview(cockpit, "core", "add", title="Docs")

    assert preview["preview_id"] == again["preview_id"]
    applied = await _apply(cockpit, "core", preview)
    row = cockpit.store.get_task_by_key(cockpit.fid, "core", "docs")
    assert row is not None and row.title == "Docs"
    assert applied["line"].endswith("to core.")
    # The same preview over the facts it changed no longer applies.
    resp = await cockpit.post(
        _url(cockpit, "core", "apply"),
        {"verb": "add", "args": preview["args"], "preview_id": preview["preview_id"]},
    )
    assert resp.status_code in (409,), resp.text


async def test_unknown_verbs_and_nodes(cockpit: Cockpit) -> None:
    verb = await cockpit.post(_url(cockpit, "core", "preview"), {"verb": "fly", "args": {}})
    node = await cockpit.post(_url(cockpit, "T-404", "preview"), {"verb": "add", "args": {}})
    here = await cockpit.post(_url(cockpit, "PR-9001", "preview"), {"verb": "add", "args": {}})

    assert (verb.status_code, error_code(verb)) == (400, "unknown_verb")
    assert (node.status_code, error_code(node)) == (404, "unknown_node")
    assert (here.status_code, error_code(here)) == (422, "not_here")


def test_slugs_from_titles() -> None:
    assert node_verbs.slugify("Usage report (v2)") == "usage-report-v2"
    assert node_verbs.slugify("  !!  ") == "untitled"


async def test_filing_an_implicit_features_projects_under_a_feature_and_undo(
    cockpit: Cockpit,
) -> None:
    seeded = await _page(cockpit, "api-version")
    implicit = next(c for c in seeded["breadcrumb"] if c["level"] == "feature")

    two_up = await cockpit.post(
        _url(cockpit, implicit["ref"], "preview"),
        {"verb": "name", "args": {"to": "product"}},
    )
    preview = await _preview(cockpit, implicit["ref"], "name", to="I-1")
    applied = await _apply(cockpit, implicit["ref"], preview)
    filed = await _page(cockpit, "api-version")

    assert (two_up.status_code, error_code(two_up)) == (422, "wrong_level")
    assert applied["filed"] and filed["breadcrumb"][-1]["ref"] == "I-1"
    assert cockpit.store.get_project(cockpit.fid, "api-version").feature == "I-1"

    undone = await _undo(cockpit, applied["undo"]["token"])
    back = await _page(cockpit, "api-version")

    assert undone.status_code == 200, undone.text
    assert cockpit.store.get_project(cockpit.fid, "api-version").feature is None
    assert next(c for c in back["breadcrumb"] if c["level"] == "feature")["implicit"] is True


# ── The projects proposal ops agents file: move and reorder ────


async def test_agents_file_moves_and_reorders_as_projects_proposals(cockpit: Cockpit) -> None:
    other = cockpit.store.create_initiative(
        cockpit.fid, title="Second (sample)", ask="Another outcome", plan={"projects": []}
    )

    moved = await propose(cockpit, {"op": "move", "slug": "panel", "to": other.ref.lower()})
    unknown = await propose(cockpit, {"op": "move", "slug": "panel", "to": "I-99"})
    reorder = await propose(cockpit, {"op": "reorder", "slug": "panel", "reason": "first"})
    stray = await propose(cockpit, {"op": "reorder", "slug": "panel", "after": "api-version"})

    assert moved.status_code in (200, 201), moved.text
    row = cockpit.store.get_proposal_by_number(
        cockpit.fid, int(moved.json()["proposal"].removeprefix("C-"))
    )
    assert row is not None and row.ops == [{"op": "move", "slug": "panel", "to": other.ref}]
    assert (unknown.status_code, error_code(unknown)) == (404, "unknown_feature")
    assert reorder.status_code in (200, 201), reorder.text
    again = cockpit.store.get_proposal_by_number(
        cockpit.fid, int(reorder.json()["proposal"].removeprefix("C-"))
    )
    assert again is not None and again.ops[0]["op"] == "update"
    assert again.ops[0]["patch"]["depends_on"] == []
    assert (stray.status_code, error_code(stray)) == (400, "invalid_body")
