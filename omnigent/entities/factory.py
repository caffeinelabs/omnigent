"""Software-factory entities — rows of the ``factory_*`` tables.

Plain frozen dataclasses the factory store converts ORM rows into. Enum-like
columns carry their string names (the store owns the int codes) and JSON
columns carry decoded Python values. ``to_dict()`` gives a JSON-ready copy.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any


def _plain(obj: Any) -> dict[str, Any]:
    """Deep-copied ``asdict`` so callers can't mutate a row's JSON blobs."""
    return copy.deepcopy(asdict(obj))


@dataclass(frozen=True)
class FactoryProject:
    """
    One project of a factory (``factory_projects``).

    :param id: Row id (bare 32-char hex).
    :param factory_id: Root lead session id.
    :param slug: Unique within the factory, e.g. ``"api-version"``.
    :param title: Display title, e.g. ``"API version endpoint"``.
    :param repo: ``owner/name``, e.g. ``"caffeinelabs/app"``.
    :param base_branch: Branch PRs target, e.g. ``"main"``.
    :param charter: The charter JSON (goal, dod, scope_paths, preview, ...).
    :param priority: 0 (highest) to 3.
    :param stage: Server-derived stage name, e.g. ``"building"``.
    :param stage_changed_at: Epoch seconds the stage last changed.
    :param agent_stage: The PL's own stage claim, or ``None``.
    :param agent_stage_at: When the claim was made.
    :param pl_session_id: Current PL session id, or ``None`` before spawn.
    :param env: Dev-env truth ``{reported: {...}, probe: {...}}``, or ``None``.
    :param created_by: Who created the row, e.g. ``"alice@example.com"``.
    :param created_at: Epoch seconds.
    :param updated_at: Epoch seconds.
    :param status: ``active`` / ``cancelled`` / ``merged`` / ``done``.
    :param parent_slug: The project this one was split from or merged into.
    :param closed_at: When it left ``active``.
    :param def_version: Factory definition version in force at creation.
    :param version_id: ``factory_versions`` row of the platform that created it.
    :param workspace_id: Tenant partition key.
    """

    id: str
    factory_id: str
    slug: str
    title: str
    repo: str | None = None
    base_branch: str | None = None
    charter: dict[str, Any] | None = None
    priority: int = 2
    stage: str = "queued"
    stage_changed_at: int = 0
    agent_stage: str | None = None
    agent_stage_at: int | None = None
    pl_session_id: str | None = None
    env: dict[str, Any] | None = None
    created_by: str = ""
    created_at: int = 0
    updated_at: int = 0
    status: str = "active"
    parent_slug: str | None = None
    closed_at: int | None = None
    def_version: int | None = None
    version_id: str | None = None
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactoryPr:
    """
    One tracked pull request (``factory_prs``).

    :param id: Row id.
    :param factory_id: Root lead session id.
    :param repo: ``owner/name``.
    :param number: PR number, e.g. ``9630``.
    :param url: PR web URL.
    :param title: PR title.
    :param head_ref: Head branch, e.g. ``"factory/api-version/version-endpoint"``.
    :param base_ref: Base branch, e.g. ``"main"``.
    :param head_sha: Current head commit (40 hex), or ``""`` before the first poll.
    :param state: ``open`` / ``merged`` / ``closed``.
    :param is_draft: Whether GitHub reports the PR as a draft.
    :param project_slug: Owning project, or ``None`` when unknown.
    :param task: Task slug parsed from the branch, or ``None``.
    :param additions: Lines added.
    :param deletions: Lines removed.
    :param changed_files: Files touched.
    :param gates: Gate report ``{draft, checks, review, verify, mergeable,
        protected_paths, demo?, reasons, notes, evidence}``.
    :param ready: Every ready gate passes on an open PR.
    :param ready_at: When it last became ready.
    :param sentinel: ``{verdict, head_sha, note, by, at}`` or ``None``.
    :param human_review: ``{verdict, text, by, at, head_sha, source}`` or ``None``.
    :param preview: Factory-preview truth (see ``PreviewView``) or ``None``.
    :param merged_sha: Merge commit, once merged.
    :param merged_by: GitHub login of the merger.
    :param merged_at: Epoch seconds of the merge.
    :param closed_at: Epoch seconds of the close (merged or not).
    :param source: ``tool`` (registered by an agent) or ``discovered`` (poller).
    :param last_polled_at: Last GitHub refresh, ``None`` when due now.
    :param poll_error: Last refresh error, e.g. ``"GitHub not connected"``.
    :param created_at: Epoch seconds.
    :param updated_at: Epoch seconds.
    :param opened_at: GitHub ``createdAt``.
    :param first_ready_at: When every ready gate first passed (``ready_at`` is the latest).
    :param task_id: ``factory_tasks`` row the branch binds to.
    :param version_id: ``factory_versions`` row of the platform that registered it.
    :param workspace_id: Tenant partition key.
    """

    id: str
    factory_id: str
    repo: str
    number: int
    url: str
    title: str = ""
    head_ref: str = ""
    base_ref: str = ""
    head_sha: str = ""
    state: str = "open"
    is_draft: bool = False
    project_slug: str | None = None
    task: str | None = None
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    gates: dict[str, Any] = field(default_factory=dict)
    ready: bool = False
    ready_at: int | None = None
    sentinel: dict[str, Any] | None = None
    human_review: dict[str, Any] | None = None
    preview: dict[str, Any] | None = None
    merged_sha: str | None = None
    merged_by: str | None = None
    merged_at: int | None = None
    closed_at: int | None = None
    source: str = "tool"
    last_polled_at: int | None = None
    poll_error: str | None = None
    created_at: int = 0
    updated_at: int = 0
    opened_at: int | None = None
    first_ready_at: int | None = None
    task_id: str | None = None
    version_id: str | None = None
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactoryDecision:
    """
    One question for the human (``factory_decisions``), shown as ``D-<number>``.

    :param id: Row id.
    :param factory_id: Root lead session id.
    :param number: Per-factory sequence number.
    :param question: The question, ≤ 1000 chars.
    :param source: ``agent`` / ``relaunch_failed`` / ``blocked_approval`` /
        ``pr_closed`` / ``storm`` / ``proposal`` / ``loop`` / ``access_request`` /
        ``done_check``.
    :param status: ``open`` / ``answered`` / ``withdrawn`` / ``expired``.
    :param dedupe_key: Key a re-ask matches while the decision is open.
    :param options: ``[{id, label, description?, recommended?, action?, href?, target?}]``.
    :param blocking: Whether the asker waits on it.
    :param allow_text: Whether a free-text answer is accepted.
    :param project_slug: Project it concerns, or ``None``.
    :param asked_by_session_id: The asking session (agent decisions).
    :param context: Markdown context, or ``None``.
    :param answer_option_id: Chosen option id.
    :param answer_text: Free-text answer.
    :param answered_by: Human who answered.
    :param answered_at: Epoch seconds.
    :param delivered_mail_id: Mail id of the delivered answer.
    :param delivered_at: When the answer reached the asker.
    :param created_at: Epoch seconds.
    :param updated_at: Epoch seconds.
    :param assignee: ``owners``, ``operators`` or a user id; ``None`` means operators.
    :param claimed_by: User who claimed it.
    :param claimed_at: When it was claimed.
    :param thread: Room thread of its card, e.g. ``"project:db-version"``.
    :param subject: e.g. ``"task:T-4"``, ``"pr:<id>"`` or ``"proposal:C-4"``.
    :param workspace_id: Tenant partition key.
    """

    id: str
    factory_id: str
    number: int
    question: str
    source: str = "agent"
    status: str = "open"
    dedupe_key: str = ""
    options: list[dict[str, Any]] = field(default_factory=list)
    blocking: bool = True
    allow_text: bool = True
    project_slug: str | None = None
    asked_by_session_id: str | None = None
    context: str | None = None
    answer_option_id: str | None = None
    answer_text: str | None = None
    answered_by: str | None = None
    answered_at: int | None = None
    delivered_mail_id: str | None = None
    delivered_at: int | None = None
    created_at: int = 0
    updated_at: int = 0
    assignee: str | None = None
    claimed_by: str | None = None
    claimed_at: int | None = None
    thread: str | None = None
    subject: str | None = None
    workspace_id: int = 0

    @property
    def ref(self) -> str:
        """:returns: The human-facing reference, e.g. ``"D-7"``."""
        return f"D-{self.number}"

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row, plus ``ref``."""
        out = _plain(self)
        out["ref"] = self.ref
        return out


@dataclass(frozen=True)
class EventDraft:
    """
    An event to append to the log (the store assigns ``id`` and, if unset, ``ts``).

    :param factory_id: Root lead session id.
    :param kind: Stable kind, e.g. ``"pr.ready"``.
    :param summary: One line, ≤ 300 chars.
    :param severity: ``info`` / ``notice`` / ``warning`` / ``critical``.
    :param project_slug: Project it concerns.
    :param from_id: Acting session id.
    :param to_id: Target session id.
    :param subject: e.g. ``"pr:caffeinelabs/app#9630"`` or ``"decision:D-3"``.
    :param data: JSON payload.
    :param ts: Epoch milliseconds, or ``None`` for now.
    """

    factory_id: str
    kind: str
    summary: str
    severity: str = "info"
    project_slug: str | None = None
    from_id: str | None = None
    to_id: str | None = None
    subject: str | None = None
    data: dict[str, Any] | None = None
    ts: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy."""
        return _plain(self)


@dataclass(frozen=True)
class FactoryEvent:
    """
    One logged event (``factory_events``).

    :param id: uuid7 hex; sorts by creation time.
    :param factory_id: Root lead session id.
    :param ts: Epoch milliseconds.
    :param kind: Stable kind, e.g. ``"message.delivered"``.
    :param summary: One line.
    :param severity: ``info`` / ``notice`` / ``warning`` / ``critical``.
    :param project_slug: Project it concerns.
    :param from_id: Acting session id.
    :param to_id: Target session id.
    :param subject: What it is about.
    :param data: JSON payload.
    :param workspace_id: Tenant partition key.
    """

    id: str
    factory_id: str
    ts: int
    kind: str
    summary: str
    severity: str = "info"
    project_slug: str | None = None
    from_id: str | None = None
    to_id: str | None = None
    subject: str | None = None
    data: dict[str, Any] | None = None
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactoryDemo:
    """
    One recorded demo of a PR head (``factory_demos``).

    :param id: Row id.
    :param factory_id: Root lead session id.
    :param project_slug: Owning project.
    :param repo: ``owner/name``.
    :param pr_number: PR number.
    :param head_sha: Commit the demo was recorded at.
    :param session_id: PL session owning the demo's files.
    :param created_by_session: Session that published it (demo agent, PL, ...).
    :param status: ``PASS`` / ``PARTIAL`` / ``FAIL`` / ``ERROR``.
    :param title: Short title.
    :param summary: What the demo shows, ≤ 1000 chars.
    :param files: ``[{file_id, name, kind, caption, content_type, bytes, filename?,
        step?, store?, key?}]``; ``store`` and ``key`` locate a file kept in a
        factory artifact store, else it is a session file of *session_id*.
    :param manifest: The harness ``demo.json`` (steps, plan_source, timings), or ``None``.
    :param pr_id: ``factory_prs`` row id, when known.
    :param preview_url: Preview app URL the demo ran against.
    :param created_at: Epoch seconds.
    :param workspace_id: Tenant partition key.
    """

    id: str
    factory_id: str
    project_slug: str
    repo: str
    pr_number: int
    head_sha: str
    session_id: str
    created_by_session: str
    status: str = "PASS"
    title: str = ""
    summary: str = ""
    files: list[dict[str, Any]] = field(default_factory=list)
    manifest: dict[str, Any] | None = None
    pr_id: str | None = None
    preview_url: str | None = None
    created_at: int = 0
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


# ── Round 3: people, Room, proposals, work graph, loops, issues, telemetry ──


def _with_class(obj: Any) -> dict[str, Any]:
    """:func:`_plain` with the ``class_`` attribute exposed as ``class``."""
    out = _plain(obj)
    out["class"] = out.pop("class_")
    return out


ISSUE_REF_PREFIXES: dict[str, str] = {"incident": "INC", "finding": "F"}
"""Ref prefix per issue class; retros have no ref."""


@dataclass(frozen=True)
class FactoryPerson:
    """
    One human on a factory's roster (``factory_people``).

    :param factory_id: Root lead session id.
    :param user_id: The person's user id, e.g. ``"mia@caffeine.ai"``.
    :param role: ``viewer`` / ``operator`` / ``owner``.
    :param is_creator: The human who started the factory (always an owner).
    :param invited_by: Who added them.
    :param invited_at: Epoch seconds.
    :param last_seen_at: Last cockpit presence (written at most once a minute).
    :param notify: Notification preferences, or ``None`` for the defaults.
    :param workspace_id: Tenant partition key.
    """

    factory_id: str
    user_id: str
    role: str = "viewer"
    is_creator: bool = False
    invited_by: str = ""
    invited_at: int = 0
    last_seen_at: int | None = None
    notify: dict[str, Any] | None = None
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactoryChatMessage:
    """
    One message in the Room (``factory_chat_messages``).

    :param id: Row id.
    :param factory_id: Root lead session id.
    :param thread: ``factory`` / ``improvements`` / ``project:<slug>`` / ``pr:<id>``.
    :param seq: Per-factory sequence (shared by every thread).
    :param author_kind: ``human`` / ``agent`` / ``system``.
    :param author: User id, session id, or ``"server"``.
    :param body: Markdown, ≤ 4000 chars for agents.
    :param author_role: e.g. ``"operator"`` or ``"project_lead"``.
    :param mentions: e.g. ``["@atlas", "@pl:db-version"]``.
    :param refs: e.g. ``["D-7", "C-4", "!9630"]``.
    :param reply_to: Id of the message this answers.
    :param card: ``{type, ref}`` of a mirrored object, or ``None``.
    :param routed: ``[{session_id, mail_id, delivered_at, replied_at}]``.
    :param auto_reply: Posted by the server from a silent agent turn.
    :param created_at: Epoch seconds.
    :param workspace_id: Tenant partition key.
    """

    id: str
    factory_id: str
    thread: str
    seq: int
    author_kind: str
    author: str
    body: str
    author_role: str | None = None
    mentions: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    reply_to: str | None = None
    card: dict[str, Any] | None = None
    routed: list[dict[str, Any]] = field(default_factory=list)
    auto_reply: bool = False
    created_at: int = 0
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactoryChatRead:
    """
    A user's read mark in one thread (``factory_chat_reads``).

    :param last_seq: Highest ``seq`` the user has seen in the thread.
    """

    factory_id: str
    user_id: str
    thread: str
    last_seq: int
    updated_at: int = 0
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class ChatThreadStats:
    """
    Aggregate of one thread's messages (not a table row).

    :param thread: Thread key.
    :param last_seq: Highest ``seq`` in the thread.
    :param last_at: ``created_at`` of that message.
    :param messages: Number of messages.
    """

    thread: str
    last_seq: int
    last_at: int
    messages: int


@dataclass(frozen=True)
class FactoryProposal:
    """
    A proposed change (``factory_proposals``), shown as ``C-<number>``.

    :param kind: ``projects`` / ``tuning`` / ``rules`` / ``code``.
    :param status: ``pending`` / ``applied`` / ``trial`` / ``kept`` / ``reverted`` /
        ``rejected`` / ``expired`` / ``withdrawn`` / ``refused`` / ``undone`` /
        ``awaiting_cosign``.
    :param approval: ``auto`` / ``operator`` / ``owner``.
    :param class_: Tier-1 approval class ``A`` / ``B`` / ``human`` (JSON key ``class``).
    :param ops: Project ops (``[{op, project|slug, ...}]``) or definition patches.
    :param before: The touched state before the change (definition paths, charters).
    :param after: The state the change produces.
    :param rationale: Why, ≤ 2000 chars.
    :param analysis: Planner analysis (cohesion, hub files, size estimates).
    :param evidence: Ids the proposal cites, e.g. ``["F-9", "D-2"]``.
    :param expected_effect: ``{metric, direction, delta?}``.
    :param warnings: Validation warnings, e.g. ``["scope_overlap: ..."]``.
    :param author_kind: ``human`` / ``agent`` / ``server``.
    :param author: User id or session id of the proposer.
    :param on_behalf_of: The human whose chat message the proposal answers.
    :param cosigned_by: The peer lead that co-signed a class-A change.
    :param thread: Room thread it was proposed in.
    :param reply_to_chat: Chat message id it answers.
    :param decision_id: The ``proposal`` decision mirroring it.
    :param based_on: ``{slug: updated_at}`` of the rows it read (stale check).
    :param trial_until: End of the trial window (epoch seconds).
    :param undo_until: End of the undo window (epoch seconds).
    :param result: Apply or trial outcome (spawned PLs, metrics, reason).
    :param dedupe_key: A re-proposal with the same key returns this row.
    """

    id: str
    factory_id: str
    number: int
    kind: str
    title: str
    status: str = "pending"
    approval: str = "operator"
    class_: str | None = None
    ops: list[dict[str, Any]] = field(default_factory=list)
    before: Any = None
    after: Any = None
    rationale: str = ""
    analysis: Any = None
    evidence: list[Any] = field(default_factory=list)
    expected_effect: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    author_kind: str = "agent"
    author: str = ""
    author_role: str | None = None
    on_behalf_of: str | None = None
    cosigned_by: str | None = None
    thread: str | None = None
    reply_to_chat: str | None = None
    decision_id: str | None = None
    based_on: dict[str, Any] | None = None
    trial_until: int | None = None
    undo_until: int | None = None
    result: dict[str, Any] | None = None
    def_version_from: int | None = None
    def_version_to: int | None = None
    decided_by: str | None = None
    decided_at: int | None = None
    applied_at: int | None = None
    reverted_at: int | None = None
    reverted_by: str | None = None
    dedupe_key: str | None = None
    created_at: int = 0
    updated_at: int = 0
    workspace_id: int = 0

    @property
    def ref(self) -> str:
        """:returns: The human-facing reference, e.g. ``"C-4"``."""
        return f"C-{self.number}"

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy with ``class`` and ``ref``."""
        out = _with_class(self)
        out["ref"] = self.ref
        return out


@dataclass(frozen=True)
class FactoryTask:
    """
    One node of the work graph (``factory_tasks``), shown as ``T-<number>``.

    :param project_slug: Owning project.
    :param key: Branch suffix, e.g. ``"route"`` (``factory/<slug>/<key>``).
    :param kind: ``implement`` / ``test`` / ``docs`` / ``spike`` / ``ops`` / ``adhoc``.
    :param state: ``planned`` / ``ready`` / ``running`` / ``review`` / ``blocked`` /
        ``done`` / ``failed`` / ``cancelled``.
    :param card: ``{text, acceptance[], env_checks[], preview?, demo_focus?, harness?}``.
    :param deps: Task keys, ``project:<slug>`` and ``decision:D-n``.
    :param scope: Path globs.
    :param priority: 0 (highest) to 3.
    :param estimate_min: Planner estimate in minutes.
    :param vendor: Harness vendor of the current worker, e.g. ``"claude"``.
    :param session_ids: Bound worker sessions, oldest first.
    :param pr_id: ``factory_prs`` row of the task's PR.
    :param attempts: Implement attempts so far.
    :param budget: ``{attempts?, usd?}`` overrides.
    :param cost_usd: Spend attributed to the task.
    :param created_via: ``human`` / ``agent`` / ``server``.
    """

    id: str
    factory_id: str
    number: int
    project_slug: str
    key: str
    title: str
    kind: str = "implement"
    state: str = "planned"
    card: dict[str, Any] = field(default_factory=dict)
    deps: list[str] = field(default_factory=list)
    scope: list[str] = field(default_factory=list)
    priority: int = 2
    estimate_min: int | None = None
    vendor: str | None = None
    session_ids: list[str] = field(default_factory=list)
    pr_id: str | None = None
    attempts: int = 0
    budget: dict[str, Any] | None = None
    cost_usd: float | None = None
    created_by: str = ""
    created_via: str = "agent"
    def_version: int | None = None
    started_at: int | None = None
    closed_at: int | None = None
    created_at: int = 0
    updated_at: int = 0
    workspace_id: int = 0

    @property
    def ref(self) -> str:
        """:returns: The human-facing reference, e.g. ``"T-4"``."""
        return f"T-{self.number}"

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row, plus ``ref``."""
        out = _plain(self)
        out["ref"] = self.ref
        return out


@dataclass(frozen=True)
class FactoryLoop:
    """
    One loop (``factory_loops``), unique per ``(kind, subject)``.

    :param kind: ``implement`` / ``review`` / ``verify`` / ``demo`` / ``ci`` /
        ``claim`` / ``plan`` / ``intake`` / ``improve``.
    :param subject: e.g. ``"task:<id>"`` or ``"pr:<id>@<sha7>"``.
    :param state: ``running`` / ``converged`` / ``stalled`` / ``thrashing`` /
        ``exhausted`` / ``escalated`` / ``cancelled`` / ``superseded``.
    :param iteration: Iterations so far.
    :param spec: The resolved budgets the loop started with.
    :param spent: ``{attempts, usd, wall_s, per_head, env_retries}``.
    :param last_verdict: e.g. ``"FAIL"`` or ``"CHANGES"``.
    :param last_reason: Why the last iteration ended, ≤ 300 chars.
    :param decision_id: The ``loop`` decision opened on escalation.
    """

    id: str
    factory_id: str
    kind: str
    subject: str
    project_slug: str | None = None
    task_id: str | None = None
    pr_id: str | None = None
    state: str = "running"
    iteration: int = 0
    spec: dict[str, Any] = field(default_factory=dict)
    spent: dict[str, Any] = field(default_factory=dict)
    last_verdict: str | None = None
    last_reason: str | None = None
    decision_id: str | None = None
    def_version: int | None = None
    started_at: int = 0
    updated_at: int = 0
    ended_at: int | None = None
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactoryIssue:
    """
    An incident (``INC-n``), finding (``F-n``) or retro (``factory_issues``).

    :param class_: ``incident`` / ``finding`` / ``retro`` (JSON key ``class``).
    :param number: Sequence per factory and class.
    :param rule: e.g. ``"agent.repeat"`` or ``"member.dead"``.
    :param target: e.g. ``"member:<id>"``, ``"pr:<id>"`` or ``"project:<slug>"``.
    :param summary: One line, ≤ 300 chars.
    :param category: Root-cause or taxonomy label, e.g. ``"env_flaky"``.
    :param severity: ``info`` / ``notice`` / ``warning`` / ``critical``.
    :param source: ``detector`` / ``sentinel`` / ``human`` / ``agent`` / ``server``.
    :param body: Markdown detail.
    :param evidence: Event, span or decision ids, or structured evidence.
    :param status: ``open`` / ``acked`` / ``snoozed`` / ``resolved`` / ``wontfix``.
    :param dedupe_key: Detectors re-raise the same row through this key.
    :param count: Times the condition was seen.
    """

    id: str
    factory_id: str
    class_: str
    number: int
    rule: str
    target: str
    summary: str
    category: str | None = None
    severity: str = "notice"
    source: str = "detector"
    project_slug: str | None = None
    body: str | None = None
    evidence: Any = None
    status: str = "open"
    assignee: str | None = None
    snoozed_until: int | None = None
    acked_by: str | None = None
    resolved_by: str | None = None
    dedupe_key: str | None = None
    count: int = 1
    first_seen: int = 0
    last_seen: int = 0
    resolved_at: int | None = None
    proposal_id: str | None = None
    version_id: str | None = None
    def_version: int | None = None
    workspace_id: int = 0

    @property
    def ref(self) -> str | None:
        """:returns: e.g. ``"INC-13"`` or ``"F-7"``; ``None`` for a retro."""
        prefix = ISSUE_REF_PREFIXES.get(self.class_)
        return f"{prefix}-{self.number}" if prefix else None

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy with ``class`` and ``ref``."""
        out = _with_class(self)
        out["ref"] = self.ref
        return out


@dataclass(frozen=True)
class FactorySpan:
    """
    One OTel-shaped span (``factory_spans``); times are epoch milliseconds.

    :param id: uuid7 hex; sorts by creation time.
    :param trace_key: Groups a span tree, e.g. ``"pr:<id>"``.
    :param kind: e.g. ``"pr_round"``, ``"gate"``, ``"loop_iter"`` or ``"wait"``.
    :param name: e.g. ``"review"`` or ``"T-4 route r1"``.
    :param started_at: Epoch milliseconds.
    :param ended_at: Epoch milliseconds, ``None`` while open.
    :param outcome: ``open`` / ``ok`` / ``fail`` / ``abandoned`` / ``superseded`` /
        ``timeout`` / ``cancelled`` / ``exhausted``.
    :param attrs: Free-form attributes, e.g. ``{"first_pass": true}``.
    """

    id: str
    factory_id: str
    trace_key: str
    kind: str
    name: str
    started_at: int
    parent_id: str | None = None
    project_slug: str | None = None
    pr_id: str | None = None
    task_id: str | None = None
    loop_id: str | None = None
    head_sha: str | None = None
    session_id: str | None = None
    role: str | None = None
    agent_name: str | None = None
    actor: str | None = None
    ended_at: int | None = None
    outcome: str = "open"
    attempt: int | None = None
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    version_id: str | None = None
    def_version: int | None = None
    dedupe_key: str | None = None
    attrs: dict[str, Any] | None = None
    workspace_id: int = 0

    @property
    def duration_ms(self) -> int | None:
        """:returns: ``ended_at - started_at``, or ``None`` while open."""
        return None if self.ended_at is None else self.ended_at - self.started_at

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactoryCostDaily:
    """
    One cell of the cost ledger (``factory_cost_daily``).

    :param project_slug: ``""`` for the leads.
    :param role: Factory role, e.g. ``"ic"``.
    :param agent_name: Session title or sub-agent name.
    :param model: Model alias, e.g. ``"sonnet"``.
    :param day_utc: ``YYYY-MM-DD``.
    """

    factory_id: str
    project_slug: str
    role: str
    agent_name: str
    model: str
    day_utc: str
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    turns: int = 0
    updated_at: int = 0
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactoryVersion:
    """
    A platform version (``factory_versions``): image, server sha, bundles, knobs.

    :param bundles: ``{name: {agent_id, version, content_sha}}``.
    :param knobs: ``{OMNIGENT_FACTORY_*: value}``.
    :param content_hash: sha256 of the canonical JSON of the four fields above.
    """

    id: str
    content_hash: str
    bundles: dict[str, Any] = field(default_factory=dict)
    knobs: dict[str, Any] = field(default_factory=dict)
    image_tag: str | None = None
    server_sha: str | None = None
    note: str | None = None
    created_at: int = 0
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactoryDefinition:
    """
    One version of a factory's Tier-1 definition (``factory_definitions``).

    :param version: 1, 2, … per factory.
    :param doc: The definition (engine, cadence, loops, budgets, rules, ...).
    :param hash: sha256 of the canonical JSON of *doc*.
    :param created_via: ``seed`` / ``human`` / ``proposal`` / ``revert``.
    :param proposal_id: The proposal that produced it.
    """

    factory_id: str
    version: int
    doc: dict[str, Any] = field(default_factory=dict)
    hash: str = ""
    created_by: str = ""
    created_via: str = "seed"
    parent_version: int | None = None
    proposal_id: str | None = None
    created_at: int = 0
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactoryFeedback:
    """
    One rating, tag or calibration from a human or agent (``factory_feedback``).

    :param target_kind: ``pr`` / ``demo`` / ``decision`` / ``project`` / ``member`` /
        ``factory`` / ``issue`` / ``proposal``.
    :param target_id: Row id or ref, e.g. a PR id or ``"D-7"``.
    :param author_kind: ``human`` / ``agent`` / ``server``.
    :param kind: ``rating`` / ``useful`` / ``unneeded`` / ``closure`` /
        ``calibration`` / ``root_cause``.
    :param rating: 1-5 for ratings, +1/-1 for thumbs.
    :param tags: e.g. closure reasons or root causes.
    """

    id: str
    factory_id: str
    target_kind: str
    target_id: str
    author_kind: str
    author: str
    kind: str
    project_slug: str | None = None
    head_sha: str | None = None
    rating: int | None = None
    tags: list[str] = field(default_factory=list)
    body: str | None = None
    def_version: int | None = None
    created_at: int = 0
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)
