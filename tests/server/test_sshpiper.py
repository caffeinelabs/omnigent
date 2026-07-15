"""Tests for SSHPiper config + target rendering."""

from __future__ import annotations

import pytest

from omnigent.server.sshpiper import SshPiperConfig


def test_from_env_disabled_when_host_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIGENT_SSHPIPER_HOST", raising=False)
    assert SshPiperConfig.from_env() is None


def test_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIGENT_SSHPIPER_HOST", "sshpiper.example.com")
    monkeypatch.delenv("OMNIGENT_SSHPIPER_PORT", raising=False)
    monkeypatch.delenv("OMNIGENT_SSHPIPER_USER", raising=False)
    monkeypatch.delenv("OMNIGENT_SSHPIPER_NAMESPACE", raising=False)
    monkeypatch.delenv("OMNIGENT_SSHPIPER_TARGET_TEMPLATE", raising=False)
    cfg = SshPiperConfig.from_env()
    assert cfg is not None
    assert cfg.host == "sshpiper.example.com"
    assert cfg.port == 22
    assert cfg.user == "sandbox"
    assert cfg.namespace == "omnigent-sandboxes"


def test_from_env_rejects_user_with_splitter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIGENT_SSHPIPER_HOST", "sshpiper.example.com")
    monkeypatch.setenv("OMNIGENT_SSHPIPER_USER", "bad--user")
    assert SshPiperConfig.from_env() is None


def test_ssh_target_and_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIGENT_SSHPIPER_HOST", "sshpiper.example.com")
    monkeypatch.setenv("OMNIGENT_SSHPIPER_USER", "dev")
    monkeypatch.setenv("OMNIGENT_SSHPIPER_NAMESPACE", "sshpiper-demo")
    monkeypatch.setenv(
        "OMNIGENT_SSHPIPER_TARGET_TEMPLATE",
        "{sandbox_id}.{namespace}.svc.cluster.local",
    )
    cfg = SshPiperConfig.from_env()
    assert cfg is not None
    target = cfg.ssh_target(sandbox_id="demo-workspace")
    assert target == "demo-workspace.sshpiper-demo.svc.cluster.local"
    assert cfg.sshpiper_username(target) == ("demo-workspace.sshpiper-demo.svc.cluster.local--dev")


def test_ssh_target_rejects_double_dash_in_result() -> None:
    cfg = SshPiperConfig(
        host="gw",
        port=22,
        user="sandbox",
        target_template="{sandbox_id}",
        namespace="ns",
    )
    with pytest.raises(ValueError, match="--"):
        cfg.ssh_target(sandbox_id="a--b")
