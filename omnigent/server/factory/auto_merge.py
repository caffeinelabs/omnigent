"""Owner-set auto-merge targets: which factory PRs the server merges, and when.

Agents never merge (Tier 0 for them: the role guard denies ``gh pr merge``).
An owner may name ``{repo, base}`` pairs in ``product.delivery.auto_merge``;
the server's merger (:mod:`.background.auto_merger`) then squash-merges a
factory PR into one of them, with the owner's GitHub connection, only when

- its ``(repo, base)`` is a target;
- the factory marks it ready (the condition of the cockpit's merge ask: every
  gate passed on its head and no human change request holds that head);
- GitHub, read just before the merge, reports the same head, the PR open and
  not a draft, ``MERGEABLE``, no change request, and every check on the head
  completed as success, skipped or neutral.

The merge names the head as the expected one, so a push after the read is
refused by GitHub. Only owners change the targets (class B, a human edit);
no agent proposal may name them (:data:`.definition_blocks.AGENT_NEVER`).
Nothing here writes to GitHub; this module decides, the merger acts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory.stage import ready_for_human

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryPr
    from omnigent.server.factory.context import FactoryDeps

AUTO_MERGE_PATH: Final = "product.delivery.auto_merge"
NUDGE_TOPIC: Final = "auto_merge"
"""The merger's wake-up topic (:func:`nudge_merger`)."""
GREEN_CHECKS: Final = frozenset({"SUCCESS", "SKIPPED", "NEUTRAL"})
"""Check conclusions (and status states) a merge accepts; anything else waits or fails."""
WAITING_CHECKS: Final = frozenset({"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING"})
CHECKS_MAX: Final = 100
"""Checks the merger reads on a head; a head with more is never merged automatically."""
AUTO_TEXT: Final = "merges automatically when checks pass"
"""How the cockpit words a ready PR into a target: nobody needs to merge it."""

Targets = frozenset[tuple[str, str]]


def targets_of(delivery: Mapping[str, Any] | None) -> Targets:
    """
    :param delivery: A ``product.delivery`` block (``None``: none).
    :returns: Its targets as ``(lowercase owner/repo, base)``, e.g.
        ``frozenset({("acme/app", "staging")})``.
    """
    found = (delivery or {}).get("auto_merge") if isinstance(delivery, Mapping) else None
    out: set[tuple[str, str]] = set()
    for item in found if isinstance(found, list) else []:
        if not isinstance(item, Mapping):
            continue
        repo, base = item.get("repo"), item.get("base")
        if isinstance(repo, str) and repo and isinstance(base, str) and base:
            out.add((repo.strip().lower(), base.strip()))
    return frozenset(out)


def is_target(targets: Targets, repo: str | None, base: str | None) -> bool:
    """:returns: Whether a PR of *repo* into *base* is one of *targets*."""
    if not repo or not base:
        return False
    return (repo.strip().lower(), base.strip()) in targets


def factory_targets(deps: FactoryDeps | None, factory_id: str | None) -> Targets:
    """:returns: The factory's targets (none when its definition is unresolved). Never raises."""
    from omnigent.server.factory import product_profile

    return targets_of(product_profile.delivery(deps, factory_id))


def nudge_merger(pr_id: str | None = None) -> None:
    """Wake the server's merger now (a PR into a target just became ready)."""
    from omnigent.server.factory import nudge

    nudge.nudge(NUDGE_TOPIC, pr_id)


def policy_text(repo: str, base: str) -> str:
    """:returns: The charter's merge policy for a target base (agents still never merge)."""
    return (
        f"PRs into {repo}#{base} are merged by the factory's server when ready; "
        "never merge yourself."
    )


def local_refusal(pr: FactoryPr, targets: Targets) -> str | None:
    """
    Why the stored row of *pr* is not a merge candidate (no GitHub call).

    :returns: A reason, e.g. ``"not ready"``, or ``None`` for a candidate.
    """
    if pr.state != "open":
        return f"PR is {pr.state}"
    if not is_target(targets, pr.repo, pr.base_ref):
        return "not an auto-merge target"
    if pr.is_draft:
        return "PR is a draft"
    if not ready_for_human(pr):
        return "not ready"
    if len(pr.head_sha or "") != 40:
        return "head unknown"
    return None


def merge_query(number: int) -> str:
    """
    The read-only query the merger runs just before a merge (one PR).

    :returns: A query taking ``$owner`` and ``$name``; the PR is aliased
        :func:`.prs.pr_alias` and its head commit ``head``, like the poller's.
    """
    from omnigent.server.factory.prs import pr_alias

    n = int(number)
    return (
        "query($owner: String!, $name: String!) {\n"
        "  rateLimit { remaining resetAt }\n"
        "  repository(owner: $owner, name: $name) {\n"
        f"    {pr_alias(n)}: pullRequest(number: {n}) {{\n"
        "      number state isDraft headRefOid baseRefName mergeable reviewDecision\n"
        "      head: commits(last: 1) { nodes { commit { oid statusCheckRollup {\n"
        f"        contexts(first: {CHECKS_MAX}) {{ totalCount nodes {{\n"
        "          __typename\n"
        "          ... on CheckRun { name status conclusion startedAt completedAt }\n"
        "          ... on StatusContext { context state createdAt }\n"
        "        } }\n"
        "      } } } }\n"
        "    }\n"
        "  }\n"
        "}"
    )


def _contexts_total(node: Mapping[str, Any]) -> int | None:
    heads = (
        (node.get("head") or {}).get("nodes") if isinstance(node.get("head"), Mapping) else None
    )
    commit = heads[-1].get("commit") if isinstance(heads, list) and heads else None
    rollup = commit.get("statusCheckRollup") if isinstance(commit, Mapping) else None
    contexts = rollup.get("contexts") if isinstance(rollup, Mapping) else None
    total = contexts.get("totalCount") if isinstance(contexts, Mapping) else None
    return total if isinstance(total, int) else None


def checks_refusal(checks: Iterable[Mapping[str, Any]], total: int | None) -> str | None:
    """
    Why the head's checks do not allow a merge yet.

    :param checks: The head's checks as :func:`.prs.head_checks` gives them
        (newest run per name, ``state`` its conclusion or ``PENDING``).
    :param total: How many contexts GitHub reports on the head (``None``: unknown).
    :returns: A reason, or ``None`` when every check completed green.
    """
    found = list(checks)
    if total is not None and total > CHECKS_MAX:
        return f"more than {CHECKS_MAX} checks on the head"
    if not found:
        return "no checks reported on the head yet"
    waiting = sorted(str(c.get("name")) for c in found if str(c.get("state")) in WAITING_CHECKS)
    bad = sorted(
        f"{c.get('name')} ({str(c.get('state')).lower()})"
        for c in found
        if str(c.get("state")) not in GREEN_CHECKS | WAITING_CHECKS
    )
    if bad:
        return "checks not green: " + ", ".join(bad[:5])
    if waiting:
        return "checks still running: " + ", ".join(waiting[:5])
    return None


def github_refusal(pr: FactoryPr, node: Mapping[str, Any] | None, targets: Targets) -> str | None:
    """
    Why GitHub's view of *pr*, read just now, does not allow a merge.

    :param node: The PR from :func:`merge_query` (``None``: not found).
    :returns: A reason, or ``None`` when it may merge at its stored head.
    """
    from omnigent.server.factory.prs import head_checks

    if not isinstance(node, Mapping):
        return "PR not found on GitHub"
    if node.get("state") != "OPEN":
        return f"PR is {str(node.get('state') or 'unknown').lower()} on GitHub"
    if node.get("isDraft") is True:
        return "PR is a draft"
    base = node.get("baseRefName")
    if not isinstance(base, str) or not is_target(targets, pr.repo, base) or base != pr.base_ref:
        return f"base is {base or 'unknown'} on GitHub"
    head = str(node.get("headRefOid") or "").lower()
    if head != (pr.head_sha or "").lower():
        return f"head moved since ready ({head[:7] or '?'} on GitHub, ready at {pr.head_sha[:7]})"
    if node.get("mergeable") != "MERGEABLE":
        return f"mergeable={node.get('mergeable') or 'UNKNOWN'}"
    if node.get("reviewDecision") == "CHANGES_REQUESTED":
        return "changes requested on GitHub"
    return checks_refusal(head_checks(node), _contexts_total(node))


__all__ = [
    "AUTO_MERGE_PATH",
    "AUTO_TEXT",
    "CHECKS_MAX",
    "GREEN_CHECKS",
    "NUDGE_TOPIC",
    "WAITING_CHECKS",
    "Targets",
    "checks_refusal",
    "factory_targets",
    "github_refusal",
    "is_target",
    "local_refusal",
    "merge_query",
    "nudge_merger",
    "policy_text",
    "targets_of",
]
