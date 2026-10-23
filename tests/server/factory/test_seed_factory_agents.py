"""Factory built-ins are seeded from ``examples/factory/*`` only when enabled."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from omnigent.runtime.agent_cache import AgentCache
from omnigent.server import app as server_app
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore


def _bundle(root: Path, name: str, role: str) -> None:
    bundle = root / name
    (bundle / "skills" / "factory-protocol").mkdir(parents=True)
    (bundle / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "name": name,
                "description": f"{name} for tests",
                "executor": {
                    "type": "omnigent",
                    "config": {"harness": "claude-native", "factory_role": role},
                },
            }
        )
    )
    (bundle / "skills" / "factory-protocol" / "SKILL.md").write_text(
        "---\nname: factory-protocol\ndescription: How factory members talk.\n---\nBe brief.\n"
    )


@pytest.fixture()
def bundles(tmp_path: Path) -> Path:
    root = tmp_path / "factory"
    _bundle(root, "factory-lead", "lead")
    _bundle(root, "factory-pl", "project_lead")
    (root / "README.md").write_text("not a bundle")
    (root / "notes").mkdir()  # a directory without config.yaml is ignored
    return root


@pytest.fixture()
def stores(
    db_uri: str, tmp_path: Path
) -> tuple[SqlAlchemyAgentStore, LocalArtifactStore, AgentCache]:
    artifacts = LocalArtifactStore(str(tmp_path / "artifacts"))
    return (
        SqlAlchemyAgentStore(db_uri),
        artifacts,
        AgentCache(artifacts, tmp_path / "cache"),
    )


def test_not_seeded_without_the_flag(
    monkeypatch: pytest.MonkeyPatch,
    bundles: Path,
    stores: tuple[SqlAlchemyAgentStore, LocalArtifactStore, AgentCache],
) -> None:
    monkeypatch.delenv("OMNIGENT_FACTORY_ENABLED", raising=False)
    agent_store, artifacts, cache = stores

    server_app._ensure_factory_agents(agent_store, artifacts, cache, bundles_root=bundles)

    assert agent_store.get_by_name("factory-lead") is None
    assert agent_store.get_by_name("factory-pl") is None


def test_seeded_with_the_flag_and_named_after_the_directory(
    monkeypatch: pytest.MonkeyPatch,
    bundles: Path,
    stores: tuple[SqlAlchemyAgentStore, LocalArtifactStore, AgentCache],
) -> None:
    monkeypatch.setenv("OMNIGENT_FACTORY_ENABLED", "1")
    agent_store, artifacts, cache = stores

    server_app._ensure_factory_agents(agent_store, artifacts, cache, bundles_root=bundles)

    lead = agent_store.get_by_name("factory-lead")
    pl = agent_store.get_by_name("factory-pl")
    assert lead is not None and pl is not None
    assert agent_store.get_by_name("notes") is None
    spec = cache.load(lead.id, lead.bundle_location, expand_env=True).spec
    assert spec.executor.config["factory_role"] == "lead"
    assert [s.name for s in spec.skills] == ["factory-protocol"]

    # Idempotent: a second boot keeps the same rows.
    server_app._ensure_factory_agents(agent_store, artifacts, cache, bundles_root=bundles)
    again = agent_store.get_by_name("factory-lead")
    assert again is not None and again.id == lead.id
    assert again.bundle_location == lead.bundle_location


def test_flag_values_other_than_1_do_not_seed(
    monkeypatch: pytest.MonkeyPatch,
    bundles: Path,
    stores: tuple[SqlAlchemyAgentStore, LocalArtifactStore, AgentCache],
) -> None:
    monkeypatch.setenv("OMNIGENT_FACTORY_ENABLED", "true")
    agent_store, artifacts, cache = stores

    server_app._ensure_factory_agents(agent_store, artifacts, cache, bundles_root=bundles)

    assert agent_store.get_by_name("factory-lead") is None


def test_missing_bundle_dir_and_broken_bundles_never_block_startup(
    monkeypatch: pytest.MonkeyPatch,
    bundles: Path,
    tmp_path: Path,
    stores: tuple[SqlAlchemyAgentStore, LocalArtifactStore, AgentCache],
) -> None:
    monkeypatch.setenv("OMNIGENT_FACTORY_ENABLED", "1")
    agent_store, artifacts, cache = stores
    (bundles / "broken").mkdir()
    (bundles / "broken" / "config.yaml").write_text("{{ not yaml")

    server_app._ensure_factory_agents(
        agent_store, artifacts, cache, bundles_root=tmp_path / "absent"
    )
    server_app._ensure_factory_agents(agent_store, artifacts, cache, bundles_root=bundles)

    assert agent_store.get_by_name("broken") is None
    assert agent_store.get_by_name("factory-pl") is not None


def test_default_agents_include_the_packaged_factory_dir(
    monkeypatch: pytest.MonkeyPatch,
    bundles: Path,
    stores: tuple[SqlAlchemyAgentStore, LocalArtifactStore, AgentCache],
) -> None:
    monkeypatch.setenv("OMNIGENT_FACTORY_ENABLED", "1")
    monkeypatch.setattr(server_app, "_FACTORY_BUNDLES_ROOT", bundles)
    agent_store, artifacts, cache = stores

    server_app._ensure_default_agents(agent_store, artifacts, cache)

    assert agent_store.get_by_name("factory-lead") is not None
    assert agent_store.get_by_name("polly") is not None
