"""Tests for the managed-path sandbox keepalive.

Covers the resolution chain (runner -> session -> host -> provider), the
per-runner rate limit, and the two skip paths (provider can't extend, host has
no sandbox). Stubs stand in for the stores/deployment: the module only reads a
few attributes off each, so a real store would add setup without adding cover.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnigent.onboarding.sandboxes.base import SandboxCapabilityError
from omnigent.server import managed_keepalive


class _Launcher:
    def __init__(self, raises: BaseException | None = None) -> None:
        self.calls: list[str] = []
        self._raises = raises

    def keep_alive(self, sandbox_id: str) -> None:
        self.calls.append(sandbox_id)
        if self._raises is not None:
            raise self._raises


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    launcher: _Launcher,
    host: object | None,
    host_id: str | None = "host1",
) -> None:
    """Point the module at stub stores returning one session on *host_id*."""
    conversations = SimpleNamespace(
        list_conversations_by_runner_id=lambda _rid: [SimpleNamespace(host_id=host_id)]
    )
    hosts = SimpleNamespace(get_host=lambda _hid: host)
    deployment = SimpleNamespace(
        recorded=lambda _provider: SimpleNamespace(launcher_factory=lambda: launcher)
    )
    monkeypatch.setattr(managed_keepalive, "_conversation_store", conversations)
    monkeypatch.setattr(managed_keepalive, "_host_store", hosts)
    monkeypatch.setattr(managed_keepalive, "_sandbox_config", deployment)


def test_extends_the_hosts_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = _Launcher()
    _wire(
        monkeypatch,
        launcher=launcher,
        host=SimpleNamespace(sandbox_id="sbx1", sandbox_provider="modal"),
    )
    managed_keepalive._keep_alive_for_runner("r1")
    assert launcher.calls == ["sbx1"]


def test_provider_without_keep_alive_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    # kubernetes today: the base class raises, and that must not propagate.
    launcher = _Launcher(raises=SandboxCapabilityError("nope"))
    _wire(
        monkeypatch,
        launcher=launcher,
        host=SimpleNamespace(sandbox_id="sbx1", sandbox_provider="kubernetes"),
    )
    managed_keepalive._keep_alive_for_runner("r1")
    assert launcher.calls == ["sbx1"]  # attempted, error swallowed


def test_store_failure_never_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_rid: str) -> list[object]:
        raise RuntimeError("db down")

    monkeypatch.setattr(
        managed_keepalive,
        "_conversation_store",
        SimpleNamespace(list_conversations_by_runner_id=_boom),
    )
    monkeypatch.setattr(
        managed_keepalive, "_host_store", SimpleNamespace(get_host=lambda _h: None)
    )
    monkeypatch.setattr(
        managed_keepalive, "_sandbox_config", SimpleNamespace(recorded=lambda _p: None)
    )
    managed_keepalive._keep_alive_for_runner("r1")  # must not raise


def test_cli_host_without_a_sandbox_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = _Launcher()
    _wire(
        monkeypatch,
        launcher=launcher,
        host=SimpleNamespace(sandbox_id=None, sandbox_provider=None),
    )
    managed_keepalive._keep_alive_for_runner("r1")
    assert launcher.calls == []


def test_touch_is_rate_limited_per_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[str] = []
    monkeypatch.setattr(managed_keepalive, "_sandbox_config", object())
    monkeypatch.setattr(managed_keepalive, "_host_store", object())
    monkeypatch.setattr(
        managed_keepalive,
        "_executor",
        SimpleNamespace(submit=lambda _fn, rid: submitted.append(rid)),
    )
    monkeypatch.setattr(managed_keepalive, "_last_kept", {})

    managed_keepalive.touch("r1")
    managed_keepalive.touch("r1")  # inside the window: dropped
    managed_keepalive.touch("r2")  # different runner: allowed
    assert submitted == ["r1", "r2"]


def test_touch_is_a_noop_without_a_sandbox_config(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[str] = []
    monkeypatch.setattr(managed_keepalive, "_sandbox_config", None)
    monkeypatch.setattr(
        managed_keepalive,
        "_executor",
        SimpleNamespace(submit=lambda _fn, rid: submitted.append(rid)),
    )
    monkeypatch.setattr(managed_keepalive, "_last_kept", {})
    managed_keepalive.touch("r1")
    assert submitted == []
