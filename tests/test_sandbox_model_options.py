"""Per-harness pre-sandbox Databricks model-option resolution."""

from __future__ import annotations

import httpx

from omnigent.sandbox_model_options import resolve_sandbox_model_options

_HOST = "https://example.cloud.databricks.com"
_TOKEN = "dapi-test"

# Serving-endpoints listing (opencode's any-model source): two chat LLMs, one
# embedding endpoint (dropped), one not-READY endpoint (dropped).
_SERVING_ENDPOINTS = {
    "endpoints": [
        {"name": "databricks-claude-sonnet-4-6", "task": "llm/v1/chat"},
        {"name": "databricks-glm-5-3-flash", "task": "llm/v1/chat"},
        {"name": "databricks-gte-large-en", "task": "llm/v1/embeddings"},
        {
            "name": "databricks-kimi-k3",
            "task": "llm/v1/chat",
            "state": {"ready": "NOT_READY"},
        },
    ]
}

# Unity Catalog model-services (claude family picks + codex ids).
_MODEL_SERVICES = {
    "model_services": [
        {"name": "model-services/system.ai.claude-opus-5"},
        {"name": "model-services/system.ai.claude-sonnet-5"},
        {"name": "model-services/system.ai.claude-haiku-4-5"},
        {"name": "model-services/system.ai.gpt-5-5"},
    ]
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/2.0/serving-endpoints":
        return httpx.Response(200, json=_SERVING_ENDPOINTS, request=request)
    if path == "/api/2.1/unity-catalog/model-services":
        return httpx.Response(200, json=_MODEL_SERVICES, request=request)
    if path == "/ai-gateway/anthropic/v1/models":
        # The legacy anthropic-gateway listing; empty is a valid answer.
        return httpx.Response(200, json={"data": []}, request=request)
    return httpx.Response(404, request=request)


def _resolve(harness: str, **kwargs: object) -> list[dict[str, object]]:
    return resolve_sandbox_model_options(
        harness,
        _HOST,
        _TOKEN,
        transport=httpx.MockTransport(_handler),
        **kwargs,  # type: ignore[arg-type]
    )


def test_opencode_lists_every_ready_chat_endpoint_as_bare_ids() -> None:
    rows = _resolve("opencode-native", default_model="databricks-glm-5-3-flash")
    ids = [r["id"] for r in rows]
    # Both READY chat LLMs, embeddings + NOT_READY dropped. Ids are the BARE
    # endpoint names (the create-time model_override; the launch re-qualifies to
    # databricks-gateway/<name> itself — a qualified id here would double-prefix).
    assert ids == [
        "databricks-claude-sonnet-4-6",
        "databricks-glm-5-3-flash",
    ]
    by_id = {r["id"]: r for r in rows}
    glm = by_id["databricks-glm-5-3-flash"]
    assert glm["model"] == "databricks-glm-5-3-flash"
    assert glm["providerID"] == "databricks-gateway"
    assert glm["displayName"] == "GLM 5 3 Flash"
    assert glm["isDefault"] is True
    assert by_id["databricks-claude-sonnet-4-6"]["isDefault"] is False


def test_opencode_alias_is_canonicalized() -> None:
    # A user-facing alias must resolve the same as the canonical id.
    assert _resolve("opencode") == _resolve("opencode-native")


def test_claude_offers_family_aliases_backed_by_served_endpoints() -> None:
    rows = _resolve("claude-native")
    assert [r["id"] for r in rows] == ["opus", "sonnet", "haiku"]
    by_id = {r["id"]: r for r in rows}
    assert by_id["opus"]["model"] == "system.ai.claude-opus-5"
    assert by_id["opus"]["displayName"] == "Claude Opus 5"
    assert by_id["sonnet"]["isDefault"] is True
    assert by_id["opus"]["isDefault"] is False
    # Never the raw system.ai.* endpoints (Claude Code's /model can't select them).
    assert all(not str(r["id"]).startswith("system.ai.") for r in rows)


def test_codex_offers_gpt_family_system_ai_ids() -> None:
    rows = _resolve("codex-native")
    assert [r["id"] for r in rows] == ["system.ai.gpt-5-5"]
    assert rows[0]["displayName"] == "GPT 5 5"


def test_pi_offers_full_catalog_as_system_ai_ids() -> None:
    rows = _resolve("pi-native")
    ids = [r["id"] for r in rows]
    # Every READY chat endpoint, in the system.ai.* spelling both gateway surfaces
    # answer to (Claude via anthropic, the rest via the OpenAI chat surface).
    assert ids == ["system.ai.claude-sonnet-4-6", "system.ai.glm-5-3-flash"]
    by_id = {r["id"]: r for r in rows}
    assert by_id["system.ai.glm-5-3-flash"]["displayName"] == "GLM 5 3 Flash"


def test_unknown_or_unsupported_harness_is_empty() -> None:
    assert _resolve("cursor-native") == []


def test_discovery_failure_degrades_to_empty() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    rows = resolve_sandbox_model_options(
        "opencode-native", _HOST, _TOKEN, transport=httpx.MockTransport(_boom)
    )
    assert rows == []
