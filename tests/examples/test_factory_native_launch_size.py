"""Every factory bundle's claude-native launch fits tmux's command cap on old runners.

claude-native carries the agent's raw instructions in argv
(``--append-system-prompt``). Runners from before the private launch script
(``omnigent.inner.terminal._write_launch_script``) pass that whole pane command
inline, and tmux's client packs the launch sequence (option commands,
``new-session`` with the pane command, the pane-died hook) into one message:
tmux 3.5a rejects anything above 16364 packed bytes with "command too long",
so the agent's terminal never starts. A sandbox pod keeps its runner until the
pod is recreated, while the server serves the bundle, so the bundles must stay
under the cap with a margin for every pod still running an old runner.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from omnigent.harnesses.claude_native import bridge as claude_bridge
from omnigent.inner import bundle_skills
from omnigent.inner import terminal as term
from omnigent.runner.native.orchestration import (
    _build_claude_native_base_args,
    _native_startup_raw_instructions_from_spec,
)
from omnigent.spec import load
from omnigent.spec.types import AgentSpec

pytestmark = pytest.mark.posix_only

_FACTORY_DIR = Path(__file__).resolve().parents[2] / "examples" / "factory"

# tmux 3.5a's single-message command cap, in packed bytes.
_TMUX_COMMAND_CAP = 16364
# What a factory bundle may use: 1.3 KB under the cap for path and argv drift.
_LAUNCH_BUDGET = 15000

# Production-shaped launch values: only their lengths matter.
_PYTHON = "/opt/omnigent/.venv/bin/python3"
_SERVER = "https://omnigent.caffeinelabs.io"
_SESSION_ID = "987213ac226e4369a5f2e7814702cf30"
_RESUME_ID = "02857840-6362-408f-b41f-309e396ed7c6"
_BRIDGE_DIR = Path("/tmp/omnigent-1000/claude-native") / ("0" * 32)
_SPEC_CACHE_DIR = "/tmp/runner-specs-runner_0123456789abcdef-x1y2z3w4/" + "a" * 32 + "-v12"
_WORKSPACE = Path("/home/omnigent/workspace")


def _bundles() -> Iterator[tuple[str, Path, str]]:
    """``(name, source dir, production bundle dir)`` for every factory agent."""
    for root_name in ("factory-lead", "factory-pl"):
        root = _FACTORY_DIR / root_name
        yield root_name, root, _SPEC_CACHE_DIR
        for agent in sorted((root / "agents").iterdir()):
            if (agent / "config.yaml").is_file():
                yield agent.name, agent, f"{_SPEC_CACHE_DIR}/agents/{agent.name}"


_BUNDLES = list(_bundles())


def _inline_launch_bytes(
    spec: AgentSpec,
    source_dir: Path,
    bundle_dir: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, int]:
    """
    Pack the claude-native launch the way a pre-launch-script runner does.

    :returns: ``(packed tmux command bytes, raw instruction bytes)``.
    """
    # The settings file and plugin manifest would be written under the bundle
    # and bridge dirs; only their paths reach argv.
    monkeypatch.setattr(claude_bridge, "_write_json_file", lambda *_a, **_k: None)
    monkeypatch.setattr(bundle_skills, "ensure_bundle_plugin_manifest", lambda *_a, **_k: None)
    # Old runners have no launch script: the pane command always goes inline.
    monkeypatch.setattr(term, "_TMUX_INLINE_LAUNCH_MAX_BYTES", sys.maxsize)

    raw = _native_startup_raw_instructions_from_spec(spec) or ""
    executor_config = spec.executor.config or {}
    launch_args = ["--permission-mode", str(executor_config.get("permission_mode", "default"))]
    base = _build_claude_native_base_args(
        reasoning_effort=spec.executor.reasoning_effort or "high",
        model_override=str(spec.executor.model or "opus"),
        terminal_launch_args=launch_args,
        resume_external_session_id=_RESUME_ID,
    )
    args = claude_bridge.augment_claude_args(
        base,
        bridge_dir=_BRIDGE_DIR,
        python_executable=_PYTHON,
        ap_server_url=_SERVER,
        ap_auth_headers={"Authorization": "Bearer x"},
        bundle_dir=source_dir,
        agent_name=str(spec.name),
        skills_filter=spec.skills_filter,
        append_system_prompt=raw or None,
    )
    if raw:
        flag = args.index("--append-system-prompt")
        assert args[flag + 1] == raw, (
            "claude-native no longer passes the raw instructions inline; old runners still "
            "do, so model their construction here before relaxing this check"
        )
    if "--plugin-dir" in args:
        args[args.index("--plugin-dir") + 1] = bundle_dir

    instance = term.TerminalInstance(
        name="claude",
        session_key="main",
        socket_path=tmp_path / "tmux.sock",
        private_dir=tmp_path,
        command="claude",
        args=args,
        scrollback=50000,
        keep_alive_after_exit=True,
        conversation_link=f"{_SERVER}/c/{_SESSION_ID}",
    )
    captured: list[list[str]] = []

    class _Done:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def _fake_exec(*argv: str, **_kwargs: object) -> _Done:
        captured.append(list(argv))
        return _Done()

    # Swap the module's asyncio binding, not the asyncio singleton's attribute.
    monkeypatch.setattr(
        term,
        "asyncio",
        SimpleNamespace(create_subprocess_exec=_fake_exec, subprocess=asyncio.subprocess),
    )
    asyncio.run(instance.launch(cwd=_WORKSPACE))
    [launch] = captured
    client_options = len(instance._tmux_base_cmd())
    sequence = launch[client_options:]
    assert "new-session" in sequence, sequence[:8]
    packed = sum(len(os.fsencode(arg)) + 1 for arg in sequence)
    return packed, len(raw.encode())


def test_every_factory_agent_is_measured() -> None:
    names = sorted(name for name, _, _ in _BUNDLES)
    assert names == sorted(
        [
            "factory-lead",
            "planner",
            "factory-pl",
            "demo",
            "env_keeper",
            "ic",
            "ic_codex",
            "reviewer",
        ]
    )


@pytest.mark.parametrize(
    ("name", "source_dir", "bundle_dir"), _BUNDLES, ids=[name for name, _, _ in _BUNDLES]
)
def test_claude_native_inline_launch_fits_the_tmux_cap(
    name: str,
    source_dir: Path,
    bundle_dir: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = load(source_dir, expand_env=False)
    packed, raw = _inline_launch_bytes(spec, source_dir, bundle_dir, tmp_path, monkeypatch)
    assert raw > 0, name
    assert packed <= _LAUNCH_BUDGET, (
        f"{name}: the claude-native launch packs to {packed} bytes ({raw} of them the "
        f"bundle's raw instructions), over the {_LAUNCH_BUDGET}-byte budget. Runners "
        "without the private launch script pass --append-system-prompt inline in one tmux "
        f"command, and tmux rejects more than {_TMUX_COMMAND_CAP} packed bytes ('command "
        "too long'): the agent's terminal never starts in any pod still on such a runner. "
        "Move long reference material out of the prompt into the bundle's skills, or "
        "tighten its wording, without dropping a rule."
    )
