"""Checkpoints: every decision, whoever made it, in one record a person can read and change.

A checkpoint (``K-n``, ``factory_checkpoints``) records one decision: who
decided (an agent, the server or a person), the question, the options
considered, the choice, a one-line why and a confidence, the node it is
scoped to, its effects (refs, with how each is taken back), the facts and
definition version at the time, and its status:

- ``decided``: the factory decided (:mod:`.decide_policy` let it);
- ``asked``: a person decides (it waits in Your move);
- ``answered``: a person decided;
- ``changed``: a person changed direction from it later (``changed_by``).

The factory's own decisions and a person's changes of direction are stored;
so is how an ask that waits for a person was routed (a hidden ``asked`` row,
:mod:`.decide_first`). Everything else reads as a checkpoint through the
adapter (:func:`load`), with no history copied:

- a decision (``D-n``) an agent or the server asked: open → ``asked``,
  answered by a person → ``answered`` (a decision the server closed as noise
  is no decision and is left out; one the factory decided is its stored
  ``K-n``);
- a projects proposal (``C-n``): pending → ``asked``; applied or undone →
  ``decided`` when the factory applied it on its own (an autonomy level, a
  minor update, the grace period), ``answered`` when a person applied it,
  asked for it or answered the decision it carried out; rejected →
  ``answered`` with "Reject".

A person's change of direction (:mod:`.change_direction`) is a stored
``answered`` checkpoint whose ``overrides`` names the one it changes; that one
reads ``changed`` from then on, whether it is stored or adapted.

:func:`list_view` is a node's Decisions lens (``GET …/n/{ref}/decisions``:
newest first, one line each), :func:`detail_view` one checkpoint's details,
:func:`decided_since` the node page's "Decided since you looked · n" and
:func:`decided_count` the Slack digest's line. Nothing here names a product.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory import node_refs
from omnigent.server.factory.actor import decision_ref, proposal_ref
from omnigent.server.factory.control import clip
from omnigent.server.factory.envelope import display_title
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryCheckpoint, FactoryDecision, FactoryProposal
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.nodes import Tree

FACTORY_BY: Final = "factory"
"""``answered_by`` of a decision the factory decided (never a person: see answer_authority)."""
FACTORY_NAME: Final = "the factory"
KIND_DECIDED: Final = "checkpoint.decided"
KIND_CHANGED: Final = "checkpoint.changed"
STATUSES: Final = ("decided", "asked", "answered", "changed")
SHOWN: Final = ("decided", "answered", "changed")
"""Stored statuses the cockpit shows (a stored ``asked`` row keeps an open ask's route)."""
WINDOW_S: Final = 30 * 86400
"""How far back the adapter reads decisions and proposals."""
SINCE_DEFAULT_S: Final = 7 * 86400
"""A viewer who never looked at a node counts what the factory decided this long back."""
READ_MAX: Final = 500
"""Rows of each kind the adapter reads."""
PAGE: Final = 30
"""Rows of the Decisions lens per page."""
LINE_MAX: Final = 90
_NOT_A_PERSON: Final = frozenset({"server", FACTORY_BY, ""})
_PROPOSAL_DECIDED: Final = frozenset({"applied", "undone", "trial", "kept", "reverted"})
_REF_PREFIXES: Final = ("K-", "D-", "C-")


def is_factory_answer(decision: FactoryDecision) -> bool:
    """Whether the factory answered *decision* (decide-first), not a person."""
    return (decision.answered_by or "") == FACTORY_BY


# ── One checkpoint, however it is stored ───────────────────────


@dataclass
class Item:
    """
    One checkpoint as the cockpit reads it.

    :param ref: ``K-n`` (stored), ``D-n`` or ``C-n`` (adapted).
    :param source: ``stored``, ``decision`` or ``proposal``.
    :param by_kind: ``agent``, ``server`` or ``person``.
    :param by_factory: The factory decided it (an agent or the server, never a person).
    :param nodes: Refs it is placed on (a proposal may touch several projects).
    :param at: When it was decided, or asked (epoch seconds).
    :param row: The stored checkpoint, when there is one.
    """

    ref: str
    source: str
    status: str
    question: str
    by_kind: str
    by_name: str
    at: int
    by: str | None = None
    by_factory: bool = False
    options: list[dict[str, Any]] = field(default_factory=list)
    choice_id: str | None = None
    choice_label: str | None = None
    direction: str | None = None
    why: str | None = None
    confidence: float | None = None
    decision_class: str | None = None
    policy: dict[str, Any] = field(default_factory=dict)
    effects: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    node_ref: str | None = None
    project_slug: str | None = None
    decision: str | None = None
    proposal: str | None = None
    overrides: str | None = None
    changed_by: str | None = None
    asker: str | None = None
    nodes: list[str] = field(default_factory=list)
    row: FactoryCheckpoint | None = None
    decision_row: FactoryDecision | None = None
    proposal_row: FactoryProposal | None = None

    def line(self) -> str:
        """One line: who decided what ("The factory decided: Next to Chat")."""
        who = self.by_name[:1].upper() + self.by_name[1:] if self.by_name else "Someone"
        what = self.choice_label or (clip(self.direction, 60) if self.direction else None)
        if self.status == "asked":
            return f"Waits for a person: {clip(self.question, LINE_MAX)}"
        if self.overrides and self.by_kind == "person":
            return f"{who} changed direction: {what or 'a new direction'}"
        verb = "decided" if self.by_factory else "chose"
        return f"{who} {verb}: {what or '(free text)'}"


def _iso(at: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


def options_of(raw: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """:returns: Options as a checkpoint keeps them ``{id, label, …}``."""
    out = []
    for option in raw:
        entry: dict[str, Any] = {
            "id": str(option.get("id") or ""),
            "label": str(option.get("label") or option.get("id") or ""),
        }
        if option.get("description"):
            entry["description"] = str(option["description"])
        if option.get("recommended"):
            entry["recommended"] = True
        out.append(entry)
    return out


def _label_of(options: Sequence[Mapping[str, Any]], option_id: str | None) -> str | None:
    if not option_id:
        return None
    found = next((o for o in options if str(o.get("id")) == option_id), None)
    return str(found.get("label") or option_id) if found is not None else option_id


def _stored(row: FactoryCheckpoint, proposals: Mapping[str, FactoryProposal]) -> Item:
    proposal = proposals.get(row.proposal_id or "")
    return Item(
        ref=row.ref,
        source="stored",
        status=row.status,
        question=row.question,
        by_kind=row.decided_by_kind,
        by_name=row.decided_by_name or (FACTORY_NAME if row.decided_by_kind == "server" else ""),
        by=row.decided_by,
        by_factory=row.decided_by_kind in ("agent", "server"),
        at=row.created_at,
        options=list(row.options or []),
        choice_id=row.choice_id,
        choice_label=row.choice_label,
        direction=row.direction,
        why=row.why,
        confidence=row.confidence,
        decision_class=row.decision_class,
        policy=dict(row.policy or {}),
        effects=list(row.effects or []),
        facts=dict(row.facts or {}),
        node_ref=row.node_ref,
        project_slug=row.project_slug,
        proposal=proposal_ref(proposal.number) if proposal is not None else None,
        overrides=row.overrides,
        changed_by=row.changed_by,
        asker=row.asked_by_session_id,
        row=row,
        proposal_row=proposal,
    )


def _decision_item(deps: FactoryDeps, row: FactoryDecision, names: Names) -> Item | None:
    from omnigent.server.factory.server_decisions import SERVER_BY

    if row.status not in ("open", "answered") or row.source == "proposal":
        return None
    if row.status == "answered" and (row.answered_by or "") in (SERVER_BY, FACTORY_BY):
        return None
    asker = _title(deps, row.asked_by_session_id, names)
    options = options_of(row.options)
    answered = row.status == "answered"
    return Item(
        ref=decision_ref(row.number),
        source="decision",
        status="answered" if answered else "asked",
        question=row.question,
        by_kind="person" if answered else ("agent" if row.source == "agent" else "server"),
        by_name=(row.answered_by or "a person") if answered else (asker or ""),
        by=row.answered_by if answered else row.asked_by_session_id,
        at=(row.answered_at or row.created_at) if answered else row.created_at,
        options=options,
        choice_id=row.answer_option_id,
        choice_label=_label_of(options, row.answer_option_id),
        direction=row.answer_text if not row.answer_option_id else None,
        why=clip(row.answer_text, 300) if row.answer_text else None,
        node_ref=row.subject,
        project_slug=row.project_slug,
        decision=decision_ref(row.number),
        asker=row.asked_by_session_id,
        decision_row=row,
    )


def _proposal_item(deps: FactoryDeps, row: FactoryProposal, names: Names) -> Item | None:
    status = row.status
    if status not in (*_PROPOSAL_DECIDED, "pending", "rejected"):
        return None
    ref = proposal_ref(row.number)
    result = row.result or {}
    question = f"{ref}: {row.title}"
    options = [
        {"id": "apply", "label": "Apply", "recommended": True},
        {"id": "reject", "label": "Reject"},
    ]
    base = {
        "ref": ref,
        "source": "proposal",
        "question": question,
        "options": options,
        "proposal": ref,
        "asker": row.author if row.author_kind == "agent" else None,
        "why": clip(row.rationale, 300) if row.rationale else None,
        "effects": _proposal_effects(row),
        "proposal_row": row,
    }
    if status == "pending":
        return Item(
            status="asked",
            by_kind="agent" if row.author_kind == "agent" else "person",
            by_name=_title(deps, row.author, names) or "",
            by=row.author,
            at=row.created_at,
            **base,
        )
    if status == "rejected":
        return Item(
            status="answered",
            by_kind="person",
            by_name=_person(deps, row.factory_id, row.decided_by, names),
            by=row.decided_by,
            at=row.decided_at or row.updated_at,
            choice_id="reject",
            choice_label="Reject",
            **base,
        )
    at = row.applied_at or row.decided_at or row.updated_at
    label = "Edit & apply" if result.get("edited") else "Apply"
    authority = result.get("authority")
    person: str | None = None
    if isinstance(authority, Mapping) and authority.get("answered_by"):
        person = str(authority["answered_by"])
    elif row.on_behalf_of:
        person = row.on_behalf_of
    elif row.author_kind == "human":
        person = row.author
    elif row.decided_by and row.decided_by not in _NOT_A_PERSON and row.decided_by != row.author:
        person = row.decided_by
    if person is not None:
        return Item(
            status="answered",
            by_kind="person",
            by_name=_person(deps, row.factory_id, person, names),
            by=person,
            at=at,
            choice_id="apply",
            choice_label=label,
            **base,
        )
    server = (row.decided_by or "server") in _NOT_A_PERSON
    why = (
        f"{result['level']} autonomy"
        if result.get("level")
        else "no objection in the grace period"
        if result.get("grace")
        else "a minor update the agent may make"
    )
    level = str(result.get("level") or "")
    return Item(
        status="decided",
        by_kind="server" if server else "agent",
        by_name=FACTORY_NAME if server else (_title(deps, row.author, names) or FACTORY_NAME),
        by=row.decided_by,
        by_factory=True,
        at=at,
        choice_id="apply",
        choice_label=label,
        policy={"who": "factory", "level": level, "reasons": [why]} if level else {},
        **{**base, "why": base["why"] or why},
    )


def _proposal_effects(row: FactoryProposal) -> list[dict[str, Any]]:
    result = row.result or {}
    out: list[dict[str, Any]] = []
    for slug in result.get("created") or []:
        out.append({"ref": str(slug), "kind": "created"})
    for slug in result.get("updated") or []:
        out.append({"ref": str(slug), "kind": "updated"})
    for slug in result.get("cancelled") or []:
        out.append({"ref": str(slug), "kind": "cancelled"})
    for slug in (result.get("split") or []) + (result.get("merged") or []):
        out.append({"ref": str(slug), "kind": "restructured"})
    return out


Names = dict[tuple[str, str], str | None]
"""Names read during one :func:`load` (sessions and people repeat across rows)."""


def _title(deps: FactoryDeps, session_id: str | None, names: Names) -> str | None:
    if not session_id:
        return None
    key = ("session", session_id)
    if key in names:
        return names[key]
    try:
        conv = deps.conversation_store.get_conversation(session_id)
    except Exception:  # noqa: BLE001 - a name is a courtesy
        conv = None
    names[key] = display_title(conv.title, conv.id) if conv is not None else None
    return names[key]


def _person(deps: FactoryDeps, factory_id: str, user_id: str | None, names: Names) -> str:
    from omnigent.server.factory import people

    if not user_id:
        return "a person"
    key = ("person", user_id)
    if key not in names:
        try:
            names[key] = people.human_label(deps, factory_id, user_id)
        except Exception:  # noqa: BLE001 - a name is a courtesy
            names[key] = user_id
    return names[key] or user_id


# ── Loading ────────────────────────────────────────────────────


@dataclass
class Book:
    """Every checkpoint of one factory in the window, newest first."""

    items: list[Item]

    def get(self, ref: str) -> Item | None:
        """:returns: The checkpoint *ref* names (``K-3``, ``D-49``, ``C-16``)."""
        wanted = ref.strip().upper()
        return next((i for i in self.items if i.ref.upper() == wanted), None)


def load(deps: FactoryDeps, factory_id: str, *, now: float | None = None) -> Book:
    """
    Every checkpoint in the last :data:`WINDOW_S`, stored and adapted, newest
    first (synchronous: store reads).
    """
    store = deps.factory_store
    now = time.time() if now is None else now
    since = int(now) - WINDOW_S
    # A stored ``asked`` row only keeps how an open ask was routed: the ask reads as its D-n.
    stored = store.list_checkpoints(factory_id, since=since, status=list(SHOWN), limit=READ_MAX)
    proposals = {
        norm_id(p.id): p
        for p in store.list_proposals(
            factory_id, kind="projects", limit=READ_MAX, newest_first=True
        )
    }
    items = [_stored(row, proposals) for row in stored]
    names: Names = {}
    covered_decisions = {norm_id(r.decision_id) for r in stored if r.decision_id}
    covered_proposals = {norm_id(r.proposal_id) for r in stored if r.proposal_id}
    for row in store.list_decisions(
        factory_id=factory_id, status=["open", "answered"], limit=READ_MAX, newest_first=True
    ):
        if norm_id(row.id) in covered_decisions or (row.answered_at or row.created_at) < since:
            continue
        if (item := _decision_item(deps, row, names)) is not None:
            items.append(item)
    for key, row in proposals.items():
        if key in covered_proposals or (row.applied_at or row.updated_at) < since:
            continue
        if (item := _proposal_item(deps, row, names)) is not None:
            items.append(item)
    changed = {r.overrides: r.ref for r in stored if r.overrides}
    for item in items:
        if item.ref in changed and item.status != "changed":
            item.status = "changed"
            item.changed_by = item.changed_by or changed[item.ref]
    items.sort(key=lambda i: (i.at, i.source == "stored"), reverse=True)
    return Book(items)


# ── Placing checkpoints on the tree ────────────────────────────


def place(tree: Tree, item: Item) -> list[str]:
    """
    The nodes *item* is about, as this viewer's tree shows them (folded); none
    when it is about work they may not read (or that is gone).
    """
    if item.project_slug and item.project_slug in tree.hidden:
        return []
    if item.proposal and item.proposal_row is not None:
        found = [r for r in tree.proposal_nodes(item.proposal) if r != node_refs.PRODUCT]
        if found:
            return found
    if item.node_ref:
        node = tree.lookup(_subject_ref(item.node_ref))
        if node is not None:
            return [tree.fold(node).ref]
    if item.project_slug:
        ref = tree.project_ref(item.project_slug)
        return [tree.fold(tree.nodes[ref]).ref] if ref and ref in tree.nodes else []
    if (item.node_ref or "").startswith(("project:", "task:", "pr:")):
        return []
    return [node_refs.PRODUCT]


def _subject_ref(subject: str) -> str:
    head, sep, rest = subject.partition(":")
    if sep and head.lower() in ("project", "initiative", "opportunity") and rest:
        return rest
    return subject


def _under(tree: Tree, ref: str, placed: Iterable[str]) -> bool:
    for found in placed:
        if found == ref:
            return True
        if any(a.ref == ref for a in tree.ancestors(found)):
            return True
    return False


def in_subtree(tree: Tree, book: Book, ref: str) -> list[Item]:
    """:returns: The checkpoints placed at *ref* or under it, newest first."""
    out: list[Item] = []
    for item in book.items:
        placed = place(tree, item)
        item.nodes = placed
        if placed and _under(tree, ref, placed):
            out.append(item)
    return out


def decided_since(tree: Tree, book: Book, ref: str, since: int) -> int:
    """:returns: How many decisions the factory made at or under *ref* after *since*."""
    return sum(
        1
        for item in in_subtree(tree, book, ref)
        if item.by_factory and item.status in ("decided", "changed") and item.at > since
    )


def item_slugs(item: Item) -> set[str]:
    """:returns: The projects *item* touches: its own, its node's and what its effects name."""
    out = {item.project_slug} if item.project_slug else set()
    if (item.node_ref or "").startswith("project:"):
        out.add((item.node_ref or "").removeprefix("project:"))
    for effect in item.effects:
        if effect.get("kind") in ("created", "updated", "cancelled", "restructured"):
            out.add(str(effect.get("ref") or ""))
    row = item.proposal_row
    if row is not None:
        for op in row.ops or []:
            if isinstance(op, Mapping) and op.get("slug"):
                out.add(str(op["slug"]))
    out.discard("")
    return out


def decided_items(
    deps: FactoryDeps, factory_id: str, since: int, hidden: Iterable[str]
) -> list[Item]:
    """:returns: Decisions the factory made after *since*, none touching a project in *hidden*."""
    gone = set(hidden)
    book = load(deps, factory_id)
    return [
        i
        for i in book.items
        if i.by_factory
        and i.status in ("decided", "changed")
        and i.at > since
        and not (item_slugs(i) & gone)
    ]


def decided_count(deps: FactoryDeps, factory_id: str, since: int, hidden: Iterable[str]) -> int:
    """:returns: How many of :func:`decided_items` there are."""
    return len(decided_items(deps, factory_id, since, hidden))


# ── Views ──────────────────────────────────────────────────────


def row_view(item: Item) -> dict[str, Any]:
    """One row of the Decisions lens: ``{ref, line, about, at, status, by, node}``."""
    out: dict[str, Any] = {
        "ref": item.ref,
        "line": item.line(),
        "about": clip(item.question, LINE_MAX),
        "at": _iso(item.at),
        "status": item.status,
        "by": {"kind": item.by_kind, "name": item.by_name},
        "by_factory": item.by_factory,
    }
    if item.nodes:
        out["node"] = item.nodes[0]
    if item.changed_by:
        out["changed_by"] = item.changed_by
    if item.overrides:
        out["overrides"] = item.overrides
    return out


def detail_view(item: Item, *, can_change: bool) -> dict[str, Any]:
    """
    One checkpoint's details (the peek): the question, the options, the choice,
    why, the effects, the policy and what it overrides or was changed by.
    """
    out = row_view(item)
    out["question"] = item.question
    out["source"] = item.source
    if item.options:
        out["options"] = item.options
    if item.choice_id or item.choice_label:
        out["choice"] = {
            k: v for k, v in (("id", item.choice_id), ("label", item.choice_label)) if v
        }
    for key, value in (
        ("direction", item.direction),
        ("why", item.why),
        ("confidence", item.confidence),
        ("class", item.decision_class),
        ("decision", item.decision),
        ("proposal", item.proposal),
        ("project", item.project_slug),
    ):
        if value is not None and value != "":
            out[key] = value
    if item.effects:
        out["effects"] = item.effects
    if item.policy:
        out["policy"] = item.policy
    if item.facts:
        out["facts"] = item.facts
    if item.nodes:
        out["nodes"] = item.nodes
    out["can_change"] = can_change and item.status in ("decided", "answered")
    return out


def list_view(
    tree: Tree,
    book: Book,
    ref: str,
    *,
    cursor: str | None = None,
    include_asked: bool = False,
    limit: int = PAGE,
) -> dict[str, Any]:
    """
    ``GET …/n/{ref}/decisions``: the checkpoints at or under *ref*, newest
    first, one line each; asks a person must answer stay in Your move unless
    *include_asked*.

    :raises ValueError: On a cursor no page handed out.
    """
    items = [i for i in in_subtree(tree, book, ref) if include_asked or i.status != "asked"]
    start = 0
    if cursor:
        if not cursor.isdigit() or int(cursor) > len(items):
            raise ValueError(f"bad cursor {cursor!r}")
        start = int(cursor)
    page = items[start : start + limit]
    out: dict[str, Any] = {
        "ref": ref,
        "items": [row_view(i) for i in page],
        "total": len(items),
    }
    if start + limit < len(items):
        out["next"] = str(start + limit)
    return out


def parse_ref(value: str) -> str | None:
    """:returns: ``K-3``, ``D-49`` or ``C-16`` from any case, else ``None``."""
    text = value.strip().upper()
    if not text.startswith(_REF_PREFIXES):
        return None
    number = text[2:]
    return text if number.isdigit() and not number.startswith("0") else None


__all__ = [
    "FACTORY_BY",
    "FACTORY_NAME",
    "KIND_CHANGED",
    "KIND_DECIDED",
    "PAGE",
    "SHOWN",
    "SINCE_DEFAULT_S",
    "STATUSES",
    "Book",
    "Item",
    "decided_count",
    "decided_items",
    "decided_since",
    "detail_view",
    "in_subtree",
    "is_factory_answer",
    "item_slugs",
    "list_view",
    "load",
    "options_of",
    "parse_ref",
    "place",
    "row_view",
]
