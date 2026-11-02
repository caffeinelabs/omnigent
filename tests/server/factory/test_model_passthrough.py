"""``sys_factory_task(op="start")`` says which member, agent and model runs each attempt.

Without its own team a project keeps today's dispatch: the card's harness, and
the ``role_defaults`` model when an owner set one for that vendor. With its own
team the least busy builder runs it (its agent may differ from the card's), the
member is recorded on the task, and its model is what the PL passes as
``args.model`` to ``sys_session_send``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.server.factory import binder, snapshot, tasks
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import definition_cockpit, put

SLUG = "api-version"
MEMBERS = f"team.projects.{SLUG}.members"
PLAN = [
    {"key": "one", "title": "First change (sample)"},
    {"key": "two", "title": "Second change (sample)"},
    {"key": "three", "title": "Third change (sample)", "harness": "ic_codex"},
]


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    binder.reset()
    tasks.reset()
    snapshot.reset_cache()
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        resp = await value.client.put(
            "/v1/factory/agent/tasks",
            json={"from_session_id": world.id("pl"), "tasks": PLAN},
            headers={"X-Test-User": "alice"},
        )
        assert resp.status_code == 200, resp.text
        yield value
    binder.reset()
    tasks.reset()


async def _start(cockpit: Cockpit, key: str, **body: Any) -> httpx.Response:
    return await cockpit.client.post(
        f"/v1/factory/agent/tasks/{key}/start",
        json={"from_session_id": cockpit.world.id("pl"), **body},
        headers={"X-Test-User": "alice"},
    )


async def test_without_its_own_team_start_keeps_todays_dispatch(cockpit: Cockpit) -> None:
    resp = await _start(cockpit, "one")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["agent"], body["model"], body["member"], body["team"]) == (
        "ic",
        None,
        None,
        "default",
    )
    assert body["harness"] == "ic"


async def test_role_defaults_name_the_model_for_their_vendor(cockpit: Cockpit) -> None:
    resp = await put(cockpit, {"op": "replace", "path": "role_defaults.ic.model", "value": "opus"})
    assert resp.status_code == 200, resp.text

    claude = (await _start(cockpit, "one")).json()
    codex = (await _start(cockpit, "three")).json()

    assert (claude["agent"], claude["model"]) == ("ic", "opus")
    # A default without a vendor is for the role's default vendor only.
    assert (codex["agent"], codex["model"]) == ("ic_codex", None)


async def test_its_own_team_picks_the_least_busy_builder_and_its_model(cockpit: Cockpit) -> None:
    resp = await put(
        cockpit,
        {"op": "remove", "path": f"{MEMBERS}/M-3"},
        {"op": "replace", "path": f"{MEMBERS}/M-2/model", "value": "sonnet"},
        {"op": "add", "path": MEMBERS, "value": {"role": "ic", "model": "opus", "handle": "Nova"}},
    )
    assert resp.status_code == 200, resp.text

    first = (await _start(cockpit, "one")).json()
    second = (await _start(cockpit, "two")).json()
    # The card asked for Codex; the team has no Codex builder, so a Claude builder runs it.
    third = (await _start(cockpit, "three")).json()

    assert (first["member"], first["agent"], first["model"]) == ("M-2", "ic", "sonnet")
    assert (second["member"], second["model"]) == ("M-7", "opus")
    assert (third["agent"], third["team"]) == ("ic", "own")
    assert third["harness"] == "ic" and third["member"] == "M-2"
    row = cockpit.store.get_task_by_key(cockpit.fid, SLUG, "two")
    assert row is not None and row.card["member"] == "M-7"
    row = cockpit.store.get_task_by_key(cockpit.fid, SLUG, "three")
    assert row is not None and row.card["harness"] == "ic" and row.vendor == "claude"


async def test_a_named_harness_outside_the_team_is_kept_with_a_warning(cockpit: Cockpit) -> None:
    resp = await put(cockpit, {"op": "remove", "path": f"{MEMBERS}/M-3"})
    assert resp.status_code == 200, resp.text

    body = (await _start(cockpit, "one", harness="ic_codex")).json()

    assert (body["agent"], body["team"], body["member"]) == ("ic_codex", "outside", None)
    assert body["team_warning"] == "ic_codex is not on this project's team"


async def test_the_team_s_builders_at_once_caps_the_wave(cockpit: Cockpit) -> None:
    resp = await put(
        cockpit, {"op": "replace", "path": f"team.projects.{SLUG}/builders_at_once", "value": 1}
    )
    assert resp.status_code == 200, resp.text

    ctx = tasks.load_ctx(cockpit.deps, cockpit.fid, SLUG)
    assert ctx.slots.budget_ics == 1
    assert tasks.analyze(ctx).plan.waves[0] == ("one",)
