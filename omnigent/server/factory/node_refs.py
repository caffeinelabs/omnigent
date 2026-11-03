"""
Node refs: one address for every node of the cockpit's level tree.

A ref names one node of the product tree (product › goal › need › feature ›
project › task › change › run) or the factory tree (fx › area › item). It
never changes when the node moves, is safe as a URL path segment
(``/factory/:id/n/:ref``) and is what chips, Find and agents pass around.

===============  ==========================================  =====================================
Level            Ref                                         Key (subjects, targets, threads)
===============  ==========================================  =====================================
product          ``product``                                 ``factory``
goal             ``G-1``                                     ``goal:G-1``
need             ``O-3`` · ``ask:<message id>`` ·            ``opportunity:O-3`` · the ref
                 ``need:<implicit key>``
feature          ``I-1`` · ``R-1`` · ``feature:<implicit>``  ``initiative:I-1`` · ``study:R-1`` ·
                                                             the ref
project          ``<slug>`` (``project:<slug>`` when the     ``project:<slug>``
                 slug is a reserved word)
task             ``T-16`` · ``adhoc:<pr id>``                ``task:T-16`` · the ref
change           ``PR-9876`` · ``commit:<sha>``              ``pr:<id>``, ``pr:<repo>#<n>`` or
                                                             ``pr:<n>`` · the ref
run              ``run:<session id>``                        the ref
fx (root)        ``fx``                                      the ref
area             ``fx:<area>`` (:data:`FACTORY_AREAS`)       the ref
item             ``C-17`` · ``D-46`` · ``member:<id>`` ·     ``proposal:C-17`` ·
                 ``env:<name>``                              ``decision:D-46`` · the ref
===============  ==========================================  =====================================

Stored ``decisions.subject``, ``issues.target`` and ``events.subject`` keep the
key spelling every reader parses today (the cockpit and the server read
``pr:``, ``project:``, ``member:`` and ``proposal:``); :func:`canonical_key`
rewrites other spellings of a node to its key on write and leaves every key
the server writes as it is. :func:`normalize` reads any spelling, old or new,
back as the ref, so old rows need no migration. Unknown text is never a guess:
it normalizes and resolves to ``None``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

from omnigent.db.db_models import InvalidUuidError
from omnigent.entities.factory import (
    FactoryDecision,
    FactoryLoop,
    FactoryPr,
    FactoryProject,
    FactoryProposal,
    FactoryTask,
)
from omnigent.factory import (
    PROJECT_KIND_WORK,
    PROJECT_SLUG_RE,
    TASK_KEY_RE,
    THREAD_DISCOVERY,
    THREAD_FACTORY,
    THREAD_IMPROVEMENTS,
    THREAD_PR_PREFIX,
    THREAD_PROJECT_PREFIX,
)
from omnigent.server.factory.charter import project_kind
from omnigent.server.factory.definition_blocks import FACTORY_TREE, PRODUCT_TREE
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.server.factory.context import FactoryDeps

RefKind = Literal[
    "product",
    "goal",
    "opportunity",
    "ask",
    "need",
    "initiative",
    "study",
    "feature",
    "project",
    "task",
    "adhoc",
    "pr",
    "commit",
    "run",
    "fx",
    "area",
    "proposal",
    "decision",
    "member",
    "env",
]
"""What a ref names. ``need`` and ``feature`` are implicit parents keyed by their origin."""

PRODUCT: Final = "product"
FX: Final = "fx"
RESERVED: Final = frozenset({PRODUCT, FX, THREAD_FACTORY})
"""Bare words that never name a project (such a project's ref is ``project:<slug>``)."""
FACTORY_AREAS: Final[dict[str, str]] = {
    "rules": "Rules",
    "team": "Team",
    "environments": "Environments",
    "health": "Health",
    "definition": "Definition",
}
"""The factory tree's areas (``fx:<area>``)."""

LEVEL_OF: Final[dict[RefKind, tuple[str, str]]] = {
    "product": (PRODUCT_TREE, "product"),
    "goal": (PRODUCT_TREE, "goal"),
    "opportunity": (PRODUCT_TREE, "need"),
    "ask": (PRODUCT_TREE, "need"),
    "need": (PRODUCT_TREE, "need"),
    "initiative": (PRODUCT_TREE, "feature"),
    "study": (PRODUCT_TREE, "feature"),
    "feature": (PRODUCT_TREE, "feature"),
    "project": (PRODUCT_TREE, "project"),
    "task": (PRODUCT_TREE, "task"),
    "adhoc": (PRODUCT_TREE, "task"),
    "pr": (PRODUCT_TREE, "change"),
    "commit": (PRODUCT_TREE, "change"),
    "run": (PRODUCT_TREE, "run"),
    "fx": (FACTORY_TREE, "fx"),
    "area": (FACTORY_TREE, "area"),
    "proposal": (FACTORY_TREE, "item"),
    "decision": (FACTORY_TREE, "item"),
    "member": (FACTORY_TREE, "item"),
    "env": (FACTORY_TREE, "item"),
}
"""``(tree, level)`` of each kind of ref (a project of kind integration or self is an item)."""

_NUMBERED: Final[dict[str, RefKind]] = {
    "G": "goal",
    "O": "opportunity",
    "I": "initiative",
    "R": "study",
    "T": "task",
    "PR": "pr",
    "C": "proposal",
    "D": "decision",
}
_PREFIX: Final[dict[RefKind, str]] = {kind: prefix for prefix, kind in _NUMBERED.items()}
_TYPED: Final[dict[RefKind, str]] = {
    "ask": "ask",
    "need": "need",
    "feature": "feature",
    "adhoc": "adhoc",
    "commit": "commit",
    "run": "run",
    "area": "fx",
    "member": "member",
    "env": "env",
}
"""Kinds whose ref is ``<prefix>:<id>``."""
_KEY_PREFIX: Final[dict[RefKind, str]] = {
    "goal": "goal",
    "opportunity": "opportunity",
    "initiative": "initiative",
    "study": "study",
    "task": "task",
    "proposal": "proposal",
    "decision": "decision",
}
"""Numbered kinds whose key adds a prefix to the ref (``task:T-16``)."""
_KEY_LETTER: Final[dict[str, str]] = {
    prefix: next(letter for letter, kind in _NUMBERED.items() if kind == ref_kind)
    for ref_kind, prefix in _KEY_PREFIX.items()
}
"""Key head -> the letter its ref must carry (``decision`` -> ``D``)."""
_ID_KINDS: Final[dict[str, RefKind]] = {
    "ask": "ask",
    "adhoc": "adhoc",
    "run": "run",
    "member": "member",
}
"""Kinds whose ref carries a uuid (as bare hex)."""

_NUMBER: Final = r"[1-9][0-9]{0,8}"
_NUMBERED_RE: Final = re.compile(rf"^(G|O|I|R|T|PR|C|D)-({_NUMBER})$")
_ANY_NUMBERED_RE: Final = re.compile(rf"^(G|O|I|R|T|PR|C|D)-({_NUMBER})$", re.IGNORECASE)
_ISSUE_RE: Final = re.compile(rf"^(INC|F)-{_NUMBER}$", re.IGNORECASE)
_HEX_RE: Final = re.compile(r"^[0-9a-f]{32}$")
_SHA_RE: Final = re.compile(r"^[0-9a-f]{7,40}$")
_IMPLICIT_RE: Final = re.compile(r"^[a-z0-9][A-Za-z0-9._-]{0,63}$")
"""An implicit parent's key: its origin, e.g. ``from-C-12`` or ``no-goal``."""
_DIGITS_RE: Final = re.compile(rf"^{_NUMBER}$")
_PR_TEXT_RE: Final = re.compile(rf"^(?:PR-|!|#)({_NUMBER})$", re.IGNORECASE)
_REPO_PR_RE: Final = re.compile(rf"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#({_NUMBER})$")
_PR_URL_RE: Final = re.compile(rf"^https?://[^/\s]+/([^/\s]+/[^/\s]+)/pull/({_NUMBER})/?$")
_ID_RE: Final = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TEXT_MAX: Final = 200


@dataclass(frozen=True)
class Ref:
    """
    One parsed ref.

    :param kind: What it names (:data:`RefKind`).
    :param id: The number (``"16"``), slug, implicit key, sha or bare-hex id;
        empty for ``product`` and ``fx``.
    :param repo: The ``owner/name`` a PR's spelling named (``pr:<repo>#<n>``, its
        URL, ``pr:<id>``), so a lookup never lands on another repo's PR; not part
        of the ref (``PR-n``), empty when the spelling named none.
    """

    kind: RefKind
    id: str = ""
    repo: str = ""

    @property
    def text(self) -> str:
        """:returns: The canonical spelling, e.g. ``"T-16"`` or ``"run:<hex>"``."""
        if self.kind in (PRODUCT, FX):
            return self.kind
        if self.kind in _PREFIX:
            return f"{_PREFIX[self.kind]}-{self.id}"
        if self.kind == "project":
            return f"{THREAD_PROJECT_PREFIX}{self.id}" if self.id in RESERVED else self.id
        return f"{_TYPED[self.kind]}:{self.id}"

    @property
    def tree(self) -> str:
        """:returns: ``product`` or ``factory``."""
        return LEVEL_OF[self.kind][0]

    @property
    def level(self) -> str:
        """:returns: Its level key (a factory-tree project is an ``item``: :func:`resolve`)."""
        return LEVEL_OF[self.kind][1]


def _hex(value: str) -> str | None:
    if not _ID_RE.match(value):
        return None
    try:
        return norm_id(value)
    except (InvalidUuidError, ValueError):
        return None


def _implicit(value: str) -> str | None:
    """An implicit key: origin words like ``from-C-12``, never a numbered ref itself."""
    if not _IMPLICIT_RE.match(value) or _ANY_NUMBERED_RE.match(value):
        return None
    return value


def parse(text: object) -> Ref | None:
    """
    Parse one ref in its canonical spelling (see the module table).

    :returns: The ref, or ``None`` when *text* is not one (other spellings: :func:`normalize`).
    """
    if not isinstance(text, str) or not text or len(text) > _TEXT_MAX:
        return None
    if text == PRODUCT:
        return Ref("product")
    if text == FX:
        return Ref("fx")
    if match := _NUMBERED_RE.match(text):
        return Ref(_NUMBERED[match.group(1)], match.group(2))
    if PROJECT_SLUG_RE.match(text):
        return None if text in RESERVED else Ref("project", text)
    head, sep, rest = text.partition(":")
    if not sep:
        return None
    if head == "project":
        return Ref("project", rest) if rest in RESERVED else None
    if (kind := _ID_KINDS.get(head)) is not None:
        return Ref(kind, rest) if _HEX_RE.match(rest) else None
    if head == "need":
        return Ref("need", rest) if _implicit(rest) else None
    if head == "feature":
        return Ref("feature", rest) if _implicit(rest) else None
    if head == "commit":
        return Ref("commit", rest) if _SHA_RE.match(rest) else None
    if head == "env":
        return Ref("env", rest) if PROJECT_SLUG_RE.match(rest) else None
    if head == FX:
        return Ref("area", rest) if rest in FACTORY_AREAS else None
    return None


# ── Facts the lookups read ─────────────────────────────────────


@dataclass(frozen=True)
class Implicit:
    """
    An implicit parent the node projection made (never stored).

    :param kind: ``ask``, ``proposal`` (from a proposal) or ``question``.
    """

    title: str
    kind: str = "proposal"


@dataclass
class RefFacts:
    """
    What resolving a ref reads, loaded once (see :meth:`load`).

    Sequences hold store rows; the mappings are ``id -> title``. ``implicit``
    (``need:…`` / ``feature:…`` refs), ``asks`` and ``commits`` are filled by
    the node projection; until then those refs resolve to ``None``.

    :param initiatives: Rows with ``number`` and ``title`` (features, ``I-n``).
    :param studies: Rows with ``number`` and ``title`` or ``question`` (``R-n``).
    :param commits: Sha -> the task whose local head it is.
    """

    product_title: str = ""
    goals: Sequence[Mapping[str, Any]] = ()
    opportunities: Sequence[Any] = ()
    initiatives: Sequence[Any] = ()
    studies: Sequence[Any] = ()
    projects: Sequence[FactoryProject] = ()
    tasks: Sequence[FactoryTask] = ()
    prs: Sequence[FactoryPr] = ()
    proposals: Sequence[FactoryProposal] = ()
    decisions: Sequence[FactoryDecision] = ()
    loops: Sequence[FactoryLoop] = ()
    members: Mapping[str, str] = field(default_factory=dict)
    asks: Mapping[str, str] = field(default_factory=dict)
    implicit: Mapping[str, Implicit] = field(default_factory=dict)
    commits: Mapping[str, str] = field(default_factory=dict)
    _index: dict[str, dict[Any, Any]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        def by(rows: Iterable[Any], key: Callable[[Any], Any]) -> dict[Any, Any]:
            return {key(r): r for r in rows}

        prs_by_number: dict[int, list[FactoryPr]] = {}
        for pr in self.prs:
            prs_by_number.setdefault(pr.number, []).append(pr)
        prs_by_repo = {(pr.repo.lower(), pr.number): pr for pr in self.prs}
        runs: dict[str, FactoryTask] = {}
        for task in self.tasks:
            for session in task.session_ids or []:
                if (sid := _hex(session)) is not None:
                    runs[sid] = task
        self._index = {
            "goal": by(self.goals, lambda g: str(g.get("id"))),
            "opportunity": by(self.opportunities, lambda o: o.number),
            "initiative": by(self.initiatives, lambda i: i.number),
            "study": by(self.studies, lambda s: s.number),
            "project": by(self.projects, lambda p: p.slug),
            "task": by(self.tasks, lambda t: t.number),
            "task_id": by(self.tasks, lambda t: _hex(t.id)),
            "task_key": by(self.tasks, lambda t: (t.project_slug, t.key)),
            "pr_id": by(self.prs, lambda p: _hex(p.id)),
            "pr": prs_by_number,
            "pr_repo": prs_by_repo,
            "proposal": by(self.proposals, lambda p: p.number),
            "decision": by(self.decisions, lambda d: d.number),
            "loop": by(self.loops, lambda loop: _hex(loop.id)),
            "run": runs,
            "member": {h: t for k, t in self.members.items() if (h := _hex(k)) is not None},
            "ask": {h: t for k, t in self.asks.items() if (h := _hex(k)) is not None},
        }

    def get(self, table: str, key: Any) -> Any:
        """:returns: The row of *table* under *key*, or ``None``."""
        return self._index[table].get(key)

    def pr_by_number(self, number: int, repo: str = "") -> FactoryPr | None:
        """
        :param repo: ``owner/name`` (any case): only that repo's PR.
        :returns: The PR *repo* has under *number*; without *repo*, the one PR
            with *number*, ``None`` when none or several repos share it.
        """
        if repo:
            return self._index["pr_repo"].get((repo.lower(), number))
        found = self._index["pr"].get(number) or []
        return found[0] if len(found) == 1 else None

    def pr_for(self, ref: Ref) -> FactoryPr | None:
        """:returns: The PR a ``pr`` ref names (in the repo it names, if any), or ``None``."""
        return self.pr_by_number(int(ref.id), ref.repo) if ref.kind == "pr" else None

    def numbered(self, number: int) -> bool:
        """Whether any repo has a PR with *number*."""
        return bool(self._index["pr"].get(number))

    @classmethod
    def load(cls, deps: FactoryDeps, factory_id: str) -> RefFacts:
        """Read every row a ref may name from the store (synchronous)."""
        from omnigent.server.factory import definition
        from omnigent.server.factory.envelope import display_title
        from omnigent.server.factory.membership import factory_members

        store = deps.factory_store
        resolved = definition.definition_for(deps, factory_id)
        doc = resolved.doc if resolved is not None else {}
        root = deps.conversation_store.get_conversation(factory_id)
        members = factory_members(factory_id, deps.conversation_store)
        product = (doc.get("product") or {}).get("name") or (root.title if root else "") or ""
        return cls(
            product_title=str(product),
            goals=list((doc.get("strategy") or {}).get("goals") or []),
            opportunities=store.list_opportunities(factory_id, limit=5000),
            initiatives=stored_features(store, factory_id),
            projects=store.list_projects(factory_id),
            tasks=store.list_tasks(factory_id),
            prs=store.list_prs(factory_id),
            proposals=store.list_proposals(factory_id, limit=5000),
            decisions=store.list_decisions(factory_id=factory_id, limit=5000),
            loops=store.list_loops(factory_id),
            members={c.id: display_title(c.title, c.id) for c in members},
        )

    @classmethod
    def for_subject(cls, store: Any, factory_id: str, text: str | None) -> RefFacts:
        """
        Just the rows :func:`canonical_key` reads to write *text*, looked up one
        by one (writing one subject never loads the factory): the PRs with the
        number it names, the PR with the id it names, or its bare slug's project.
        Free text reads nothing.
        """
        value = (text or "").strip()
        if len(value) > _TEXT_MAX:
            return cls()
        head, sep, rest = value.partition(":")
        if sep and head.strip().lower() == "pr":
            spelled = _pr_number(rest.strip())
            if spelled is not None:
                return cls(prs=store.list_prs(factory_id, number=int(spelled[1])))
            found = _hex(rest.strip().split("@", 1)[0])
            pr = store.get_pr(found) if found is not None else None
            mine = pr is not None and norm_id(pr.factory_id) == norm_id(factory_id)
            return cls(prs=[pr] if mine else [])
        bare = parse(value)
        if bare is not None and bare.kind == "project" and ":" not in value:
            project = store.get_project(factory_id, bare.id)
            return cls(projects=[project] if project is not None else [])
        ref = bare or _read(value, None)
        if ref is not None and ref.kind == "pr":
            return cls(prs=store.list_prs(factory_id, number=int(ref.id)))
        return cls()


def stored_features(store: Any, factory_id: str) -> list[Any]:
    """:returns: The factory's stored features: its initiatives (``I-n``)."""
    return list(store.list_initiatives(factory_id, limit=5000))


# ── Reading any spelling ───────────────────────────────────────


def _task_ref(task: FactoryTask | None) -> Ref | None:
    return Ref("task", str(task.number)) if task is not None else None


def _pr_ref(pr: FactoryPr | None) -> Ref | None:
    return Ref("pr", str(pr.number), pr.repo) if pr is not None else None


def _typed(head: str, rest: str, facts: RefFacts | None) -> Ref | None:
    """A ``<prefix>:<rest>`` spelling (prefix lowercased) as a ref."""
    numbered = _ANY_NUMBERED_RE.match(rest)
    letter = numbered.group(1).upper() if numbered else ""
    number = numbered.group(2) if numbered else ""
    if head in _KEY_LETTER and head != "task":
        return Ref(_NUMBERED[letter], number) if letter == _KEY_LETTER[head] else None
    if head == "need":
        if letter == "O":
            return Ref("opportunity", number)
        if rest.lower().startswith("ask:"):
            return _typed("ask", rest[4:], facts)
        return Ref("need", rest) if _implicit(rest) else None
    if head == "feature":
        if letter in ("I", "R"):
            return Ref(_NUMBERED[letter], number)
        return Ref("feature", rest) if _implicit(rest) else None
    if head in ("project", "p"):
        slug = rest.lower()
        return Ref("project", slug) if PROJECT_SLUG_RE.match(slug) else None
    if head == "task":
        return _task(rest, facts)
    if head == "pr":
        return _pr(rest, facts)
    if head == "commit":
        sha = rest.lower()
        return Ref("commit", sha) if _SHA_RE.match(sha) else None
    if (kind := _ID_KINDS.get(head)) is not None:
        found = _hex(rest)
        return Ref(kind, found) if found else None
    if head == "session":
        sid = _hex(rest)
        if sid is None:
            return None
        return Ref("run", sid) if facts and facts.get("run", sid) else Ref("member", sid)
    if head == "env":
        name = rest.lower()
        return Ref("env", name) if PROJECT_SLUG_RE.match(name) else None
    if head == FX:
        area = rest.lower()
        return Ref("area", area) if area in FACTORY_AREAS else None
    if head == "loop":
        return _loop(rest, facts)
    return None


def _task(rest: str, facts: RefFacts | None) -> Ref | None:
    numbered = _ANY_NUMBERED_RE.match(rest)
    if numbered and numbered.group(1).upper() == "T":
        return Ref("task", numbered.group(2))
    if rest.lower().startswith("adhoc:"):
        return _typed("adhoc", rest[6:], facts)
    if facts is None:
        return None
    slug, sep, key = rest.partition("/")
    if sep:
        if not (PROJECT_SLUG_RE.match(slug) and TASK_KEY_RE.match(key)):
            return None
        return _task_ref(facts.get("task_key", (slug, key)))
    found = _hex(rest)
    return _task_ref(facts.get("task_id", found)) if found else None


def _pr_number(rest: str) -> tuple[str, str] | None:
    """``(repo, number)`` of a PR spelled by its number (``repo`` empty when none is named)."""
    if match := _PR_URL_RE.match(rest):
        return match.group(1), match.group(2)
    head = rest.split("@", 1)[0]
    if match := _REPO_PR_RE.match(head):
        return match.group(1), match.group(2)
    if _DIGITS_RE.match(head):
        return "", head
    if match := _PR_TEXT_RE.match(head):
        return "", match.group(1)
    return None


def _pr(rest: str, facts: RefFacts | None) -> Ref | None:
    spelled = _pr_number(rest)
    if spelled is None:
        found = _hex(rest.split("@", 1)[0])
        if facts is None or found is None:
            return None
        return _pr_ref(facts.get("pr_id", found))
    repo, number = spelled
    ref = Ref("pr", number, repo)
    # The named repo has no such PR but another repo does: ``PR-n`` would name
    # that other PR, so the spelling names no node the facts know.
    if repo and facts is not None and facts.pr_for(ref) is None and facts.numbered(int(number)):
        return None
    return ref


def _loop(rest: str, facts: RefFacts | None) -> Ref | None:
    """A loop names the node it works on: its task, else its PR, else its project."""
    found = _hex(rest)
    loop = facts.get("loop", found) if facts and found else None
    if loop is None or facts is None:
        return None
    if loop.task_id and (task := facts.get("task_id", _hex(loop.task_id))) is not None:
        return _task_ref(task)
    if loop.pr_id and (pr := facts.get("pr_id", _hex(loop.pr_id))) is not None:
        return _pr_ref(pr)
    return Ref("project", loop.project_slug) if loop.project_slug else None


def _lenient(value: str, facts: RefFacts | None) -> Ref | None:
    lowered = value.lower()
    if lowered in (PRODUCT, THREAD_FACTORY, f"#{THREAD_FACTORY}"):
        return Ref(PRODUCT)
    if lowered == FX:
        return Ref(FX)
    if _ISSUE_RE.match(value):
        return None
    if match := _PR_TEXT_RE.match(value):
        return Ref("pr", match.group(1))
    if (numbered := _ANY_NUMBERED_RE.match(value)) and value[0].isupper():
        return Ref(_NUMBERED[numbered.group(1).upper()], numbered.group(2))
    if value.startswith("#"):
        slug = value[1:].lower()
        known = PROJECT_SLUG_RE.match(slug) and slug not in (THREAD_IMPROVEMENTS, THREAD_DISCOVERY)
        return Ref("project", slug) if known else None
    head, sep, rest = value.partition(":")
    if not sep:
        return None
    return _typed(head.strip().lower(), rest.strip(), facts)


def normalize(text: object, facts: RefFacts | None = None) -> str | None:
    """
    Any spelling of a node, old or new, as its ref (the read-time shim).

    Reads refs, keys (``task:T-4``, ``project:<slug>``, ``pr:<repo>#<n>``,
    ``decision:D-7``, ``member:<id>``), the chat refs (``!9630``, ``#<slug>``,
    ``p:<slug>``) and, with *facts*, spellings only the store can map
    (``pr:<id>``, ``task:<slug>/<key>``, ``task:<id>``, ``loop:<id>``, and
    ``session:<id>`` as a run when a task ran it). A bare slug reads as a
    project; whether one exists is :func:`resolve`'s call. A PR spelling that
    names a repo (``pr:<repo>#<n>``, its URL) is ``None`` when *facts* know
    PR ``n`` only in other repos: ``PR-n`` would name one of those.

    :returns: The canonical ref, or ``None`` when *text* names no node.
    """
    ref = _read(text, facts)
    return ref.text if ref is not None else None


def _read(text: object, facts: RefFacts | None) -> Ref | None:
    """:func:`normalize`'s ref, keeping the repo a PR's spelling named (:attr:`Ref.repo`)."""
    if not isinstance(text, str):
        return None
    value = text.strip()
    if not value or len(value) > _TEXT_MAX:
        return None
    return parse(value) or _lenient(value, facts)


# ── Writing one spelling ───────────────────────────────────────

_KEPT_HEADS: Final = frozenset({"pr", "session", "member", "run", "ask", "adhoc", "loop"})
"""Key heads whose id the writer chose; their keys are kept exactly as written."""


def key(ref: str, facts: RefFacts | None = None) -> str | None:
    """
    The key spelling of a ref, as subjects and targets store it.

    A change is ``pr:<id>`` when *facts* know the PR (the spelling decisions
    and issues act on), else ``pr:<n>``.

    :returns: e.g. ``"task:T-16"``, ``"project:<slug>"``, ``"factory"``; ``None`` for a non-ref.
    """
    parsed = parse(ref)
    if parsed is None:
        return None
    if parsed.kind == PRODUCT:
        return THREAD_FACTORY
    if parsed.kind == "pr":
        pr = facts.pr_by_number(int(parsed.id)) if facts is not None else None
        return f"{THREAD_PR_PREFIX}{norm_id(pr.id) if pr is not None else parsed.id}"
    if parsed.kind == "project":
        return f"{THREAD_PROJECT_PREFIX}{parsed.id}"
    if parsed.kind in _KEY_PREFIX:
        return f"{_KEY_PREFIX[parsed.kind]}:{parsed.text}"
    return parsed.text


def canonical_key(text: str | None, facts: RefFacts | None = None) -> str | None:
    """
    Normalize on write: one spelling per node for a subject, target or thread.

    Every key the server writes comes back exactly as it is (readers and
    dedupe keys match on it), and so do keys whose id the writer chose
    (``pr:``, ``member:``, ``session:``, ``task:<slug>/<key>``); events and
    issues pass no *facts*, so this always holds for them. Another
    spelling of a node becomes its :func:`key`: ``T-4`` and ``Task:t-4``
    become ``task:T-4``, ``#api`` and ``p:api`` ``project:api``, ``D-7``
    ``decision:D-7``, ``PR-9876`` and ``!9876`` ``pr:9876``, ``product``
    ``factory``. With *facts*, every spelling of a PR they know (``pr:9876``,
    ``pr:<repo>#9876``, its URL) becomes ``pr:<id>``, the spelling decisions
    act on (a spelling that names a repo only when that repo has the PR), and
    a project's bare slug ``project:<slug>``. Anything else (free text,
    ``mail:<id>``, ``budget:<scope>``, another repo's PR) is returned unchanged.
    """
    if text is None:
        return None
    value = text.strip()
    if not value or len(value) > _TEXT_MAX:
        return text
    head, sep, rest = value.partition(":")
    if sep and head.strip().lower() == "pr" and facts is not None:
        found = _pr(rest.strip(), facts)
        pr = facts.pr_for(found) if found is not None else None
        return f"{THREAD_PR_PREFIX}{norm_id(pr.id)}" if pr is not None else text
    if sep and head in _KEPT_HEADS:
        return text
    if sep and head == "task" and not _ANY_NUMBERED_RE.match(rest):
        return text
    ref = parse(value)
    if ref is not None and ref.kind == "project" and ":" not in value:
        known = facts is not None and facts.get("project", ref.id) is not None
        return key(ref.text) if known else text
    ref = ref or _lenient(value, facts)
    if ref is None:
        return text
    return key(ref.text, facts) or text


# ── Threads ────────────────────────────────────────────────────

THREAD_HEADS: Final[dict[str, str]] = {"need": "need", "feature": "feature", "task": "task"}
"""Level -> the head of its node threads' keys (a study's is ``study``)."""


def thread_key(ref: str, facts: RefFacts | None = None) -> str | None:
    """
    The chat thread of a node: ``factory`` (product), ``improvements`` (fx),
    ``project:<slug>``, ``pr:<id>`` (with *facts*), ``study:R-n``, and
    ``need:``, ``feature:`` or ``task:`` with the ref (``need:O-3``,
    ``feature:I-1``, ``task:T-16``; an implicit ``need:<key>`` is its own key).

    :returns: The key, or ``None`` for a node without a thread (a run shows its task's).
    """
    parsed = parse(ref)
    if parsed is None:
        return None
    if parsed.kind == PRODUCT:
        return THREAD_FACTORY
    if parsed.kind == FX:
        return THREAD_IMPROVEMENTS
    if parsed.kind == "project":
        return f"{THREAD_PROJECT_PREFIX}{parsed.id}"
    if parsed.kind == "pr":
        pr = facts.pr_by_number(int(parsed.id)) if facts is not None else None
        return f"{THREAD_PR_PREFIX}{norm_id(pr.id)}" if pr is not None else None
    head = "study" if parsed.kind == "study" else THREAD_HEADS.get(parsed.level)
    if head is None or parsed.tree != PRODUCT_TREE:
        return None
    return parsed.text if parsed.text.startswith(f"{head}:") else f"{head}:{parsed.text}"


def thread_ref(thread: str, facts: RefFacts | None = None) -> str | None:
    """:returns: The node a chat thread is about (``factory`` -> ``product``), or ``None``."""
    if thread == THREAD_FACTORY:
        return PRODUCT
    if thread == THREAD_IMPROVEMENTS:
        return FX
    head = thread.partition(":")[0]
    ref = normalize(thread, facts)
    parsed = parse(ref) if ref is not None else None
    if parsed is None:
        return None
    wanted = {
        "need": parsed.level == "need",
        "feature": parsed.level == "feature" and parsed.kind != "study",
        "study": parsed.kind == "study",
        "task": parsed.level == "task",
        "project": parsed.kind == "project",
        "pr": parsed.kind == "pr",
    }
    return ref if wanted.get(head, False) else None


# ── Resolving a ref to its node ────────────────────────────────


@dataclass(frozen=True)
class Node:
    """
    A ref resolved against the facts.

    :param kind: The node's kind (a key of the definition's ``structure.kinds``).
    :param row: The store row (or definition item) behind it, when there is one.
    """

    ref: str
    kind: str
    tree: str
    level: str
    title: str
    implicit: bool = False
    row: Any = None


def _title(row: Any, *names: str) -> str:
    for name in names:
        found = row.get(name) if isinstance(row, Mapping) else getattr(row, name, None)
        if found:
            return str(found)
    return ""


def resolve(facts: RefFacts, ref: object) -> Node | None:
    """
    The node *ref* names in *facts* (any spelling :func:`normalize` reads).

    :returns: The node, or ``None`` for unknown text or a node the facts lack.
    """
    parsed = _read(ref, facts)
    if parsed is None:
        return None
    text = parsed.text
    tree, level = LEVEL_OF[parsed.kind]

    def node(kind: str, title: str, row: Any = None, *, implicit: bool = False) -> Node:
        return Node(text, kind, tree, level, title, implicit, row)

    kind = parsed.kind
    number = int(parsed.id) if parsed.id.isdigit() else 0
    if kind == PRODUCT:
        return node("product", facts.product_title)
    if kind == FX:
        return node("factory", "")
    if kind == "area":
        return node("area", FACTORY_AREAS[parsed.id])
    if kind in ("need", "feature"):
        found = facts.implicit.get(text)
        return node(found.kind, found.title, implicit=True) if found else None
    if kind == "ask":
        title = facts.get("ask", parsed.id)
        return node("ask", title) if title is not None else None
    if kind == "goal":
        goal = facts.get("goal", text)
        return node("goal", _title(goal, "statement")) if goal is not None else None
    numbered_rows: dict[RefKind, tuple[str, str, tuple[str, ...]]] = {
        "opportunity": ("opportunity", "opportunity", ("title", "statement")),
        "initiative": ("initiative", "feature", ("title",)),
        "study": ("study", "study", ("title", "question")),
        "decision": ("decision", "decision", ("question",)),
    }
    if kind in numbered_rows:
        table, shown, names = numbered_rows[kind]
        row = facts.get(table, number)
        return node(shown, _title(row, *names), row) if row is not None else None
    if kind == "proposal":
        row = facts.get("proposal", number)
        if row is None:
            return None
        return node(
            "rule" if row.kind in ("tuning", "rules", "code") else "proposal", row.title, row
        )
    if kind == "project":
        row = facts.get("project", parsed.id)
        if row is None:
            return None
        if project_kind(row) != PROJECT_KIND_WORK:
            return Node(text, "project", FACTORY_TREE, "item", row.title, False, row)
        return node("project", row.title, row)
    if kind == "env":
        row = facts.get("project", parsed.id)
        return node("env", row.title, row) if row is not None else None
    if kind == "task":
        row = facts.get("task", number)
        return node("adhoc" if row.kind == "adhoc" else "task", row.title, row) if row else None
    if kind == "adhoc":
        # The implicit task of a PR that no task owns.
        row = facts.get("pr_id", parsed.id)
        if row is None or row.task_id:
            return None
        return node("adhoc", row.title or f"#{row.number}", row, implicit=True)
    if kind == "pr":
        # In the repo its spelling named, if any: never another repo's PR.
        row = facts.pr_for(parsed)
        return node("pr", row.title or f"#{row.number}", row) if row is not None else None
    if kind == "commit":
        task_ref = facts.commits.get(parsed.id)
        return node("commit", parsed.id[:7]) if task_ref is not None else None
    if kind == "run":
        task = facts.get("run", parsed.id)
        if task is None:
            return None
        return node("run", facts.get("member", parsed.id) or task.title, task)
    if kind == "member":
        title = facts.get("member", parsed.id)
        return node("agent", title) if title is not None else None
    return None


__all__ = [
    "FACTORY_AREAS",
    "FX",
    "LEVEL_OF",
    "PRODUCT",
    "RESERVED",
    "THREAD_HEADS",
    "Implicit",
    "Node",
    "Ref",
    "RefFacts",
    "RefKind",
    "canonical_key",
    "key",
    "normalize",
    "parse",
    "resolve",
    "stored_features",
    "thread_key",
    "thread_ref",
]
