"""Tests for ``linux_userns_supported`` (the hardened-container probe).

Codex's own command sandbox needs unprivileged user namespaces; on hosts
that deny them every model-issued shell command hard-fails with bwrap's
"No permissions to create new namespace" (omnigent-ai/omnigent#657). The
probe in :mod:`omnigent.inner.sandbox` is what the runner consults to
decide the codex-native launch stance.
"""

from __future__ import annotations

import sys

import pytest

from omnigent.inner import sandbox as sandbox_module
from omnigent.inner.sandbox import linux_userns_supported


@pytest.fixture(autouse=True)
def _reset_userns_probe_cache(monkeypatch: pytest.MonkeyPatch):
    """Each test starts with a cold probe cache and restores it after."""
    monkeypatch.setattr(sandbox_module, "_linux_userns_supported_cache", None)
    yield
    monkeypatch.setattr(sandbox_module, "_linux_userns_supported_cache", None)


class _ForkProbe:
    """Stub :func:`os.fork` + :func:`os.waitpid` pair for one probe.

    ``child_exit`` is the exit status the fake child reports (``0`` =
    unshare succeeded). ``fork_error`` makes the fork itself raise.
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        child_exit: int = 0,
        fork_error: OSError | None = None,
        wait_error: OSError | None = None,
    ) -> None:
        self.fork_calls = 0
        self._child_exit = child_exit
        self._fork_error = fork_error
        self._wait_error = wait_error
        monkeypatch.setattr(sandbox_module.os, "fork", self._fork)
        monkeypatch.setattr(sandbox_module.os, "waitpid", self._waitpid)

    def _fork(self) -> int:
        self.fork_calls += 1
        if self._fork_error is not None:
            raise self._fork_error
        # A fake CHILD pid (never 0 — 0 would make the caller take the real
        # child branch and os._exit the pytest process).
        return 999999

    def _waitpid(self, pid: int, options: int) -> tuple[int, int]:
        if self._wait_error is not None:
            raise self._wait_error
        assert pid == 999999
        # A zero raw status == exit code 0 (unshare succeeded).
        return pid, self._child_exit


def test_non_linux_platforms_report_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe asks "can this Linux host run user-namespace sandboxes?"

    Off Linux that question is meaningless, and the only safe answer is
    ``False`` — no caller should take a Linux-namespace-dependent stance
    based on a non-Linux host.
    """
    monkeypatch.setattr(sandbox_module.sys, "platform", "darwin")
    assert linux_userns_supported() is False


def test_supported_host_caches_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful child ``unshare(CLONE_NEWUSER)`` reports ``True``, once.

    The fork probe must run exactly once per process — the kernel setting
    cannot change under a running process, and each probe forks. The
    cache is what makes the runner's per-session consultation cheap.
    """
    probe = _ForkProbe(monkeypatch, child_exit=0)
    assert linux_userns_supported() is True
    assert linux_userns_supported() is True
    assert probe.fork_calls == 1
    assert sandbox_module._linux_userns_supported_cache is True


def test_denied_userns_caches_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child that fails to unshare (EPERM) reports ``False``, once.

    Hardened containers deny ``CLONE_NEWUSER`` via seccomp — the exact
    #657 environment. The verdict must stick: a flapping answer would
    flip the runner's launch stance between sessions on the same host.
    """
    probe = _ForkProbe(monkeypatch, child_exit=1)
    assert linux_userns_supported() is False
    assert linux_userns_supported() is False
    assert probe.fork_calls == 1
    assert sandbox_module._linux_userns_supported_cache is False


def test_denied_fork_reports_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fork that cannot even happen counts as "unsupported", not a crash.

    A host restrictive enough to deny ``fork()`` for the probe cannot run
    user-namespace sandboxes either, and the runner must get a usable
    answer rather than an exception from a launch-path read.
    """
    probe = _ForkProbe(monkeypatch, fork_error=OSError("seccomp denies fork"))
    assert linux_userns_supported() is False
    assert probe.fork_calls == 1


def test_unwaitable_child_reports_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A waitpid failure degrades to ``False`` instead of raising."""
    _ForkProbe(monkeypatch, child_exit=0, wait_error=ChildProcessError("gone"))
    assert linux_userns_supported() is False


@pytest.mark.skipif(sys.platform != "linux", reason="real probe is Linux-only")
def test_real_probe_returns_a_bool() -> None:
    """The real (unstubbed) probe answers with a plain bool.

    Value-agnostic by design: CI hosts legitimately differ in whether
    unprivileged user namespaces are allowed (this repo's own hardening
    denies them). The contract under test is "never raises, answers
    once, and caches" — the stance decision is the runner's.
    """
    first = linux_userns_supported()
    assert first is True or first is False
    assert linux_userns_supported() is first
