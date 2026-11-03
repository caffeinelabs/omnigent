"""Initiatives (``I-n``): an ask bigger than one project, planned and tracked as one outcome.

A lead files the plan as one projects proposal whose analysis carries an
``initiative`` block. The server refuses a plan that would keep people
waiting (a skeleton with dependencies, a user-facing project that waits on
every other one, a dependency on unrelated work), stores the ask verbatim
when the proposal applies, stamps each project's charter with its part, and
serves the initiative to the cockpit. Every name here is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.factory import events, initiatives, proposals
from omnigent.server.factory.charter import render_pl_charter
from omnigent.stores.factory_store import InitiativeVersionConflict
from omnigent.stores.factory_store.sqlalchemy_store import SqlAlchemyFactoryStore
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import header_cockpit, project, propose

ASK = (
    "Build an in-app assistant (sample): it answers questions in a side panel "
    "and can call our connectors."
)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    initiatives.register()
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value


def ask(cockpit: Cockpit, body: str = ASK) -> str:
    """A person's ask in the Room; returns its ``chat_<id>``."""
    message = cockpit.store.append_chat(
        cockpit.fid,
        thread="factory",
        author_kind="human",
        author="alice",
        author_role="owner",
        body=body,
    )
    return f"chat_{message.id}"


def block(**over: Any) -> dict[str, Any]:
    """An analysis carrying a sample initiative block."""
    plan: dict[str, Any] = {
        "title": "Sample assistant",
        "outcome": "People get answers in a side panel",
        "flag": "assistant.panel",
        "acceptance": [
            {
                "step": "Open the help panel from the header",
                "expect": "it opens",
                "owner": "panel",
            },
            {
                "step": "Ask a question in the panel",
                "expect": "an answer appears",
                "owner": "core",
                "needs": ["login"],
            },
        ],
        "skeleton": "panel",
        "user_facing": ["panel"],
        "waves": [{"n": 2, "goal": "Call the connectors", "projects": ["connectors"]}],
    }
    plan.update(over)
    return {"initiative": plan, "analysis": {"notes": ["sample"]}}


def creates(*specs: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"op": "create", "project": project(slug, **extra)} for slug, extra in specs]


async def plan(cockpit: Cockpit, analysis: Any, *specs: tuple[str, dict[str, Any]]) -> Any:
    specs = specs or (("panel", {}), ("core", {}))
    return await propose(
        cockpit, *creates(*specs), reply_to=ask(cockpit), thread="factory", analysis=analysis
    )


async def get(cockpit: Cockpit, path: str, user: str = "alice") -> Any:
    return await cockpit.client.get(path, headers={"X-Test-User": user})


# ── Planning rules ─────────────────────────────────────────────


async def test_several_projects_for_one_ask_need_an_initiative(cockpit: Cockpit) -> None:
    resp = await plan(cockpit, {"analysis": {"notes": []}})

    assert (resp.status_code, error_code(resp)) == (400, "initiative_missing")
    assert resp.json()["error"]["field"] == "analysis.initiative"
    assert '"skeleton"' in resp.json()["error"]["message"]
    assert cockpit.store.list_proposals(cockpit.fid) == []


async def test_separate_outcomes_opt_out_with_a_reason(cockpit: Cockpit) -> None:
    resp = await plan(cockpit, {"initiative": {"none": "two unrelated fixes (sample)"}})

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "applied"
    assert cockpit.store.list_initiatives(cockpit.fid) == []


async def test_one_project_needs_no_initiative(cockpit: Cockpit) -> None:
    resp = await propose(cockpit, *creates(("panel", {})), reply_to=ask(cockpit))

    assert resp.json()["status"] == "applied", resp.text
    assert cockpit.store.list_initiatives(cockpit.fid) == []


@pytest.mark.parametrize(
    ("over", "field"),
    [
        ({"acceptance": []}, "analysis.initiative.acceptance"),
        ({"title": None}, "analysis.initiative.title"),
        ({"skeleton": None}, "analysis.initiative.skeleton"),
        ({"skeleton": "ghost"}, "analysis.initiative.skeleton"),
        ({"user_facing": ["ghost"]}, "analysis.initiative.user_facing[0]"),
        ({"flag": {"maybe": "x"}}, "analysis.initiative.flag"),
    ],
)
async def test_a_new_initiative_is_checked_field_by_field(
    cockpit: Cockpit, over: dict[str, Any], field: str
) -> None:
    resp = await plan(cockpit, block(**over))

    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["field"] == field


async def test_a_step_owner_must_be_a_project_of_it(cockpit: Cockpit) -> None:
    resp = await plan(cockpit, block(acceptance=[{"step": "Open it", "owner": "ghost"}]))

    assert resp.json()["error"]["field"] == "analysis.initiative.acceptance[0].owner"


async def test_the_walking_skeleton_starts_now(cockpit: Cockpit) -> None:
    waits_on_core = ("panel", {"depends_on": ["core: reads its answers API"]})
    waits = await plan(cockpit, block(), waits_on_core, ("core", {}))

    assert (waits.status_code, error_code(waits)) == (409, "skeleton_waits")
    assert waits.json()["error"]["field"] == "changes[0].project.depends_on"
    assert "stub what is missing" in waits.json()["error"]["message"]

    said = await plan(
        cockpit,
        block(why_wait={"panel": "needs the auth migration first (sample)"}),
        waits_on_core,
        ("core", {}),
    )
    assert said.status_code == 200, said.text


async def test_a_user_facing_project_never_waits_on_every_other_one(cockpit: Cockpit) -> None:
    widget = ("widget", {"depends_on": ["core: calls its API", "panel: lives in it"]})
    refused = await plan(
        cockpit, block(user_facing=["panel", "widget"]), ("panel", {}), ("core", {}), widget
    )

    assert (refused.status_code, error_code(refused)) == (409, "surface_waits_on_all")
    assert refused.json()["project"] == "widget"
    assert refused.json()["error"]["field"] == "changes[2].project.depends_on"

    one_dep = await plan(
        cockpit,
        block(user_facing=["panel", "widget"]),
        ("panel", {}),
        ("core", {}),
        ("widget", {"depends_on": ["core: calls its API"]}),
    )
    assert one_dep.status_code == 200, one_dep.text


async def test_a_part_may_wait_on_another_part(cockpit: Cockpit) -> None:
    resp = await plan(
        cockpit,
        block(),
        ("panel", {}),
        ("core", {}),
        ("connectors", {"depends_on": ["core: reuses the core client"]}),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == []


async def test_a_user_facing_project_never_waits_on_unrelated_work(cockpit: Cockpit) -> None:
    unrelated = ("panel", {"depends_on": ["api-version: shows its version"]})
    refused = await plan(cockpit, block(skeleton="core"), unrelated, ("core", {}))

    assert (refused.status_code, error_code(refused)) == (409, "unrelated_dependency")
    assert "api-version" in refused.json()["error"]["message"]
    assert refused.json()["project"] == "panel"

    said = await plan(
        cockpit,
        block(skeleton="core", why_wait={"panel": "shows that version"}),
        unrelated,
        ("core", {}),
    )
    assert said.status_code == 200, said.text
    part = await plan(
        cockpit,
        block(skeleton="ui", user_facing=["ui"], acceptance=[{"step": "Open it"}]),
        ("ui", {}),
        ("svc", {"depends_on": ["api-version: reads its route"]}),
    )
    assert (part.status_code, error_code(part)) == (409, "unrelated_dependency")
    assert part.json()["project"] == "svc"
    assert "svc is part of it but waits on api-version" in part.json()["error"]["message"]
    part_said = await plan(
        cockpit,
        block(
            skeleton="ui",
            user_facing=["ui"],
            acceptance=[{"step": "Open it"}],
            why_wait={"svc": "reads the route that project adds (sample)"},
        ),
        ("ui", {}),
        ("svc", {"depends_on": ["api-version: reads its route"]}),
    )
    assert part_said.status_code == 200, part_said.text


async def test_the_live_shape_is_refused(cockpit: Cockpit) -> None:
    """The back end named as the skeleton, no user_facing, the UI waiting on it and more."""
    panel = ("panel", {"depends_on": ["core: shows its answers", "api-version: shows it"]})
    refused = await plan(cockpit, block(skeleton="core", user_facing=[]), panel, ("core", {}))

    assert refused.status_code == 409, refused.text
    assert error_code(refused) in ("surface_waits_on_all", "unrelated_dependency")
    assert refused.json()["project"] == "panel"
    no_owners = [{"step": "Open the panel"}, {"step": "Ask it something"}]
    unrelated = await plan(
        cockpit,
        block(skeleton="core", user_facing=[], acceptance=no_owners),
        ("panel", {"depends_on": ["core: shows its answers", "api-version: shows it"]}),
        ("core", {}),
    )
    assert (unrelated.status_code, error_code(unrelated)) == (409, "unrelated_dependency")


async def test_the_skeleton_owns_a_step(cockpit: Cockpit) -> None:
    steps = [
        {"step": "Open the help panel", "owner": "panel"},
        {"step": "Ask a question", "owner": "panel"},
    ]
    refused = await plan(cockpit, block(skeleton="core", user_facing=[], acceptance=steps))

    assert (refused.status_code, error_code(refused)) == (409, "skeleton_owns_no_step")
    assert refused.json()["error"]["field"] == "analysis.initiative.skeleton"
    owned = await plan(cockpit, block(skeleton="panel", user_facing=[], acceptance=steps))
    assert owned.status_code == 200, owned.text


# ── Applying ───────────────────────────────────────────────────


async def test_applying_starts_it_with_the_ask_verbatim(cockpit: Cockpit) -> None:
    resp = await plan(cockpit, block())

    body = resp.json()
    assert (body["status"], body["initiative"]) == ("applied", "I-1"), body
    [row] = cockpit.store.list_initiatives(cockpit.fid)
    assert (row.ref, row.title, row.ask, row.status) == ("I-1", "Sample assistant", ASK, "open")
    assert row.requested_by == {"kind": "human", "id": "alice", "name": "alice"}
    assert row.created_by == "C-1" and row.thread == "factory"
    plan_ = initiatives.plan_of(row)
    assert plan_["projects"] == ["panel", "core"]
    assert plan_["skeleton"] == "panel" and plan_["flag"] == {"key": "assistant.panel"}
    assert [s["n"] for s in plan_["acceptance"]] == [1, 2]
    assert plan_["acceptance"][1]["needs"] == ["login"]
    assert plan_["history"][0]["wave"] == 1 and plan_["history"][0]["proposal"] == "C-1"
    panel = cockpit.store.get_project(cockpit.fid, "panel")
    core = cockpit.store.get_project(cockpit.fid, "core")
    assert panel is not None and core is not None
    assert panel.charter["initiative"] == {
        "ref": "I-1",
        "title": "Sample assistant",
        "role": "skeleton",
        "wave": 1,
        "flag": "assistant.panel",
    }
    assert core.charter["initiative"]["role"] == "surface"
    text = render_pl_charter(panel)
    assert "Initiative: I-1 Sample assistant · wave 1" in text
    assert "walking skeleton" in text and "behind the flag assistant.panel" in text
    [created] = cockpit.events("initiative.created")
    assert created.data["projects"] == ["panel", "core"]
    proposal = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert proposal is not None and (proposal.result or {})["initiative"] == "I-1"
    assert "· initiative: I-1" in proposals.applied_text("C-1", "alice", proposal.result or {})


async def test_a_later_wave_extends_it(cockpit: Cockpit) -> None:
    await plan(cockpit, block())
    steps = [{"step": "Ask the panel to call a connector", "expect": "the result shows"}]
    resp = await propose(
        cockpit,
        *creates(("connectors", {})),
        reply_to=ask(cockpit, "Next: connectors (sample)"),
        analysis={"initiative": {"id": "I-1", "acceptance": steps, "user_facing": []}},
    )

    assert (resp.json()["status"], resp.json()["initiative"]) == ("applied", "I-1"), resp.text
    row = initiatives.find(cockpit.deps, cockpit.fid, "I-1")
    assert row is not None
    plan_ = initiatives.plan_of(row)
    assert plan_["projects"] == ["panel", "core", "connectors"]
    assert [h["wave"] for h in plan_["history"]] == [1, 2]
    assert plan_["acceptance"] == [
        {"n": 1, "step": "Ask the panel to call a connector", "expect": "the result shows"}
    ]
    assert plan_["history"][1]["acceptance_before"][0]["owner"] == "panel"
    connectors = cockpit.store.get_project(cockpit.fid, "connectors")
    assert connectors is not None and connectors.charter["initiative"]["wave"] == 2
    assert cockpit.events("initiative.extended")


async def test_a_later_wave_applies_for_the_person_who_asked(cockpit: Cockpit) -> None:
    """The lead's wave 2 answers no new message: it builds what the person asked for."""
    await plan(cockpit, block())
    steps = [{"step": "Ask the panel to call a connector", "owner": "connectors"}]

    resp = await propose(
        cockpit,
        *creates(("connectors", {})),
        analysis={"initiative": {"id": "I-1", "acceptance": steps}},
    )

    body = resp.json()
    assert (body["status"], body["approval"], body["initiative"]) == ("applied", "auto", "I-1")
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 2)
    assert row is not None and (row.on_behalf_of, row.thread) == ("alice", "factory")
    opened = cockpit.events("proposal.opened")[-1]
    assert opened.data["requested"] is True and opened.data["for_initiative"] == "I-1"
    assert cockpit.store.list_decisions(factory_id=cockpit.fid, status="open") == []
    asks = await get(cockpit, f"/v1/factory/factories/{cockpit.fid}/asks")
    fyi = {item["ref"]: item for item in asks.json().get("fyi") or []}
    assert fyi["C-2"]["why"] == "asked for by alice, part of I-1", fyi


async def test_confirm_autonomy_still_asks_for_a_later_wave(cockpit: Cockpit) -> None:
    from omnigent.server.factory import definition

    await plan(cockpit, block())
    definition.set_values(
        cockpit.deps, cockpit.fid, {"autonomy.projects": "confirm"}, created_by="alice"
    )

    resp = await propose(
        cockpit, *creates(("connectors", {})), analysis={"initiative": {"id": "I-1"}}
    )

    assert (resp.json()["status"], resp.json()["approval"]) == ("pending", "operator"), resp.text


async def test_a_follow_up_for_a_closed_owner_applies_too(cockpit: Cockpit) -> None:
    await plan(cockpit, block())
    core = cockpit.store.get_project(cockpit.fid, "core")
    assert core is not None
    cockpit.store.update_project(core.id, status="merged")

    resp = await propose(
        cockpit,
        *creates(("core-fix", {})),
        analysis={"initiative": {"id": "I-1"}},
        rationale="acceptance step 2 of I-1 fails in a closed project",
    )

    assert (resp.json()["status"], resp.json()["approval"]) == ("applied", "auto"), resp.text
    row = initiatives.find(cockpit.deps, cockpit.fid, "I-1")
    assert row is not None and "core-fix" in initiatives.plan_of(row)["projects"]


async def test_an_extension_that_restructures_or_was_never_asked_for_waits(
    cockpit: Cockpit,
) -> None:
    await plan(cockpit, block())
    structural = await propose(
        cockpit,
        {"op": "create", "project": project("extra")},
        {"op": "cancel", "slug": "core"},
        analysis={"initiative": {"id": "I-1"}},
        reason="the panel absorbs core (sample)",
    )
    assert (structural.json()["status"], structural.json()["approval"]) == (
        "pending",
        "operator",
    ), structural.text
    row = initiatives.find(cockpit.deps, cockpit.fid, "I-1")
    assert row is not None
    cockpit.store.update_initiative(
        row.id, requested_by={"kind": "agent", "id": cockpit.world.id("lead_a"), "name": "Lead"}
    )
    own = await propose(cockpit, *creates(("more", {})), analysis={"initiative": {"id": "I-1"}})

    assert (own.json()["status"], own.json()["approval"]) == ("pending", "operator"), own.text


async def test_one_ask_filed_as_several_plans_is_one_outcome(cockpit: Cockpit) -> None:
    message = ask(cockpit)
    first = await propose(cockpit, *creates(("panel", {})), reply_to=message, thread="factory")
    second = await propose(cockpit, *creates(("core", {})), reply_to=message, thread="factory")

    assert first.json()["status"] == "applied", first.text
    assert (second.status_code, error_code(second)) == (400, "initiative_missing")
    assert "C-1: panel" in second.json()["error"]["message"]
    separate = await propose(
        cockpit,
        *creates(("core", {})),
        reply_to=message,
        thread="factory",
        analysis={"initiative": {"none": "a separate fix asked in passing (sample)"}},
    )
    assert separate.json()["status"] == "applied", separate.text


async def test_a_later_plan_for_the_same_ask_extends_its_initiative(cockpit: Cockpit) -> None:
    message = ask(cockpit)
    await propose(
        cockpit, *creates(("panel", {}), ("core", {})), reply_to=message, analysis=block()
    )

    bare = await propose(cockpit, *creates(("connectors", {})), reply_to=message)
    second = await propose(
        cockpit, *creates(("connectors", {})), reply_to=message, analysis=block(skeleton=None)
    )
    extended = await propose(
        cockpit,
        *creates(("connectors", {})),
        reply_to=message,
        analysis={"initiative": {"id": "I-1"}},
    )

    assert (bare.status_code, error_code(bare)) == (400, "initiative_missing")
    assert '{"id": "I-1"}' in bare.json()["error"]["message"]
    assert (second.status_code, error_code(second)) == (409, "initiative_open_for_ask")
    assert extended.json()["status"] == "applied", extended.text
    assert len(cockpit.store.list_initiatives(cockpit.fid)) == 1


async def test_extending_an_unknown_or_closed_one_is_refused(cockpit: Cockpit) -> None:
    unknown = await propose(cockpit, *creates(("x", {})), analysis={"initiative": {"id": "I-9"}})
    assert (unknown.status_code, error_code(unknown)) == (404, "unknown_initiative")

    await plan(cockpit, block())
    row = initiatives.find(cockpit.deps, cockpit.fid, "I-1")
    assert row is not None
    cockpit.store.update_initiative(row.id, status="cancelled")
    closed = await propose(cockpit, *creates(("y", {})), analysis={"initiative": {"id": "I-1"}})
    assert (closed.status_code, error_code(closed)) == (409, "initiative_closed")


async def test_an_undone_proposal_takes_its_projects_back(cockpit: Cockpit) -> None:
    await plan(cockpit, block())

    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "proposal.undone",
        subject="proposal:C-1",
        summary="C-1 undone by alice",
        data={"dropped": ["panel", "core"]},
    )

    row = initiatives.find(cockpit.deps, cockpit.fid, "I-1")
    assert row is not None
    assert (row.status, row.closed_by) == ("cancelled", "undo C-1")
    assert initiatives.plan_of(row)["projects"] == []
    assert cockpit.events("initiative.undone")


# ── The cockpit's routes ───────────────────────────────────────


async def test_the_list_and_the_page(cockpit: Cockpit) -> None:
    await plan(cockpit, block())
    listed = await get(cockpit, f"/v1/factory/factories/{cockpit.fid}/initiatives")
    page = await get(cockpit, f"/v1/factory/factories/{cockpit.fid}/initiatives/I-1")

    assert listed.status_code == 200, listed.text
    [line] = listed.json()["initiatives"]
    assert line["ref"] == "I-1" and line["href"] == f"/factory/{cockpit.fid}/initiatives/I-1"
    assert line["line"] == (
        "Sample assistant: 0/2 merged, skeleton not on base env yet, no acceptance run yet"
    )
    assert line["state"] == "planned" and line["acceptance"]["total"] == 2
    assert page.status_code == 200, page.text
    view = page.json()
    assert view["ask"] == ASK and view["asked_by"]["kind"] == "human"
    assert [p["slug"] for p in view["projects"]] == ["panel", "core"]
    assert [p["role"] for p in view["projects"]] == ["skeleton", "surface"]
    assert view["acceptance"][0]["last"] is None
    assert view["next_wave"] == {"n": 2, "goal": "Call the connectors", "projects": ["connectors"]}
    assert view["waves"][0]["proposal"] == "C-1"
    missing = await get(cockpit, f"/v1/factory/factories/{cockpit.fid}/initiatives/I-7")
    assert (missing.status_code, error_code(missing)) == (404, "unknown_initiative")


async def test_a_person_edits_closes_and_reopens_it(cockpit: Cockpit) -> None:
    await plan(cockpit, block())
    path = f"/v1/factory/factories/{cockpit.fid}/initiatives/I-1"

    renamed = await cockpit.patch(
        path, {"title": "Help panel (sample)", "acceptance": [{"step": "Open it"}]}
    )
    closed = await cockpit.patch(path, {"status": "done"})
    reopened = await cockpit.patch(path, {"status": "open"})

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Help panel (sample)"
    assert [s["step"] for s in renamed.json()["acceptance"]] == ["Open it"]
    assert closed.json()["state"] == "closed"
    assert reopened.json()["status"] == "open"
    edited = cockpit.events("initiative.edited")[0]
    assert edited.data["before"]["title"] == "Sample assistant"
    assert edited.data["after"]["title"] == "Help panel (sample)"
    assert [e.data["after"]["status"] for e in cockpit.events("initiative.closed")] == ["done"]
    assert cockpit.events("initiative.reopened")


async def test_agents_cannot_use_the_peoples_routes(cockpit: Cockpit) -> None:
    await plan(cockpit, block())
    path = f"/v1/factory/factories/{cockpit.fid}/initiatives/I-1"
    headers = {"X-Test-User": "alice", RUNNER_TUNNEL_TOKEN_HEADER: "rt_abc.def"}

    patched = await cockpit.client.patch(path, json={"status": "done"}, headers=headers)
    run = await cockpit.client.post(f"{path}/acceptance", headers=headers)
    viewer = await cockpit.patch(path, {"status": "done"}, user="bob")

    assert (patched.status_code, error_code(patched)) == (403, "human_only")
    assert (run.status_code, error_code(run)) == (403, "human_only")
    assert viewer.status_code in (403, 404)


async def test_run_now_needs_a_pull_request(cockpit: Cockpit) -> None:
    await plan(cockpit, block())

    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/initiatives/I-1/acceptance")

    assert (resp.status_code, error_code(resp)) == (409, "no_pr_yet")


async def test_the_lead_lists_open_initiatives(cockpit: Cockpit) -> None:
    await plan(cockpit, block())

    resp = await cockpit.client.get(
        "/v1/factory/agent/projects",
        params={"from_session_id": cockpit.world.id("lead_a")},
        headers={"X-Test-User": "alice"},
    )

    [item] = resp.json()["initiatives"]
    assert item["initiative"] == "I-1" and item["skeleton"] == "panel"
    assert item["acceptance_steps"] == 2 and item["last_run"] is None
    assert item["planned_waves"][0]["n"] == 2


# ── The store ──────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["sql", "memory"])
def test_the_store_numbers_and_compares_versions(db_uri: str, kind: str) -> None:
    from omnigent.stores.factory_store import InMemoryFactoryStore

    store = SqlAlchemyFactoryStore(db_uri) if kind == "sql" else InMemoryFactoryStore()
    fid = "0" * 31 + "1"
    first = store.create_initiative(fid, title="A (sample)", ask="ask", plan={"projects": ["a"]})
    second = store.create_initiative(fid, title="B (sample)", ask="ask", status="done")
    assert (first.ref, second.ref, first.version) == ("I-1", "I-2", 1)
    assert [r.ref for r in store.list_initiatives(fid)] == ["I-2", "I-1"]
    assert [r.ref for r in store.list_initiatives(fid, status="open")] == ["I-1"]
    updated = store.update_initiative(first.id, expect_version=1, runs=[{"n": 1}], title="x" * 200)
    assert updated is not None and updated.version == 2 and updated.runs == [{"n": 1}]
    assert len(updated.title) == 120
    with pytest.raises(InitiativeVersionConflict):
        store.update_initiative(first.id, expect_version=1, title="late")
    assert store.get_initiative_by_number(fid, 1) == store.get_initiative(first.id)
    with pytest.raises(ValueError):
        store.update_initiative(first.id, status="maybe")
