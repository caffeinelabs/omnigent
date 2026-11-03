"""A person's answer to a decision is the approval of the proposal that carries it out.

An agent asks a person (``sys_factory_ask``, a ``D-<n>``); the person
answers; the agent files the project change the answer calls for with
``sys_factory_project(reply_to="D-<n>")``. That proposal applies at once on
the person's behalf, with Undo for the usual window and a line in the Room: it
never waits for a second approval of what the person already decided.

Guards (anything outside them waits as before, with what the answer did not
cover named in a warning):

- the decision is an agent's question, answered by a person (not the eval
  harness, not the server) in the last :data:`ANSWER_WINDOW_S`;
- the proposal comes from the agent the answer was given to: the asker, the
  session it was delivered to, or the top-level lead above either;
- only the ops the answer authorised: creates and updates of the decision's
  project, of the asker's project, and of projects the chosen option or the
  answer's text names; a split, merge or cancel never rides on an answer (no
  Undo brings them back);
- only within the class the person may approve: an owner-class op (money,
  another repo or base) needs an owner's answer, any other an operator's;
- one proposal per answer: once one carried it out (applied or undone), the
  next waits.

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
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE
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
NOT_A_PERSON: Final = frozenset({"server", "eval-auto", ""})
"""``answered_by`` values that are not a person (the eval harness answers as ``eval-auto``)."""
_ROLE_SUFFIX_RE: Final = re.compile(r"\s+\((?:viewer|operator|owner|creator)\)$")
CARRIED_OPS: Final = frozenset({"create", "update"})
"""Ops an answer can carry: the ones an Undo takes back."""


def is_decision_ref(value: str | None) -> bool:
    """:returns: Whether *value* names a decision, e.g. ``"D-42"``."""
    return bool(value and DECISION_REF_RE.match(value))


@dataclass
class Authority:
    """
    What one answer approves of one proposal.

    :param refused: Why the answer approves nothing (not answered, too old,
        given to another agent …), else ``None``.
    :param covered: Indexes of the changes the answer covers.
    :param extra: One line per change it does not cover, saying why.
    :param spent_by: The proposal that already carried the answer out.
    :param owner: The person may approve owner-class ops.
    """

    ref: str
    decision: FactoryDecision | None = None
    user_id: str | None = None
    name: str = "human"
    owner: bool = False
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


def _given_to(deps: FactoryDeps, decision: FactoryDecision, event: Any, caller_id: str) -> bool:
    """Whether the answer went to *caller_id*: the asker, the recipient, or the lead above."""
    from omnigent.server.factory.membership import root_conversation

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


def named_slugs(texts: Iterable[str | None], known: Iterable[str]) -> set[str]:
    """
    :param texts: The option's label, description and target, and the answer's text.
    :param known: Project slugs (existing ones and the proposal's new ones).
    :returns: The slugs the texts name as whole words, e.g. ``{"api-version"}``.
    """
    blob = "\n".join(t for t in texts if t).lower()
    if not blob:
        return set()
    return {
        slug
        for slug in known
        if re.search(rf"(?<![a-z0-9-]){re.escape(slug.lower())}(?![a-z0-9-])", blob)
    }


def spent_by(deps: FactoryDeps, factory_id: str, ref: str) -> str | None:
    """:returns: The proposal that already carried out answer *ref* (applied or undone)."""
    from omnigent.server.factory.actor import proposal_ref

    for row in deps.factory_store.list_proposals(
        factory_id, status=["applied", "undone"], kind="projects", limit=200
    ):
        authority = (row.result or {}).get("authority")
        if isinstance(authority, Mapping) and authority.get("decision") == ref:
            return proposal_ref(row.number)
    return None


# ── What the answer covers ─────────────────────────────────────


def _cover(change: Change, scope: set[str], named: set[str], authority: Authority) -> str | None:
    """:returns: Why *change* is not covered, or ``None`` when the answer covers it."""
    what = change.summary()
    if change.op not in CARRIED_OPS:
        return f"{what} (a split, merge or cancel waits for its own approval)"
    slug = change.slug if change.op == "update" else change.creates[0].slug
    allowed = scope | named if change.op == "update" else named
    if slug not in allowed:
        return f"{what} (the answer does not name {slug})"
    if change.op_class == "owner" and not authority.owner:
        return f"{what} (needs an owner; {authority.name} answered as an operator)"
    return None


def authority_for(
    deps: FactoryDeps, root: Conversation, caller: AgentCaller, ref: str, plan: Plan
) -> Authority:
    """
    What answer *ref* approves of *plan* (synchronous: store reads; run it in a thread).

    :param ref: The proposal's ``reply_to``, e.g. ``"D-42"``.
    """
    from omnigent.server.factory.people import viewer_level

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
    authority.owner = level >= LEVEL_MANAGE
    option = _option(decision)
    authority.option = str(option.get("label") or option.get("id")) if option else None
    if not _given_to(deps, decision, event, caller.session.id):
        authority.refused = "the answer was given to another agent"
        return authority
    authority.spent_by = spent_by(deps, root.id, label)
    scope = {
        s for s in (decision.project_slug, _project_of(deps, decision.asked_by_session_id)) if s
    }
    texts: list[str | None] = [decision.answer_text]
    if option is not None:
        texts += [str(option.get(k) or "") for k in ("label", "description", "target")]
        target = str(option.get("target") or "")
        if target.startswith("project:"):
            scope.add(target.removeprefix("project:").strip())
    known = {p.slug for p in deps.factory_store.list_projects(root.id)}
    known |= {p.slug for c in plan.changes for p in c.creates}
    named = named_slugs(texts, known)
    for change in plan.changes:
        why = _cover(change, scope, named, authority)
        if why is None:
            authority.covered.append(change.index)
        else:
            authority.extra.append(why)
    return authority


def covers_all(authority: Authority | None, changes: Sequence[Change]) -> bool:
    """:returns: Whether *authority* approves every one of *changes*."""
    return authority is not None and authority.carries and len(authority.covered) == len(changes)


__all__ = [
    "ANSWER_WINDOW_S",
    "CARRIED_OPS",
    "DECISION_REF_RE",
    "NOT_A_PERSON",
    "Authority",
    "answerer",
    "authority_for",
    "covers_all",
    "is_decision_ref",
    "named_slugs",
    "spent_by",
]
