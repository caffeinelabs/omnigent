"""Routes for per-user LLM cost reporting and aggregated usage analytics.

Exposes two endpoints (both mounted under ``/v1``):

* ``GET /usage`` — the per-user LLM cost report backing ``omni usage``: a
  daily-rollup cost summary (today / 7d / 30d / all-time) plus per-session
  detail for the calling user.
* ``GET /usage/summary`` — a rollup of token usage and USD spend across a
  caller's conversations (or, for an admin, any user's or all users'),
  broken down by provider, harness, or model over a time window.

The ``/usage/summary`` aggregation sums each conversation's OWN per-node
``session_usage`` blob (see
:class:`omnigent.stores.conversation_store.UsageRecord`), NOT the
subtree-summed value from
:func:`omnigent.runtime.policies.builder.load_session_usage` — the latter
folds a sub-agent's spend into every ancestor's roll-up, so summing it
across the conversation set would double-count. Every conversation kind
(``"default"`` and ``"sub_agent"``) is included because each row's blob is
its own disjoint spend.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Request

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runtime.policies.builder import load_session_usage
from omnigent.server.auth import RESERVED_USER_LOCAL, AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_user
from omnigent.server.schemas import (
    SessionUsage,
    UsageBucketEntry,
    UsageGroupEntry,
    UsageReport,
    UsageSummaryResponse,
)
from omnigent.stores import ConversationStore
from omnigent.stores.conversation_store import UsageRecord
from omnigent.stores.permission_store import PermissionStore

# The daily rollup floor for an "all-time" sum: earlier than any real row, so
# ``sum_daily_cost`` with this lower bound totals every recorded day.
_EPOCH_DAY = "0000-00-00"

# Sentinel ``user`` query value meaning "every user's spend" (admin only).
_USER_ALL = "all"

# Window lengths in seconds, keyed by the ``period`` query param.
_DAY_SECONDS = 86_400
_PERIOD_SECONDS: dict[str, int] = {
    "today": _DAY_SECONDS,
    "7days": 7 * _DAY_SECONDS,
    "30days": 30 * _DAY_SECONDS,
}

# Token/cost keys summed out of a conversation's OWN ``session_usage`` (and
# out of each ``by_model`` bucket). Restricted to the fields the endpoint
# reports so an unexpected persisted key can't leak into a total.
_INPUT_KEY = "input_tokens"
_OUTPUT_KEY = "output_tokens"
_COST_KEY = "total_cost_usd"


def _utc_today() -> str:
    """Return the current UTC calendar day as ``"YYYY-MM-DD"``."""
    from omnigent.db.utils import now_epoch

    return datetime.fromtimestamp(now_epoch(), tz=timezone.utc).date().isoformat()


def _day_offset(day_utc: str, *, days: int) -> str:
    """Return the UTC day *days* before *day_utc*, as ``"YYYY-MM-DD"``."""
    base = datetime.strptime(day_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (base - timedelta(days=days)).date().isoformat()


def _session_models(usage: dict[str, Any]) -> dict[str, float]:
    """
    Project a session's ``by_model`` map into a ``{model_id: cost_usd}`` dict.

    Mirrors the web session sidebar's per-model list: each model's recorded
    cost, keyed by the raw harness model id, shown faithfully. Models with no
    recorded cost are omitted. NOT guaranteed to sum to the session's
    ``total_cost_usd`` — see :class:`SessionUsage`.

    :param usage: A subtree-summed ``session_usage`` dict.
    :returns: Per-model cost map (empty when no per-model cost was recorded).
    """
    by_model = usage.get("by_model")
    if not isinstance(by_model, dict):
        return {}
    models: dict[str, float] = {}
    for name, bucket in by_model.items():
        if not isinstance(bucket, dict) or "total_cost_usd" not in bucket:
            continue
        try:
            models[str(name)] = float(bucket["total_cost_usd"])
        except (TypeError, ValueError):
            continue
    return models


def _session_cost(usage: dict[str, Any]) -> float:
    """
    Read a session's authoritative cumulative cost, or ``0.0`` when unpriced.

    ``total_cost_usd`` is present only on priced sessions (the "priced ⟺ key
    present" contract); an absent or malformed value reads as ``0.0``.
    """
    if "total_cost_usd" not in usage:
        return 0.0
    try:
        return float(usage["total_cost_usd"])
    except (TypeError, ValueError):
        return 0.0


def _build_usage_report(
    conversation_store: ConversationStore,
    user_id: str | None,
) -> UsageReport:
    """
    Build the usage report: a daily-rollup cost summary plus session detail.

    The summary (today / last 7 days / last 30 days / all-time) is summed
    from the per-user daily-cost rollup (``user_daily_cost``), which
    attributes spend to the UTC day it occurred on — so the windows reflect
    when spend actually happened, not merely a session's last-activity time.

    The per-session detail is a separate view over each top-level session's
    cumulative ``session_usage`` (rolled up across its sub-agent subtree via
    :func:`load_session_usage`), newest activity first, carrying the
    authoritative session cost and the per-model breakdown.

    :param conversation_store: Store to read the rollup and sessions from.
    :param user_id: The caller / ACL scope. ``None`` in single-user mode maps
        to the reserved local owner the daily rollup and grants are keyed by.
    :returns: The populated :class:`UsageReport`.
    """
    # The daily rollup and session-permission grants key spend by the resolved
    # owner, which is the reserved local sentinel in single-user mode (where
    # require_user yields None). Map None -> "local" so the summary reads the
    # same rows the write path recorded.
    rollup_user = user_id if user_id is not None else RESERVED_USER_LOCAL

    today = _utc_today()
    cost_today = conversation_store.sum_daily_cost(rollup_user, today)
    cost_7d = conversation_store.sum_daily_cost(rollup_user, _day_offset(today, days=6))
    cost_30d = conversation_store.sum_daily_cost(rollup_user, _day_offset(today, days=29))
    total = conversation_store.sum_daily_cost(rollup_user, _EPOCH_DAY)

    sessions: list[SessionUsage] = []
    after: str | None = None
    while True:
        page = conversation_store.list_conversations(
            limit=200,
            after=after,
            accessible_by=user_id,
            has_agent_id=True,
            kind="default",
            order="desc",
            sort_by="updated_at",
        )
        for conv in page.data:
            if conv.agent_id is None:
                continue
            usage = load_session_usage(conv.id, conversation_store)
            sessions.append(
                SessionUsage(
                    id=conv.id,
                    created_at=conv.created_at,
                    updated_at=conv.updated_at,
                    title=conv.title,
                    cost_usd=_session_cost(usage),
                    models=_session_models(usage),
                )
            )
        if not page.has_more:
            break
        after = page.last_id

    return UsageReport(
        cost_today=cost_today,
        cost_last_7d=cost_7d,
        cost_last_30d=cost_30d,
        total_cost_usd=total,
        sessions=sessions,
    )


def _provider_from_model(model_id: str) -> str:
    """Derive a provider token from a raw model id.

    Follows the endpoint contract: a ``vendor/model`` id yields its prefix
    (``"z-ai/glm-5.2"`` -> ``"z-ai"``); an id with no ``/`` falls back to a
    known vendor family (``"anthropic"`` / ``"openai"``) inferred from the
    id text, else ``"unknown"``.

    :param model_id: Raw harness-reported model id, e.g. ``"z-ai/glm-5.2"``
        or ``"claude-opus-4-8"``.
    :returns: The provider token, e.g. ``"z-ai"``, ``"anthropic"``,
        ``"openai"``, or ``"unknown"``.
    """
    if "/" in model_id:
        prefix = model_id.split("/", 1)[0].strip()
        return prefix or "unknown"
    from omnigent.model_catalog import model_family_token

    family = model_family_token(model_id)
    if family == "claude":
        return "anthropic"
    if family == "openai":
        return "openai"
    return "unknown"


class _HarnessResolver:
    """Resolve (and memoize) the harness for a conversation.

    A conversation's per-session ``harness_override`` wins; otherwise the
    harness is read from the bound agent's spec via the agent cache. Spec
    loads are keyed by ``agent_id`` and cached for the life of one request
    so a window with many sessions on the same agent pays the load once.
    Mirrors :func:`omnigent.server.routes.sessions._resolve_harness` but
    trimmed to what the rollup needs (no sub-agent head special-casing —
    the record already carries any per-session override).
    """

    def __init__(self) -> None:
        """Initialize an empty per-request harness cache."""
        self._by_agent: dict[str, str] = {}

    def resolve(self, record: UsageRecord) -> str:
        """Return the harness group key for one usage record.

        :param record: The conversation's usage record.
        :returns: The canonical harness id, e.g. ``"claude-sdk"``, or
            ``"unknown"`` when it cannot be determined.
        """
        if record.harness_override:
            return record.harness_override
        agent_id = record.agent_id
        if agent_id is None:
            return "unknown"
        cached = self._by_agent.get(agent_id)
        if cached is not None:
            return cached
        resolved = self._load_agent_harness(agent_id)
        self._by_agent[agent_id] = resolved
        return resolved

    @staticmethod
    def _load_agent_harness(agent_id: str) -> str:
        """Load an agent's declared harness from its spec.

        :param agent_id: The bound agent id, e.g. ``"ag_abc123"``.
        :returns: The canonical harness id, or ``"unknown"`` when the agent
            row / spec / harness cannot be resolved.
        """
        try:
            from omnigent.harness_aliases import canonicalize_harness
            from omnigent.model_catalog import spec_harness
            from omnigent.runtime import get_agent_cache
            from omnigent.runtime._globals import _agent_store

            if _agent_store is None:
                return "unknown"
            agent = _agent_store.get(agent_id)
            if agent is None:
                return "unknown"
            loaded = get_agent_cache().load(
                agent.id, agent.bundle_location, expand_env=agent.session_id is None
            )
            harness = spec_harness(loaded.spec)
            if harness is None:
                return "unknown"
            return canonicalize_harness(harness) or harness
        except (KeyError, AttributeError, ValueError, ImportError, OSError):
            return "unknown"


def _record_totals(usage: dict[str, object]) -> tuple[int, int, float]:
    """Extract the flat (input, output, cost) totals from a usage blob.

    :param usage: A conversation's OWN ``session_usage`` dict.
    :returns: ``(input_tokens, output_tokens, total_cost_usd)``; missing or
        non-numeric fields count as zero.
    """
    return (
        _as_int(usage.get(_INPUT_KEY)),
        _as_int(usage.get(_OUTPUT_KEY)),
        _as_float(usage.get(_COST_KEY)),
    )


def _as_int(value: object) -> int:
    """Coerce a persisted numeric to ``int``, treating junk / ``bool`` as 0.

    :param value: A value read from a JSON usage blob.
    :returns: The integer value, or ``0`` when absent / non-numeric /
        boolean (``bool`` is an ``int`` subclass, excluded so a stray flag
        isn't summed as ``1``).
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _as_float(value: object) -> float:
    """Coerce a persisted numeric to ``float``, treating junk / ``bool`` as 0.

    :param value: A value read from a JSON usage blob.
    :returns: The float value, or ``0.0`` when absent / non-numeric /
        boolean.
    """
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _bucket_start(created_at: int, by_hour: bool) -> int:
    """Snap a timestamp to the start of its day (or hour) bucket.

    :param created_at: The conversation's ``created_at`` epoch seconds.
    :param by_hour: ``True`` to bucket by hour (``period="today"``), else
        by UTC day.
    :returns: Unix epoch seconds of the bucket start.
    """
    width = 3_600 if by_hour else _DAY_SECONDS
    return created_at - (created_at % width)


def _aggregate(
    records: list[UsageRecord],
    *,
    group_by: str,
    by_hour: bool,
    harness_resolver: _HarnessResolver,
) -> tuple[int, int, float, int, list[UsageGroupEntry], list[UsageBucketEntry]]:
    """Roll usage records up into totals, per-group rows, and a time series.

    Cost/token grouping keys come from the requested dimension:

    * ``provider`` / ``model`` — walk the record's ``by_model`` map so a
      session that ran multiple models splits correctly; the model id is the
      group key (``model``) or its derived provider (``provider``). A record
      with no ``by_model`` breakdown contributes its flat totals to an
      ``"unknown"`` group for these dimensions.
    * ``harness`` — one group per resolved harness; the record's flat totals
      apply wholesale (harness is a per-conversation property).

    The window totals and the time-series buckets always come from each
    record's flat totals, so they are identical across ``group_by`` values.

    :param records: The per-conversation OWN usage records in the window.
    :param group_by: The grouping dimension, ``"provider"`` / ``"harness"``
        / ``"model"``.
    :param by_hour: ``True`` to bucket the time series by hour, else by day.
    :param harness_resolver: Per-request harness resolver / cache.
    :returns: ``(total_input, total_output, total_cost, total_sessions, groups, buckets)``.
    """
    total_input = 0
    total_output = 0
    total_cost = 0.0
    total_sessions = 0
    group_in: dict[str, int] = {}
    group_out: dict[str, int] = {}
    group_cost: dict[str, float] = {}
    group_sessions: dict[str, int] = {}
    bucket_in: dict[int, int] = {}
    bucket_out: dict[int, int] = {}
    bucket_cost: dict[int, float] = {}

    for record in records:
        usage = record.session_usage
        r_in, r_out, r_cost = _record_totals(usage)
        # Window totals + time series: always from the flat per-node totals.
        total_input += r_in
        total_output += r_out
        total_cost += r_cost
        total_sessions += 1
        bucket = _bucket_start(record.created_at, by_hour)
        bucket_in[bucket] = bucket_in.get(bucket, 0) + r_in
        bucket_out[bucket] = bucket_out.get(bucket, 0) + r_out
        bucket_cost[bucket] = bucket_cost.get(bucket, 0.0) + r_cost

        if group_by == "harness":
            key = harness_resolver.resolve(record)
            group_in[key] = group_in.get(key, 0) + r_in
            group_out[key] = group_out.get(key, 0) + r_out
            group_cost[key] = group_cost.get(key, 0.0) + r_cost
            group_sessions[key] = group_sessions.get(key, 0) + 1
            continue

        # provider / model: split via the per-model breakdown so a session
        # that ran multiple models is attributed correctly.
        by_model = usage.get("by_model")
        if not isinstance(by_model, dict) or not by_model:
            # No per-model breakdown recorded — attribute the flat totals to
            # an "unknown" group rather than dropping this record's spend.
            key = "unknown"
            group_in[key] = group_in.get(key, 0) + r_in
            group_out[key] = group_out.get(key, 0) + r_out
            group_cost[key] = group_cost.get(key, 0.0) + r_cost
            group_sessions[key] = group_sessions.get(key, 0) + 1
            continue
        for model_id, model_bucket in by_model.items():
            if not isinstance(model_bucket, dict):
                continue
            key = model_id if group_by == "model" else _provider_from_model(model_id)
            group_in[key] = group_in.get(key, 0) + _as_int(model_bucket.get(_INPUT_KEY))
            group_out[key] = group_out.get(key, 0) + _as_int(model_bucket.get(_OUTPUT_KEY))
            group_cost[key] = group_cost.get(key, 0.0) + _as_float(model_bucket.get(_COST_KEY))
            group_sessions[key] = group_sessions.get(key, 0) + 1

    groups = [
        UsageGroupEntry(
            group_key=key,
            input_tokens=group_in.get(key, 0),
            output_tokens=group_out.get(key, 0),
            total_cost_usd=group_cost.get(key, 0.0),
            session_count=group_sessions.get(key, 0),
        )
        for key in group_cost
    ]
    # Highest spend first; group_key breaks ties for a stable order.
    groups.sort(key=lambda g: (-g.total_cost_usd, g.group_key))

    buckets = [
        UsageBucketEntry(
            bucket_start=start,
            input_tokens=bucket_in.get(start, 0),
            output_tokens=bucket_out.get(start, 0),
            total_cost_usd=bucket_cost.get(start, 0.0),
        )
        for start in sorted(bucket_cost)
    ]
    return total_input, total_output, total_cost, total_sessions, groups, buckets


def create_usage_router(
    conversation_store: ConversationStore,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Create the usage router (per-user cost report + aggregated analytics).

    Registers two routes, both mounted under ``/v1`` by the app:

    * ``GET /usage`` — the calling user's cost report.
    * ``GET /usage/summary`` — aggregated usage + cost analytics.

    Both are user-scoped rather than session-scoped, so they live in their
    own router rather than under the sessions router.

    :param conversation_store: Store the reports and usage records read from.
    :param auth_provider: Auth provider for user identity. ``None`` disables
        auth (single-user / local mode).
    :param permission_store: Permission store used by ``/usage/summary`` both
        to attribute a session to its owner and to check the admin flag.
        ``None`` disables per-user scoping (single-user mode) — the ``user``
        param is then rejected because there is no notion of another user.
    :returns: The configured router (mounted under ``/v1``).
    """
    router = APIRouter()

    @router.get("/usage", response_model=UsageReport)
    async def get_usage(request: Request) -> UsageReport:
        """
        Aggregate the calling user's LLM spend across their sessions.

        require_user, not get_user_id: the aggregation scopes to the caller,
        so a request slipping through as ``None`` in multi-user mode would
        read another scope. Fail closed with 401 instead (``user_id`` is
        ``None`` only when auth is disabled — the single-user / local case).
        """
        user_id = require_user(request, auth_provider)
        return await asyncio.to_thread(_build_usage_report, conversation_store, user_id)

    @router.get("/usage/summary")
    async def usage_summary(
        request: Request,
        period: Literal["today", "7days", "30days"] = "7days",
        group_by: Literal["provider", "harness", "model"] = "model",
        user: str | None = None,
    ) -> UsageSummaryResponse:
        """Aggregate LLM usage + cost over a time window.

        The default scope is the caller's own conversations. Passing
        ``user`` (a specific id, or the ``"all"`` sentinel for every user)
        is honored only for admins; a non-admin caller who passes it gets
        403. In single-user mode (no ``permission_store``) the ``user``
        param is rejected as unsupported.

        :param request: The incoming request, used to extract the caller.
        :param period: Window length, ``"today"`` | ``"7days"`` |
            ``"30days"`` (default ``"7days"``). ``"today"`` buckets the
            time series by hour; the others by day.
        :param group_by: Rollup dimension, ``"provider"`` | ``"harness"`` |
            ``"model"`` (default ``"model"``).
        :param user: Optional target user (or ``"all"``). Admin-only.
        :returns: The aggregated :class:`UsageSummaryResponse`.
        :raises OmnigentError: 403 when a non-admin requests another user's
            spend; 400 when ``user`` is passed in single-user mode.
        """
        from omnigent.db.utils import now_epoch

        caller = get_user_id(request, auth_provider)
        is_admin = await _is_admin(caller, permission_store)

        # Resolve the owner scope. Default = the caller's own sessions.
        owner_scope: str | None
        if user is None:
            owner_scope = caller
        else:
            if permission_store is None:
                raise OmnigentError(
                    "The 'user' parameter requires multi-user mode.",
                    code=ErrorCode.INVALID_INPUT,
                )
            if not is_admin:
                raise OmnigentError(
                    "Only admins may query another user's usage.",
                    code=ErrorCode.FORBIDDEN,
                )
            # Admin: "all" means every user (no owner filter); otherwise the
            # named user's owned sessions.
            owner_scope = None if user == _USER_ALL else user

        window_seconds = _PERIOD_SECONDS[period]
        start_epoch = now_epoch() - window_seconds
        by_hour = period == "today"

        records = await asyncio.to_thread(
            conversation_store.list_usage_records,
            start_epoch=start_epoch,
            end_epoch=None,
            owner_user_id=owner_scope,
        )

        harness_resolver = _HarnessResolver()
        total_input, total_output, total_cost, total_sessions, groups, buckets = _aggregate(
            records,
            group_by=group_by,
            by_hour=by_hour,
            harness_resolver=harness_resolver,
        )

        # ``user`` reported on the response is the scope actually applied:
        # the named/target user, or None for the all-users / caller-None view.
        reported_user = owner_scope if user != _USER_ALL else None
        return UsageSummaryResponse(
            period=period,
            group_by=group_by,
            start_epoch=start_epoch,
            user=reported_user,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_cost_usd=total_cost,
            total_sessions=total_sessions,
            groups=groups,
            buckets=buckets,
        )

    return router


async def _is_admin(
    user_id: str | None,
    permission_store: PermissionStore | None,
) -> bool:
    """Return whether the caller holds the admin flag.

    Mirrors the admin check the session routes use
    (``permission_store.is_admin`` via :func:`asyncio.to_thread` to keep the
    event loop unblocked). The file-backed admin-list promotion is not
    consulted here — like the session list route, this relies on the DB
    ``users.is_admin`` flag the login path maintains.

    :param user_id: The caller's id, or ``None`` (unauthenticated /
        single-user).
    :param permission_store: Permission store, or ``None`` to skip.
    :returns: ``True`` when the caller is a known admin, else ``False``.
    """
    if user_id is None or permission_store is None:
        return False
    return await asyncio.to_thread(permission_store.is_admin, user_id)
