"""Why a project or a task is not moving: what it waits on, and who must act.

One answer for every surface. :func:`task_waits` and :func:`project_waits` are
pure: they read :class:`Facts` (the factory's open decisions, open proposals,
projects, PRs, tasks and bad loops, loaded once per pass) and return
:class:`Wait` items, the most decisive first:

- ``stall``: a loop of the project went bad and a person decides what next;
- ``decision``: an open question a person owes the task (its sessions or its
  lead asked it, its subject or its text names the task, or a member of the
  project is parked on an approval or will not relaunch); the project's list
  shows every open question about the project;
- ``proposal``: a proposal that changes the project waits on a person (its
  ``D-n``) or on the other lead's case against;
- ``hold``: the task's lead holds it on purpose and says on what
  (``sys_factory_task(op="hold")``, or the blocker of its update naming an
  open ``D-n``, ``C-n``, ``PR #n`` or ``T-n``);
- ``merge``: the project's own PR, or the PR of a project it depends on,
  waits for a person to merge it;
- ``dependency``: a project it depends on is not there yet;
- ``capacity``: no free machine to start it on.

A hold on an item that closed, or past its ``until``, lapses by itself. The
loop engine's stall clock (:mod:`.task_pulse`) waits while a task has any
wait, and the snapshot shows each project's list (``ProjectView.waiting_on``)
so the cockpit, Home and Slack say the same thing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import PROPOSAL_OPEN_STATUSES, STAGE_DONE, STAGE_MERGED
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities.factory import (
        FactoryDecision,
        FactoryLoop,
        FactoryPr,
        FactoryProject,
        FactoryProposal,
        FactoryTask,
    )
    from omnigent.server.factory.context import FactoryDeps

KINDS: Final = ("stall", "decision", "proposal", "hold", "merge", "dependency", "capacity")
"""Wait kinds, in the order a project's list shows them."""
_RANK: Final = {kind: i for i, kind in enumerate(KINDS)}

HOLD_TEXT_MAX_S: Final = 24 * 3600
"""A hold on free text (no ``D-n``/``C-n``/``PR #n``/``T-n``/project) lapses after this."""
HOLD_UNTIL_MAX_S: Final = 7 * 24 * 3600
"""The latest ``until`` a hold may name."""
HOLD_NOTE_MAX: Final = 200
LABEL_MAX: Final = 160
QUESTION_WORDS_MAX: Final = 60

PROJECT_SOURCES: Final = frozenset({"agent", "blocked_approval", "proposal", "relaunch_failed"})
"""Decision sources a project's list shows while open (never the stall's own ``loop``)."""
WORK_SOURCES: Final = frozenset({"proposal", "blocked_approval", "relaunch_failed"})
"""Decisions about a project that hold every task of it naming no other task: a
proposal that changes it, a member parked on an approval, one that will not relaunch.
Another agent's question holds a task only when it names it."""
OPEN_TASK_STATES: Final = frozenset({"planned", "ready", "running", "review", "blocked"})
MOVING_TASK_STATES: Final = frozenset({"running", "review", "blocked"})
BAD_LOOP_STATES: Final = frozenset({"stalled", "exhausted", "thrashing"})

REF_KIND_DECISION: Final = "decision"
REF_KIND_PROPOSAL: Final = "proposal"
REF_KIND_TASK: Final = "task"
REF_KIND_PR: Final = "pr"
REF_KIND_PROJECT: Final = "project"
REF_KIND_TEXT: Final = "text"

_DECISION_RE: Final = re.compile(r"(?<![\w-])D-(\d+)(?![\w-])", re.IGNORECASE)
_PROPOSAL_RE: Final = re.compile(r"(?<![\w-])C-(\d+)(?![\w-])", re.IGNORECASE)
_TASK_RE: Final = re.compile(r"(?<![\w-])T-(\d+)(?![\w-])", re.IGNORECASE)
_PR_RE: Final = re.compile(r"(?:(?<![\w-])PR\s*#?|(?<![\w#])#|!)(\d+)(?![\w-])", re.IGNORECASE)
_SLUG_RE: Final = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")
_WORD_RE: Final = re.compile(r"[a-z0-9][a-z0-9-]*[a-z0-9]")


@dataclass(frozen=True)
class Wait:
    """
    One thing a project or task waits on.

    :param kind: One of :data:`KINDS`.
    :param ref: What it is: ``D-45``, ``C-16``, ``PR #9881``, ``T-3``, a project slug,
        ``pods``, or the held task's ref for a hold on free text.
    :param label: One line, e.g. ``waiting on D-45 (Approve C-16, needs an owner)``.
    :param who: Who must act, in plain words: ``an owner``, ``an operator``, a
        person's id, ``a person``, ``the project lead``, ``the other lead``, …
    :param person: A person must act (it belongs in "Needs you").
    :param since: When the wait began (epoch seconds), when known.
    :param task: The task that waits, e.g. ``T-16`` (``None``: the project).
    :param decision_id: The decision a person answers, when there is one.
    """

    kind: str
    ref: str
    label: str
    who: str
    person: bool = False
    since: int | None = None
    task: str | None = None
    decision_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        """:returns: The ``WaitingOnView`` JSON."""
        return {
            "kind": self.kind,
            "ref": self.ref,
            "label": self.label,
            "who": self.who,
            "person": self.person,
            "since": self.since,
            "task": self.task,
            "decision_id": self.decision_id,
        }


@dataclass
class Facts:
    """
    What the waits read, loaded once per pass.

    :param decisions: The factory's open decisions.
    :param proposals: Its open (pending, co-signing) proposals.
    :param tasks: Its tasks (any state: dependencies name closed ones too).
    :param loops: Its bad loops (stalled, exhausted, thrashing).
    :param leads: Project slug -> its lead's session id (the lead-asked rule).
    """

    projects: Mapping[str, FactoryProject] = field(default_factory=dict)
    prs: Sequence[FactoryPr] = ()
    decisions: Sequence[FactoryDecision] = ()
    proposals: Sequence[FactoryProposal] = ()
    tasks: Sequence[FactoryTask] = ()
    loops: Sequence[FactoryLoop] = ()
    leads: Mapping[str, str | None] = field(default_factory=dict)
    now: float = 0.0
    _decisions_by_number: dict[int, FactoryDecision] = field(init=False, repr=False)
    _proposals_by_number: dict[int, FactoryProposal] = field(init=False, repr=False)
    _tasks_by_number: dict[int, FactoryTask] = field(init=False, repr=False)
    _prs_by_id: dict[str, FactoryPr] = field(init=False, repr=False)
    _mirrors: dict[str, FactoryDecision] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.decisions = [d for d in self.decisions if d.status == "open"]
        self.proposals = [p for p in self.proposals if p.status in PROPOSAL_OPEN_STATUSES]
        self._decisions_by_number = {d.number: d for d in self.decisions}
        self._proposals_by_number = {p.number: p for p in self.proposals}
        self._tasks_by_number = {t.number: t for t in self.tasks}
        self._prs_by_id = {norm_id(p.id): p for p in self.prs}
        self._mirrors = {
            (d.subject or "").lower(): d
            for d in self.decisions
            if d.source == "proposal" and (d.subject or "").lower().startswith("proposal:")
        }

    @classmethod
    def load(cls, deps: FactoryDeps, factory_id: str, now: float) -> Facts:
        """Read what the waits need (synchronous; a handful of row reads)."""
        from omnigent.server.factory.control import find_project_pl

        store = deps.factory_store
        projects = {p.slug: p for p in store.list_projects(factory_id)}
        leads: dict[str, str | None] = {}
        for slug, project in projects.items():
            if project.status != "active":
                continue
            pl = find_project_pl(deps, factory_id, slug)
            leads[slug] = pl.id if pl is not None else project.pl_session_id
        return cls(
            projects=projects,
            prs=store.list_prs(factory_id),
            decisions=store.list_decisions(factory_id=factory_id, status="open", limit=1000),
            proposals=store.list_proposals(
                factory_id, status=sorted(PROPOSAL_OPEN_STATUSES), limit=500
            ),
            tasks=store.list_tasks(factory_id),
            loops=store.list_loops(factory_id, states=sorted(BAD_LOOP_STATES)),
            leads=leads,
            now=now,
        )

    def decision(self, number: int) -> FactoryDecision | None:
        """:returns: Open decision ``D-<number>``, else ``None``."""
        return self._decisions_by_number.get(number)

    def proposal(self, number: int) -> FactoryProposal | None:
        """:returns: Open proposal ``C-<number>``, else ``None``."""
        return self._proposals_by_number.get(number)

    def mirror(self, proposal: FactoryProposal) -> FactoryDecision | None:
        """:returns: The open decision a person answers *proposal* with, else ``None``."""
        return self._mirrors.get(f"proposal:c-{proposal.number}")

    def task(self, number: int) -> FactoryTask | None:
        """:returns: Task ``T-<number>`` (any state), else ``None``."""
        return self._tasks_by_number.get(number)

    def pr(self, pr_id: str | None) -> FactoryPr | None:
        """:returns: The PR row, else ``None``."""
        return self._prs_by_id.get(norm_id(pr_id)) if pr_id else None

    def pr_number(self, number: int) -> FactoryPr | None:
        """:returns: The newest tracked PR with *number*, else ``None``."""
        found = [p for p in self.prs if p.number == number]
        return max(found, key=lambda p: p.created_at) if found else None


# ── Plain words ────────────────────────────────────────────────


def clip(text: str | None, limit: int = LABEL_MAX) -> str:
    """:returns: *text* on one line, at most *limit* characters."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _person_name(user_id: str) -> str:
    return user_id.split("@")[0] or user_id


def who_of_decision(decision: FactoryDecision) -> str:
    """:returns: Who answers *decision*: ``an owner``, ``an operator`` or a person."""
    if decision.claimed_by:
        return _person_name(decision.claimed_by)
    target = decision.assignee or "operators"
    if target == "owners":
        return "an owner"
    if target == "operators":
        return "an operator"
    return _person_name(target)


def _decision_words(decision: FactoryDecision) -> str:
    """``Approve C-16`` for a proposal's decision, else its question in a few words."""
    subject = (decision.subject or "").lower()
    if decision.source == "proposal" and subject.startswith("proposal:"):
        return f"Approve {subject.removeprefix('proposal:').upper()}"
    return clip(decision.question, QUESTION_WORDS_MAX)


def decision_label(decision: FactoryDecision) -> str:
    """:returns: e.g. ``D-45 (Approve C-16, needs an owner)``."""
    return f"{decision.ref} ({_decision_words(decision)}, needs {who_of_decision(decision)})"


def _proposal_wait(
    proposal: FactoryProposal, facts: Facts, *, task: str | None, prefix: str = ""
) -> Wait:
    """A proposal that waits on a person (its decision) or on the other lead."""
    mirror = facts.mirror(proposal)
    if mirror is not None:
        who = who_of_decision(mirror)
        return Wait(
            kind="proposal",
            ref=mirror.ref,
            label=clip(f"{prefix}waiting on {decision_label(mirror)}"),
            who=who,
            person=True,
            since=int(mirror.created_at),
            task=task,
            decision_id=mirror.id,
        )
    ref = f"C-{proposal.number}"
    if proposal.status == "awaiting_cosign":
        what, who, person = "the other lead's case against", "the other lead", False
    else:
        what, who, person = "a person's approval", "an operator", True
    return Wait(
        kind="proposal",
        ref=ref,
        label=clip(f"{prefix}waiting on {ref} ({clip(proposal.title, 50)}, {what})"),
        who=who,
        person=person,
        since=int(proposal.created_at),
        task=task,
    )


def merge_who(project: FactoryProject | None) -> str:
    """:returns: Who merges *project*'s PRs: its human owner, else an operator."""
    from omnigent.server.factory.charter import normalize_charter

    owner = normalize_charter(project.charter).get("human_owner") if project else None
    return _person_name(str(owner)) if owner else "an operator"


# ── Holds a lead declares ──────────────────────────────────────


@dataclass(frozen=True)
class HoldRef:
    """
    What a hold's ``on`` names.

    :param kind: ``decision``, ``proposal``, ``task``, ``pr``, ``project`` or ``text``.
    :param ref: ``D-45``, ``C-16``, ``T-3``, ``PR #12``, a slug, or ``None`` (text).
    """

    kind: str
    ref: str | None
    number: int | None = None


def parse_on(on: str, projects: Iterable[str] = ()) -> HoldRef:
    """
    Read what a hold waits on: the first ``D-n``, ``C-n``, ``PR #n``, ``T-n`` or
    project slug in *on*, else free text.

    :param projects: The factory's project slugs (a bare word naming one is a project).
    """
    text = " ".join((on or "").split())
    found: list[tuple[int, HoldRef]] = []
    for kind, pattern, ref in (
        (REF_KIND_DECISION, _DECISION_RE, "D-{}"),
        (REF_KIND_PROPOSAL, _PROPOSAL_RE, "C-{}"),
        (REF_KIND_TASK, _TASK_RE, "T-{}"),
        (REF_KIND_PR, _PR_RE, "PR #{}"),
    ):
        match = pattern.search(text)
        if match is not None:
            number = int(match.group(1))
            found.append((match.start(), HoldRef(kind, ref.format(number), number)))
    if found:
        return min(found, key=lambda pair: pair[0])[1]
    slugs = set(projects)
    lowered = text.lower().removeprefix("project:")
    if lowered in slugs:
        return HoldRef(REF_KIND_PROJECT, lowered)
    for word in _WORD_RE.findall(lowered):
        if word in slugs:
            return HoldRef(REF_KIND_PROJECT, word)
    return HoldRef(REF_KIND_TEXT, None)


def hold_record(
    on: str,
    *,
    note: str | None,
    until: int | None,
    by: str,
    by_kind: str,
    by_title: str | None,
    now: int,
    projects: Iterable[str] = (),
) -> dict[str, Any]:
    """:returns: The ``factory_tasks.hold`` JSON for a hold put on now."""
    parsed = parse_on(on, projects)
    record: dict[str, Any] = {
        "on": clip(on, HOLD_NOTE_MAX),
        "kind": parsed.kind,
        "ref": parsed.ref,
        "note": clip(note, HOLD_NOTE_MAX) or None,
        "until": until,
        "by": by,
        "by_kind": by_kind,
        "by_title": by_title,
        "since": now,
    }
    return record


def _ref_wait(
    hold: HoldRef, facts: Facts, *, task: str | None, prefix: str, explain: bool = True
) -> Wait | None:
    """What a held reference waits on now, or ``None`` when it closed (the hold lapsed)."""
    if hold.kind == REF_KIND_DECISION and hold.number is not None:
        decision = facts.decision(hold.number)
        if decision is None:
            return None
        subject = (decision.subject or "").lower()
        kind = "proposal" if subject.startswith("proposal:") else "decision"
        return Wait(
            kind=kind,
            ref=decision.ref,
            label=clip(f"{prefix}waiting on {decision_label(decision)}"),
            who=who_of_decision(decision),
            person=True,
            since=int(decision.created_at),
            task=task,
            decision_id=decision.id,
        )
    if hold.kind == REF_KIND_PROPOSAL and hold.number is not None:
        proposal = facts.proposal(hold.number)
        if proposal is None:
            return None
        return _proposal_wait(proposal, facts, task=task, prefix=prefix)
    if hold.kind == REF_KIND_TASK and hold.number is not None:
        other = facts.task(hold.number)
        if other is None or other.state not in OPEN_TASK_STATES:
            return None
        return Wait(
            kind="dependency",
            ref=other.ref,
            label=clip(f"{prefix}waiting on {other.ref} ({clip(other.title, 50)}, {other.state})"),
            who=f"{other.project_slug}'s lead",
            since=None,
            task=task,
        )
    if hold.kind == REF_KIND_PR and hold.number is not None:
        pr = facts.pr_number(hold.number)
        if pr is None or pr.state != "open":
            return None
        project = facts.projects.get(pr.project_slug or "")
        if pr.ready:
            who = merge_who(project)
            return Wait(
                kind="merge",
                ref=f"PR #{pr.number}",
                label=clip(f"{prefix}waiting on a person to merge PR #{pr.number}"),
                who=who,
                person=True,
                since=pr.ready_at,
                task=task,
            )
        return Wait(
            kind="dependency",
            ref=f"PR #{pr.number}",
            label=clip(f"{prefix}waiting on PR #{pr.number} ({clip(pr.title, 50)})"),
            who=f"{pr.project_slug}'s lead" if pr.project_slug else "its author",
            task=task,
        )
    if hold.kind == REF_KIND_PROJECT and hold.ref:
        project = facts.projects.get(hold.ref)
        if project is None or project.status != "active":
            return None
        if project.stage in (STAGE_MERGED, STAGE_DONE):
            return None
        return _dependency_wait(
            project, facts, task=task, prefix=prefix, mode="merged", explain=explain
        )
    return None


def explicit_hold(task: FactoryTask, facts: Facts, *, explain: bool = True) -> Wait | None:
    """
    The hold *task*'s lead (or a person) put on it, while it holds.

    It lapses when ``until`` passed, when what it names closed, or a day after
    a hold on free text was put on.

    :param explain: Say what a held-on project waits on in turn (one level).
    """
    hold = task.hold if isinstance(task.hold, Mapping) else None
    if not hold:
        return None
    now = facts.now
    until = hold.get("until")
    if isinstance(until, int | float) and now >= until:
        return None
    since = int(hold.get("since") or 0) or None
    by = str(
        hold.get("by_title") or ("a person" if hold.get("by_kind") == "human" else "its lead")
    )
    prefix = f"{task.ref} held: "
    parsed = HoldRef(
        str(hold.get("kind") or REF_KIND_TEXT),
        hold.get("ref"),
        ref_number(hold.get("ref")),
    )
    if parsed.kind != REF_KIND_TEXT:
        found = _ref_wait(parsed, facts, task=task.ref, prefix=prefix, explain=explain)
        if found is None:
            return None
        note = hold.get("note")
        label = found.label if not note else clip(f"{found.label} · {note}")
        return replace(found, kind="hold", label=label, since=since or found.since)
    if until is None and since is not None and now - since >= HOLD_TEXT_MAX_S:
        return None
    text = hold.get("note") or hold.get("on") or "on purpose"
    return Wait(
        kind="hold",
        ref=task.ref,
        label=clip(f"{prefix}{text}"),
        who=by,
        since=since,
        task=task.ref,
    )


def ref_number(ref: Any) -> int | None:
    """:returns: The number a ref ends with (``D-45`` -> 45), else ``None``."""
    match = re.search(r"(\d+)$", str(ref or ""))
    return int(match.group(1)) if match else None


def update_holds(
    project: FactoryProject, facts: Facts, *, explain: bool = True
) -> list[tuple[Wait, set[str]]]:
    """
    What the blocker of the lead's newest update names, while it is open.

    :returns: ``(wait, the task refs it names)`` pairs; an empty set holds
        every task of the project.
    """
    update = project.lead_update if isinstance(project.lead_update, Mapping) else None
    blocker = str((update or {}).get("blocker") or "").strip()
    if not blocker:
        return []
    tasks = {f"T-{m}" for m in _TASK_RE.findall(blocker)}
    since = int((update or {}).get("at") or 0) or None
    out: list[tuple[Wait, set[str]]] = []
    seen: set[str] = set()
    for kind, pattern in (
        (REF_KIND_DECISION, _DECISION_RE),
        (REF_KIND_PROPOSAL, _PROPOSAL_RE),
        (REF_KIND_PR, _PR_RE),
    ):
        for match in pattern.finditer(blocker):
            number = int(match.group(1))
            ref = HoldRef(kind, None, number)
            found = _ref_wait(ref, facts, task=None, prefix="held by its lead: ", explain=explain)
            if found is None or found.ref in seen:
                continue
            seen.add(found.ref)
            out.append((replace(found, kind="hold", since=since or found.since), tasks))
    return out


# ── Dependencies and merges ────────────────────────────────────


def _dependency_wait(
    target: FactoryProject,
    facts: Facts,
    *,
    task: str | None,
    prefix: str = "",
    mode: str,
    why: str | None = None,
    explain: bool = True,
) -> Wait:
    """
    A project not there yet: its PR waits on a person (``merge``) or it is still building.

    :param explain: Say what the project itself waits on (one level down).
    """
    own = [p for p in facts.prs if p.project_slug == target.slug and p.state == "open"]
    ready = [p for p in own if p.ready]
    because = f" · {clip(why, 80)}" if why else ""
    if ready and mode != "ready":
        pr = min(ready, key=lambda p: p.ready_at or p.created_at)
        return Wait(
            kind="merge",
            ref=target.slug,
            label=clip(f"{prefix}waiting on {target.slug}'s merge (needs a person){because}"),
            who=merge_who(target),
            person=True,
            since=pr.ready_at or None,
            task=task,
        )
    inner = _first_reason(target, facts) if explain else None
    state = inner or (target.stage if target.stage != "queued" else "not started")
    return Wait(
        kind="dependency",
        ref=target.slug,
        label=clip(f"{prefix}waiting on {target.slug} ({state}){because}"),
        who=f"{target.slug}'s lead",
        since=None,
        task=task,
    )


def _first_reason(project: FactoryProject, facts: Facts) -> str | None:
    """One level down: the first thing a dependency itself waits on, in a few words."""
    found = project_waits(project, facts, depth=1)
    if not found:
        return None
    first = found[0]
    words = first.label.split(" · ", 1)[0]
    for head in (f"{first.task} held: " if first.task else None, "held by its lead: "):
        if head and words.startswith(head):
            words = f"held: {words[len(head) :]}"
    return clip(words, 100)


def unmet_dependencies(project: FactoryProject, facts: Facts) -> list[tuple[FactoryProject, str]]:
    """:returns: ``(dependency, mode)`` pairs *project* still waits for, in charter order."""
    from omnigent.server.factory.charter import project_depends_on
    from omnigent.server.factory.gating import dependency_met, resolve_alias

    depends_on, mode = project_depends_on(project)
    out: list[tuple[FactoryProject, str]] = []
    for slug in depends_on:
        target = resolve_alias(slug, facts.projects)
        if target is None or dependency_met(target, facts.prs, mode):
            continue
        out.append((target, mode))
    return out


def dependency_waits(project: FactoryProject, facts: Facts) -> list[Wait]:
    """The projects *project* depends on that are not there yet (merge or dependency waits)."""
    from omnigent.server.factory.charter import normalize_charter

    why = normalize_charter(project.charter).get("depends_why") or {}
    return [
        _dependency_wait(target, facts, task=None, mode=mode, why=why.get(target.slug))
        for target, mode in unmet_dependencies(project, facts)
    ]


# ── Decisions and proposals about a task or project ────────────


def _names(text: str, mine: re.Pattern[str], others: Sequence[re.Pattern[str]]) -> str | None:
    if mine.search(text):
        return "mine"
    if _TASK_RE.search(text) or any(p.search(text) for p in others):
        return "other"
    return None


def _about_task(
    decision: FactoryDecision,
    subjects: set[str],
    mine: re.Pattern[str],
    others: Sequence[re.Pattern[str]],
) -> bool:
    """A question about the task, or about no task in particular."""
    subject = (decision.subject or "").strip().lower()
    if subject.startswith("task:"):
        return subject in subjects
    for text in (decision.question, decision.context):
        found = _names(text or "", mine, others)
        if found is not None:
            return found == "mine"
    return True


def _task_pattern(task: FactoryTask) -> re.Pattern[str]:
    key = re.escape(task.key)
    names = [rf"(?<![\w-]){re.escape(task.ref)}(?![\w-])", rf"factory/[\w.-]+/{key}(?![\w-])"]
    if "-" in task.key or any(ch.isdigit() for ch in task.key):
        names.append(rf"(?<![\w-]){key}(?![\w-])")
    return re.compile("|".join(names), re.IGNORECASE)


def _names_task(decision: FactoryDecision, mine: re.Pattern[str]) -> bool:
    """Its question or context names the task (its ref, branch or distinct key)."""
    return any(mine.search(text or "") for text in (decision.question, decision.context))


def _holds_project(decision: FactoryDecision) -> bool:
    """A decision about a project that holds its work while it is open."""
    if decision.source not in PROJECT_SOURCES:
        return False
    return decision.blocking or decision.source == "proposal"


def decision_waits(task: FactoryTask, facts: Facts, *, lead_id: str | None) -> list[Wait]:
    """
    The open questions *task* waits on, oldest first.

    Its sessions asked them; its lead asked them about this task or about no
    task in particular; their subject is the task, or they name it; or they
    are about its project (a proposal that changes it, a member parked on an
    approval or failing to relaunch) and name no other task.
    """
    askers = {norm_id(s) for s in task.session_ids or [] if _is_id(s)}
    lead = norm_id(lead_id) if lead_id and _is_id(lead_id) else None
    subjects = {f"task:{task.ref}".lower(), f"task:{task.key}".lower()}
    mine = _task_pattern(task)
    others = [
        _task_pattern(t)
        for t in facts.tasks
        if t.project_slug == task.project_slug and t.id != task.id
    ]
    out: list[Wait] = []
    for decision in sorted(facts.decisions, key=lambda d: (d.created_at, d.number)):
        if decision.status != "open" or decision.source in ("loop", "done_check"):
            continue
        subject = (decision.subject or "").strip().lower()
        about = subject in subjects and decision.project_slug in (None, task.project_slug)
        asker = (
            norm_id(decision.asked_by_session_id)
            if decision.asked_by_session_id and _is_id(decision.asked_by_session_id)
            else None
        )
        blocking_agent = decision.blocking and decision.source in ("agent", "blocked_approval")
        same_project = decision.project_slug in (None, task.project_slug)
        held = (
            about
            or (blocking_agent and asker is not None and asker in askers)
            or (
                blocking_agent
                and asker is not None
                and asker == lead
                and _about_task(decision, subjects, mine, others)
            )
            or (blocking_agent and same_project and _names_task(decision, mine))
            or (
                decision.project_slug == task.project_slug
                and decision.source in WORK_SOURCES
                and (decision.blocking or decision.source == "proposal")
                and _about_task(decision, subjects, mine, others)
            )
        )
        if not held:
            continue
        kind = "proposal" if subject.startswith("proposal:") else "decision"
        out.append(
            Wait(
                kind=kind,
                ref=decision.ref,
                label=clip(f"waiting on {decision_label(decision)}"),
                who=who_of_decision(decision),
                person=True,
                since=int(decision.created_at),
                task=task.ref,
                decision_id=decision.id,
            )
        )
    return out


def _is_id(value: str) -> bool:
    try:
        norm_id(value)
    except ValueError:
        return False
    return True


def proposal_slugs(proposal: FactoryProposal) -> set[str]:
    """:returns: The projects a projects proposal changes (updates, splits, merges, cancels)."""
    out: set[str] = set()
    for op in proposal.ops or []:
        if not isinstance(op, Mapping):
            continue
        for key in ("slug", "into"):
            value = op.get(key)
            if isinstance(value, str) and value:
                out.add(value)
        for value in op.get("from") or []:
            if isinstance(value, str):
                out.add(value)
    return out


def proposal_waits(slug: str, facts: Facts, *, task: str | None = None) -> list[Wait]:
    """Open proposals that change project *slug* (their decision, or the other lead)."""
    out: list[Wait] = []
    for proposal in sorted(facts.proposals, key=lambda p: p.number):
        if proposal.status not in PROPOSAL_OPEN_STATUSES or proposal.kind != "projects":
            continue
        if slug not in proposal_slugs(proposal):
            continue
        out.append(_proposal_wait(proposal, facts, task=task))
    return out


# ── A task, a project ──────────────────────────────────────────


def _task_dependency_waits(task: FactoryTask, facts: Facts, *, explain: bool = True) -> list[Wait]:
    """``project:<slug>`` and ``decision:D-n`` dependencies of a task that are not met."""
    out: list[Wait] = []
    for dep in task.deps or []:
        text = str(dep)
        if text.startswith("project:"):
            target = facts.projects.get(text.removeprefix("project:"))
            if target is None:
                continue
            from omnigent.server.factory.gating import dependency_met

            if not dependency_met(target, facts.prs, "merged"):
                out.append(
                    _dependency_wait(target, facts, task=task.ref, mode="merged", explain=explain)
                )
        elif text.startswith("decision:"):
            number = ref_number(text)
            decision = facts.decision(number) if number is not None else None
            if decision is not None:
                out.append(
                    Wait(
                        kind="decision",
                        ref=decision.ref,
                        label=clip(f"waiting on {decision_label(decision)}"),
                        who=who_of_decision(decision),
                        person=True,
                        since=int(decision.created_at),
                        task=task.ref,
                        decision_id=decision.id,
                    )
                )
    return out


def _own_merge(task: FactoryTask, facts: Facts) -> Wait | None:
    """The task's PR is ready and only waits for a person to merge it."""
    pr = facts.pr(task.pr_id)
    if pr is None or pr.state != "open" or not pr.ready:
        return None
    project = facts.projects.get(task.project_slug)
    return Wait(
        kind="merge",
        ref=f"PR #{pr.number}",
        label=clip(f"waiting on a person to merge PR #{pr.number}"),
        who=merge_who(project),
        person=True,
        since=pr.ready_at,
        task=task.ref,
    )


def task_waits(task: FactoryTask, facts: Facts, *, lead_id: str | None = None) -> list[Wait]:
    """
    Everything *task* waits on now, most decisive first (empty: nothing the server knows of).

    :param lead_id: Its project lead's session (``None``: the facts' own).
    """
    if task.state not in OPEN_TASK_STATES:
        return []
    project = facts.projects.get(task.project_slug)
    if lead_id is None:
        lead_id = facts.leads.get(task.project_slug)
    found: list[Wait] = []
    held = explicit_hold(task, facts)
    if held is not None:
        found.append(held)
    found += decision_waits(task, facts, lead_id=lead_id)
    found += proposal_waits(task.project_slug, facts, task=task.ref)
    if project is not None:
        for wait, names in update_holds(project, facts):
            if not names or task.ref in names:
                found.append(replace(wait, task=task.ref))
    own = _own_merge(task, facts)
    if own is not None:
        found.append(own)
    found += _task_dependency_waits(task, facts)
    if project is not None and project.status == "active":
        found += [replace(w, task=task.ref) for w in dependency_waits(project, facts)]
    return _dedupe(found)


def _dedupe(waits: Iterable[Wait]) -> list[Wait]:
    """One item per ref, the first (most specific) kept."""
    out: list[Wait] = []
    seen: set[str] = set()
    for wait in waits:
        if wait.ref in seen:
            continue
        seen.add(wait.ref)
        out.append(wait)
    return out


def _stall_waits(project: FactoryProject, facts: Facts) -> list[Wait]:
    """Bad loops of the project and the decision a person answers for each."""
    from omnigent.server.factory.loops import task_label

    by_id = {norm_id(d.id): d for d in facts.decisions}
    tasks = {norm_id(t.id): t for t in facts.tasks}
    out: list[Wait] = []
    for loop in facts.loops:
        if loop.project_slug != project.slug or loop.state not in BAD_LOOP_STATES:
            continue
        decision = by_id.get(norm_id(loop.decision_id)) if loop.decision_id else None
        task = tasks.get(norm_id(loop.task_id)) if loop.task_id else None
        what = task_label(task) if task is not None else loop.kind
        reason = clip(loop.last_reason or loop.state, 60)
        if decision is not None:
            label = f"{what} {loop.state}: {reason} ({decision.ref})"
            who, ref, did = who_of_decision(decision), decision.ref, decision.id
        else:
            label, who, ref, did = f"{what} {loop.state}: {reason}", "an operator", what, None
        out.append(
            Wait(
                kind="stall",
                ref=ref,
                label=clip(label),
                who=who,
                person=True,
                since=int(loop.updated_at) or None,
                task=task.ref if task is not None else None,
                decision_id=did,
            )
        )
    return out


def _project_decisions(project: FactoryProject, facts: Facts) -> list[Wait]:
    """Open decisions about the project that hold its work (a stall's own is a ``stall``)."""
    out: list[Wait] = []
    for decision in sorted(facts.decisions, key=lambda d: (d.created_at, d.number)):
        if decision.project_slug != project.slug or not _holds_project(decision):
            continue
        subject = (decision.subject or "").lower()
        out.append(
            Wait(
                kind="proposal" if subject.startswith("proposal:") else "decision",
                ref=decision.ref,
                label=clip(f"waiting on {decision_label(decision)}"),
                who=who_of_decision(decision),
                person=True,
                since=int(decision.created_at),
                decision_id=decision.id,
            )
        )
    return out


def _own_merges(project: FactoryProject, facts: Facts) -> list[Wait]:
    out: list[Wait] = []
    for pr in sorted(facts.prs, key=lambda p: p.number):
        if pr.project_slug != project.slug or pr.state != "open" or not pr.ready:
            continue
        out.append(
            Wait(
                kind="merge",
                ref=f"PR #{pr.number}",
                label=f"waiting on a person to merge PR #{pr.number}",
                who=merge_who(project),
                person=True,
                since=pr.ready_at,
            )
        )
    return out


def capacity_wait(start: Mapping[str, Any] | None) -> Wait | None:
    """A queued project that waits for a free machine (``ProjectStartView`` phase ``pod_cap``)."""
    if not isinstance(start, Mapping) or start.get("phase") != "pod_cap":
        return None
    cap, ahead = start.get("pod_cap"), int(start.get("ahead") or 0)
    full = f"all {cap} machines in use" if cap else "every machine in use"
    spot = f", {ahead} ahead" if ahead else ""
    return Wait(
        kind="capacity",
        ref="pods",
        label=f"waiting for a free machine ({full}{spot})",
        who="the factory",
        since=start.get("since") if isinstance(start.get("since"), int) else None,
    )


def project_waits(
    project: FactoryProject,
    facts: Facts,
    *,
    start: Mapping[str, Any] | None = None,
    depth: int = 0,
) -> list[Wait]:
    """
    Why *project* is not moving, most decisive first (empty: nothing holds it).

    :param start: Its ``ProjectStartView`` (a queued project waiting for a pod).
    :param depth: ``1`` while explaining a dependency of another project (its own
        dependencies are not followed further).
    """
    if project.status != "active" or project.stage == STAGE_DONE:
        return []
    lead_id = facts.leads.get(project.slug)
    explain = depth == 0
    mine = [
        t
        for t in sorted(facts.tasks, key=lambda t: t.number)
        if t.project_slug == project.slug and t.state in OPEN_TASK_STATES
    ]
    # A lead's own hold says the most (which task, on what): it wins a shared ref.
    found: list[Wait] = [h for t in mine if (h := explicit_hold(t, facts, explain=explain))]
    found += _stall_waits(project, facts)
    found += [wait for wait, _ in update_holds(project, facts, explain=explain)]
    found += _project_decisions(project, facts)
    found += proposal_waits(project.slug, facts)
    for task in mine:
        if task.state in MOVING_TASK_STATES:
            found += decision_waits(task, facts, lead_id=lead_id)
            found += _task_dependency_waits(task, facts, explain=explain)
    found += _own_merges(project, facts)
    if depth == 0:
        found += dependency_waits(project, facts)
    capacity = capacity_wait(start)
    if capacity is not None:
        found.append(capacity)
    ranked = sorted(
        enumerate(_dedupe(found)),
        key=lambda pair: (_RANK.get(pair[1].kind, len(KINDS)), pair[0]),
    )
    return [wait for _, wait in ranked]


def one_line(waits: Sequence[Wait | Mapping[str, Any]]) -> str | None:
    """:returns: The first wait's label, with ``+N more``; ``None`` when nothing waits."""
    if not waits:
        return None
    first = waits[0]
    label = first.label if isinstance(first, Wait) else str(first.get("label") or "")
    more = len(waits) - 1
    return f"{label} +{more} more" if more > 0 else label


def task_hold_view(task: FactoryTask, facts: Facts | None = None) -> dict[str, Any] | None:
    """:returns: The task's hold as stored, with ``active`` when *facts* says it still holds."""
    hold = task.hold if isinstance(task.hold, Mapping) else None
    if not hold:
        return None
    out = {k: hold.get(k) for k in ("on", "kind", "ref", "note", "until", "by", "by_kind")}
    out["by_title"] = hold.get("by_title")
    out["since"] = hold.get("since")
    if facts is not None:
        found = explicit_hold(task, facts)
        out["active"] = found is not None
        out["label"] = found.label if found is not None else None
    return out


__all__ = [
    "HOLD_TEXT_MAX_S",
    "HOLD_UNTIL_MAX_S",
    "KINDS",
    "Facts",
    "HoldRef",
    "Wait",
    "capacity_wait",
    "clip",
    "decision_label",
    "decision_waits",
    "dependency_waits",
    "explicit_hold",
    "hold_record",
    "merge_who",
    "one_line",
    "parse_on",
    "project_waits",
    "proposal_slugs",
    "proposal_waits",
    "ref_number",
    "task_hold_view",
    "task_waits",
    "unmet_dependencies",
    "update_holds",
    "who_of_decision",
]
