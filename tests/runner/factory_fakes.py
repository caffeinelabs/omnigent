"""A scripted fake Omnigent server for the factory runner-tool tests.

Routes the runner's ``server_client`` through :class:`httpx.MockTransport`, so
the tests see the exact paths, query params and JSON bodies the factory
executors send, while coding strictly to the ``/v1/factory`` contract.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from omnigent.spec.types import AgentSpec, ExecutorSpec

LEAD = "conv_lead_a"
PEER = "conv_lead_b"
FACTORY = LEAD

Handler = Callable[["Recorded"], httpx.Response]


@dataclass
class Recorded:
    """One request the fake server received."""

    method: str
    path: str
    params: dict[str, str]
    body: Any


class FakeServer:
    """Answers scripted ``(method, path)`` routes and records every request.

    An unscripted route answers ``404 {"detail": "Not Found"}`` (FastAPI's
    unknown-route reply), so a test can assert it was never hit.
    """

    def __init__(self) -> None:
        self.requests: list[Recorded] = []
        self._routes: dict[tuple[str, str], list[Handler]] = {}

    def on(
        self,
        method: str,
        path: str,
        *,
        status: int = 200,
        body: object = None,
        handler: Handler | None = None,
        replace: bool = False,
    ) -> FakeServer:
        """Script a route; repeated calls queue answers (the last one sticks).

        ``replace=True`` drops any answers already scripted for the route.
        """
        if handler is None:
            payload = {} if body is None else body

            def handler(_req: Recorded) -> httpx.Response:
                return httpx.Response(status, json=payload)

        key = (method.upper(), path)
        if replace:
            self._routes.pop(key, None)
        self._routes.setdefault(key, []).append(handler)
        return self

    def client(self) -> httpx.AsyncClient:
        """An ``httpx.AsyncClient`` wired to this fake."""
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle),
            base_url="http://omnigent.test",
        )

    def calls(self, method: str | None = None, path: str | None = None) -> list[Recorded]:
        """Recorded requests, optionally filtered by method and/or path."""
        return [
            r
            for r in self.requests
            if (method is None or r.method == method.upper()) and (path is None or r.path == path)
        ]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        recorded = Recorded(
            method=request.method,
            path=request.url.path,
            params=dict(request.url.params),
            body=body,
        )
        self.requests.append(recorded)
        handlers = self._routes.get((request.method, request.url.path))
        if not handlers:
            return httpx.Response(404, json={"detail": "Not Found"})
        handler = handlers.pop(0) if len(handlers) > 1 else handlers[0]
        return handler(recorded)


def factory_spec(role: str | None, **config: object) -> AgentSpec:
    """A claude-native spec carrying ``executor.config.factory_role``."""
    executor_config: dict[str, object] = {"harness": "claude-native", **config}
    if role is not None:
        executor_config["factory_role"] = role
    return AgentSpec(
        spec_version=1,
        name="factory-test",
        executor=ExecutorSpec(type="omnigent", config=executor_config),
    )


def membership(
    session_id: str,
    *,
    role: str | None,
    factory_id: str | None = FACTORY,
    supervisor: str | None = None,
) -> dict[str, object]:
    """A ``GET /v1/factory/membership/{id}`` 200 body."""
    return {
        "session_id": session_id,
        "factory_id": factory_id,
        "role": role,
        "project": None,
        "parent_session_id": None,
        "supervisor_session_id": supervisor,
        "state": "active",
        "heartbeat_s": 3600,
        "health": None,
        "summary": None,
        "is_top_level": True,
        "root_session_id": session_id,
        "title": session_id,
    }


def roster(*members: dict[str, object]) -> dict[str, object]:
    """A ``GET /v1/factory/roster`` 200 body."""
    return {"factory_id": FACTORY, "members": list(members)}


def member(
    session_id: str,
    *,
    role: str,
    state: str = "active",
    project: str | None = None,
    children: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """One roster member row."""
    return {
        "session_id": session_id,
        "title": f"title {session_id}",
        "role": role,
        "project": project,
        "parent_session_id": LEAD if session_id != LEAD else None,
        "supervisor_session_id": None,
        "status": "idle",
        "runner_online": True,
        "last_activity_at": "2026-10-23T10:00:00Z",
        "heartbeat_s": 3600,
        "state": state,
        "health": "ok",
        "summary": None,
        "url": f"/c/{session_id}",
        "children": children or [],
    }


def pl_create_body(
    *,
    agent_id: str,
    factory_id: str,
    parent: str,
    supervisor: str,
    project: str,
    heartbeat_s: int,
    workspace: str,
    version_labels: dict[str, str] | None = None,
) -> dict[str, object]:
    """
    The managed ``POST /v1/sessions`` body of a PL spawn.

    Shared by the ``sys_factory_spawn`` tests and the server-spawn tests, so a
    server-spawned PL is created exactly like a lead-spawned one.

    :param version_labels: The ``version`` / ``bundle`` labels the spawn adds, if any.
    """
    from omnigent import factory as F

    return {
        "agent_id": agent_id,
        "host_type": "managed",
        "title": f"PL · {project}",
        "labels": {
            F.LABEL_ID: factory_id,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PARENT: parent,
            F.LABEL_PROJECT: project,
            F.LABEL_SUPERVISOR: supervisor,
            F.LABEL_STATE: "active",
            F.LABEL_HEARTBEAT_S: str(heartbeat_s),
            **(version_labels or {}),
        },
        "workspaces": [workspace],
    }
