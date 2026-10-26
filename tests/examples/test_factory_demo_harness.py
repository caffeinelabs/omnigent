"""The demo agent's Playwright harness (``factory-demo`` skill) is pinned and its logic holds.

The browser-free parts (exit codes, ``demo.json``, HAR and trace scrubbing,
browser selection, the stale-preview and usage exits of ``run.mjs``) run under
``node --test``; a real recording is ``node run.mjs --selftest`` (see the skill).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
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


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for the harness tests")
def test_scrubbed_trace_is_a_valid_zip_without_credentials(tmp_path: Path) -> None:
    """A trace written by another zip implementation comes back readable and clean."""
    node = shutil.which("node")
    assert node is not None
    if _node_major(node) < 18:
        pytest.skip("the harness needs node 18 or newer")
    secret = "s" * 64
    event = {
        "type": "before",
        "method": "fetch",
        "params": {"headers": [{"name": "Authorization", "value": f"Bearer {secret}"}]},
    }
    cookies = {
        "type": "before",
        "params": {"cookies": [{"name": "access_token", "value": "tok-12345678"}]},
    }
    source = tmp_path / "trace.zip"
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr(
            "0-trace.trace", f"{json.dumps(event)}\n{json.dumps(cookies)}\n", zipfile.ZIP_DEFLATED
        )
        zf.writestr(
            "0-trace.network",
            '{"snapshot":{"request":{"cookies":[{"name":"a","value":"tok-12345678"}]}}}',
        )
        zf.writestr(
            "resources/page.jpeg", b"\xff\xd8" + secret.encode() + b"\xff\xd9", zipfile.ZIP_STORED
        )
    scrubbed = tmp_path / "scrubbed.zip"
    script = (
        "const [lib, src, dst, ...secrets] = process.argv.slice(1);"
        "const { scrubTraceZip } = await import(lib);"
        "const fs = await import('node:fs');"
        "fs.writeFileSync(dst, scrubTraceZip(fs.readFileSync(src), secrets));"
    )
    proc = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            script,
            (_HARNESS / "lib.mjs").as_uri(),
            str(source),
            str(scrubbed),
            secret,
            "tok-12345678",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    with zipfile.ZipFile(scrubbed) as zf:
        assert zf.testzip() is None
        assert zf.namelist() == ["0-trace.trace", "0-trace.network", "resources/page.jpeg"]
        blobs = {name: zf.read(name) for name in zf.namelist()}
    for name, blob in blobs.items():
        assert secret.encode() not in blob, name
        assert b"tok-12345678" not in blob, name
    lines = blobs["0-trace.trace"].decode().splitlines()
    assert json.loads(lines[0])["params"]["headers"][0] == {
        "name": "Authorization",
        "value": "[redacted]",
    }
    assert blobs["resources/page.jpeg"].startswith(b"\xff\xd8")
