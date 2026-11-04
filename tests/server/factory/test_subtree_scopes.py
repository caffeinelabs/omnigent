"""
Autonomy and auto-merge per subtree: an owner's setting on a node covers everything under it.

A person's ask planned as one outcome (``I-1``, "Sample helper"): ``core`` and
``panel`` on the factory's base (``develop``, the tryout base here), a third
project held for a later wave; ``drafts`` is outside it, on ``main``. The
owner turns auto-merge on for the outcome with the "Autonomy & merge" verb
(preview, apply, Undo): its projects' PRs into ``develop`` merge themselves,
so do the ones of projects added under it later, while ``drafts`` stays
merged by people, and ``main`` is never chosen unless an owner names it. The
nearest scoped ancestor decides, an autonomy level per subtree changes how
proposals about the projects under it apply, and agents can neither add nor
widen a scope. Every name is sample data.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

import pytest
import pytest_asyncio
from pydantic import BaseModel, ConfigDict, ValidationError

from omnigent.entities.factory import FactoryPr
from omnigent.entities.factory_discovery import FactoryOpportunity
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.factory import (
    auto_merge,
    autonomy,
    definition,
    initiatives,
    nodes,
    proposals,
    snapshot,
    subtrees,
)
from omnigent.server.factory import definition_blocks as blocks
from omnigent.server.factory.charter import project_base_branch
from omnigent.server.factory.node_views import validate_page
from omnigent.server.factory.subtrees import Lineage
from tests.server.factory import definition_helpers as defs
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.node_fixtures import FID, factory_1340, scale_factory
from tests.server.factory.projects_helpers import BASE, REPO, header_cockpit, project, propose

ASK = "Build an in-app helper (sample): a side panel and a core behind it."
SCOPES = auto_merge.SCOPES_PATH
FEATURE = "Sample helper"


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    initiatives.register()
    nodes.reset_cache()
    snapshot.reset_cache()
    check = nodes.check_levels

    def strict(tree: nodes.Tree) -> list[str]:
        bad = check(tree)
        assert not bad, bad
        return bad

    monkeypatch.setattr(nodes, "check_levels", strict)
    async for value in header_cockpit(world, db_uri, monkeypatch):
        restore = definition.install(value.app.state)
        try:
            await _plan(value)
            yield value
        finally:
            restore()
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
                "title": FEATURE,
                "acceptance": [{"step": "Ask the core", "expect": "it answers", "owner": "core"}],
                "skeleton": "core",
                "user_facing": ["panel"],
                "why_wait": {"panel": "it shows what the core answers"},
                "waves": [{"n": 2, "goal": "reach ad accounts", "projects": ["ads-sync"]}],
            }
        },
    )
    assert resp.json()["status"] == "applied", resp.text
    cockpit.store.upsert_project(
        cockpit.fid, "drafts", title="Drafts (sample)", repo=REPO, base_branch="main"
    )


# ── Helpers ────────────────────────────────────────────────────


def _url(cockpit: Cockpit, ref: str, what: str) -> str:
    return f"/v1/factory/factories/{cockpit.fid}/n/{ref}/{what}"


def _fresh() -> None:
    nodes.reset_cache()
    snapshot.reset_cache()
    definition.invalidate()


async def _page(cockpit: Cockpit, ref: str) -> dict[str, Any]:
    _fresh()
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/n/{ref}", headers={"X-Test-User": "alice"}
    )
    assert resp.status_code == 200, resp.text
    return validate_page(resp.json())


async def _preview(cockpit: Cockpit, ref: str, user: str = "alice", **args: Any) -> Any:
    _fresh()
    return await cockpit.post(
        _url(cockpit, ref, "preview"), {"verb": "auto", "args": args}, user=user
    )


async def _ok_preview(cockpit: Cockpit, ref: str, **args: Any) -> dict[str, Any]:
    resp = await _preview(cockpit, ref, **args)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _apply(cockpit: Cockpit, ref: str, preview: dict[str, Any], user: str = "alice") -> Any:
    _fresh()
    return await cockpit.post(
        _url(cockpit, ref, "apply"),
        {
            "verb": "auto",
            "args": preview["args"],
            "based_on": preview["based_on"],
            "preview_id": preview["preview_id"],
        },
        user=user,
    )


async def _set(cockpit: Cockpit, ref: str, **args: Any) -> dict[str, Any]:
    resp = await _apply(cockpit, ref, await _ok_preview(cockpit, ref, **args))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _pr(cockpit: Cockpit, slug: str, number: int, base: str) -> tuple[FactoryPr, Any]:
    pr, _ = cockpit.store.upsert_pr(
        cockpit.fid,
        REPO,
        number,
        project_slug=slug,
        head_ref=f"factory/{slug}/task-{number}",
        base_ref=base,
        head_sha="a" * 40,
        state="open",
        ready=True,
    )
    return pr, cockpit.store.get_project(cockpit.fid, slug)


def _merger_says(cockpit: Cockpit, slug: str, number: int, base: str = BASE) -> str | None:
    """Why the server's merger would not merge a ready PR of *slug* (``None``: it merges it)."""
    pr, row = _pr(cockpit, slug, number, base)
    targets = auto_merge.factory_targets(cockpit.deps, cockpit.fid, fresh=True)
    return auto_merge.local_refusal(pr, targets, row)


def _scopes(cockpit: Cockpit) -> list[dict[str, Any]]:
    resolved = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)
    assert resolved is not None
    return list(resolved.doc["product"]["delivery"]["auto_merge_scopes"])


def _chips(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [*page["chips"], *(c for g in page.get("chips_more") or [] for c in g["chips"])]


def _merge_chip(page: dict[str, Any]) -> dict[str, Any] | None:
    return next((c for c in _chips(page) if str(c.get("kind", "")).startswith("auto-merge")), None)


def _effects(preview: dict[str, Any]) -> str:
    """The preview's effect lines, ``Label: words`` one per line."""
    return "\n".join(f"{e['label']}: {e['text']}" for e in preview["effects"])


# ── The verb: preview, apply, Undo ─────────────────────────────


async def test_an_outcome_on_the_tryout_base_covers_its_projects_and_future_ones(
    cockpit: Cockpit,
) -> None:
    preview = await _ok_preview(cockpit, "I-1", auto_merge=True)

    assert preview["title"] == f"Autonomy for {FEATURE} and everything under it"
    assert preview["args"] == {"auto_merge": True, "repo": REPO, "base": BASE}
    assert (preview["approver"], preview["can_apply"]) == ("you", True)
    assert preview["apply_label"] == "Turn on for this subtree"
    effects = _effects(preview)
    assert f"Merges into: {REPO}#{BASE}" in effects
    assert "Covers: 2 projects now (core, panel) and any added under it later" in effects
    assert "main stays merged by people (drafts is outside this subtree)" in effects
    [delta] = preview["rollup_delta"]["nodes"]
    assert delta["items"] == [
        {"key": "auto_merge", "label": "Auto-merge", "before": "off", "after": f"→ {BASE} (here)"}
    ]
    # Nothing changed by the preview.
    assert _scopes(cockpit) == []
    assert _merger_says(cockpit, "core", 9001) == "not an auto-merge target"

    applied = await _apply(cockpit, "I-1", preview)

    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["undo"]["token"] and body["filed"].startswith("C-")
    assert f"auto-merge into {BASE} on for {FEATURE}" in body["message"]
    assert _scopes(cockpit) == [{"ref": "I-1", "merge": True, "repo": REPO, "base": BASE}]
    # Stored apart, so a server from before the scopes drops only them.
    stored = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)
    assert stored is not None and blocks.AUTO_MERGE_SCOPES_KEY in stored.stored
    assert "auto_merge_scopes" not in (stored.stored.get("product") or {}).get("delivery", {})
    # The merger: the outcome's projects merge themselves; the others wait for a person.
    assert _merger_says(cockpit, "core", 9001) is None
    assert _merger_says(cockpit, "panel", 9002) is None
    assert _merger_says(cockpit, "drafts", 9003, "main") == "not an auto-merge target"
    assert _merger_says(cockpit, "api-version", 9004) == "not an auto-merge target"
    # A project added under it later is covered too.
    added = await cockpit.post(
        _url(cockpit, "I-1", "preview"), {"verb": "add", "args": {"title": "Usage report"}}
    )
    assert added.status_code == 200, added.text
    made = await cockpit.post(
        _url(cockpit, "I-1", "apply"),
        {"verb": "add", "args": added.json()["args"], "preview_id": added.json()["preview_id"]},
    )
    assert made.status_code == 200, made.text
    assert _merger_says(cockpit, "usage-report", 9005) is None
    # Every descendant says so; the outcome itself says "this subtree".
    feature = await _page(cockpit, "I-1")
    core = await _page(cockpit, "core")
    drafts = await _page(cockpit, "drafts")
    assert _merge_chip(feature) == {
        "kind": "auto-merge →",
        "ref": "I-1",
        "title": BASE,
        "state": "this subtree",
        "tone": "acc",
    }
    assert _merge_chip(core) == {
        "kind": "auto-merge →",
        "ref": "I-1",
        "title": BASE,
        "state": f"from {FEATURE}",
        "tone": "acc",
    }
    assert _merge_chip(drafts) is None
    # The subtree's setting comes before the links, so it is never folded into "+n".
    assert (await _page(cockpit, "panel"))["chips"][0]["kind"] == "auto-merge →"
    assert feature["node"].get("auto_merge") is True
    tiles = {k["ref"]: k for k in feature["children"]["items"]}
    assert tiles["core"].get("auto_merge") is True and tiles["panel"].get("auto_merge") is True

    undone = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/undo/{body['undo']['token']}"
    )

    assert undone.status_code == 200, undone.text
    assert _scopes(cockpit) == []
    assert _merger_says(cockpit, "core", 9001) == "not an auto-merge target"
    assert _merge_chip(await _page(cockpit, "core")) is None


async def test_main_is_never_auto_merged_unless_the_owner_names_it(cockpit: Cockpit) -> None:
    unnamed = await _preview(cockpit, "drafts", auto_merge=True)
    other = cockpit.store.create_initiative(
        cockpit.fid, title="Drafts outcome (sample)", ask="Draft things", plan={"projects": []}
    )
    row = cockpit.store.get_project(cockpit.fid, "drafts")
    assert row is not None
    cockpit.store.update_project(row.id, feature=other.ref)
    via_feature = await _preview(cockpit, other.ref, auto_merge=True)

    for resp in (unnamed, via_feature):
        assert (resp.status_code, error_code(resp)) == (400, "invalid_args"), resp.text
        assert resp.json()["error"]["field"] == "args.base"
        assert resp.json()["base"] == "main"  # what the cockpit offers the owner to name
        assert "main stays merged by people unless an owner names it" in resp.text
    assert _scopes(cockpit) == []

    named = await _ok_preview(cockpit, "drafts", auto_merge=True, base="main")
    assert named["args"]["base"] == "main"
    assert (await _apply(cockpit, "drafts", named)).status_code == 200

    assert _merger_says(cockpit, "drafts", 9003, "main") is None
    assert _merger_says(cockpit, "core", 9001) == "not an auto-merge target"


async def test_the_nearest_scope_decides_and_off_keeps_people_merging(cockpit: Cockpit) -> None:
    await _set(cockpit, "I-1", auto_merge=True)

    off = await _ok_preview(cockpit, "panel", auto_merge=False)
    assert off["apply_label"] == "Turn off for this subtree"
    assert "People merge: every PR here" in _effects(off)
    [delta] = off["rollup_delta"]["nodes"]
    assert delta["items"][0]["before"] == f"→ {BASE} (from {FEATURE})"
    assert (await _apply(cockpit, "panel", off)).status_code == 200

    assert _merger_says(cockpit, "panel", 9002) == "people merge panel and everything under it"
    assert _merger_says(cockpit, "core", 9001) is None
    panel = await _page(cockpit, "panel")
    assert _merge_chip(panel) == {
        "kind": "auto-merge",
        "ref": "panel",
        "title": "off",
        "state": "this subtree",
    }
    assert panel["node"].get("auto_merge") is None
    assert "panel (its own setting)" in _effects(
        await _ok_preview(cockpit, "I-1", base="main", auto_merge=True)
    )

    # On again where the ancestor already merges: the node keeps no entry of its own.
    await _set(cockpit, "panel", auto_merge=True)
    assert _scopes(cockpit) == [{"ref": "I-1", "merge": True, "repo": REPO, "base": BASE}]
    again = await _preview(cockpit, "panel", auto_merge=True)
    assert (again.status_code, error_code(again)) == (409, "nothing_to_do")


async def test_only_an_owner_applies_it_and_only_as_a_person(cockpit: Cockpit) -> None:
    editor = await _preview(cockpit, "I-1", user="carol", auto_merge=True)
    assert editor.status_code == 200, editor.text
    assert (editor.json()["can_apply"], editor.json()["approver"]) == (False, "an owner")
    refused = await _apply(cockpit, "I-1", editor.json(), user="carol")
    owner = await _ok_preview(cockpit, "I-1", auto_merge=True)
    runner = await cockpit.client.post(
        _url(cockpit, "I-1", "apply"),
        json={"verb": "auto", "args": owner["args"], "preview_id": owner["preview_id"]},
        headers={"X-Test-User": "alice", RUNNER_TUNNEL_TOKEN_HEADER: "tok"},
    )

    assert (refused.status_code, error_code(refused)) == (403, "authority")
    assert (runner.status_code, error_code(runner)) == (403, "human_only")
    assert _scopes(cockpit) == []


@pytest.mark.parametrize("path", [SCOPES, autonomy.SCOPES_PATH])
@pytest.mark.parametrize("seat", ["lead_a", "lead_b"])
async def test_agents_never_add_or_widen_a_scope(cockpit: Cockpit, path: str, seat: str) -> None:
    value = (
        [{"ref": "I-1", "repo": REPO, "base": BASE}]
        if path == SCOPES
        else [{"ref": "I-1", "level": "tryout"}]
    )
    resp = await defs.propose(
        cockpit, key=seat, changes=[{"op": "replace", "path": path, "value": value}]
    )

    assert (resp.status_code, error_code(resp)) == (403, "path_not_allowed"), resp.text
    assert "agents never propose it" in resp.json()["error"]["message"]
    assert blocks.agent_never(path)
    assert _scopes(cockpit) == [] and autonomy.factory_scopes(cockpit.deps, cockpit.fid) == {}


async def test_the_verb_is_served_where_a_scope_can_be(cockpit: Cockpit) -> None:
    task, _ = cockpit.store.upsert_task(cockpit.fid, "core", "api", title="Core API")
    feature = await _page(cockpit, "I-1")
    need = next(c["ref"] for c in feature["breadcrumb"] if c["level"] == "need")
    implicit = await _page(cockpit, "api-version")
    home = implicit["breadcrumb"][-1]["ref"]

    offered = {
        ref: [v["verb"] for v in (await _page(cockpit, ref))["verbs"]]
        for ref in ("I-1", "core", task.ref, home, need)
    }
    on_implicit = await _preview(cockpit, home, auto_merge=True)
    bad = await _preview(cockpit, "I-1", auto_merge="yes")
    empty = await _preview(cockpit, "I-1")

    assert "auto" in offered["I-1"] and "auto" in offered["core"]
    assert "auto" not in offered[task.ref] and "auto" not in offered[home]
    assert (on_implicit.status_code, error_code(on_implicit)) == (422, "not_here")
    assert (bad.status_code, error_code(bad)) == (400, "invalid_args")
    assert (empty.status_code, error_code(empty)) == (400, "invalid_args")


# ── Autonomy per subtree ───────────────────────────────────────


async def _ask_again(cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch) -> str:
    """The Room message that started ``I-1``; its 24 h request window has lapsed."""
    monkeypatch.setattr(proposals, "REQUEST_WINDOW_S", -60)
    [message] = [
        m for m in cockpit.store.list_chat(cockpit.fid, "factory", limit=50) if m.body == ASK
    ]
    return f"chat_{message.id}"


async def test_a_tryout_outcome_grows_by_itself_while_the_factory_stays_careful(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask = await _ask_again(cockpit, monkeypatch)
    routine = {"dod": ["the core answers in under a second (sample)"]}

    async def grow(slug: str, touches: str) -> Any:
        """A lead's own plan inside the outcome: a new project and a routine update."""
        return await propose(
            cockpit,
            {"op": "create", "project": project(slug)},
            {"op": "update", "slug": touches, "patch": routine},
            reply_to=ask,
            analysis={"initiative": {"id": "I-1"}},
        )

    before = await grow("export-ui", "core")
    assert before.json()["status"] == "pending", before.text

    preview = await _ok_preview(cockpit, "I-1", autonomy="tryout", auto_merge=True)
    assert "Autonomy: Tryout (here)." in _effects(preview)
    assert (await _apply(cockpit, "I-1", preview)).status_code == 200
    assert autonomy.factory_level(cockpit.deps, cockpit.fid) == "careful"

    grown = await grow("export-api", "panel")
    joined = await propose(cockpit, {"op": "create", "project": project("side-api")}, reply_to=ask)
    unasked = await propose(cockpit, {"op": "create", "project": project("side-quest")})

    assert grown.json()["status"] == "applied", grown.text
    opened = _opened(cockpit, grown.json()["proposal"])
    assert opened.data["level"] == "careful" and opened.data["by_level"] == "tryout"
    assert opened.data["scoped"] == [
        {"op": 0, "ref": "I-1", "level": "tryout"},
        {"op": 1, "ref": "I-1", "level": "tryout"},
    ]
    # It lands where it was decided: in the outcome, whose auto-merge covers it.
    page = await _page(cockpit, "export-api")
    assert "I-1" in [c["ref"] for c in page["breadcrumb"]]
    assert _merge_chip(page) is not None and _merge_chip(page)["ref"] == "I-1"
    assert _merger_says(cockpit, "export-api", 9100) is None
    # Citing the ask that started the outcome is not extending it: the project
    # would land outside it, so the factory's level decides, and it waits.
    assert joined.json()["status"] == "pending", joined.text
    assert _opened(cockpit, joined.json()["proposal"]).data["scoped"] is None
    assert unasked.json()["status"] == "pending", unasked.text
    chip = next(c for c in _chips(await _page(cockpit, "core")) if c.get("kind") == "autonomy")
    assert chip == {
        "kind": "autonomy",
        "ref": "I-1",
        "title": "Tryout",
        "state": f"from {FEATURE}",
        "tone": "acc",
    }


def _opened(cockpit: Cockpit, ref: str) -> Any:
    [opened] = [e for e in cockpit.events("proposal.opened") if e.subject == f"proposal:{ref}"]
    return opened


async def test_a_factory_wide_target_counts_on_the_nodes_it_covers(cockpit: Cockpit) -> None:
    definition.set_values(
        cockpit.deps,
        cockpit.fid,
        {"product.delivery.auto_merge": [{"repo": REPO, "base": BASE}]},
        created_by="alice",
    )

    feature = await _page(cockpit, "I-1")
    drafts = await _page(cockpit, "drafts")

    # "Own or inherited": the factory's target covers the outcome and its projects.
    assert feature["node"].get("auto_merge") is True
    tiles = {k["ref"]: k for k in feature["children"]["items"]}
    assert tiles["core"].get("auto_merge") is True
    assert drafts["node"].get("auto_merge") is None  # main is no target
    assert _merge_chip(feature) is None  # no subtree setting: no chip
    # So the verb opens on what changes something: turning it off here.
    again = await _preview(cockpit, "I-1", auto_merge=True)
    assert (again.status_code, error_code(again)) == (409, "nothing_to_do")
    off = await _ok_preview(cockpit, "I-1", auto_merge=False)
    assert off["apply_label"] == "Turn off for this subtree"
    [delta] = off["rollup_delta"]["nodes"]
    assert delta["items"][0]["before"] == f"→ {BASE} (the factory's)"
    assert (await _apply(cockpit, "I-1", off)).status_code == 200
    assert _scopes(cockpit) == [{"ref": "I-1", "merge": False, "repo": None, "base": None}]
    assert (await _page(cockpit, "I-1"))["node"].get("auto_merge") is None
    assert _merger_says(cockpit, "core", 9001) == "people merge I-1 and everything under it"


async def test_a_projects_level_covers_only_that_project(cockpit: Cockpit) -> None:
    await _set(cockpit, "core", autonomy="standard")
    routine = {"dod": ["the core answers in under a second (sample)"]}

    core = await propose(cockpit, {"op": "update", "slug": "core", "patch": routine})
    panel = await propose(cockpit, {"op": "update", "slug": "panel", "patch": routine})
    both = await propose(
        cockpit,
        {"op": "update", "slug": "core", "patch": routine},
        {"op": "update", "slug": "panel", "patch": routine},
    )

    assert core.json()["status"] == "applied", core.text
    assert panel.json()["status"] == "pending", panel.text
    # One proposal about both waits: the strictest subtree decides.
    assert both.json()["status"] == "pending", both.text
    assert autonomy.factory_scopes(cockpit.deps, cockpit.fid) == {"core": "standard"}

    await _set(cockpit, "core", autonomy="inherit")
    assert autonomy.factory_scopes(cockpit.deps, cockpit.fid) == {}


# ── The rules in the definition ────────────────────────────────


class _OldAutonomyDef(BaseModel):
    """``autonomy`` as a server from before the scopes reads it."""

    model_config = ConfigDict(extra="forbid")

    projects: Literal["confirm", "requested", "grace"] = "requested"
    level: Literal["careful", "standard", "tryout"] = "careful"


class _OldDeliveryDef(blocks._Block):
    """``product.delivery`` as a server from before the scopes reads it."""

    merge_policy: str | None = None
    auto_merge: list[blocks.AutoMergeTargetDef] = []
    protected_repos: list[str] = []
    demo_paths: list[str] = []
    preview_domain: str | None = None
    release: str = "merged"
    flags: blocks.FlagsDef = blocks.FlagsDef()


def test_scopes_are_stored_apart_and_only_once_set() -> None:
    empty = definition.stored_form(definition.default_doc())
    doc = definition.default_doc()
    doc["autonomy"]["scopes"] = [{"ref": "I-1", "level": "tryout"}]
    doc["product"]["delivery"]["auto_merge_scopes"] = [
        {"ref": "I-1", "repo": REPO, "base": "tryout-base"},
        {"ref": "panel", "merge": False},
    ]

    stored = definition.stored_form(doc)

    assert blocks.AUTONOMY_SCOPES_KEY not in empty and blocks.AUTO_MERGE_SCOPES_KEY not in empty
    assert "scopes" not in empty["autonomy"]
    assert stored[blocks.AUTONOMY_SCOPES_KEY] == [{"ref": "I-1", "level": "tryout"}]
    assert len(stored[blocks.AUTO_MERGE_SCOPES_KEY]) == 2
    # A server from before them reads the rest as the owners set it.
    _OldAutonomyDef.model_validate(stored["autonomy"])
    _OldDeliveryDef.model_validate(stored["product"]["delivery"])
    full = definition.full_doc(stored)
    assert full["autonomy"]["scopes"] == [{"ref": "I-1", "level": "tryout"}]
    assert full["product"]["delivery"]["auto_merge_scopes"][1] == {
        "ref": "panel",
        "merge": False,
        "repo": None,
        "base": None,
    }
    paths = {d["path"] for d in definition.diff(empty, stored)}
    assert paths == {SCOPES, autonomy.SCOPES_PATH}


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([{"ref": "product", "repo": REPO, "base": BASE}], "a scope names a goal"),
        ([{"ref": "T-4", "repo": REPO, "base": BASE}], "a scope names a goal"),
        ([{"ref": "feature:from-C-1", "repo": REPO, "base": BASE}], "a scope names a goal"),
        ([{"ref": "I-1"}], "names its repo and base"),
        ([{"ref": "I-1", "base": BASE}], "names its repo and base"),
        ([{"ref": "I-1", "merge": False, "base": "main"}], "names no base"),
        ([{"ref": "I-1", "repo": REPO, "base": "release/*"}], "branch name"),
        (
            [{"ref": "I-1", "repo": REPO, "base": BASE}, {"ref": "I-1", "merge": False}],
            "scoped twice",
        ),
    ],
)
def test_an_auto_merge_scope_names_a_node_and_its_base(value: list[Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        blocks.DeliveryDef.model_validate({"auto_merge_scopes": value})


def test_an_autonomy_scope_names_a_node_and_a_level() -> None:
    with pytest.raises(ValidationError, match="scoped twice"):
        autonomy.AutonomyDef.model_validate(
            {"scopes": [{"ref": "O-2", "level": "tryout"}, {"ref": "O-2", "level": "standard"}]}
        )
    with pytest.raises(ValidationError):
        autonomy.AutonomyDef.model_validate({"scopes": [{"ref": "G-1", "level": "reckless"}]})
    ok = autonomy.AutonomyDef.model_validate({"scopes": [{"ref": "G-1", "level": "standard"}]})
    assert ok.scopes[0].ref == "G-1"


# ── The lookups, pure ──────────────────────────────────────────


def test_the_strictest_subtree_an_op_touches_decides() -> None:
    scopes = {"I-1": "tryout", "core": "standard"}

    assert autonomy.op_level([("panel", "I-1", "O-3")], scopes, "careful") == ("tryout", "I-1")
    assert autonomy.op_level([("core", "I-1")], scopes, "careful") == ("standard", "core")
    assert autonomy.op_level([("core", "I-1"), ("x", "I-2")], scopes, "careful") == (
        "careful",
        None,
    )
    assert autonomy.op_level([], scopes, "standard") == ("standard", None)
    verdict = autonomy.decide(
        ["create_joined", "update_routine"],
        requested=False,
        level="careful",
        levels=["tryout", "standard"],
    )
    assert verdict == autonomy.Verdict("auto", info_line=True, by_level="tryout")
    with pytest.raises(ValueError, match="one level per op"):
        autonomy.decide(["create"], requested=False, levels=[])


def test_a_scope_adds_its_base_and_off_takes_every_base_away() -> None:
    pairs = frozenset({(REPO, "staging")})
    delivery = {
        "auto_merge": [{"repo": REPO, "base": "staging"}],
        "auto_merge_scopes": [
            {"ref": "I-1", "repo": REPO, "base": "tryout-base"},
            {"ref": "panel", "merge": False},
        ],
    }
    targets = auto_merge.targets_from(delivery)

    class _Row:
        def __init__(self, slug: str, feature: str | None) -> None:
            self.slug, self.feature = slug, feature

    core, panel, other = _Row("core", "I-1"), _Row("panel", "I-1"), _Row("drafts", None)

    assert targets.pairs == pairs and bool(targets)
    assert targets.cover(core).pairs == pairs | {(REPO, "tryout-base")}  # type: ignore[arg-type]
    assert targets.cover(panel).pairs == frozenset()  # type: ignore[arg-type]
    assert targets.cover(other).pairs == pairs  # type: ignore[arg-type]
    assert auto_merge.is_target(targets, REPO, "tryout-base", core)  # type: ignore[arg-type]
    assert not auto_merge.is_target(targets, REPO, "tryout-base")
    # Only scopes that merge make anything merge by itself.
    assert not auto_merge.targets_from({"auto_merge_scopes": [{"ref": "x", "merge": False}]})
    # Without the initiatives read, a project is placed by its stored feature.
    assert Lineage().chain(core) == ("core", "I-1")  # type: ignore[arg-type]


# ── Where a project sits: as the tree files it ────────────────


def _needs(facts: nodes.NodeFacts) -> nodes.NodeFacts:
    """The sample factory with a goal and two needs: C-12 planned ``O-1`` (under ``G-1``)."""
    opportunities = [
        FactoryOpportunity(
            factory_id=FID,
            number=n,
            title=f"Need {n} (sample)",
            statement="When I build, I want help (sample)",
            created_by="scout",
            goal=goal,
        )
        for n, goal in ((1, "G-1"), (2, None))
    ]
    return dataclasses.replace(
        facts,
        goals=[{"id": "G-1", "statement": "Users build faster (sample)"}],
        opportunities=opportunities,
        proposals=[
            dataclasses.replace(p, opportunity="O-1") if p.number == 12 else p
            for p in facts.proposals
        ],
    )


def _agents_feature(facts: nodes.NodeFacts) -> nodes.NodeFacts:
    """``I-1`` started by an agent's C-11, which planned ``O-2``: the feature sits under it."""
    return dataclasses.replace(
        _needs(facts),
        initiatives=[
            dataclasses.replace(i, requested_by={"kind": "agent", "name": "Atlas"})
            for i in facts.initiatives
        ],
        proposals=[
            dataclasses.replace(p, opportunity={11: "O-2", 12: "O-1"}.get(p.number))
            for p in facts.proposals
        ],
    )


def _planned_feature(facts: nodes.NodeFacts) -> nodes.NodeFacts:
    """``I-1`` names its need itself (``plan.opportunity``)."""
    return dataclasses.replace(
        _needs(facts),
        initiatives=[
            dataclasses.replace(i, plan={**i.plan, "opportunity": "O-1"})
            for i in facts.initiatives
        ],
    )


@pytest.mark.parametrize(
    "make",
    [
        factory_1340,
        lambda: _needs(factory_1340()),
        lambda: _agents_feature(factory_1340()),
        lambda: _planned_feature(factory_1340()),
        lambda: scale_factory(features=4, projects=12, tasks=24),
    ],
    ids=["sample", "implicit-under-need", "agents-feature", "planned-feature", "scale"],
)
def test_a_projects_scope_chain_is_its_place_in_the_tree(make: Callable[[], Any]) -> None:
    tree = nodes.Tree(make())
    checked = 0

    for row in tree.project_rows.values():
        node = tree.lookup(row.slug)
        if node is None or node.tree != nodes.PRODUCT_TREE:
            continue
        stored = [a.ref for a in reversed(tree.ancestors(node.ref))]
        assert tree.lineage.chain(row) == (
            row.slug,
            *(r for r in stored if subtrees.is_scope_ref(r)),
        ), (row.slug, stored)
        checked += 1

    assert checked >= 4


def test_a_scope_on_a_need_covers_the_projects_in_its_implicit_features() -> None:
    facts = _needs(factory_1340())
    row = next(p for p in facts.projects if p.slug == "build-tier-drafts-3")
    pair = ((row.repo or "").lower(), project_base_branch(row))
    scope = {"G-1": auto_merge.MergeScope("G-1", pair)}
    tree = nodes.Tree(
        dataclasses.replace(
            facts,
            merge_scopes=scope,
            autonomy_scopes={"O-1": "tryout"},
            targets=auto_merge.Targets(frozenset(), scope),
        )
    )
    node = tree.lookup(row.slug)
    assert node is not None

    # The tree files it in C-12's implicit feature, under the need C-12 planned.
    assert [a.ref for a in tree.ancestors(node.ref)] == [
        "product",
        "G-1",
        "O-1",
        "feature:from-C-12",
    ]
    assert tree.lineage.chain(row) == (row.slug, "O-1", "G-1")
    assert tree.merge_scope(node) == ("G-1", scope["G-1"])
    assert tree.autonomy_scope(node) == ("O-1", "tryout")
    assert tree.auto_merged(node)
    # The merger reads the same place.
    assert auto_merge.as_targets(tree.wfacts.targets).cover(row).pairs == {pair}
    assert subtrees.lineage_of(
        facts.initiatives, facts.opportunities, facts.goals, facts.proposals
    ).chain(row) == (row.slug, "O-1", "G-1")


def test_a_new_feature_s_need_is_its_own_and_an_extended_one_keeps_its_need() -> None:
    facts = _planned_feature(factory_1340())
    lineage = subtrees.lineage_of(
        facts.initiatives, facts.opportunities, facts.goals, facts.proposals
    )

    # Extending I-1 lands there, whatever opportunity the proposal names.
    assert lineage.above("I-1", "O-2") == ("I-1", "O-1", "G-1")
    # A new or implicit feature sits under the proposal's opportunity.
    assert lineage.above(None, "O-2") == ("O-2",)
    assert lineage.above(None, "O-9") == ()
