"""Lowering a budget applies at once, for editors too; raising one stays the owners' (spec §8.4).

A lower dollar budget (the factory's ``budgets.*``, discovery's
``discovery.budgets.*``, a loop's ``loops.<loop>.usd``) only reduces spend, so
it is class A and an editor's ``PUT …/definition`` applies it as a version,
with the editor's 10-minute Undo. Raising one, or mixing it with an owner
change, waits for an owner (403 ``owners_only``), and an agent's proposal of
any budget still waits for the owners. An editor's lowering never runs as a
trial (owners run trials), and a $0 ask budget is a cap like any other.
``alice`` owns the factory, ``carol`` is an editor (operator), ``bob`` has no
access.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.server.factory import definition, tuning
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import (
    definition_cockpit,
    propose,
    put,
    row,
    url,
    version,
)
from tests.server.factory.discovery_helpers import (
    base,
    discovery_cockpit,
    ingest,
    label,
    make_opportunity,
)

DAY = "budgets.factory_usd_per_day"


def _set(path: str, value: Any) -> dict[str, Any]:
    return {"op": "replace", "path": path, "value": value}


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


@pytest_asyncio.fixture()
async def discovering(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value


async def _preview(cockpit: Cockpit, *changes: dict[str, Any], user: str) -> httpx.Response:
    return await cockpit.client.post(
        url(cockpit, "/preview"), json={"changes": list(changes)}, headers={"X-Test-User": user}
    )


def _value(cockpit: Cockpit, path: str) -> Any:
    definition.invalidate(cockpit.fid)
    return definition.value(cockpit.deps, cockpit.fid, path)


@pytest.mark.parametrize(
    ("path", "before", "after", "klass"),
    [
        (DAY, 400, 300, "A"),
        (DAY, 400, 500, "B"),
        ("budgets.project_usd", 150, 100, "A"),
        ("budgets.usd_per_hour_alert", 15, 10, "A"),
        ("discovery.budgets.per_ask", 2, 1, "A"),
        ("discovery.budgets.usd_per_day", 10, 20, "B"),
        ("loops.implement.usd", 15, 10, "A"),
    ],
)
def test_a_lower_budget_is_class_a_and_a_higher_one_class_b(
    path: str, before: float, after: float, klass: str
) -> None:
    entry = {"path": path, "before": before, "after": after}

    assert definition.classify(path, before, after) == klass
    assert tuning.editor_may_apply([entry]) is (klass == "A")


def test_only_dollar_budgets_count_as_budgets() -> None:
    assert definition.is_budget_path("budgets.project_usd")
    assert definition.is_budget_path("discovery.budgets.per_ask")
    assert definition.is_budget_path("loops.improve.usd")
    # Lower is A for these too, but they are not money: editors still propose them.
    assert not definition.is_budget_path("loops.implement.attempts")
    assert not definition.is_budget_path("caps.max_projects")
    lower_rounds = {"path": "loops.review.rounds", "before": 3, "after": 2}
    assert not tuning.editor_may_apply([lower_rounds])
    mixed = [{"path": DAY, "before": 400, "after": 300}, lower_rounds]
    assert not tuning.editor_may_apply(mixed)


async def test_an_editor_lowers_the_daily_budget_at_once_and_undoes_it(cockpit: Cockpit) -> None:
    before = version(cockpit)

    resp = await put(cockpit, _set(DAY, 300), user="carol", note="slow down (sample)")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "applied" and body["def_version"] == before + 1
    assert _value(cockpit, DAY) == 300
    assert (row(cockpit).author, row(cockpit).class_) == ("carol", "human")

    undo = await cockpit.post(url(cockpit, "/pin"), {"version": before}, user="carol")

    assert undo.status_code == 200, undo.text
    assert version(cockpit) == before and _value(cockpit, DAY) == 400


async def test_an_editor_lowers_discovery_and_loop_budgets_at_once(cockpit: Cockpit) -> None:
    resp = await put(
        cockpit,
        _set("discovery.budgets.usd_per_day", 5),
        _set("discovery.budgets.per_ask", 1),
        _set("loops.implement.usd", 10),
        user="carol",
    )

    assert resp.status_code == 200, resp.text
    assert _value(cockpit, "discovery.budgets.usd_per_day") == 5
    assert _value(cockpit, "discovery.budgets.per_ask") == 1
    assert _value(cockpit, "loops.implement.usd") == 10


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ([_set(DAY, 500)], "owners_only"),
        ([_set("discovery.budgets.usd_per_day", 20)], "owners_only"),
        ([_set(DAY, 300), _set("budgets.project_usd", 200)], "owners_only"),
        # One lower budget does not carry an owner change with it (refused before the diff).
        ([_set(DAY, 300), _set("autonomy.projects", "grace")], None),
    ],
)
async def test_raising_a_budget_waits_for_an_owner(
    cockpit: Cockpit, changes: list[dict[str, Any]], code: str | None
) -> None:
    before = version(cockpit)

    resp = await put(cockpit, *changes, user="carol")

    assert resp.status_code == 403, resp.text
    assert code is None or error_code(resp) == code
    assert version(cockpit) == before and _value(cockpit, DAY) == 400
    owner = await put(cockpit, *changes)
    assert owner.status_code == 200, owner.text


async def test_people_without_edit_rights_cannot_lower_a_budget(cockpit: Cockpit) -> None:
    resp = await put(cockpit, _set(DAY, 300), user="bob")

    assert resp.status_code in (403, 404)
    assert _value(cockpit, DAY) == 400


async def test_the_preview_says_editors_apply_a_lower_budget(cockpit: Cockpit) -> None:
    lower = (await _preview(cockpit, _set(DAY, 300), user="carol")).json()
    higher = (await _preview(cockpit, _set(DAY, 500), user="carol")).json()

    assert lower["class"] == "A" and lower["approvers"] == ["owners", "editors"]
    assert lower["you_can_apply"] is True
    assert lower["who"].startswith("Owners and editors (class A: it only lowers a budget).")
    assert "You are an editor, so it applies when you press Apply" in lower["who"]
    assert higher["class"] == "B" and higher["approvers"] == ["owners"]
    assert higher["you_can_apply"] is False and higher["you_can_propose"] is True


async def test_an_editor_may_not_pin_back_someone_else_s_budget_change(cockpit: Cockpit) -> None:
    before = version(cockpit)
    assert (await put(cockpit, _set(DAY, 300))).status_code == 200

    undo = await cockpit.post(url(cockpit, "/pin"), {"version": before}, user="carol")

    assert undo.status_code == 403 and error_code(undo) == "owners_only"
    assert _value(cockpit, DAY) == 300


async def test_an_agent_s_lower_budget_still_waits_for_the_owners(cockpit: Cockpit) -> None:
    resp = await propose(cockpit, changes=[_set(DAY, 300)])

    assert resp.status_code == 200, resp.text
    assert (resp.json()["class"], row(cockpit).status) == ("B", "pending")
    assert _value(cockpit, DAY) == 400


async def test_an_editor_s_lower_budget_applies_at_once_never_as_a_trial(
    cockpit: Cockpit,
) -> None:
    before = version(cockpit)

    trial = await put(cockpit, _set(DAY, 300), user="carol", trial=True)
    seen = (await _preview(cockpit, _set(DAY, 300), user="carol")).json()["trialable"]
    owners = (await _preview(cockpit, _set(DAY, 300), user="alice")).json()["trialable"]

    assert (trial.status_code, error_code(trial)) == (403, "owners_only")
    assert "owners run trials" in trial.json()["error"]["message"]
    assert version(cockpit) == before and _value(cockpit, DAY) == 400
    assert (seen["ok"], seen["code"]) == (False, "not_trialable")
    assert seen["reason"].startswith("An editor's change applies at once")
    assert owners["ok"] is True
    owner = await put(cockpit, _set(DAY, 300), trial=True)
    assert (owner.status_code, owner.json()["status"]) == (200, "trial")


async def test_an_editor_s_zero_ask_budget_holds_for_their_asks(discovering: Cockpit) -> None:
    signals = ingest(discovering, "repo-issues", ["Can I show a draft first? (sample)"])
    label(discovering, signals, "workaround")
    make_opportunity(discovering, signals)
    ask = base(discovering, "/opportunities/O-1/research")
    assert (await put(discovering, _set("discovery.budgets.per_ask", 1))).status_code == 200

    lowered = await put(discovering, _set("discovery.budgets.per_ask", 0), user="carol")
    default = await discovering.post(ask, {"question": "Who shares? (sample)"}, user="carol")
    over = await discovering.post(
        ask, {"question": "Dig deeper (sample)", "budget_usd": 1}, user="carol"
    )

    assert lowered.status_code == 200 and lowered.json()["status"] == "applied"
    assert default.status_code == 200, default.text
    assert default.json()["research"]["budget_usd"] == 0
    assert (over.status_code, error_code(over)) == (403, "owners_only")
    owner = await discovering.post(ask, {"question": "Dig deeper (sample)", "budget_usd": 3})
    assert owner.status_code == 200, owner.text
    assert owner.json()["research"]["budget_usd"] == 3
