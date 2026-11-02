"""The unread ``models.ic|reviewer|planner`` settings moved to ``role_defaults``.

Nothing read ``models.*``; ``role_defaults.<role>`` holds a role's vendor and
model now. Stored versions that still carry ``models`` read as the new block
(an owner-set alias kept, a set role default winning), the old paths still
write through to the new ones, and a version stored from a legacy doc drops
``models``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryProposal
from omnigent.server.factory import definition, tuning
from omnigent.server.factory import definition_blocks as blocks
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import definition_cockpit, put, row, url

LEGACY = {"models": {"ic": "sonnet", "reviewer": "opus", "planner": None}}


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


def _legacy_version(cockpit: Cockpit, doc: dict[str, Any]) -> None:
    definition.definition_for(cockpit.deps, cockpit.fid)
    stored = cockpit.store.put_definition(
        cockpit.fid, doc=doc, created_by="alice", created_via="human", parent_version=1
    )
    definition.pin(cockpit.deps, cockpit.fid, stored.version)


def test_owner_set_models_become_role_defaults(cockpit: Cockpit) -> None:
    _legacy_version(cockpit, {**definition.default_doc(), **LEGACY})

    resolved = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)

    assert resolved is not None and "models" not in resolved.doc
    defaults = resolved.doc["role_defaults"]
    assert defaults["ic"] == {"vendor": None, "model": "sonnet"}
    assert defaults["reviewer"] == {"vendor": None, "model": "opus"}
    assert defaults["planner"] == {"vendor": None, "model": None}
    assert definition.value(cockpit.deps, cockpit.fid, "role_defaults.ic.model") == "sonnet"


def test_a_set_role_default_wins_over_the_legacy_value() -> None:
    upgraded = blocks.upgrade(
        {**LEGACY, "role_defaults": {"ic": {"vendor": "codex", "model": "codex-default"}}}
    )
    assert upgraded["role_defaults"]["ic"] == {"vendor": "codex", "model": "codex-default"}
    assert upgraded["role_defaults"]["reviewer"] == {"model": "opus"}
    assert blocks.upgrade({"engine": {}}) == {"engine": {}}
    assert blocks.upgrade({"models": None}) == {}


def test_a_legacy_doc_diffs_as_its_migrated_self() -> None:
    legacy = {**definition.default_doc(), **LEGACY}
    migrated = definition.full_doc(legacy)

    assert definition.diff(legacy, migrated) == []
    assert "models" not in migrated
    assert definition.moved_paths({"models.ic": "sonnet"}, legacy) == []


async def test_the_old_paths_write_the_new_ones(cockpit: Cockpit) -> None:
    resp = await put(cockpit, {"op": "replace", "path": "models.reviewer", "value": "sonnet"})

    assert resp.status_code == 200, resp.text
    assert definition.value(cockpit.deps, cockpit.fid, "role_defaults.reviewer.model") == "sonnet"
    assert list(row(cockpit).after) == ["role_defaults.reviewer.model"]
    view = (await cockpit.client.get(url(cockpit), headers={"X-Test-User": "alice"})).json()
    assert "models" not in view["effective"] and "models.ic" not in view["paths"]
    assert "role_defaults.ic.vendor" in view["paths"]


async def test_a_version_stored_on_a_legacy_doc_drops_models(cockpit: Cockpit) -> None:
    _legacy_version(cockpit, {**definition.default_doc(), **LEGACY})

    resp = await put(cockpit, {"op": "replace", "path": "loops.review.rounds", "value": 2})

    assert resp.status_code == 200, resp.text
    latest = cockpit.store.latest_definition(cockpit.fid)
    assert latest is not None and "models" not in latest.doc
    assert latest.doc["role_defaults"]["ic"]["model"] == "sonnet"


def test_reverting_a_legacy_change_reverts_the_role_default(cockpit: Cockpit) -> None:
    base = definition.default_doc()
    current = {**definition.default_doc(), "models": {"ic": "sonnet"}}
    legacy = FactoryProposal(
        id="p" * 32,
        factory_id=cockpit.fid,
        number=1,
        kind="tuning",
        title="models.ic: None → sonnet",
        author_kind="human",
        author="alice",
        before={"models.ic": None},
        after={"models.ic": "sonnet"},
    )

    doc = tuning.revert_doc(current, base, legacy)

    assert doc["role_defaults"]["ic"]["model"] is None
