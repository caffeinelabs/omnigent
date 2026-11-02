"""The PL hears about its team: the Team block in its charter and one TEAM UPDATE per change.

A project with its own team gets a ``Team`` block in its charter (members,
agents, vendors, models, builders at once, budget, briefs); a change to it
sends its PL exactly one ``[Factory control · TEAM UPDATE · vN · by <who>]``
with the diff and the new team, and nobody else a definition mail. With
``when="now"`` the server interrupts the sessions of changed or removed
members. A project without its own team keeps today's charter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import interventions, team
from omnigent.server.factory.charter import render_pl_charter
from omnigent.server.factory.spawn import brief_text
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import definition_cockpit, put, texts

SLUG = "api-version"
MEMBERS = f"team.projects.{SLUG}.members"


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


def _team_updates(cockpit: Cockpit, key: str) -> list[str]:
    return [t for t in texts(cockpit, key) if "TEAM UPDATE" in t.split("\n", 1)[0]]


async def test_a_team_change_sends_its_pl_one_team_update(cockpit: Cockpit) -> None:
    before = {key: len(texts(cockpit, key)) for key in ("pl", "lead_a", "lead_b")}
    resp = await put(
        cockpit,
        {"op": "replace", "path": f"{MEMBERS}/M-2/model", "value": "opus"},
        {"op": "replace", "path": f"{MEMBERS}/M-2/handle", "value": "Rex"},
        {
            "op": "replace",
            "path": f"team.projects.{SLUG}/briefs/M-2",
            "value": "Keep each PR under 300 lines (sample).",
        },
    )

    assert resp.status_code == 200, resp.text
    (update,) = _team_updates(cockpit, "pl")
    lines = update.splitlines()
    assert lines[0] == "[Factory control · TEAM UPDATE · v2 · by alice]"
    assert f"- ~ {MEMBERS}.M-2 · handle none → Rex; model default → opus" in lines
    assert "Team (definition v2 · template standard · 6 members · builders at once: 3 · " in (
        update
    )
    assert "- M-2 Rex · builder · agent ic · claude · opus" in lines
    assert "- M-2: Keep each PR under 300 lines (sample)." in lines
    assert any(line.startswith("When: after current tasks.") for line in lines)
    # One TEAM UPDATE and nothing else, to the PL only.
    assert len(texts(cockpit, "pl")) == before["pl"] + 1
    assert len(texts(cockpit, "lead_a")) == before["lead_a"]
    assert len(texts(cockpit, "lead_b")) == before["lead_b"]
    (event,) = cockpit.events(team.UPDATED_KIND)
    assert event.project_slug == SLUG and event.data["version"] == 2
    assert {c["path"] for c in event.data["changes"]} == {
        f"{MEMBERS}.M-2",
        f"team.projects.{SLUG}.briefs.M-2",
    }


async def test_other_settings_still_mail_the_members(cockpit: Cockpit) -> None:
    resp = await put(cockpit, {"op": "replace", "path": "loops.review.rounds", "value": 2})

    assert resp.status_code == 200, resp.text
    assert _team_updates(cockpit, "pl") == []
    assert texts(cockpit, "pl")[-1].startswith("[Factory definition · v2")


async def test_now_interrupts_the_removed_members_sessions(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = cockpit.world.make_session(
        "codex_ic", agent="pl", title="route", parent="pl", sub_agent_name="ic_codex"
    )
    stopped: list[str] = []

    async def _interrupt(_app_state: Any, session_id: str) -> bool:
        stopped.append(session_id)
        return True

    monkeypatch.setattr(interventions, "interrupt_session", _interrupt)
    monkeypatch.setattr(team, "_running", lambda _deps, _pl, agents: [codex] if agents else [])

    resp = await put(cockpit, {"op": "remove", "path": f"{MEMBERS}/M-3"}, when="now")

    assert resp.status_code == 200, resp.text
    assert stopped == [codex.id]
    (update,) = _team_updates(cockpit, "pl")
    assert "When: now. The server interrupted 1 session(s)" in update
    (event,) = cockpit.events(team.INTERRUPTED_KIND)
    assert event.data["sessions"] == [codex.id]


async def test_the_charter_prints_a_project_s_own_team(cockpit: Cockpit) -> None:
    project = cockpit.store.get_project(cockpit.fid, SLUG)
    assert project is not None
    # Without its own team the charter is today's.
    assert team.charter_team(cockpit.deps, cockpit.fid, project) is None
    plain = render_pl_charter(project)
    assert "Team (" not in plain and "IC budget: up to 3 ICs at once" in plain

    resp = await put(
        cockpit,
        {"op": "remove", "path": f"{MEMBERS}/M-3"},
        {"op": "replace", "path": f"team.projects.{SLUG}/builders_at_once", "value": 2},
    )
    assert resp.status_code == 200, resp.text
    block = team.charter_team(cockpit.deps, cockpit.fid, project)
    assert block is not None and block.builders_at_once == 2
    charter = render_pl_charter(project, team=block).splitlines()
    assert "IC budget: up to 2 ICs at once (the team's builders at once)" in charter
    assert "- M-1 project lead · you" in charter
    assert "- M-4 · reviewer · agent reviewer · codex · default model" in charter
    assert not any("ic_codex" in line for line in charter)
    brief = brief_text(project, by="alice", via="cockpit", mail_id="mail_x", team=block)
    assert "Team (definition v2 · template standard · 5 members" in brief
