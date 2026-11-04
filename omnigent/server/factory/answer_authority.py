"""A person's answer to a decision is the approval of the proposal that carries it out.

An agent asks a person (``sys_factory_ask``, a ``D-<n>``); the person
answers; the agent files the project change the answer calls for with
``sys_factory_project(reply_to="D-<n>")``. That proposal applies at once on
the person's behalf, with Undo for the usual window and a line in the Room: it
never waits for a second approval of what the person already decided.

Guards (anything outside them waits as before, with what the answer did not
cover named in a warning):

- the decision is an agent's question, answered by a person (not the eval
  harness, not the server) in the last :data:`ANSWER_WINDOW_S`; a person's
  change of direction from a checkpoint (``K-<n>``, still in force) counts
  as their answer, its option and direction as what they chose;
- the proposal comes from an agent the answer was given to: the asker, the
  session it was delivered to, the top-level lead above either, or the
  factory's own lead (it gets an info copy of every answer);
- only the changes the answer authorised, field by field:

  - the chosen option names a project (its label, description or
    ``project:<slug>`` target): a create of it, or an update of any of its
    fields but money and its owner;
  - the decision's project, the asker's project, or one the answer's free text
    names: only routine upkeep (:data:`ANSWER_SCOPE_FIELDS`: scope paths,
    constraints, DoD, priority, dependencies …); a goal or outcome rewrite
    waits;
  - a name the person negated ("do not touch api") names nothing;
  - never: a split, merge or cancel (no Undo brings them back), money, another
    repo or base, a project's owner (:data:`OWN_APPROVAL_FIELDS`), or a bet;

- the person may approve project changes (an operator or owner);
- one proposal per answer: the first that claims it (pending, applied or
  undone) carries it out, and any later one waits, also when two arrive at
  once (:func:`claimed_by` runs after the claim is stored).

The audit is the same a person's own click leaves: the proposal's
``on_behalf_of`` is the person, ``result.authority`` holds who answered, when,
which decision and which option, and the ``proposal.opened`` /
``proposal.applied`` events carry it too. The answer itself went through a
human-only route (``HUMAN_ORIGIN``); this module only reads it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import LABEL_PROJECT
from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import autonomy
from omnigent.server.factory.actor import decision_ref, project_thread
from omnigent.server.factory.control import clip, human_name, same_id
from omnigent.stores.factory_store import now_s

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryDecision
    from omnigent.server.factory.agent_scope import AgentCaller
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.proposals import Change, Plan

ANSWER_WINDOW_S: Final = 24 * 3600
"""An answer older than this no longer approves a proposal on its own."""
DECISION_REF_RE: Final = re.compile(r"^\s*D-([1-9][0-9]{0,8})\s*$")
CHECKPOINT_REF_RE: Final = re.compile(r"^\s*K-([1-9][0-9]{0,8})\s*$")
"""A person's change of direction (``K-n``) approves like an answer to a ``D-n``."""
NOT_A_PERSON: Final = frozenset({"server", "eval-auto", "factory", ""})
"""``answered_by`` values that are not a person (the eval harness answers as ``eval-auto``,
decide-first as ``factory``: :mod:`.decide_first`)."""
_ROLE_SUFFIX_RE: Final = re.compile(r"\s+\((?:viewer|operator|owner|creator)\)$")
CARRIED_OPS: Final = frozenset({"create", "update"})
"""Ops an answer can carry: the ones an Undo takes back."""
ANSWER_SCOPE_FIELDS: Final = autonomy.ROUTINE_UPDATE_FIELDS | autonomy.DEPENDS_FIELDS
"""Fields an answer carries for a project its chosen option does not name (the
decision's or the asker's project, or one its free text names): routine upkeep."""
OWN_APPROVAL_FIELDS: Final = frozenset({"budgets", "human_owner"})
"""Fields no answer carries: money and who owns a project need their own approval."""
CLAIM_STATUSES: Final = ("pending", "applied", "undone")
"""A proposal in these that holds ``result.authority`` has carried out (or is carrying
out) that answer; one that was refused, rejected or withdrawn gives it back."""
_NEGATION_RE: Final = re.compile(
    r"\b(?:not|never|no|nor|without|except|excluding|avoid|skip|[a-z]+n't|dont|cant|wont)\b"
)
"""A word that negates a slug named shortly after it ("do not touch api")."""
_NEGATION_WORDS: Final = 4
"""How many words before a slug a negation reaches (within its sentence)."""
_CLAUSE_END_RE: Final = re.compile(r"[.;!?\n]")


def is_decision_ref(value: str | None) -> bool:
    """:returns: Whether *value* names a decision (``"D-42"``) or a checkpoint (``"K-3"``)."""
    return bool(value and (DECISION_REF_RE.match(value) or CHECKPOINT_REF_RE.match(value)))


@dataclass(frozen=True)
class _Delivered:
    """Where a change of direction's re-plan went (what :func:`_given_to` reads)."""

    to_id: str | None
    data: Mapping[str, Any] = field(default_factory=dict)


def is_direction(deps: FactoryDeps, factory_id: str, value: str | None) -> bool:
    """Whether *value* names a person's change of direction (``K-n``), not the factory's own."""
    match = CHECKPOINT_REF_RE.match(value or "")
    if match is None:
        return False
    row = deps.factory_store.get_checkpoint_by_number(factory_id, int(match.group(1)))
    return row is not None and row.decided_by_kind == "person" and bool(row.overrides)


def _direction(
    deps: FactoryDeps, factory_id: str, number: int
) -> tuple[FactoryDecision | None, Any, str | None]:
    """
    A person's change of direction ``K-<number>`` read as the answer it is.

    :returns: ``(the answer, where its re-plan went, why it approves nothing)``.
    """
    from omnigent.entities.factory import FactoryDecision
    from omnigent.server.factory.checkpoints import KIND_CHANGED

    store = deps.factory_store
    row = store.get_checkpoint_by_number(factory_id, number)
    if row is None:
        return None, None, "no such checkpoint in this factory"
    if row.decided_by_kind != "person" or not row.overrides:
        return None, None, "it is not a person's change of direction"
    if row.status != "answered":
        return None, None, f"it is {row.status}, not in force"
    answer = FactoryDecision(
        id=row.id,
        factory_id=row.factory_id,
        number=row.number,
        question=row.question,
        source="agent",
        status="answered",
        options=list(row.options or []),
        project_slug=row.project_slug,
        asked_by_session_id=row.asked_by_session_id,
        answer_option_id=row.choice_id,
        answer_text=row.direction,
        answered_by=row.decided_by,
        answered_at=row.created_at,
        created_at=row.created_at,
        subject=row.node_ref,
    )
    to_id = None
    for event in store.find_events(
        factory_id, KIND_CHANGED, 0, row.node_ref, limit=50, newest_first=True
    ):
        if (event.data or {}).get("checkpoint") == row.ref:
            to_id = getattr(event, "to_id", None)
            break
    return answer, _Delivered(to_id), None


@dataclass
class Authority:
    """
    What one answer approves of one proposal.

    :param refused: Why the answer approves nothing (not answered, too old,
        given to another agent …), else ``None``.
    :param covered: Indexes of the changes the answer covers.
    :param extra: One line per change it does not cover, saying why.
    :param spent_by: The proposal that already carried the answer out.
    """

    ref: str
    decision: FactoryDecision | None = None
    user_id: str | None = None
    name: str = "human"
    option: str | None = None
    refused: str | None = None
    covered: list[int] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    spent_by: str | None = None

    @property
    def carries(self) -> bool:
        """Whether the answer approves every change of the proposal."""
        return (
            self.refused is None
            and self.spent_by is None
            and not self.extra
            and bool(self.covered)
            and self.user_id is not None
        )

    @property
    def thread(self) -> str | None:
        """:returns: The decision's Room thread, else its project's."""
        if self.decision is None:
            return None
        if self.decision.thread:
            return self.decision.thread
        return project_thread(self.decision.project_slug) if self.decision.project_slug else None

    def audit(self) -> dict[str, Any]:
        """:returns: ``result.authority``: who answered, when, which decision and option."""
        decision = self.decision
        text = decision.answer_text if decision is not None else None
        return {
            "decision": self.ref,
            "decision_id": decision.id if decision is not None else None,
            "answered_by": self.user_id,
            "answered_by_name": self.name,
            "answered_at": decision.answered_at if decision is not None else None,
            "option": self.option,
            "text": clip(text, 300) if text else None,
        }

    def warning(self) -> str | None:
        """:returns: Why the proposal still waits despite naming the answer, or ``None``."""
        if self.carries:
            return None
        head = f"answer_scope: {self.ref}"
        if self.refused:
            return f"{head} does not approve this proposal: {self.refused}"
        if self.spent_by:
            return f"{head} was already carried out by {self.spent_by}; this one waits"
        return f"{head} does not cover: {'; '.join(self.extra)}"


# ── Reading the answer ─────────────────────────────────────────


def _answered_event(deps: FactoryDeps, decision: FactoryDecision) -> Any:
    rows = deps.factory_store.find_events(
        decision.factory_id,
        "decision.answered",
        0,
        f"decision:{decision.ref}",
        limit=1,
        newest_first=True,
    )
    return rows[0] if rows else None


def answerer(decision: FactoryDecision, event: Any) -> str | None:
    """
    The user id of the person who answered *decision*.

    The ``decision.answered`` event's ``by_id`` when it has one, else the
    stored ``answered_by`` without its role (answers from before ``by_id``).

    :returns: ``None`` when nobody, the server or the eval harness answered.
    """
    by = (decision.answered_by or "").strip()
    if by in NOT_A_PERSON:
        return None
    data = (event.data or {}) if event is not None else {}
    by_id = data.get("by_id")
    if isinstance(by_id, str) and by_id.strip():
        return by_id.strip()
    return _ROLE_SUFFIX_RE.sub("", by).strip() or None


def _option(decision: FactoryDecision) -> Mapping[str, Any] | None:
    chosen = decision.answer_option_id
    if not chosen:
        return None
    return next((o for o in decision.options or [] if str(o.get("id")) == chosen), None)


def _project_of(deps: FactoryDeps, session_id: str | None) -> str | None:
    from omnigent.server.factory.membership import root_conversation

    conv = deps.conversation_store.get_conversation(session_id) if session_id else None
    if conv is None:
        return None
    top = root_conversation(conv, deps.conversation_store)
    return top.labels.get(LABEL_PROJECT) or None


def _given_to(
    deps: FactoryDeps, decision: FactoryDecision, event: Any, caller: AgentCaller
) -> bool:
    """
    Whether the answer went to *caller*: the asker, the recipient, the lead
    above either, or the factory's own lead (it gets an info copy of every
    answer and plans every project of the factory).
    """
    from omnigent.server.factory.membership import root_conversation

    caller_id = caller.session.id
    if same_id(caller_id, caller.factory_id) and same_id(decision.factory_id, caller.factory_id):
        return True
    to_id = getattr(event, "to_id", None) if event is not None else None
    for sid in (decision.asked_by_session_id, to_id):
        if not sid:
            continue
        if same_id(sid, caller_id):
            return True
        conv: Conversation | None = deps.conversation_store.get_conversation(sid)
        if conv is not None and same_id(
            root_conversation(conv, deps.conversation_store).id, caller_id
        ):
            return True
    return False


def _negated(blob: str, start: int) -> bool:
    """Whether a negation word comes shortly before *start* in the same sentence."""
    before = _CLAUSE_END_RE.split(blob[:start])[-1]
    words = before.replace("\u2019", "'").split()[-_NEGATION_WORDS:]
    return any(_NEGATION_RE.fullmatch(w.strip(",:()\"'")) for w in words)


def named_slugs(texts: Iterable[str | None], known: Iterable[str]) -> set[str]:
    """
    :param texts: The option's label, description and target, or the answer's text.
    :param known: Project slugs (existing ones and the proposal's new ones).
    :returns: The slugs the texts name as whole words, e.g. ``{"api-version"}``;
        a slug every mention of which a person negated ("do not touch
        api-version") is left out.
    """
    blob = "\n".join(t for t in texts if t).lower()
    if not blob:
        return set()
    out: set[str] = set()
    for slug in known:
        pattern = rf"(?<![a-z0-9-]){re.escape(slug.lower())}(?![a-z0-9-])"
        if any(not _negated(blob, m.start()) for m in re.finditer(pattern, blob)):
            out.add(slug)
    return out


def claimed_by(
    deps: FactoryDeps, factory_id: str, ref: str, *, before: int | None = None
) -> str | None:
    """
    :param ref: The answer, e.g. ``"D-42"``.
    :param before: Only proposals filed before ``C-<before>`` (the one asking).
    :returns: The proposal that carried out (or is carrying out) answer *ref*,
        the earliest first, e.g. ``"C-16"``, else ``None``.
    """
    from omnigent.server.factory.actor import proposal_ref

    found: list[int] = []
    for row in deps.factory_store.list_proposals(
        factory_id, status=list(CLAIM_STATUSES), kind="projects", limit=500
    ):
        if before is not None and row.number >= before:
            continue
        authority = (row.result or {}).get("authority")
        if isinstance(authority, Mapping) and authority.get("decision") == ref:
            found.append(row.number)
    return proposal_ref(min(found)) if found else None


def spent_by(deps: FactoryDeps, factory_id: str, ref: str) -> str | None:
    """:returns: The proposal that already carried out answer *ref* (see :func:`claimed_by`)."""
    return claimed_by(deps, factory_id, ref)


# ── What the answer covers ─────────────────────────────────────


def _cover(
    change: Change, scope: set[str], option_named: set[str], text_named: set[str]
) -> str | None:
    """
    :param scope: The decision's and the asker's projects.
    :param option_named: Projects the chosen option names.
    :param text_named: Projects the answer's free text names.
    :returns: Why *change* is not covered, or ``None`` when the answer covers it.
    """
    what = change.summary()
    if change.op not in CARRIED_OPS:
        return f"{what}: a split, merge or cancel needs its own approval"
    if change.op == "create":
        slug = change.creates[0].slug
        if change.op_class == "owner":
            return (
                f"{what}: a project with a budget, or on another repo or base, "
                "needs its own owner approval"
            )
        if slug in option_named:
            return None
        return f"{what}: a new project needs the chosen option to name {slug}"
    slug = change.slug or ""
    fields = set(change.patch.model_fields_set) if change.patch is not None else set()
    if change.op_class == "owner" or "budgets" in fields:
        return f"{what}: money needs its own owner approval"
    if own := sorted(fields & OWN_APPROVAL_FIELDS):
        return f"{what}: {', '.join(own)} needs its own approval"
    if slug in option_named:
        return None
    if slug not in scope | text_named:
        return f"{what}: the answer does not name {slug}"
    if outside := sorted(fields - ANSWER_SCOPE_FIELDS):
        return (
            f"{what}: {', '.join(outside)} of {slug} needs its own approval "
            f"(the chosen option does not name {slug})"
        )
    return None


def authority_for(
    deps: FactoryDeps, root: Conversation, caller: AgentCaller, ref: str, plan: Plan
) -> Authority:
    """
    What answer *ref* approves of *plan* (synchronous: store reads; run it in a thread).

    :param ref: The proposal's ``reply_to``, e.g. ``"D-42"``, or a person's
        change of direction ``"K-3"`` (its choice and direction are the answer).
    """
    from omnigent.server.factory.people import viewer_level

    checkpoint = CHECKPOINT_REF_RE.match(ref)
    event: Any = None
    if checkpoint is not None:
        label = f"K-{int(checkpoint.group(1))}"
        authority = Authority(ref=label)
        decision, event, refused = _direction(deps, root.id, int(checkpoint.group(1)))
        if decision is None:
            authority.refused = refused
            return authority
    else:
        match = DECISION_REF_RE.match(ref)
        assert match is not None
        label = decision_ref(int(match.group(1)))
        authority = Authority(ref=label)
        decision = deps.factory_store.get_decision_by_number(root.id, int(match.group(1)))
    if decision is None or not same_id(decision.factory_id, root.id):
        authority.refused = "no such decision in this factory"
        return authority
    authority.decision = decision
    if decision.source != "agent":
        authority.refused = "it is not an agent's question"
        return authority
    if decision.status != "answered" or not decision.answered_at:
        authority.refused = f"it is {decision.status}, not answered"
        return authority
    if decision.answered_at < now_s() - ANSWER_WINDOW_S:
        authority.refused = "it was answered more than 24 hours ago"
        return authority
    if checkpoint is None:
        event = _answered_event(deps, decision)
    user_id = answerer(decision, event)
    if user_id is None:
        authority.refused = "no person answered it"
        return authority
    level = viewer_level(deps, root.id, user_id)
    if level < LEVEL_EDIT:
        authority.refused = f"{human_name(user_id)} may not approve project changes"
        return authority
    authority.user_id = user_id
    authority.name = human_name(user_id)
    option = _option(decision)
    authority.option = str(option.get("label") or option.get("id")) if option else None
    if not _given_to(deps, decision, event, caller):
        authority.refused = "the answer was given to another agent"
        return authority
    authority.spent_by = spent_by(deps, root.id, label)
    scope = {
        s for s in (decision.project_slug, _project_of(deps, decision.asked_by_session_id)) if s
    }
    known = {p.slug for p in deps.factory_store.list_projects(root.id)}
    known |= {p.slug for c in plan.changes for p in c.creates}
    option_named: set[str] = set()
    if option is not None:
        option_named = named_slugs(
            [str(option.get(k) or "") for k in ("label", "description", "target")], known
        )
        target = str(option.get("target") or "")
        if target.startswith("project:"):
            option_named.add(target.removeprefix("project:").strip())
    text_named = named_slugs([decision.answer_text], known)
    for change in plan.changes:
        why = _cover(change, scope, option_named, text_named)
        if why is None:
            authority.covered.append(change.index)
        else:
            authority.extra.append(why)
    return authority


def covers_all(authority: Authority | None, changes: Sequence[Change]) -> bool:
    """:returns: Whether *authority* approves every one of *changes*."""
    return authority is not None and authority.carries and len(authority.covered) == len(changes)


__all__ = [
    "ANSWER_SCOPE_FIELDS",
    "ANSWER_WINDOW_S",
    "CARRIED_OPS",
    "CHECKPOINT_REF_RE",
    "CLAIM_STATUSES",
    "DECISION_REF_RE",
    "NOT_A_PERSON",
    "OWN_APPROVAL_FIELDS",
    "Authority",
    "answerer",
    "authority_for",
    "claimed_by",
    "covers_all",
    "is_decision_ref",
    "is_direction",
    "named_slugs",
    "spent_by",
]
