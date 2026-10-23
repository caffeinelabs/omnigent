"""The shared factory vocabulary (labels, roles, spec role lookup)."""

from types import SimpleNamespace

from omnigent import factory


def _spec(config: object) -> SimpleNamespace:
    return SimpleNamespace(executor=SimpleNamespace(config=config))


def test_label_keys_share_prefix() -> None:
    keys = [v for k, v in vars(factory).items() if k.startswith("LABEL_") and k != "LABEL_PREFIX"]
    keys = [k for k in keys if isinstance(k, str) and k.startswith("omnigent.")]
    assert keys and all(k.startswith(factory.LABEL_PREFIX) for k in keys)


def test_factory_role_from_spec() -> None:
    assert factory.factory_role_from_spec(_spec({"factory_role": "project_lead"})) == "project_lead"
    assert factory.factory_role_from_spec(_spec({"factory_role": "wizard"})) is None
    assert factory.factory_role_from_spec(_spec({})) is None
    assert factory.factory_role_from_spec(_spec(None)) is None
    assert factory.factory_role_from_spec(None) is None


def test_heartbeat_bounds_and_clamp() -> None:
    assert factory.valid_heartbeat_s(0)
    assert factory.valid_heartbeat_s(600)
    assert not factory.valid_heartbeat_s(30)
    assert not factory.valid_heartbeat_s(10**6)
    assert len(factory.clamp_label_value("x" * 400)) == factory.LABEL_VALUE_MAX_LEN
    assert factory.clamp_label_value("short") == "short"
