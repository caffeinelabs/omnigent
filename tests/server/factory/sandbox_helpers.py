"""A managed-sandbox stand-in for the factory's relaunch and release tests.

Wires a real host store, a launch tracker and a sandbox config onto an
``app.state`` and records, instead of running, the three provider-facing
steps: provisioning a fresh sandbox for a session, terminating one, and
stopping one.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.db.utils import now_epoch
from omnigent.server import managed_hosts
from omnigent.server.managed_host_isolation import MANAGED_HOST_CLAIM_LABEL_KEY
from omnigent.server.managed_hosts import ManagedLaunchTracker
from omnigent.server.routes._sessions import orchestration
from omnigent.server.routes._sessions.common import (
    _session_launch_progress_cache,
    _session_sandbox_status_cache,
)
from omnigent.server.routes._sessions.helpers import _publish_sandbox_status_impl
from omnigent.stores.host_store import HostStore

OWNER = "alice"


@dataclass
class SandboxRig:
    """The managed-sandbox wiring of one test and what it was asked to do."""

    host_store: HostStore
    tracker: ManagedLaunchTracker
    kicks: list[str] = field(default_factory=list)
    terminated: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)

    def bind_host(
        self, store: Any, session_id: str, *, online: bool = True, claim: bool = True
    ) -> str:
        """Bind *session_id* to a fresh managed host (online by default); :returns: its id."""
        host_id = uuid.uuid4().hex
        name = f"managed-{host_id[:8]}"
        self.host_store.register_managed_host(
            host_id=host_id,
            name=name,
            user_id=OWNER,
            token="launch-token",
            provider="kubernetes",
            sandbox_id=f"omnigent-managed-{host_id[:8]}",
            token_expires_at=now_epoch() + 3600,
        )
        if online:
            self.host_store.upsert_on_connect(host_id, name, OWNER)
        store.set_host_id(session_id, host_id, "/home/omnigent/workspace")
        if claim:
            store.set_labels(session_id, {MANAGED_HOST_CLAIM_LABEL_KEY: host_id})
        return host_id

    def fail_launch(self, session_id: str, error: str = "pod did not start") -> None:
        """Record a failed managed launch of *session_id*, as the launch task does."""
        self.tracker.begin(session_id)
        self.tracker.fail(session_id, error)
        _publish_sandbox_status_impl(session_id, "failed", error)


def install_sandbox_rig(
    app_state: Any, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> SandboxRig:
    """Wire a :class:`SandboxRig` onto *app_state*; provider calls are recorded."""
    rig = SandboxRig(host_store=HostStore(db_uri), tracker=ManagedLaunchTracker())
    app_state.host_store = rig.host_store
    app_state.sandbox_config = SimpleNamespace(managed_launch_supported=True)
    app_state.managed_launches = rig.tracker

    def _kick(*, session_id: str, tracker: ManagedLaunchTracker, **_: Any) -> None:
        rig.kicks.append(session_id)
        tracker.begin(session_id)
        _publish_sandbox_status_impl(session_id, "provisioning")

    async def _terminate(host: Any, host_store: HostStore, _config: Any) -> None:
        rig.terminated.append(host.host_id)
        host_store.delete_host(host.host_id)

    async def _stop(host: Any, host_store: HostStore, _config: Any) -> bool:
        rig.stopped.append(host.host_id)
        host_store.set_offline(host.host_id)
        return True

    monkeypatch.setattr(orchestration, "_kick_managed_reprovision", _kick)
    monkeypatch.setattr(managed_hosts, "terminate_managed_host", _terminate)
    monkeypatch.setattr(managed_hosts, "stop_managed_sandbox", _stop)
    return rig


def clean_launch_caches() -> Iterator[None]:
    """Drop launch state the test left in the process-wide caches."""
    before = set(_session_sandbox_status_cache) | set(_session_launch_progress_cache)
    yield
    for cache in (_session_sandbox_status_cache, _session_launch_progress_cache):
        for sid in [s for s in cache if s not in before]:
            del cache[sid]
