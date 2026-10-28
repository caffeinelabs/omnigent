"""
A spec never puts its session into a factory through labels.

An uploaded spec could declare ``guardrails.labels`` with ``initial``
``omnigent.factory.*`` values (``id``, ``role=root``, ``engine_mode``, ...).
The first policy-engine build, which any turn or a direct
``POST /v1/sessions/{id}/policies/evaluate`` triggers, seeded them onto the
session, and a policy's ``set_labels`` wrote them the same way. Such uploads
are now refused, and an agent stored before that check neither seeds nor
writes them.

Uses the shared ``client`` fixture from ``tests/server/conftest.py`` (real
stores and routes, no subprocesses).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from omnigent import factory as F
from omnigent.policies.types import PolicyResult
from omnigent.spec.types import PolicyAction
from tests.server.helpers import create_test_agent

pytestmark = pytest.mark.asyncio

_FACTORY_ID = "1f0c8a7e4b5d4c3b9a8e7f6d5c4b3a29"
_ENGINE_MODE = f"{F.LABEL_PREFIX}engine_mode"


def _write_factory_labels(event: dict[str, Any]) -> PolicyResult:
    """
    ALLOW every event and try to write factory membership labels.

    :param event: The evaluated event (unused).
    :returns: ALLOW carrying factory label writes and one ordinary label.
    """
    del event
    return PolicyResult(
        action=PolicyAction.ALLOW,
        set_labels={
            F.LABEL_ID: _FACTORY_ID,
            F.LABEL_ROLE: F.ROLE_LEAD,
            _ENGINE_MODE: "autonomous",
            "written": "1",
        },
    )


_SEEDING_GUARDRAILS: dict[str, Any] = {
    "labels": {
        F.LABEL_ID: {"initial": _FACTORY_ID},
        F.LABEL_ROLE: {"initial": "root"},
        _ENGINE_MODE: {"initial": "autonomous"},
        F.LABEL_DEF_VERSION: {"initial": "999"},
        "team": {"initial": "ml"},
    },
    "policies": {
        "writes_factory_labels": {
            "type": "function",
            "function": {"path": f"{__name__}._write_factory_labels"},
        },
    },
}


def _tool_call() -> dict[str, Any]:
    return {
        "event": {
            "type": "PHASE_TOOL_CALL",
            "target": "",
            "data": {"name": "Bash", "arguments": {}},
            "context": {},
        },
    }


async def test_an_upload_that_seeds_factory_labels_is_refused(client: httpx.AsyncClient) -> None:
    with pytest.raises(AssertionError, match=r"omnigent\.factory\.\* labels are set only"):
        await create_test_agent(client, guardrails=_SEEDING_GUARDRAILS)


async def test_a_stored_spec_never_seeds_or_writes_factory_labels(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server import bundles

    # An agent stored before uploads were checked for factory label defs.
    with monkeypatch.context() as patched:
        patched.setattr(bundles, "reject_factory_label_defs", lambda spec: None)
        agent = await create_test_agent(client, guardrails=_SEEDING_GUARDRAILS)
    session_id = agent["_session_id"]

    # The first evaluate builds the policy engine: label seeds, then the policy's writes.
    resp = await client.post(f"/v1/sessions/{session_id}/policies/evaluate", json=_tool_call())

    assert resp.status_code == 200, resp.text
    assert resp.json()["result"] == "POLICY_ACTION_ALLOW"
    session = await client.get(f"/v1/sessions/{session_id}")
    assert session.status_code == 200, session.text
    labels = session.json()["labels"]
    assert not [key for key in labels if F.is_factory_label(key)], labels
    assert labels["team"] == "ml"
    assert labels["written"] == "1"
