"""Tests for per-user GitHub credential injection into sandboxes.

Covers the pure builders (:func:`github_sandbox_env`,
:func:`ssh_authorized_keys_setup_commands`) and the exec-model
:meth:`SandboxLauncher.start_host` wiring that runs them — so a connected
user's git/``gh`` token seed and public SSH keys land in the sandbox.

git/``gh`` credentials are seeded via env only; the native credential broker
+ ``git_credential_github`` helper keep them live per operation (no on-disk
``hosts.yml`` / ``.git-credentials`` written here). See
``docs/GITHUB_APP_SETUP.md``.
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


def test_start_host_injects_ssh_and_env() -> None:
    launcher = _RecordingLauncher()
    launcher.start_host(
        "sb-1",
        token="tok",
        host_id="host_abc",
        host_name="managed-abc",
        server_url="https://srv",
        github_token="ghu_tok",
        github_login="octocat",
        ssh_authorized_keys=("ssh-ed25519 AAAAKEY a@b",),
    )
    all_run = "\n".join(launcher.commands)
    # SSH keys were written via run(); no on-disk gh/git credential files —
    # those are vended live by the native broker + helper.
    assert "authorized_keys" in all_run
    assert "hosts.yml" not in all_run
    assert ".git-credentials" not in all_run
    # The host launch carries the per-user credential env seed.
    [raw] = launcher.backgrounded
    assert "GIT_TOKEN=ghu_tok" in raw
    assert "GH_TOKEN=ghu_tok" in raw


def test_start_host_no_identity_is_unchanged() -> None:
    """Without a token, no ssh commands and no credential env leak in."""
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
    [raw] = launcher.backgrounded
    assert "GIT_TOKEN" not in raw
    assert "GH_TOKEN" not in raw
