"""The mailer's trail in the factory event log, and its replay after a server roll.

Every accepted message is logged as ``message.queued`` (with its full text) and
closed by ``message.delivered`` or ``message.bounced``, all sharing the subject
``mail:<mail_id>``. A queued message with no closing event was lost with the
process that held it, so :func:`replay_undelivered` re-submits it on start with
the same id; recipients ignore an id they already handled.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory import events
from omnigent.server.factory.envelope import failure_text
from omnigent.stores.factory_store import now_ms

if TYPE_CHECKING:
    from omnigent.server.factory.mailer import Mailer, MailItem

_logger = logging.getLogger(__name__)

MAIL_REPLAY_ENV: Final = "OMNIGENT_FACTORY_MAIL_REPLAY"
REPLAY_WINDOW_S: Final = 2 * 3600
EXCERPT_MAX_CHARS: Final = 300
_SCAN_LIMIT: Final = 5000

KIND_QUEUED: Final = "message.queued"
KIND_DELIVERED: Final = "message.delivered"
KIND_BOUNCED: Final = "message.bounced"
KIND_RATE_LIMITED: Final = "rate_limited"
TERMINAL_KINDS: Final = (KIND_DELIVERED, KIND_BOUNCED)


def mail_subject(mail_id: str) -> str:
    """:returns: The event subject shared by one message's events, e.g. ``"mail:mail_ab12"``."""
    return f"mail:{mail_id}"


def replay_enabled() -> bool:
    """Replay runs unless ``OMNIGENT_FACTORY_MAIL_REPLAY`` is set to something other than ``1``."""
    raw = os.environ.get(MAIL_REPLAY_ENV, "").strip()
    return raw in ("", "1")


def mail_excerpt(text: str, limit: int = EXCERPT_MAX_CHARS) -> str:
    """
    The message body on one line, without the envelope header.

    :param text: The delivered text, e.g. ``'[Factory message · …]\\nplease add GET /version'``.
    :returns: e.g. ``"please add GET /version"``, cut to *limit* chars.
    """
    body = text
    if text.startswith("[Factory message ·"):
        _, _, body = text.partition("\n")
    flat = " ".join(body.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


async def _resolve_factory(app_state: Any, item: MailItem) -> str | None:
    """Fill ``item.factory_id``/``item.project`` from the target's membership when unset."""
    if item.factory_id:
        return item.factory_id
    from omnigent.factory import LABEL_PROJECT
    from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
    from omnigent.server.factory.membership import resolve_factory_id, root_conversation

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return None
    store = deps.conversation_store

    def _project(session_id: str | None) -> str | None:
        conv = store.get_conversation(session_id) if session_id else None
        return root_conversation(conv, store).labels.get(LABEL_PROJECT) if conv else None

    def _lookup() -> tuple[str | None, str | None]:
        conv = store.get_conversation(item.target_id)
        if conv is None:
            return None, None
        project = root_conversation(conv, store).labels.get(LABEL_PROJECT)
        return resolve_factory_id(conv, store), project or _project(item.sender_id)

    try:
        factory_id, project = await asyncio.to_thread(_lookup)
    except Exception:  # noqa: BLE001 - an unlogged message still gets delivered
        _logger.warning("factory: could not resolve the factory of mail %s", item.mail_id)
        return None
    item.factory_id = factory_id
    if item.project is None:
        item.project = project
    return factory_id


async def _emit(
    app_state: Any,
    item: MailItem,
    kind: str,
    summary: str,
    data: dict[str, Any],
    severity: str = "info",
    ts: int | None = None,
) -> None:
    factory_id = await _resolve_factory(app_state, item)
    if not factory_id:
        return
    await events.emit(
        app_state,
        factory_id,
        kind,
        severity=severity,
        project=item.project,
        from_id=item.sender_id,
        to_id=item.target_id,
        subject=mail_subject(item.mail_id),
        summary=summary,
        data={"mail_id": item.mail_id, "kind": item.kind, **data},
        ts=ts,
    )


async def record_queued(app_state: Any, item: MailItem, ts: int | None = None) -> None:
    """Log ``message.queued`` with everything a replay needs (never raises)."""
    excerpt = mail_excerpt(item.text)
    await _emit(
        app_state,
        item,
        KIND_QUEUED,
        excerpt or item.kind,
        {
            "excerpt": excerpt,
            "text": item.text,
            "restart": item.restart,
            "sender_id": item.sender_id,
            "target_id": item.target_id,
            "target_title": item.target_title,
            **({"created_by": item.created_by} if item.created_by else {}),
            **({"thread": item.thread} if item.thread else {}),
        },
        ts=ts,
    )


async def record_delivered(app_state: Any, item: MailItem, ts: int | None = None) -> None:
    """Log ``message.delivered`` (the receipt decisions and briefs join on)."""
    summary = f"Delivered: {mail_excerpt(item.text)}"
    await _emit(app_state, item, KIND_DELIVERED, summary, {}, ts=ts)


async def record_bounced(
    app_state: Any,
    item: MailItem,
    reason: str,
    ts: int | None = None,
    *,
    detail: str | None = None,
) -> None:
    """Log ``message.bounced`` with the final failure reason and its underlying error."""
    data = {"reason": reason, **({"detail": detail} if detail else {})}
    await _emit(
        app_state,
        item,
        KIND_BOUNCED,
        f"Undeliverable ({failure_text(reason, detail)}): {mail_excerpt(item.text)}",
        data,
        severity="warning",
        ts=ts,
    )


async def record_rate_limited(
    app_state: Any,
    factory_id: str,
    sender_id: str,
    target_id: str,
    limit: str,
    ts: int | None = None,
) -> None:
    """Log a refused post (``rate_limited``)."""
    await events.emit(
        app_state,
        factory_id,
        KIND_RATE_LIMITED,
        severity="warning",
        from_id=sender_id,
        to_id=target_id,
        summary=f"Post refused: rate limit of {limit}",
        data={"limit": limit},
        ts=ts,
    )


async def replay_undelivered(app_state: Any, mailer: Mailer, *, now: int | None = None) -> int:
    """
    Re-submit mail queued in the last two hours that never reached a terminal event.

    Oldest first, with the same id and text, so recipients can drop repeats.

    :param app_state: ``app.state`` carrying the factory deps.
    :param mailer: The mailer to submit to.
    :param now: Epoch milliseconds (tests); defaults to now.
    :returns: How many messages were re-submitted.
    """
    from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
    from omnigent.server.factory.mailer import MailItem

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return 0
    store = deps.factory_store
    since = (now if now is not None else now_ms()) - REPLAY_WINDOW_S * 1000
    try:
        queued = await asyncio.to_thread(
            store.find_events, None, KIND_QUEUED, since, limit=_SCAN_LIMIT
        )
        closed = await asyncio.to_thread(
            store.find_events, None, list(TERMINAL_KINDS), since, limit=_SCAN_LIMIT
        )
    except Exception:  # noqa: BLE001 - a failed replay must not stop the server starting
        _logger.warning("factory: could not read the mail log for replay", exc_info=True)
        return 0
    finished = {e.subject for e in closed}
    count = 0
    for event in queued:
        data = event.data or {}
        mail_id, text, target = data.get("mail_id"), data.get("text"), data.get("target_id")
        if event.subject in finished or not (mail_id and text and target):
            continue
        if mailer.is_queued(str(mail_id)):
            continue
        mailer.submit(
            MailItem(
                mail_id=str(mail_id),
                sender_id=data.get("sender_id"),
                target_id=str(target),
                target_title=data.get("target_title"),
                text=str(text),
                kind=str(data.get("kind") or "info"),
                restart=bool(data.get("restart")),
                factory_id=event.factory_id,
                project=event.project_slug,
                replayed=True,
                created_by=data.get("created_by"),
                thread=data.get("thread"),
            )
        )
        count += 1
    if count:
        _logger.info("factory: replaying %d undelivered message(s) from the event log", count)
    return count


__all__ = [
    "KIND_BOUNCED",
    "KIND_DELIVERED",
    "KIND_QUEUED",
    "KIND_RATE_LIMITED",
    "MAIL_REPLAY_ENV",
    "mail_excerpt",
    "mail_subject",
    "record_bounced",
    "record_delivered",
    "record_queued",
    "record_rate_limited",
    "replay_enabled",
    "replay_undelivered",
]
