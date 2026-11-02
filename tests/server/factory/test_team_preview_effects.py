"""``POST …/definition/preview`` for team changes: what the staged panel shows.

The server says what a team change does before it applies (the cockpit never
computes cost or class itself): the rate while working, the cost to finish
against the budget, who restarts and when, the review route, the TEAM UPDATE
it sends, who may apply it, what blocks it, and that it never runs as a trial.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.server.factory import team
from omnigent.server.factory.telemetry.spans import write_span
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import definition_cockpit, url, version

SLUG = "api-version"
MEMBERS = f"team.projects.{SLUG}.members"


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


async def _preview(cockpit: Cockpit, *changes: dict[str, Any], **body: Any) -> httpx.Response:
    user = body.pop("user", "alice")
    return await cockpit.client.post(
        url(cockpit, "/preview"),
        json={"changes": list(changes), **body},
        headers={"X-Test-User": user},
    )


def _effects(body: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [e for e in body["effects"] if e["kind"] == kind]


async def test_a_model_swap_previews_rate_finish_restart_route_and_mail(cockpit: Cockpit) -> None:
    resp = await _preview(
        cockpit,
        {"op": "replace", "path": f"{MEMBERS}/M-3/vendor", "value": "claude"},
        {"op": "replace", "path": f"team.projects.{SLUG}/budget_usd", "value": 80},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [d["path"] for d in body["diff"]] == [
        f"team.projects.{SLUG}.budget_usd",
        f"{MEMBERS}.M-3",
    ]
    assert [d["class"] for d in body["diff"]] == ["B", "B"]
    assert body["class"] == "B" and body["you_can_apply"] is True
    assert body["who"].startswith("Owners (class B). You are an owner")
    (rate,) = _effects(body, "rate")
    assert rate["project"] == SLUG and rate["before_usd_h"] > 0 and rate["after_usd_h"] > 0
    (finish,) = _effects(body, "finish")
    assert finish["budget_usd"] == 80.0 and finish["text"].endswith("of $80.")
    (restart,) = _effects(body, "restart")
    assert restart["members"] == ["M-3"] and restart["when"] == "after_current"
    assert "finishes its current task, then restarts on claude" in restart["text"]
    (route,) = _effects(body, "review_route")
    assert route["text"] == "M-3's PRs then get a Codex review (cross-vendor rule)."
    (update,) = _effects(body, "team_update")
    assert update["to"] == cockpit.world.id("pl")
    assert update["text"] == "PL · api-version gets one TEAM UPDATE message with v2."
    # Nobody else is mailed the change.
    assert _effects(body, "mail") == []
    assert body["trialable"]["ok"] is False
    assert "trial team changes on a template instead" in body["trialable"]["reason"]
    # A dry run.
    assert version(cockpit) == 1


async def test_interrupting_now_says_so(cockpit: Cockpit) -> None:
    resp = await _preview(cockpit, {"op": "remove", "path": f"{MEMBERS}/M-3"}, when="now")

    assert resp.status_code == 200, resp.text
    (restart,) = _effects(resp.json(), "restart")
    assert restart["when"] == "now"
    assert restart["text"].startswith("M-3 is interrupted now and leaves the team.")


async def test_an_editor_sees_a_narrowing_team_change_is_theirs(cockpit: Cockpit) -> None:
    narrow = await _preview(cockpit, {"op": "remove", "path": f"{MEMBERS}/M-3"}, user="carol")
    body = narrow.json()
    assert body["class"] == "A" and body["approvers"] == ["owners", "editors"]
    assert body["you_can_apply"] is True
    assert body["who"] == (
        "Owners and editors (class A: it only narrows a team). You are an editor, so it "
        "applies when you press Apply."
    )

    wider = await _preview(
        cockpit, {"op": "add", "path": MEMBERS, "value": {"role": "ic"}}, user="carol"
    )
    body = wider.json()
    assert body["class"] == "B" and body["you_can_apply"] is False
    assert body["who"].endswith("It waits for an owner: propose it to them.")


async def test_a_gate_refusal_is_blocked_in_the_preview(cockpit: Cockpit) -> None:
    resp = await _preview(
        cockpit,
        {"op": "remove", "path": f"{MEMBERS}/M-4"},
        {"op": "remove", "path": f"{MEMBERS}/M-6"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["you_can_apply"] is False
    assert [(b["code"], b["gate"]) for b in body["blocked"]] == [
        ("gate_requires_role", "review"),
        ("gate_requires_role", "demo"),
    ]
    assert body["blocked"][0]["message"] == (
        "api-version: can't remove the last reviewer: the review gate needs a reviewer (Tier 0)"
    )


async def test_rates_come_from_the_cost_ledger_once_it_has_some(cockpit: Cockpit) -> None:
    book = team.rate_book(cockpit.deps, cockpit.fid)
    default = book.rate("ic", "claude", None)
    assert (default.source, default.usd_per_h) == ("default", team.MODEL_USD_PER_H["sonnet"])

    now_ms = int(time.time() * 1000)
    for i in range(3):
        write_span(
            cockpit.store,
            cockpit.fid,
            kind="task",
            name=f"run {i} (sample)",
            started_at=now_ms - 3_600_000,
            ended_at=now_ms,
            outcome="ok",
            agent_name="ic",
            cost_usd=6.0,
            dedupe_key=f"sample-{i}",
        )
    book = team.rate_book(cockpit.deps, cockpit.fid)
    learned = book.rate("ic", "claude", None)
    assert (learned.source, learned.n, learned.usd_per_h) == ("ledger", 3, 6.0)
