"""Tests for the managed-sandbox GitHub-credential refresh sweep.

The critical property is the scope guard: the sweep must push ONLY to
server-managed sandbox hosts, never to a user's personal machine (which also
runs ``omnigent host`` and whose real git credentials must not be clobbered).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.server import github_refresh


class FakeConn:
    """Minimal stand-in for HostConnection with the fields the sweep touches."""

    def __init__(self, host_id: str, owner: str | None, workspace_id: int = 0) -> None:
        self.host_id = host_id
        self.owner = owner
        self.workspace_id = workspace_id
        self.pending_github_refreshes: dict[str, Any] = {}


class FakeRegistry:
    def __init__(
        self,
        conns: list[FakeConn],
        *,
        reply: str = "ok",
        error: str | None = None,
        silent_hosts: set[str] | None = None,
    ) -> None:
        self._conns = conns
        self._reply = reply
        self._error = error
        # Hosts that never ack (simulate an older sandbox host that ignores the
        # unknown frame) — their future is left unresolved so the pusher times
        # out on them alone.
        self._silent = silent_hosts or set()
        self.sent: list[str] = []

    def all_connections(self) -> list[FakeConn]:
        return list(self._conns)

    def send_text(self, conn: FakeConn, _data: str) -> None:
        self.sent.append(conn.host_id)
        if conn.host_id in self._silent:
            return  # no ack — the pusher will time out for this host only
        # Simulate an instant host ack by resolving the pending future the
        # pusher just registered, so `_push_refresh`'s await returns at once.
        for fut in conn.pending_github_refreshes.values():
            if not fut.done():
                fut.set_result({"status": self._reply, "error": self._error})


class FakeHostStore:
    def __init__(self, hosts: dict[str, Any]) -> None:
        self._hosts = hosts

    def get_host(self, host_id: str) -> Any:
        return self._hosts.get(host_id)


def _managed(host_id: str) -> SimpleNamespace:
    return SimpleNamespace(host_id=host_id, sandbox_provider="kubernetes")


def _personal(host_id: str) -> SimpleNamespace:
    return SimpleNamespace(host_id=host_id, sandbox_provider=None)


def _github_store(login: str | None) -> Any:
    return SimpleNamespace(
        get=lambda _owner, with_tokens=False: (
            SimpleNamespace(github_login=login) if login is not None else None
        )
    )


@pytest.fixture(autouse=True)
def _stub_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: resolve_access_token returns a token (overridable per test)."""

    async def _ok(_owner: str, **_kw: Any) -> str | None:
        return "ghu_fresh"

    monkeypatch.setattr(github_refresh, "resolve_access_token", _ok)


async def _sweep(
    registry: FakeRegistry, host_store: FakeHostStore, login: str | None = "octocat"
) -> int:
    return await github_refresh.refresh_once(
        host_registry=registry,
        host_store=host_store,
        github_store=_github_store(login),
        github_client=object(),
    )


async def test_pushes_to_a_managed_sandbox() -> None:
    reg = FakeRegistry([FakeConn("h1", owner="u1")])
    count = await _sweep(reg, FakeHostStore({"h1": _managed("h1")}))
    assert count == 1
    assert reg.sent == ["h1"]


async def test_never_touches_a_personal_machine() -> None:
    # A personal-machine host (no sandbox_provider) must be skipped even though
    # its owner has a GitHub connection — overwriting its creds would clobber
    # the user's own git setup.
    reg = FakeRegistry([FakeConn("h1", owner="u1")])
    count = await _sweep(reg, FakeHostStore({"h1": _personal("h1")}))
    assert count == 0
    assert reg.sent == []


async def test_skips_local_and_ownerless_hosts() -> None:
    reg = FakeRegistry([FakeConn("h1", owner="local"), FakeConn("h2", owner=None)])
    count = await _sweep(reg, FakeHostStore({"h1": _managed("h1"), "h2": _managed("h2")}))
    assert count == 0
    assert reg.sent == []


async def test_skips_owner_without_github_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _none(_owner: str, **_kw: Any) -> str | None:
        return None

    monkeypatch.setattr(github_refresh, "resolve_access_token", _none)
    reg = FakeRegistry([FakeConn("h1", owner="u1")])
    count = await _sweep(reg, FakeHostStore({"h1": _managed("h1")}), login=None)
    assert count == 0
    assert reg.sent == []


async def test_one_host_failure_does_not_stop_the_sweep() -> None:
    # Two managed hosts; the host reports failure. The sweep counts zero
    # successes but still attempts both and never raises.
    reg = FakeRegistry(
        [FakeConn("h1", owner="u1"), FakeConn("h2", owner="u2")],
        reply="failed",
        error="disk full",
    )
    count = await _sweep(reg, FakeHostStore({"h1": _managed("h1"), "h2": _managed("h2")}))
    assert count == 0
    assert sorted(reg.sent) == ["h1", "h2"]


async def test_unresponsive_host_does_not_block_others(monkeypatch: pytest.MonkeyPatch) -> None:
    # h1 never acks (an older host ignoring the frame); h2 acks. Concurrency
    # means the whole sweep costs ~one timeout, not one-per-stale-host, and h2
    # still succeeds despite h1 hanging.
    monkeypatch.setattr(github_refresh, "_PUSH_TIMEOUT_S", 0.2)
    reg = FakeRegistry(
        [FakeConn("h1", owner="u1"), FakeConn("h2", owner="u2")],
        silent_hosts={"h1"},
    )
    count = await _sweep(reg, FakeHostStore({"h1": _managed("h1"), "h2": _managed("h2")}))
    assert count == 1  # only h2 acked
    assert sorted(reg.sent) == ["h1", "h2"]  # both were attempted


async def test_refresh_margin_exceeds_interval() -> None:
    # Invariant: a pushed token must outlive the gap to the next sweep.
    assert github_refresh._REFRESH_MARGIN_S > github_refresh.REFRESH_INTERVAL_S
