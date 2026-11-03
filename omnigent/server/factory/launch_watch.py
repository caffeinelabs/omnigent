"""Launch watch: members whose sandbox fails to start, and finished PLs' sandboxes.

The supervisor runs :meth:`LaunchWatch.tick` every pass over the top-level members.

- **Retry once.** An active member whose managed sandbox did not come up is
  retried once, :data:`RETRY_DELAY_S` after it failed. A first launch (no
  sandbox, or one the failed launch already removed), or one a server restart
  cut off, is retried on a NEW sandbox for the same session
  (:func:`.new_sandbox.relaunch_on_new_sandbox`); its project, charter, role
  and the server's records stay. A member whose own sandbox failed to wake (or
  whose runner did not connect) is woken again on that same sandbox
  (:func:`.new_sandbox.wake_same_sandbox`): the server never deletes a sandbox,
  and the files in it, on its own.
- **Stale failures.** A failure cached for a member whose host is online again,
  or whose runner is attached (a later message brought it back), is cleared.
- **Then ask.** When the retry fails too, owners get one decision with plain
  options, worded by what happened; nothing retries on its own again. Every
  try is counted in the member's ``launch_tries`` label (the server's retry
  in ``launch_auto``) and logged (``member.launch_failed``,
  ``member.new_sandbox``, ``member.wake_retried``, ``member.launch_recovered``).
- **Brief it.** Once a new sandbox is up, its member is told what happened
  (:func:`.new_sandbox.send_follow_up`): a PL that never got its brief gets it
  again. Sent only then, so a slow launch cannot bounce it.
- **Release.** A PL whose project was cancelled or finished (or that finished
  itself) has its sandbox stopped once it and its sub-agents are idle with
  nothing left to deliver for :data:`RELEASE_GRACE_S`, unless it keeps a live
  preview up for review. Its sub-agents run in that sandbox; the next message
  wakes it.

State is in memory on one replica; the labels make a server roll safe.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from omnigent.entities import Conversation
from omnigent.factory import (
    LABEL_ID,
    LABEL_KEEP_PREVIEWS,
    LABEL_LAUNCH_ASKED,
    LABEL_LAUNCH_AUTO,
    LABEL_LAUNCH_NEW,
    LABEL_LAUNCH_TRIES,
    LABEL_PROJECT,
    LABEL_ROLE,
    LABEL_SANDBOX_RELEASED_AT,
    LABEL_STATE,
    ROLE_PROJECT_LEAD,
    SEAT_ROLES,
    STATE_ACTIVE,
    STATE_DONE,
)
from omnigent.server.factory import events, previews, server_decisions
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.control import CockpitError, clip, is_closed
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.mailer import get_mailer
from omnigent.server.factory.membership import is_top_level
from omnigent.server.factory.new_sandbox import (
    last_relaunch,
    launch_in_flight,
    launch_tries,
    new_sandbox_since,
    relaunch_on_new_sandbox,
    send_follow_up,
    server_retried,
    wake_same_sandbox,
)

_logger = logging.getLogger(__name__)

RETRY_DELAY_S: Final = 30.0
"""Wait after a failed launch before the one retry."""
LOST_AFTER_S: Final = 1200.0
"""A managed member with no host, runner or launch on record this long after its
latest launch began lost that launch to a server restart (launches time out well before)."""
BRIEF_WAIT_S: Final = 900.0
"""How long a PL up on its new sandbox waits for a brief still on its way before a new one."""
RELEASE_GRACE_S: Final = 300.0
"""How long a finished PL must sit idle, with nothing to deliver, before its sandbox stops."""
_ERROR_MAX: Final = 300
_EPISODE_LABELS: Final = (
    LABEL_LAUNCH_TRIES,
    LABEL_LAUNCH_AUTO,
    LABEL_LAUNCH_NEW,
    LABEL_LAUNCH_ASKED,
)


@dataclass(frozen=True)
class LaunchFailure:
    """
    Why a member has no sandbox.

    :param error: The launch error, e.g. ``"managed sandbox host startup failed: …"``.
    :param at: Epoch seconds it failed (or was first seen lost).
    :param lost: A server restart cut the launch off; nothing records an error.
    :param wake: The member's own sandbox did not wake (or its runner did not
        connect); a retry wakes that sandbox again, never replaces it.
    :param owned: The member owns that sandbox (one it borrows is never woken by the watch).
    """

    error: str
    at: float
    lost: bool = False
    wake: bool = False
    owned: bool = True


def _deps(app_state: Any) -> FactoryDeps | None:
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    return deps if isinstance(deps, FactoryDeps) else None


def launch_pending(conv: Conversation) -> bool:
    """:returns: Whether *conv*'s managed launch is in flight or failed (the watch owns it)."""
    from omnigent.server.routes._sessions.common import _session_sandbox_status_cache

    return conv.id in _session_sandbox_status_cache


def sandbox_up(app_state: Any, conv: Conversation) -> bool:
    """:returns: Whether *conv* is bound to a host that is online, with no launch pending."""
    if launch_pending(conv) or launch_in_flight(app_state, conv.id) or not conv.host_id:
        return False
    host_store = getattr(app_state, "host_store", None)
    return host_store is None or bool(host_store.is_online(conv.host_id))


def back_up(app_state: Any, conv: Conversation) -> bool:
    """
    Whether *conv* is up despite a failure on record: its runner is attached,
    or its host is online, and no launch runs.

    A wake whose runner did not connect leaves its host online, and a later
    message can bring the runner back without a ``ready`` stage.
    """
    if launch_in_flight(app_state, conv.id):
        return False
    tunnels = getattr(app_state, "tunnel_registry", None)
    if conv.runner_id and tunnels is not None and tunnels.get(conv.runner_id) is not None:
        return True
    host_store = getattr(app_state, "host_store", None)
    if not conv.host_id or host_store is None:
        return False
    return bool(host_store.is_online(conv.host_id))


def clear_failure(session_id: str) -> None:
    """Drop a stale failed launch of *session_id* (its sandbox is up after all)."""
    from omnigent.server.routes._sessions.helpers import _publish_sandbox_status

    _publish_sandbox_status(session_id, "ready")


class LaunchWatch:
    """
    Retry a failed member launch once (a new sandbox only when it has none),
    then ask owners; stop finished PLs' sandboxes.

    :param app_state: ``app.state`` with the factory deps, host store and tracker.
    """

    def __init__(self, app_state: Any) -> None:
        self._app_state = app_state
        self._failed: dict[str, tuple[int, float]] = {}
        self._up: dict[str, float] = {}
        self._finished: dict[str, float] = {}

    async def tick(
        self, members: Sequence[Conversation], children: Sequence[Conversation], now: float
    ) -> None:
        """Run one pass over the factory's top-level *members* (and their *children*)."""
        for conv in members:
            try:
                await self._watch(conv, now)
            except Exception:  # noqa: BLE001 - one member must not stop the pass
                _logger.warning("factory: launch watch of %s failed", conv.id, exc_info=True)
        try:
            await self._release_finished(members, children, now)
        except Exception:  # noqa: BLE001 - the next pass retries
            _logger.warning("factory: releasing finished sandboxes failed", exc_info=True)

    def forget(self, member_ids: set[str]) -> None:
        """Drop state of members that left."""
        for state in (self._failed, self._up, self._finished):
            for sid in [s for s in state if s not in member_ids]:
                del state[sid]

    # ── failed launches ─────────────────────────────────────────

    async def _watch(self, conv: Conversation, now: float) -> None:
        if (
            not is_top_level(conv)
            or is_closed(conv)
            or conv.labels.get(LABEL_STATE) != STATE_ACTIVE
        ):
            self._failed.pop(conv.id, None)
            return
        failure = await self._failure(conv, now)
        tries = launch_tries(conv)
        if failure is None:
            self._failed.pop(conv.id, None)
            if any(label in conv.labels for label in _EPISODE_LABELS) and await asyncio.to_thread(
                sandbox_up, self._app_state, conv
            ):
                await self._recovered(conv, tries, now)
            return
        seen = self._failed.get(conv.id)
        if seen is None or seen[0] != tries:
            seen = (tries, failure.at)
            self._failed[conv.id] = seen
            await self._log_failure(conv, tries, failure)
        if self._will_retry(conv, failure):
            if now - seen[1] >= RETRY_DELAY_S:
                await self._retry(conv, failure)
            return
        await self._ask(conv, tries, failure)

    @staticmethod
    def _will_retry(conv: Conversation, failure: LaunchFailure) -> bool:
        """:returns: Whether the server retries *failure* on its own (once per start)."""
        return not server_retried(conv) and (failure.owned or not failure.wake)

    async def _failure(self, conv: Conversation, now: float) -> LaunchFailure | None:
        """
        :returns: Why *conv*'s managed sandbox is not up, or ``None`` while it is
            up, starting, or not a managed sandbox at all.
        """
        from omnigent.server.managed_hosts import read_managed_repo_workspaces
        from omnigent.server.routes._sessions.common import (
            _session_launch_progress_cache,
            _session_sandbox_status_cache,
        )

        status = _session_sandbox_status_cache.get(conv.id)
        if status is not None:
            if status.stage != "failed" or launch_in_flight(self._app_state, conv.id):
                return None
            if await asyncio.to_thread(back_up, self._app_state, conv):
                _logger.info("factory: %s is up after a failed launch; clearing it", conv.id)
                clear_failure(conv.id)
                return None
            progress = _session_launch_progress_cache.get(conv.id)
            failed_at = progress is not None and progress.stage == "failed"
            at = float(progress.since) if progress is not None and failed_at else now
            host, owned = await self._own_host(conv)
            return LaunchFailure(
                status.error or "the sandbox did not start", at, wake=host is not None, owned=owned
            )
        if getattr(self._app_state, "managed_launches", None) is None or launch_in_flight(
            self._app_state, conv.id
        ):
            return None
        if conv.host_id or conv.runner_id or not read_managed_repo_workspaces(conv.labels):
            return None
        if now - self._launch_started(conv) < LOST_AFTER_S:
            return None
        return LaunchFailure("its sandbox launch was cut off by a server restart", now, lost=True)

    @staticmethod
    def _launch_started(conv: Conversation) -> float:
        """:returns: When *conv*'s latest launch began: its newest new sandbox, else creation."""
        return max(float(conv.created_at), new_sandbox_since(conv) or 0.0)

    async def _own_host(self, conv: Conversation) -> tuple[Any, bool]:
        """
        :returns: ``(host, owned)``: the managed host *conv* is bound to (``None``
            when it has none, or the failed launch already removed it) and
            whether *conv* owns it.
        """
        from omnigent.server.managed_host_isolation import session_owns_managed_host

        deps = _deps(self._app_state)
        host_store = getattr(self._app_state, "host_store", None)
        if deps is None or host_store is None or not conv.host_id:
            return None, True
        host = await asyncio.to_thread(host_store.get_host, conv.host_id)
        if host is None:
            return None, True
        owned = await asyncio.to_thread(
            session_owns_managed_host,
            conv,
            host,
            deps.conversation_store,
            scheduled_task_store=getattr(self._app_state, "scheduled_task_store", None),
        )
        return host, bool(owned)

    async def _log_failure(self, conv: Conversation, tries: int, failure: LaunchFailure) -> None:
        title = display_title(conv.title, conv.id)
        retry = self._will_retry(conv, failure)
        if failure.wake:
            what = "did not wake"
            then = "waking it again" if retry else "owners decide"
        else:
            what = "did not start"
            then = "retrying on a new sandbox" if retry else "owners decide"
        if failure.lost:
            _logger.warning(
                "factory: a server restart cut off the sandbox launch of %s; a sandbox it "
                "made, if any, is left to the managed-sandbox reaper",
                conv.id,
            )
        await self._emit(
            conv,
            "member.launch_failed",
            severity="warning",
            summary=f'"{title}" sandbox {what} (try {tries}), {then}: '
            f"{clip(failure.error, _ERROR_MAX)}",
            data={
                "tries": tries,
                "error": clip(failure.error, _ERROR_MAX),
                "lost": failure.lost,
                "wake": failure.wake,
                "retry": retry,
            },
        )

    async def _retry(self, conv: Conversation, failure: LaunchFailure) -> None:
        """The one retry, on the member as it is now (it may have come back meanwhile)."""
        deps = _deps(self._app_state)
        if deps is None:
            return
        fresh = await asyncio.to_thread(deps.conversation_store.get_conversation, conv.id)
        if fresh is None or fresh.labels.get(LABEL_STATE) != STATE_ACTIVE or server_retried(fresh):
            return
        if launch_in_flight(self._app_state, fresh.id):
            return
        if await asyncio.to_thread(back_up, self._app_state, fresh):
            clear_failure(fresh.id)
            return
        host, owned = await self._own_host(fresh)
        try:
            if host is not None:
                # Its own sandbox, with its files: wake it again, never replace it.
                if owned:
                    await wake_same_sandbox(self._app_state, fresh, reason=failure.error)
            elif not failure.wake:
                await relaunch_on_new_sandbox(
                    self._app_state, fresh, by="server", auto=True, reason=failure.error
                )
        except CockpitError as exc:
            _logger.info("factory: no retry for %s yet: %s", conv.id, exc.message)

    async def _ask(self, conv: Conversation, tries: int, failure: LaunchFailure) -> None:
        factory_id = conv.labels.get(LABEL_ID)
        deps = _deps(self._app_state)
        if not factory_id or deps is None or conv.labels.get(LABEL_LAUNCH_ASKED) == str(tries):
            return
        relaunched_by = None
        if new_sandbox_since(conv) is not None:
            last = await asyncio.to_thread(last_relaunch, deps, conv)
            if last and not last.get("auto") and last.get("by"):
                relaunched_by = str(last["by"])
        opened = await server_decisions.open_launch_failed_decision(
            self._app_state,
            conv,
            factory_id,
            tries,
            clip(failure.error, _ERROR_MAX),
            wake=failure.wake,
            lost=failure.lost,
            retried=server_retried(conv),
            relaunched_by=relaunched_by,
        )
        if opened is not None:
            await asyncio.to_thread(
                deps.conversation_store.set_labels, conv.id, {LABEL_LAUNCH_ASKED: str(tries)}
            )

    async def _recovered(self, conv: Conversation, tries: int, now: float) -> None:
        """Its sandbox is up: send a new one's member what it reads first, then end the episode."""
        deps = _deps(self._app_state)
        if deps is None:
            return
        new = new_sandbox_since(conv) is not None
        mail_id = None
        relaunch = await asyncio.to_thread(last_relaunch, deps, conv) if new else {}
        if relaunch:
            up_since = self._up.setdefault(conv.id, now)
            note = relaunch.get("note")
            mail_id = await send_follow_up(
                self._app_state,
                conv,
                by=str(relaunch.get("by") or "server"),
                note=note if isinstance(note, str) else None,
                wait_for_brief=now - up_since < BRIEF_WAIT_S,
            )
            if mail_id is None:
                return  # its brief is still on its way
        self._up.pop(conv.id, None)
        for label in _EPISODE_LABELS:
            if label in conv.labels:
                await asyncio.to_thread(deps.conversation_store.delete_label, conv.id, label)
        factory_id = conv.labels.get(LABEL_ID)
        if factory_id:
            await server_decisions.expire_by_key(
                self._app_state, factory_id, server_decisions.launch_failed_key(conv.id)
            )
        if new or tries > 1:
            title = display_title(conv.title, conv.id)
            where = "on a new sandbox" if new else "again"
            suffix = f" (try {tries})" if tries > 1 else ""
            await self._emit(
                conv,
                "member.launch_recovered",
                severity="notice",
                summary=f'"{title}" is up {where}{suffix}',
                data={"tries": tries, "new": new, "mail_id": mail_id},
            )

    # ── finished PLs ────────────────────────────────────────────

    async def _release_finished(
        self, members: Sequence[Conversation], children: Sequence[Conversation], now: float
    ) -> None:
        if getattr(self._app_state, "host_store", None) is None:
            return
        kids: dict[str, list[Conversation]] = {}
        for child in children:
            if child.parent_conversation_id:
                kids.setdefault(child.parent_conversation_id, []).append(child)
        for conv in members:
            why = await self._finished_why(conv)
            host = await self._own_sandbox(conv) if why else None
            if why is None or host is None or not self._idle(conv, kids.get(conv.id, [])):
                self._finished.pop(conv.id, None)
                continue
            first = self._finished.setdefault(conv.id, now)
            if now - first < RELEASE_GRACE_S:
                continue
            self._finished.pop(conv.id, None)
            await self._release(conv, host, kids.get(conv.id, []), why, now)

    async def _finished_why(self, conv: Conversation) -> str | None:
        """:returns: Why *conv*'s sandbox can go, e.g. ``"project cancelled"``, else ``None``."""
        deps = _deps(self._app_state)
        slug = conv.labels.get(LABEL_PROJECT)
        factory_id = conv.labels.get(LABEL_ID)
        if deps is not None and conv.labels.get(LABEL_ROLE) in SEAT_ROLES:
            # A seat the server stopped (discovery off) frees its sandbox once idle.
            return "seat stopped" if conv.labels.get(LABEL_STATE) == STATE_DONE else None
        if deps is None or conv.labels.get(LABEL_ROLE) != ROLE_PROJECT_LEAD or not slug:
            return None
        project = (
            await asyncio.to_thread(deps.factory_store.get_project, factory_id, slug)
            if factory_id
            else None
        )
        ended = server_decisions.project_ended(project)
        if ended is not None:
            why = f"project {ended}"
        elif conv.labels.get(LABEL_STATE) == STATE_DONE:
            why = "PL finished"
        else:
            return None
        if conv.labels.get(LABEL_KEEP_PREVIEWS) != "0" and factory_id:
            prs = await asyncio.to_thread(deps.factory_store.list_prs, factory_id, ["open"])
            if previews.has_live_preview(prs, slug):
                return None
        return why

    def _idle(self, conv: Conversation, kids: Sequence[Conversation]) -> bool:
        """
        :param kids: *conv*'s sub-agents; they run in its sandbox.
        :returns: Whether *conv* and its sub-agents run no turn and have no mail
            on its way, and *conv* has no launch.
        """
        from omnigent.server.routes._sessions.helpers import _session_status_from_cache

        if launch_pending(conv) or launch_in_flight(self._app_state, conv.id):
            return False
        try:
            mailer = get_mailer(self._app_state)
        except Exception:  # noqa: BLE001 - no mailer means nothing is on its way
            mailer = None
        for session in (conv, *kids):
            status = _session_status_from_cache(session.id, session.live_status)
            if status in ("running", "waiting"):
                return False
            if mailer is not None and mailer.has_mail_for(session.id):
                return False
        return True

    async def _own_sandbox(self, conv: Conversation) -> Any:
        """:returns: The live managed host *conv* owns, or ``None``."""
        from omnigent.server.managed_host_isolation import (
            is_managed_host,
            session_owns_managed_host,
        )
        from omnigent.stores.host_store import host_is_live

        deps = _deps(self._app_state)
        host_store = getattr(self._app_state, "host_store", None)
        if deps is None or host_store is None or not conv.host_id:
            return None
        host = await asyncio.to_thread(host_store.get_host, conv.host_id)
        if host is None or not is_managed_host(host) or host.sandbox_id is None:
            return None
        if not host_is_live(host):
            return None
        owns = await asyncio.to_thread(
            session_owns_managed_host,
            conv,
            host,
            deps.conversation_store,
            scheduled_task_store=getattr(self._app_state, "scheduled_task_store", None),
        )
        return host if owns else None

    async def _release(
        self,
        conv: Conversation,
        host: Any,
        kids: Sequence[Conversation],
        why: str,
        now: float,
    ) -> None:
        from omnigent.server.managed_hosts import stop_managed_sandbox

        deps = _deps(self._app_state)
        host_store = self._app_state.host_store
        config = getattr(self._app_state, "sandbox_config", None)
        if deps is None or not await stop_managed_sandbox(host, host_store, config):
            return
        stopped = [host.host_id]
        for kid in kids:
            own = await self._own_sandbox(kid) if kid.host_id != host.host_id else None
            if own is not None and await stop_managed_sandbox(own, host_store, config):
                stopped.append(own.host_id)
        await asyncio.to_thread(
            deps.conversation_store.set_labels,
            conv.id,
            {LABEL_SANDBOX_RELEASED_AT: str(int(now))},
        )
        title = display_title(conv.title, conv.id)
        await self._emit(
            conv,
            "member.sandbox_released",
            severity="notice",
            summary=f'Stopped the idle sandbox of "{title}" ({why}); a message wakes it',
            data={"why": why, "host_ids": stopped},
        )
        _logger.info("factory: stopped the sandbox of %s (%s)", conv.id, why)

    async def _emit(self, conv: Conversation, kind: str, **kwargs: Any) -> None:
        factory_id = conv.labels.get(LABEL_ID)
        if not factory_id:
            return
        await events.emit(
            self._app_state,
            factory_id,
            kind,
            project=conv.labels.get(LABEL_PROJECT),
            to_id=conv.id,
            subject=f"member:{conv.id}",
            **kwargs,
        )


__all__ = [
    "BRIEF_WAIT_S",
    "LOST_AFTER_S",
    "RELEASE_GRACE_S",
    "RETRY_DELAY_S",
    "LaunchFailure",
    "LaunchWatch",
    "back_up",
    "clear_failure",
    "launch_pending",
    "sandbox_up",
]
