"""Scout drafts the product profile, a first goal and new sources as proposals owners decide.

Scout proposes only under ``product``, ``strategy`` and ``discovery.sources``
(``kind="tuning"``, with evidence, 2 a day); every such change waits for an
owner (class B), never runs as a trial, and its card lands in ``#discovery``.
It co-signs nothing, proposes no rules or code, and is refused while
discovery is off. Every product word is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.server.factory import definition
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import answer, row
from tests.server.factory.discovery_helpers import discovery_cockpit
from tests.server.factory.source_helpers import add_scout


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value


async def _propose(cockpit: Cockpit, session_id: str, **body: Any) -> httpx.Response:
    payload = {
        "from_session_id": session_id,
        "kind": "tuning",
        "rationale": "Drafted from the README and the first signals (sample)",
        "evidence": ["repo:README.md", "S-1"],
        **body,
    }
    return await cockpit.client.post(
        "/v1/factory/agent/proposals", json=payload, headers={"X-Test-User": "alice"}
    )


async def test_scout_drafts_the_profile_and_an_owner_applies_it(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    scout = add_scout(cockpit, monkeypatch)

    resp = await _propose(
        cockpit,
        scout,
        title="Product profile (draft)",
        changes=[
            {"op": "replace", "path": "product.name", "value": "Sample App (sample)"},
            {"op": "replace", "path": "product.summary", "value": "A sample product (sample)."},
            {
                "op": "add",
                "path": "product.segments",
                "value": {"id": "admins", "label": "Admins (sample)", "rule": "manages a team"},
            },
        ],
    )

    assert resp.status_code == 200, resp.text
    proposal = row(cockpit)
    assert (proposal.class_, proposal.approval, proposal.status) == ("B", "owner", "pending")
    assert proposal.thread == "discovery" and proposal.author_role == "scout"
    assert definition.value(cockpit.deps, cockpit.fid, "product.name") is None
    applied = await answer(cockpit, 1)
    assert applied.status_code == 200, applied.text
    definition.invalidate(cockpit.fid)
    assert definition.value(cockpit.deps, cockpit.fid, "product.name") == "Sample App (sample)"
    # Strategy and profile are judged on outcomes: never a trial.
    assert row(cockpit).status in ("applied", "kept")


async def test_scout_suggests_a_first_goal(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    scout = add_scout(cockpit, monkeypatch)

    resp = await _propose(
        cockpit,
        scout,
        changes=[
            {
                "op": "add",
                "path": "strategy.goals",
                "value": {"statement": "More teams finish a first project (sample)", "weight": 1},
            }
        ],
    )

    assert resp.status_code == 200, resp.text
    assert row(cockpit).class_ == "B"


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        (
            {"changes": [{"op": "replace", "path": "discovery.rubric.window_days", "value": 60}]},
            403,
            "path_not_allowed",
        ),
        (
            {"changes": [{"op": "replace", "path": "briefs.scout", "value": "be bold (sample)"}]},
            403,
            "path_not_allowed",
        ),
        (
            {"changes": [{"op": "replace", "path": "discovery.enabled", "value": False}]},
            403,
            "path_not_allowed",
        ),
        ({"kind": "rules", "rule": {"role": "scout", "text": "x (sample)"}}, 403, "not_allowed"),
        ({"kind": "code", "changes": []}, 403, "not_allowed"),
        ({"op": "cosign", "id": "C-1"}, 403, "not_allowed"),
        (
            {"evidence": [], "changes": [{"op": "replace", "path": "product.name", "value": "x"}]},
            422,
            "evidence_required",
        ),
    ],
)
async def test_what_scout_may_not_propose(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any], status: int, code: str
) -> None:
    scout = add_scout(cockpit, monkeypatch)

    resp = await _propose(cockpit, scout, **body)

    assert resp.status_code == status and error_code(resp) == code, resp.text


async def test_two_a_day(cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch) -> None:
    scout = add_scout(cockpit, monkeypatch)
    for path, value in (("product.name", "A (sample)"), ("product.kind", "web app (sample)")):
        ok = await _propose(
            cockpit, scout, changes=[{"op": "replace", "path": path, "value": value}]
        )
        assert ok.status_code == 200, ok.text

    third = await _propose(
        cockpit, scout, changes=[{"op": "replace", "path": "product.summary", "value": "x"}]
    )

    assert third.status_code == 429 and error_code(third) == "daily_quota"


async def test_refused_while_discovery_is_off(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    scout = add_scout(cockpit, monkeypatch)
    definition.set_values(
        cockpit.deps, cockpit.fid, {"discovery.enabled": False}, created_by="alice"
    )
    definition.invalidate(cockpit.fid)

    resp = await _propose(
        cockpit, scout, changes=[{"op": "replace", "path": "product.name", "value": "x"}]
    )

    assert resp.status_code == 404 and error_code(resp) == "discovery_off"
