"""A managed sandbox host runs only the session tree it was provisioned for.

A ``host_type="managed"`` session gets its own server-provisioned sandbox (one
pod / VM per session). The sandbox's ``hosts`` row is registered under the
session creator's USER id, so every user-scoped host lookup sees it as just
another host of that user. Without this policy the scheduled fire's "owner's
live host" resolution, ``POST /v1/hosts/{id}/runners`` and a create naming
``host_id`` could launch an unrelated session's runner inside another
session's sandbox (e.g. an hourly task firing into a factory member's pod).

The policy:

* A managed host admits a session when the session's spawn-tree root (the
  session itself, or the top-level session a sub-agent descends from) owns
  it, or when the session itself holds the claim (a sub-agent created with a
  sandbox of its own). Ownership is the server-written claim label
  :data:`MANAGED_HOST_CLAIM_LABEL_KEY`: stamped when the managed launch binds
  the session, carried by a live-terminal transfer (the native ``/clear`` /
  ``/fork`` rotation), never inherited by a fork, never client-writable (the
  whole ``omnigent.sandbox.`` label namespace is server-reserved).
* A root bound before claims existed carries none. It is admitted when it is
  already bound to the host, unless positive evidence shows the binding was
  borrowed: another session holds the host's claim (the sandbox was
  provisioned for that session), or the session was created by a
  scheduled-task fire, which never provisions a sandbox, so a fired session
  bound to a managed host is always on another session's sandbox.
* Only the session the sandbox belongs to takes it down when it is deleted
  (:func:`session_owns_managed_host`): a sub-agent riding its root's sandbox,
  or a borrower bound to it, never terminates it.
* Shared hosts (a user's laptop, SSH hosts, anything without a
  ``sandbox_provider``) admit every session, exactly as before.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final, cast

from omnigent.entities import Conversation
from omnigent.errors import ErrorCode, OmnigentError

_logger = logging.getLogger(__name__)

# Server-written label naming the managed host a session tree owns. Lives in
# the server-reserved ``omnigent.sandbox.`` namespace (client writes 400) and
# is hidden from session snapshots so a client-side label copy (the native
# rotation flows copy every label) never carries it.
MANAGED_HOST_CLAIM_LABEL_KEY: Final = "omnigent.sandbox.host_id"

# Structured error code carried by every refusal of this policy.
MANAGED_HOST_NOT_SHAREABLE: Final = "managed_host_not_shareable"


def is_managed_host(host: object | None) -> bool:
    """
    Return whether *host* is a server-managed (per-session) sandbox host.

    :param host: A :class:`~omnigent.stores.host_store.Host` (or any object
        exposing ``sandbox_provider``), or ``None``.
    :returns: ``True`` when the host is backed by a server-provisioned sandbox.
    """
    provider = getattr(host, "sandbox_provider", None) if host is not None else None
    return isinstance(provider, str) and bool(provider)


def spawn_tree_root(conv: Conversation, conversation_store: Any) -> Conversation:
    """
    Return the top-level session *conv* descends from (itself when top-level).

    Sub-agents ride their root's runner, so host ownership is a property of the
    root. A root that no longer exists falls back to *conv* itself, which then
    has to prove ownership on its own.

    :param conv: The session being checked.
    :param conversation_store: Store used to read the root row.
    :returns: The root conversation.
    """
    root_id = getattr(conv, "root_conversation_id", None) or conv.id
    if root_id == conv.id:
        return conv
    root = conversation_store.get_conversation(root_id)
    return root if root is not None else conv


def managed_host_admits(
    root: Conversation,
    host: object,
    *,
    fired_by_schedule: bool = False,
    claimed_by_another: bool = False,
) -> bool:
    """
    Decide whether the session tree rooted at *root* may run on *host*.

    :param root: The spawn-tree root (see :func:`spawn_tree_root`).
    :param host: The host the tree would run on.
    :param fired_by_schedule: Whether *root* was created by a scheduled-task
        fire. Only consulted for a claim-less root.
    :param claimed_by_another: Whether a session other than *root* holds the
        host's claim. Only consulted for a claim-less root: the sandbox was
        provisioned for the claimant, so a claim-less binding to it is borrowed.
    :returns: ``True`` for shared hosts; for a managed host, ``True`` only
        when the tree owns it.
    """
    if not is_managed_host(host):
        return True
    host_id = getattr(host, "host_id", None)
    claim = (root.labels or {}).get(MANAGED_HOST_CLAIM_LABEL_KEY)
    if claim is not None:
        return claim == host_id
    if root.host_id != host_id:
        return False
    return not fired_by_schedule and not claimed_by_another


def managed_host_claimed_by_another(
    host_id: str,
    conversation_id: str,
    conversation_store: Any,
) -> bool:
    """
    Return whether a session other than *conversation_id* claims *host_id*.

    Archived sessions count: archiving does not release a sandbox, so an
    archived owner still owns its pod. Store failures propagate — a refusal
    here would release (and re-provision) a legitimate legacy owner, so an
    unanswerable question fails the request instead of guessing.

    :param host_id: The managed host.
    :param conversation_id: The claim-less root being checked.
    :param conversation_store: Store with the label reverse index.
    :returns: ``True`` when another session holds the claim.
    """
    claimants = conversation_store.list_conversation_ids_with_label(
        MANAGED_HOST_CLAIM_LABEL_KEY,
        host_id,
        limit=2,
        include_archived=True,
    )
    return any(claimant != conversation_id for claimant in claimants)


def session_fired_by_schedule(conversation_id: str, scheduled_task_store: Any | None) -> bool:
    """
    Return whether a scheduled-task fire created *conversation_id*.

    :param conversation_id: The session to look up.
    :param scheduled_task_store: The server's scheduled-task store, or ``None``.
    :returns: ``True`` only on positive evidence; any lookup failure is
        "no evidence".
    """
    if scheduled_task_store is None:
        return False
    try:
        return bool(scheduled_task_store.has_run_for_conversation(conversation_id))
    except Exception:  # noqa: BLE001 — evidence lookup must never break a request
        _logger.warning(
            "scheduled-run lookup failed for session %s; treating as no evidence",
            conversation_id,
            exc_info=True,
        )
        return False


def session_may_use_host(
    conv: Conversation,
    host: object | None,
    conversation_store: Any,
    *,
    scheduled_task_store: Any | None = None,
) -> bool:
    """
    Decide whether *conv* (and its tree) may run on *host*.

    Synchronous (store reads); run it via ``asyncio.to_thread`` on the loop.

    :param conv: The session being bound / launched / relaunched.
    :param host: The host it would run on (``None`` admits: nothing to guard).
    :param conversation_store: Store used to resolve the spawn-tree root.
    :param scheduled_task_store: Optional store supplying the legacy
        borrowed-binding evidence; ``None`` skips that check.
    :returns: Whether the tree may run on the host.
    """
    if not is_managed_host(host):
        return True
    host_id = getattr(host, "host_id", None)
    if _holds_claim(conv, host_id):
        # The sandbox was provisioned for this very session (e.g. a sub-agent
        # created with its own managed sandbox).
        return True
    root = spawn_tree_root(conv, conversation_store)
    # Borrowed-binding evidence only matters for a claim-less root already
    # bound to the host (everything else is decided by the claim / binding).
    legacy_bound = (
        isinstance(host_id, str)
        and MANAGED_HOST_CLAIM_LABEL_KEY not in (root.labels or {})
        and root.host_id == host_id
    )
    fired = legacy_bound and session_fired_by_schedule(root.id, scheduled_task_store)
    claimed_by_another = (
        legacy_bound
        and not fired
        and managed_host_claimed_by_another(cast(str, host_id), root.id, conversation_store)
    )
    return managed_host_admits(
        root,
        host,
        fired_by_schedule=fired,
        claimed_by_another=claimed_by_another,
    )


def session_owns_managed_host(
    conv: Conversation,
    host: object | None,
    conversation_store: Any,
    *,
    scheduled_task_store: Any | None = None,
) -> bool:
    """
    Decide whether *conv* is itself the session *host* was provisioned for.

    Stricter than :func:`session_may_use_host`: a sub-agent may RUN on its
    root's sandbox but never owns it, so deleting one must not terminate the
    sandbox. Only the session holding the claim itself (the sandbox was
    provisioned for it, or a live terminal carried the claim to it), or a
    legacy claim-less top-level session bound to the host with no evidence of
    borrowing, owns it.

    Synchronous (store reads); run it via ``asyncio.to_thread`` on the loop.

    :param conv: The session (e.g. one being deleted).
    :param host: The host it is bound to, or ``None``.
    :param conversation_store: Store used for the claim reverse lookup.
    :param scheduled_task_store: Optional store supplying the legacy
        borrowed-binding evidence.
    :returns: ``True`` only for a managed host owned by *conv* itself.
    """
    if not is_managed_host(host):
        return False
    if _holds_claim(conv, getattr(host, "host_id", None)):
        return True
    if conv.kind == "sub_agent" or (conv.root_conversation_id or conv.id) != conv.id:
        return False
    return session_may_use_host(
        conv, host, conversation_store, scheduled_task_store=scheduled_task_store
    )


def _holds_claim(conv: Conversation, host_id: object) -> bool:
    """Whether *conv* itself carries the server-written claim on *host_id*."""
    return (
        isinstance(host_id, str)
        and (conv.labels or {}).get(MANAGED_HOST_CLAIM_LABEL_KEY) == host_id
    )


def foreign_managed_host_message(host_id: str) -> str:
    """
    Build the refusal text for a session that is not *host_id*'s owner tree.

    :param host_id: The managed host that refused.
    :returns: A human-readable, actionable message.
    """
    return (
        f"host {host_id!r} is a managed sandbox provisioned for another session; "
        "a managed sandbox only runs its own session and that session's sub-agents"
    )


def foreign_managed_host_error(host_id: str, *, code: str = ErrorCode.CONFLICT) -> OmnigentError:
    """
    Build the :class:`OmnigentError` a refused managed-host binding raises.

    :param host_id: The managed host that refused.
    :param code: API error code; ``CONFLICT`` (409) for an existing session,
        ``INVALID_INPUT`` (400) for a request that names the host.
    :returns: The error to raise.
    """
    return OmnigentError(foreign_managed_host_message(host_id), code=code)


async def require_host_admits_new_session(
    *,
    user_id: str | None,
    host_id: str,
    parent_session_id: str | None,
    host_store: Any | None,
    conversation_store: Any,
    scheduled_task_store: Any | None = None,
) -> None:
    """
    Reject a session create that names another session's managed host.

    A brand-new top-level session never owns an existing sandbox (a managed
    create provisions its own via ``host_type="managed"``), so only a sub-agent
    whose root owns the host may name it. A local store read only — nothing
    reaches the host. A host the caller does not own is left to the existing
    ownership check (403/404), so this never reveals another user's hosts.

    :param user_id: Authenticated caller, or ``None`` when auth is disabled.
    :param host_id: The host the create names.
    :param parent_session_id: The create's parent, or ``None`` (top-level).
    :param host_store: Persistent host registrations, or ``None`` (skip).
    :param conversation_store: Store used to resolve the parent's root.
    :param scheduled_task_store: Optional store supplying the legacy
        borrowed-binding evidence for the parent's root (a sub-agent of a
        fired session bound to the sandbox is refused here, not at launch).
    :raises OmnigentError: 400 when the host is another session's sandbox.
    """
    if host_store is None:
        return
    host = await asyncio.to_thread(host_store.get_host, host_id)
    if not is_managed_host(host):
        return
    if user_id is not None and getattr(host, "user_id", None) != user_id:
        return
    if parent_session_id is not None:
        parent = await asyncio.to_thread(conversation_store.get_conversation, parent_session_id)
        if parent is not None and await asyncio.to_thread(
            session_may_use_host,
            parent,
            host,
            conversation_store,
            scheduled_task_store=scheduled_task_store,
        ):
            return
    raise foreign_managed_host_error(host_id, code=ErrorCode.INVALID_INPUT)


__all__ = [
    "MANAGED_HOST_CLAIM_LABEL_KEY",
    "MANAGED_HOST_NOT_SHAREABLE",
    "foreign_managed_host_error",
    "foreign_managed_host_message",
    "is_managed_host",
    "managed_host_admits",
    "managed_host_claimed_by_another",
    "require_host_admits_new_session",
    "session_fired_by_schedule",
    "session_may_use_host",
    "session_owns_managed_host",
    "spawn_tree_root",
]
