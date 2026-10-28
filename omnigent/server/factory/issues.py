"""Factory issues: incidents (``INC-n``), findings (``F-n``) and retros, one table.

Detectors raise and auto-resolve issues through :func:`raise_issue` and
:func:`clear_issue` (deduped on ``rule:target``); people move incidents
through their lifecycle (:func:`ack`, :func:`assign`, :func:`snooze`,
:func:`resolve`, EDIT on the root) and anyone who can read the factory files
one with :func:`report` ("Report a problem").

Routing: the snapshot's ``counts.issues_open`` badge, ``mine`` for the
viewer's notifications, and an optional Slack webhook per factory. The webhook
is a secret: owners set it, it lives in the server's artifact store (a path no
route serves), and no read route, snapshot, event or agent ever sees it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import deque
from collections.abc import Mapping
from typing import Any, Final

import httpx

from omnigent.entities.factory import FactoryIssue
from omnigent.factory import ISSUE_OPEN_STATUSES, ROOT_CAUSES
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE, LEVEL_READ
from omnigent.server.factory import events, live, people
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import (
    CockpitError,
    find_project_pl,
    human_label,
    load_factory_root,
    require,
    same_id,
)
from omnigent.stores.factory_store import norm_id

_logger = logging.getLogger(__name__)

INCIDENT: Final = "incident"
FINDING: Final = "finding"
RETRO: Final = "retro"
HUMAN_RULE: Final = "human.report"
SCOPE: Final = "issues"
"""The frame scope an issue change announces."""

ASSIGN_OWNERS: Final = "owners"
ASSIGN_OPERATORS: Final = "operators"
SNOOZE_MIN_M: Final = 5
SNOOZE_MAX_M: Final = 7 * 24 * 60
NOTE_MAX: Final = 2000
SUMMARY_MAX: Final = 300
_DEDUPE_MAX: Final = 96
_REF_RE: Final = re.compile(r"^(INC|F)-([1-9][0-9]{0,8})$")
_CLASS_BY_PREFIX: Final = {"INC": INCIDENT, "F": FINDING}

SLACK_KEY_TEMPLATE: Final = "factory/secrets/{fid}/slack_webhook"
SLACK_HOSTS: Final = ("https://hooks.slack.com/", "https://hooks.slack-gov.com/")
SLACK_TIMEOUT_S: Final = 5.0
_SLACK_CACHE_S: Final = 60.0
SLACK_MIN_SEVERITY: Final = "warning"
SLACK_REPOST_S: Final = 3600.0
"""An incident that reopens is posted to Slack again at most this often."""
_SEVERITY_RANK: Final = {"info": 0, "notice": 1, "warning": 2, "critical": 3}
REPORTS_PER_HOUR: Final = 10
""""Report a problem" filings one person may make per factory and hour."""


def dedupe_key(rule: str, target: str) -> str:
    """:returns: ``<rule>:<target>``, hashed when longer than the column (96)."""
    key = f"{rule}:{target}"
    if len(key) <= _DEDUPE_MAX:
        return key
    return f"{rule[:40]}:{hashlib.sha1(key.encode()).hexdigest()}"


def _now() -> int:
    return int(time.time())


def _issue_data(issue: FactoryIssue, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "issue_id": issue.id,
        "ref": issue.ref,
        "class": issue.class_,
        "rule": issue.rule,
        "target": issue.target,
        "severity": issue.severity,
        "status": issue.status,
        "count": issue.count,
    }
    data.update({k: v for k, v in extra.items() if v is not None})
    return data


def _event_severity(issue: FactoryIssue) -> str:
    return issue.severity if issue.class_ == INCIDENT else "info"


def _subject(issue: FactoryIssue) -> str:
    return f"issue:{issue.ref or issue.id}"


async def _emit(app_state: Any, issue: FactoryIssue, kind: str, summary: str, **data: Any) -> None:
    await events.emit(
        app_state,
        issue.factory_id,
        kind,
        severity=_event_severity(issue),
        project=issue.project_slug,
        subject=_subject(issue),
        summary=summary,
        data=_issue_data(issue, **data),
    )
    live.announce_factory_changed(app_state, issue.factory_id, [SCOPE])


def _label(issue: FactoryIssue) -> str:
    return issue.ref or f"{issue.class_} {issue.id[:8]}"


# ── Detector side ──────────────────────────────────────────────


async def raise_issue(
    app_state: Any,
    factory_id: str,
    *,
    rule: str,
    target: str,
    summary: str,
    class_: str = INCIDENT,
    severity: str = "warning",
    project: str | None = None,
    evidence: Any = None,
    body: str | None = None,
    source: str = "detector",
    category: str | None = None,
    assignee: str | None = None,
    by: Mapping[str, Any] | None = None,
) -> FactoryIssue | None:
    """
    Open (or re-raise) the issue of *rule* on *target*. Never raises.

    A re-raise bumps ``count`` and ``last_seen``; a resolved one reopens and,
    like a new one, emits ``issue.opened`` and is routed (Slack for incidents
    of warning or worse). ``wontfix`` stays closed.

    :param target: e.g. ``"member:<id>"``, ``"pr:<id>"``, ``"project:<slug>"``.
    :param by: Attribution for the event (``by``, ``by_kind``, ``by_role``); the server by default.
    :returns: The stored row, or ``None`` when it could not be stored.
    """
    deps = factory_deps(app_state)
    key = dedupe_key(rule, target)
    try:
        before = await asyncio.to_thread(_find_by_key, deps, factory_id, key)
        fields: dict[str, Any] = {
            "severity": severity,
            "source": source,
            "project_slug": project,
            "evidence": evidence,
        }
        if body is not None:
            fields["body"] = body
        if category is not None:
            fields["category"] = category
        if before is None and assignee is not None:
            fields["assignee"] = assignee
        issue, created = await asyncio.to_thread(
            lambda: deps.factory_store.upsert_issue(
                factory_id,
                class_=class_,
                rule=rule,
                target=target,
                summary=summary,
                dedupe_key=key,
                **fields,
            )
        )
    except Exception:  # noqa: BLE001 - a detector hiccup must not break its caller
        _logger.warning("factory: could not raise %s on %s", rule, target, exc_info=True)
        return None
    reopened = before is not None and before.status not in ISSUE_OPEN_STATUSES
    reopened = reopened and issue.status in ISSUE_OPEN_STATUSES
    attribution = dict(by or {"by": "server", "by_kind": "server"})
    if created or reopened:
        await _emit(
            app_state,
            issue,
            "issue.opened",
            f"{_label(issue)} {issue.summary}",
            reopened=reopened or None,
            **attribution,
        )
        await route_opened(app_state, issue)
    else:
        live.announce_factory_changed(app_state, factory_id, [SCOPE])
    return issue


def _find_by_key(deps: FactoryDeps, factory_id: str, key: str) -> FactoryIssue | None:
    return deps.factory_store.get_issue_by_dedupe(factory_id, key)


def open_issue_for(
    deps: FactoryDeps, factory_id: str, rule: str, target: str
) -> FactoryIssue | None:
    """:returns: The open (or acked or snoozed) issue of *rule* on *target*, if any."""
    found = _find_by_key(deps, factory_id, dedupe_key(rule, target))
    return found if found is not None and found.status in ISSUE_OPEN_STATUSES else None


def open_issues(deps: FactoryDeps, factory_id: str, rule: str | None = None) -> list[FactoryIssue]:
    """:returns: Open, acked and snoozed issues (of *rule*, or of every rule)."""
    rows = deps.factory_store.list_issues(
        factory_id, statuses=sorted(ISSUE_OPEN_STATUSES), limit=2000
    )
    return [r for r in rows if rule is None or r.rule == rule]


async def clear_issue(
    app_state: Any, factory_id: str, rule: str, target: str, *, reason: str | None = None
) -> FactoryIssue | None:
    """
    Auto-resolve the issue of *rule* on *target* now that its condition cleared.

    :returns: The resolved row, or ``None`` when none was open. Never raises.
    """
    deps = factory_deps(app_state)
    try:
        issue = await asyncio.to_thread(open_issue_for, deps, factory_id, rule, target)
        if issue is None:
            return None
        updated = await asyncio.to_thread(
            deps.factory_store.update_issue,
            issue.id,
            status="resolved",
            resolved_at=_now(),
            resolved_by="server",
        )
    except Exception:  # noqa: BLE001 - retried on the next detector pass
        _logger.warning("factory: could not clear %s on %s", rule, target, exc_info=True)
        return None
    if updated is None:
        return None
    await _emit(
        app_state,
        updated,
        "issue.resolved",
        f"{_label(updated)} resolved: {reason or 'condition cleared'}",
        by="server",
        by_kind="server",
        reason=reason,
    )
    return updated


async def unsnooze_due(app_state: Any, factory_id: str, now: float) -> int:
    """Reopen snoozed issues whose snooze ran out. :returns: How many reopened."""
    deps = factory_deps(app_state)
    rows = await asyncio.to_thread(
        deps.factory_store.list_issues, factory_id, statuses=["snoozed"], limit=500
    )
    woke = 0
    for issue in rows:
        if issue.snoozed_until is not None and issue.snoozed_until <= now:
            await asyncio.to_thread(
                deps.factory_store.update_issue, issue.id, status="open", snoozed_until=None
            )
            woke += 1
    if woke:
        live.announce_factory_changed(app_state, factory_id, [SCOPE])
    return woke


# ── Lookup and views ───────────────────────────────────────────


def find_issue(deps: FactoryDeps, factory_id: str, ref: str) -> FactoryIssue | None:
    """
    :param ref: ``INC-12``, ``F-3`` or the issue's row id.
    :returns: The issue of this factory, or ``None``.
    """
    ref = (ref or "").strip()
    match = _REF_RE.match(ref.upper())
    if match:
        return deps.factory_store.get_issue_by_number(
            factory_id, _CLASS_BY_PREFIX[match.group(1)], int(match.group(2))
        )
    try:
        issue = deps.factory_store.get_issue(ref)
    except ValueError:
        return None
    return issue if issue is not None and same_id(issue.factory_id, factory_id) else None


def is_mine(issue: FactoryIssue, user_id: str | None, level: int) -> bool:
    """
    Whether *issue* is for this viewer: assigned to them, or to their role.

    Unassigned issues are the operators'; owners also get critical incidents.
    """
    if issue.status not in ISSUE_OPEN_STATUSES or issue.status == "snoozed":
        return False
    assignee = issue.assignee
    if assignee == ASSIGN_OWNERS:
        return level >= LEVEL_MANAGE
    if assignee in (None, "", ASSIGN_OPERATORS):
        return level >= LEVEL_EDIT or (issue.severity == "critical" and level >= LEVEL_MANAGE)
    return user_id is not None and assignee == user_id


def counts_open(rows: list[FactoryIssue], hidden_projects: set[str] | frozenset[str]) -> int:
    """:returns: The badge count: open or acked incidents of visible projects."""
    return sum(
        1
        for r in rows
        if r.class_ == INCIDENT
        and r.status in ("open", "acked")
        and (not r.project_slug or r.project_slug not in hidden_projects)
    )


def visible_to(
    project: str | None,
    target: str,
    *,
    hidden: set[str] | frozenset[str],
    top_level_of: Mapping[str, str],
    readable: set[str] | frozenset[str],
) -> bool:
    """
    Whether a viewer may see an issue: its project and its agent are readable.

    :param top_level_of: Session id -> its top-level member (the snapshot's).
    :param readable: Session ids the viewer reads.
    """
    if project and project in hidden:
        return False
    kind, _, ident = target.partition(":")
    if kind != "member":
        return True
    try:
        sid = norm_id(ident)
    except ValueError:
        return True
    members = {norm_id(s): s for s in top_level_of}
    return sid not in members or members[sid] in readable


# ── Human lifecycle ────────────────────────────────────────────


async def _load_for_edit(
    app_state: Any, factory_id: str, ref: str, user_id: str | None
) -> tuple[FactoryDeps, FactoryIssue, dict[str, Any]]:
    deps = factory_deps(app_state)
    root = await load_factory_root(deps, user_id, factory_id)
    await require(deps, user_id, root.id, LEVEL_EDIT)
    issue = await asyncio.to_thread(find_issue, deps, root.id, ref)
    if issue is None:
        raise CockpitError(404, "unknown_issue", f"no issue {ref!r} in this factory")
    by = await human_label(deps, root.id, user_id)
    role = await asyncio.to_thread(people.role_of, deps, root.id, user_id)
    return deps, issue, {"by": by, "by_kind": "human", "by_role": role, "by_user": user_id}


def _require_open(issue: FactoryIssue) -> None:
    if issue.status not in ISSUE_OPEN_STATUSES:
        raise CockpitError(409, "issue_closed", f"{_label(issue)} is {issue.status}")


async def ack(app_state: Any, factory_id: str, ref: str, *, user_id: str | None) -> FactoryIssue:
    """Acknowledge an open issue (someone is on it)."""
    deps, issue, by = await _load_for_edit(app_state, factory_id, ref, user_id)
    _require_open(issue)
    updated = await asyncio.to_thread(
        deps.factory_store.update_issue,
        issue.id,
        status="acked",
        acked_by=user_id or by["by"],
        snoozed_until=None,
    )
    assert updated is not None
    await _emit(
        app_state, updated, "issue.acked", f"{by['by']} acknowledged {_label(updated)}", **by
    )
    return updated


async def _assignee(deps: FactoryDeps, factory_id: str, to: str | None) -> str | None:
    if to in (None, "", ASSIGN_OPERATORS):
        return None if to in (None, "") else ASSIGN_OPERATORS
    if to == ASSIGN_OWNERS:
        return to
    rows = await asyncio.to_thread(deps.factory_store.list_people, factory_id)
    known = {r.user_id for r in rows} | {people.creator_of(deps, factory_id)}
    if to not in known:
        raise CockpitError(
            400, "unknown_assignee", f"{to!r} is not on this factory's roster", field="to"
        )
    return to


async def assign(
    app_state: Any, factory_id: str, ref: str, *, user_id: str | None, to: str | None
) -> FactoryIssue:
    """Assign an issue to a person on the roster, ``operators`` or ``owners``."""
    deps, issue, by = await _load_for_edit(app_state, factory_id, ref, user_id)
    _require_open(issue)
    assignee = await _assignee(deps, issue.factory_id, to)
    updated = await asyncio.to_thread(deps.factory_store.update_issue, issue.id, assignee=assignee)
    assert updated is not None
    whom = assignee or ASSIGN_OPERATORS
    await _emit(
        app_state,
        updated,
        "issue.assigned",
        f"{by['by']} assigned {_label(updated)} to {whom}",
        assignee=whom,
        **by,
    )
    return updated


async def snooze(
    app_state: Any,
    factory_id: str,
    ref: str,
    *,
    user_id: str | None,
    minutes: int | None = None,
    until: int | None = None,
) -> FactoryIssue:
    """Snooze an issue for *minutes* (5 min to 7 days) or until an epoch second."""
    deps, issue, by = await _load_for_edit(app_state, factory_id, ref, user_id)
    _require_open(issue)
    now = _now()
    if until is None:
        if minutes is None:
            raise CockpitError(400, "invalid_snooze", "give minutes or until", field="minutes")
        until = now + int(minutes) * 60
    span_m = (until - now) / 60
    if span_m < SNOOZE_MIN_M or span_m > SNOOZE_MAX_M:
        raise CockpitError(
            400,
            "invalid_snooze",
            f"snooze between {SNOOZE_MIN_M} minutes and 7 days",
            field="minutes",
        )
    updated = await asyncio.to_thread(
        deps.factory_store.update_issue, issue.id, status="snoozed", snoozed_until=int(until)
    )
    assert updated is not None
    await _emit(
        app_state,
        updated,
        "issue.snoozed",
        f"{by['by']} snoozed {_label(updated)} for {round(span_m)} min",
        snoozed_until=int(until),
        **by,
    )
    return updated


async def resolve(
    app_state: Any,
    factory_id: str,
    ref: str,
    *,
    user_id: str | None,
    note: str | None = None,
    root_cause: str | None = None,
    wontfix: bool = False,
) -> FactoryIssue:
    """
    Resolve an issue (or close it as ``wontfix``), optionally confirming its root cause.

    A confirmed root cause is recorded as ``root_cause`` feedback on the issue.
    """
    if note is not None and len(note) > NOTE_MAX:
        raise CockpitError(400, "note_too_long", f"note exceeds {NOTE_MAX} chars", field="note")
    if root_cause is not None and root_cause not in ROOT_CAUSES:
        raise CockpitError(
            400,
            "invalid_root_cause",
            f"root_cause must be one of {list(ROOT_CAUSES)}",
            field="root_cause",
        )
    deps, issue, by = await _load_for_edit(app_state, factory_id, ref, user_id)
    _require_open(issue)
    fields: dict[str, Any] = {
        "status": "wontfix" if wontfix else "resolved",
        "resolved_at": _now(),
        "resolved_by": user_id or by["by"],
        "snoozed_until": None,
    }
    if root_cause is not None:
        fields["category"] = root_cause
    updated = await asyncio.to_thread(deps.factory_store.update_issue, issue.id, **fields)
    assert updated is not None
    if root_cause is not None:
        await asyncio.to_thread(
            lambda: deps.factory_store.add_feedback(
                issue.factory_id,
                target_kind="issue",
                target_id=issue.id,
                author_kind="human",
                author=user_id or by["by"],
                kind="root_cause",
                project_slug=issue.project_slug,
                tags=[root_cause],
                body=note,
            )
        )
    word = "closed as won't fix" if wontfix else "resolved"
    await _emit(
        app_state,
        updated,
        "issue.resolved",
        f"{by['by']} {word} {_label(updated)}",
        note=note,
        root_cause=root_cause,
        **by,
    )
    return updated


async def report(
    app_state: Any,
    factory_id: str,
    *,
    user_id: str | None,
    summary: str,
    body: str | None = None,
    target: str | None = None,
    class_: str = INCIDENT,
    severity: str = "notice",
    project: str | None = None,
) -> FactoryIssue:
    """
    "Report a problem": anyone who can read the factory files an issue.

    :raises CockpitError: 400 on a bad class, severity, summary or body.
    """
    summary = " ".join((summary or "").split())
    if not summary:
        raise CockpitError(400, "invalid_summary", "summary is required", field="summary")
    if len(summary) > SUMMARY_MAX:
        raise CockpitError(400, "invalid_summary", f"summary exceeds {SUMMARY_MAX} chars")
    if body is not None and len(body) > NOTE_MAX * 5:
        raise CockpitError(400, "body_too_long", "body exceeds 10000 chars", field="body")
    if class_ not in (INCIDENT, FINDING):
        raise CockpitError(
            400, "invalid_class", "class must be incident or finding", field="class"
        )
    if severity not in ("info", "notice", "warning"):
        raise CockpitError(
            400, "invalid_severity", "severity must be info, notice or warning", field="severity"
        )
    deps = factory_deps(app_state)
    root = await load_factory_root(deps, user_id, factory_id)
    await require(deps, user_id, root.id, LEVEL_READ)
    _take_report_slot(root.id, user_id)
    if project:
        row = await asyncio.to_thread(deps.factory_store.get_project, root.id, project)
        pl = row or await asyncio.to_thread(
            find_project_pl, deps, root.id, project, include_done=True
        )
        if pl is None:
            raise CockpitError(404, "unknown_project", f"no project {project!r}", field="project")
    by = await human_label(deps, root.id, user_id)
    role = await asyncio.to_thread(people.role_of, deps, root.id, user_id)
    issue, _ = await asyncio.to_thread(
        lambda: deps.factory_store.upsert_issue(
            root.id,
            class_=class_,
            rule=HUMAN_RULE,
            target=(target or "").strip()[:128]
            or (f"project:{project}" if project else "factory"),
            summary=summary,
            severity=severity,
            source="human",
            project_slug=project or None,
            body=body,
            evidence={"reported_by": user_id or by},
        )
    )
    await _emit(
        app_state,
        issue,
        "issue.opened",
        f"{by} reported {_label(issue)}: {summary}",
        by=by,
        by_kind="human",
        by_role=role,
        by_user=user_id,
    )
    await route_opened(app_state, issue)
    return issue


_reports: dict[tuple[str, str], deque[float]] = {}


def _take_report_slot(factory_id: str, user_id: str | None) -> None:
    """Count one report against its filer. :raises CockpitError: 429 past the hourly limit."""
    now = time.monotonic()
    window = _reports.setdefault((norm_id(factory_id), user_id or ""), deque())
    while window and now - window[0] >= 3600:
        window.popleft()
    if len(window) >= REPORTS_PER_HOUR:
        retry = int(3600 - (now - window[0])) + 1
        raise CockpitError(
            429,
            "rate_limited",
            f"at most {REPORTS_PER_HOUR} reports per hour",
            retry_after=retry,
        )
    window.append(now)


# ── Slack webhook (owners set it; nothing ever reads it back) ──

_slack_cache: dict[str, tuple[float, str | None]] = {}


def _secret_store(deps: FactoryDeps) -> Any:
    return deps.artifact_store or deps.factory_artifact_store


def _slack_key(factory_id: str) -> str:
    return SLACK_KEY_TEMPLATE.format(fid=norm_id(factory_id))


def valid_webhook(url: str) -> bool:
    """:returns: Whether *url* is a Slack incoming-webhook URL (the only host we post to)."""
    return (
        isinstance(url, str)
        and url.startswith(SLACK_HOSTS)
        and len(url) <= 512
        and not any(c.isspace() for c in url)
    )


def slack_webhook(deps: FactoryDeps, factory_id: str) -> str | None:
    """:returns: The factory's Slack webhook (server side only), cached for a minute."""
    key = norm_id(factory_id)
    cached = _slack_cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < _SLACK_CACHE_S:
        return cached[1]
    store = _secret_store(deps)
    value: str | None = None
    if store is not None:
        try:
            if store.exists(_slack_key(key)):
                value = store.get(_slack_key(key)).decode().strip() or None
        except Exception:  # noqa: BLE001 - routing degrades to the cockpit only
            _logger.warning("factory: could not read the Slack webhook of %s", key)
    _slack_cache[key] = (time.monotonic(), value)
    return value


async def set_slack_webhook(
    app_state: Any, factory_id: str, *, user_id: str | None, url: str | None
) -> bool:
    """
    Set or clear the factory's Slack webhook (MANAGE on the root).

    :returns: Whether a webhook is configured afterwards.
    :raises CockpitError: 400 on a URL that is not a Slack webhook; 409 without a store.
    """
    deps = factory_deps(app_state)
    root = await load_factory_root(deps, user_id, factory_id)
    await require(deps, user_id, root.id, LEVEL_MANAGE)
    url = (url or "").strip() or None
    if url is not None and not valid_webhook(url):
        raise CockpitError(
            400, "invalid_webhook", "url must be a https://hooks.slack.com/ webhook", field="url"
        )
    store = _secret_store(deps)
    if store is None:
        raise CockpitError(409, "no_secret_store", "this server has no artifact store")
    key = _slack_key(root.id)
    if url is None:
        if await asyncio.to_thread(store.exists, key):
            await asyncio.to_thread(store.delete, key)
    else:
        await asyncio.to_thread(store.put, key, url.encode())
    _slack_cache.pop(norm_id(root.id), None)
    by = await human_label(deps, root.id, user_id)
    await events.emit(
        app_state,
        root.id,
        "alerts.routing",
        severity="notice",
        subject="alerts:slack",
        summary=f"{by} {'set' if url else 'removed'} the Slack alert webhook",
        data={"by": by, "by_kind": "human", "slack": url is not None},
    )
    return url is not None


def routing_view(deps: FactoryDeps, factory_id: str) -> dict[str, Any]:
    """:returns: ``{cockpit, browser, slack}``; ``slack`` says only whether one is set."""
    return {
        "cockpit": True,
        "browser": True,
        "slack": slack_webhook(deps, factory_id) is not None,
        "slack_min_severity": SLACK_MIN_SEVERITY,
    }


def slack_escape(text: str) -> str:
    """:returns: *text* with Slack's control characters escaped (no mentions or links)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_text(issue: FactoryIssue, factory_name: str) -> str:
    """:returns: The one-line Slack message of an opened incident."""
    parts = (f"[{factory_name}]", _label(issue), issue.severity, issue.rule, issue.summary)
    head, *rest = (slack_escape(p) for p in parts)
    return f"{head} {' · '.join(rest)}"


async def _post_slack(url: str, text: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=SLACK_TIMEOUT_S, follow_redirects=False) as client:
            resp = await client.post(url, json={"text": text})
        if resp.status_code >= 400:
            _logger.warning("factory: Slack alert refused (%s)", resp.status_code)
    except Exception as exc:  # noqa: BLE001 - a fire-and-forget post must never raise
        # Never log the URL: it is the secret.
        _logger.warning("factory: Slack alert failed: %s", type(exc).__name__)


_slack_tasks: set[asyncio.Task[None]] = set()
_slack_sent: dict[tuple[str, str], float] = {}
SlackPoster = Any
slack_poster: Any = _post_slack
"""The coroutine that posts to Slack (tests swap it)."""


async def route_opened(app_state: Any, issue: FactoryIssue) -> None:
    """Send an opened incident of warning or worse to the factory's Slack webhook, if set."""
    if issue.class_ != INCIDENT:
        return
    if _SEVERITY_RANK.get(issue.severity, 0) < _SEVERITY_RANK[SLACK_MIN_SEVERITY]:
        return
    deps = factory_deps(app_state)
    url = await asyncio.to_thread(slack_webhook, deps, issue.factory_id)
    if url is None:
        return
    # A flapping condition reopens its incident each time: post it once an hour.
    key = (norm_id(issue.factory_id), issue.dedupe_key or issue.id)
    now = time.monotonic()
    last = _slack_sent.get(key)
    if last is not None and now - last < SLACK_REPOST_S:
        return
    if len(_slack_sent) > 1024:
        for stale in [k for k, at in _slack_sent.items() if now - at >= SLACK_REPOST_S]:
            del _slack_sent[stale]
    _slack_sent[key] = now
    from omnigent.factory import LABEL_NAME

    root = await asyncio.to_thread(deps.conversation_store.get_conversation, issue.factory_id)
    name = (root.labels.get(LABEL_NAME) if root is not None else None) or "factory"
    task = asyncio.get_running_loop().create_task(slack_poster(url, slack_text(issue, name)))
    _slack_tasks.add(task)
    task.add_done_callback(_slack_tasks.discard)


def reset() -> None:
    """Forget cached webhooks, Slack repost stamps and report counts (tests)."""
    _slack_cache.clear()
    _slack_sent.clear()
    _reports.clear()


__all__ = [
    "ASSIGN_OPERATORS",
    "ASSIGN_OWNERS",
    "FINDING",
    "HUMAN_RULE",
    "INCIDENT",
    "RETRO",
    "SCOPE",
    "ack",
    "assign",
    "clear_issue",
    "counts_open",
    "dedupe_key",
    "find_issue",
    "is_mine",
    "open_issue_for",
    "open_issues",
    "raise_issue",
    "report",
    "resolve",
    "route_opened",
    "routing_view",
    "set_slack_webhook",
    "slack_escape",
    "slack_text",
    "slack_webhook",
    "snooze",
    "unsnooze_due",
    "valid_webhook",
    "visible_to",
]
