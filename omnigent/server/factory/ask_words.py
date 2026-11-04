"""
Asks in plain words (calm rule 9): one sentence saying who wants what and
what a yes does, and buttons named by their outcome.

A row of Your move never reads "Apply C-15", a bare id or the agent's jargon
("Widen scope: …"). It reads "Atlas wants Connectors core to also edit 3 more
files" with [Yes, allow it] [No]; the original wording and the ids stay in
the ask's details.

Where the words come from, first that applies:

1. The factory's words (``factory_node_names`` under ``plain:<D-n>``, and the
   labels of a question's answers under ``plain:<D-n>#labels``), made by the
   cheap title model the short names use (:mod:`.node_names`, a :class:`Want`
   of kind ``ask``) from :func:`source_text`, again only when that text changes.
2. Until then, and whenever the model cannot answer, words built from the rows
   (:func:`fallback_sentence`): a projects proposal's ops and the project's
   short name ("Atlas wants Connectors core to also edit 3 more files"), a
   factory change's title, a question's own words; a merge always reads
   "Nova wants Build tier drafts 3 merged into main (2 changes)".

Labels: a projects proposal's yes names what it does ("Yes, allow it", "Yes,
start it", "Yes, stop it"), its no is "No" and Edit & apply waits in the
details ("Change it first"); a question's answers are the model's labels,
else their own words without codes or a jargon head. A row shows the
recommended answer and at most :data:`ROW_ANSWERS` in all; the rest are
``more`` (in the details).

Nothing names a product: every word comes from the factory's rows.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory import node_names
from omnigent.server.factory.node_status import decision_proposal

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryDecision, FactoryProject, FactoryProposal
    from omnigent.server.factory.node_status import Ask
    from omnigent.server.factory.nodes import Tree

PREFIX: Final = "plain:"
"""Where an ask's words are stored: ``plain:D-45``."""
ROW_ANSWERS: Final = 3
"""Answers a row shows at most (the recommended one first); the rest wait in its details."""
SOURCE_MAX: Final = 1800
"""Characters of an ask's text the model reads."""
_SENTENCE_WORDS: Final = 18
_NEW: Final = "a new project"
_NEW_ANY: Final = ("a new project", "2 new", "3 new", "4 new", "5 new", "6 new")
"""What a create proposes is a thing a person wants ("Atlas wants a new project: …")."""
_PHRASE_WORDS: Final = 10
"""Two things an update does are said together only within this many words."""
_REF_RE: Final = re.compile(r"(?<![\w#/-])(?:(?:PR|INC|[CDFGIORT])-\d+|#\d+)(?![\w-])")
_PRIMARY_IDS: Final = ("apply",)
_NO_IDS: Final = frozenset({"reject", "no", "decline"})
_LATER_IDS: Final = frozenset({"edit"})
_FIELD_WORDS: Final[dict[str, str]] = {
    "goal": "change its goal",
    "outcome": "change the outcome it aims for",
    "constraints": "take on a new constraint",
    "budget_ics": "get a different budget",
    "budgets": "get a different budget",
    "preview": "change its preview",
    "preview_slots": "change its preview",
    "demo": "change what its demo shows",
    "demo_focus": "change what its demo shows",
    "human_owner": "get a new owner",
    "depends_mode": "change when it may start",
}
_FIELD_ORDER: Final = (
    "scope_paths",
    "dod",
    "goal",
    "outcome",
    "depends_on",
    "constraints",
    "priority",
    "budget_ics",
    "budgets",
    "preview",
    "preview_slots",
    "demo_focus",
    "demo",
    "human_owner",
    "depends_mode",
)


@dataclass(frozen=True)
class Words:
    """
    An ask in plain words.

    :param sentence: Who wants what and what a yes does, one sentence.
    :param answers: ``[{option, label, primary?, url?, more?}]``, the primary first.
    :param factory: Whether the factory's model made the sentence (else the rows did).
    """

    sentence: str
    answers: list[dict[str, Any]]
    factory: bool = False


def ref_of(ask_id: str) -> str:
    """:returns: Where *ask_id*'s words are stored (``plain:D-45``)."""
    return f"{PREFIX}{ask_id}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _low(text: str) -> str:
    """A continuation in lower case ("Each turn …" -> "each turn …"); an acronym stays."""
    return text[:1].lower() + text[1:] if re.match(r"^[A-Z][a-z]", text) else text


def _plain(text: str) -> str:
    """*text* without record codes (an aside naming one goes whole) and doubled spaces."""
    out = re.sub(
        r"\s*\([^()]*\)",
        lambda m: " " if _REF_RE.search(m.group(0)) else m.group(0),
        str(text or ""),
    )
    out = _REF_RE.sub(" ", out)
    return " ".join(out.split()).strip(" ,;:·-")


def _cut(text: str, limit: int = _SENTENCE_WORDS) -> str:
    """At most *limit* words of *text*, never ending on a dangling word."""
    words = text.split()
    if len(words) <= limit:
        return text
    kept = words[:limit]
    while len(kept) > 1 and kept[-1].lower().strip(",.;:") in node_names._STOP_TAIL:
        kept.pop()
    return " ".join(kept).rstrip(",.;:") + "…"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


# ── What a projects proposal does ──────────────────────────────


def _name(tree: Tree, slug: str) -> str:
    """A project by its short name ("Connectors core"), its slug read as words else."""
    found = tree.nodes.get(tree.project_ref(slug) or "")
    return found.title if found is not None else node_names.slug_words(slug)


def _names(tree: Tree, slugs: Sequence[str]) -> str:
    named = [_name(tree, s) for s in slugs]
    return named[0] if len(named) == 1 else f"{', '.join(named[:-1])} and {named[-1]}"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _scope_phrase(new: list[str], old: list[str] | None) -> str:
    if old is None:
        return "edit a different set of files"
    added = [p for p in new if p not in old]
    removed = [p for p in old if p not in new]
    if added and not removed:
        return f"also edit {_plural(len(added), 'more file')}"
    if removed and not added:
        return f"stop editing {_plural(len(removed), 'file')}"
    return "edit a different set of files"


def _dod_phrase(new: list[str], old: list[str] | None) -> str:
    added = [d for d in new if old is None or d not in old]
    if old is not None and not added:
        return "change what it must deliver"
    if len(added) == 1 and node_names.word_count(added[0]) <= 10:
        return f"also ensure {_low(_plain(added[0]).rstrip('.'))}"
    return (
        f"deliver {_plural(len(added), 'more thing')}" if added else "change what it must deliver"
    )


def _field_phrase(
    tree: Tree, key: str, patch: Mapping[str, Any], row: FactoryProject | None
) -> str | None:
    charter = dict(row.charter or {}) if row is not None else {}
    if key == "scope_paths":
        old = _list(charter.get("scope_paths")) if row is not None else None
        return _scope_phrase(_list(patch.get(key)), old)
    if key == "dod":
        old = _list(charter.get("dod")) if row is not None else None
        return _dod_phrase(_list(patch.get(key)), old)
    if key == "depends_on":
        new, old = _list(patch.get(key)), _list(charter.get("depends_on"))
        added = [d for d in new if d not in old]
        dropped = [d for d in old if d not in new]
        if added:
            return f"wait for {_names(tree, added)}"
        if dropped:
            return f"stop waiting for {_names(tree, dropped)}"
        return None
    if key == "priority":
        new_p = patch.get(key)
        if not isinstance(new_p, int) or row is None or new_p == row.priority:
            return None
        return "move up the queue" if new_p < row.priority else "move down the queue"
    if key == "depends_why":
        return None
    return _FIELD_WORDS.get(key, "change its plan")


def _update(
    tree: Tree, op: Mapping[str, Any], rows: Mapping[str, FactoryProject]
) -> tuple[str | None, str]:
    slug = str(op.get("slug") or "")
    patch = _mapping(op.get("patch"))
    row = rows.get(slug)
    keys = sorted(patch, key=lambda k: _FIELD_ORDER.index(k) if k in _FIELD_ORDER else 99)
    phrases: list[str] = []
    for key in keys:
        phrase = _field_phrase(tree, key, patch, row)
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    if not phrases:
        phrases = ["change its plan"]
    # The first thing it changes, and a second when both fit; the rest is in its details.
    said = phrases[0]
    if len(phrases) > 1 and node_names.word_count(f"{said} {phrases[1]}") <= _PHRASE_WORDS:
        said = f"{said} and {phrases[1]}"
    return _name(tree, slug), said


def _op(
    tree: Tree, op: Mapping[str, Any], rows: Mapping[str, FactoryProject]
) -> tuple[str | None, str]:
    """``(subject, what it does)``: "Connectors core", "also edit 3 more files"."""
    kind = op.get("op")
    slug = str(op.get("slug") or "")
    if kind == "update":
        return _update(tree, op, rows)
    if kind == "create":
        project = _mapping(op.get("project"))
        title = str(project.get("title") or "") or node_names.slug_words(
            str(project.get("slug") or "")
        )
        return None, f"{_NEW}: {_plain(title)}" if title else _NEW
    if kind == "cancel":
        return None, f"stop {_name(tree, slug)}"
    if kind == "split":
        parts = len(op.get("into") or [])
        return None, f"split {_name(tree, slug)} into {_plural(parts, 'project')}"
    if kind == "merge":
        into = str(op.get("into") or "")
        return None, f"fold {_names(tree, _list(op.get('from')))} into {_name(tree, into)}"
    if kind == "move":
        to = tree.nodes.get(str(op.get("to") or ""))
        where = to.title if to is not None else "another feature"
        return _name(tree, slug), f"move under {where}"
    if kind == "reorder":
        after = op.get("after")
        if isinstance(after, str) and after:
            return _name(tree, slug), f"wait for {_name(tree, after)}"
        return _name(tree, slug), "go first"
    return (_name(tree, slug) if slug else None), "change its plan"


_NO_WHO: Final = frozenset({"", "local", "a person", "the server"})
"""Askers a sentence leaves out ("Let Assistant core also …"): nobody named."""


def _wants(asker: str, subject: str | None, what: str) -> str:
    """
    "Atlas wants Connectors core to also edit …", "Atlas wants a new project:
    Fix live search"; with nobody named, "Let Connectors core also edit …",
    "Start a new project: Fix live search".
    """
    thing = subject is None and what.startswith(_NEW_ANY)
    if (asker or "").strip().lower() in _NO_WHO:
        if thing:
            return f"Start {what}"
        return f"Let {subject} {what}" if subject else _cap(what)
    who = _cap(asker)
    if thing:
        return f"{who} wants {what}"
    return f"{who} wants {subject} to {what}" if subject else f"{who} wants to {what}"


def _projects_sentence(tree: Tree, asker: str, proposal: FactoryProposal) -> str:
    rows = tree.wfacts.projects
    said = [_op(tree, op, rows) for op in proposal.ops or [] if isinstance(op, Mapping)]
    if not said:
        return _wants(asker, None, f"change the plan: {_plain(proposal.title)}")
    creates = [what for subject, what in said if what.startswith(_NEW)]
    if len(creates) > 1 and len(creates) == len(said):
        return _wants(asker, None, f"{len(creates)} new projects")
    subject, what = said[0]
    same = [w for s, w in said[1:] if s == subject and s is not None]
    others = len(said) - 1 - len(same)
    if same:
        what = " and ".join([what, *same][:2])
    sentence = _wants(asker, subject, what)
    if others:
        sentence += f", and {_plural(others, 'more change')}"
    return sentence


def _question(text: str) -> str:
    """The sentence of *text* that asks, without codes ("Start a follow-up project to fix it")."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    asking = [s for s in sentences if s.endswith("?")]
    pick = (asking or sentences or [""])[-1 if asking else 0]
    return _plain(pick.rstrip("?")) or _plain(text)


def fallback_sentence(
    tree: Tree, ask: Ask, decision: FactoryDecision | None, proposal: FactoryProposal | None
) -> str:
    """The sentence the rows say until the factory words the ask (see the module docstring)."""
    if decision is None:
        return _merge_sentence(tree, ask)
    asker = ask.asker
    if proposal is not None and decision.source == "proposal":
        if proposal.kind == "projects":
            return _cut(_projects_sentence(tree, asker, proposal))
        what = {
            "rules": "add a factory rule",
            "tuning": "change how the factory works",
        }.get(proposal.kind, "change the factory")
        title = _plain(proposal.title)
        return _cut(_wants(asker, None, f"{what}: {_low(title)}" if title else what))
    asked = _question(decision.question or "")
    if not asked:
        return _cut(f"{_cap(asker)} asks for an answer")
    return _cut(f"{_cap(asker)} asks: {_low(asked)}?")


def _merge_sentence(tree: Tree, ask: Ask) -> str:
    project = tree.nodes.get(ask.at or "")
    base = (ask.title or "").rsplit(" into ", 1)[-1] if ask.title else ""
    count = next((len(prs) for found, prs in tree.paths.merge_asks() if found.id == ask.id), 0)
    what = project.title if project is not None else "the work"
    tail = f" ({_plural(count, 'change')})" if count > 1 else ""
    into = f" into {base}" if base else ""
    return f"{_cap(ask.asker)} wants {what} merged{into}{tail}"


# ── Labels ─────────────────────────────────────────────────────


def _plain_label(label: str) -> str:
    """An answer's own words without codes or a jargon head ("Widen scope: X" -> "X")."""
    text = _plain(label)
    head, sep, tail = text.partition(":")
    if sep and 1 <= node_names.word_count(head) <= 3 and node_names.word_count(tail) >= 1:
        text = tail.strip()
    if node_names.word_count(text) > 5:
        text = node_names.fallback(text, limit=5)
    return _cap(text) or _cap(label)


def _yes(proposal: FactoryProposal) -> str:
    if proposal.kind == "rules":
        return "Yes, add the rule"
    if proposal.kind != "projects":
        return "Yes, change it"
    kinds = {op.get("op") for op in proposal.ops or [] if isinstance(op, Mapping)}
    if kinds == {"create"}:
        return "Yes, start it"
    if kinds == {"cancel"}:
        return "Yes, stop it"
    return "Yes, allow it"


def _options(decision: FactoryDecision) -> list[Mapping[str, Any]]:
    return [o for o in decision.options or [] if isinstance(o, Mapping) and o.get("id")]


def _proposal_answers(
    decision: FactoryDecision, proposal: FactoryProposal
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for option in _options(decision):
        oid = str(option["id"])
        if oid in _PRIMARY_IDS:
            out.append({"option": oid, "label": _yes(proposal), "primary": True})
        elif oid in _NO_IDS:
            out.append({"option": oid, "label": "No"})
        elif oid in _LATER_IDS:
            out.append({"option": oid, "label": "Change it first", "more": True})
        else:
            out.append({"option": oid, "label": _plain_label(str(option.get("label") or oid))})
    return out


def _question_answers(
    decision: FactoryDecision, labels: Sequence[str] | None
) -> list[dict[str, Any]]:
    options = _options(decision)
    named = list(labels) if labels is not None and len(labels) == len(options) else None
    out: list[dict[str, Any]] = []
    for index, option in enumerate(options):
        label = named[index] if named else _plain_label(str(option.get("label") or option["id"]))
        out.append({"option": str(option["id"]), "label": label})
    pick = next((i for i, o in enumerate(options) if o.get("recommended")), 0)
    if out:
        out.insert(0, {**out.pop(pick), "primary": True})
    return out


def _row(answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The primary first, at most :data:`ROW_ANSWERS` on the row; the rest are ``more``."""
    shown = 0
    out: list[dict[str, Any]] = []
    for answer in sorted(answers, key=lambda a: (not a.get("primary"), bool(a.get("more")))):
        if not answer.get("more") and shown < ROW_ANSWERS:
            shown += 1
            out.append(answer)
        else:
            out.append({**answer, "more": True})
    return out


def answers(
    ask: Ask,
    decision: FactoryDecision | None,
    proposal: FactoryProposal | None,
    labels: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """The ask's answers, outcome-named, the primary first (see the module docstring)."""
    if decision is None:
        found = [{"option": i, "label": label} for i, label in ask.actions]
        if not found:
            return []
        first = {**found[0], "primary": True}
        if ask.external_url:
            first["url"] = ask.external_url
        return _row([first, *found[1:]])
    if proposal is not None and decision.source == "proposal":
        return _row(_proposal_answers(decision, proposal))
    return _row(_question_answers(decision, labels))


# ── The model's text, and the words a row shows ────────────────


def _op_lines(proposal: FactoryProposal, tree: Tree) -> list[str]:
    lines: list[str] = []
    for op in proposal.ops or []:
        if not isinstance(op, Mapping):
            continue
        slug = str(op.get("slug") or op.get("into") or "")
        name = f"{_name(tree, slug)} ({slug})" if slug else ""
        detail = op.get("patch") if op.get("op") == "update" else op.get("project")
        flat = " ".join(str(detail or "").split())[:400]
        lines.append(f"- {op.get('op')} {name}: {flat}".rstrip(": "))
    return lines


def source_text(tree: Tree, ask: Ask, decision: FactoryDecision) -> tuple[str, int]:
    """
    What the model words an ask from, and how many answers it names (0 for a
    proposal, whose answers the rows name).
    """
    proposal = decision_proposal(decision, tree.proposals_by_ref)
    lines = [f"Who asks: {ask.asker}", f"What they ask: {decision.question}"]
    context = (decision.context or "").strip()
    labels = 0
    if proposal is not None and decision.source == "proposal":
        if proposal.rationale:
            lines.append(f"Why: {' '.join(proposal.rationale.split())[:500]}")
        lines.append(f"Kind of change: {proposal.kind}")
        if proposal.kind == "projects":
            lines.append("What a yes changes:")
            lines.extend(_op_lines(proposal, tree))
        else:
            lines.append(f"What a yes changes: {proposal.title}")
    else:
        if context:
            lines.append(f"Context: {' '.join(context.split())[:500]}")
        options = _options(decision)
        if options:
            labels = len(options)
            lines.append("Answers:")
            for index, option in enumerate(options, start=1):
                about = option.get("description")
                tail = f" ({' '.join(str(about).split())[:160]})" if about else ""
                lines.append(f"{index}. {option.get('label') or option['id']}{tail}")
    return "\n".join(lines)[:SOURCE_MAX], labels


def want(tree: Tree, ask: Ask, decision: FactoryDecision) -> node_names.Want | None:
    """The model's job for *decision*'s words, unless its words are stored for this text."""
    text, labels = source_text(tree, ask, decision)
    digest = node_names.digest(text)
    found = tree.facts.names.get(ref_of(ask.id))
    if found is not None and (found.named_by or found.source_hash == digest):
        return None
    return node_names.Want(ref_of(ask.id), text, digest, "ask", kind="ask", labels=labels)


def _decision(tree: Tree, ref: str) -> FactoryDecision | None:
    by_ref: dict[str, FactoryDecision] = tree.memo(
        "ask_words", "decisions", lambda: {d.ref: d for d in tree.facts.decisions}
    )
    return by_ref.get(ref)


def words(tree: Tree, ask: Ask) -> Words:
    """*ask* in plain words: the factory's when stored for its current text, else the rows'."""

    def make() -> Words:
        decision = _decision(tree, ask.id)
        proposal = (
            decision_proposal(decision, tree.proposals_by_ref) if decision is not None else None
        )
        sentence: str | None = None
        labels: list[str] | None = None
        if decision is not None:
            found = tree.facts.names.get(ref_of(ask.id))
            text, count = source_text(tree, ask, decision)
            if found is not None and found.source_hash == node_names.digest(text):
                sentence = node_names.clean_sentence(found.name)
                stored = tree.facts.names.get(ref_of(ask.id) + node_names.LABELS_SUFFIX)
                if stored is not None and stored.source_hash == found.source_hash:
                    labels = node_names.clean_labels(stored.name, count)
        made = sentence is not None
        return Words(
            sentence=sentence or fallback_sentence(tree, ask, decision, proposal),
            answers=answers(ask, decision, proposal, labels),
            factory=made,
        )

    found: Words = tree.memo("ask_words", ask.id, make)
    return found


__all__ = [
    "PREFIX",
    "ROW_ANSWERS",
    "Words",
    "answers",
    "fallback_sentence",
    "ref_of",
    "source_text",
    "want",
    "words",
]
