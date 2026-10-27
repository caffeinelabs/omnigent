"""``omnigent server`` with an ``s3://`` artifact location (moto S3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import boto3
import pytest
from click.testing import CliRunner
from moto import mock_aws

from omnigent.cli import cli
from omnigent.stores.artifact_store.s3 import S3ArtifactStore


def test_config_s3_location_opens_s3_and_keeps_local_state_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An ``s3://`` location from ``-c`` is not joined to the config dir, opens
    the S3 store, and the agent cache stays in the local data dir."""
    import uvicorn.server

    from omnigent.server import app as app_module

    config = tmp_path / "conf" / "config.yaml"
    config.parent.mkdir()
    config.write_text("artifact_location: s3://omnigent-artifacts/server\n")
    monkeypatch.setenv("OMNIGENT_AUTH_ENABLED", "0")
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    captured: dict[str, Any] = {}

    def _spy_create_app(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return original_create_app(**kwargs)

    original_create_app = app_module.create_app
    monkeypatch.setattr(app_module, "create_app", _spy_create_app)
    monkeypatch.setattr(uvicorn.server.Server, "run", lambda self: None)
    monkeypatch.setattr("omnigent.cli._assert_server_port_bindable", Mock())

    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="omnigent-artifacts")
        result = CliRunner().invoke(
            cli,
            [
                "server",
                "-c",
                str(config),
                "--database-uri",
                f"sqlite:///{tmp_path / 'omnigent.db'}",
                "--host",
                "127.0.0.1",
                "--port",
                "44772",
                "--no-open",
            ],
        )

    assert result.exit_code == 0, result.output
    store = captured["artifact_store"]
    assert isinstance(store, S3ArtifactStore)
    assert store.storage_location == "s3://omnigent-artifacts/server"
    assert captured["agent_cache"]._cache_dir == tmp_path / "data" / "artifacts" / ".cache"
    assert "artifacts: s3://omnigent-artifacts/server" in result.output
