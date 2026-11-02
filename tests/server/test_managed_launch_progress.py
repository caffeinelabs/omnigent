"""What a starting managed sandbox waits on, and stopping a finished one.

A launcher that declares ``reports_launch_progress`` gets an ``on_progress``
observer at ``start_host``; the session's launch progress cache records each
step (the factory cockpit names it). ``stop_managed_sandbox`` stops a sandbox
but keeps its host row, so the next message wakes it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from omnigent.server.managed_hosts import (
    ManagedSandboxConfig,
    ManagedSandboxDeployment,
    launch_managed_host,
    stop_managed_sandbox,
)
from omnigent.server.routes._sessions.common import (
    _session_launch_progress_cache,
    _session_sandbox_status_cache,
)
from omnigent.server.routes._sessions.helpers import (
    _publish_launch_step,
    _publish_sandbox_status_impl,
)
from omnigent.stores.host_store import HostStore
from tests.server.helpers import FakeSandboxLauncher, HostStartInvocation

_OWNER = "alice@example.com"


class _ProgressFake(FakeSandboxLauncher):
    """A fake that reports launch progress the way the Kubernetes launcher does."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.observers: list[object] = []

    @property
    def capabilities(self) -> Any:
        return replace(super().capabilities, reports_launch_progress=True)

    def start_host(self, sandbox_id: str, *, on_progress: Any = None, **kwargs: Any) -> str:
        self.observers.append(on_progress)
        if on_progress is not None:
            on_progress("node", 1010)
            on_progress("image", 1100)
        return super().start_host(sandbox_id, **kwargs)


class _SuspendingFake(FakeSandboxLauncher):
    """A fake whose sandboxes can be stopped and woken in place."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.suspended: list[str] = []

    def suspend(self, sandbox_id: str) -> None:
        self.suspended.append(sandbox_id)


def _config(fake: FakeSandboxLauncher) -> ManagedSandboxDeployment:
    return ManagedSandboxDeployment.single(
        ManagedSandboxConfig(
            server_url="https://srv.example.com",
            launcher_factory=lambda: fake,
            token_ttl_s=3600,
        )
    )


def _registering(host_store: HostStore) -> Any:
    def _register(invocation: HostStartInvocation) -> None:
        host_store.upsert_on_connect(
            host_id=invocation.host_id, name=invocation.host_name, user_id=_OWNER
        )

    return _register


async def test_progress_reaches_a_launcher_that_reports_it(db_uri: str) -> None:
    host_store = HostStore(db_uri)
    fake = _ProgressFake(on_host_start=_registering(host_store))
    seen: list[tuple[str, int | None]] = []

    await launch_managed_host(
        config=_config(fake),
        owner=_OWNER,
        host_store=host_store,
        on_progress=lambda step, since: seen.append((step, since)),
    )

    assert seen == [("node", 1010), ("image", 1100)]


async def test_progress_never_reaches_a_launcher_that_does_not_report_it(db_uri: str) -> None:
    """The keyword is gated on the capability: an exec-model start_host has none."""
    host_store = HostStore(db_uri)
    fake = FakeSandboxLauncher(on_host_start=_registering(host_store))

    result = await launch_managed_host(
        config=_config(fake),
        owner=_OWNER,
        host_store=host_store,
        on_progress=lambda step, since: None,
    )

    assert host_store.get_host(result.host_id) is not None


def test_the_launch_step_rides_the_current_stage() -> None:
    sid = "conv_progress_steps"
    try:
        _publish_sandbox_status_impl(sid, "starting")
        _publish_launch_step(sid, "image", 1100)
        progress = _session_launch_progress_cache[sid]
        assert (progress.stage, progress.step, progress.since) == ("starting", "image", 1100)

        # A new stage starts its own clock and drops the step.
        _publish_sandbox_status_impl(sid, "connecting")
        progress = _session_launch_progress_cache[sid]
        assert (progress.stage, progress.step) == ("connecting", None)

        # A failed launch keeps when it failed; a late step does not overwrite it.
        _publish_sandbox_status_impl(sid, "failed", "pod did not start")
        failed_at = _session_launch_progress_cache[sid].since
        _publish_launch_step(sid, "host")
        assert _session_launch_progress_cache[sid].since == failed_at
        assert _session_launch_progress_cache[sid].step is None

        _publish_sandbox_status_impl(sid, "ready")
        assert sid not in _session_launch_progress_cache
    finally:
        _session_sandbox_status_cache.pop(sid, None)
        _session_launch_progress_cache.pop(sid, None)


def test_a_step_without_a_launch_is_ignored() -> None:
    _publish_launch_step("conv_no_launch", "node", 1)
    assert "conv_no_launch" not in _session_launch_progress_cache


async def test_stopping_a_sandbox_keeps_its_host_row(db_uri: str) -> None:
    host_store = HostStore(db_uri)
    fake = _SuspendingFake(on_host_start=_registering(host_store))
    config = _config(fake)
    launched = await launch_managed_host(config=config, owner=_OWNER, host_store=host_store)
    host = host_store.get_host(launched.host_id)
    assert host is not None and host_store.is_online(host.host_id)

    assert await stop_managed_sandbox(host, host_store, config) is True

    assert fake.suspended == [host.sandbox_id]
    assert fake.terminated == []
    after = host_store.get_host(host.host_id)
    assert after is not None and after.sandbox_id == host.sandbox_id
    assert not host_store.is_online(host.host_id)


@pytest.mark.parametrize("why", ["cannot", "fails"])
async def test_a_sandbox_that_cannot_stop_is_left_running(db_uri: str, why: str) -> None:
    host_store = HostStore(db_uri)
    fake: FakeSandboxLauncher = FakeSandboxLauncher(on_host_start=_registering(host_store))
    if why == "fails":

        class _Broken(_SuspendingFake):
            def suspend(self, sandbox_id: str) -> None:
                raise RuntimeError("apiserver down")

        fake = _Broken(on_host_start=_registering(host_store))
    config = _config(fake)
    launched = await launch_managed_host(config=config, owner=_OWNER, host_store=host_store)
    host = host_store.get_host(launched.host_id)
    assert host is not None

    assert await stop_managed_sandbox(host, host_store, config) is False
    assert host_store.is_online(host.host_id)
