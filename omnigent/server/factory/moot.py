"""
Asks that no longer matter (calm rule 9): they close themselves with a cause.

- A pending ``projects`` proposal whose project closed (cancelled, merged into
  another, marked done), whose project finished (merged or done) after it was
  filed, or whose changes already hold (the charter already says what it
  proposes) asks nothing a person can still decide. A proposal filed against a
  project that had finished already is a follow-up the lead meant, and one a
  person reopened for its reason stays open.
- A question an agent asked about a task, a PR or a project that finished
  after it was asked (the task closed, the PR merged or closed, the project
  merged, finished or closed) has nothing left to act on.

Pure: it reads rows. The proposals sweep closes them with their cause
(:func:`.proposals.close_stale`, :func:`.server_decisions.close_finished_subjects`);
the level projection never shows one in the meantime (:class:`.nodes.Tree`).
Nothing here names a product: the causes are the rows' own slugs and codes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    PROPOSAL_OPEN_STATUSES,
    STAGE_DONE,
    STAGE_MERGED,
    TASK_CLOSED_STATES,
)
from omnigent.server.factory import node_refs
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities.factory import (
        FactoryDecision,
        FactoryPr,
        FactoryProject,
        FactoryProposal,
        FactoryTask,
    )

DONE_STAGES: Final = (STAGE_MERGED, STAGE_DONE)
"""Stages a project keeps once its work shipped (its status stays ``active``)."""
ACTIVE: Final = "active"
ALREADY: Final = "already_applied"
"""The code of a proposal whose changes already hold."""
ASKED_SOURCES: Final = frozenset({"agent"})
"""Decisions whose subject finishing ends them here (the server's own close elsewhere)."""


# ── Proposals ──────────────────────────────────────────────────


def touched(proposal: FactoryProposal) -> list[str]:
    """The projects a proposal changes (updates, cancels, splits, merges, moves), sorted."""
    out: set[str] = set()
    for op in proposal.ops or []:
        if not isinstance(op, Mapping):
            continue
        for key in ("slug", "into"):
            value = op.get(key)
            if isinstance(value, str) and value:
                out.add(value)
        out.update(v for v in op.get("from") or [] if isinstance(v, str) and v)
    return sorted(out)


def _closed(
    proposal: FactoryProposal, rows: Mapping[str, FactoryProject]
) -> tuple[str, str] | None:
    for slug in touched(proposal):
        row = rows.get(slug)
        if row is not None and row.status not in (None, ACTIVE):
            return "project_closed", f"project {slug!r} is {row.status}"
    return None


def _finished(
    proposal: FactoryProposal, rows: Mapping[str, FactoryProject]
) -> tuple[str, str] | None:
    """
    ``project_done`` when a project it touches merged or finished after it was
    filed: its stage then is in ``based_on``; a row from before stages were
    recorded is judged by when the project's stage last changed.
    """
    based = proposal.based_on or {}
    for slug in sorted({*touched(proposal), *based}):
        row = rows.get(slug)
        if row is None or row.stage not in DONE_STAGES:
            continue
        ref = based.get(slug)
        then = ref.get("stage") if isinstance(ref, Mapping) else None
        if isinstance(then, str):
            if then in DONE_STAGES:
                continue
        elif row.stage_changed_at and row.stage_changed_at < proposal.created_at:
            continue
        return "project_done", f"project {slug!r} is {row.stage}"
    return None


def _already_holds(
    proposal: FactoryProposal, rows: Mapping[str, FactoryProject]
) -> tuple[str, str] | None:
    """``already_applied`` when every op is an update the project's charter already holds."""
    from pydantic import ValidationError

    from omnigent.server.factory.charter import normalize_charter
    from omnigent.server.factory.start import CharterPatchIn, merge_charter

    ops = [op for op in proposal.ops or [] if isinstance(op, Mapping)]
    if not ops or any(op.get("op") != "update" for op in ops):
        return None
    slugs: list[str] = []
    for op in ops:
        row = rows.get(str(op.get("slug") or ""))
        if row is None:
            return None
        try:
            patch = CharterPatchIn.model_validate(op.get("patch") or {})
        except ValidationError:
            return None
        if not patch.model_fields_set:
            return None
        before = normalize_charter(row.charter)
        if normalize_charter(merge_charter(row.charter, patch)) != before:
            return None
        if patch.priority is not None and patch.priority != row.priority:
            return None
        slugs.append(row.slug)
    names = sorted(set(slugs))
    verb = "has" if len(names) == 1 else "have"
    return ALREADY, f"{' and '.join(repr(s) for s in names)} already {verb} these changes"


def proposal_reason(
    proposal: FactoryProposal, rows: Mapping[str, FactoryProject]
) -> tuple[str, str] | None:
    """
    Why an open ``projects`` proposal no longer matters, from the rows alone.

    :param rows: The factory's projects by slug.
    :returns: ``(code, reason)``, e.g. ``("project_done", "project 'api' is merged")``
        or ``("already_applied", "'api' already has these changes")``; ``None``
        while a person could still decide it (or kept it open for that reason).
    """
    if proposal.kind != "projects" or proposal.status not in PROPOSAL_OPEN_STATUSES:
        return None
    found = _closed(proposal, rows) or _finished(proposal, rows) or _already_holds(proposal, rows)
    reopened = (proposal.result or {}).get("reopened")
    if found is not None and isinstance(reopened, Mapping) and reopened.get("code") == found[0]:
        return None
    return found


# ── A question whose subject finished ──────────────────────────


def _task_why(task: FactoryTask | None, since: int) -> str | None:
    if task is None or task.state not in TASK_CLOSED_STATES:
        return None
    at = task.closed_at or task.updated_at
    return f"T-{task.number} is {task.state}" if at >= since else None


def _pr_why(pr: FactoryPr | None, since: int) -> str | None:
    if pr is None or pr.state == "open":
        return None
    at = (pr.merged_at if pr.state == "merged" else None) or pr.closed_at or pr.updated_at
    if not at or at < since:
        return None
    return f"#{pr.number} {'merged' if pr.state == 'merged' else 'closed'}"


def _project_why(row: FactoryProject | None, since: int) -> str | None:
    if row is None:
        return None
    if row.status not in (None, ACTIVE):
        return f"{row.slug} is {row.status}" if row.updated_at >= since else None
    if row.stage in DONE_STAGES and row.stage_changed_at >= since:
        return f"{row.slug} is {row.stage}"
    return None


class Subjects:
    """The rows a question's subject names, indexed once per pass."""

    def __init__(
        self,
        projects: Iterable[FactoryProject],
        tasks: Iterable[FactoryTask],
        prs: Iterable[FactoryPr],
    ) -> None:
        self.projects = {p.slug: p for p in projects}
        task_rows = list(tasks)
        self.tasks_by_number = {t.number: t for t in task_rows}
        self.tasks_by_key = {(t.project_slug, t.key): t for t in task_rows}
        pr_rows = list(prs)
        self.prs_by_id = {norm_id(p.id): p for p in pr_rows}
        self.prs_by_number = {p.number: p for p in pr_rows}

    def why(self, decision: FactoryDecision) -> str | None:
        """
        Why an open question no longer matters: what it is about finished
        after it was asked (``"T-16 is done"``, ``"#9882 merged"``,
        ``"connectors-core is merged"``); ``None`` while it still does.
        """
        if decision.status != "open" or decision.source not in ASKED_SOURCES:
            return None
        since = int(decision.created_at)
        subject = (decision.subject or "").strip()
        head, sep, rest = subject.partition(":")
        if sep and head.lower() == "task" and "/" in rest:
            slug, _s, key = rest.partition("/")
            return _task_why(self.tasks_by_key.get((slug, key)), since)
        if sep and head.lower() == "pr":
            bare = rest.split("@", 1)[0].strip()
            try:
                return _pr_why(self.prs_by_id.get(norm_id(bare)), since)
            except ValueError:
                pass
        parsed = node_refs.parse(node_refs.normalize(subject) or "") if subject else None
        if parsed is not None:
            return self._ref_why(parsed, since)
        if not subject and decision.project_slug:
            return _project_why(self.projects.get(decision.project_slug), since)
        return None

    def _ref_why(self, ref: node_refs.Ref, since: int) -> str | None:
        if ref.kind == "task" and ref.id.isdigit():
            return _task_why(self.tasks_by_number.get(int(ref.id)), since)
        if ref.kind == "pr" and ref.id.isdigit():
            return _pr_why(self.prs_by_number.get(int(ref.id)), since)
        if ref.kind == "project":
            return _project_why(self.projects.get(ref.id), since)
        return None


def decision_reasons(
    decisions: Iterable[FactoryDecision], subjects: Subjects
) -> list[tuple[FactoryDecision, str]]:
    """:returns: ``(decision, why)`` for each open question whose subject finished since."""
    out: list[tuple[FactoryDecision, str]] = []
    for decision in decisions:
        why = subjects.why(decision)
        if why is not None:
            out.append((decision, why))
    return out


def quiet_ids(
    decisions: Iterable[FactoryDecision],
    proposals: Mapping[str, FactoryProposal],
    subjects: Subjects,
) -> frozenset[str]:
    """
    The open decisions (bare ids) the sweep closes by itself: a moot proposal's
    and a question whose subject finished. Until then they are no ask.

    :param proposals: The factory's proposals by ref (``C-16``).
    """
    out: set[str] = set()
    for decision in decisions:
        if decision.status != "open":
            continue
        if decision.source == "proposal":
            proposal = _subject_proposal(decision, proposals)
            if proposal is not None and proposal_reason(proposal, subjects.projects) is not None:
                out.add(norm_id(decision.id))
        elif subjects.why(decision) is not None:
            out.add(norm_id(decision.id))
    return frozenset(out)


def _subject_proposal(
    decision: FactoryDecision, proposals: Mapping[str, FactoryProposal]
) -> FactoryProposal | None:
    parsed = node_refs.parse(node_refs.normalize((decision.subject or "").strip()) or "")
    found: Any = proposals.get(parsed.text) if parsed is not None else None
    return found


__all__ = [
    "ALREADY",
    "ASKED_SOURCES",
    "DONE_STAGES",
    "Subjects",
    "decision_reasons",
    "proposal_reason",
    "quiet_ids",
    "touched",
]
