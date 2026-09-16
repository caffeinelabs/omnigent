"""Tests for the Databricks AI-gateway model discovery that feeds the picker."""

from __future__ import annotations

import httpx
import pytest

from omnigent.harnesses.claude_native import main as m


def _cfg(base_url: str, helper: str | None = "printf tok") -> object:
    return m.ClaudeNativeUcodeConfig(
        env={"ANTHROPIC_BASE_URL": base_url, "CLAUDE_CODE_USE_GATEWAY": "1"},
        api_key_helper=helper,
    )


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    orig = httpx.AsyncClient

    def fake(*_args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return orig(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(m.httpx, "AsyncClient", fake)


async def test_gateway_rows_strip_prefix_and_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-Claude ids lose the ``anthropic-aigw-<hash>-`` prefix; duplicates drop."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "system.ai.claude-opus-4-8", "display_name": "Claude Opus 4.8"},
                    {"id": "anthropic-aigw-ba377e31-system.ai.kimi-k3", "display_name": "Kimi K3"},
                    {
                        "id": "anthropic-aigw-77df06ea-system.ai.glm-5-3-flash",
                        "display_name": "GLM 5.3 Flash",
                    },
                    # A second display id that strips to an already-seen model.
                    {"id": "anthropic-aigw-000000aa-system.ai.kimi-k3", "display_name": "dupe"},
                    {"id": "", "display_name": "blank"},
                ]
            },
        )

    _mock_client(monkeypatch, handler)
    rows = await m._databricks_gateway_model_rows(_cfg("https://ws.example.com/ai-gateway/anthropic"))
    assert [r["model"] for r in rows] == [
        "system.ai.claude-opus-4-8",
        "system.ai.kimi-k3",
        "system.ai.glm-5-3-flash",
    ]
    labels = {r["model"]: r["displayName"] for r in rows}
    assert labels["system.ai.kimi-k3"] == "Kimi K3"
    assert labels["system.ai.glm-5-3-flash"] == "GLM 5.3 Flash"


async def test_gateway_rows_skips_anthropic_and_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """anthropic.com endpoints, a None config, and a missing base URL yield no rows."""

    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("must not query anthropic.com or a config-less launch")

    _mock_client(monkeypatch, handler)
    assert await m._databricks_gateway_model_rows(None) == []
    assert await m._databricks_gateway_model_rows(_cfg("https://api.anthropic.com")) == []
    assert await m._databricks_gateway_model_rows(_cfg("")) == []


async def test_gateway_rows_best_effort_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gateway error degrades to no rows rather than breaking the picker."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    _mock_client(monkeypatch, handler)
    rows = await m._databricks_gateway_model_rows(_cfg("https://ws.example.com/ai-gateway/anthropic"))
    assert rows == []
