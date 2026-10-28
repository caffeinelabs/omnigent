"""New factory "Describe it": a chat-first start with a brief, autonomy and engine mode.

The web form sends ``projects: []``, the brief, the default ``repo#base`` and
integration env toggle, plus the factory's project autonomy and engine mode,
which land on the root labels the proposal and loop engines read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.factory import autonomy, definition, tasks
from omnigent.server.factory.charter import DEFAULT_BASE_ENV
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FactoryWorld


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    monkeypatch.delenv("OMNIGENT_FACTORY_PREVIEW_TOOLING_REF", raising=False)
    monkeypatch.delenv(DEFAULT_BASE_ENV, raising=False)
    async for value in build_cockpit(world, db_uri, monkeypatch):
        yield value


def _describe(root_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "root_session_id": root_id,
        "name": "Atlas factory",
        "projects": [],
        "brief": "Show the deployed versions in Settings.",
        "defaults": {
            "repo": "caffeinelabs/app",
            "base_branch": "temp-main",
            "integration_env": True,
        },
        **extra,
    }


def _lead(cockpit: Cockpit) -> str:
    root = cockpit.world.make_session("atlas", agent="lead", title="Atlas")
    cockpit.permissions.grant("alice", root.id, LEVEL_OWNER)
    return root.id


async def test_autonomy_and_engine_mode_land_on_the_root(cockpit: Cockpit) -> None:
    root = _lead(cockpit)

    resp = await cockpit.post(
        "/v1/factory/factories", _describe(root, autonomy="grace", engine_mode="observe")
    )

    assert resp.status_code == 201, resp.text
    # The start's events resolve the definition, which takes the modes in from the root labels.
    assert definition.autonomy_mode(cockpit.deps, root) == "grace"
    assert definition.engine_mode(cockpit.deps, root) == "observe"
    rows = {p.slug for p in cockpit.store.list_projects(root)}
    assert rows == {"base-env"}
    [first] = cockpit.store.list_chat(root, "factory", limit=5)[:1]
    assert first.body == "Show the deployed versions in Settings."


async def test_modes_win_over_a_definition_seeded_before_the_start(cockpit: Cockpit) -> None:
    from omnigent import factory as F

    root = _lead(cockpit)
    # Lazy enrolment, then a supervisor tick, resolve the definition before the start.
    cockpit.world.store.set_labels(root, {F.LABEL_ID: root, F.LABEL_ROLE: F.ROLE_LEAD})
    seeded = definition.definition_for(cockpit.deps, root, fresh=True)
    assert seeded is not None and seeded.get("engine.mode") == "assist"

    resp = await cockpit.post(
        "/v1/factory/factories", _describe(root, autonomy="grace", engine_mode="observe")
    )

    assert resp.status_code == 201, resp.text
    assert definition.engine_mode(cockpit.deps, root) == "observe"
    assert definition.autonomy_mode(cockpit.deps, root) == "grace"


async def test_unset_modes_keep_the_defaults(cockpit: Cockpit) -> None:
    root = _lead(cockpit)

    resp = await cockpit.post("/v1/factory/factories", _describe(root))

    assert resp.status_code == 201, resp.text
    labels = cockpit.world.get("atlas").labels
    assert autonomy.LABEL_AUTONOMY not in labels
    assert tasks.LABEL_ENGINE_MODE not in labels
    assert autonomy.autonomy_mode(labels) == "requested"


@pytest.mark.parametrize(("field", "value"), [("autonomy", "always"), ("engine_mode", "turbo")])
async def test_an_unknown_mode_is_refused_with_its_field(
    cockpit: Cockpit, field: str, value: str
) -> None:
    root = _lead(cockpit)

    resp = await cockpit.post("/v1/factory/factories", _describe(root, **{field: value}))

    assert resp.status_code == 400
    assert resp.json()["field"] == field
    assert cockpit.store.list_projects(root) == []
