"""The software-factory agent bundles (``examples/factory``) load and are coherent.

These are the shipped ``factory-lead`` and ``factory-pl`` built-ins: the specs,
their sub-agents, their policies, their skills and the tools their prompts tell
the model to call. A typo in any of them only surfaces once a sandbox is up and
an agent stalls on a tool that does not exist, so it is pinned here.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
import tomllib

from omnigent.factory import (
    ROLE_ENV_KEEPER,
    ROLE_IC,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    ROLE_REVIEWER,
    factory_role_from_spec,
)
from omnigent.policies.registry import is_registered_handler
from omnigent.resources import examples as _examples_resources
from omnigent.spec import load, materialize_bundle
from omnigent.spec.types import AgentSpec, FunctionPolicySpec, ProviderAuth

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FACTORY_DIR = _REPO_ROOT / "examples" / "factory"
_LEAD_DIR = _FACTORY_DIR / "factory-lead"
_PL_DIR = _FACTORY_DIR / "factory-pl"
_PL_SUB_AGENTS = ("env_keeper", "ic", "ic_codex", "reviewer")

# The runner tools the factory adds next to the existing builtins.
_FACTORY_TOOLS = frozenset(
    {"sys_session_post", "sys_factory_roster", "sys_factory_set", "sys_factory_spawn"}
)
_FACTORY_POLICY_MODULE = "omnigent.policies.builtins.factory"
_FACTORY_TOOLS_MODULE = "omnigent.tools.builtins.factory"
_TOOL_TOKEN = re.compile(r"\b(sys_[a-z_]+|load_skill|read_skill_file)\b")

# Family aliases: the provider's models map turns them into concrete ids.
_CLAUDE_MODELS = {
    "factory-lead": "opus",
    "factory-pl": "opus",
    "env_keeper": "sonnet",
    "ic": "sonnet",
}
_ROLES = {
    "factory-lead": ROLE_LEAD,
    "factory-pl": ROLE_PROJECT_LEAD,
    "env_keeper": ROLE_ENV_KEEPER,
    "ic": ROLE_IC,
    "ic_codex": ROLE_IC,
    "reviewer": ROLE_REVIEWER,
}


def _module_available(dotted: str) -> bool:
    """Return whether *dotted* imports here (sibling factory modules may lag)."""
    try:
        return importlib.util.find_spec(dotted) is not None
    except ModuleNotFoundError:
        return False


@pytest.fixture(scope="module")
def lead() -> AgentSpec:
    return load(_LEAD_DIR, expand_env=False)


@pytest.fixture(scope="module")
def pl() -> AgentSpec:
    return load(_PL_DIR, expand_env=False)


@pytest.fixture()
def _terminal_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a terminal registry so the relay can register ``sys_terminal_*``."""
    from omnigent.runtime import _globals as rt_globals
    from omnigent.terminals.registry import TerminalRegistry

    monkeypatch.setattr(rt_globals, "_terminal_registry", TerminalRegistry())


def _all_specs(lead: AgentSpec, pl: AgentSpec) -> Iterator[AgentSpec]:
    yield lead
    yield pl
    yield from pl.sub_agents


def _sub_agent(pl: AgentSpec, name: str) -> AgentSpec:
    from omnigent.runner.tool_dispatch import _find_subagent_spec

    spec = _find_subagent_spec(name, pl)
    assert spec is not None, f"factory-pl sub-agent {name!r} does not resolve"
    return spec


def _skill_dir(spec: AgentSpec) -> Path:
    if spec.name == "factory-lead":
        return _LEAD_DIR / "skills"
    if spec.name == "factory-pl":
        return _PL_DIR / "skills"
    return _PL_DIR / "agents" / str(spec.name) / "skills"


# ── Parsing and structure ────────────────────────────────────────────────────


def test_both_bundles_parse_with_the_expected_shape(lead: AgentSpec, pl: AgentSpec) -> None:
    assert lead.name == "factory-lead"
    assert pl.name == "factory-pl"
    for spec in (lead, pl):
        assert spec.spawn is True
        assert spec.async_enabled is True
        assert spec.executor.reasoning_effort == "high"
        assert spec.executor.config["harness"] == "claude-native"
        assert spec.executor.config["permission_mode"] == "auto"
        assert spec.instructions
    assert lead.executor.config["factory_max_pods"] == "12"
    assert {s.name for s in lead.skills} == {
        "factory-protocol",
        "factory-lead-playbook",
        "factory-heartbeat",
        "factory-recovery",
    }
    assert {s.name for s in pl.skills} == {
        "factory-protocol",
        "factory-pl-playbook",
        "factory-heartbeat",
    }


def test_pl_sub_agents_resolve(pl: AgentSpec) -> None:
    assert list(pl.tools.agents) == list(_PL_SUB_AGENTS)
    for name in _PL_SUB_AGENTS:
        sub = _sub_agent(pl, name)
        assert sub.instructions, f"{name} has no prompt"
    assert {s.name for s in _sub_agent(pl, "env_keeper").skills} == {"factory-env"}
    assert {s.name for s in _sub_agent(pl, "ic").skills} == {"factory-ic"}
    assert "tilt" in (_sub_agent(pl, "env_keeper").terminals or {})
    for name in ("ic_codex", "reviewer"):
        sub = _sub_agent(pl, name)
        assert sub.executor.config["harness"] == "codex-native"
        assert sub.executor.config["yolo"] == "True"


def test_bundle_survives_the_server_tarball_round_trip(tmp_path: Path) -> None:
    """Seeded built-ins are tarred, stored, and re-loaded with pruning on the runner."""
    from omnigent.server.app import _tar_gz_dir

    for bundle in (_LEAD_DIR, _PL_DIR):
        blob = _tar_gz_dir(materialize_bundle(bundle, tmp_path / bundle.name / "src"))
        spec = load(
            blob,
            dest=tmp_path / bundle.name / "out",
            expand_env=False,
            prune_invalid_sub_agents=True,
        )
        assert spec.name == bundle.name
    assert [s.name for s in spec.sub_agents] == sorted(_PL_SUB_AGENTS)


def test_packaged_resource_points_at_the_bundles() -> None:
    packaged = Path(_examples_resources.__file__).parent / "factory"
    assert packaged.resolve() == _FACTORY_DIR.resolve()
    assert (packaged / "factory-lead" / "config.yaml").is_file()
    assert (packaged / "factory-pl" / "config.yaml").is_file()
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert "factory/**/*" in package_data["omnigent.resources.examples"]


# ── Roles and model routing ──────────────────────────────────────────────────


def test_every_spec_declares_its_factory_role(lead: AgentSpec, pl: AgentSpec) -> None:
    for spec in _all_specs(lead, pl):
        assert factory_role_from_spec(spec) == _ROLES[str(spec.name)], spec.name


def test_claude_agents_route_through_bifrost_with_pinned_models(
    lead: AgentSpec, pl: AgentSpec
) -> None:
    for spec in _all_specs(lead, pl):
        name = str(spec.name)
        if spec.executor.config["harness"] == "claude-native":
            assert spec.executor.auth == ProviderAuth(name="bifrost"), name
            assert spec.executor.model == _CLAUDE_MODELS[name], name
        else:
            # Codex keeps the sandbox's default (keyless Bifrost) openai provider.
            assert spec.executor.auth is None, name
            assert spec.executor.model is None, name
    assert set(_CLAUDE_MODELS) == {
        str(s.name)
        for s in _all_specs(lead, pl)
        if s.executor.config["harness"] == "claude-native"
    }


# ── Policies ─────────────────────────────────────────────────────────────────


def _function_policies(lead: AgentSpec, pl: AgentSpec) -> list[tuple[str, FunctionPolicySpec]]:
    found: list[tuple[str, FunctionPolicySpec]] = []
    for spec in _all_specs(lead, pl):
        assert spec.guardrails is not None, spec.name
        found.extend(
            (str(spec.name), p)
            for p in spec.guardrails.policies
            if isinstance(p, FunctionPolicySpec)
        )
    return found


def _policy_cases() -> list[object]:
    lead = load(_LEAD_DIR, expand_env=False)
    pl = load(_PL_DIR, expand_env=False)
    cases: list[object] = []
    for agent, policy in _function_policies(lead, pl):
        assert policy.function is not None
        arguments = dict(policy.function.arguments or {})
        cases.append(
            pytest.param(
                agent, policy.name, policy.function.path, arguments, id=f"{agent}-{policy.name}"
            )
        )
    return cases


@pytest.mark.parametrize(("agent", "policy", "path", "arguments"), _policy_cases())
def test_policy_paths_are_registered_and_accept_their_arguments(
    agent: str, policy: str, path: str, arguments: dict[str, object]
) -> None:
    module_name, _, attr = path.rpartition(".")
    if module_name == _FACTORY_POLICY_MODULE and not _module_available(module_name):
        pytest.skip(f"{module_name} is not on this branch yet")
    assert is_registered_handler(path), f"{agent}.{policy}: {path} is not in the policy registry"
    factory = getattr(importlib.import_module(module_name), attr)
    assert callable(factory(**arguments)), f"{agent}.{policy} did not build an evaluator"


def test_policy_set_per_role(lead: AgentSpec, pl: AgentSpec) -> None:
    for spec in _all_specs(lead, pl):
        assert spec.guardrails is not None
        assert spec.guardrails.ask_timeout == 86400, spec.name
        policies = {
            p.name: p for p in spec.guardrails.policies if isinstance(p, FunctionPolicySpec)
        }
        blast = policies["blast_radius"].function
        guard = policies["factory_guard"].function
        assert blast is not None and guard is not None
        assert blast.path == "omnigent.policies.builtins.orchestration.blast_radius"
        assert blast.arguments == {"gate_pushes": False}
        assert guard.path == f"{_FACTORY_POLICY_MODULE}.factory_role_guard"
        assert guard.arguments == {
            "role": factory_role_from_spec(spec),
            "write_repos": ["caffeinelabs/app"],
        }, spec.name
        bounds = policies.get("spawn_bounds")
        if spec.name == "factory-lead":
            assert bounds is not None and bounds.function is not None
            assert bounds.function.arguments == {
                "max_dispatches_per_turn": 4,
                "dispatch_tools": ["sys_factory_spawn", "sys_session_create"],
            }
        elif spec.name == "factory-pl":
            assert bounds is not None and bounds.function is not None
            assert bounds.function.arguments == {
                "max_dispatches_per_turn": 8,
                "dispatch_tools": ["sys_session_send", "sys_session_create"],
            }
        else:
            assert bounds is None, spec.name


def test_every_spec_runs_unsandboxed_in_the_caller_process(lead: AgentSpec, pl: AgentSpec) -> None:
    for spec in _all_specs(lead, pl):
        assert spec.os_env is not None, spec.name
        assert spec.os_env.type == "caller_process", spec.name
        assert spec.os_env.sandbox is not None and spec.os_env.sandbox.type == "none", spec.name
        assert "bash" in (spec.terminals or {}), spec.name


# ── Skills ───────────────────────────────────────────────────────────────────


def test_factory_protocol_skill_is_identical_in_both_bundles() -> None:
    lead_copy = _LEAD_DIR / "skills" / "factory-protocol" / "SKILL.md"
    pl_copy = _PL_DIR / "skills" / "factory-protocol" / "SKILL.md"
    assert lead_copy.read_bytes() == pl_copy.read_bytes()


def test_skills_have_name_and_description_frontmatter(lead: AgentSpec, pl: AgentSpec) -> None:
    for spec in _all_specs(lead, pl):
        for skill in spec.skills:
            assert skill.description, f"{spec.name}/{skill.name} has no description"
            assert (_skill_dir(spec) / skill.name / "SKILL.md").is_file(), skill.name


# ── Tools the prompts name ───────────────────────────────────────────────────


def _relay_tool_names(spec: AgentSpec) -> set[str]:
    """The Omnigent tool surface a native harness sees for *spec*."""
    from omnigent.runner.tool_dispatch import build_native_relay_tool_schemas

    names = {str(s["name"]) for s in build_native_relay_tool_schemas(spec)}
    if not _module_available(_FACTORY_TOOLS_MODULE):
        # Until the factory runner tools land, assume their documented gating.
        names |= _FACTORY_TOOLS - {"sys_factory_spawn"}
        if factory_role_from_spec(spec) == ROLE_LEAD:
            names.add("sys_factory_spawn")
    return names


def _skill_text(spec: AgentSpec) -> str:
    return "\n".join(
        (_skill_dir(spec) / s.name / "SKILL.md").read_text(encoding="utf-8") for s in spec.skills
    )


@pytest.mark.usefixtures("_terminal_registry")
def test_prompts_only_name_tools_their_agent_has(lead: AgentSpec, pl: AgentSpec) -> None:
    for spec in _all_specs(lead, pl):
        named = set(_TOOL_TOKEN.findall(spec.instructions or ""))
        missing = named - _relay_tool_names(spec)
        assert not missing, f"{spec.name} prompt names tools it does not have: {sorted(missing)}"


@pytest.mark.usefixtures("_terminal_registry")
def test_skills_only_name_tools_that_exist(lead: AgentSpec, pl: AgentSpec) -> None:
    universe: set[str] = set()
    for spec in _all_specs(lead, pl):
        universe |= _relay_tool_names(spec)
    for spec in _all_specs(lead, pl):
        missing = set(_TOOL_TOKEN.findall(_skill_text(spec))) - universe
        assert not missing, f"{spec.name} skills name unknown tools: {sorted(missing)}"


@pytest.mark.usefixtures("_terminal_registry")
def test_pl_sub_agent_enum_matches_the_declared_team(pl: AgentSpec) -> None:
    from omnigent.runner.tool_dispatch import build_native_relay_tool_schemas

    send = next(s for s in build_native_relay_tool_schemas(pl) if s["name"] == "sys_session_send")
    parameters = send["parameters"]
    assert isinstance(parameters, dict)
    assert sorted(parameters["properties"]["agent"]["enum"]) == sorted(_PL_SUB_AGENTS)


def test_prompts_carry_the_operational_contracts(lead: AgentSpec, pl: AgentSpec) -> None:
    lead_text = lead.instructions or ""
    for token in (
        "sys_factory_roster",
        "sys_factory_set",
        "sys_factory_spawn",
        "sys_session_post",
        'load_skill("factory-heartbeat")',
        'load_skill("factory-recovery")',
        "Sentinel",
        "~/factory/ledger.md",
    ):
        assert token in lead_text, token
    pl_text = pl.instructions or ""
    for token in (
        "In ONE response",
        'agent="env_keeper"',
        "sys_read_inbox",
        "sys_session_post",
        "python -m omnigent.factory_merge",
        "sys_cancel_task",
        "sys_session_close",
        'load_skill("factory-heartbeat")',
    ):
        assert token in pl_text, token
    env_text = _sub_agent(pl, "env_keeper").instructions or ""
    for token in ("UP:", "STATUS:", "VERIFY ", "MERGED:", "DOWN:", "pnpm factory:up"):
        assert token in env_text, token
    assert "~/factory/env.json" in env_text and "factory:urls" in env_text
    assert "<!-- factory:verify sha=" in env_text
    for name in ("ic", "ic_codex"):
        text = _sub_agent(pl, name).instructions or ""
        assert "RESULT" in text and "NOTES.md" in text, name
        assert 'sys_session_post(to="parent", kind="request"' in text, name
    assert "<!-- factory:review sha=" in (_sub_agent(pl, "reviewer").instructions or "")
    assert "<!-- factory:review sha=" in _skill_text(_sub_agent(pl, "ic"))
