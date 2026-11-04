"""Why a project or a task is not moving: what it waits on, and who must act.

One answer for every surface. :func:`task_waits` and :func:`project_waits` are
pure: they read :class:`Facts` (the factory's open decisions, open proposals,
projects, PRs, open tasks and bad loops, loaded once per pass) and return
:class:`Wait` items, the most decisive first:

- ``stall``: a loop of the project went bad and a person decides what next;
- ``decision``: an open question a person owes the task (its sessions or its
  lead asked it, its subject or its text names the task, or a member of the
  project is parked on an approval or will not relaunch); the project's list
  shows every open question about the project;
- ``proposal``: a proposal that changes the project waits on a person (its
  ``D-n``) or on the other lead's case against;
- ``hold``: the task's lead (or a person) holds it on purpose and says on what
  (``sys_factory_task(op="hold")``, stored on the task);
- ``merge``: the project's own PR, or the PR of a project it depends on,
  waits for a person to merge it (or for the server, on an owner-set
  auto-merge target);
- ``dependency``: a project it depends on is not there yet;
- ``capacity``: no free machine to start it on.

The blocker of the lead's newest update holds the tasks it names by ``T-n``
while the ``D-n``, ``C-n`` or ``PR #n`` it names is open; those waits keep
their own kind. A stored hold lapses when what it names closes or at its
``until`` (a day unless given, at most a week). Only a hold that a person
must act on, or that names no ref, counts as on purpose
(:attr:`Wait.on_purpose`): the stall clock then waits even with no IC open.
The loop engine's stall clock (:mod:`.task_pulse`) waits while a task has any
wait, and the snapshot shows each project's list (``ProjectView.waiting_on``)
so the cockpit, Home and Slack say the same thing.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
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
    from omnigent.server.factory.auto_merge import Targets, TargetsLike
    from omnigent.server.factory.context import FactoryDeps

KINDS: Final = ("stall", "decision", "proposal", "hold", "merge", "dependency", "capacity")
"""Wait kinds, in the order a project's list shows them."""
_RANK: Final = {kind: i for i, kind in enumerate(KINDS)}

HOLD_DEFAULT_S: Final = 24 * 3600
"""A hold without ``until`` lapses this long after it was put on (holding again renews it)."""
HOLD_UNTIL_MAX_S: Final = 7 * 24 * 3600
"""The latest ``until`` a hold may name."""
HOLD_NOTE_MAX: Final = 200
LABEL_MAX: Final = 160
QUESTION_WORDS_MAX: Final = 60

SOURCE_HOLD: Final = "hold"
"""A wait from the hold stored on the task."""
SOURCE_UPDATE: Final = "update"
"""A wait the blocker of the lead's update names."""

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
_PR_RE: Final = re.compile(
    r"(?:(?<![\w-])(?P<named>PR)\s*#?|(?<![\w#])#|!)(?P<n>\d+)(?![\w-])", re.IGNORECASE
)
"""``PR #12``, ``PR 12`` (named), or a bare ``#12`` / ``!12`` (a PR only when tracked)."""
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
        person's id, ``a person``, ``the project lead``, ``the other lead``,
        ``the server``, …
    :param person: A person must act (it belongs in "Needs you").
    :param since: When the wait began (epoch seconds), when known.
    :param task: The task that waits, e.g. ``T-16`` (``None``: the project).
    :param decision_id: The decision a person answers, when there is one.
    :param source: Where it comes from: :data:`SOURCE_HOLD` (stored on the task),
        :data:`SOURCE_UPDATE` (the lead's update blocker), ``None`` (inferred).
        Not serialized.
    :param on_purpose: A stored hold the stall clock trusts even with no IC open:
        a person must act on what it names, a person put it on, or it names
        no ref (it lapses at its ``until``). Not serialized.
    """

    kind: str
    ref: str
    label: str
    who: str
    person: bool = False
    since: int | None = None
    task: str | None = None
    decision_id: str | None = None
    source: str | None = None
    on_purpose: bool = False

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
    :param tasks: Its open tasks (closed ones may be given too; they never hold).
    :param loops: Its bad loops (stalled, exhausted, thrashing).
    :param leads: Project slug -> its lead's session id (the lead-asked rule).
    :param targets: The owner-set auto-merge targets the server merges into now
        (:func:`auto_targets`), per subtree too: a ready factory PR into one waits
        on the server, not a person.
    """

    projects: Mapping[str, FactoryProject] = field(default_factory=dict)
    prs: Sequence[FactoryPr] = ()
    decisions: Sequence[FactoryDecision] = ()
    proposals: Sequence[FactoryProposal] = ()
    tasks: Sequence[FactoryTask] = ()
    loops: Sequence[FactoryLoop] = ()
    leads: Mapping[str, str | None] = field(default_factory=dict)
    now: float = 0.0
    targets: TargetsLike = frozenset()
    _decisions_by_number: dict[int, FactoryDecision] = field(init=False, repr=False)
    _decisions_by_id: dict[str, FactoryDecision] = field(init=False, repr=False)
    _proposals_by_number: dict[int, FactoryProposal] = field(init=False, repr=False)
    _tasks_by_number: dict[int, FactoryTask] = field(init=False, repr=False)
    _tasks_by_id: dict[str, FactoryTask] = field(init=False, repr=False)
    _open_by_project: dict[str, list[FactoryTask]] = field(init=False, repr=False)
    _prs_by_id: dict[str, FactoryPr] = field(init=False, repr=False)
    _prs_by_project: dict[str, list[FactoryPr]] = field(init=False, repr=False)
    _mirrors: dict[str, FactoryDecision] = field(init=False, repr=False)
    _askers: dict[str, str | None] = field(init=False, repr=False)
    _patterns: dict[str, re.Pattern[str]] = field(init=False, repr=False)
    _project_names: dict[str, re.Pattern[str] | None] = field(init=False, repr=False)
    _inner: dict[str, str | None] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.decisions = sorted(
            (d for d in self.decisions if d.status == "open"),
            key=lambda d: (d.created_at, d.number),
        )
        self.proposals = sorted(
            (p for p in self.proposals if p.status in PROPOSAL_OPEN_STATUSES),
            key=lambda p: p.number,
        )
        self._decisions_by_number = {d.number: d for d in self.decisions}
        self._decisions_by_id = {norm_id(d.id): d for d in self.decisions}
        self._proposals_by_number = {p.number: p for p in self.proposals}
        self._tasks_by_number = {t.number: t for t in self.tasks}
        self._tasks_by_id = {norm_id(t.id): t for t in self.tasks}
        self._open_by_project = {}
        for row in sorted(self.tasks, key=lambda t: t.number):
            if row.state in OPEN_TASK_STATES:
                self._open_by_project.setdefault(row.project_slug, []).append(row)
        self._prs_by_id = {norm_id(p.id): p for p in self.prs}
        self._prs_by_project = {}
        for pr in sorted(self.prs, key=lambda p: p.number):
            self._prs_by_project.setdefault(pr.project_slug or "", []).append(pr)
        self._mirrors = {
            (d.subject or "").lower(): d
            for d in self.decisions
            if d.source == "proposal" and (d.subject or "").lower().startswith("proposal:")
        }
        self._askers = {d.id: _norm_or_none(d.asked_by_session_id) for d in self.decisions}
        self._patterns = {}
        self._project_names = {}
        self._inner = {}

    @classmethod
    def load(
        cls,
        deps: FactoryDeps,
        factory_id: str,
        now: float,
        *,
        tasks: Sequence[FactoryTask] | None = None,
        targets: TargetsLike = frozenset(),
    ) -> Facts:
        """
        Read what the waits need (synchronous; a handful of row reads).

        :param tasks: The factory's tasks when the caller already read them (only
            the open ones are kept); else the open tasks are read.
        :param targets: The auto-merge targets (:func:`auto_targets`; none: a
            person merges every PR).
        """
        store = deps.factory_store
        projects = {p.slug: p for p in store.list_projects(factory_id)}
        rows = (
            [t for t in tasks if t.state in OPEN_TASK_STATES]
            if tasks is not None
            else store.list_tasks(factory_id, states=sorted(OPEN_TASK_STATES))
        )
        return cls(
            projects=projects,
            prs=store.list_prs(factory_id),
            decisions=store.list_decisions(factory_id=factory_id, status="open", limit=1000),
            proposals=store.list_proposals(
                factory_id, status=sorted(PROPOSAL_OPEN_STATUSES), limit=500
            ),
            tasks=rows,
            loops=store.list_loops(factory_id, states=sorted(BAD_LOOP_STATES)),
            leads=project_leads(deps, factory_id, projects.values()),
            now=now,
            targets=targets,
        )

    def decision(self, number: int) -> FactoryDecision | None:
        """:returns: Open decision ``D-<number>``, else ``None``."""
        return self._decisions_by_number.get(number)

    def decision_by_id(self, decision_id: str | None) -> FactoryDecision | None:
        """:returns: The open decision with row id *decision_id*, else ``None``."""
        return self._decisions_by_id.get(norm_id(decision_id)) if decision_id else None

    def proposal(self, number: int) -> FactoryProposal | None:
        """:returns: Open proposal ``C-<number>``, else ``None``."""
        return self._proposals_by_number.get(number)

    def mirror(self, proposal: FactoryProposal) -> FactoryDecision | None:
        """:returns: The open decision a person answers *proposal* with, else ``None``."""
        return self._mirrors.get(f"proposal:c-{proposal.number}")

    def task(self, number: int) -> FactoryTask | None:
        """:returns: Task ``T-<number>`` (open, or closed when given), else ``None``."""
        return self._tasks_by_number.get(number)

    def task_by_id(self, task_id: str | None) -> FactoryTask | None:
        """:returns: The task with row id *task_id*, else ``None``."""
        return self._tasks_by_id.get(norm_id(task_id)) if task_id else None

    def open_tasks(self, slug: str) -> list[FactoryTask]:
        """:returns: Project *slug*'s open tasks, by number."""
        return self._open_by_project.get(slug, [])

    def pr(self, pr_id: str | None) -> FactoryPr | None:
        """:returns: The PR row, else ``None``."""
        return self._prs_by_id.get(norm_id(pr_id)) if pr_id else None

    def prs_of(self, slug: str) -> list[FactoryPr]:
        """:returns: Project *slug*'s PRs (any state), by number."""
        return self._prs_by_project.get(slug, [])

    def pr_number(self, number: int) -> FactoryPr | None:
        """:returns: The newest tracked PR with *number*, else ``None``."""
        found = [p for p in self.prs if p.number == number]
        return max(found, key=lambda p: p.created_at) if found else None

    def merges_itself(self, pr: FactoryPr) -> bool:
        """:returns: Whether the server, not a person, merges *pr* once it is ready."""
        if not self.targets:
            return False
        from omnigent.server.factory import auto_merge

        return auto_merge.merges_itself(pr, self.targets, self.projects.get(pr.project_slug or ""))

    def asker(self, decision: FactoryDecision) -> str | None:
        """:returns: The normalized session id that asked *decision*, else ``None``."""
        if decision.id in self._askers:
            return self._askers[decision.id]
        return _norm_or_none(decision.asked_by_session_id)

    def task_pattern(self, task: FactoryTask) -> re.Pattern[str]:
        """:returns: What names *task* in prose (its ref, branch or distinct key), cached."""
        found = self._patterns.get(task.id)
        if found is None:
            found = self._patterns[task.id] = re.compile(
                "|".join(_task_names(task)), re.IGNORECASE
            )
        return found

    def project_names(self, slug: str) -> re.Pattern[str] | None:
        """:returns: What names any open task of *slug* in prose (one pattern), cached."""
        if slug not in self._project_names:
            names = [n for t in self.open_tasks(slug) for n in _task_names(t)]
            self._project_names[slug] = (
                re.compile("|".join(names), re.IGNORECASE) if names else None
            )
        return self._project_names[slug]

    def inner_reason(self, project: FactoryProject) -> str | None:
        """:returns: The first thing *project* itself waits on, in a few words (cached)."""
        if project.slug not in self._inner:
            self._inner[project.slug] = _first_reason(project, self)
        return self._inner[project.slug]


def _norm_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return norm_id(value)
    except ValueError:
        return None


def auto_targets(
    app_state: Any, deps: FactoryDeps, factory_id: str, *, lineage: bool = True
) -> Targets:
    """
    The owner-set auto-merge targets the server merges into now, per subtree
    too: none while this server runs no merger (a person merges, as before).
    Synchronous; never raises.

    :param lineage: Read where the projects sit (``False``: the caller attaches its own).
    """
    from omnigent.server.factory import auto_merge

    try:
        if app_state is None or not auto_merge.merger_on(app_state):
            return auto_merge.Targets()
        return auto_merge.factory_targets(deps, factory_id, lineage=lineage)
    except Exception:  # noqa: BLE001 - a person merges: the waits say so
        return auto_merge.Targets()


def project_leads(
    deps: FactoryDeps, factory_id: str, projects: Iterable[FactoryProject]
) -> dict[str, str | None]:
    """
    Each active project's lead session: its row's ``pl_session_id``, else the
    live member labelled its project lead (the factory's members read once).
    """
    from omnigent.factory import LABEL_PROJECT, LABEL_ROLE, ROLE_PROJECT_LEAD
    from omnigent.server.factory.control import is_gone
    from omnigent.server.factory.membership import factory_members

    active = [p for p in projects if p.status == "active"]
    leads: dict[str, str | None] = {p.slug: p.pl_session_id for p in active}
    missing = {slug for slug, pl in leads.items() if not pl}
    if missing:
        for member in factory_members(factory_id, deps.conversation_store):
            slug = member.labels.get(LABEL_PROJECT)
            if (
                slug is not None
                and slug in missing
                and member.labels.get(LABEL_ROLE) == ROLE_PROJECT_LEAD
                and not is_gone(member)
            ):
                leads[slug] = member.id
    return leads


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


def _decision_wait(decision: FactoryDecision, *, task: str | None, prefix: str = "") -> Wait:
    """An open decision a person answers."""
    subject = (decision.subject or "").lower()
    return Wait(
        kind="proposal" if subject.startswith("proposal:") else "decision",
        ref=decision.ref,
        label=clip(f"{prefix}waiting on {decision_label(decision)}"),
        who=who_of_decision(decision),
        person=True,
        since=int(decision.created_at),
        task=task,
        decision_id=decision.id,
    )


def _proposal_wait(
    proposal: FactoryProposal, facts: Facts, *, task: str | None, prefix: str = ""
) -> Wait:
    """A proposal that waits on a person (its decision) or on the other lead."""
    mirror = facts.mirror(proposal)
    if mirror is not None:
        return replace(_decision_wait(mirror, task=task, prefix=prefix), kind="proposal")
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


SERVER_MERGES: Final = "the server"
"""Who merges a ready PR into an owner-set auto-merge target."""


def _merge_wait(
    pr: FactoryPr, project: FactoryProject | None, facts: Facts, *, task: str | None, prefix: str
) -> Wait:
    """A ready PR that waits for its merge: a person's, or the server's on a target."""
    auto = facts.merges_itself(pr)
    what = SERVER_MERGES if auto else "a person"
    return Wait(
        kind="merge",
        ref=f"PR #{pr.number}",
        label=clip(f"{prefix}waiting on {what} to merge PR #{pr.number}"),
        who=SERVER_MERGES if auto else merge_who(project),
        person=not auto,
        since=pr.ready_at,
        task=task,
    )


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


def _first(found: Iterable[tuple[int, HoldRef]]) -> HoldRef | None:
    ordered = sorted(found, key=lambda pair: pair[0])
    return ordered[0][1] if ordered else None


def parse_on(
    on: str,
    projects: Iterable[str] = (),
    *,
    own: str | None = None,
    prs: Collection[int] | None = None,
) -> HoldRef:
    """
    Read what a hold waits on: a ``D-n`` or ``C-n`` first (who must act), then a
    ``T-n`` or ``PR #n``, then a project slug, else free text. The earliest of
    each pair wins.

    :param projects: The factory's project slugs (a bare word naming one is a project).
    :param own: The held task's ref (``T-16 waits on C-16`` names C-16, not T-16).
    :param prs: The PR numbers the factory tracks: a bare ``#n`` naming none is text
        (``the CI run #4``). ``None``: every ``#n`` is a PR.
    """
    text = " ".join((on or "").split())
    people: list[tuple[int, HoldRef]] = []
    for kind, pattern, fmt in (
        (REF_KIND_DECISION, _DECISION_RE, "D-{}"),
        (REF_KIND_PROPOSAL, _PROPOSAL_RE, "C-{}"),
    ):
        match = pattern.search(text)
        if match is not None:
            number = int(match.group(1))
            people.append((match.start(), HoldRef(kind, fmt.format(number), number)))
    found = _first(people)
    if found is not None:
        return found
    work: list[tuple[int, HoldRef]] = []
    for match in _TASK_RE.finditer(text):
        number = int(match.group(1))
        if own is not None and f"T-{number}" == own.upper():
            continue
        work.append((match.start(), HoldRef(REF_KIND_TASK, f"T-{number}", number)))
        break
    for match in _PR_RE.finditer(text):
        number = int(match.group("n"))
        if not match.group("named") and prs is not None and number not in prs:
            continue
        work.append((match.start(), HoldRef(REF_KIND_PR, f"PR #{number}", number)))
        break
    found = _first(work)
    if found is not None:
        return found
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
    own: str | None = None,
    prs: Collection[int] | None = None,
) -> dict[str, Any]:
    """
    :returns: The ``factory_tasks.hold`` JSON for a hold put on now; without
        *until* it lapses after :data:`HOLD_DEFAULT_S`.
    """
    parsed = parse_on(on, projects, own=own, prs=prs)
    return {
        "on": clip(on, HOLD_NOTE_MAX),
        "kind": parsed.kind,
        "ref": parsed.ref,
        "note": clip(note, HOLD_NOTE_MAX) or None,
        "until": until if until is not None else now + HOLD_DEFAULT_S,
        "by": by,
        "by_kind": by_kind,
        "by_title": by_title,
        "since": now,
    }


def _ref_wait(
    hold: HoldRef, facts: Facts, *, task: str | None, prefix: str, explain: bool = True
) -> Wait | None:
    """What a held reference waits on now, or ``None`` when it closed (the hold lapsed)."""
    if hold.kind == REF_KIND_DECISION and hold.number is not None:
        decision = facts.decision(hold.number)
        if decision is None:
            return None
        return _decision_wait(decision, task=task, prefix=prefix)
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
            return _merge_wait(pr, project, facts, task=task, prefix=prefix)
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


def _stored_hold(task: FactoryTask) -> Mapping[str, Any] | None:
    hold = task.hold if isinstance(task.hold, Mapping) else None
    return hold or None


def hold_until(hold: Mapping[str, Any]) -> int | None:
    """:returns: When a stored hold lapses at the latest (epoch seconds), else ``None``."""
    until = hold.get("until")
    if isinstance(until, int | float) and not isinstance(until, bool):
        return int(until)
    since = hold.get("since")
    if isinstance(since, int | float) and not isinstance(since, bool) and since:
        return int(since) + HOLD_DEFAULT_S
    return None


def _hold_state(
    task: FactoryTask, facts: Facts, *, explain: bool = True
) -> tuple[Wait | None, str | None]:
    """:returns: ``(its stored hold's wait, None)`` while it holds, else ``(None, why not)``."""
    hold = _stored_hold(task)
    if hold is None:
        return None, None
    until = hold_until(hold)
    if until is not None and facts.now >= until:
        return None, "its time limit passed"
    since = int(hold.get("since") or 0) or None
    human = hold.get("by_kind") == "human"
    by = str(hold.get("by_title") or ("a person" if human else "its lead"))
    prefix = f"{task.ref} held: "
    parsed = HoldRef(
        str(hold.get("kind") or REF_KIND_TEXT), hold.get("ref"), ref_number(hold.get("ref"))
    )
    if parsed.kind != REF_KIND_TEXT:
        found = _ref_wait(parsed, facts, task=task.ref, prefix=prefix, explain=explain)
        if found is None:
            return None, f"{parsed.ref or 'what it named'} closed"
        note = hold.get("note")
        label = found.label if not note else clip(f"{found.label} · {note}")
        return (
            replace(
                found,
                kind="hold",
                label=label,
                since=since or found.since,
                source=SOURCE_HOLD,
                on_purpose=found.person or human,
            ),
            None,
        )
    text = hold.get("note") or hold.get("on") or "on purpose"
    return (
        Wait(
            kind="hold",
            ref=task.ref,
            label=clip(f"{prefix}{text}"),
            who=by,
            since=since,
            task=task.ref,
            source=SOURCE_HOLD,
            on_purpose=True,
        ),
        None,
    )


def explicit_hold(task: FactoryTask, facts: Facts, *, explain: bool = True) -> Wait | None:
    """
    The hold *task*'s lead (or a person) put on it, while it holds.

    It lapses at its ``until`` (a day after it was put on, unless given) and
    when what it names closes. A hold on a ``T-n``, a PR in the works or a
    project is not on purpose: nobody must act on it, so a dead IC still stalls.

    :param explain: Say what a held-on project waits on in turn (one level).
    """
    return _hold_state(task, facts, explain=explain)[0]


def hold_lapse(task: FactoryTask, facts: Facts) -> str | None:
    """:returns: Why *task*'s stored hold no longer holds (``None``: it holds, or has none)."""
    return _hold_state(task, facts, explain=False)[1]


def ref_number(ref: Any) -> int | None:
    """:returns: The number a ref ends with (``D-45`` -> 45), else ``None``."""
    match = re.search(r"(\d+)$", str(ref or ""))
    return int(match.group(1)) if match else None


def update_holds(
    project: FactoryProject, facts: Facts, *, explain: bool = True
) -> list[tuple[Wait, set[str]]]:
    """
    What the blocker of the lead's newest update names, while it is open.

    The waits keep their own kind (a lead's word is not a hold the clock
    trusts). A PR of the project itself that is not ready is skipped: nobody
    else owes it anything.

    :returns: ``(wait, the task refs it names)`` pairs; an empty set holds no
        task (the project's list still shows it).
    """
    update = project.lead_update if isinstance(project.lead_update, Mapping) else None
    blocker = str((update or {}).get("blocker") or "").strip()
    if not blocker:
        return []
    tasks = {f"T-{m}" for m in _TASK_RE.findall(blocker)}
    since = int((update or {}).get("at") or 0) or None
    refs: list[HoldRef] = []
    for kind, pattern in ((REF_KIND_DECISION, _DECISION_RE), (REF_KIND_PROPOSAL, _PROPOSAL_RE)):
        refs += [HoldRef(kind, None, int(m.group(1))) for m in pattern.finditer(blocker)]
    for match in _PR_RE.finditer(blocker):
        pr = facts.pr_number(int(match.group("n")))
        if pr is None or (pr.project_slug == project.slug and not pr.ready):
            continue
        refs.append(HoldRef(REF_KIND_PR, None, pr.number))
    out: list[tuple[Wait, set[str]]] = []
    seen: set[str] = set()
    for ref in refs:
        found = _ref_wait(ref, facts, task=None, prefix="held by its lead: ", explain=explain)
        if found is None or found.ref in seen:
            continue
        seen.add(found.ref)
        out.append((replace(found, since=since or found.since, source=SOURCE_UPDATE), tasks))
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
    A project not there yet: its PR waits on a merge (``merge``) or it is still building.

    :param explain: Say what the project itself waits on (one level down).
    """
    ready = [p for p in facts.prs_of(target.slug) if p.state == "open" and p.ready]
    because = f" · {clip(why, 80)}" if why else ""
    if ready and mode != "ready":
        pr = min(ready, key=lambda p: p.ready_at or p.created_at)
        if all(facts.merges_itself(p) for p in ready):
            label = f"{prefix}waiting on {SERVER_MERGES} to merge {target.slug}{because}"
            who, person = SERVER_MERGES, False
        else:
            label = f"{prefix}waiting on {target.slug}'s merge (needs a person){because}"
            who, person = merge_who(target), True
        return Wait(
            kind="merge",
            ref=target.slug,
            label=clip(label),
            who=who,
            person=person,
            since=pr.ready_at or None,
            task=task,
        )
    inner = facts.inner_reason(target) if explain else None
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
    from omnigent.server.factory.charter import change_dependency, project_depends_on
    from omnigent.server.factory.gating import dependency_met, resolve_alias

    depends_on, mode = project_depends_on(project)
    out: list[tuple[FactoryProject, str]] = []
    for slug in depends_on:
        if change_dependency(slug) is not None:
            continue  # a change, not a project: :func:`unmet_changes`
        target = resolve_alias(slug, facts.projects)
        if target is None or dependency_met(target, facts.prs_of(target.slug), mode, facts.tasks):
            continue
        out.append((target, mode))
    return out


def unmet_changes(project: FactoryProject, facts: Facts) -> list[str]:
    """:returns: The changes (``PR-9876``) *project* depends on that have not merged yet."""
    from omnigent.server.factory.charter import change_dependency, project_depends_on
    from omnigent.server.factory.gating import change_met

    depends_on, _mode = project_depends_on(project)
    return [
        item
        for item in depends_on
        if (number := change_dependency(item)) is not None
        and not change_met(number, facts.prs, project.repo)
    ]


def dependency_waits(project: FactoryProject, facts: Facts) -> list[Wait]:
    """
    The projects *project* depends on that are not there yet (merge or dependency
    waits), then the changes it depends on that have not merged.
    """
    from omnigent.server.factory.charter import normalize_charter

    why = normalize_charter(project.charter).get("depends_why") or {}
    out = [
        _dependency_wait(target, facts, task=None, mode=mode, why=why.get(target.slug))
        for target, mode in unmet_dependencies(project, facts)
    ]
    for item in unmet_changes(project, facts):
        because = f" ({clip(why[item], 120)})" if why.get(item) else ""
        out.append(
            Wait(
                kind="dependency",
                ref=item,
                label=clip(f"waiting on {item} to merge{because}"),
                who="whoever merges it",
            )
        )
    return out


# ── Decisions and proposals about a task or project ────────────


def _names(text: str, mine: re.Pattern[str], tasks: re.Pattern[str] | None) -> str | None:
    """``mine`` when *text* names the task, ``other`` when it names another one."""
    if mine.search(text):
        return "mine"
    if _TASK_RE.search(text) or (tasks is not None and tasks.search(text)):
        return "other"
    return None


def _about_task(
    decision: FactoryDecision,
    subjects: set[str],
    mine: re.Pattern[str],
    tasks: re.Pattern[str] | None,
) -> bool:
    """
    A question about the task, or about no task in particular.

    :param tasks: Names any open task of the project (the task's own included:
        once *mine* did not match, a match names another task).
    """
    subject = (decision.subject or "").strip().lower()
    if subject.startswith("task:"):
        return subject in subjects
    for text in (decision.question, decision.context):
        found = _names(text or "", mine, tasks)
        if found is not None:
            return found == "mine"
    return True


def _task_names(task: FactoryTask) -> list[str]:
    key = re.escape(task.key)
    names = [rf"(?<![\w-]){re.escape(task.ref)}(?![\w-])", rf"factory/[\w.-]+/{key}(?![\w-])"]
    if "-" in task.key or any(ch.isdigit() for ch in task.key):
        names.append(rf"(?<![\w-]){key}(?![\w-])")
    return names


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
    askers = {a for s in task.session_ids or [] if (a := _norm_or_none(s))}
    lead = _norm_or_none(lead_id)
    subjects = {f"task:{task.ref}".lower(), f"task:{task.key}".lower()}
    mine = facts.task_pattern(task)
    names = facts.project_names(task.project_slug)
    out: list[Wait] = []
    for decision in facts.decisions:
        if decision.source in ("loop", "done_check"):
            continue
        same_project = decision.project_slug in (None, task.project_slug)
        subject = (decision.subject or "").strip().lower()
        asker = facts.asker(decision)
        blocking_agent = decision.blocking and decision.source in ("agent", "blocked_approval")
        held = (
            (subject in subjects and same_project)
            or (blocking_agent and asker is not None and asker in askers)
            or (
                blocking_agent
                and asker is not None
                and asker == lead
                and _about_task(decision, subjects, mine, names)
            )
            or (blocking_agent and same_project and _names_task(decision, mine))
            or (
                decision.project_slug == task.project_slug
                and decision.source in WORK_SOURCES
                and (decision.blocking or decision.source == "proposal")
                and _about_task(decision, subjects, mine, names)
            )
        )
        if held:
            out.append(_decision_wait(decision, task=task.ref))
    return out


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
    return [
        _proposal_wait(proposal, facts, task=task)
        for proposal in facts.proposals
        if proposal.kind == "projects" and slug in proposal_slugs(proposal)
    ]


# ── A task, a project ──────────────────────────────────────────


def _task_dependency_waits(task: FactoryTask, facts: Facts, *, explain: bool = True) -> list[Wait]:
    """``project:<slug>`` and ``decision:D-n`` dependencies of a task that are not met."""
    from omnigent.server.factory.gating import dependency_met

    out: list[Wait] = []
    for dep in task.deps or []:
        text = str(dep)
        if text.startswith("project:"):
            target = facts.projects.get(text.removeprefix("project:"))
            if target is None or dependency_met(
                target, facts.prs_of(target.slug), "merged", facts.tasks
            ):
                continue
            out.append(
                _dependency_wait(target, facts, task=task.ref, mode="merged", explain=explain)
            )
        elif text.startswith("decision:"):
            number = ref_number(text)
            decision = facts.decision(number) if number is not None else None
            if decision is not None:
                out.append(replace(_decision_wait(decision, task=task.ref), kind="decision"))
    return out


def _own_merge(task: FactoryTask, facts: Facts) -> Wait | None:
    """The task's PR is ready and only waits for its merge."""
    pr = facts.pr(task.pr_id)
    if pr is None or pr.state != "open" or not pr.ready:
        return None
    project = facts.projects.get(task.project_slug)
    return _merge_wait(pr, project, facts, task=task.ref, prefix="")


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
            if task.ref in names:
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

    out: list[Wait] = []
    for loop in facts.loops:
        if loop.project_slug != project.slug or loop.state not in BAD_LOOP_STATES:
            continue
        decision = facts.decision_by_id(loop.decision_id)
        task = facts.task_by_id(loop.task_id)
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
    return [
        _decision_wait(decision, task=None)
        for decision in facts.decisions
        if decision.project_slug == project.slug and _holds_project(decision)
    ]


def _own_merges(project: FactoryProject, facts: Facts) -> list[Wait]:
    return [
        _merge_wait(pr, project, facts, task=None, prefix="")
        for pr in facts.prs_of(project.slug)
        if pr.state == "open" and pr.ready
    ]


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
    mine = facts.open_tasks(project.slug)
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
    hold = _stored_hold(task)
    if hold is None:
        return None
    out = {k: hold.get(k) for k in ("on", "kind", "ref", "note", "until", "by", "by_kind")}
    out["by_title"] = hold.get("by_title")
    out["since"] = hold.get("since")
    out["until"] = hold_until(hold)
    if facts is not None:
        found = explicit_hold(task, facts)
        out["active"] = found is not None
        out["label"] = found.label if found is not None else None
    return out


__all__ = [
    "HOLD_DEFAULT_S",
    "HOLD_UNTIL_MAX_S",
    "KINDS",
    "SERVER_MERGES",
    "SOURCE_HOLD",
    "SOURCE_UPDATE",
    "Facts",
    "HoldRef",
    "Wait",
    "auto_targets",
    "capacity_wait",
    "clip",
    "decision_label",
    "decision_waits",
    "dependency_waits",
    "explicit_hold",
    "hold_lapse",
    "hold_record",
    "hold_until",
    "merge_who",
    "one_line",
    "parse_on",
    "project_leads",
    "project_waits",
    "proposal_slugs",
    "proposal_waits",
    "ref_number",
    "task_hold_view",
    "task_waits",
    "unmet_changes",
    "unmet_dependencies",
    "update_holds",
    "who_of_decision",
]
