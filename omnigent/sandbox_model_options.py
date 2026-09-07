"""Per-harness Databricks model options for the pre-sandbox composer.

The New-chat composer picks a model BEFORE a managed sandbox (and therefore its
host) exists, so it cannot ask a host what a harness could launch with. This
module lets the server answer instead, from the user's brokered Databricks
credential: it enumerates the workspace's serving endpoints once and shapes them
into each harness's own picker vocabulary, so the pre-sandbox list matches what
the harness offers in-session.

Pure and credential-agnostic — it takes an already-resolved ``(workspace_host,
token)`` pair (the server mints it per user via the credential broker) so the
same discovery the launched harness performs runs here without a
``~/.databrickscfg`` profile. Every lookup is best-effort: any discovery failure
returns an empty list and the caller falls back to its static vocabulary.
"""

from __future__ import annotations

import logging

import httpx

from omnigent.databricks_model_discovery import (
    discover_databricks_claude_catalog,
    discover_databricks_codex_models,
    humanize_model,
)
from omnigent.harness_aliases import canonicalize_harness
from omnigent.model_catalog import list_databricks_llm_endpoint_names
from omnigent.opencode_native_provider import DATABRICKS_GATEWAY_PROVIDER_ID

_logger = logging.getLogger(__name__)

# Claude Code's family aliases, most-capable first. These are the only ids the
# native ``/model`` command can route to (it resolves an alias to a concrete
# endpoint via the ANTHROPIC_DEFAULT_<TIER>_MODEL env pin the launch config
# writes), so the pre-sandbox Claude picker offers exactly the aliases the
# workspace can back — never the raw ``system.ai.*`` endpoints, which the CLI
# cannot select.
_CLAUDE_ALIAS_ORDER: tuple[str, ...] = ("fable", "opus", "sonnet", "haiku")
_CLAUDE_DEFAULT_ALIAS = "sonnet"

ModelRow = dict[str, object]


def resolve_sandbox_model_options(
    harness: str,
    workspace_host: str,
    token: str,
    *,
    default_model: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[ModelRow]:
    """Model-picker rows a *harness* could launch a Databricks sandbox with.

    :param harness: Native harness id, e.g. ``"opencode-native"`` (aliases are
        canonicalized).
    :param workspace_host: Databricks workspace origin.
    :param token: A currently-valid workspace bearer token for the user.
    :param default_model: The deployment's pinned gateway endpoint (the runner's
        ``OMNIGENT_DATABRICKS_GATEWAY_MODEL``), marked ``isDefault`` when present
        in the list; ``None`` marks no row default.
    :param transport: Optional httpx transport override for tests.
    :returns: ``NativeModelOption``-shaped rows (``id``/``model``/``displayName``/
        ``isDefault``, plus ``providerID`` for opencode). Empty on any discovery
        failure or for a harness with no gateway picker.
    """
    canonical = canonicalize_harness(harness) or harness
    try:
        if canonical == "opencode-native":
            return _opencode_rows(workspace_host, token, default_model, transport)
        if canonical in ("claude-native", "claude-sdk"):
            return _claude_rows(workspace_host, token, transport)
        if canonical == "codex-native":
            return _codex_rows(workspace_host, token, default_model, transport)
        if canonical == "pi-native":
            return _pi_rows(workspace_host, token, transport)
    except (httpx.HTTPError, ValueError, OSError) as exc:
        _logger.info("sandbox model options for %r failed: %r", canonical, exc)
        return []
    return []


def _opencode_rows(
    host: str,
    token: str,
    default_model: str | None,
    transport: httpx.BaseTransport | None,
) -> list[ModelRow]:
    """Every workspace endpoint, keyed by the bare ``databricks-<name>`` id.

    OpenCode routes through the OpenAI-compatible invocations surface, so every
    served chat endpoint is selectable (unlike Claude Code, capped at its family
    aliases).

    The row ``id`` is the composer's create-time ``model_override``, which the
    launch resolves through ``resolve_databricks_gateway`` — that path expects the
    **bare** endpoint id and re-qualifies it to ``databricks-gateway/<name>``
    itself. Emitting the already-qualified id here would double-prefix and break
    routing, so the id stays bare (``providerID`` names the gateway for display).
    """
    names = list_databricks_llm_endpoint_names(host, token, transport=transport)
    rows: list[ModelRow] = []
    for name in sorted(names):
        base = name[len("databricks-") :] if name.startswith("databricks-") else name
        rows.append(
            {
                "id": name,
                "model": name,
                "providerID": DATABRICKS_GATEWAY_PROVIDER_ID,
                "displayName": humanize_model(base),
                "isDefault": name == default_model,
            }
        )
    return rows


def _claude_rows(
    host: str,
    token: str,
    transport: httpx.BaseTransport | None,
) -> list[ModelRow]:
    """The Claude Code family aliases the workspace can back, resolved live.

    Discovery decides which aliases to offer (only families the workspace serves)
    and supplies each alias's concrete endpoint for the display name, so the
    picker never lists an alias with no Databricks Claude model behind it.
    """
    catalog = discover_databricks_claude_catalog(host, token, transport=transport)
    families = catalog.families
    rows: list[ModelRow] = []
    for alias in _CLAUDE_ALIAS_ORDER:
        concrete = families.get(alias)
        if not concrete:
            continue
        base = concrete[len("system.ai.") :] if concrete.startswith("system.ai.") else concrete
        rows.append(
            {
                "id": alias,
                "model": concrete,
                "displayName": humanize_model(base),
                "isDefault": alias == _CLAUDE_DEFAULT_ALIAS,
            }
        )
    return rows


def _codex_rows(
    host: str,
    token: str,
    default_model: str | None,
    transport: httpx.BaseTransport | None,
) -> list[ModelRow]:
    """The codex-servable ``system.ai.gpt-*`` endpoints the workspace exposes.

    Codex routes the Responses API through the gateway, so only the
    codex-compatible endpoints (GPT/codex families) are offered, best default
    first as ``discover_databricks_codex_models`` orders them.
    """
    model_ids = discover_databricks_codex_models(host, token, transport=transport)
    rows: list[ModelRow] = []
    for model_id in model_ids:
        base = model_id[len("system.ai.") :] if model_id.startswith("system.ai.") else model_id
        rows.append(
            {
                "id": model_id,
                "model": model_id,
                "displayName": humanize_model(base),
                "isDefault": model_id == default_model,
            }
        )
    return rows


def _pi_rows(
    host: str,
    token: str,
    transport: httpx.BaseTransport | None,
) -> list[ModelRow]:
    """The Claude endpoints Pi routes through the gateway's anthropic surface.

    Pi routes a chosen model to whichever declared gateway family matches it, and
    only the anthropic surface (the same ``/ai-gateway/anthropic`` claude-native
    uses) answers Pi's request shape. The OpenAI Responses surface returns 501 to
    Pi's generic openai-responses client — Codex's native protocol works there,
    Pi's does not — so GPT endpoints are deliberately omitted rather than listed
    as unroutable. Claude family tiers only.
    """
    catalog = discover_databricks_claude_catalog(host, token, transport=transport)
    rows: list[ModelRow] = []
    seen: set[str] = set()
    for model_id in catalog.families.values():
        if model_id in seen:
            continue
        seen.add(model_id)
        base = model_id[len("system.ai.") :] if model_id.startswith("system.ai.") else model_id
        rows.append(
            {
                "id": model_id,
                "model": model_id,
                "displayName": humanize_model(base),
                "isDefault": False,
            }
        )
    return rows
