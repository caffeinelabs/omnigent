"""Tests for ``_codex_native_launch_config`` in ``omnigent/runner/app.py``.

The runner fetches a session snapshot over HTTP and validates it before
launching a runner-owned Codex terminal. Each malformed field is meant to
fail loud with a RuntimeError rather than launch Codex with garbage; those
guards were previously unexercised by any direct test. These tests drive the
function with a stub async client returning controlled snapshots.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from omnigent.runner.app import _codex_native_launch_config


class _Resp:
    """Minimal stand-in for an httpx response carrying a fixed status + payload."""

    def __init__(self, status_code: int, payload: Any, *, json_raises: bool = False) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_raises = json_raises

    def json(self) -> Any:
        if self._json_raises:
            raise ValueError("not json")
        return self._payload


class _Client:
    """Async client stub whose ``get`` returns a fixed response or raises."""

    def __init__(self, resp: _Resp | None = None, raise_exc: Exception | None = None) -> None:
        self._resp = resp
        self._raise_exc = raise_exc

    async def get(self, url: str, timeout: float | None = None) -> _Resp:
        if self._raise_exc is not None:
            raise self._raise_exc
        assert self._resp is not None
        return self._resp


async def _run(client: _Client | None, session_id: str = "conv_1") -> Any:
    return await _codex_native_launch_config(session_id=session_id, server_client=client)


@pytest.mark.asyncio
async def test_missing_client_raises() -> None:
    """No server client means there is no way to fetch config — fail loud."""
    with pytest.raises(RuntimeError, match="server_client is required"):
        await _run(None)


@pytest.mark.asyncio
async def test_http_error_raises() -> None:
    """A transport error fetching the snapshot surfaces as a RuntimeError."""
    client = _Client(raise_exc=httpx.ConnectError("boom"))
    with pytest.raises(RuntimeError, match="Could not fetch Codex launch config"):
        await _run(client)


@pytest.mark.asyncio
async def test_non_200_raises() -> None:
    """A non-200 status is rejected and names the status in the error."""
    client = _Client(_Resp(404, None))
    with pytest.raises(RuntimeError, match="returned 404"):
        await _run(client)


@pytest.mark.asyncio
async def test_invalid_json_raises() -> None:
    """A body that does not parse as JSON is rejected."""
    client = _Client(_Resp(200, None, json_raises=True))
    with pytest.raises(RuntimeError, match="invalid JSON"):
        await _run(client)


@pytest.mark.asyncio
async def test_non_dict_snapshot_raises() -> None:
    """A JSON array (not an object) is not a valid session snapshot."""
    client = _Client(_Resp(200, ["not", "a", "dict"]))
    with pytest.raises(RuntimeError, match="not a JSON object"):
        await _run(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("terminal_launch_args", "not-a-list", "terminal_launch_args"),
        ("terminal_launch_args", [1, 2], "terminal_launch_args"),
        ("model_override", "", "model_override"),
        ("model_override", 5, "model_override"),
        ("external_session_id", "", "external_session_id"),
        ("workspace", "", "workspace"),
    ],
)
async def test_invalid_field_raises(field: str, value: Any, match: str) -> None:
    """Each malformed optional field is rejected with a field-specific error."""
    client = _Client(_Resp(200, {field: value}))
    with pytest.raises(RuntimeError, match=match):
        await _run(client)


@pytest.mark.asyncio
async def test_happy_path_parses_full_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """A well-formed snapshot (with fork labels) parses into a launch config."""
    monkeypatch.setenv("RUNNER_SERVER_URL", "http://127.0.0.1:8123")
    snapshot = {
        "workspace": "/tmp/repo",
        "terminal_launch_args": ["--config", "approval_policy=on-request"],
        "model_override": "gpt-5.4-mini",
        "external_session_id": "thread_abc",
        "labels": {
            "omnigent.fork.source_id": "conv_source",
            "omnigent.fork.source_external_session_id": "thread_src",
            "omnigent.fork.carry_history": "1",
            "omnigent.codex_native.bypass_sandbox": "1",
        },
    }
    cfg = await _run(_Client(_Resp(200, snapshot)))
    assert cfg.policy_server_url == "http://127.0.0.1:8123"
    assert cfg.terminal_launch_args == ["--config", "approval_policy=on-request"]
    assert cfg.model_override == "gpt-5.4-mini"
    assert cfg.external_session_id == "thread_abc"
    assert cfg.fork_source_id == "conv_source", "Fork source id should be read from labels."
    assert cfg.fork_source_external_id == "thread_src"
    assert cfg.fork_carry_history is True, "carry_history label '1' should parse to True."
    assert cfg.bypass_sandbox is True, "bypass_sandbox label '1' should parse to True."
    assert cfg.workspace.name == "repo", (
        f"Workspace path should resolve from snapshot, got {cfg.workspace}."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "labels",
    [
        {"omnigent.codex_native.bypass_sandbox": "0"},  # explicit off
        {"omnigent.codex_native.bypass_sandbox": "true"},  # only "1" arms it
        {"omnigent.codex_native.bypass_sandbox": ""},  # empty string
    ],
)
async def test_bypass_sandbox_defaults_off_unless_label_is_one(
    monkeypatch: pytest.MonkeyPatch, labels: dict[str, str]
) -> None:
    """
    Fail-safe: ``bypass_sandbox`` is False unless the label is ``"1"`` or
    the host probe arms it.

    When a bypass label is PRESENT (any value — ``"0"``, ``"true"``, ``""``),
    it decides the stance: only the canonical ``"1"`` (set by the guarded
    web toggle) arms the full bypass, every near-miss leaves Codex's normal
    approval/sandbox stance, and the host probe is never consulted — a
    deliberate ``"0"`` opt-out is honored even on a hardened host. (The
    no-labels-at-all case is covered by
    ``test_userns_capable_host_keeps_normal_stance`` and
    ``test_host_without_userns_auto_enables_bypass`` — with no label the
    host probe decides.)
    """
    monkeypatch.setenv("RUNNER_SERVER_URL", "http://127.0.0.1:8123")
    # Pin the host probe so this test doesn't depend on the CI runner's
    # kernel: with a label present the probe must not even be consulted.
    probe_calls: list[int] = []

    def _unexpected_probe() -> bool:
        probe_calls.append(1)
        return False

    monkeypatch.setattr("omnigent.inner.sandbox.linux_userns_supported", _unexpected_probe)
    snapshot: dict[str, Any] = {"workspace": "/tmp/repo", "labels": dict(labels)}
    cfg = await _run(_Client(_Resp(200, snapshot)))
    assert cfg.bypass_sandbox is False
    assert probe_calls == [], "a stored label must decide the stance, not the host probe"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "labels",
    [
        None,  # no labels at all — nothing decided the stance explicitly
        {},  # labels present but no bypass key
    ],
)
async def test_host_without_userns_auto_enables_bypass(
    monkeypatch: pytest.MonkeyPatch, labels: Any
) -> None:
    """
    A hardened container (no unprivileged user namespaces) auto-enables bypass.

    On such a host Codex's own command sandbox cannot start, so every
    shell command hard-fails with bwrap's "No permissions to create new
    namespace" and the session is unusable (#657). When the snapshot
    carries NO bypass label, the runner consults the host probe and arms
    the same full-bypass stance the web toggle would have — plus
    ``host_userns_unavailable`` so the launch site persists the label.
    """
    monkeypatch.setenv("RUNNER_SERVER_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr("omnigent.inner.sandbox.linux_userns_supported", lambda: False)
    snapshot: dict[str, Any] = {"workspace": "/tmp/repo"}
    if labels is not None:
        snapshot["labels"] = labels
    cfg = await _run(_Client(_Resp(200, snapshot)))
    assert cfg.bypass_sandbox is True, (
        "A host that cannot run Codex's command sandbox must default to bypass."
    )
    assert cfg.host_userns_unavailable is True, (
        "The launch site persists the label only when the stance was host-derived."
    )


@pytest.mark.asyncio
async def test_explicit_label_wins_over_host_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    An explicit label decides the stance; the host probe is never consulted.

    ``"1"`` stays armed AND ``"0"`` stays disarmed even on a host whose
    command sandbox can never start — a deliberate operator choice (or the
    persisted record of one) outranks the host-based default. Neither case
    flags ``host_userns_unavailable``, so the launch site writes no label.
    """
    monkeypatch.setenv("RUNNER_SERVER_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr("omnigent.inner.sandbox.linux_userns_supported", lambda: False)
    snapshot: dict[str, Any] = {
        "workspace": "/tmp/repo",
        "labels": {"omnigent.codex_native.bypass_sandbox": "0"},
    }
    cfg = await _run(_Client(_Resp(200, snapshot)))
    assert cfg.bypass_sandbox is False
    assert cfg.host_userns_unavailable is False
    snapshot["labels"] = {"omnigent.codex_native.bypass_sandbox": "1"}
    cfg = await _run(_Client(_Resp(200, snapshot)))
    assert cfg.bypass_sandbox is True
    assert cfg.host_userns_unavailable is False


@pytest.mark.asyncio
async def test_userns_capable_host_keeps_normal_stance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a host where the command sandbox works, nothing changes."""
    monkeypatch.setenv("RUNNER_SERVER_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr("omnigent.inner.sandbox.linux_userns_supported", lambda: True)
    cfg = await _run(_Client(_Resp(200, {"workspace": "/tmp/repo"})))
    assert cfg.bypass_sandbox is False
    assert cfg.host_userns_unavailable is False


@pytest.mark.asyncio
async def test_probe_failure_keeps_normal_stance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A crashing host probe must not block the launch NOR arm bypass.

    Failing closed (normal stance) is the fail-safe direction here: a
    session that hits the bwrap error still gets the forwarder's recovery
    guidance, while a silently disarmed sandbox is never entered by
    accident on an inconclusive probe.
    """
    monkeypatch.setenv("RUNNER_SERVER_URL", "http://127.0.0.1:8123")

    def _boom() -> bool:
        raise OSError("seccomp denies everything")

    monkeypatch.setattr("omnigent.inner.sandbox.linux_userns_supported", _boom)
    cfg = await _run(_Client(_Resp(200, {"workspace": "/tmp/repo"})))
    assert cfg.bypass_sandbox is False
    assert cfg.host_userns_unavailable is False
