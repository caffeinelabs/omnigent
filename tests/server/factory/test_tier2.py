"""The Tier-2 gate probe: review, CODEOWNERS review and CI on the base, a non-admin identity."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from omnigent.server.factory import tier2

REVIEW = {
    "type": "pull_request",
    "parameters": {"required_approving_review_count": 1, "require_code_owner_review": True},
}
CI = {"type": "required_status_checks", "parameters": {"required_status_checks": []}}


@pytest.fixture(autouse=True)
def _smith_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tier2.SMITH_ENV, "1")
    monkeypatch.setenv(tier2.LOGIN_ENV, "factory-smith")
    monkeypatch.setenv(tier2.TOKEN_ENV, "test-token")
    monkeypatch.delenv(tier2.REPO_ENV, raising=False)


def _github(rules: Any, role: Any = None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rules/branches/staging"):
            if isinstance(rules, str):
                return httpx.Response(200, text=rules)
            return httpx.Response(200, json=rules)
        if request.url.path.endswith("/collaborators/factory-smith/permission"):
            return httpx.Response(200, json=role or {"role_name": "write"})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handle)


async def test_review_codeowners_and_ci_with_a_plain_identity_enable_tier2() -> None:
    result = await tier2.probe(_github([REVIEW, CI]))

    assert (result["enabled"], result["reason"]) == (True, None)
    assert result["checks"]["protected"] is True


@pytest.mark.parametrize(
    ("rules", "missing"),
    [
        ([CI], "pull-request review"),
        ([{**REVIEW, "parameters": {"required_approving_review_count": 1}}, CI], "CODEOWNERS"),
        ([REVIEW], "status checks"),
        ("<html>rate limited</html>", "pull-request review"),
    ],
)
async def test_a_partly_protected_base_keeps_tier2_off(rules: Any, missing: str) -> None:
    result = await tier2.probe(_github(rules))

    assert result["enabled"] is False and result["checks"]["protected"] is False
    assert missing in result["reason"]


async def test_an_admin_identity_keeps_tier2_off() -> None:
    result = await tier2.probe(_github([REVIEW, CI], role={"role_name": "admin"}))

    assert result["enabled"] is False and "admin" in result["reason"]
