"""
Factory fixtures, stories, docs and code name no product.

The tokens are :data:`~tests.examples.factory_sample_tokens.FORBIDDEN_SAMPLE_TOKENS`;
bundles have their own check in ``test_factory_bundles.py``. Sample data uses
``sample-org/sample-app``, ``example.com`` and people such as ``ana (sample)``.
"""

from __future__ import annotations

from pathlib import Path

from tests.examples.factory_sample_tokens import REPO_ROOT, token_lines

SCANNED: tuple[str, ...] = (
    # Server fixtures, goldens, fakes and the tests built on them.
    "tests/server/factory",
    "tests/policies/factory_samples.py",
    # Cockpit components, stories and their __fixtures__.
    "web/src/components/factory",
    "web/src/pages/factory",
    "web/src/lib/__fixtures__",
    "web/src/lib/factory*",
    "docs/FACTORY.md",
    "docs/factory-demo",
    # Factory code.
    "omnigent/server/factory",
    "omnigent/stores/factory_store",
    "omnigent/factory.py",
    "omnigent/factory_gates.py",
    "omnigent/factory_merge.py",
    "omnigent/entities/factory.py",
    "omnigent/policies/builtins/factory*.py",
    "omnigent/tools/builtins/factory.py",
)
# Lines that still name a product, and why; a count may only shrink.
TOKENS_LEFT: dict[str, int] = {
    # Tier 2's default Smith repo: the deployment's own omnigent fork (env-overridable).
    "omnigent/server/factory/tier2.py": 1,
    # Runner-imported guard constants (hosts lag the server): the default
    # protected branches include a live test base; the smith profile's repo.
    "omnigent/policies/builtins/factory.py": 2,
    # The cockpit's docs link points at the fork that hosts docs/FACTORY.md.
    "web/src/lib/factory.ts": 1,
}


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for entry in SCANNED:
        for match in sorted(REPO_ROOT.glob(entry)):
            files.extend(sorted(match.rglob("*")) if match.is_dir() else [match])
    return files


def test_the_scan_covers_every_entry() -> None:
    """A moved or renamed path must not silently drop out of the scan."""
    missing = [entry for entry in SCANNED if not list(REPO_ROOT.glob(entry))]
    assert missing == []


def test_no_product_names_in_factory_fixtures_docs_or_code() -> None:
    found = token_lines(_scanned_files())
    grown = {rel: n for rel, n in found.items() if n > TOKENS_LEFT.get(rel, 0)}
    assert grown == {}, (
        f"product names in factory files: {grown}; use sample-org/sample-app, "
        "example.com and people marked (sample), or read the value from the definition"
    )


def test_the_tokens_left_only_shrink() -> None:
    """A file cleaned of product text leaves :data:`TOKENS_LEFT`, so it cannot come back."""
    found = token_lines(_scanned_files())
    stale = {rel: n for rel, n in TOKENS_LEFT.items() if found.get(rel, 0) < n}
    assert stale == {}, f"lower these counts in TOKENS_LEFT: {stale} (now {found})"
