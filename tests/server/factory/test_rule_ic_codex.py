"""Rules for the Codex builder: a PL may propose them, and its cards and sessions get them.

The PL's rule roles always listed ``ic_codex``, but ``RuleDef`` refused it (it
only knew factory roles). A rule for ``ic_codex`` now validates, rides the card
of a task that runs on ``ic_codex`` (next to the ``ic`` rules), and is served by
``sys_factory_rules`` to a Codex builder session, whose role is ``ic``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from omnigent.server.factory import definition, tasks
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import definition_cockpit, propose, put

SLUG = "api-version"
CODEX_RULE = "Run the formatter before you commit (sample)."
IC_RULE = "Keep each PR under 300 lines (sample)."


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    tasks.reset()
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value
    tasks.reset()


def test_a_rule_for_the_codex_builder_validates() -> None:
    rule = definition.RuleDef(id="R-1", role="ic_codex", text=CODEX_RULE)
    assert rule.role == "ic_codex"
    assert "ic_codex" in definition.RULE_ROLES


async def test_a_pl_proposes_a_codex_builder_rule(cockpit: Cockpit) -> None:
    resp = await propose(
        cockpit, key="pl", kind="rules", rule={"role": "ic_codex", "text": CODEX_RULE}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None
    (added,) = row.after.values()
    assert (added["role"], added["project"]) == ("ic_codex", SLUG)


async def test_codex_cards_and_sessions_get_their_rules(cockpit: Cockpit) -> None:
    resp = await put(
        cockpit,
        {"op": "add", "path": "rules", "value": {"role": "ic", "text": IC_RULE}},
        {"op": "add", "path": "rules", "value": {"role": "ic_codex", "text": CODEX_RULE}},
    )
    assert resp.status_code == 200, resp.text
    upsert = await cockpit.client.put(
        "/v1/factory/agent/tasks",
        json={
            "from_session_id": cockpit.world.id("pl"),
            "tasks": [
                {"key": "claude-one", "title": "One (sample)"},
                {"key": "codex-one", "title": "Two (sample)", "harness": "ic_codex"},
            ],
        },
        headers={"X-Test-User": "alice"},
    )
    assert upsert.status_code == 200, upsert.text

    ctx = tasks.load_ctx(cockpit.deps, cockpit.fid, SLUG)
    by_key = ctx.by_key()
    claude = tasks.dispatch_item(cockpit.deps, ctx, by_key["claude-one"], mode="assist")
    codex = tasks.dispatch_item(cockpit.deps, ctx, by_key["codex-one"], mode="assist")
    assert f"R-1: {IC_RULE}" in claude["card_text"]
    assert CODEX_RULE not in claude["card_text"]
    assert f"R-1: {IC_RULE}" in codex["card_text"] and f"R-2: {CODEX_RULE}" in codex["card_text"]

    session = cockpit.world.make_session(
        "codex_ic", agent="pl", title="codex-one", parent="pl", sub_agent_name="ic_codex"
    )
    for sid, want in ((session.id, ["R-1", "R-2"]), (cockpit.world.id("ic"), ["R-1"])):
        rules = await cockpit.client.get(
            "/v1/factory/agent/rules",
            params={"from_session_id": sid},
            headers={"X-Test-User": "alice"},
        )
        assert rules.status_code == 200, rules.text
        assert [r["id"] for r in rules.json()["rules"]] == want
