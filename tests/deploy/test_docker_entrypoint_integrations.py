"""Regression: Docker entrypoint must wire GitHub App + SSHPiper.

Production deploys boot via ``deploy/docker/entrypoint.py``, not
``omnigent server``. If ``create_app`` is called without
``github_config`` / ``github_store`` / ``sshpiper_config``, ``/v1/info``
keeps ``github_app_enabled=false`` and ``sshpiper_host=null`` even when
the env vars are set — and the SPA hides Connect GitHub / Open in VS Code.
"""

from __future__ import annotations

from pathlib import Path

_ENTRYPOINT = Path(__file__).resolve().parents[2] / "deploy" / "docker" / "entrypoint.py"


def test_entrypoint_wires_github_app_and_sshpiper_from_env() -> None:
    text = _ENTRYPOINT.read_text()
    assert "GitHubAppConfig.from_env()" in text
    assert "SshPiperConfig.from_env()" in text
    assert "GithubConnectionStore(" in text
    assert "github_config=github_config" in text
    assert "github_store=github_store" in text
    assert "sshpiper_config=sshpiper_config" in text
