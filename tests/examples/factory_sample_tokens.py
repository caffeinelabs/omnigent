"""
The product names factory text must not carry. Not a test module.

A factory learns its product (repos, domains, people, sources) from its
definition at runtime. Bundles, server code, sample fixtures and stories use
neutral samples instead: ``sample-org/sample-app``, ``example.com`` and people
such as ``ana (sample)``. :data:`FORBIDDEN_SAMPLE_TOKENS` holds the tokens a
sweep of the factory's files found; the checks in ``test_factory_sample_tokens.py``,
``test_factory_bundles.py`` and ``tests/server/factory/test_gateway_scope.py``
all read it, so it stays one list.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final

FORBIDDEN_SAMPLE_TOKENS: Final = ("caffeine", "temp-main", "internet identity")
"""Matched case-insensitively anywhere in a line (a token also covers the names built on it)."""

FORBIDDEN_SAMPLE_PATTERN: Final = re.compile(
    "|".join(re.escape(token) for token in FORBIDDEN_SAMPLE_TOKENS), re.IGNORECASE
)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]


def token_lines(paths: Iterable[Path], root: Path = REPO_ROOT) -> dict[str, int]:
    """
    :param paths: Files to read; ones that are not UTF-8 text are skipped.
    :param root: What the returned paths are relative to.
    :returns: Path (relative to *root*, POSIX) -> lines naming a forbidden token.
    """
    out: dict[str, int] = {}
    for path in paths:
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits = sum(1 for line in text.splitlines() if FORBIDDEN_SAMPLE_PATTERN.search(line))
        if hits:
            out[path.relative_to(root).as_posix()] = hits
    return out


__all__ = ["FORBIDDEN_SAMPLE_PATTERN", "FORBIDDEN_SAMPLE_TOKENS", "REPO_ROOT", "token_lines"]
