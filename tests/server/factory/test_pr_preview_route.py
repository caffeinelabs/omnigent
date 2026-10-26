"""``GET /v1/factory/prs/{pr_id}/preview``: the stable factory-preview link."""

from __future__ import annotations

from typing import Any

import pytest

from omnigent.server.factory.api.pr_preview import preview_redirect_target, preview_stable_path
from tests.server.factory.conftest import FactoryApi, FactoryWorld

REPO = "caffeinelabs/app"
APP = "https://app-b693ef0a-2c6683-a38d-p9630.envs.sandbox.caffeinelabs.io"


def _pr(world: FactoryWorld, preview: dict[str, Any] | None) -> Any:
    # The app's deps (``authed_api`` registers its own), not the fixture's.
    store = world.app_state.factory.factory_store
    row, _ = store.upsert_pr(
        world.id("lead_a"), REPO, 9630, project_slug="api-version", preview=preview
    )
    return row


async def _get(api: FactoryApi, pr_id: str, user: str = "alice") -> Any:
    return await api.client.get(
        preview_stable_path(pr_id), headers={"X-Test-User": user}, follow_redirects=False
    )


@pytest.mark.parametrize("state", ["ready", "stale"])
async def test_live_preview_redirects_to_its_app(
    authed_api: FactoryApi, world: FactoryWorld, state: str
) -> None:
    row = _pr(world, {"name": "p9630", "state": state, "urls": {"app": f"{APP}/some/page"}})
    resp = await _get(authed_api, row.id)
    assert resp.status_code == 302
    assert resp.headers["location"] == f"{APP}/"
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "preview",
    [
        None,
        {"name": "p9630", "state": "gone", "urls": {"app": APP}},
        {"name": "p9630", "state": "parked", "urls": {"app": APP}},
        {"name": "p9630", "state": "deploying", "urls": {"app": APP}},
        {"name": "p9630", "state": "ready", "urls": {"app": "https://evil.example.com/"}},
        {
            "name": "p9630",
            "state": "ready",
            "urls": {"app": "http://app-x.envs.sandbox.caffeinelabs.io"},
        },
        {"name": "p9630", "state": "ready", "urls": {}},
    ],
    ids=["none", "gone", "parked", "deploying", "foreign-host", "plain-http", "no-url"],
)
async def test_unavailable_preview_redirects_to_the_cockpit(
    authed_api: FactoryApi, world: FactoryWorld, preview: dict[str, Any] | None
) -> None:
    row = _pr(world, preview)
    resp = await _get(authed_api, row.id)
    assert resp.status_code == 302
    assert resp.headers["location"] == (
        f"/factory/{world.id('lead_a')}?review=9630&pr={row.id}&preview=unavailable"
    )


async def test_readers_only(authed_api: FactoryApi, world: FactoryWorld) -> None:
    row = _pr(world, {"name": "p9630", "state": "ready", "urls": {"app": APP}})
    assert (await _get(authed_api, row.id, user="bob")).status_code == 404
    resp = await authed_api.client.get(preview_stable_path(row.id), follow_redirects=False)
    assert resp.status_code == 401


@pytest.mark.parametrize("pr_id", ["not-a-uuid", "0" * 32])
async def test_unknown_pr_is_404(authed_api: FactoryApi, pr_id: str) -> None:
    assert (await _get(authed_api, pr_id)).status_code == 404


def test_redirect_target_is_pure(world: FactoryWorld) -> None:
    row = _pr(world, {"name": "p9630", "state": "stale", "urls": {"app": APP}})
    assert preview_redirect_target(row) == f"{APP}/"
