"""Tests for deployment-wide release feature resolution."""

from __future__ import annotations

import dataclasses

import pytest

from omnigent.factory import FACTORY_ENABLED_ENV
from omnigent.server.feature_flags import (
    FEATURE_DEFINITIONS,
    FEATURES_ENV_VAR,
    Feature,
    FeatureFlags,
    resolve_feature_flags,
)


@pytest.fixture(autouse=True)
def _factory_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FACTORY_ENABLED_ENV, raising=False)


def test_features_default_off() -> None:
    flags = resolve_feature_flags({})

    assert flags.enabled_features == frozenset()
    assert flags.frontend_dict() == {
        "usage_page": False,
        "harness_install": False,
        "canvas": False,
        "factory": False,
    }


def test_resolves_comma_separated_enabled_set() -> None:
    flags = resolve_feature_flags({FEATURES_ENV_VAR: " usage_page, harness_install,usage_page "})

    assert flags.enabled(Feature.USAGE_PAGE)
    assert flags.enabled(Feature.HARNESS_INSTALL)
    assert flags.enabled_names() == ("harness_install", "usage_page")


def test_empty_entries_are_ignored() -> None:
    assert resolve_feature_flags({FEATURES_ENV_VAR: " , , "}) == FeatureFlags()


def test_removed_harness_install_variable_fails_with_migration_hint() -> None:
    with pytest.raises(ValueError, match="OMNIGENT_FEATURES=harness_install"):
        resolve_feature_flags({"OMNIGENT_HARNESS_INSTALL_ENABLED": "1"})


def test_removed_harness_install_variable_allows_explicit_off() -> None:
    flags = resolve_feature_flags({"OMNIGENT_HARNESS_INSTALL_ENABLED": "0"})

    assert not flags.enabled(Feature.HARNESS_INSTALL)


def test_canvas_is_a_frontend_visible_feature() -> None:
    flags = resolve_feature_flags({FEATURES_ENV_VAR: "canvas"})

    assert flags.enabled(Feature.CANVAS)
    assert flags.frontend_dict()["canvas"] is True
    assert flags.enabled_names() == ("canvas",)


def test_factory_is_enabled_by_the_features_list() -> None:
    flags = resolve_feature_flags({FEATURES_ENV_VAR: "factory"})

    assert flags.enabled(Feature.FACTORY)
    assert flags.frontend_dict()["factory"] is True


def test_factory_follows_the_factory_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FACTORY_ENABLED_ENV, "1")
    flags = resolve_feature_flags({})

    # The cockpit shows wherever the factory runs, without listing it twice.
    assert flags.frontend_dict()["factory"] is True
    assert flags.frontend_dict()["canvas"] is False
    assert not flags.enabled(Feature.FACTORY)


@pytest.mark.parametrize("value", ["", "0", "true", "yes"])
def test_factory_switch_other_values_keep_the_cockpit_off(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(FACTORY_ENABLED_ENV, value)

    assert resolve_feature_flags({}).frontend_dict()["factory"] is False


def test_unknown_feature_fails_with_known_names() -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_feature_flags({FEATURES_ENV_VAR: "usage-pgae"})

    message = str(exc_info.value)
    assert "usage-pgae" in message
    assert "usage_page" in message
    assert "harness_install" in message


def test_snapshot_is_immutable_and_does_not_follow_environment_mutation() -> None:
    environ = {FEATURES_ENV_VAR: "usage_page"}
    flags = resolve_feature_flags(environ)
    environ[FEATURES_ENV_VAR] = "harness_install"

    assert flags.enabled(Feature.USAGE_PAGE)
    assert not flags.enabled(Feature.HARNESS_INSTALL)
    with pytest.raises(dataclasses.FrozenInstanceError):
        flags.enabled_features = frozenset()  # type: ignore[misc]


def test_release_flags_have_lifecycle_metadata_and_default_off() -> None:
    assert {definition.feature for definition in FEATURE_DEFINITIONS} == set(Feature)
    for definition in FEATURE_DEFINITIONS:
        assert definition.owner
        assert definition.review_by_release
        assert not FeatureFlags().enabled(definition.feature)
