"""Who is calling a runner-facing cockpit route, and what it may write.

The agent routes (``/v1/factory/agent/*``) authenticate the caller exactly
like ``/v1/factory/messages`` (EDIT on ``from_session_id`` plus tunnel-token
binding). Writes are then scoped to the caller's factory: a PR must be on a
repo of one of its projects, and a sub-agent writes for its root PL's project.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from fastapi import Request
from sqlalchemy.exc import IntegrityError

from omnigent.factory import canonical_pr_url
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.membership import Membership, membership_of
from omnigent.server.factory.routes import (
    FactoryRouteError,
    _authorize_sender,
    _out_of_factory,
)

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryPr

PREVIEW_DOMAIN_ENV: Final = "OMNIGENT_FACTORY_PREVIEW_DOMAIN"
DEFAULT_PREVIEW_DOMAIN: Final = "envs.sandbox.caffeinelabs.io"

_WORKSPACE_REPO_RE: Final = re.compile(
    r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?"
)
_LINK_RE: Final = re.compile(r"https?://[^\s<>\"'`]+")


@dataclass(frozen=True)
class AgentCaller:
    """
    An authenticated factory member calling an agent route.

    :param deps: The factory stores.
    :param user_id: The runner owner's user id (``None`` when auth is off).
    :param session: The caller's session row.
    :param membership: The caller's factory membership (``factory_id`` set).
    """

    deps: FactoryDeps
    user_id: str | None
    session: Conversation
    membership: Membership

    @property
    def factory_id(self) -> str:
        """:returns: The caller's factory (root lead session id)."""
        assert self.membership.factory_id is not None
        return self.membership.factory_id

    @property
    def title(self) -> str:
        """:returns: The caller's display title, e.g. ``"PL · api-version"``."""
        return display_title(self.session.title, self.session.id)


async def authorize_agent(request: Request, from_session_id: str) -> AgentCaller:
    """
    Authenticate the caller as *from_session_id* and require factory membership.

    :raises FactoryRouteError: 403 ``sender_not_bound`` or ``session_out_of_factory``.
    """
    deps = factory_deps(request.app.state)
    user_id, sender = await _authorize_sender(request, deps, from_session_id)
    membership = await asyncio.to_thread(membership_of, sender, deps)
    if membership.factory_id is None:
        raise _out_of_factory("from_session_id is not a factory member")
    return AgentCaller(deps=deps, user_id=user_id, session=sender, membership=membership)


def repo_from_workspace(workspace: str) -> str | None:
    """
    The GitHub ``owner/repo`` a managed workspace clones.

    :param workspace: e.g. ``"https://github.com/caffeinelabs/app#main"``.
    :returns: ``"caffeinelabs/app"`` (lowercase), or ``None`` for other hosts.
    """
    match = _WORKSPACE_REPO_RE.fullmatch(workspace.partition("#")[0].strip())
    if match is None:
        return None
    return f"{match.group(1)}/{match.group(2)}".lower()


def factory_repos(caller: AgentCaller) -> dict[str, set[str]]:
    """
    The repos this factory works on, each with the project slugs that use it.

    Project rows are the source; until a project row exists, the repos the
    caller's root PL and the factory root were launched with also count.

    :returns: e.g. ``{"caffeinelabs/app": {"api-version", "db-version"}}``.
    """
    from omnigent.factory import LABEL_PROJECT
    from omnigent.server.managed_hosts import read_managed_repo_workspaces

    deps = caller.deps
    repos: dict[str, set[str]] = {}
    for project in deps.factory_store.list_projects(caller.factory_id):
        if project.repo:
            repos.setdefault(project.repo.lower(), set()).add(project.slug)
    roots: list[Conversation] = []
    for session_id in {caller.membership.root_session_id, caller.factory_id}:
        row = deps.conversation_store.get_conversation(session_id)
        if row is not None:
            roots.append(row)
    for row in roots:
        slug = row.labels.get(LABEL_PROJECT)
        for workspace in read_managed_repo_workspaces(row.labels):
            repo = repo_from_workspace(workspace)
            if repo is None:
                continue
            slugs = repos.setdefault(repo, set())
            if slug:
                slugs.add(slug)
    return repos


def require_factory_repo(caller: AgentCaller, repo: str) -> set[str]:
    """
    Refuse a PR on a repo none of the factory's projects works on.

    :returns: The project slugs that use *repo*.
    :raises FactoryRouteError: 403 ``pr_out_of_factory``.
    """
    repos = factory_repos(caller)
    if repo not in repos:
        known = ", ".join(sorted(repos)) or "none yet"
        raise FactoryRouteError(
            403,
            "pr_out_of_factory",
            f"{repo} is not a repo of this factory's projects (repos: {known})",
        )
    return repos[repo]


def pr_project(caller: AgentCaller, slugs: set[str], existing: FactoryPr | None) -> str | None:
    """
    The project a PR belongs to: the caller's own, else the PR's, else the repo's only one.

    :param slugs: Projects using the PR's repo (:func:`require_factory_repo`).
    :param existing: The tracked PR row, if any.
    """
    if caller.membership.project:
        return caller.membership.project
    if existing is not None and existing.project_slug:
        return existing.project_slug
    return next(iter(slugs)) if len(slugs) == 1 else None


def ensure_pr(
    caller: AgentCaller,
    repo: str,
    number: int,
    *,
    slugs: set[str],
    refresh: bool = False,
    task: str | None = None,
) -> tuple[FactoryPr, bool]:
    """
    Track a PR, creating its row (``source=tool``) on first sight.

    The owning project and task are only filled in when unknown, so a lead's
    report never re-files a PL's PR.

    :param refresh: Also ask the GitHub poller to refresh it now.
    :returns: ``(row, created)``.
    """
    try:
        return _ensure_pr_once(caller, repo, number, slugs, refresh, task)
    except IntegrityError:
        # Two reports raced to create the row; the second one now updates it.
        return _ensure_pr_once(caller, repo, number, slugs, refresh, task)


def _ensure_pr_once(
    caller: AgentCaller,
    repo: str,
    number: int,
    slugs: set[str],
    refresh: bool,
    task: str | None,
) -> tuple[FactoryPr, bool]:
    store = caller.deps.factory_store
    existing = store.get_pr_by_number(caller.factory_id, repo, number)
    fields: dict[str, object] = {}
    if existing is None:
        fields.update(url=canonical_pr_url(repo, number), source="tool")
    project = pr_project(caller, slugs, existing)
    if project and (existing is None or not existing.project_slug):
        fields["project_slug"] = project
    if task and (existing is None or not existing.task):
        fields["task"] = task
    if refresh:
        # A NULL last poll is how a refresh is requested from the poller.
        fields["last_polled_at"] = None
    if existing is not None and not fields:
        return existing, False
    return store.upsert_pr(caller.factory_id, repo, number, **fields)


async def announce_new_pr(app_state: Any, caller: AgentCaller, pr: FactoryPr) -> None:
    """Log a PR an agent report made the factory track for the first time."""
    from omnigent.server.factory import events

    await events.emit(
        app_state,
        caller.factory_id,
        "pr.discovered",
        project=pr.project_slug,
        from_id=caller.session.id,
        subject=f"pr:{pr.repo}#{pr.number}",
        summary=f"{caller.title} registered {pr.repo}#{pr.number}",
        data={"pr_id": pr.id, "source": "tool"},
    )


def preview_domain() -> str:
    """:returns: The domain factory previews and dev envs are served under."""
    return os.environ.get(PREVIEW_DOMAIN_ENV, "").strip().lower() or DEFAULT_PREVIEW_DOMAIN


def env_url_error(key: str, url: str) -> str | None:
    """
    Check one reported env URL.

    App, API and MinIO URLs are probed by the server, so they must be https on
    the env domain (an SSRF guard); the Tilt UI link only needs to be http(s).

    :param key: ``app`` / ``api`` / ``minio`` / ``tilt_ui``.
    :returns: Why the URL is refused, or ``None``.
    """
    if key == "tilt_ui":
        return None if _LINK_RE.fullmatch(url) else "must be an http(s) URL"
    pattern = rf"https://[a-z0-9-]+\.{re.escape(preview_domain())}(?:/[^\s<>\"'`]*)?"
    if re.fullmatch(pattern, url) is None:
        return f"must be https://<host>.{preview_domain()}/…"
    return None


__all__ = [
    "AgentCaller",
    "announce_new_pr",
    "authorize_agent",
    "ensure_pr",
    "env_url_error",
    "factory_repos",
    "pr_project",
    "preview_domain",
    "repo_from_workspace",
    "require_factory_repo",
]
