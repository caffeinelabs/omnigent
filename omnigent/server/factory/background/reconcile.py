"""Reconcile every factory with its read model, every minute.

For each factory root this:

1. ensures the owner's sidebar folder project ``<name>`` (🏭 icon) exists (once:
   a folder the owner deleted stays deleted);
2. files top-level members that sit in no project into it (never moving a
   session the user filed elsewhere);
3. creates project rows for PLs whose project label has none (title from the
   PL title, repo from the PL's managed workspace, else the root's) and links
   a missing or departed PL;
4. persists each project's derived stage, logging ``stage.changed``.

A folder that fails skips only step 2 for that factory; a factory that fails is
skipped. Either is counted in :attr:`ReconcileReport.failed`, logged at most
hourly per factory until it succeeds again, and the pass goes on.

So a factory started from a chat (the 2026-10-26 Atlas run) shows up in the
cockpit with no other help.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.factory import (
    FOLDER_DELETED,
    LABEL_FOLDER_PROJECT,
    LABEL_PROJECT,
    LABEL_ROLE,
    ROLE_PROJECT_LEAD,
    STAGE_READY,
)
from omnigent.server.factory import events
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.membership import factory_members
from omnigent.server.factory.snapshot import (
    MAIL_WINDOW_S,
    list_factory_roots,
    pending_brief_targets,
    pick_pl,
    pl_title_slug,
    project_stage,
)
from omnigent.server.factory.start import FOLDER_ICON, folder_name, is_factory_folder
from omnigent.stores.factory_store import now_ms
from omnigent.util.session_lifecycle import is_session_closed

if TYPE_CHECKING:
    from omnigent.entities import Conversation

_logger = logging.getLogger(__name__)

INTERVAL_S: Final = 60.0
# A factory that keeps failing is logged at most this often per step, not every pass.
FAILURE_LOG_EVERY_S: Final = 3600.0

# ``(factory id, step)`` -> when its failure was last logged; cleared when the step succeeds.
_last_failure_log: dict[tuple[str, str], float] = {}


@dataclass
class ReconcileReport:
    """What one pass changed (for logs and tests)."""

    folders_created: list[str] = field(default_factory=list)
    filed: list[str] = field(default_factory=list)
    projects_created: list[str] = field(default_factory=list)
    pls_linked: list[str] = field(default_factory=list)
    stages_changed: list[tuple[str, str, str]] = field(default_factory=list)
    # ``(factory id, step)`` for each step that failed this pass, logged or not.
    failed: list[tuple[str, str]] = field(default_factory=list)


def _failure_log_due(factory_id: str, step: str) -> bool:
    """
    Whether a failure is due a log line: once per factory and step per :data:`FAILURE_LOG_EVERY_S`.

    :param factory_id: The factory root session id.
    :param step: What failed, e.g. ``"folder"``.
    :returns: ``True`` (and the clock restarts) when the last line is older than the window.
    """
    now = time.monotonic()
    last = _last_failure_log.get((factory_id, step))
    if last is not None and now - last < FAILURE_LOG_EVERY_S:
        return False
    _last_failure_log[(factory_id, step)] = now
    return True


def _record_failure(
    report: ReconcileReport, factory_id: str, step: str, exc: BaseException
) -> None:
    """
    Count a failed step in *report* and log it ("<step> of <id> failed") when due.

    :param report: The pass's report.
    :param factory_id: The factory root session id.
    :param step: What failed, ``"folder"`` or ``"reconcile"``.
    :param exc: The failure, logged with its traceback.
    """
    report.failed.append((factory_id, step))
    if _failure_log_due(factory_id, step):
        _logger.warning("factory: %s of %s failed", step, factory_id, exc_info=exc)


def _clear_failure(factory_id: str, step: str) -> None:
    """Forget a step's last failure once it succeeds, so a relapse logs straight away."""
    _last_failure_log.pop((factory_id, step), None)


def repo_from_labels(labels: Mapping[str, str]) -> tuple[str | None, str | None]:
    """
    The ``owner/name`` and branch of a managed session's first repository.

    :param labels: Session labels carrying ``omnigent.sandbox.repo.<i>``.
    :returns: e.g. ``("caffeinelabs/app", "main")``, or ``(None, None)``.
    """
    from omnigent.server.managed_hosts import read_managed_repo_workspaces

    for workspace in read_managed_repo_workspaces(labels):
        url, _, branch = workspace.partition("#")
        if "://" in url:
            path = url.split("://", 1)[1].partition("/")[2]
        elif url.startswith("git@"):
            path = url.partition(":")[2]
        else:
            continue
        path = path.strip("/").removesuffix(".git")
        if path.count("/") >= 1:
            return path, branch or None
    return None, None


def _ensure_folder(
    deps: FactoryDeps, root: Conversation, owner: str | None, claimed: set[str]
) -> tuple[str | None, bool]:
    """
    :param claimed: Folder ids other factories already use.
    :returns: ``(folder project id, created)``; ``(None, False)`` without a project store.
    """
    projects = deps.project_store
    if projects is None:
        return None, False
    current = root.labels.get(LABEL_FOLDER_PROJECT)
    if current == FOLDER_DELETED:
        return None, False
    if current and projects.get(current, user_id=owner) is not None:
        return current, False
    if current:
        # The owner deleted the folder; refiling every member would undo that.
        deps.conversation_store.set_labels(root.id, {LABEL_FOLDER_PROJECT: FOLDER_DELETED})
        return None, False
    name = folder_name(root)
    for candidate in (name, f"{name} · {root.id[:6]}"):
        try:
            folder = projects.create(
                project_id=uuid.uuid4().hex,
                name=candidate,
                user_id=owner,
                config={"icon": FOLDER_ICON},
            )
        except OmnigentError as exc:
            if exc.code != ErrorCode.ALREADY_EXISTS:
                raise
            existing = next((p for p in projects.list(user_id=owner) if p.name == candidate), None)
            if existing is None or existing.id in claimed or not is_factory_folder(existing):
                continue
            folder = existing
        deps.conversation_store.set_labels(root.id, {LABEL_FOLDER_PROJECT: folder.id})
        return folder.id, True
    return None, False


def _owner(deps: FactoryDeps, session_id: str) -> str | None:
    from omnigent.server.routes._auth_helpers import get_session_owner_id

    return get_session_owner_id(session_id, deps.permission_store)


def _sync_factory(
    deps: FactoryDeps, root: Conversation, claimed: set[str], report: ReconcileReport
) -> tuple[str | None, bool, list[tuple[Any, str, str, str | None]]]:
    """
    Steps 1-4 for one factory (synchronous store work).

    :returns: ``(owner, sidebar changed, [(project, old stage, new stage, overlay)])``.
    """
    store = deps.conversation_store
    fstore = deps.factory_store
    factory_id = root.id
    owner = _owner(deps, factory_id)
    try:
        folder, created = _ensure_folder(deps, root, owner, claimed)
    except Exception as exc:  # noqa: BLE001 - a broken folder must not block steps 3-4
        _record_failure(report, factory_id, "folder", exc)
        folder, created = None, False
    else:
        _clear_failure(factory_id, "folder")
    if created and folder:
        report.folders_created.append(folder)
    members = [
        m for m in factory_members(factory_id, store) if not is_session_closed(m.labels, m.title)
    ]
    changed_sidebar = created
    if folder:
        for member in members:
            if member.project_id is not None:
                continue
            if deps.permission_store is not None and _owner(deps, member.id) != owner:
                continue
            if store.set_conversation_project(member.id, folder):
                report.filed.append(member.id)
                changed_sidebar = True

    rows = {p.slug: p for p in fstore.list_projects(factory_id)}
    root_repo, root_branch = repo_from_labels(root.labels)
    member_ids = {m.id for m in members}
    for pl in sorted(members, key=lambda m: m.created_at):
        slug = pl.labels.get(LABEL_PROJECT)
        if pl.labels.get(LABEL_ROLE) != ROLE_PROJECT_LEAD or not slug:
            continue
        row = rows.get(slug)
        if row is None:
            repo, branch = repo_from_labels(pl.labels)
            if repo is None:
                repo, branch = root_repo, root_branch
            current = pick_pl(None, slug, members)
            rows[slug] = fstore.upsert_project(
                factory_id,
                slug,
                title=pl_title_slug(pl.title) or slug,
                repo=repo,
                base_branch=branch,
                pl_session_id=current.id if current is not None else pl.id,
                created_by="reconcile",
            )
            report.projects_created.append(slug)
        elif row.pl_session_id is None or row.pl_session_id not in member_ids:
            current = pick_pl(None, slug, members)
            if current is not None and current.id != row.pl_session_id:
                rows[slug] = fstore.update_project(row.id, pl_session_id=current.id) or row
                report.pls_linked.append(slug)

    prs = fstore.list_prs(factory_id)
    open_decisions = fstore.list_decisions(factory_id=factory_id, status="open", limit=500)
    mail = fstore.find_events(
        factory_id,
        ["message.queued", "message.delivered", "message.bounced"],
        now_ms() - MAIL_WINDOW_S * 1000,
        limit=5000,
    )
    pending = pending_brief_targets(mail)
    changes: list[tuple[Any, str, str, str | None]] = []
    for row in rows.values():
        pl = pick_pl(row, row.slug, members)
        result = project_stage(row, prs, pl, open_decisions, pending)
        if result.stage == row.stage:
            continue
        updated = fstore.update_project(row.id, stage=result.stage)
        if updated is not None:
            changes.append((updated, row.stage, result.stage, result.overlay))
            report.stages_changed.append((row.slug, row.stage, result.stage))
    return owner, changed_sidebar, changes


async def reconcile_factory(
    app_state: Any,
    deps: FactoryDeps,
    root: Conversation,
    claimed: set[str],
    report: ReconcileReport,
) -> None:
    """Reconcile one factory and announce what changed."""
    from omnigent.server.routes._sessions.helpers import announce_projects_changed

    owner, sidebar, changes = await asyncio.to_thread(_sync_factory, deps, root, claimed, report)
    if sidebar:
        announce_projects_changed(owner)
    for project, old, new, overlay in changes:
        await events.emit(
            app_state,
            root.id,
            "stage.changed",
            severity="notice" if new == STAGE_READY else "info",
            project=project.slug,
            from_id=project.pl_session_id,
            subject=f"project:{project.slug}",
            summary=f"{project.title}: {old} → {new}",
            data={"from": old, "to": new, "overlay": overlay},
        )


async def reconcile_once(app_state: Any) -> ReconcileReport:
    """Run one reconcile pass over every factory; a failing factory is logged and skipped."""
    report = ReconcileReport()
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return report
    roots = await asyncio.to_thread(list_factory_roots, deps.conversation_store)
    folders = {r.id: r.labels.get(LABEL_FOLDER_PROJECT) for r in roots}
    for root in roots:
        if is_session_closed(root.labels, root.title):
            continue
        claimed = {f for rid, f in folders.items() if f and rid != root.id}
        try:
            await reconcile_factory(app_state, deps, root, claimed, report)
        except Exception as exc:  # noqa: BLE001 - one factory must not stop the others
            _record_failure(report, root.id, "reconcile", exc)
        else:
            _clear_failure(root.id, "reconcile")
    return report


async def start(app_state: Any) -> Callable[[], Awaitable[None]]:
    """Reconcile now and then every :data:`INTERVAL_S`."""

    async def _loop() -> None:
        while True:
            try:
                await reconcile_once(app_state)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory: reconcile pass failed")
            await asyncio.sleep(INTERVAL_S)

    task = asyncio.get_running_loop().create_task(_loop(), name="factory-reconcile")

    async def stop() -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    return stop


__all__ = [
    "FAILURE_LOG_EVERY_S",
    "INTERVAL_S",
    "ReconcileReport",
    "reconcile_once",
    "repo_from_labels",
    "start",
]
