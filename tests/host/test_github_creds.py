"""Tests for the in-sandbox GitHub credential rewriter."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from omnigent.host.github_creds import write_github_credentials


def test_writes_both_files_with_expected_content(tmp_path: Path) -> None:
    write_github_credentials(tmp_path, token="ghu_abc123", login="octocat")

    creds = tmp_path / ".git-credentials"
    hosts = tmp_path / ".config" / "gh" / "hosts.yml"

    # git HTTPS credential: x-access-token username, token as password.
    assert creds.read_text() == "https://x-access-token:ghu_abc123@github.com\n"
    # gh hosts.yml: exact four-line shape the launcher writes.
    assert hosts.read_text() == (
        "github.com:\n    user: octocat\n    oauth_token: ghu_abc123\n    git_protocol: https\n"
    )


def test_files_are_mode_600(tmp_path: Path) -> None:
    write_github_credentials(tmp_path, token="ghu_abc123", login="octocat")
    for rel in (".git-credentials", ".config/gh/hosts.yml"):
        mode = stat.S_IMODE((tmp_path / rel).stat().st_mode)
        assert mode == 0o600, f"{rel} is {oct(mode)}, expected 0o600 (holds a bearer token)"


def test_overwrites_an_existing_credential(tmp_path: Path) -> None:
    write_github_credentials(tmp_path, token="ghu_old", login="octocat")
    write_github_credentials(tmp_path, token="ghu_new", login="octocat")
    assert (tmp_path / ".git-credentials").read_text() == (
        "https://x-access-token:ghu_new@github.com\n"
    )
    # No leftover temp file from the atomic replace.
    assert not (tmp_path / ".git-credentials.tmp").exists()


def test_creates_parent_dirs(tmp_path: Path) -> None:
    # ~/.config/gh doesn't exist yet — the writer must create it.
    write_github_credentials(tmp_path, token="ghu_abc123", login="octocat")
    assert (tmp_path / ".config" / "gh").is_dir()


@pytest.mark.parametrize(
    ("token", "login"),
    [("", "octocat"), ("ghu_abc", "")],
)
def test_refuses_empty_token_or_login(tmp_path: Path, token: str, login: str) -> None:
    with pytest.raises(ValueError):
        write_github_credentials(tmp_path, token=token, login=login)
    # Nothing written on refusal.
    assert not (tmp_path / ".git-credentials").exists()
