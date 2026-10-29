"""The repos a factory member may write: its factory projects' repos, resolved by the server.

Every factory bundle fences its agents with ``factory_role_guard``, and the
server repeats the Tier-0 rules with the ``factory_invariants`` admin policy.
Both decide pushes and PR / API writes by repo. A bundle cannot know which
repos a factory works on (each project of the New factory form picks its own),
so the server resolves them whenever it builds a member's policy engine and
passes them to both policies as ``factory_repos``
(:data:`omnigent.runtime.policies.builder.SERVER_POLICY_ARGUMENTS`):

- a project lead and its sub-agents write the repo of the PL's own project;
- a lead and its sub-agents (the planner) write the repos of all the
  factory's projects (the role guard still keeps leads from pushing or
  opening PRs).

Only server state counts. Membership comes from the labels the server stamps
at spawn (``omnigent.factory.id``, ``role`` and ``project``), which no agent
can write, and repos from the factory's live project rows. A row
:mod:`reconcile` backfilled from a PL's sandbox labels is skipped: its repo
came from a lead's spawn arguments, not from a human. So are cancelled and
merged-away projects. Tool input, ``session_state``, charters and every other
label are never read. The rows are read on every engine build, so a project
added later extends the set on the next tool call.

The same set (plus the factory's default repo) decides which repos an agent's
project proposal may use without an owner (:func:`human_repos`, read by
:mod:`omnigent.server.factory.proposals`). Every repo in it therefore traces
back to a human: one the owner typed, approved, or that an agent re-used from
a project a human already gave the factory. A repo an agent only spawned a PL
on stays out of both.

What the repos do not change: merging stays denied on every repo, pushes go
only to ``factory/**`` refs and never to a protected branch, and
``caffeinelabs/infra`` is never written.

Only the server's engine gets them. The runner-side gate
(:class:`omnigent.runner.policy.RunnerToolPolicyGate`) builds the bundle guard
with its static defaults; that is safe because every GitHub write a factory
agent makes is a shell command the runner hands to the server's
``/policies/evaluate`` (no factory bundle declares an MCP server, which
``tests/examples/test_factory_bundles.py`` pins).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from omnigent.factory import (
    LABEL_DEFAULT_REPO,
    LABEL_ID,
    LABEL_PROJECT,
    LABEL_ROLE,
    ROLE_LEAD,
)
from omnigent.policies.builtins.factory import FACTORY_REPOS_ARG
from omnigent.policies.builtins.factory_invariants import HANDLER as INVARIANTS_HANDLER
from omnigent.policies.builtins.github import _normalize_repo
from omnigent.server.bundles import FACTORY_GUARD_HANDLER
from omnigent.server.factory.background.reconcile import CREATED_BY_RECONCILE
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.membership import is_top_level

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.stores.factory_store import FactoryStore

_logger = logging.getLogger(__name__)

GUARDED_HANDLERS: tuple[str, ...] = (FACTORY_GUARD_HANDLER, INVARIANTS_HANDLER)
"""The policies that take ``factory_repos`` from the server."""

_RETIRED_STATUSES: frozenset[str] = frozenset({"cancelled", "merged"})
"""Project statuses whose repo no longer counts (``active`` and ``done`` do)."""


def project_repos(store: FactoryStore, factory_id: str) -> dict[str, str]:
    """
    The repo of each live project a human gave the factory.

    :param store: The factory store.
    :param factory_id: Factory root session id.
    :returns: Project slug to lowercase ``owner/repo``, e.g.
        ``{"docs-guide": "caffeinelabs/omnigent"}``; rows without a repo, rows
        backfilled by reconcile and cancelled or merged-away projects are left out.
    """
    out: dict[str, str] = {}
    for project in store.list_projects(factory_id):
        if project.created_by == CREATED_BY_RECONCILE or project.status in _RETIRED_STATUSES:
            continue
        repo = _normalize_repo(project.repo or "")
        if repo:
            out[project.slug] = repo
    return out


def human_repos(store: FactoryStore, root: Conversation) -> set[str]:
    """
    The repos a human gave the factory: its live projects' and its default repo.

    An agent's project proposal on any other repo needs the owner
    (:func:`omnigent.server.factory.proposals.factory_repo_set`). Reading the
    guard's own rows here keeps a proposal from turning a repo an agent chose
    (a spawn's workspace, a sandbox label) into one the guards let it write.

    :param store: The factory store.
    :param root: The factory root (its default-repo label is server-stamped).
    :returns: Lowercase ``owner/repo`` values, e.g. ``{"caffeinelabs/app"}``.
    """
    repos = set(project_repos(store, root.id).values())
    default = _normalize_repo(root.labels.get(LABEL_DEFAULT_REPO) or "")
    if default:
        repos.add(default)
    return repos


def member_repos(
    conversation: Conversation, root: Conversation, store: FactoryStore
) -> frozenset[str] | None:
    """
    The repos *conversation* may write as a factory member.

    :param conversation: The evaluated session (a top-level member or a sub-agent).
    :param root: Its spawn-tree root (the member itself when top-level).
    :param store: The factory store.
    :returns: Lowercase ``owner/repo`` values: the PL's own project's for a
        project lead and its sub-agents, every project's for a lead and its
        sub-agents, none for a member with no project, and none for a
        sub-agent whose root could not be loaded. ``None`` when the session
        is in no factory (its policies keep their own arguments).
    """
    if not is_top_level(conversation) and root.id == conversation.id:
        # A sub-agent carries no factory labels of its own: without its root
        # its factory is unknown, so it writes nothing rather than the
        # bundle default.
        return frozenset()
    top = conversation if is_top_level(conversation) else root
    factory_id = conversation.labels.get(LABEL_ID) or top.labels.get(LABEL_ID)
    if not factory_id:
        return None
    repos = project_repos(store, factory_id)
    if top.labels.get(LABEL_ROLE) == ROLE_LEAD:
        return frozenset(repos.values())
    slug = top.labels.get(LABEL_PROJECT)
    return frozenset([repos[slug]] if slug in repos else [])


def guard_arguments(
    deps: FactoryDeps,
) -> Callable[[Conversation, Conversation], Mapping[str, object] | None]:
    """
    Build the server resolver of ``factory_repos`` for one app's factory store.

    :param deps: The app's factory stores.
    :returns: ``resolve(conversation, root)``: ``{"factory_repos": [...]}`` for
        a factory member, ``None`` for any other session. When the store cannot
        be read it fails closed with no writable repos.
    """

    def _resolve(conversation: Conversation, root: Conversation) -> Mapping[str, object] | None:
        try:
            repos = member_repos(conversation, root, deps.factory_store)
        except Exception:  # noqa: BLE001 - a store error must deny, not widen
            _logger.warning(
                "factory: could not resolve the project repos of session %s; "
                "it writes no repo until they resolve",
                conversation.id,
                exc_info=True,
            )
            return {FACTORY_REPOS_ARG: []}
        if repos is None:
            return None
        return {FACTORY_REPOS_ARG: sorted(repos)}

    return _resolve


def install(app_state: Any) -> Callable[[], None]:
    """
    Register the resolver for the factory guards of *app_state*'s factory.

    :param app_state: ``app.state`` carrying the :class:`FactoryDeps`.
    :returns: A callable that removes what this call registered.
    """
    from omnigent.runtime.policies.builder import SERVER_POLICY_ARGUMENTS

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return lambda: None
    resolver = guard_arguments(deps)
    for handler in GUARDED_HANDLERS:
        SERVER_POLICY_ARGUMENTS[handler] = resolver

    def _uninstall() -> None:
        for handler in GUARDED_HANDLERS:
            if SERVER_POLICY_ARGUMENTS.get(handler) is resolver:
                del SERVER_POLICY_ARGUMENTS[handler]

    return _uninstall


__all__ = [
    "GUARDED_HANDLERS",
    "guard_arguments",
    "human_repos",
    "install",
    "member_repos",
    "project_repos",
]
