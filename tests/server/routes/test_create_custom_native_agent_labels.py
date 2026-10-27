"""Create-time terminal-first labels for custom-named native built-ins.

A built-in whose own spec runs a native harness under a custom name (e.g.
``factory-lead`` on ``claude-native``) must render terminal-first and take
the native single-writer path from its first message, exactly like the
canonical ``claude-native-ui`` wrapper. The JSON ``POST /v1/sessions``
create stamps the wrapper labels for it while keeping caller labels.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from omnigent._wrapper_labels import (
    CLAUDE_NATIVE_WRAPPER_VALUE,
    CODEX_NATIVE_WRAPPER_VALUE,
    UI_MODE_LABEL_KEY,
    UI_MODE_TERMINAL_VALUE,
    WRAPPER_LABEL_KEY,
)
from omnigent.db.utils import builtin_agent_id
from omnigent.factory import LABEL_ROLE, ROLE_PROJECT_LEAD
from omnigent.harness_plugins import CLAUDE_NATIVE_CODING_AGENT
from omnigent.server.managed_hosts import ManagedLaunchTracker, parse_sandbox_config
from omnigent.server.routes.sessions import routes_core
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from tests.server.helpers import create_test_agent, factory_guardrails

_CLAUDE_NATIVE = {"type": "omnigent", "config": {"harness": "claude-native"}}
_CODEX_NATIVE = {"type": "omnigent", "config": {"harness": "codex-native"}}
_CLAUDE_SDK = {"type": "omnigent", "config": {"harness": "claude-sdk"}}
_CALLER_LABELS = {LABEL_ROLE: ROLE_PROJECT_LEAD}


async def _seed_builtin(
    client: httpx.AsyncClient,
    db_uri: str,
    name: str,
    executor: dict[str, Any],
    sub_agents: list[dict[str, Any]] | None = None,
    guardrails: dict[str, Any] | None = None,
) -> str:
    """
    Register a built-in (template) agent over a freshly uploaded bundle.

    The ``app`` fixture skips the lifespan seeding, so the bundle is uploaded
    through a multipart create and re-registered under the deterministic
    built-in id, the way ``OMNIGENT_BUILTIN_AGENT_DIRS`` seeds one.

    :param client: Test HTTP client.
    :param db_uri: Database URI for a direct agent-store handle.
    :param name: Built-in agent name, e.g. ``"factory-lead"``.
    :param executor: Executor block of the bundle spec.
    :param sub_agents: Optional sub-agent config dicts for the bundle.
    :param guardrails: Optional ``guardrails:`` block (factory roles need one).
    :returns: The built-in agent id.
    """
    source = await create_test_agent(
        client, name=name, executor=executor, sub_agents=sub_agents, guardrails=guardrails
    )
    store = SqlAlchemyAgentStore(db_uri)
    uploaded = store.get(str(source["id"]))
    assert uploaded is not None
    agent_id = builtin_agent_id(name)
    store.create(agent_id, name=name, bundle_location=uploaded.bundle_location)
    return agent_id


async def _create(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    """
    POST a JSON session create and return the persisted labels.

    :param client: Test HTTP client.
    :param body: The JSON create body.
    :returns: The session snapshot's labels read back after the create.
    """
    resp = await client.post("/v1/sessions", json=body)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    snapshot = await client.get(f"/v1/sessions/{created['id']}")
    assert snapshot.status_code == 200, snapshot.text
    labels = snapshot.json()["labels"]
    assert labels == created["labels"], "create response and stored labels must agree"
    return labels


def _arm_managed_app(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """
    Wire the app for managed creates and record the scheduled launches.

    :param app: The app under test.
    :param monkeypatch: Fixture used to swap the background launch.
    :returns: The list the recorder appends each launch's kwargs to.
    """
    app.state.sandbox_config = parse_sandbox_config(
        {"provider": "modal", "server_url": "https://managed-test.example.com"}
    )
    # Never dereferenced: the recorder replaces the only consumer.
    app.state.host_store = object()
    app.state.managed_launches = ManagedLaunchTracker()
    launches: list[dict[str, Any]] = []

    async def _record(**kwargs: Any) -> None:
        """Record the launch instead of provisioning a sandbox."""
        launches.append(kwargs)

    monkeypatch.setattr(routes_core, "_run_managed_launch", _record)
    return launches


async def test_custom_native_builtin_gets_wrapper_labels(
    client: httpx.AsyncClient, db_uri: str
) -> None:
    """A top-level custom claude-native built-in is terminal-first and keeps caller labels."""
    agent_id = await _seed_builtin(client, db_uri, "factory-lead", _CLAUDE_NATIVE)

    labels = await _create(client, {"agent_id": agent_id, "labels": _CALLER_LABELS})

    assert labels[WRAPPER_LABEL_KEY] == CLAUDE_NATIVE_WRAPPER_VALUE
    assert labels[UI_MODE_LABEL_KEY] == UI_MODE_TERMINAL_VALUE
    assert labels[LABEL_ROLE] == ROLE_PROJECT_LEAD


async def test_custom_native_builtin_managed_create_gets_wrapper_labels(
    app: FastAPI,
    client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A managed create (no host yet) is stamped before the sandbox launch."""
    launches = _arm_managed_app(app, monkeypatch)
    agent_id = await _seed_builtin(client, db_uri, "factory-pl", _CLAUDE_NATIVE)

    labels = await _create(
        client,
        {"agent_id": agent_id, "host_type": "managed", "labels": _CALLER_LABELS},
    )

    assert len(launches) == 1, "the managed create must schedule one sandbox launch"
    assert launches[0]["agent_id"] == agent_id
    assert labels[WRAPPER_LABEL_KEY] == CLAUDE_NATIVE_WRAPPER_VALUE
    assert labels[UI_MODE_LABEL_KEY] == UI_MODE_TERMINAL_VALUE
    assert labels[LABEL_ROLE] == ROLE_PROJECT_LEAD


async def test_canonical_native_wrapper_labels_unchanged(
    client: httpx.AsyncClient, db_uri: str
) -> None:
    """The canonical ``claude-native-ui`` keeps exactly its presentation labels."""
    agent_id = await _seed_builtin(
        client, db_uri, CLAUDE_NATIVE_CODING_AGENT.agent_name, _CLAUDE_NATIVE
    )

    labels = await _create(client, {"agent_id": agent_id, "labels": _CALLER_LABELS})

    assert labels == {**_CALLER_LABELS, **CLAUDE_NATIVE_CODING_AGENT.presentation_labels}


async def test_claude_sdk_builtin_gets_no_wrapper_labels(
    client: httpx.AsyncClient, db_uri: str
) -> None:
    """A non-native built-in is untouched: only the caller's labels persist."""
    agent_id = await _seed_builtin(client, db_uri, "factory-sdk", _CLAUDE_SDK)

    labels = await _create(client, {"agent_id": agent_id, "labels": _CALLER_LABELS})

    assert labels == _CALLER_LABELS


async def test_uploaded_custom_native_agent_stays_chat_first(
    client: httpx.AsyncClient,
) -> None:
    """A session-scoped (uploaded) native bundle keeps its chat-first presentation."""
    uploaded = await create_test_agent(client, name="my-native", executor=_CLAUDE_NATIVE)

    labels = await _create(client, {"agent_id": uploaded["id"], "labels": _CALLER_LABELS})

    assert labels == _CALLER_LABELS


@pytest.mark.parametrize("override", ["claude-sdk", "auto"])
async def test_harness_override_off_native_skips_wrapper_labels(
    client: httpx.AsyncClient, db_uri: str, override: str
) -> None:
    """An override that moves the brain off the spec's native harness gets no wrapper."""
    agent_id = await _seed_builtin(client, db_uri, "factory-lead", _CLAUDE_NATIVE)

    labels = await _create(
        client,
        {"agent_id": agent_id, "harness_override": override, "labels": _CALLER_LABELS},
    )

    assert WRAPPER_LABEL_KEY not in labels
    assert UI_MODE_LABEL_KEY not in labels
    assert labels[LABEL_ROLE] == ROLE_PROJECT_LEAD


async def test_harness_override_to_other_native_takes_its_wrapper(
    client: httpx.AsyncClient, db_uri: str
) -> None:
    """An override to another native harness renders that CLI's terminal-first UI."""
    agent_id = await _seed_builtin(client, db_uri, "factory-lead", _CLAUDE_NATIVE)

    labels = await _create(
        client,
        {"agent_id": agent_id, "harness_override": "codex-native", "labels": _CALLER_LABELS},
    )

    assert labels[WRAPPER_LABEL_KEY] == CODEX_NATIVE_WRAPPER_VALUE
    assert labels[UI_MODE_LABEL_KEY] == UI_MODE_TERMINAL_VALUE
    assert labels[LABEL_ROLE] == ROLE_PROJECT_LEAD


async def test_native_override_on_sdk_builtin_stays_unlabelled(
    client: httpx.AsyncClient, db_uri: str
) -> None:
    """A non-native built-in keeps today's labels even under a native override."""
    agent_id = await _seed_builtin(client, db_uri, "factory-sdk", _CLAUDE_SDK)

    labels = await _create(
        client,
        {"agent_id": agent_id, "harness_override": "claude-native", "labels": _CALLER_LABELS},
    )

    assert labels == _CALLER_LABELS


@pytest.mark.parametrize(
    "sub_agent,expected",
    [
        ("reviewer", {WRAPPER_LABEL_KEY: CODEX_NATIVE_WRAPPER_VALUE}),
        ("helper", None),
    ],
)
async def test_subagent_create_uses_sub_spec(
    client: httpx.AsyncClient,
    db_uri: str,
    sub_agent: str,
    expected: dict[str, str] | None,
) -> None:
    """A sub-agent create resolves labels from its sub-spec, never the parent's spec."""
    agent_id = await _seed_builtin(
        client,
        db_uri,
        "factory-pl",
        _CLAUDE_NATIVE,
        sub_agents=[
            {"name": "reviewer", "executor": _CODEX_NATIVE},
            {"name": "helper", "executor": _CLAUDE_SDK},
        ],
    )
    parent = await client.post("/v1/sessions", json={"agent_id": agent_id})
    assert parent.status_code == 201, parent.text

    labels = await _create(
        client,
        {
            "agent_id": agent_id,
            "parent_session_id": parent.json()["id"],
            "sub_agent_name": sub_agent,
        },
    )

    if expected is None:
        assert WRAPPER_LABEL_KEY not in labels
        assert UI_MODE_LABEL_KEY not in labels
    else:
        assert labels[WRAPPER_LABEL_KEY] == expected[WRAPPER_LABEL_KEY]
        assert labels[UI_MODE_LABEL_KEY] == UI_MODE_TERMINAL_VALUE


async def test_factory_agent_spec_owns_its_launch_permission_mode(
    client: httpx.AsyncClient, db_uri: str
) -> None:
    """A composer permission-mode pick does not override a factory agent's spec mode."""
    executor = {
        "type": "omnigent",
        "config": {
            "harness": "claude-native",
            "permission_mode": "bypassPermissions",
            "factory_role": "lead",
        },
    }
    agent_id = await _seed_builtin(
        client, db_uri, "factory-lead", executor, guardrails=factory_guardrails("lead")
    )

    resp = await client.post(
        "/v1/sessions",
        json={"agent_id": agent_id, "terminal_launch_args": ["--permission-mode", "auto"]},
    )
    assert resp.status_code == 201, resp.text
    snapshot = await client.get(f"/v1/sessions/{resp.json()['id']}")
    assert snapshot.json()["terminal_launch_args"] == ["--permission-mode", "bypassPermissions"]


async def test_non_factory_agent_keeps_the_composer_permission_mode(
    client: httpx.AsyncClient, db_uri: str
) -> None:
    """Ordinary custom native built-ins keep the caller's permission mode."""
    executor = {
        "type": "omnigent",
        "config": {"harness": "claude-native", "permission_mode": "bypassPermissions"},
    }
    agent_id = await _seed_builtin(client, db_uri, "custom-claude", executor)

    resp = await client.post(
        "/v1/sessions",
        json={"agent_id": agent_id, "terminal_launch_args": ["--permission-mode", "auto"]},
    )
    assert resp.status_code == 201, resp.text
    snapshot = await client.get(f"/v1/sessions/{resp.json()['id']}")
    assert snapshot.json()["terminal_launch_args"] == ["--permission-mode", "auto"]
