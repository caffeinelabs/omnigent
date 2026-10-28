"""The planner's import-graph tool (``factory-planning/scope-graph.mjs``) holds on its fixture.

It runs with ``node`` only (no dependencies, no network): ``--selftest`` checks
the shipped fixture with ``rg`` and with the plain directory walk, and the CLI
gives the same report either way.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL = _REPO_ROOT / "examples/factory/factory-lead/skills/factory-planning"
_SCRIPT = _SKILL / "scope-graph.mjs"
_FIXTURE = _SKILL / "fixture"
_SCOPES = {
    "api": "services/api/**",
    "db": "services/db",
    "web": "web/src/**",
    "contracts": "services/*/src/contract.ts",
}


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for scope-graph.mjs")
    out = subprocess.run([node, "--version"], capture_output=True, text=True, check=False)
    match = re.match(r"v(\d+)\.", out.stdout.strip())
    if not match or int(match.group(1)) < 18:
        pytest.skip("scope-graph.mjs needs node 18 or newer")
    return node


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_node(), str(_SCRIPT), *args], capture_output=True, text=True, timeout=120, check=False
    )


def _report(*extra: str) -> dict[str, object]:
    scopes = [a for name, glob in _SCOPES.items() for a in ("--scope", f"{name}={glob}")]
    proc = _run("--root", str(_FIXTURE), *scopes, *extra)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_selftest_passes() -> None:
    proc = _run("--selftest")
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert proc.stdout.strip() == "SELFTEST OK"


def test_the_report_is_the_same_with_and_without_rg() -> None:
    assert _report() == _report("--no-rg")


def test_the_report_finds_coupling_hubs_and_overlaps() -> None:
    report = _report()

    assert report["files"] == 11
    assert report["unresolved_relative"] == 0
    assert [(c["from"], c["to"], c["edges"]) for c in report["cross_scope"]] == [  # type: ignore[index, union-attr]
        ("web", "api", 1)
    ]
    hubs = {h["file"]: h["from"] for h in report["hubs"]}  # type: ignore[index, union-attr]
    assert hubs == {
        "packages/shared/src/version.ts": ["api", "db"],
        "packages/shared/src/index.ts": ["api", "web"],
    }
    assert report["shared_outside_scopes"] == list(hubs)
    assert [o["scopes"] for o in report["overlaps"]] == [  # type: ignore[index, union-attr]
        ["api", "contracts"],
        ["contracts", "db"],
    ]
    scopes = report["scopes"]
    assert scopes["db"]["globs"] == ["services/db"]  # type: ignore[index]
    assert {n: s["cohesion"] for n, s in scopes.items()} == {  # type: ignore[union-attr]
        "api": 0.333,
        "contracts": None,
        "db": 0.5,
        "web": 0.5,
    }


def test_the_report_can_go_to_a_file(tmp_path: Path) -> None:
    out = tmp_path / "plan.graph.json"
    proc = _run("--root", str(_FIXTURE), "--scope", "web=web/src/**", "--out", str(out))
    assert proc.returncode == 0 and proc.stdout == ""
    assert json.loads(out.read_text())["scopes"]["web"]["files"] == 3


@pytest.mark.parametrize(
    "args",
    [
        ["--root", str(_FIXTURE)],
        ["--root", str(_FIXTURE), "--scope", "nameless"],
        ["--bogus"],
        ["--root", "/nonexistent/scope-graph", "--scope", "a=b"],
    ],
)
def test_bad_arguments_exit_2(args: list[str]) -> None:
    proc = _run(*args)
    assert proc.returncode == 2
    assert proc.stderr.startswith("scope-graph: ")


def test_the_fixture_ships_no_files_the_bundle_checks_scan() -> None:
    """Fixture files stay out of the prompt and config checks (no .md or .yaml)."""
    assert not [p for p in _FIXTURE.rglob("*") if p.suffix in (".md", ".yaml", ".yml")]
