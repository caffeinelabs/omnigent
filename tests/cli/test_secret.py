"""Tests for the ``omnigent secret`` command group."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from omnigent.cli import cli


@pytest.fixture()
def _isolated_store(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the secret store at a throwaway file backend."""
    monkeypatch.setenv("OMNIGENT_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("OMNIGENT_DISABLE_KEYRING", "1")


def test_secret_set_list_rm_roundtrip(_isolated_store: None) -> None:
    """set → list → rm → list round-trips through the file backend."""
    runner = CliRunner()

    r = runner.invoke(cli, ["secret", "set", "datadog-api", "--value", "sk-test-123"])
    assert r.exit_code == 0, r.output
    assert "Stored secret 'datadog-api'" in r.output

    r = runner.invoke(cli, ["secret", "list"])
    assert r.exit_code == 0, r.output
    assert "datadog-api" in r.output

    r = runner.invoke(cli, ["secret", "rm", "datadog-api"])
    assert r.exit_code == 0, r.output

    r = runner.invoke(cli, ["secret", "list"])
    assert r.exit_code == 0, r.output
    assert "datadog-api" not in r.output


def test_secret_list_never_prints_values(_isolated_store: None) -> None:
    """``list`` shows names only — never the stored value."""
    runner = CliRunner()
    runner.invoke(cli, ["secret", "set", "datadog-app", "--value", "super-secret-value"])

    r = runner.invoke(cli, ["secret", "list"])
    assert r.exit_code == 0, r.output
    assert "datadog-app" in r.output
    assert "super-secret-value" not in r.output


def test_secret_set_rejects_empty_value(_isolated_store: None) -> None:
    """An empty value is refused rather than stored."""
    runner = CliRunner()
    r = runner.invoke(cli, ["secret", "set", "empty", "--value", ""])
    assert r.exit_code != 0
    assert "empty secret" in r.output


def test_secret_set_prompts_when_value_omitted(_isolated_store: None) -> None:
    """With no --value, the command prompts (hidden) and stores the input."""
    runner = CliRunner()
    r = runner.invoke(cli, ["secret", "set", "datadog-api"], input="prompted-token\n")
    assert r.exit_code == 0, r.output

    r = runner.invoke(cli, ["secret", "list"])
    assert "datadog-api" in r.output
