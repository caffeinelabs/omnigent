"""Tests for per-user GitHub credential injection into sandboxes.

Covers the pure command/env builders
(:func:`github_sandbox_setup_commands`, :func:`github_sandbox_env`) and
the exec-model :meth:`SandboxLauncher.start_host` wiring that runs them —
so a connected user's ``gh`` / git auth and public SSH keys land in the
sandbox. See ``designs/GITHUB_APP_SANDBOX_AUTH.md``.
"""

from __future__ import annotations

from typing import ClassVar

from omnigent.onboarding.sandboxes.base import (
    RemoteCommandResult,
    SandboxLauncher,
    github_sandbox_env,
    github_sandbox_setup_commands,
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


def test_setup_commands_empty_without_identity() -> None:
    assert (
        github_sandbox_setup_commands(
            "/root", github_token=None, github_login=None, ssh_authorized_keys=None
        )
        == []
    )


def test_setup_commands_write_gh_hosts_yml_and_git_credentials() -> None:
    cmds = github_sandbox_setup_commands(
        "/root", github_token="ghu_tok", github_login="octocat", ssh_authorized_keys=None
    )
    joined = "\n".join(cmds)
    assert "/root/.config/gh/hosts.yml" in joined
    assert "base64 -d" in joined  # content is written base64-decoded
    assert "chmod 600" in joined
    # git authenticates as the user via an on-disk credential.
    assert "/root/.git-credentials" in joined
    assert "git config --global credential.helper store" in joined
    # An AGENTS.md is dropped in the workspace so the agent knows gh/git are
    # already authenticated and can clone/PR directly.
    assert "/root/workspace/AGENTS.md" in joined


def test_setup_commands_no_agents_md_without_identity() -> None:
    """No AGENTS.md (nor gh config) is written when there's no GitHub identity."""
    cmds = github_sandbox_setup_commands(
        "/root", github_token=None, github_login=None, ssh_authorized_keys=("ssh-ed25519 K a@b",)
    )
    joined = "\n".join(cmds)
    assert "AGENTS.md" not in joined


def test_setup_commands_append_ssh_keys_deduped() -> None:
    keys = ("ssh-ed25519 AAAAKEY1 a@b", "ssh-rsa AAAAKEY2 c@d")
    cmds = github_sandbox_setup_commands(
        "/root", github_token=None, github_login=None, ssh_authorized_keys=keys
    )
    joined = "\n".join(cmds)
    assert "/root/.ssh" in joined
    assert "chmod 700" in joined
    assert "authorized_keys" in joined
    # Each key is guarded so a resume (same volume) doesn't duplicate it.
    assert joined.count("grep -qxF") == 2
    assert "AAAAKEY1" in joined
    assert "AAAAKEY2" in joined


# ── start_host wiring ────────────────────────────────────────────


def test_start_host_injects_gh_and_ssh_and_env() -> None:
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
    # gh config + git credential + authorized_keys were written via run().
    assert ".config/gh/hosts.yml" in all_run
    assert ".git-credentials" in all_run
    assert "authorized_keys" in all_run
    # The host launch carries the per-user credential env.
    [raw] = launcher.backgrounded
    assert "GIT_TOKEN=ghu_tok" in raw
    assert "GH_TOKEN=ghu_tok" in raw


def test_start_host_clone_authenticates_via_on_disk_credential() -> None:
    launcher = _RecordingLauncher()
    launcher.start_host(
        "sb-1",
        token="tok",
        host_id="host_abc",
        host_name="managed-abc",
        server_url="https://srv",
        repo_url="https://github.com/acme/repo",
        repo_name="repo",
        github_token="ghu_tok",
        github_login="octocat",
    )
    # The on-disk credential is written BEFORE the clone runs, so the clone
    # authenticates as the user without an inline token prefix.
    cred_idx = next(i for i, c in enumerate(launcher.commands) if ".git-credentials" in c)
    clone_idx = next(i for i, c in enumerate(launcher.commands) if "git clone" in c)
    assert cred_idx < clone_idx


def test_start_host_no_identity_is_unchanged() -> None:
    """Without a token, no gh/ssh commands and no credential env leak in."""
    launcher = _RecordingLauncher()
    launcher.start_host(
        "sb-1",
        token="tok",
        host_id="host_abc",
        host_name="managed-abc",
        server_url="https://srv",
    )
    all_run = "\n".join(launcher.commands)
    assert "gh/hosts.yml" not in all_run
    assert "authorized_keys" not in all_run
    [raw] = launcher.backgrounded
    assert "GIT_TOKEN" not in raw
    assert "GH_TOKEN" not in raw
