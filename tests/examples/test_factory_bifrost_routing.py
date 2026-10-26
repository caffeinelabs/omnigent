"""Factory Claude Code agents route through keyless Bifrost, not Databricks or a Claude login.

The sandbox host config (``~/.omnigent/config.yaml`` in every runner pod)
declares one ``bifrost`` gateway provider. Its ``openai`` family is the default
for codex/pi. Its ``anthropic`` family is deliberately NOT the Claude default,
so ordinary Claude sessions keep the per-user Databricks connect-broker path.
The factory's claude-native specs opt in with
``executor.auth: {type: provider, name: bifrost}`` and pin a model family alias
(``opus`` / ``sonnet``) that the provider maps to its Bifrost ids, so they keep
working when the owner's Databricks token is dead. These tests run the real
resolvers against a copy of that host config, with a connect-broker sidecar
present, to prove which path each factory agent takes.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from omnigent.harnesses.claude_native import main as claude_native
from omnigent.harnesses.codex_native.app_server import resolve_native_codex_launch
from omnigent.onboarding.provider_config import default_provider_for_harness, load_config
from omnigent.runner.native.orchestration import _claude_native_model_from_spec
from omnigent.runtime.workflow import _resolve_provider_for_build
from omnigent.spec import load
from omnigent.spec.types import AgentSpec

_FACTORY_DIR = Path(__file__).resolve().parents[2] / "examples" / "factory"
_BIFROST_ANTHROPIC = "http://bifrost.bifrost.svc.cluster.local:4000/anthropic"
_BIFROST_OPENAI = "http://bifrost.bifrost.svc.cluster.local:4000/v1"
_TOKEN = "dummy-bifrost-token"
_BROKER_URL = "https://ws.example/ai-gateway/anthropic"

# The `providers:` block of the caffeine-sandbox host_config (infra repo,
# clusters/caffeine-sandbox/omnigent/sandbox-config.yaml), trimmed to the keys
# the resolvers read.
_SANDBOX_PROVIDERS = {
    "bifrost": {
        "kind": "gateway",
        "default": ["openai", "pi"],
        "openai": {
            "base_url": _BIFROST_OPENAI,
            "api_key_ref": "env:BIFROST_GATEWAY_TOKEN",
            "wire_api": "chat",
            "models": {
                "default": "GLM-5.3-Flash",
                "opus": "GLM-5.3",
                "fable": "claude-fable-5",
                "kimi": "kimi-k3",
                "haiku": "claude-haiku-4-5",
            },
        },
        "anthropic": {
            "base_url": _BIFROST_ANTHROPIC,
            "api_key_ref": "env:BIFROST_GATEWAY_TOKEN",
            "models": {
                "default": "claude-sonnet-5-5",
                "opus": "claude-opus-5-5",
                "sonnet": "claude-sonnet-5-5",
                "haiku": "claude-haiku-4-5",
                "fable": "claude-fable-5",
                "opus-4-8": "claude-opus-4-8",
                "sonnet-4-6": "claude-sonnet-4-6",
            },
        },
    }
}


@pytest.fixture()
def sandbox_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A runner pod: the sandbox host config plus a Databricks connect-broker sidecar."""
    from omnigent.host import databricks_credential as dc

    monkeypatch.setenv("OMNIGENT_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("OMNIGENT_DISABLE_KEYRING", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BIFROST_GATEWAY_TOKEN", _TOKEN)
    for var in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OMNIGENT_ANTHROPIC_API_KEY",
        "OMNIGENT_OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "DATABRICKS_CONFIG_PROFILE",
        "OMNIGENT_DATABRICKS_GATEWAY_MODEL",
        "CLAUDE_CODE_USE_GATEWAY",
    ):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"providers": _SANDBOX_PROVIDERS}))
    # The owner connected Databricks: this is what an unrouted Claude session uses.
    databrickscfg = tmp_path / ".databrickscfg"
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(databrickscfg))
    dc._write_profile(databrickscfg, "https://ws.example")
    dc._write_sidecar(databrickscfg, "https://srv", "hid", "launch-tok", "https://ws.example")
    return tmp_path


def _factory_spec(name: str) -> AgentSpec:
    if name in ("factory-lead", "factory-pl"):
        return load(_FACTORY_DIR / name, expand_env=False)
    pl = load(_FACTORY_DIR / "factory-pl", expand_env=False)
    return next(s for s in pl.sub_agents if s.name == name)


@pytest.mark.parametrize(
    ("agent", "alias", "alias_env", "model"),
    [
        ("factory-lead", "opus", "ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-5-5"),
        ("factory-pl", "opus", "ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-5-5"),
        ("env_keeper", "sonnet", "ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5-5"),
        ("ic", "sonnet", "ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5-5"),
        ("demo", "sonnet", "ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5-5"),
    ],
)
@pytest.mark.usefixtures("sandbox_host")
def test_claude_factory_agents_launch_against_bifrost(
    agent: str, alias: str, alias_env: str, model: str
) -> None:
    spec = _factory_spec(agent)

    entry = _resolve_provider_for_build(spec, harness_type="claude-sdk")
    assert entry is not None and entry.name == "bifrost"

    config = claude_native.resolve_native_claude_config(spec=spec, refresh_models=False)
    assert config is not None, "fell back to the Claude CLI login"
    assert config.env["ANTHROPIC_BASE_URL"] == _BIFROST_ANTHROPIC
    assert config.api_key_helper == f"printf %s {_TOKEN}"
    # Not the Databricks connect-broker path.
    assert "CLAUDE_CODE_USE_GATEWAY" not in config.env
    assert "databricks" not in config.api_key_helper

    # The terminal launches with `--model <alias>`, and the provider pins that
    # alias to the Bifrost model id, which the gateway routes.
    assert _claude_native_model_from_spec(spec) == alias
    assert claude_native.resolve_claude_native_model_selection(alias, config) == alias
    assert config.env[alias_env] == model
    assert model in config.routable_models


@pytest.mark.usefixtures("sandbox_host")
def test_claude_spec_without_bifrost_auth_takes_the_databricks_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contrast: the same host routes an unauthed Claude spec to the owner's Databricks."""
    # Ambient detection could otherwise find a Claude login on the test machine.
    monkeypatch.setattr(
        "omnigent.onboarding.detected.effective_config_with_detected", lambda cfg: cfg
    )
    lead = _factory_spec("factory-lead")
    unrouted = dataclasses.replace(lead, executor=dataclasses.replace(lead.executor, auth=None))

    assert default_provider_for_harness(load_config(), "claude-sdk") is None
    config = claude_native.resolve_native_claude_config(spec=unrouted, refresh_models=False)
    assert config is not None
    assert config.env["ANTHROPIC_BASE_URL"] == _BROKER_URL


@pytest.mark.parametrize("agent", ["ic_codex", "reviewer"])
@pytest.mark.usefixtures("sandbox_host")
def test_codex_factory_agents_use_the_default_bifrost_openai_family(agent: str) -> None:
    spec = _factory_spec(agent)

    entry = _resolve_provider_for_build(spec, harness_type="codex", for_launch=True)
    assert entry is not None and entry.name == "bifrost"

    launch = resolve_native_codex_launch(model=None, spec=spec)
    assert any(f'base_url="{_BIFROST_OPENAI}"' in o for o in launch.config_overrides)
    assert launch.model == "GLM-5.3-Flash"
    assert not launch.login_required
