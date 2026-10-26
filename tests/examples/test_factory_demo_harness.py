"""The demo agent's Playwright harness (``factory-demo`` skill) is pinned and its logic holds.

The browser-free parts (exit codes, ``demo.json``, HAR redaction, browser
selection, the stale-preview and usage exits of ``run.mjs``) run under
``node --test``; a real recording is ``node run.mjs --selftest`` (see the skill).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL = _REPO_ROOT / "examples/factory/factory-pl/agents/demo/skills/factory-demo"
_HARNESS = _SKILL / "harness"
_NODE_TESTS = Path(__file__).with_name("factory_demo_harness.test.mjs")
# Must equal the Playwright the sandbox host image bakes into /opt/ms-playwright.
_PLAYWRIGHT = "1.56.1"
_HARNESS_FILES = {
    "package.json",
    "package-lock.json",
    "playwright.config.ts",
    "tsconfig.json",
    "fixtures.ts",
    "lib.mjs",
    "setup.mjs",
    "run.mjs",
    "explore.mjs",
    "selftest.mjs",
}


def _node_major(node: str) -> int:
    out = subprocess.run([node, "--version"], capture_output=True, text=True, check=False)
    match = re.match(r"v(\d+)\.", out.stdout.strip())
    return int(match.group(1)) if match else 0


def test_harness_ships_exactly_its_files() -> None:
    assert {p.name for p in _HARNESS.iterdir()} == _HARNESS_FILES
    assert (_SKILL / "SKILL.md").is_file()


def test_playwright_is_pinned_to_the_baked_version() -> None:
    package = json.loads((_HARNESS / "package.json").read_text())
    assert package["devDependencies"] == {"@playwright/test": _PLAYWRIGHT}
    assert package["type"] == "module"
    lock = json.loads((_HARNESS / "package-lock.json").read_text())
    packages = lock["packages"]
    assert packages[""]["devDependencies"] == {"@playwright/test": _PLAYWRIGHT}
    for name in ("@playwright/test", "playwright", "playwright-core"):
        entry = packages[f"node_modules/{name}"]
        assert entry["version"] == _PLAYWRIGHT, name
        assert entry["resolved"].startswith("https://registry.npmjs.org/"), name
        assert entry["integrity"].startswith("sha512-"), name


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for the harness tests")
def test_harness_logic_passes_its_node_tests() -> None:
    node = shutil.which("node")
    assert node is not None
    if _node_major(node) < 18:
        pytest.skip("the harness needs node 18 or newer")
    proc = subprocess.run(
        [node, "--test", str(_NODE_TESTS)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, f"{proc.stdout[-6000:]}\n{proc.stderr[-3000:]}"
    assert re.search(r"^# fail 0$", proc.stdout, re.MULTILINE), proc.stdout[-2000:]
