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

import re
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import chat, definition, initiatives, node_verbs, nodes, snapshot
from omnigent.server.factory.charter import normalize_charter
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.initiative_view import _next_wave
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


# ── Each verb's preview is its apply, and Undo puts the rows back ──


def _rows(cockpit: Cockpit) -> dict[str, Any]:
    """The stored structure a verb changes (closed rows left out: an Undo may close)."""
    store, fid = cockpit.store, cockpit.fid
    live = {p.slug: p for p in store.list_projects(fid) if p.status == "active"}
    resolved = definition.definition_for(cockpit.deps, fid, fresh=True)
    return {
        "projects": {
            s: (p.feature, tuple(_charter(cockpit, s).get("depends_on") or []))
            for s, p in live.items()
        },
        "tasks": {t.ref: bool(t.hold) for t in store.list_tasks(fid) if t.state != "cancelled"},
        "features": {
            r.ref: (
                initiatives.plan_of(r).get("opportunity"),
                sorted(s for s in initiatives.plan_of(r)["projects"] if s in live),
                [
                    (int(w.get("n") or 0), list(w.get("projects") or []))
                    for w in initiatives.plan_of(r)["waves"]
                ],
            )
            for r in store.list_initiatives(fid, limit=100)
            if r.status == "open"
        },
        "needs": sorted(o.ref for o in store.list_opportunities(fid) if not o.kill),
        "filed": {
            p.ref: p.opportunity for p in store.list_proposals(fid, limit=200) if p.opportunity
        },
        "team": ((resolved.doc if resolved else {}) or {}).get("team"),
    }


async def _maybe_page(cockpit: Cockpit, ref: str) -> dict[str, Any] | None:
    """The node page of *ref*, or ``None`` once the verb made it go (named, closed)."""
    nodes.reset_cache()
    snapshot.reset_cache()
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/n/{ref}", headers={"X-Test-User": "alice"}
    )
    return validate_page(resp.json()) if resp.status_code == 200 else None


def _counts(page: dict[str, Any]) -> dict[str, int]:
    items = page["children"]["items"]
    return {
        "projects": sum(1 for i in items if i.get("level") == "project"),
        "features": sum(1 for i in items if i.get("level") == "feature"),
        "later": len(page["children"]["later"]),
    }


async def _lone_proposal(cockpit: Cockpit) -> str:
    """A project filed with no initiative: its feature and need are implicit, from C-n."""
    resp = await propose(
        cockpit,
        {
            "op": "create",
            "project": project(
                "lone", depends_on=["core"], depends_why={"core": "reads the core's API"}
            ),
        },
        analysis={"initiative": {"none": "a separate outcome (sample)"}},
    )
    ref = str(resp.json()["proposal"])
    applied = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/{ref}/apply")
    assert applied.status_code == 200, applied.text
    return ref


async def _opportunity(cockpit: Cockpit) -> str:
    row = cockpit.store.create_opportunity(
        cockpit.fid,
        title="Answers in the app (sample)",
        statement="When I am stuck, I want an answer where I work.",
        created_by="alice",
        created_kind="human",
    )
    return str(row.ref)


async def _need_of(cockpit: Cockpit, ref: str) -> str:
    page = await _page(cockpit, ref)
    return next(c["ref"] for c in page["breadcrumb"] if c["level"] == "need")


async def _ref(cockpit: Cockpit, setup: str) -> tuple[str, dict[str, Any]]:
    """Where a case runs and with what args, after its setup."""
    if setup == "need":
        return await _need_of(cockpit, "I-1"), {"title": "Second outcome (sample)"}
    if setup == "move-feature":
        return "I-1", {"to": await _opportunity(cockpit)}
    if setup == "name-need":
        proposal = await _lone_proposal(cockpit)
        return f"need:from-{proposal}", {"title": "Lone work (sample)"}
    if setup == "file-need":
        proposal = await _lone_proposal(cockpit)
        return f"need:from-{proposal}", {"to": await _opportunity(cockpit)}
    if setup == "move-project":
        other = cockpit.store.create_initiative(
            cockpit.fid, title="Second (sample)", ask="Another outcome", plan={"projects": []}
        )
        return "panel", {"to": other.ref}
    if setup == "task":
        task = cockpit.store.get_task_by_key(cockpit.fid, "core", "cache")
        assert task is not None
        return task.ref, {}
    raise AssertionError(setup)


_TEAM = {"changes": [{"op": "replace", "path": "team.projects.core.builders_at_once", "value": 2}]}


@pytest.mark.parametrize(
    ("ref", "verb", "args"),
    [
        pytest.param("I-1", "add", {"title": "Usage report", "depends_on": ["panel"]}, id="add"),
        pytest.param(
            "I-1", "add", {"title": "ads sync", "depends_on": ["panel"]}, id="pull-forward"
        ),
        pytest.param("I-1", "add", {"title": "Exports", "col": "later"}, id="hold"),
        pytest.param("core", "add", {"title": "Rate limit"}, id="add-task"),
        pytest.param("@need", "add", {}, id="add-feature"),
        pytest.param("panel", "start", {}, id="start"),
        pytest.param("panel", "reorder", {"col": 0}, id="reorder"),
        pytest.param("@move-project", "move", {}, id="move-project"),
        pytest.param("@move-feature", "move", {}, id="move-feature"),
        pytest.param("I-1", "close", {}, id="close-feature"),
        pytest.param("feature:by-server", "name", {"title": "Versioning"}, id="name-feature"),
        pytest.param("feature:by-server", "name", {"to": "I-1"}, id="file-feature"),
        pytest.param("@name-need", "name", {}, id="name-need"),
        pytest.param("@file-need", "name", {}, id="file-need"),
        pytest.param("@task", "pause", {}, id="pause-task"),
        pytest.param("core", "team", _TEAM, id="team"),
    ],
)
async def test_each_verbs_preview_is_its_apply_and_undo_restores_the_rows(
    cockpit: Cockpit, ref: str, verb: str, args: dict[str, Any]
) -> None:
    if ref.startswith("@"):
        ref, more = await _ref(cockpit, ref[1:])
        args = {**args, **more}
    before = _rows(cockpit)

    preview = await _preview(cockpit, ref, verb, **args)
    applied = await _apply(cockpit, ref, preview)
    changed = _rows(cockpit)

    assert preview["effects"] and all(preview["effects"])
    assert changed != before, "the apply wrote nothing"
    delta = preview["rollup_delta"]
    assert delta is None or delta["nodes"] or delta["text"]
    for node in (delta or {}).get("nodes") or []:
        shown = await _maybe_page(cockpit, node["ref"])
        counts = _counts(shown) if shown else {}
        for item in node["items"]:
            if item["key"] in counts:
                assert counts[item["key"]] == item["after"], (node["ref"], item)
    # Each ghost sits where the apply put it: in its parent's columns, or held.
    parents = [
        ref,
        str(args.get("to") or ""),
        *(c["ref"] for c in applied["result"]["breadcrumb"]),
    ]
    pages = [p for r in dict.fromkeys(parents) if r and (p := await _maybe_page(cockpit, r))]
    for ghost in (preview["columns_after"] or {}).get("ghosts") or []:
        if ghost["state"] != "ghost" and ghost["ref"] != ref:
            continue  # an existing ghost of the parent, not this verb's
        for page in pages:
            placed = {i["ref"]: i.get("col") or 0 for i in page["children"]["items"]}
            held = {i["ref"] for i in page["children"]["later"]}
            if ghost["col"] == "later" and ghost["ref"] in held:
                break
            if ghost["ref"] in placed:
                assert placed[ghost["ref"]] == ghost["col"], (page["node"]["ref"], ghost)
                break
    assert applied["undo"], "every verb here can be undone"

    undone = await _undo(cockpit, applied["undo"]["token"])

    assert undone.status_code == 200, undone.text
    assert _rows(cockpit) == before


# ── Regressions: what a preview says is what applies ───────────


async def test_a_reorder_after_a_named_sibling_previews_the_column_it_lands_in(
    cockpit: Cockpit,
) -> None:
    await _apply(cockpit, "I-1", await _preview(cockpit, "I-1", "add", title="Extra"))

    preview = await _preview(cockpit, "extra", "reorder", col=2, after="core")
    ghost = preview["columns_after"]["ghosts"][-1]
    await _apply(cockpit, "extra", preview)
    page = await _page(cockpit, "I-1")

    assert (ghost["ref"], ghost["col"], preview["args"]["col"]) == ("extra", 1, 1)
    assert any("lands in Then" in w for w in preview["warnings"])
    assert next(i for i in page["children"]["items"] if i["ref"] == "extra")["col"] == 1


async def test_a_reorder_to_a_column_with_two_siblings_names_one(cockpit: Cockpit) -> None:
    await _apply(cockpit, "I-1", await _preview(cockpit, "I-1", "add", title="Extra"))
    await _apply(cockpit, "I-1", await _preview(cockpit, "I-1", "add", title="Other"))

    resp = await cockpit.post(
        _url(cockpit, "panel", "preview"), {"verb": "reorder", "args": {"col": 1}}
    )

    assert (resp.status_code, error_code(resp)) == (400, "invalid_args")
    assert resp.json()["field"] == "args.after"


async def test_adds_keep_the_features_later_waves_ahead(cockpit: Cockpit) -> None:
    """v2's next wave and v3's Later agree after an add, a pull-forward and a hold."""

    async def both() -> tuple[Any, list[str]]:
        row = cockpit.store.get_initiative_by_number(cockpit.fid, 1)
        wave = _next_wave(initiatives.plan_of(row))
        later = [i["ref"] for i in (await _page(cockpit, "I-1"))["children"]["later"]]
        return (wave["n"], wave["projects"]) if wave else None, later

    assert await both() == ((2, ["ads-sync"]), ["ads-sync"])

    await _apply(cockpit, "I-1", await _preview(cockpit, "I-1", "add", title="Usage report"))
    assert await both() == ((2, ["ads-sync"]), ["ads-sync"])

    await _apply(cockpit, "I-1", await _preview(cockpit, "I-1", "add", title="ads sync"))
    assert await both() == (None, [])

    held = await _preview(cockpit, "I-1", "add", title="Exports", col="later", held_for="later")
    await _apply(cockpit, "I-1", held)
    assert await both() == ((2, ["exports"]), ["exports"])


async def test_undo_of_the_team_verb_never_drops_a_later_definition_change(
    cockpit: Cockpit,
) -> None:
    applied = await _apply(cockpit, "core", await _preview(cockpit, "core", "team", **_TEAM))
    version = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True).version
    later = await cockpit.client.put(
        f"/v1/factory/factories/{cockpit.fid}/definition",
        json={
            "changes": [
                {"op": "replace", "path": "team.projects.panel.builders_at_once", "value": 3}
            ],
            "base_version": version,
        },
        headers={"X-Test-User": "alice"},
    )
    assert later.status_code == 200, later.text

    undone = await _undo(cockpit, applied["undo"]["token"])

    team = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True).doc["team"]
    assert (undone.status_code, error_code(undone)) == (409, "undo_closed")
    assert team["projects"]["panel"]["builders_at_once"] == 3


async def test_every_verb_a_page_offers_is_served_there(cockpit: Cockpit) -> None:
    task = cockpit.store.get_task_by_key(cockpit.fid, "core", "cache")
    assert task is not None
    refs = ["product", await _need_of(cockpit, "I-1"), "I-1", "core", "panel", task.ref]
    args = {
        "add": {"title": "Sample"},
        "move": {"to": "I-1"},
        "name": {"title": "Sample"},
        "auto": {"auto_merge": True},
    }
    for ref in refs:
        page = await _page(cockpit, ref)
        for verb in [v["verb"] for v in page["verbs"]]:
            if verb in ("talk", "research"):
                continue  # the chat; S14 serves research
            resp = await cockpit.post(
                _url(cockpit, ref, "preview"), {"verb": verb, "args": args.get(verb, {})}
            )
            # Served: a plan, or the verb's own answer for this node right now.
            assert resp.status_code == 200 or error_code(resp) in (
                "nothing_to_do",
                "invalid_args",
                "wrong_level",
            ), (ref, verb, resp.text)
    product = [v["verb"] for v in (await _page(cockpit, "product"))["verbs"]]
    tasks = [v["verb"] for v in (await _page(cockpit, task.ref))["verbs"]]
    assert "add" not in product and "move" not in tasks


async def _structure(cockpit: Cockpit, path: str, value: Any) -> None:
    resp = await cockpit.client.put(
        f"/v1/factory/factories/{cockpit.fid}/definition",
        json={"changes": [{"op": "replace", "path": f"structure.{path}", "value": value}]},
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 200, resp.text
    nodes.reset_cache()
    snapshot.reset_cache()


async def test_a_verb_its_level_does_not_list_is_not_served(cockpit: Cockpit) -> None:
    await _structure(cockpit, "verbs.project", ["talk", "add", "start", "team", "pause"])

    move = await cockpit.post(
        _url(cockpit, "panel", "preview"), {"verb": "move", "args": {"to": "I-1"}}
    )
    order = await cockpit.post(
        _url(cockpit, "panel", "preview"), {"verb": "reorder", "args": {"col": 0}}
    )
    page = await _page(cockpit, "panel")

    assert (move.status_code, error_code(move)) == (422, "not_here")
    assert (order.status_code, error_code(order)) == (422, "not_here")
    assert "structure.verbs.project" in move.json()["error"]["message"]
    assert "move" not in [v["verb"] for v in page["verbs"]]


async def test_who_applies_follows_the_levels_authority(cockpit: Cockpit) -> None:
    editor = await _preview(cockpit, "I-1", "add", user="carol", title="Usage report")
    await _structure(cockpit, "levels.project.authority.approval", "owner")
    owners = await _preview(cockpit, "I-1", "add", user="carol", title="Usage report")
    other = cockpit.store.create_initiative(
        cockpit.fid, title="Second (sample)", ask="Another outcome", plan={"projects": []}
    )
    await _structure(cockpit, "levels.project.authority.move", [])
    never = await cockpit.post(
        _url(cockpit, "panel", "preview"), {"verb": "move", "args": {"to": other.ref}}
    )

    assert (editor["approver"], editor["can_apply"]) == ("you", True)
    assert (owners["approver"], owners["can_apply"]) == ("an owner", False)
    assert (never.status_code, error_code(never)) == (422, "not_here")
    assert "authority.move" in never.json()["error"]["message"]


async def test_pausing_a_feature_names_what_it_cannot_stop(cockpit: Cockpit) -> None:
    cockpit.store.update_project(
        cockpit.store.get_project(cockpit.fid, "core").id, pl_session_id=cockpit.world.id("pl")
    )
    nodes.reset_cache()
    snapshot.reset_cache()

    preview = await _preview(cockpit, "I-1", "pause")

    assert any("leads of core pause" in e for e in preview["effects"])
    assert any("panel" in w and "no lead yet" in w for w in preview["warnings"])
    assert any("ads-sync" in w for w in preview["warnings"])


async def test_a_preview_with_no_roll_up_change_has_no_roll_up(cockpit: Cockpit) -> None:
    preview = await _preview(cockpit, "api-version", "pause")

    assert preview["rollup_delta"] is None


async def test_an_implicit_parents_thread_needs_its_node(cockpit: Cockpit) -> None:
    deps, fid = cockpit.deps, cockpit.fid

    found = chat.resolve_thread(deps, fid, "need:by-server")
    feature = chat.resolve_thread(deps, fid, "feature:by-server")
    with pytest.raises(CockpitError) as nobody:
        chat.resolve_thread(deps, fid, "need:by-nobody-at-all")

    assert (found.node, feature.node) == ("need:by-server", "feature:by-server")
    assert nobody.value.code == "unknown_node"


async def test_an_agents_reorder_after_a_sibling_says_why(cockpit: Cockpit) -> None:
    await _apply(cockpit, "I-1", await _preview(cockpit, "I-1", "add", title="Extra"))

    bare = await propose(cockpit, {"op": "reorder", "slug": "extra", "after": "core"})
    said = await propose(
        cockpit,
        {"op": "reorder", "slug": "extra", "after": "core", "reason": "uses the core's API"},
    )

    assert (bare.status_code, error_code(bare)) == (400, "dependency_reason_required")
    assert said.status_code in (200, 201), said.text


# ── What cockpit v3 reads beyond api.md (docs/FACTORY.md, the S12 contract) ──


async def test_move_and_name_first_read_lists_the_parents(cockpit: Cockpit) -> None:
    other = cockpit.store.create_initiative(
        cockpit.fid, title="Second (sample)", ask="Another outcome", plan={"projects": []}
    )

    move = await _preview(cockpit, "panel", "move")
    name = await _preview(cockpit, "feature:by-server", "name")
    applied = await cockpit.post(
        _url(cockpit, "panel", "apply"),
        {"verb": "move", "args": {}, "preview_id": move["preview_id"]},
    )
    picked = await _preview(cockpit, "panel", "move", to=other.ref)

    assert {p["ref"] for p in move["parents"]} == {"I-1", other.ref}
    assert move["can_apply"] is False and "one level up" in move["effects"][0]
    assert {p["ref"] for p in name["parents"]} == {"I-1", other.ref}
    # Name it files the work under a stored node of its own level, and says so.
    assert "only a stored feature is offered" in name["effects"][0], name["effects"]
    assert (applied.status_code, error_code(applied)) == (400, "invalid_args")
    assert picked["can_apply"] is True and picked["parents"] == move["parents"]


async def test_a_preview_says_who_approves_and_what_a_pull_forward_takes(
    cockpit: Cockpit,
) -> None:
    pulled = await _preview(cockpit, "I-1", "add", title="ads sync")
    start = await _preview(cockpit, "panel", "start")
    reader = await _preview(cockpit, "I-1", "add", user="carol", title="Usage")
    await _structure(cockpit, "levels.project.authority.approval", "owner")
    owners = await _preview(cockpit, "I-1", "add", user="carol", title="Usage")

    assert pulled["pulls_forward"]["ref"] == "ads-sync"
    assert pulled["moved_from"] == {"ref": "ads-sync", "col": "later"}
    assert pulled["apply_label"] == "Pull forward and plan"
    assert pulled["approver_note"] == "You, as owner. Applies now; Undo for 10 min."
    assert start["moved_from"] == {"ref": "panel", "col": 1}
    assert reader["approver_note"].startswith("You. Applies now")
    assert owners["approver_note"].startswith("An owner applies this")


async def test_apply_answers_in_one_line_with_an_undo_until_a_time(cockpit: Cockpit) -> None:
    applied = await _apply(cockpit, "core", await _preview(cockpit, "core", "add", title="Docs"))

    undone = await _undo(cockpit, applied["undo"]["token"])

    assert applied["message"] == applied["line"] and "Docs" in applied["message"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", applied["undo"]["expires_at"])
    assert undone.json()["message"].startswith("Undone: ")


async def test_start_now_keeps_links_to_finished_work_without_warning(cockpit: Cockpit) -> None:
    # The panel also waited on a spike that is done: a met link it already had.
    spike = cockpit.store.upsert_project(cockpit.fid, "spike", title="spike", repo=REPO)
    cockpit.store.update_project(spike.id, stage="merged", status="done")
    row = cockpit.store.get_project(cockpit.fid, "panel")
    assert row is not None
    charter = {**normalize_charter(row.charter), "depends_on": ["core", "spike"]}
    cockpit.store.update_project(row.id, charter=charter)

    preview = await _preview(cockpit, "panel", "start")

    assert preview["args"]["depends_on"] == ["PR-9001", "spike"]
    assert not any("spike" in w for w in preview["warnings"]), preview["warnings"]
    added = await _preview(cockpit, "I-1", "add", title="Late", depends_on=["spike"])
    assert any("spike" in w for w in added["warnings"]), "a new link to done work still warns"


async def test_the_undone_card_names_the_token_it_undid(cockpit: Cockpit) -> None:
    applied = await _apply(cockpit, "I-1", await _preview(cockpit, "I-1", "add", title="Usage"))
    token = applied["undo"]["token"]

    undone = await _undo(cockpit, token)

    assert undone.status_code == 200, undone.text
    cards = [
        (m.card or {}).get("meta") or {}
        for m in cockpit.store.list_chat(cockpit.fid, "feature:I-1", limit=20)
        if (m.card or {}).get("type") == "preview_applied"
    ]
    assert [c.get("undo_token") for c in cards] == [token, token]
    assert [bool(c.get("undone")) for c in cards] == [False, True]


async def test_add_takes_what_it_waits_on_as_a_list_with_reasons(cockpit: Cockpit) -> None:
    preview = await _preview(
        cockpit,
        "I-1",
        "add",
        title="Usage",
        depends_on=["core"],
        depends_why={"core": "reads the core's API"},
    )

    assert preview["args"]["depends_on"] == ["core"]
    assert preview["args"]["depends_why"] == {"core": "reads the core's API"}
    ghost = preview["columns_after"]["ghosts"][-1]
    assert (ghost["ref"], ghost["col"]) == ("usage", 1)
    one = await cockpit.post(
        _url(cockpit, "I-1", "preview"),
        {"verb": "add", "args": {"title": "Usage", "depends_on": "core"}},
    )
    assert (one.status_code, error_code(one)) == (400, "invalid_args")
