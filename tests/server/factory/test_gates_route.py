"""``GET …/gates``: the gates a pull request passes before a merge, read only (Tier 0).

Steer › Gates lists each ready gate and the demo gate in order, when it is
required, the role a team needs for it and what it checks; the roles come from
the same table that locks team edits, so the two never disagree.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from omnigent.factory_gates import DEMO_GATE, PROTECTED_PATHS, READY_GATES
from omnigent.server.factory import team_spec
from omnigent.server.factory.api.gates import GATE_TEXT, gates_view
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import definition_cockpit


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


def test_every_gate_has_words_and_the_order_of_the_merge_gate() -> None:
    view = gates_view()

    assert [g["gate"] for g in view["gates"]] == [*READY_GATES, DEMO_GATE]
    assert set(GATE_TEXT) == {*READY_GATES, DEMO_GATE}
    assert all(g["label"] and g["checks"] for g in view["gates"])
    assert view["protected_paths"] == list(PROTECTED_PATHS)
    assert view["tier"] == 0


def test_the_roles_come_from_the_table_that_locks_team_edits() -> None:
    gates = {g["gate"]: g for g in gates_view()["gates"]}

    for need in team_spec.GATE_NEEDS:
        if need.gate in gates:
            assert gates[need.gate]["role"] == need.role
            assert gates[need.gate]["role_when"] == need.when
    assert gates["review"]["role_label"] == "reviewer"
    assert gates["verify"]["role_when"] == "env"
    assert gates["verify"]["skipped_by"] == ["factory:no-env", "factory:revert"]
    assert gates[DEMO_GATE]["required"] == "previews"
    assert gates["protected_paths"]["allowed_by"] == "factory:allow-protected"
    assert gates["draft"]["role"] is None
    needs = {n["gate"]: n for n in gates_view()["team_needs"]}
    assert needs["builder"]["pr_gate"] is False
    assert needs["builder"]["when_text"] == "on every team"


async def test_people_who_read_the_factory_read_its_gates(cockpit: Cockpit) -> None:
    path = f"/v1/factory/factories/{cockpit.fid}/gates"

    resp = await cockpit.client.get(path, headers={"X-Test-User": "carol"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == gates_view()

    denied = await cockpit.client.get(path, headers={"X-Test-User": "bob"})
    assert denied.status_code in (403, 404)


async def test_nothing_writes_a_gate(cockpit: Cockpit) -> None:
    path = f"/v1/factory/factories/{cockpit.fid}/gates"

    for method in ("post", "put", "patch", "delete"):
        resp = await getattr(cockpit.client, method)(path, headers={"X-Test-User": "alice"})
        assert resp.status_code == 405, method
