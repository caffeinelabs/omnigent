"""Fixtures for the policy tests."""

from __future__ import annotations

import pytest

from tests.policies.factory_samples import use_sample_repos


@pytest.fixture(autouse=True)
def _sample_factory_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    """(sample) The factory guard's repo configuration (see :mod:`.factory_samples`)."""
    use_sample_repos(monkeypatch)
