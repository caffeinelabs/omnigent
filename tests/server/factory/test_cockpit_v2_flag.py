"""The new cockpit is dark by default: preflight says whether people may switch to it."""

from __future__ import annotations

import pytest

from omnigent import factory as F
from tests.server.factory.conftest import FactoryApi, FactoryWorld


@pytest.mark.parametrize(("value", "on"), [(None, False), ("0", False), ("1", True)])
async def test_preflight_reports_the_cockpit_v2_flag(
    world: FactoryWorld,
    authed_api: FactoryApi,
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    on: bool,
) -> None:
    if value is None:
        monkeypatch.delenv(F.COCKPIT_V2_ENV, raising=False)
    else:
        monkeypatch.setenv(F.COCKPIT_V2_ENV, value)

    resp = await authed_api.client.get("/v1/factory/preflight", headers={"X-Test-User": "alice"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["cockpit_v2"] is on
    assert F.cockpit_v2_enabled() is on
