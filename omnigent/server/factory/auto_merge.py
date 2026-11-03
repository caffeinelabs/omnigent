"""Owner-set auto-merge targets: which factory PRs the server merges, and when.

Agents never merge (Tier 0 for them: the role guard denies ``gh pr merge``).
An owner may name ``{repo, base}`` pairs in ``product.delivery.auto_merge``;
the server's merger (:mod:`.background.auto_merger`) then squash-merges a
factory PR into one of them, with the owner's GitHub connection, only when

- its ``(repo, base)`` is a target, it belongs to an active project and its
  head is a factory branch of that project (``factory/<slug>/<task>``);
- the factory marks it ready (the condition of the cockpit's merge ask: every
  gate passed on its head and no human change request holds that head);
- GitHub, read just before the merge, reports the same head in the same
  repository (never a fork), the PR open and not a draft, ``MERGEABLE``, no
  change request, nothing changed since the factory last read it, every check
  on the head completed as success, skipped or neutral, and the review and
  verify markers that pass the head written by a repo owner, member or
  collaborator, or by the factory owner's login the agents act as.

The merge names the head as the expected one, so a push after the read is
refused by GitHub. Only owners change the targets (class B, a human edit);
no agent proposal may name them (:data:`.definition_blocks.AGENT_NEVER`).
Nothing here writes to GitHub; this module decides, the merger acts.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory_gates import parse_time
from omnigent.server.factory.stage import ready_for_human

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryPr, FactoryProject
    from omnigent.server.factory.context import FactoryDeps

AUTO_MERGE_PATH: Final = "product.delivery.auto_merge"
NUDGE_TOPIC: Final = "auto_merge"
"""The merger's wake-up topic (:func:`nudge_merger`)."""
GREEN_CHECKS: Final = frozenset({"SUCCESS", "SKIPPED", "NEUTRAL"})
"""Check conclusions (and status states) a merge accepts; anything else waits or fails."""
WAITING_CHECKS: Final = frozenset({"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING"})
CHECKS_MAX: Final = 100
"""Checks the merger reads on a head; a head with more is never merged automatically."""
TRUSTED_ASSOCIATIONS: Final = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
"""GitHub author associations whose review and verify markers a merge accepts."""
AUTO_TEXT: Final = "merges automatically when checks pass"
"""How the cockpit words a ready PR into a target: nobody needs to merge it."""
FAILED_RULE: Final = "pr.auto_merge_failed"
"""Incident: GitHub refused or failed the merge call (the merger backs off)."""
WAITING_RULE: Final = "pr.auto_merge_waiting"
"""Incident: a ready PR into a target does not merge, for a reason that needs a person."""
INCIDENT_RULES: Final = (FAILED_RULE, WAITING_RULE)

Targets = frozenset[tuple[str, str]]


@dataclass(frozen=True)
class Refusal:
    """
    Why GitHub's view of a PR does not allow a merge now.

    :param text: e.g. ``"checks not green: lint (failure)"``.
    :param lasting: It does not clear by itself (a person must act), so
        owners hear of it at once instead of after a long wait.
    :param refresh: The factory's stored view is behind GitHub: the PR poller
        should read the PR again (its readiness follows from that read).
    """

    text: str
    lasting: bool = False
    refresh: bool = False


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


def factory_targets(
    deps: FactoryDeps | None, factory_id: str | None, *, fresh: bool = False
) -> Targets:
    """
    :param fresh: Read the definition in force now, past its short cache.
    :returns: The factory's targets (none when its definition is unresolved). Never raises.
    """
    from omnigent.server.factory import definition, product_profile

    if not fresh:
        return targets_of(product_profile.delivery(deps, factory_id))
    if deps is None or not factory_id:
        return frozenset()
    try:
        resolved = definition.definition_for(deps, factory_id, fresh=True)
    except Exception:  # noqa: BLE001 - no targets: nothing merges
        return frozenset()
    found = resolved.get("product.delivery") if resolved is not None else None
    return targets_of(found if isinstance(found, Mapping) else None)


def merger_on(app_state: Any) -> bool:
    """:returns: Whether this server runs the merger (``OMNIGENT_FACTORY_AUTO_MERGE_S > 0``)."""
    from omnigent.server.factory.background.auto_merger import get_merger

    merger = get_merger(app_state)
    return merger is not None and merger.enabled


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


def scope_refusal(pr: FactoryPr, targets: Targets, project: FactoryProject | None) -> str | None:
    """
    Why the server would not merge *pr* even once it is ready (who merges it).

    :param project: The PR's project row (``None``: it has none).
    :returns: A reason, e.g. ``"not an auto-merge target"``, or ``None`` when
        the server merges it once ready and green.
    """
    from omnigent.server.factory.prs import parse_factory_branch

    if not is_target(targets, pr.repo, pr.base_ref):
        return "not an auto-merge target"
    if project is None or not pr.project_slug or project.slug != pr.project_slug:
        return "PR has no project"
    if project.status != "active":
        return f"project is {project.status}"
    if parse_factory_branch(pr.head_ref, {project.slug}) is None:
        return f"head branch {pr.head_ref or '?'} is not a factory branch of {project.slug}"
    return None


def merges_itself(pr: FactoryPr, targets: Targets, project: FactoryProject | None) -> bool:
    """:returns: Whether the server, not a person, merges *pr* once it is ready and green."""
    return scope_refusal(pr, targets, project) is None


def local_refusal(pr: FactoryPr, targets: Targets, project: FactoryProject | None) -> str | None:
    """
    Why the stored row of *pr* is not a merge candidate (no GitHub call).

    :param project: The PR's project row (``None``: it has none).
    :returns: A reason, e.g. ``"not ready"``, or ``None`` for a candidate.
    """
    if pr.state != "open":
        return f"PR is {pr.state}"
    scope = scope_refusal(pr, targets, project)
    if scope is not None:
        return scope
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

    It reads what the PR poller reads for the review and verify gates
    (labels, commits, comments and reviews, each with its author's
    association), the head's repository and branch, and the head's checks.

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
        "      number state isDraft headRefOid headRefName isCrossRepository\n"
        "      headRepository { nameWithOwner } baseRefName mergeable reviewDecision updatedAt\n"
        "      labels(first: 50) { nodes { name } }\n"
        "      commits(last: 20) { nodes { commit { oid committedDate } } }\n"
        "      head: commits(last: 1) { nodes { commit { oid statusCheckRollup {\n"
        f"        contexts(first: {CHECKS_MAX}) {{ totalCount nodes {{\n"
        "          __typename\n"
        "          ... on CheckRun { name status conclusion startedAt completedAt }\n"
        "          ... on StatusContext { context state createdAt }\n"
        "        } }\n"
        "      } } } }\n"
        "      comments(last: 100) {\n"
        "        nodes { body createdAt url author { login } authorAssociation }\n"
        "      }\n"
        "      reviews(last: 50) {\n"
        "        nodes { body submittedAt state url author { login } authorAssociation }\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "}"
    )


def _head_commit(node: Mapping[str, Any]) -> Mapping[str, Any] | None:
    head = node.get("head")
    heads = head.get("nodes") if isinstance(head, Mapping) else None
    commit = heads[-1].get("commit") if isinstance(heads, list) and heads else None
    return commit if isinstance(commit, Mapping) else None


def _contexts_total(node: Mapping[str, Any]) -> int | None:
    commit = _head_commit(node)
    rollup = commit.get("statusCheckRollup") if commit is not None else None
    contexts = rollup.get("contexts") if isinstance(rollup, Mapping) else None
    total = contexts.get("totalCount") if isinstance(contexts, Mapping) else None
    return total if isinstance(total, int) else None


def checks_refusal(checks: Iterable[Mapping[str, Any]], total: int | None) -> Refusal | None:
    """
    Why the head's checks do not allow a merge yet.

    :param checks: The head's checks as :func:`.prs.head_checks` gives them
        (newest run per name, ``state`` its conclusion or ``PENDING``).
    :param total: How many contexts GitHub reports on the head (``None``: unknown).
    :returns: A reason (lasting unless checks are still running), or ``None``
        when every check completed green.
    """
    found = list(checks)
    if total is not None and total > CHECKS_MAX:
        return Refusal(f"more than {CHECKS_MAX} checks on the head", lasting=True)
    if not found:
        # Checks report within seconds of a push; a head that is ready without any
        # (review and verify done) never gets one by itself.
        return Refusal("no checks reported on the head yet", lasting=True)
    waiting = sorted(str(c.get("name")) for c in found if str(c.get("state")) in WAITING_CHECKS)
    bad = sorted(
        f"{c.get('name')} ({str(c.get('state')).lower()})"
        for c in found
        if str(c.get("state")) not in GREEN_CHECKS | WAITING_CHECKS
    )
    if bad:
        return Refusal("checks not green: " + ", ".join(bad[:5]), lasting=True)
    if waiting:
        return Refusal("checks still running: " + ", ".join(waiting[:5]))
    return None


def _trusted(item: Mapping[str, Any], logins: Collection[str]) -> bool:
    if str(item.get("authorAssociation") or "").upper() in TRUSTED_ASSOCIATIONS:
        return True
    author = item.get("author")
    login = author.get("login") if isinstance(author, Mapping) else None
    return isinstance(login, str) and login.lower() in logins


def markers_refusal(
    node: Mapping[str, Any], head: str, trusted_logins: Collection[str] = ()
) -> Refusal | None:
    """
    Whether GitHub's review and verify markers, read now, still pass *head*,
    and whether the markers that pass it come from people the repo trusts.

    The gates are the poller's own (``gate_review``, ``gate_verify``), run once
    over every comment and review (a FAIL posted since the last poll stops the
    merge) and once over only those by a repo owner, member or collaborator
    or by one of *trusted_logins* (an outsider's APPROVE or PASS never decides).

    :param node: The PR from :func:`merge_query`.
    :param head: The head the merge would name (40 hex, lowercase).
    :param trusted_logins: Lowercase logins trusted whatever their association,
        e.g. the factory owner's login the agents act as.
    :returns: A reason, or ``None`` when both gates pass on trusted markers.
    """
    from omnigent.factory_gates import (
        GATE_PASS,
        GATE_SKIP,
        GateResult,
        gate_review,
        gate_verify,
        label_names,
    )
    from omnigent.server.factory.prs import normalize_pr

    every, _ = normalize_pr(node)
    logins = {login.lower() for login in trusted_logins}
    trusted = {
        **every,
        "comments": [c for c in every.get("comments") or [] if _trusted(c, logins)],
        "reviews": [r for r in every.get("reviews") or [] if _trusted(r, logins)],
    }
    labels = label_names(every)

    def review(pr: Mapping[str, Any]) -> GateResult:
        return gate_review(dict(pr), head)

    def verify(pr: Mapping[str, Any]) -> GateResult:
        return gate_verify(dict(pr), head, labels)

    for name, gate in (("review", review), ("verify", verify)):
        now = gate(every)
        if now.state not in (GATE_PASS, GATE_SKIP):
            why = f"the {name} gate is {now.state} on GitHub now"
            return Refusal(f"{why}: {now.reason}" if now.reason else why, refresh=True)
        if gate(trusted).state not in (GATE_PASS, GATE_SKIP):
            return Refusal(
                f"the {name} marker that passes the head is not by a repo owner, member or "
                "collaborator, or the factory owner's login",
                lasting=True,
            )
    return None


def github_refusal(
    pr: FactoryPr,
    node: Mapping[str, Any] | None,
    targets: Targets,
    trusted_logins: Collection[str] = (),
) -> Refusal | None:
    """
    Why GitHub's view of *pr*, read just now, does not allow a merge.

    :param node: The PR from :func:`merge_query` (``None``: not found).
    :param trusted_logins: Lowercase logins whose markers count whatever their
        association (the factory owner's), see :func:`markers_refusal`.
    :returns: A reason, or ``None`` when it may merge at its stored head.
    """
    from omnigent.server.factory.prs import head_checks

    if not isinstance(node, Mapping):
        return Refusal("PR not found on GitHub", lasting=True)
    if node.get("state") != "OPEN":
        return Refusal(
            f"PR is {str(node.get('state') or 'unknown').lower()} on GitHub", refresh=True
        )
    if node.get("isDraft") is True:
        return Refusal("PR is a draft", refresh=True)
    base = node.get("baseRefName")
    if not isinstance(base, str) or not is_target(targets, pr.repo, base) or base != pr.base_ref:
        return Refusal(f"base is {base or 'unknown'} on GitHub", refresh=True)
    head = str(node.get("headRefOid") or "").lower()
    if head != (pr.head_sha or "").lower():
        return Refusal(
            f"head moved since ready ({head[:7] or '?'} on GitHub, ready at {pr.head_sha[:7]})",
            refresh=True,
        )
    head_repo = node.get("headRepository")
    head_name = head_repo.get("nameWithOwner") if isinstance(head_repo, Mapping) else None
    if (
        node.get("isCrossRepository") is not False
        or not isinstance(head_name, str)
        or head_name.lower() != pr.repo.lower()
    ):
        return Refusal(
            f"its head is in {head_name or 'another repository'}, not {pr.repo}: "
            "the factory never merges a fork's branch",
            lasting=True,
        )
    if node.get("headRefName") != pr.head_ref:
        return Refusal(f"head branch is {node.get('headRefName') or '?'} on GitHub", refresh=True)
    commit = _head_commit(node)
    if commit is None or str(commit.get("oid") or "").lower() != head:
        return Refusal("GitHub reported the checks of another commit than the head")
    if node.get("mergeable") != "MERGEABLE":
        return Refusal(f"mergeable={node.get('mergeable') or 'UNKNOWN'}")
    if node.get("reviewDecision") == "CHANGES_REQUESTED":
        return Refusal("changes requested on GitHub", lasting=True)
    updated = parse_time(node.get("updatedAt"))
    # Same second counts as later: the poll may have read just before the update.
    if pr.last_polled_at is None or (
        updated is not None and updated.timestamp() >= pr.last_polled_at
    ):
        return Refusal("changed on GitHub since the factory last read it", refresh=True)
    markers = markers_refusal(node, head, trusted_logins)
    if markers is not None:
        return markers
    return checks_refusal(head_checks(node), _contexts_total(node))


__all__ = [
    "AUTO_MERGE_PATH",
    "AUTO_TEXT",
    "CHECKS_MAX",
    "FAILED_RULE",
    "GREEN_CHECKS",
    "INCIDENT_RULES",
    "NUDGE_TOPIC",
    "TRUSTED_ASSOCIATIONS",
    "WAITING_CHECKS",
    "WAITING_RULE",
    "Refusal",
    "Targets",
    "checks_refusal",
    "factory_targets",
    "github_refusal",
    "is_target",
    "local_refusal",
    "markers_refusal",
    "merge_query",
    "merger_on",
    "merges_itself",
    "nudge_merger",
    "policy_text",
    "scope_refusal",
    "targets_of",
]
