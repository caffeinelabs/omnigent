"""Tests for per-user GitHub credentials in sandboxes.

Covers the pure builders (:func:`github_sandbox_env`,
:func:`ssh_authorized_keys_setup_commands`) and the exec-model
:meth:`SandboxLauncher.start_host` launch, which carries no GitHub
credentials: the host points git and ``gh`` at the server's credential
broker when it starts (``omnigent.host.connect`` →
``omnigent.git_credential_github``), so no launcher injects anything
GitHub-specific. See ``docs/GITHUB_APP_SETUP.md``.
"""

from __future__ import annotations

from typing import ClassVar

from omnigent.onboarding.sandboxes.base import (
    RemoteCommandResult,
    SandboxLauncher,
    github_sandbox_env,
    ssh_authorized_keys_setup_commands,
)


class _RecordingLauncher(SandboxLauncher):
    """Minimal exec-model launcher recording every ``run`` command."""

    provider: ClassVar[str] = "recording"

    def __init__(self, home: str = "/root") -> None:
        self.commands: list[str] = []
        self.backgrounded: list[str] = []
        self._home = home

    def prepare(self) -> None:  # pragma: no cover - unused stub
        pass

    def provision(self, name: str) -> str:  # pragma: no cover - unused stub
        return "sb-1"

    def run(self, sandbox_id: str, command: str, *, check: bool = True) -> RemoteCommandResult:
        self.commands.append(command)
        stdout = self._home if command == 'printf %s "$HOME"' else ""
        return RemoteCommandResult(returncode=0, stdout=stdout, stderr="")

    def run_background(
        self, sandbox_id: str, command: str, *, log_path: str = "/tmp/omnigent-host.log"
    ) -> RemoteCommandResult:
        self.backgrounded.append(command)
        return super().run_background(sandbox_id, command, log_path=log_path)


# ── Pure builders ────────────────────────────────────────────────


def test_env_empty_without_token() -> None:
    assert github_sandbox_env(None) == {}
    assert github_sandbox_env("") == {}


def test_env_sets_git_and_gh_vars() -> None:
    env = github_sandbox_env("ghu_tok")
    assert env["GIT_TOKEN"] == "ghu_tok"
    assert env["GIT_USERNAME"] == "x-access-token"
    # gh / conventional tooling read these.
    assert env["GH_TOKEN"] == "ghu_tok"
    assert env["GITHUB_TOKEN"] == "ghu_tok"


def test_ssh_setup_empty_without_keys() -> None:
    assert ssh_authorized_keys_setup_commands("/root", None) == []
    assert ssh_authorized_keys_setup_commands("/root", ()) == []


def test_ssh_setup_appends_keys_deduped() -> None:
    keys = ("ssh-ed25519 AAAAKEY1 a@b", "ssh-rsa AAAAKEY2 c@d")
    cmds = ssh_authorized_keys_setup_commands("/root", keys)
    joined = "\n".join(cmds)
    assert "/root/.ssh" in joined
    assert "chmod 700" in joined
    assert "authorized_keys" in joined
    # Each key is guarded so a resume (same volume) doesn't duplicate it.
    assert joined.count("grep -qxF") == 2
    assert "AAAAKEY1" in joined
    assert "AAAAKEY2" in joined


# ── start_host wiring ────────────────────────────────────────────


def test_start_host_leaves_github_credentials_to_the_broker() -> None:
    launcher = _RecordingLauncher()
    launcher.start_host(
        "sb-1",
        token="tok",
        host_id="host_abc",
        host_name="managed-abc",
        server_url="https://srv",
    )
    all_run = "\n".join(launcher.commands)
    assert "authorized_keys" not in all_run
    assert "hosts.yml" not in all_run
    assert ".git-credentials" not in all_run
    [raw] = launcher.backgrounded
    assert "GIT_TOKEN" not in raw
    assert "GH_TOKEN" not in raw
    assert "GITHUB_TOKEN" not in raw
