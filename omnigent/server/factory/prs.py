"""PR truth for the cockpit, from GitHub only.

The GitHub poller (``background/pr_poller.py``) finds factory PRs by branch
name and refreshes them with one GraphQL query per repo; this module turns
one refreshed PR into its stored row (gates with evidence, head, merge and
close facts), records what changed as events and tells the project lead.
Agents' claims never write these fields, and nothing here writes to GitHub.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryPr, FactoryProject
from omnigent.factory import (
    LABEL_ID,
    LABEL_PROJECT,
    LABEL_ROLE,
    LABEL_STARTED_AT,
    LABEL_STATE,
    ROLE_PROJECT_LEAD,
    STATE_DONE,
)
from omnigent.factory_gates import (
    DEMO_GATE,
    GATE_FAIL,
    READY_GATES,
    GateReport,
    evaluate_snapshot,
    parse_time,
)
from omnigent.server.factory import events, nudge
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.envelope import quote_spoofed_lines
from omnigent.server.factory.mailer import get_mailer
from omnigent.server.factory.stage import derive_stage
from omnigent.stores.factory_store import norm_id
from omnigent.util.session_lifecycle import is_session_closed

_logger = logging.getLogger(__name__)

NUDGE_TOPIC: Final = "prs"

BRANCH_RE: Final = re.compile(r"^factory/(?P<slug>[a-z0-9][a-z0-9-]*)/(?P<task>[A-Za-z0-9._-]+)$")
"""Factory branches: ``factory/<project slug>/<task>``."""

HUMAN_REVIEW_TEXT_MAX: Final = 600
REVIEW_NOTICE_TEXT_MAX: Final = 2000
_TITLE_MAX = 512
_REF_MAX = 255
_POLL_ERROR_MAX = 512

_STATES: Final = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}

_PR_SELECTION: Final = """
      number url title state isDraft createdAt
      headRefOid headRefName baseRefName mergeable
      mergedAt closedAt mergeCommit {{ oid }} mergedBy {{ login }}
      additions deletions changedFiles
      labels(first: 50) {{ nodes {{ name }} }}
      files(first: 100) {{ nodes {{ path }} }}
      commits(last: 20) {{ nodes {{ commit {{ oid committedDate }} }} }}
      head: commits(last: 1) {{ nodes {{ commit {{ oid statusCheckRollup {{
        contexts(first: 100) {{ nodes {{
          __typename
          ... on CheckRun {{
            name status conclusion detailsUrl startedAt completedAt
            isRequired(pullRequestNumber: {number})
          }}
          ... on StatusContext {{
            context state targetUrl createdAt
            isRequired(pullRequestNumber: {number})
          }}
        }} }}
      }} }} }} }}
      comments(last: 100) {{ nodes {{ body createdAt url author {{ login }} }} }}
      reviews(last: 50) {{
        nodes {{ body submittedAt state url author {{ login }} commit {{ oid }} }}
      }}
      closedEvents: timelineItems(last: 1, itemTypes: [CLOSED_EVENT]) {{
        nodes {{ ... on ClosedEvent {{ actor {{ login }} createdAt }} }}
      }}
"""


# ── Branches, subjects and the refresh query ──────────────────


def parse_factory_branch(
    ref: object, slugs: Iterable[str] | None = None
) -> tuple[str, str] | None:
    """
    Parse a factory branch name.

    :param ref: A head branch, e.g. ``"factory/api-status/status-route"``.
    :param slugs: Accept only these project slugs (``None`` accepts any).
    :returns: ``(slug, task)``, e.g. ``("api-status", "status-route")``, or ``None``.
    """
    if not isinstance(ref, str):
        return None
    match = BRANCH_RE.fullmatch(ref)
    if match is None:
        return None
    slug, task = match.group("slug"), match.group("task")
    if slugs is not None and slug not in set(slugs):
        return None
    return slug, task


def pr_subject(repo: str, number: int) -> str:
    """:returns: The event subject of a PR, e.g. ``"pr:caffeinelabs/app#9630"``."""
    return f"pr:{repo}#{number}"


def checks_url(repo: str, number: int) -> str:
    """:returns: The PR's checks page."""
    return f"https://github.com/{repo}/pull/{number}/checks"


def pr_alias(number: int) -> str:
    """:returns: The GraphQL alias of PR *number* in :func:`refresh_query`."""
    return f"pr{int(number)}"


def refresh_query(numbers: Iterable[int]) -> str:
    """
    Build the read-only query that refreshes several PRs of one repo.

    :param numbers: PR numbers, e.g. ``[9630, 9631]``.
    :returns: A query taking ``$owner`` and ``$name``, one alias per PR plus ``rateLimit``.
    """
    parts = [
        f"    {pr_alias(n)}: pullRequest(number: {int(n)}) {{"
        + _PR_SELECTION.format(number=int(n))
        + "    }"
        for n in sorted(set(numbers))
    ]
    return (
        "query($owner: String!, $name: String!) {\n"
        "  rateLimit { remaining resetAt }\n"
        "  repository(owner: $owner, name: $name) {\n" + "\n".join(parts) + "\n  }\n}"
    )


# ── GraphQL → ``gh pr view`` shape ─────────────────────────────


def _nodes(obj: object, key: str) -> list[dict[str, Any]]:
    conn = obj.get(key) if isinstance(obj, Mapping) else None
    nodes = conn.get("nodes") if isinstance(conn, Mapping) else None
    return [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else []


def _login(obj: object, key: str) -> str | None:
    actor = obj.get(key) if isinstance(obj, Mapping) else None
    login = actor.get("login") if isinstance(actor, Mapping) else None
    return login if isinstance(login, str) and login else None


def _rollup_checks(node: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The head's checks as ``{name, state, required, url}``, newest run per name."""
    heads = _nodes(node, "head")
    commit = heads[-1].get("commit") if heads else None
    rollup = commit.get("statusCheckRollup") if isinstance(commit, Mapping) else None
    newest: dict[str, tuple[str, dict[str, Any]]] = {}
    for ctx in _nodes(rollup, "contexts"):
        if ctx.get("__typename") == "CheckRun":
            name = ctx.get("name")
            done = ctx.get("status") == "COMPLETED"
            state = (ctx.get("conclusion") or "PENDING") if done else "PENDING"
            # A queued run has no times yet and is the newest run of its name.
            when = ctx.get("completedAt") or ctx.get("startedAt") or "~"
            url = ctx.get("detailsUrl")
        elif ctx.get("__typename") == "StatusContext":
            name = ctx.get("context")
            state = ctx.get("state") or "PENDING"
            when = ctx.get("createdAt") or "~"
            url = ctx.get("targetUrl")
        else:
            continue
        if not isinstance(name, str) or not name:
            continue
        check = {"name": name, "state": state, "required": ctx.get("isRequired") is True}
        if isinstance(url, str):
            check["url"] = url
        previous = newest.get(name)
        if previous is not None:
            check["required"] = check["required"] or previous[1]["required"]
            if str(when) < previous[0]:
                previous[1]["required"] = check["required"]
                continue
        newest[name] = (str(when), check)
    return [check for _, check in newest.values()]


def normalize_pr(node: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Convert a GraphQL pull request to the ``gh pr view --json`` shape.

    :param node: One aliased ``pullRequest`` from :func:`refresh_query`.
    :returns: ``(pr_json, checks)`` ready for
        :func:`~omnigent.factory_gates.evaluate_snapshot`.
    """
    merge_commit = node.get("mergeCommit")
    pr: dict[str, Any] = {
        key: node.get(key)
        for key in (
            "number",
            "url",
            "title",
            "state",
            "isDraft",
            "createdAt",
            "headRefOid",
            "headRefName",
            "baseRefName",
            "mergeable",
            "mergedAt",
            "closedAt",
            "additions",
            "deletions",
            "changedFiles",
        )
    }
    pr["mergeCommit"] = merge_commit if isinstance(merge_commit, Mapping) else None
    pr["mergedBy"] = {"login": _login(node, "mergedBy")} if _login(node, "mergedBy") else None
    pr["labels"] = [{"name": n.get("name")} for n in _nodes(node, "labels")]
    pr["files"] = [{"path": n.get("path")} for n in _nodes(node, "files")]
    pr["commits"] = [
        {"oid": c.get("oid"), "committedDate": c.get("committedDate")}
        for n in _nodes(node, "commits")
        if isinstance(c := n.get("commit"), Mapping)
    ]
    pr["comments"] = _nodes(node, "comments")
    pr["reviews"] = _nodes(node, "reviews")
    closed = _nodes(node, "closedEvents")
    pr["closedBy"] = _login(closed[-1], "actor") if closed else None
    return pr, _rollup_checks(node)


# ── Server notices (spec §10 formats) ──────────────────────────


def _sha7(sha: str | None) -> str:
    return (sha or "")[:7] or "?"


def _teardown(preview: Mapping[str, Any] | None, number: int) -> str:
    if not isinstance(preview, Mapping) or preview.get("state") in (None, "down"):
        return ""
    return f" · tear down preview p{number}"


def ready_notice(pr: FactoryPr, gate_count: int, *, for_lead: bool = False) -> str:
    """``[Factory notice · PR <repo>#<n> READY FOR HUMAN MERGE · 6/6 gates · head <sha7>]``."""
    head = (
        f"[Factory notice · PR {pr.repo}#{pr.number} READY FOR HUMAN MERGE · "
        f"{gate_count}/{gate_count} gates · head {_sha7(pr.head_sha)}]"
    )
    if for_lead:
        project = pr.project_slug or "?"
        return (
            f"{head} Info only (project {project}): the cockpit lists it for the human. {pr.url}"
        )
    return f"{head} A human merges it on GitHub; keep its preview up and do not merge it. {pr.url}"


def checks_failed_notice(pr: FactoryPr, names: Sequence[str]) -> str:
    """``[Factory notice · PR <repo>#<n> checks failed: <names> (head <sha7>)]``."""
    listed = ", ".join(names) if names else "no check passed (all skipped)"
    return (
        f"[Factory notice · PR {pr.repo}#{pr.number} checks failed: {listed} "
        f"(head {_sha7(pr.head_sha)})] Dispatch a fix on {pr.head_ref or 'the branch'}. "
        f"{checks_url(pr.repo, pr.number)}"
    )


def merged_notice(pr: FactoryPr, *, for_lead: bool = False) -> str:
    """``[Factory notice · PR <repo>#<n> merged by @<login> as <sha7> · <url>]``.

    With a live preview the header ends ``· tear down preview p<n>``.
    """
    head = (
        f"[Factory notice · PR {pr.repo}#{pr.number} merged by @{pr.merged_by or 'unknown'} "
        f"as {_sha7(pr.merged_sha)} · {pr.url}"
    )
    if for_lead:
        project = pr.project_slug or "?"
        return (
            f"{head}] Info only (project {project}): its project lead runs the post-merge steps."
        )
    teardown = _teardown(pr.preview, pr.number)
    env_step = f"env_keeper MERGED pr={pr.number}" + (
        " (tear down its preview)" if teardown else ""
    )
    return (
        f"{head}{teardown}] Run your post-merge steps: {env_step}, sync sibling worktrees "
        "(git merge origin/main), post the milestone to your lead."
    )


def closed_notice(pr: FactoryPr, closed_by: str | None, decision_ref: str | None) -> str:
    """``[Factory notice · PR <repo>#<n> closed without merge by @<login>]``.

    With a live preview the header ends ``· tear down preview p<n>``.
    """
    teardown = _teardown(pr.preview, pr.number)
    head = (
        f"[Factory notice · PR {pr.repo}#{pr.number} closed without merge by "
        f"@{closed_by or 'unknown'}{teardown}]"
    )
    steps = f" Send env_keeper CLOSED pr={pr.number}." if teardown else ""
    ask = (
        f" {decision_ref} asks the human whether to abandon the task or rework it; "
        "do not reopen or replace the PR until it is answered."
        if decision_ref
        else ""
    )
    return f"{head}{steps}{ask}"


def changes_requested_notice(number: int, sha: str, by: str, text: str) -> str:
    """``[Factory review · CHANGES REQUESTED on PR #<n> @<sha7> by <human> (GitHub)]`` + text."""
    body = quote_spoofed_lines(text[:REVIEW_NOTICE_TEXT_MAX])
    header = f"[Factory review · CHANGES REQUESTED on PR #{number} @{_sha7(sha)} by {by} (GitHub)]"
    return f"{header}\n{body}"


# ── Factory scope ──────────────────────────────────────────────


@dataclass
class FactoryScope:
    """
    One active factory as the poller and prober see it.

    :param root: The root lead session.
    :param started_at: Epoch seconds; PRs created before it are not discovered.
    :param owner: The root's owner (whose GitHub token is used), or ``None``.
    :param projects: Project rows by slug.
    """

    root: Conversation
    started_at: int
    owner: str | None
    projects: dict[str, FactoryProject] = field(default_factory=dict)

    @property
    def factory_id(self) -> str:
        """:returns: The root session id."""
        return self.root.id


def factory_owner(deps: FactoryDeps, factory_id: str) -> str | None:
    """
    :returns: The factory root's owner, the local user in single-user mode,
        or ``None`` when no owner grant exists.
    """
    from omnigent.server.auth import RESERVED_USER_LOCAL
    from omnigent.server.routes._auth_helpers import get_session_owner_id

    if deps.permission_store is None:
        return RESERVED_USER_LOCAL
    return get_session_owner_id(factory_id, deps.permission_store)


def active_factories(deps: FactoryDeps) -> list[FactoryScope]:
    """
    Every factory whose root is open and not ``done``, with its projects.

    :param deps: The factory deps.
    :returns: Scopes, oldest root first.
    """
    store = deps.conversation_store
    ids = store.list_conversation_ids_with_label(LABEL_ID)
    rows = store.get_conversations(ids)
    scopes: list[FactoryScope] = []
    for conv_id in ids:
        root = rows.get(conv_id)
        if root is None or root.labels.get(LABEL_ID) != root.id:
            continue
        if root.labels.get(LABEL_STATE) == STATE_DONE or is_session_closed(
            root.labels, root.title
        ):
            continue
        try:
            started_at = int(root.labels.get(LABEL_STARTED_AT) or root.created_at)
        except ValueError:
            started_at = int(root.created_at)
        projects = {p.slug: p for p in deps.factory_store.list_projects(root.id)}
        scopes.append(
            FactoryScope(
                root=root,
                started_at=started_at,
                owner=factory_owner(deps, root.id),
                projects=projects,
            )
        )
    scopes.sort(key=lambda s: s.root.created_at)
    return scopes


def project_lead_id(
    deps: FactoryDeps, factory_id: str, project: FactoryProject | None
) -> str | None:
    """
    :returns: The project's PL session: the linked one, else the active
        top-level member labelled with the project (newest first).
    """
    if project is None:
        return None
    if project.pl_session_id:
        return project.pl_session_id
    store = deps.conversation_store
    ids = store.list_conversation_ids_with_label(LABEL_PROJECT, project.slug)
    rows = store.get_conversations(ids)
    leads = [
        rows[i]
        for i in ids
        if i in rows
        and rows[i].parent_conversation_id is None
        and rows[i].labels.get(LABEL_ID) == factory_id
        and rows[i].labels.get(LABEL_ROLE) == ROLE_PROJECT_LEAD
    ]
    leads.sort(key=lambda c: (c.labels.get(LABEL_STATE) == STATE_DONE, -c.created_at))
    return leads[0].id if leads else None


def previews_on(project: FactoryProject | None) -> bool:
    """:returns: Whether the project's charter turns factory previews (and demos) on."""
    charter = project.charter if project is not None else None
    if not isinstance(charter, Mapping) or charter.get("env_needed") is False:
        return False
    preview = charter.get("preview")
    return isinstance(preview, Mapping) and preview.get("enabled") is True


# ── Applying one refresh ───────────────────────────────────────


@dataclass
class _Outcome:
    """What one refresh changed: events to emit and notices to send."""

    events: list[dict[str, Any]] = field(default_factory=list)
    notices: list[tuple[str, str]] = field(default_factory=list)
    open_closed_decision: bool = False
    closed_by: str | None = None


def _epoch(value: object) -> int | None:
    when = parse_time(value)
    return int(when.timestamp()) if when is not None else None


def _clip(value: object, limit: int) -> str:
    text = value if isinstance(value, str) else ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _changes_request(
    pr_json: Mapping[str, Any], row: FactoryPr, head: str
) -> tuple[dict[str, Any], str] | None:
    """The newest GitHub CHANGES_REQUESTED review not yet recorded, as ``(human_review, text)``."""
    newest: tuple[int, Mapping[str, Any]] | None = None
    for review in pr_json.get("reviews") or []:
        if not isinstance(review, Mapping) or review.get("state") != "CHANGES_REQUESTED":
            continue
        at = _epoch(review.get("submittedAt"))
        if at is None or at <= row.created_at:
            continue
        if newest is None or at > newest[0]:
            newest = (at, review)
    if newest is None:
        return None
    at, review = newest
    current = row.human_review or {}
    if isinstance(current.get("at"), int) and current["at"] >= at:
        return None
    commit = review.get("commit")
    sha = commit.get("oid") if isinstance(commit, Mapping) else None
    body = review.get("body")
    text = body if isinstance(body, str) else ""
    record = {
        "verdict": "changes_requested",
        "source": "github",
        "text": _clip(text, HUMAN_REVIEW_TEXT_MAX),
        "by": _login(review, "author") or "unknown",
        "at": at,
        "head_sha": (sha or head).lower(),
    }
    return record, text


def _event(kind: str, summary: str, severity: str = "info", **data: Any) -> dict[str, Any]:
    return {"kind": kind, "summary": summary, "severity": severity, "data": data}


async def apply_refresh(
    app_state: Any,
    row: FactoryPr,
    node: Mapping[str, Any],
    *,
    now: int,
    scope: FactoryScope,
    required_contexts: Iterable[str] = (),
) -> FactoryPr | None:
    """
    Store one refreshed PR and act on what changed.

    Writes the gates (with evidence), head, draft/state, sizes and the
    merge/close facts; then emits ``gate.changed``, ``pr.ready`` /
    ``pr.not_ready``, ``pr.merged``, ``pr.closed`` and ``pr.human_review``,
    sends the PL (and the lead) their notices once per transition, opens a
    ``pr_closed`` decision for a PR closed without merge, and re-derives the
    project's stage.

    :param app_state: ``app.state`` carrying the factory deps.
    :param row: The tracked PR as stored before this refresh.
    :param node: Its ``pullRequest`` from :func:`refresh_query`.
    :param now: Epoch seconds.
    :param scope: The PR's factory.
    :param required_contexts: Status contexts the base branch requires.
    :returns: The updated row (``None`` if it vanished meanwhile).
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    pr_json, checks = normalize_pr(node)
    project = scope.projects.get(row.project_slug or "")
    demo_required = previews_on(project)
    demos: list[dict[str, Any]] = []
    if demo_required:
        rows = await asyncio.to_thread(store.list_demos, row.factory_id, row.number, repo=row.repo)
        demos = [
            {"id": d.id, "head_sha": d.head_sha, "status": d.status, "created_at": d.created_at}
            for d in rows
        ]
    report = evaluate_snapshot(
        pr_json,
        checks,
        required_contexts,
        demo_required=demo_required,
        demos=demos,
        checks_url=checks_url(row.repo, row.number),
    )
    gates = report.to_json()
    gates.setdefault(DEMO_GATE, None)
    head = str(pr_json.get("headRefOid") or "").lower()
    state = _STATES.get(str(pr_json.get("state") or "").upper(), row.state)
    fields: dict[str, Any] = {
        "title": _clip(pr_json.get("title"), _TITLE_MAX) or row.title,
        "head_sha": head or row.head_sha,
        "head_ref": _clip(pr_json.get("headRefName"), _REF_MAX) or row.head_ref,
        "base_ref": _clip(pr_json.get("baseRefName"), _REF_MAX) or row.base_ref,
        "is_draft": pr_json.get("isDraft") is True,
        "state": state,
        "additions": int(pr_json.get("additions") or 0),
        "deletions": int(pr_json.get("deletions") or 0),
        "changed_files": int(pr_json.get("changedFiles") or 0),
        "gates": gates,
        "ready": report.ready,
        "last_polled_at": now,
        "poll_error": None,
    }
    if isinstance(pr_json.get("url"), str) and pr_json["url"]:
        fields["url"] = pr_json["url"]
    if report.ready and not row.ready:
        fields["ready_at"] = now
    out = _Outcome()
    if state == "open":
        _gate_events(row, gates, head, out)
    if state == "merged" and row.merged_at is None:
        merge_commit = pr_json.get("mergeCommit") or {}
        fields["merged_at"] = _epoch(pr_json.get("mergedAt")) or now
        fields["merged_sha"] = str(merge_commit.get("oid") or "").lower() or None
        fields["merged_by"] = _login(pr_json, "mergedBy")
        fields["closed_at"] = _epoch(pr_json.get("closedAt")) or fields["merged_at"]
    elif state == "closed" and row.closed_at is None:
        fields["closed_at"] = _epoch(pr_json.get("closedAt")) or now
        out.open_closed_decision = True
        out.closed_by = pr_json.get("closedBy")
    elif state == "open" and row.state == "closed":
        fields["closed_at"] = None
    review = _changes_request(pr_json, row, head) if state == "open" else None
    if review is not None:
        fields["human_review"] = review[0]
    updated = await asyncio.to_thread(store.update_pr, row.id, **fields)
    if updated is None:
        return None
    await _announce(app_state, deps, scope, row, updated, report, review, out)
    return updated


def _gate_events(row: FactoryPr, gates: Mapping[str, Any], head: str, out: _Outcome) -> None:
    previous = row.gates or {}
    if not any(name in previous for name in READY_GATES):
        return
    for name in (*READY_GATES, DEMO_GATE):
        before, after = previous.get(name), gates.get(name)
        if before != after and after is not None:
            out.events.append(
                _event(
                    "gate.changed",
                    f"PR #{row.number} {name}: {before or 'unknown'} → {after}",
                    gate=name,
                    **{"from": before, "to": after, "head_sha": head},
                )
            )


async def _announce(
    app_state: Any,
    deps: FactoryDeps,
    scope: FactoryScope,
    row: FactoryPr,
    pr: FactoryPr,
    report: GateReport,
    review: tuple[dict[str, Any], str] | None,
    out: _Outcome,
) -> None:
    project = scope.projects.get(pr.project_slug or "")
    pl_id = await asyncio.to_thread(project_lead_id, deps, scope.factory_id, project)
    lead_id = scope.factory_id
    pl_target = pl_id or lead_id
    previous_checks = (row.gates or {}).get("checks")
    checks_now = (pr.gates or {}).get("checks")
    if (
        pr.state == "open"
        and checks_now == GATE_FAIL
        and (previous_checks != GATE_FAIL or row.head_sha != pr.head_sha)
    ):
        names = (report.evidence.get("checks") or {}).get("failing_names") or []
        out.notices.append((pl_target, checks_failed_notice(pr, names)))
    if pr.ready and not row.ready:
        out.events.append(_event("pr.ready", f"PR #{pr.number} ready for human merge", "notice"))
        count = len(report.gates)
        out.notices.append((pl_target, ready_notice(pr, count)))
        if pl_id and pl_id != lead_id:
            out.notices.append((lead_id, ready_notice(pr, count, for_lead=True)))
    elif row.ready and not pr.ready and pr.state == "open":
        reasons = report.reasons[:3]
        out.events.append(
            _event("pr.not_ready", f"PR #{pr.number} no longer ready", "notice", reasons=reasons)
        )
    if pr.state == "merged" and row.merged_at is None:
        out.events.append(
            _event(
                "pr.merged",
                f"PR #{pr.number} merged by @{pr.merged_by or 'unknown'} "
                f"as {_sha7(pr.merged_sha)}",
                "notice",
                merged_sha=pr.merged_sha,
                merged_by=pr.merged_by,
            )
        )
        out.notices.append((pl_target, merged_notice(pr)))
        if pl_id and pl_id != lead_id:
            out.notices.append((lead_id, merged_notice(pr, for_lead=True)))
    decision_ref = None
    if out.open_closed_decision:
        decision_ref = await _open_closed_decision(app_state, deps, pr, pl_id, out.closed_by)
        out.events.append(
            _event(
                "pr.closed",
                f"PR #{pr.number} closed without merge by @{out.closed_by or 'unknown'}",
                "warning",
                closed_by=out.closed_by,
                decision=decision_ref,
            )
        )
        out.notices.append((pl_target, closed_notice(pr, out.closed_by, decision_ref)))
    if review is not None:
        record, text = review
        out.events.append(
            _event(
                "pr.human_review",
                f"PR #{pr.number} changes requested by {record['by']} on GitHub",
                "notice",
                verdict="changes_requested",
                source="github",
                by=record["by"],
                head_sha=record["head_sha"],
            )
        )
        out.notices.append(
            (
                pl_target,
                changes_requested_notice(pr.number, record["head_sha"], record["by"], text),
            )
        )
    subject = pr_subject(pr.repo, pr.number)
    for event in out.events:
        await events.emit(
            app_state,
            scope.factory_id,
            event["kind"],
            severity=event["severity"],
            project=pr.project_slug,
            to_id=pl_id,
            subject=subject,
            summary=event["summary"],
            data={"pr_id": pr.id, **event["data"]},
        )
    mailer = get_mailer(app_state)
    for target, text in out.notices:
        try:
            mailer.notify(target, text)
        except RuntimeError:
            _logger.warning("factory: mailer closed; dropped PR notice for %s", target)
    if pr.project_slug and (
        out.events or row.state != pr.state or row.ready != pr.ready or row.gates != pr.gates
    ):
        await persist_stage(app_state, scope.factory_id, pr.project_slug)


async def _open_closed_decision(
    app_state: Any,
    deps: FactoryDeps,
    pr: FactoryPr,
    pl_id: str | None,
    closed_by: str | None,
) -> str | None:
    target = f"project:{pr.project_slug}" if pr.project_slug else None
    task = pr.task or pr.head_ref or f"#{pr.number}"
    try:
        decision, deduped = await asyncio.to_thread(
            deps.factory_store.create_decision,
            pr.factory_id,
            dedupe_key=f"pr_closed:{pr.id}",
            question=(
                f"PR {pr.repo}#{pr.number} ({task}) was closed without merge by "
                f"@{closed_by or 'unknown'}. What should the factory do?"
            ),
            source="pr_closed",
            blocking=False,
            allow_text=True,
            project_slug=pr.project_slug,
            context=f"{pr.title}\n{pr.url}",
            options=[
                {
                    "id": "abandon",
                    "label": "Abandon task",
                    "description": "Drop the task; the PL tears down its preview.",
                    "action": "abandon_task",
                    "target": target,
                },
                {
                    "id": "rework",
                    "label": "Ask PL to rework",
                    "description": "Tell the PL what to change; it opens a new PR.",
                    "action": "rework",
                    "target": target,
                },
                {"id": "dismiss", "label": "Dismiss", "action": "dismiss"},
            ],
        )
    except Exception:  # noqa: BLE001 - the close notice still goes out
        _logger.warning(
            "factory: could not open the pr_closed decision for %s", pr.id, exc_info=True
        )
        return None
    if not deduped:
        await events.emit(
            app_state,
            pr.factory_id,
            "decision.opened",
            severity="notice",
            project=pr.project_slug,
            to_id=pl_id,
            subject=f"decision:{decision.ref}",
            summary=f"{decision.ref}: PR #{pr.number} closed without merge",
            data={"decision_id": decision.id, "source": "pr_closed", "pr_id": pr.id},
        )
    return decision.ref


async def persist_stage(app_state: Any, factory_id: str, slug: str) -> str | None:
    """
    Re-derive a project's stage from its PRs and store it when it changed.

    Emits ``stage.changed`` on a change.

    :returns: The current stage, or ``None`` when the project is unknown.
    """
    deps = factory_deps(app_state)
    store = deps.factory_store

    def _derive() -> tuple[str, str] | None:
        project = store.get_project(factory_id, slug)
        if project is None:
            return None
        prs = [p for p in store.list_prs(factory_id) if p.project_slug == slug]
        pl_id = project_lead_id(deps, factory_id, project)
        pl_conv = deps.conversation_store.get_conversation(pl_id) if pl_id else None
        open_decisions = store.list_decisions(factory_id=factory_id, status="open")
        result = derive_stage(project, prs, pl_conv, open_decisions)
        if result.stage != project.stage:
            store.update_project(project.id, stage=result.stage)
        return project.stage, result.stage

    try:
        derived = await asyncio.to_thread(_derive)
    except Exception:  # noqa: BLE001 - the next refresh retries
        _logger.warning(
            "factory: could not derive the stage of %s/%s", factory_id, slug, exc_info=True
        )
        return None
    if derived is None:
        return None
    before, after = derived
    if before != after:
        await events.emit(
            app_state,
            factory_id,
            "stage.changed",
            project=slug,
            subject=f"project:{slug}",
            summary=f"{slug}: {before} → {after}",
            data={"from": before, "to": after},
        )
    return after


async def refresh_now(app_state: Any, pr_id: str) -> bool:
    """
    Make the poller refresh one PR at once (the cockpit's "refresh" and ``sys_factory_pr``).

    Clears ``last_polled_at`` (due now) and nudges the poller.

    :returns: ``False`` when the PR is unknown.
    """
    store = factory_deps(app_state).factory_store
    try:
        key = norm_id(pr_id)
    except ValueError:
        return False
    updated = await asyncio.to_thread(store.update_pr, key, last_polled_at=None)
    if updated is None:
        return False
    nudge.nudge(NUDGE_TOPIC, key)
    return True


__all__ = [
    "BRANCH_RE",
    "NUDGE_TOPIC",
    "FactoryScope",
    "active_factories",
    "apply_refresh",
    "changes_requested_notice",
    "checks_failed_notice",
    "closed_notice",
    "merged_notice",
    "normalize_pr",
    "parse_factory_branch",
    "persist_stage",
    "pr_alias",
    "pr_subject",
    "previews_on",
    "project_lead_id",
    "ready_notice",
    "refresh_now",
    "refresh_query",
]
