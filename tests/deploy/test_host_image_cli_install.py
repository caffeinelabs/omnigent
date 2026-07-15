"""Regression tests for managed host image CLI availability."""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "dockerfile",
    [
        _ROOT / "deploy/docker/Dockerfile",
        _ROOT / "deploy/docker/Dockerfile.ubi",
    ],
)
def test_host_images_install_pinned_kiro_cli(dockerfile: Path) -> None:
    """Managed host images must preinstall a *pinned* Kiro CLI binary.

    The public npm package named ``kiro-cli`` is unrelated and exposes no
    ``kiro-cli`` binary. Kiro's ``curl …/install`` script has no version flag
    (it always fetches ``latest``), so the images instead pull the immutable,
    versioned per-arch zip from the CDN, verify its sha256, and copy the binary
    onto the global PATH (see the pinning rationale in the Dockerfiles). This
    guards both that the pin stays in place and that the old unpinned installer
    never creeps back.
    """
    text = dockerfile.read_text()

    # Pinned to an explicit version, fetched from the immutable versioned CDN
    # path — not the unpinned ``cli.kiro.dev/install`` script, not ``…/latest/``.
    assert "ARG KIRO_CLI_VERSION=" in text
    assert "https://prod.download.cli.kiro.dev/stable/${KIRO_CLI_VERSION}/" in text
    assert "https://cli.kiro.dev/install" not in text
    # Integrity-checked, then copied onto the global PATH for all sandbox users.
    assert "sha256sum -c" in text
    assert "install -m 0755 /root/.local/bin/kiro-cli /usr/local/bin/kiro-cli" in text
    # kiro-cli is not an npm package, so it must not appear in the npm install list.
    assert "      kiro-cli \\" not in text


@pytest.mark.parametrize(
    "dockerfile",
    [
        _ROOT / "deploy/docker/Dockerfile",
        _ROOT / "deploy/docker/Dockerfile.ubi",
    ],
)
def test_host_images_include_kiro_installer_dependency(dockerfile: Path) -> None:
    """Kiro's installer needs ``unzip`` on Linux."""
    text = dockerfile.read_text()
    assert "unzip" in text


@pytest.mark.parametrize(
    "dockerfile",
    [
        _ROOT / "deploy/docker/Dockerfile",
        _ROOT / "deploy/docker/Dockerfile.ubi",
    ],
)
def test_host_images_install_pinned_gh_cli(dockerfile: Path) -> None:
    """Managed host images must preinstall a pinned GitHub CLI (``gh``).

    Per-user GitHub App sandbox auth writes ``~/.config/gh/hosts.yml`` and
    expects ``gh`` on PATH inside the box. Pin + sha256 keeps the image
    reconstructable (same pattern as kiro-cli / agy).
    """
    text = dockerfile.read_text()
    assert "ARG GH_VERSION=" in text
    assert "https://github.com/cli/cli/releases/download/v${GH_VERSION}/" in text
    assert 'install -m 0755 "/tmp/gh_${GH_VERSION}_linux_${arch}/bin/gh" /usr/local/bin/gh' in text
    assert "sha256sum -c" in text


@pytest.mark.parametrize(
    "dockerfile",
    [
        _ROOT / "deploy/docker/Dockerfile",
        _ROOT / "deploy/docker/Dockerfile.ubi",
    ],
)
def test_host_images_install_pinned_mise(dockerfile: Path) -> None:
    """Managed host images must preinstall a pinned ``mise`` binary."""
    text = dockerfile.read_text()
    assert "ARG MISE_VERSION=" in text
    assert "https://github.com/jdx/mise/releases/download/v${MISE_VERSION}/" in text
    assert "install -m 0755 /tmp/mise/bin/mise /usr/local/bin/mise" in text
    assert "/etc/profile.d/mise.sh" in text


@pytest.mark.parametrize(
    "dockerfile",
    [
        _ROOT / "deploy/docker/Dockerfile",
        _ROOT / "deploy/docker/Dockerfile.ubi",
    ],
)
def test_host_images_preseed_vscode_server(dockerfile: Path) -> None:
    """Managed host images must preseed VS Code Server + CLI for Remote-SSH.

    A cold ``~/.vscode-server`` download on first attach is slow and fails in
    egress-restricted sandboxes; bake a pinned stable commit instead.
    """
    text = dockerfile.read_text()
    assert "ARG VSCODE_COMMIT=" in text
    assert "update.code.visualstudio.com/commit:${VSCODE_COMMIT}/cli-linux-" in text
    assert "update.code.visualstudio.com/commit:${VSCODE_COMMIT}/server-linux-" in text
    assert "/usr/local/bin/code" in text
    assert ".vscode-server/cli/servers/Stable-${VSCODE_COMMIT}/server" in text
    assert "openssh-server" in text
