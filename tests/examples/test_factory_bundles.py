"""The software-factory agent bundles (``examples/factory``) load and are coherent.

These are the shipped ``factory-lead`` and ``factory-pl`` built-ins: the specs,
their sub-agents, their policies, their skills and the tools their prompts tell
the model to call. A typo in any of them only surfaces once a sandbox is up and
an agent stalls on a tool that does not exist, so it is pinned here.
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
import tomllib
import yaml

from omnigent.factory import (
    ROLE_DEMO,
    ROLE_ENV_KEEPER,
    ROLE_IC,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    ROLE_REVIEWER,
    factory_role_from_spec,
)
from omnigent.policies.builtins.factory import factory_role_guard
from omnigent.policies.registry import is_registered_handler
from omnigent.resources import examples as _examples_resources
from omnigent.spec import load, materialize_bundle
from omnigent.spec.types import AgentSpec, FunctionPolicySpec, ProviderAuth
from omnigent.tools.builtins.factory import FACTORY_TOOL_NAMES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FACTORY_DIR = _REPO_ROOT / "examples" / "factory"
_LEAD_DIR = _FACTORY_DIR / "factory-lead"
_PL_DIR = _FACTORY_DIR / "factory-pl"
_PL_SUB_AGENTS = ("env_keeper", "demo", "ic", "ic_codex", "reviewer")

_FACTORY_POLICY_MODULE = "omnigent.policies.builtins.factory"
_TOOL_TOKEN = re.compile(r"\b(sys_[a-z_]+|load_skill|read_skill_file)\b")
# A documented call, e.g. `sys_session_post(to="parent", kind="request")`.
_TOOL_CALL = re.compile(r"\b(sys_[a-z_]+)\(([^()]*)\)")
_QUOTED = re.compile(r'"[^"]*"')
_KEYWORD = re.compile(r"\b([a-z_]+)=")
_LITERAL_ARG = re.compile(r'\b([a-z_]+)="([^"]*)"')
_FACTORY_TOOL_TOKEN = re.compile(r"\bsys_factory_[a-z_]+\b")

# Family aliases: the provider's models map turns them into concrete ids.
_CLAUDE_MODELS = {
    "factory-lead": "opus",
    "factory-pl": "opus",
    "env_keeper": "sonnet",
    "demo": "sonnet",
    "ic": "sonnet",
}
_ROLES = {
    "factory-lead": ROLE_LEAD,
    "factory-pl": ROLE_PROJECT_LEAD,
    "env_keeper": ROLE_ENV_KEEPER,
    "demo": ROLE_DEMO,
    "ic": ROLE_IC,
    "ic_codex": ROLE_IC,
    "reviewer": ROLE_REVIEWER,
}


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
        assert spec.executor.config["permission_mode"] == "bypassPermissions"
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
    assert {s.name for s in _sub_agent(pl, "demo").skills} == {"factory-demo"}
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


def test_bypass_mode_always_comes_with_the_deny_only_guards(
    lead: AgentSpec, pl: AgentSpec
) -> None:
    """Claude agents skip Claude's own prompts, so omnigent's policy hooks must fence them."""
    for spec in _all_specs(lead, pl):
        if spec.executor.config.get("harness") != "claude-native":
            continue
        assert spec.executor.config.get("permission_mode") == "bypassPermissions", spec.name
        assert spec.guardrails is not None, spec.name
        names = {p.name for p in spec.guardrails.policies}
        assert {"factory_guard", "blast_radius"} <= names, spec.name


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


def _relay_schemas(spec: AgentSpec) -> dict[str, dict[str, object]]:
    """The Omnigent tools a native harness sees for *spec*, by name."""
    from omnigent.runner.tool_dispatch import build_native_relay_tool_schemas

    return {str(s["name"]): s for s in build_native_relay_tool_schemas(spec)}


def _relay_tool_names(spec: AgentSpec) -> set[str]:
    return set(_relay_schemas(spec))


def _parameter_names(schema: dict[str, object]) -> set[str]:
    parameters = schema.get("parameters")
    assert isinstance(parameters, dict), schema
    properties = parameters.get("properties")
    return set(properties) if isinstance(properties, dict) else set()


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
def test_factory_tools_are_on_each_agents_real_surface(lead: AgentSpec, pl: AgentSpec) -> None:
    messaging = {"sys_session_post", "sys_factory_roster", "sys_factory_set"}
    for spec in _all_specs(lead, pl):
        names = _relay_tool_names(spec)
        assert messaging <= names, f"{spec.name} lacks {sorted(messaging - names)}"
        assert ("sys_factory_spawn" in names) is (spec.name == "factory-lead"), spec.name


@pytest.mark.usefixtures("_terminal_registry")
def test_documented_tool_calls_use_real_argument_names(lead: AgentSpec, pl: AgentSpec) -> None:
    """Every `sys_x(arg=...)` in a prompt or skill names a real parameter and enum value."""
    parameters: dict[str, set[str]] = {}
    enums: dict[tuple[str, str], set[str]] = {}
    for spec in _all_specs(lead, pl):
        for name, schema in _relay_schemas(spec).items():
            parameters.setdefault(name, set()).update(_parameter_names(schema))
            properties = schema["parameters"].get("properties") or {}  # type: ignore[union-attr]
            for arg, prop in properties.items():
                if isinstance(prop, dict) and isinstance(prop.get("enum"), list):
                    enums.setdefault((name, arg), set()).update(map(str, prop["enum"]))
    checked = 0
    for spec in _all_specs(lead, pl):
        for source, text in (("prompt", spec.instructions or ""), ("skills", _skill_text(spec))):
            for tool, raw_args in _TOOL_CALL.findall(text):
                assert tool in parameters, f"{spec.name} {source}: unknown tool {tool}"
                for keyword in _KEYWORD.findall(_QUOTED.sub('""', raw_args)):
                    checked += 1
                    assert keyword in parameters[tool], (
                        f"{spec.name} {source}: {tool}({keyword}=...) is not a parameter; "
                        f"real ones: {sorted(parameters[tool])}"
                    )
                for keyword, value in _LITERAL_ARG.findall(raw_args):
                    allowed = enums.get((tool, keyword))
                    if allowed is not None:
                        assert value in allowed, (
                            f"{spec.name} {source}: {tool} {keyword}={value!r}"
                        )
    assert checked > 50, "the call pattern stopped matching the prompts"


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
        "sys_factory_ask",
        "sys_factory_pr",
        "sys_session_post",
        'load_skill("factory-heartbeat")',
        'load_skill("factory-recovery")',
        "Sentinel",
        "~/factory/ledger.md",
        "[Factory brief · from <human> via cockpit",
        "[Factory control",
        "[Factory decision",
        "[Factory notice · PR",
        'message="Start. Your charter follows."',
        "Cockpit: /factory/<factory id>",
        "READY FOR HUMAN MERGE",
    ):
        assert token in lead_text, token
    pl_text = pl.instructions or ""
    for token in (
        "In ONE response",
        'agent="env_keeper"',
        "sys_read_inbox",
        "sys_session_post",
        "sys_factory_pr",
        "sys_factory_ask",
        'sys_factory_set(stage="building"',
        "READY FOR HUMAN MERGE",
        "python -m omnigent.factory_merge",
        "sys_cancel_task",
        "sys_session_close",
        'load_skill("factory-heartbeat")',
        "PREVIEW pr=<n> head=<sha>",
        "DEMO pr=<url> sha=<40-char sha>",
        "[Factory control",
        "[Factory review · CHANGES REQUESTED",
    ):
        assert token in pl_text, token
    # Humans merge caffeinelabs/app: the PL hands green PRs over instead.
    assert "never merge and never run the merge gate on it" in " ".join(pl_text.split())
    env_text = _sub_agent(pl, "env_keeper").instructions or ""
    for token in (
        "UP base=<base>:",
        "STATUS:",
        "DOWN:",
        "PREVIEW pr=<n> head=<sha> base=<base>",
        "PREVIEW_DOWN pr=<n>",
        "PREVIEW_STATUS",
        "MERGED pr=<n> base=<base>",
        "CLOSED pr=<n>",
        "pnpm factory:up",
        "scripts/factory.ts preview up",
        "pnpm -s factory:preview up",
        "sys_factory_env",
    ):
        assert token in env_text, token
    assert "~/factory/env.json" in env_text and "factory:urls" in env_text
    assert (
        "<!-- factory:verify sha=<sha> result=PASS|FAIL env=<api url> preview=<app url> -->"
        in (env_text)
    )
    demo_text = _sub_agent(pl, "demo").instructions or ""
    for token in ('load_skill("factory-demo")', "sys_factory_demo", "gh pr comment", "run.mjs"):
        assert token in demo_text, token
    for name in ("ic", "ic_codex"):
        text = _sub_agent(pl, name).instructions or ""
        assert "RESULT" in text and "NOTES.md" in text, name
        assert 'sys_session_post(to="parent", kind="request"' in text, name
        assert 'sys_factory_pr(url=<pr url>, task=<task>, claim="opened")' in text, name
        assert "Demo plan" in text, name
    assert "<!-- factory:review sha=" in (_sub_agent(pl, "reviewer").instructions or "")
    assert "<!-- factory:review sha=" in _skill_text(_sub_agent(pl, "ic"))


# ── Cockpit round: tools, server texts, previews and demos ──────────────────


def _all_texts(lead: AgentSpec, pl: AgentSpec) -> Iterator[tuple[str, str]]:
    """Every prompt and skill the factory agents read, as ``(label, text)``."""
    for spec in _all_specs(lead, pl):
        yield f"{spec.name} prompt", spec.instructions or ""
        for skill in spec.skills:
            path = _skill_dir(spec) / skill.name / "SKILL.md"
            yield f"{spec.name}/{skill.name}", path.read_text(encoding="utf-8")


def test_every_factory_tool_named_anywhere_exists(lead: AgentSpec, pl: AgentSpec) -> None:
    named: set[str] = set()
    for label, text in _all_texts(lead, pl):
        found = set(_FACTORY_TOOL_TOKEN.findall(text))
        assert found <= FACTORY_TOOL_NAMES, f"{label}: {sorted(found - FACTORY_TOOL_NAMES)}"
        named |= found
    factory_tools = {t for t in FACTORY_TOOL_NAMES if t.startswith("sys_factory_")}
    assert named == factory_tools, f"never documented: {sorted(factory_tools - named)}"


def test_no_typed_merged_command_remains(lead: AgentSpec, pl: AgentSpec) -> None:
    """Merges arrive as server notices; nobody types or relays `merged <url>` any more."""
    for label, text in _all_texts(lead, pl):
        assert not re.search(r"merged <url>", text, re.IGNORECASE), label
        # The old merge poll; Sentinel's one verification reads more fields.
        assert not re.search(r"gh pr view <url> --json state\b(?!,)", text), label


def _command_section(text: str, start: str, ends: tuple[str, ...]) -> str:
    begin = text.index(start)
    stops = [text.find(end, begin + len(start)) for end in ends]
    found = [stop for stop in stops if stop > begin]
    return text[begin : min(found)] if found else text[begin:]


def test_verify_never_moves_the_dev_env(pl: AgentSpec) -> None:
    """PREVIEW (alias VERIFY) leaves the preview at the PR head; only MERGED moves the dev env."""
    env = _sub_agent(pl, "env_keeper")
    skill = (_skill_dir(env) / "factory-env" / "SKILL.md").read_text(encoding="utf-8")
    prompt = env.instructions or ""
    reset = re.compile(r"checkout\s+(-q\s+)?--detach\s+origin/<base>")
    sections = {
        "prompt": _command_section(prompt, "PREVIEW pr=<n>", ("PREVIEW_DOWN",)),
        "skill": _command_section(skill, "### PREVIEW pr=<n>", ("### PREVIEW_DOWN",)),
    }
    for label, section in sections.items():
        assert "VERIFY" in section or label == "skill", label
        assert not reset.search(section), f"{label}: the PREVIEW/VERIFY steps reset to the base"
        assert "factory:verify sha=" in section, label
    merged = _command_section(skill, "### MERGED pr=<n>", ("## Reading Tilt",))
    assert reset.search(merged), "MERGED moves the dev env to the new origin/<base>"
    assert "VERIFY is the same" in prompt
    assert "FACTORY_TILT_PORT" not in prompt + skill


# ── Base branch ────────────────────────────────────────────────

# `main` hardcoded as the PR target, the branch point, the sync source or the dev env commit.
_HARDCODED_MAIN = re.compile(r"origin/main\b|--base main\b|merge main\b|fetch origin main\b")
# A dev env or preview command in backticks, and what follows its fixed part.
_ENV_COMMAND = re.compile(r"`(UP|MERGED pr=<n>|PREVIEW pr=<n> head=<sha>)((?: [^`]*)?)`")


def _bundle_files() -> Iterator[tuple[str, str]]:
    """Every agent-facing file of both bundles (specs and skills), whitespace folded."""
    for path in sorted(_FACTORY_DIR.rglob("*")):
        if path.suffix in (".md", ".yaml") and "node_modules" not in path.parts:
            text = " ".join(path.read_text(encoding="utf-8").split())
            yield str(path.relative_to(_FACTORY_DIR)), text


def test_no_agent_instruction_hardcodes_main_as_the_base() -> None:
    """PRs, worktrees, syncs and the dev env follow `<base>`; `main` is only its default."""
    files = dict(_bundle_files())
    assert len(files) >= 15, sorted(files)
    for label, text in files.items():
        hit = _HARDCODED_MAIN.search(text)
        assert hit is None, f"{label}: {hit.group(0) if hit else ''}"
    # The default is spelled out where the base is introduced.
    for label in (
        "factory-pl/config.yaml",
        "factory-pl/skills/factory-pl-playbook/SKILL.md",
        "factory-pl/agents/env_keeper/config.yaml",
        "factory-pl/agents/env_keeper/skills/factory-env/SKILL.md",
        "factory-pl/agents/ic/skills/factory-ic/SKILL.md",
        "factory-pl/agents/ic_codex/config.yaml",
    ):
        assert re.search(r"`main` when|default `main`", files[label]), label


def test_the_pl_passes_the_base_on_every_dispatch(pl: AgentSpec) -> None:
    """Every UP, PREVIEW and MERGED the PL sends names the base, and so does every task card."""
    playbook = (_PL_DIR / "skills/factory-pl-playbook/SKILL.md").read_text(encoding="utf-8")
    heartbeat = (_PL_DIR / "skills/factory-heartbeat/SKILL.md").read_text(encoding="utf-8")
    prompt = " ".join((pl.instructions or "").split())
    assert "`Base branch:`" in prompt
    assert 'args={input: "UP base=<base>"}' in prompt
    sent = 0
    for label, text in (("prompt", prompt), ("playbook", playbook), ("heartbeat", heartbeat)):
        for command in _ENV_COMMAND.finditer(" ".join(text.split())):
            sent += 1
            assert command.group(2).startswith(" base=<base>"), f"{label}: {command.group(0)}"
    assert sent >= 10, sent
    card = _command_section(playbook, "## Task card", ("\n## ",))
    assert "· Base: <base>\n" in card and "origin/main" not in card


def test_sub_agents_act_on_the_base(pl: AgentSpec) -> None:
    """env_keeper tracks origin/<base>; ICs branch, sync and open PRs on it; reviewers check it."""
    env = _sub_agent(pl, "env_keeper")
    env_skill = (_skill_dir(env) / "factory-env" / "SKILL.md").read_text(encoding="utf-8")
    env_text = " ".join(((env.instructions or "") + env_skill).split())
    for token in (
        "worktree add --detach /home/omnigent/workspace/wt/env origin/<base>",
        'ref: "origin/<base>"',
        "--json headRefOid,state,baseRefName",
        'CONFLICT=$(git diff --name-only "origin/$BASE...HEAD"',
        "fetch origin pull/<n>/head <base>",
    ):
        assert token in env_text, token
    ic = _sub_agent(pl, "ic")
    ic_skill = (_skill_dir(ic) / "factory-ic" / "SKILL.md").read_text(encoding="utf-8")
    codex = _sub_agent(pl, "ic_codex").instructions or ""
    for label, text in (("factory-ic", ic_skill), ("ic_codex", codex)):
        flat = " ".join(text.split())
        assert "/home/omnigent/workspace/wt/<task> origin/<base>" in flat, label
        assert "git merge origin/<base>" in flat, label
        assert "--base <base> --head factory/<slug>/<task>" in flat, label
    assert "--base <base>" in " ".join((ic.instructions or "").split())
    for label, text in (
        ("reviewer", _sub_agent(pl, "reviewer").instructions or ""),
        ("ic", ic_skill),
    ):
        assert "baseRefName" in text and "`Base:`" in text, label


def test_chat_charters_name_the_base_like_stored_ones() -> None:
    """The lead's chat charter template carries the exact base line the server renders."""
    from omnigent.entities.factory import FactoryProject
    from omnigent.server.factory.charter import render_pl_charter

    project = FactoryProject(
        id="c" * 32, factory_id="f" * 32, slug="s", title="t", repo="r", base_branch="<branch>"
    )
    [line] = [x for x in render_pl_charter(project).splitlines() if x.startswith("Base branch:")]
    playbook = (_LEAD_DIR / "skills/factory-lead-playbook/SKILL.md").read_text(encoding="utf-8")
    assert f"\n{line}\n" in playbook
    assert "baseRefName" in playbook


# A result line a sub-agent ends its turn with, e.g. `PREVIEW SKIPPED tooling-conflict <files>`.
_RESULT_LINE = re.compile(
    r"\b(ENV|PREVIEW|DEMO) ((?:[A-Z]{2,}\|)*[A-Z]{2,})\b(?: ((?:[a-z]+-)+[a-z]+)\b)?"
)


def _result_lines(text: str) -> set[tuple[str, str, str | None]]:
    """``(prefix, status, reason)`` for every result line in ``text``, ``A|B`` expanded."""
    found: set[tuple[str, str, str | None]] = set()
    for prefix, statuses, reason in _RESULT_LINE.findall(text):
        for status in statuses.split("|"):
            found.add((prefix, status, reason or None))
    return found


def _handles(text: str, prefix: str, status: str, reason: str | None) -> bool:
    alternatives = rf"(?:[A-Z]{{2,}}\|)*{status}(?:\|[A-Z]{{2,}})*"
    tail = r"(?:\|(?:[a-z]+-)+[a-z]+)*" if reason else ""
    reasons = rf" (?:(?:[a-z]+-)+[a-z]+\|)*{re.escape(reason)}{tail}" if reason else r"\b"
    return re.search(rf"\b{prefix} {alternatives}{reasons}", text) is not None


def test_pl_handles_every_env_keeper_and_demo_result(pl: AgentSpec) -> None:
    """Each line env_keeper or the demo agent can end with has a row in the PL's results table."""
    emitters = []
    for name, skill in (("env_keeper", "factory-env"), ("demo", "factory-demo")):
        agent = _sub_agent(pl, name)
        emitters.append(agent.instructions or "")
        emitters.append((_skill_dir(agent) / skill / "SKILL.md").read_text(encoding="utf-8"))
    emitters.append(
        (_PL_DIR / "agents/demo/skills/factory-demo/harness/run.mjs").read_text(encoding="utf-8")
    )
    emitted = set().union(*(_result_lines(text) for text in emitters))
    assert {
        ("ENV", "READY", None),
        ("ENV", "DEGRADED", None),
        ("PREVIEW", "FAILED", None),
        ("PREVIEW", "SKIPPED", "tooling-conflict"),
        ("PREVIEW", "SKIPPED", "not-open"),
        ("PREVIEW", "CAPPED", None),
        ("PREVIEW", "STATUS", None),
        ("DEMO", "ERROR", None),
        ("DEMO", "BLOCKED", "stale-preview"),
        ("DEMO", "BLOCKED", "head-moved"),
    } <= emitted, sorted(emitted)
    playbook = (_PL_DIR / "skills/factory-pl-playbook/SKILL.md").read_text(encoding="utf-8")
    table = _command_section(playbook, "## Results from env_keeper and demo", ("\n## ",))
    missing = sorted(
        " ".join(p for p in line if p) for line in emitted if not _handles(table, *line)
    )
    assert not missing, f"the PL playbook's results table has no row for: {missing}"
    assert "playbook's results table" in (pl.instructions or "")


def test_demo_skill_frontmatter_and_the_demo_agent(pl: AgentSpec) -> None:
    demo = _sub_agent(pl, "demo")
    assert [s.name for s in demo.skills] == ["factory-demo"]
    text = (_skill_dir(demo) / "factory-demo" / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match is not None, "factory-demo SKILL.md has no frontmatter"
    meta = yaml.safe_load(match.group(1))
    assert meta["name"] == "factory-demo"
    assert "Playwright" in meta["description"]
    harness = _skill_dir(demo) / "factory-demo" / "harness"
    for name in (
        "run.mjs",
        "explore.mjs",
        "fixtures.ts",
        "playwright.config.ts",
        "package-lock.json",
    ):
        assert (harness / name).is_file(), name
    assert demo.executor.config["permission_mode"] == "bypassPermissions"
    assert not (demo.terminals or {}).keys() - {"bash"}


@pytest.mark.usefixtures("_terminal_registry")
def test_factory_tools_follow_the_role_matrix(lead: AgentSpec, pl: AgentSpec) -> None:
    has = {str(spec.name): _relay_tool_names(spec) for spec in _all_specs(lead, pl)}
    for name, tools in has.items():
        assert {"sys_factory_ask", "sys_factory_pr"} <= tools, name
        assert ("sys_factory_env" in tools) is (name in {"factory-pl", "env_keeper"}), name
        assert ("sys_factory_demo" in tools) is (name in {"factory-pl", "env_keeper", "demo"}), (
            name
        )


def test_heartbeat_skills_end_quiet_turns_with_heartbeat_ok() -> None:
    for bundle in (_LEAD_DIR, _PL_DIR):
        text = (bundle / "skills" / "factory-heartbeat" / "SKILL.md").read_text(encoding="utf-8")
        assert "HEARTBEAT_OK · " in text, bundle.name
        assert "Handle only the items listed in the delta" in text, bundle.name
        assert "gh pr view" not in text and "gh pr checks" not in text, bundle.name


def test_protocol_covers_the_cockpit_server_texts() -> None:
    text = (_LEAD_DIR / "skills" / "factory-protocol" / "SKILL.md").read_text(encoding="utf-8")
    for token in (
        "[Factory control",
        "[Factory decision",
        "[Factory review",
        "[Factory brief · from <human> via cockpit",
        "[Factory notice · PR",
        "id=mail_",
        "never post DECISIONS NEEDED lists",
        "status goes in `summary` only",
        "Never poll GitHub or curl for what the server reports",
        "<!-- factory:demo sha=<head> status=PASS|PARTIAL|FAIL demo=<demo id> -->",
        "HEARTBEAT_OK · <status>",
    ):
        assert token in text, token


def test_leads_keep_titles_plain(lead: AgentSpec) -> None:
    """The beacon lives in the summary; titles stay the members' names."""
    text = lead.instructions or ""
    assert 'title="<Atlas|Sentinel>' not in text
    assert re.search(r'sys_factory_set\(summary="', text)


# The fenced shell blocks the skills document, run through the guards that will see them.
_PLACEHOLDERS = (
    (re.compile(r"<40-char sha>|<sha>"), "7767f0d1c2a94b8e3f50d6a71b2c9e84f03d5a16"),
    (re.compile(r"<n>"), "9630"),
    (re.compile(r"<sha7>"), "7767f0d"),
    (re.compile(r"<repo>"), "/home/omnigent/workspace/app"),
    (re.compile(r"<tooling ref>"), "main"),
    (re.compile(r"<base>"), "temp-main"),
    (re.compile(r"<pr url>|<url>"), "https://github.com/caffeinelabs/app/pull/9630"),
    (re.compile(r"<[^<>\n]+>"), "x"),
)


def _shell_blocks(path: Path) -> list[str]:
    blocks = re.findall(r"```bash\n(.*?)```", path.read_text(encoding="utf-8"), re.S)
    out = []
    for block in blocks:
        for pattern, value in _PLACEHOLDERS:
            block = pattern.sub(value, block)
        out.append(textwrap.dedent(block))
    return out


def _guard(role: str, command: str) -> dict[str, object]:
    event = {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": command}}}
    return factory_role_guard(role=role, write_repos=["caffeinelabs/app"])(event, {})


@pytest.mark.parametrize(
    ("skill", "roles"),
    [
        pytest.param(
            _PL_DIR / "agents/env_keeper/skills/factory-env/SKILL.md",
            (ROLE_ENV_KEEPER, ROLE_PROJECT_LEAD),
            id="factory-env",
        ),
        pytest.param(
            _PL_DIR / "agents/demo/skills/factory-demo/SKILL.md",
            (ROLE_DEMO, ROLE_PROJECT_LEAD),
            id="factory-demo",
        ),
        pytest.param(
            _PL_DIR / "skills/factory-pl-playbook/SKILL.md",
            (ROLE_PROJECT_LEAD,),
            id="factory-pl-playbook",
        ),
    ],
)
def test_documented_commands_pass_their_guards(skill: Path, roles: tuple[str, ...]) -> None:
    """A sub-agent answers to its own and its PL's guard; both must admit the skill's commands."""
    blocks = _shell_blocks(skill)
    assert blocks, skill
    for block in blocks:
        for role in roles:
            decision = _guard(role, block)
            assert decision["result"] == "ALLOW", f"{role} denied:\n{block}\n{decision}"


def test_preview_lifecycle_stays_with_env_keeper() -> None:
    deploy = next(
        b
        for b in _shell_blocks(_PL_DIR / "agents/env_keeper/skills/factory-env/SKILL.md")
        if "preview up" in b
    )
    for role in (ROLE_DEMO, ROLE_IC, ROLE_REVIEWER, ROLE_LEAD):
        assert _guard(role, deploy)["result"] == "DENY", role
    assert _guard(ROLE_DEMO, "git push origin factory/x/y")["result"] == "DENY"


def test_preview_overlay_and_harness_lookup_stay_portable() -> None:
    """The overlay is exactly the app CLI's tooling paths, and a failed fetch never deploys."""
    env_skill = _PL_DIR / "agents/env_keeper/skills/factory-env/SKILL.md"
    deploy = next(b for b in _shell_blocks(env_skill) if "TOOLING=" in b)
    assert "scripts/lib" not in deploy
    assert '{ echo "tooling-fetch-failed"; exit 4; }' in deploy
    down = _command_section(env_skill.read_text(encoding="utf-8"), "### PREVIEW_DOWN", ("### ",))
    assert down.index("preview.json") < down.index("FP down")
    demo_skill = (_PL_DIR / "agents/demo/skills/factory-demo/SKILL.md").read_text(encoding="utf-8")
    assert "-printf" not in demo_skill
    assert "-x .install.lock" in demo_skill


def test_preview_tooling_ref_is_app_main() -> None:
    """caffeinelabs/app main carries the preview tooling; the deleted branch only maps to main."""
    env_skill = _PL_DIR / "agents/env_keeper/skills/factory-env/SKILL.md"
    deploy = next(b for b in _shell_blocks(env_skill) if "TOOLING=" in b)
    assert re.search(r"^REF=main\b", deploy, re.M)
    env_text = " ".join(env_skill.read_text(encoding="utf-8").split())
    alias = "`tooling=factory/preview-tooling` names the deleted pre-#9640 branch and means `main`"
    assert env_text.count("factory/preview-tooling") == 1 and alias in env_text
    texts = [
        env_text.replace(alias, ""),
        (_PL_DIR / "agents/env_keeper/config.yaml").read_text(encoding="utf-8"),
        (_PL_DIR / "skills/factory-pl-playbook/SKILL.md").read_text(encoding="utf-8"),
        (_REPO_ROOT / "docs/FACTORY.md").read_text(encoding="utf-8"),
    ]
    for text in texts:
        assert "factory/preview-tooling" not in text
        assert "APP-1" not in text


_TOOLING_FILES = (
    "Tiltfile",
    "scripts/factory.ts",
    "deploy/tilt/postgres.yaml",
    "deploy/services/src/builders/tilt.ts",
    "deploy/services/api/src/tilt.ts",
    "deploy/services/api/zz_generated/tilt/Deployment.yaml",
)
_STUB = (
    '#!/bin/sh\necho "$(basename "$0") $* tooling=$FACTORY_PREVIEW_TOOLING_REF" >> "$STUB_LOG"\n'
)


def _git_env(path: str, **extra: str) -> dict[str, str]:
    """A git environment free of the caller's config and of hook variables such as GIT_DIR."""
    return {
        "PATH": path,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        **extra,
    }


def _git(cwd: Path, *args: str) -> str:
    env = _git_env(os.environ.get("PATH", ""))
    done = subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, body in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def app_repo(tmp_path: Path) -> dict[str, object]:
    """An app origin whose main gained the preview tooling, a temp-main base cut before it, and a
    clone with five PR heads."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    old = dict.fromkeys(_TOOLING_FILES, "old\n")
    old |= {"package.json": '{"scripts": {"factory:up": "x"}}\n', "app.txt": "base\n"}
    base = _commit(origin, old, "old main")
    _git(origin, "checkout", "-q", "-b", "old-head")
    heads = {"old": _commit(origin, {"app.txt": "feature\n"}, "old-based PR")}
    _git(origin, "checkout", "-q", "-b", "old-tooling", base)
    heads["conflict"] = _commit(origin, {"Tiltfile": "pr\n"}, "old-based tooling PR")
    # The project's base (the deploy block's <base>) changed a tooling file main never had.
    _git(origin, "checkout", "-q", "-b", "temp-main", base)
    _commit(origin, {"Tiltfile": "temp-main tweak\n"}, "temp-main tweak")
    _git(origin, "checkout", "-q", "-b", "temp-head")
    heads["temp"] = _commit(origin, {"app.txt": "feature\n"}, "temp-main-based PR")
    _git(origin, "checkout", "-q", "main")
    new = dict.fromkeys(_TOOLING_FILES, "new\n")
    new["scripts/factory.ts"] = "switch (command) {\n    case 'preview':\n}\n"
    new["package.json"] = '{"scripts": {"factory:preview": "x"}}\n'
    main = _commit(origin, new, "preview tooling")
    _git(origin, "checkout", "-q", "-b", "new-head")
    heads["new"] = _commit(origin, {"app.txt": "feature\n"}, "new-based PR")
    _git(origin, "checkout", "-q", "-b", "mixed-head", main)
    no_script = {"package.json": '{"scripts": {"factory:up": "x"}}\n'}
    heads["mixed"] = _commit(origin, no_script, "PR without the factory:preview script")
    _git(origin, "checkout", "-q", "main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    branches = ("old-head", "old-tooling", "new-head", "mixed-head", "temp-head", "temp-main")
    _git(clone, "fetch", "-q", "origin", *branches)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for tool in ("pnpm", "node"):
        (stubs / tool).write_text(_STUB, encoding="utf-8")
        (stubs / tool).chmod(0o755)
    return {"clone": clone, "heads": heads, "main": main, "stubs": stubs, "log": tmp_path / "log"}


def _run_deploy(app_repo: dict[str, object], head: str) -> subprocess.CompletedProcess[str]:
    clone = app_repo["clone"]
    assert isinstance(clone, Path)
    _git(clone, "checkout", "-q", "--detach", head)
    env_skill = _PL_DIR / "agents/env_keeper/skills/factory-env/SKILL.md"
    deploy = next(b for b in _shell_blocks(env_skill) if "TOOLING=" in b)
    deploy = deploy.replace("cd /home/omnigent/workspace/wt/preview-p9630", f"cd {clone}")
    path = f"{app_repo['stubs']}{os.pathsep}{os.environ.get('PATH', '')}"
    env = _git_env(path, STUB_LOG=str(app_repo["log"]))
    return subprocess.run(["bash", "-c", deploy], env=env, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_preview_deploy_block_overlays_only_heads_without_the_tooling(
    app_repo: dict[str, object],
) -> None:
    """A head with the preview tooling deploys as is; an older head gets main's tooling."""
    clone, log, heads = app_repo["clone"], app_repo["log"], app_repo["heads"]
    assert isinstance(clone, Path) and isinstance(log, Path) and isinstance(heads, dict)

    done = _run_deploy(app_repo, heads["new"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert log.read_text().splitlines() == [
        "pnpm -s factory:preview up --name p9630 --pr 9630 "
        "--sha 7767f0d1c2a94b8e3f50d6a71b2c9e84f03d5a16 tooling="
    ]
    assert _git(clone, "status", "--porcelain") == ""

    log.unlink()
    done = _run_deploy(app_repo, heads["mixed"])
    assert done.returncode == 0, done.stdout + done.stderr
    [call] = log.read_text().splitlines()
    assert call.startswith("node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON")
    assert call.endswith("tooling=")
    assert _git(clone, "status", "--porcelain") == ""

    log.unlink()
    done = _run_deploy(app_repo, heads["old"])
    assert done.returncode == 0, done.stdout + done.stderr
    [call] = log.read_text().splitlines()
    assert call.startswith("node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON")
    assert "scripts/factory.ts preview up --name p9630" in call
    assert call.endswith(f"tooling={app_repo['main']}")
    for name in _TOOLING_FILES:
        assert (clone / name).read_text() != "old\n", name
    assert "factory:up" in (clone / "package.json").read_text()
    assert (clone / "app.txt").read_text() == "feature\n"

    log.unlink()
    _git(clone, "reset", "-q", "--hard")
    done = _run_deploy(app_repo, heads["conflict"])
    assert done.returncode == 3
    assert "tooling-conflict: Tiltfile" in done.stdout
    assert not log.exists()

    # The conflict check diffs against the PR's base; a tooling change of the base is not the PR's.
    done = _run_deploy(app_repo, heads["temp"])
    assert done.returncode == 0, done.stdout + done.stderr
    [call] = log.read_text().splitlines()
    assert call.endswith(f"tooling={app_repo['main']}")
    assert (clone / "app.txt").read_text() == "feature\n"


def test_real_bundles_seed_with_the_demo_harness(
    monkeypatch: pytest.MonkeyPatch, db_uri: str, tmp_path: Path
) -> None:
    """The packaged bundles seed as built-ins, and the harness survives the round trip."""
    from omnigent.runtime.agent_cache import AgentCache
    from omnigent.server import app as server_app
    from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
    from omnigent.stores.artifact_store.local import LocalArtifactStore

    monkeypatch.setenv("OMNIGENT_FACTORY_ENABLED", "1")
    store = SqlAlchemyAgentStore(db_uri)
    artifacts = LocalArtifactStore(str(tmp_path / "artifacts"))
    cache = AgentCache(artifacts, tmp_path / "cache")
    server_app._ensure_factory_agents(store, artifacts, cache, bundles_root=_FACTORY_DIR)

    assert store.get_by_name("factory-lead") is not None
    row = store.get_by_name("factory-pl")
    assert row is not None
    spec = cache.load(row.id, row.bundle_location, expand_env=True).spec
    demo = next(s for s in spec.sub_agents if s.name == "demo")
    skill = next(s for s in demo.skills if s.name == "factory-demo")
    assert skill.skill_dir is not None
    harness = skill.skill_dir / "harness"
    assert (harness / "run.mjs").read_bytes() == (
        _PL_DIR / "agents/demo/skills/factory-demo/harness/run.mjs"
    ).read_bytes()
    assert not (harness / "node_modules").exists()


# ── Temp-main lane: integration env and base notices ───────────


def _row_pattern(row: str) -> re.Pattern[str]:
    """A DISPATCH row's backticked header as a regex: `<x>` is one token, `…` anything."""
    parts = re.split(r"(<[^>]+>|…)", row)
    out = ""
    for part in parts:
        if part == "…":
            out += ".*"
        elif part.startswith("<") and part.endswith(">"):
            out += r"\S+?"
        else:
            out += re.escape(part)
    return re.compile(out)


def test_the_integration_project_has_its_own_branch(pl: AgentSpec) -> None:
    """A `KIND: integration` charter keeps a live env at the base head and nothing else."""
    from omnigent.entities.factory import FactoryProject
    from omnigent.server.factory.charter import normalize_charter, render_pl_charter

    prompt = " ".join((pl.instructions or "").split())
    section = prompt[
        prompt.index("KIND: integration.") : prompt.index("`~/factory/<slug>/plan.md`")
    ]
    for token in (
        "no tasks, ICs, reviewers, demos or PRs",
        "heartbeats are off",
        'sys_session_send(agent="env_keeper", title="env", args={input: "UP base=<base>"})',
        "curl -fsS <app>/__factory/preview",
        "curl -fsS <api>/version",
        "git ls-remote origin refs/heads/<base>",
        'sys_factory_env(state="ready"',
        'ref: "origin/<base>"',
        'sys_factory_set(summary="<base>@<sha7>',
        "`UP base=<base>` again (it moves the env to the new head)",
        "sys_factory_ask",
    ):
        assert token in section, token
    project = FactoryProject(
        id="c" * 32,
        factory_id="f" * 32,
        slug="base-env",
        title="Base env · temp-main",
        repo="caffeinelabs/app",
        base_branch="temp-main",
        charter=normalize_charter({"kind": "integration"}),
    )
    charter = render_pl_charter(project).splitlines()
    assert charter[1].startswith("KIND: integration · ")
    # env_keeper's UP moves an existing dev env to the current head of the base.
    env = _sub_agent(pl, "env_keeper")
    skill = (_skill_dir(env) / "factory-env" / "SKILL.md").read_text(encoding="utf-8")
    up = " ".join(_command_section(skill, "UP base=<base>:", ("STATUS",)).split())
    assert "If it exists, move it to the current head" in up
    assert "checkout --detach origin/<base>" in up


def test_base_notices_have_dispatch_rows(pl: AgentSpec) -> None:
    """Every base-lane notice the server sends matches a row of the PL prompt."""
    from omnigent.entities.factory import FactoryPr
    from omnigent.server.factory.base import redeploy_notice, sync_notice
    from omnigent.server.factory.prs import base_notice

    prompt = " ".join((pl.instructions or "").split())
    rows = re.findall(r"`(\[Factory notice · (?:PR #|base )[^`]*\])`", prompt)
    assert len(rows) == 3, rows
    pr = FactoryPr(
        id="p" * 32,
        factory_id="f" * 32,
        repo="caffeinelabs/app",
        number=9630,
        url="https://github.com/caffeinelabs/app/pull/9630",
    )
    head, old = "a" * 40, "b" * 40
    texts = [
        base_notice(pr, "main", "temp-main"),
        sync_notice("temp-main", head),
        redeploy_notice("caffeinelabs/app", "temp-main", old, head),
    ]
    for text in texts:
        header = text.split("]", 1)[0] + "]"
        assert any(_row_pattern(row).fullmatch(header) for row in rows), header
