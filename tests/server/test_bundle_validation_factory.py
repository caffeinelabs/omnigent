"""Tier 0: factory agents always run fenced and never run a bundle of their own making.

``validate_agent_bundle`` rejects a factory-role spec (or sub-agent) without an
unconditional ``factory_role_guard`` for its role plus ``blast_radius``; every
shipped bundle passes. A ``sys_session_create(config_path=…)`` upload whose
parent belongs to a factory is refused with 403 ``factory_custom_bundle_denied``
(spike S5: such a child runs the uploaded spec, so it would escape the
parent's guard).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from omnigent import factory as F
from omnigent.errors import OmnigentError
from omnigent.server.bundles import validate_agent_bundle
from omnigent.spec import materialize_bundle
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from tests.server.helpers import build_agent_bundle, create_test_agent, factory_guardrails

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FACTORY_DIR = _REPO_ROOT / "examples" / "factory"


def _executor(role: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {"harness": "claude-native"}
    if role is not None:
        config["factory_role"] = role
    return {"type": "omnigent", "config": config}


def _guard_only(role: str) -> dict[str, Any]:
    guardrails = factory_guardrails(role)
    del guardrails["policies"]["blast_radius"]
    return guardrails


def _blast_only(role: str) -> dict[str, Any]:
    guardrails = factory_guardrails(role)
    del guardrails["policies"]["factory_guard"]
    return guardrails


def _conditional_guard(role: str) -> dict[str, Any]:
    guardrails = factory_guardrails(role)
    guardrails["policies"]["factory_guard"]["condition"] = {"never": "1"}
    return guardrails


def _validate(bundle: bytes) -> None:
    validate_agent_bundle(bundle, enforce_handler_allowlist=True)


# ── Bundle validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("guardrails", "why"),
    [
        (None, "no guardrails"),
        (_guard_only("ic"), "no blast_radius"),
        (_blast_only("ic"), "no factory_role_guard"),
        (factory_guardrails("project_lead"), "guard for another role"),
        (_conditional_guard("ic"), "guard behind a label condition"),
    ],
)
def test_factory_spec_without_its_guards_is_rejected(
    guardrails: dict[str, Any] | None, why: str
) -> None:
    bundle = build_agent_bundle(
        "rogue-ic", executor=_executor("ic"), guardrails=guardrails, include_llm=False
    )
    with pytest.raises(OmnigentError, match="factory_role 'ic'") as exc:
        _validate(bundle)
    assert "factory_role_guard" in str(exc.value), why


def test_unguarded_factory_sub_agent_is_rejected() -> None:
    bundle = build_agent_bundle(
        "factory-pl",
        executor=_executor("project_lead"),
        guardrails=factory_guardrails("project_lead"),
        include_llm=False,
        sub_agents=[{"name": "ic", "description": "implementer", "executor": _executor("ic")}],
    )
    with pytest.raises(OmnigentError, match="'ic' declares factory_role 'ic'"):
        _validate(bundle)


def test_guarded_factory_specs_and_plain_specs_pass() -> None:
    guarded = build_agent_bundle(
        "factory-pl",
        executor=_executor("project_lead"),
        guardrails=factory_guardrails("project_lead"),
        include_llm=False,
        sub_agents=[
            {
                "name": "ic",
                "description": "implementer",
                "executor": _executor("ic"),
                "guardrails": factory_guardrails("ic"),
            },
            {"name": "helper", "description": "no factory role"},
        ],
    )
    assert _validate(guarded) is None
    plain = build_agent_bundle("plain", executor=_executor(None), include_llm=False)
    assert _validate(plain) is None


def test_trusted_local_uploads_are_checked_too() -> None:
    """The fence is not an untrusted-upload rule: a local operator's bundle is held to it."""
    bundle = build_agent_bundle("rogue-lead", executor=_executor("lead"), include_llm=False)
    with pytest.raises(OmnigentError, match="factory_role 'lead'"):
        validate_agent_bundle(bundle, enforce_handler_allowlist=False)


@pytest.mark.parametrize("bundle", ["factory-lead", "factory-pl"])
def test_every_shipped_factory_bundle_is_accepted(bundle: str, tmp_path: Path) -> None:
    from omnigent.server.app import _tar_gz_dir

    blob = _tar_gz_dir(materialize_bundle(_FACTORY_DIR / bundle, tmp_path / "src"))
    spec = validate_agent_bundle(blob, enforce_handler_allowlist=True)
    assert spec.name == bundle


# ── S5: config_path children of factory members ──────────────────────────────


async def _upload_child(client: httpx.AsyncClient, parent_id: str) -> httpx.Response:
    """POST the bundle a ``sys_session_create(config_path=…)`` sends, as *parent_id*'s child."""
    bundle = build_agent_bundle("my-helper", executor=_executor(None), include_llm=False)
    return await client.post(
        "/v1/sessions",
        data={"metadata": json.dumps({"parent_session_id": parent_id})},
        files={"bundle": ("agent.tar.gz", bundle, "application/gzip")},
    )


async def test_config_path_child_of_a_factory_member_is_denied(
    client: httpx.AsyncClient, db_uri: str
) -> None:
    """
    Spike S5: a config_path child runs its uploaded spec, so it would escape the guard.

    The server refuses it for the PL itself and for its IC (whose factory id
    lives on the root), and still admits it for an ordinary session.
    """
    pl_agent = await create_test_agent(
        client,
        name="factory-pl",
        executor=_executor("project_lead"),
        guardrails=factory_guardrails("project_lead"),
        include_llm=False,
        sub_agents=[
            {
                "name": "ic",
                "description": "implementer",
                "executor": _executor("ic"),
                "guardrails": factory_guardrails("ic"),
            }
        ],
    )
    pl_id = str(pl_agent["_session_id"])
    store = SqlAlchemyConversationStore(db_uri)
    store.set_labels(pl_id, {F.LABEL_ID: pl_id, F.LABEL_ROLE: F.ROLE_PROJECT_LEAD})
    ic = await client.post(
        "/v1/sessions",
        json={"agent_id": pl_agent["id"], "parent_session_id": pl_id, "sub_agent_name": "ic"},
    )
    assert ic.status_code == 201, ic.text

    for parent_id in (ic.json()["id"], pl_id):
        resp = await _upload_child(client, parent_id)
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "factory_custom_bundle_denied"

    plain = await create_test_agent(client, name="plain", executor=_executor(None))
    allowed = await _upload_child(client, str(plain["_session_id"]))
    assert allowed.status_code == 201, allowed.text
