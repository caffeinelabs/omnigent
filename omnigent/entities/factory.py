"""Software-factory cockpit entities — rows of the five ``factory_*`` tables.

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
        ``pr_closed`` / ``storm``.
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
