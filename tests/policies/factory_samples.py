"""(sample) The repo configuration the factory guard tests run under.

The guard builds in no product repo: a factory member's repos come from the
server, and the Tier-0 always-deny list from server config read at boot
(:data:`omnigent.policies.builtins.factory.ALWAYS_DENY_ENV`). These tests were
written against one deployment's values; this puts them back as sample data.
"""

from __future__ import annotations

import pytest

from omnigent.policies.builtins import factory as guard

SAMPLE_WRITE_REPOS = ("sample-org/sample-app",)
SAMPLE_DENY_REPOS = ("sample-org/sample-infra", "sample-org/sample-tool")
SAMPLE_ALWAYS_DENY_REPOS = ("sample-org/sample-infra",)


def use_sample_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the guard the (sample) defaults and always-deny list for one test."""
    monkeypatch.setattr(guard, "DEFAULT_WRITE_REPOS", SAMPLE_WRITE_REPOS)
    monkeypatch.setattr(guard, "DEFAULT_DENY_REPOS", SAMPLE_DENY_REPOS)
    monkeypatch.setattr(guard, "_ALWAYS_DENY_REPOS", SAMPLE_ALWAYS_DENY_REPOS)
