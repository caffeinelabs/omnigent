"""One search over a factory for the cockpit's ⌘K: ``GET …/search?q=``.

The palette types and the server finds, so the client does not hold the whole
factory in memory: every id (``O-``, ``S-``, ``G-``, ``C-``, ``D-``, ``INC-``,
``F-``, ``T-``, ``R-``, ``!`` PRs, ``#`` projects, ``v`` versions), titles,
members (with vendor, model and state), sources, the definition's field names
(and their old names, ``models.ic``), definition versions and Room threads.

What a viewer sees is what the other reads show them: the snapshot cut to
the members they may read (projects, PRs, decisions, agents), issues by the
same rule as ``GET …/issues``, tasks and threads of the projects they see.
Opportunities, signals and sources answer only while discovery runs for the
factory (the server env and ``discovery.enabled``), like the discovery routes.

Ranking: a ref typed in full first, then a ref by its prefix or bare number,
then titles that start with or contain the query, then anything whose words
all match; ties go by kind, then by each kind's own order (newest, open
first). The client groups and labels the hits and builds their links.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    FIXED_THREADS,
    PROPOSAL_OPEN_STATUSES,
    TASK_CLOSED_STATES,
    THREAD_PR_PREFIX,
    THREAD_PROJECT_PREFIX,
)
from omnigent.server.factory import definition, issues
from omnigent.server.factory.definition_blocks import LEGACY_MODELS
from omnigent.server.factory.snapshot import filter_snapshot

if TYPE_CHECKING:
    from omnigent.entities.factory import (
        FactoryDefinition,
        FactoryIssue,
        FactoryProposal,
        FactoryTask,
    )
    from omnigent.entities.factory_discovery import FactoryOpportunity, FactorySignal
    from omnigent.server.factory.api.agents import FleetContext

Q_MAX: Final = 200
"""The longest query read (longer ones are cut)."""
LIMIT_DEFAULT: Final = 40
LIMIT_MAX: Final = 100
PER_KIND: Final = 8
"""At most this many hits of one kind, so one kind (signals) never floods the list."""
PROPOSAL_SCAN: Final = 500
ISSUE_SCAN: Final = 500
VERSION_SCAN: Final = 50
SIGNAL_SCAN: Final = 2000
"""Signal text matches look at the newest this many signals; an exact ``S-n`` always resolves."""
SIGNAL_MIN_CHARS: Final = 3
"""Signal text is searched only from this many characters (an ``S-n`` always is)."""
SIGNAL_TITLE_MAX: Final = 160

KINDS: Final = (
    "opportunity",
    "project",
    "pr",
    "agent",
    "bet",
    "proposal",
    "decision",
    "goal",
    "source",
    "setting",
    "rule",
    "signal",
    "version",
    "thread",
    "task",
    "incident",
    "finding",
)
"""Every kind a hit can be, in the order ties go."""
DISCOVERY_KINDS: Final = frozenset({"opportunity", "signal", "source"})

SCORE_REF: Final = 6
"""The query is the ref (``O-7``, ``!9631``, ``models.ic``)."""
SCORE_REF_PREFIX: Final = 5
"""The ref starts with a ref-like query (``#db``, ``O-1``) or is its bare number (``9631``)."""
SCORE_TITLE_START: Final = 4
SCORE_TITLE: Final = 3
"""The title contains the whole query ("custom domain" finds the opportunity, not a verb)."""
SCORE_WORDS: Final = 1

ROLE_WORDS: Final[dict[str, str]] = {
    "lead": "Lead",
    "project_lead": "Project lead",
    "ic": "Builder",
    "reviewer": "Reviewer",
    "env_keeper": "Env keeper",
    "demo": "Demo",
    "planner": "Planner",
    "scout": "Scout",
}
VIA_WORDS: Final[dict[str, str]] = {
    "seed": "The first definition",
    "human": "A person's change",
    "proposal": "An applied proposal",
    "revert": "A revert",
}

_REF_LIKE = re.compile(r"^(?:[!#@]|[a-z]+-\d|v\d)", re.IGNORECASE)
_BARE = re.compile(r"^(?:[!#@]|[a-z]+-(?=\d)|v(?=\d))", re.IGNORECASE)
_SPACES = re.compile(r"\s+")


# ── The query and scoring ──────────────────────────────────────


@dataclass(frozen=True)
class Query:
    """A query, folded: the whole text and its words."""

    text: str
    words: tuple[str, ...]
    ref_like: bool

    @classmethod
    def parse(cls, raw: str | None) -> Query | None:
        """:returns: The folded query, or ``None`` when it is blank."""
        text = _SPACES.sub(" ", (raw or "")[:Q_MAX]).strip().casefold()
        if not text:
            return None
        return cls(text=text, words=tuple(text.split(" ")), ref_like=bool(_REF_LIKE.match(text)))


def bare_ref(ref: str) -> str:
    """``!9631`` → ``9631``, ``O-7`` → ``7``, ``#db-version`` → ``db-version``."""
    return _BARE.sub("", ref, count=1)


def score(
    query: Query,
    *,
    ref: str | None,
    title: str,
    keywords: Iterable[str | None] = (),
    aliases: Iterable[str] = (),
) -> int:
    """
    How well a hit matches *query* (0: not at all).

    :param ref: Its id as people type it (``O-7``, ``!9631``, ``#slug``).
    :param aliases: Other names its ref goes by (a path's old name).
    """
    folded_ref = (ref or "").casefold()
    if folded_ref and folded_ref == query.text:
        return SCORE_REF
    if any(alias.casefold() == query.text for alias in aliases):
        return SCORE_REF
    if folded_ref and (
        (query.ref_like and folded_ref.startswith(query.text))
        or bare_ref(folded_ref) == query.text
    ):
        return SCORE_REF_PREFIX
    folded_title = title.casefold()
    if folded_title.startswith(query.text):
        return SCORE_TITLE_START
    if query.text in folded_title:
        return SCORE_TITLE
    hay = " ".join(
        [folded_title, folded_ref, *(k.casefold() for k in keywords if k), *aliases]
    ).casefold()
    if all(word in hay for word in query.words):
        return SCORE_WORDS
    return 0


# ── What a search reads ────────────────────────────────────────


@dataclass
class SearchInputs:
    """
    Everything one viewer's search reads (built by :func:`load_inputs`).

    :param body: The snapshot cut to the viewer (``filter_snapshot``).
    :param doc: The definition in force (effective values).
    :param discovery: Discovery runs for the factory.
    :param threads: ``(key, title)`` of the Room threads the viewer sees.
    """

    body: Mapping[str, Any]
    doc: Mapping[str, Any] = field(default_factory=dict)
    discovery: bool = False
    proposals: Sequence[FactoryProposal] = ()
    issues: Sequence[FactoryIssue] = ()
    tasks: Sequence[FactoryTask] = ()
    versions: Sequence[FactoryDefinition] = ()
    pinned: int | None = None
    threads: Sequence[tuple[str, str]] = ()
    opportunities: Sequence[FactoryOpportunity] = ()
    signals: Sequence[FactorySignal] = ()
    paths: Sequence[str] = ()


@dataclass
class _Hit:
    kind: str
    ref: str | None
    title: str
    detail: str | None
    score: int
    order: int
    extra: dict[str, Any] = field(default_factory=dict)

    def view(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "title": self.title,
            "detail": self.detail,
            "score": self.score,
            **self.extra,
        }


def _join(*parts: Any) -> str | None:
    words = [str(p) for p in parts if p not in (None, "")]
    return " · ".join(words) or None


def _clip(text: str, limit: int) -> str:
    flat = _SPACES.sub(" ", text).strip()
    return flat if len(flat) <= limit else f"{flat[: limit - 1].rstrip()}…"


def handle_name(handle: str) -> str:
    """``dot`` → ``Dot``, ``big-rex`` → ``Big rex`` (the cockpit's name for a member)."""
    spaced = handle.replace("-", " ")
    return spaced[:1].upper() + spaced[1:]


# ── Hits per kind ──────────────────────────────────────────────


def _projects(query: Query, body: Mapping[str, Any]) -> Iterable[_Hit]:
    prs_by_project: dict[str, list[str]] = {}
    for pr in body.get("prs") or []:
        prs_by_project.setdefault(pr.get("project_slug") or "", []).append(f"!{pr['number']}")
    for i, project in enumerate(body.get("projects") or []):
        slug = project["slug"]
        charter = project.get("charter") or {}
        outcome = charter.get("outcome")
        title = outcome or project.get("title") or slug
        found = score(
            query,
            ref=f"#{slug}",
            title=title,
            keywords=[
                project.get("title"),
                slug,
                charter.get("goal"),
                *prs_by_project.get(slug, ()),
            ],
        )
        if found:
            status = project.get("status") or "active"
            yield _Hit(
                "project",
                f"#{slug}",
                title,
                _join(project.get("stage") if status == "active" else status),
                found,
                i if status == "active" else 1000 + i,
                {"slug": slug},
            )


def _prs(query: Query, body: Mapping[str, Any]) -> Iterable[_Hit]:
    rows = sorted(body.get("prs") or [], key=lambda p: -int(p.get("number") or 0))
    for i, pr in enumerate(rows):
        ref = f"!{pr['number']}"
        title = pr.get("title") or ref
        found = score(
            query,
            ref=ref,
            title=title,
            keywords=[
                pr.get("head_ref"),
                pr.get("task"),
                pr.get("task_ref"),
                pr.get("project_slug"),
            ],
        )
        if found:
            state = pr.get("state")
            word = ("ready" if pr.get("ready") else "open") if state == "open" else state
            yield _Hit(
                "pr",
                ref,
                title,
                _join(f"#{pr['project_slug']}" if pr.get("project_slug") else None, word),
                found,
                i if state == "open" else 1000 + i,
                {"id": pr["id"], "slug": pr.get("project_slug")},
            )


def _decisions(query: Query, body: Mapping[str, Any]) -> Iterable[_Hit]:
    for i, decision in enumerate(body.get("decisions") or []):
        ref = decision.get("ref") or ""
        title = decision.get("question") or ref
        found = score(
            query,
            ref=ref,
            title=title,
            keywords=[decision.get("project_slug"), decision.get("asked_by_title")],
        )
        if found:
            status = decision.get("status")
            yield _Hit(
                "decision",
                ref,
                title,
                _join(
                    f"#{decision['project_slug']}" if decision.get("project_slug") else None,
                    decision.get("asked_by_title"),
                    None if status == "open" else status,
                ),
                found,
                i if status == "open" else 1000 + i,
                {"id": decision["id"], "slug": decision.get("project_slug")},
            )


def _agents(query: Query, body: Mapping[str, Any]) -> Iterable[_Hit]:
    for i, member in enumerate(body.get("members") or []):
        handle = member.get("handle")
        title = handle_name(handle) if handle else member.get("title") or ""
        role = ROLE_WORDS.get(member.get("role") or "", member.get("role"))
        found = score(
            query,
            ref=f"@{handle}" if handle else None,
            title=title,
            keywords=[
                member.get("title"),
                role,
                member.get("vendor"),
                member.get("model"),
                member.get("state"),
                member.get("project"),
                member.get("harness"),
            ],
        )
        if found:
            yield _Hit(
                "agent",
                f"@{handle}" if handle else None,
                title,
                _join(role, member.get("vendor"), member.get("model"), member.get("state")),
                found,
                i,
                {
                    "id": member["session_id"],
                    "handle": handle,
                    "role": member.get("role"),
                    "vendor": member.get("vendor"),
                    "model": member.get("model"),
                    "state": member.get("state"),
                    "slug": member.get("project"),
                },
            )


def _proposals(query: Query, rows: Sequence[FactoryProposal]) -> Iterable[_Hit]:
    for i, row in enumerate(rows):
        ref = row.ref
        found = score(
            query,
            ref=ref,
            title=row.title,
            keywords=[row.kind, row.status, row.opportunity],
        )
        if found:
            bet = row.bet is not None
            live = row.status in PROPOSAL_OPEN_STATUSES or row.status == "trial"
            yield _Hit(
                "bet" if bet else "proposal",
                ref,
                row.title,
                _join(row.opportunity if bet else row.kind, row.status),
                found,
                i if live else 1000 + i,
                {"id": row.id, "status": row.status, "opportunity": row.opportunity},
            )


def _issues(query: Query, rows: Sequence[FactoryIssue]) -> Iterable[_Hit]:
    for i, row in enumerate(rows):
        ref = row.ref
        if ref is None:
            continue
        found = score(
            query,
            ref=ref,
            title=row.summary,
            keywords=[row.rule, row.category, row.project_slug, row.status],
        )
        if found:
            live = row.status in ("open", "acked")
            yield _Hit(
                row.class_,
                ref,
                row.summary,
                _join(f"#{row.project_slug}" if row.project_slug else None, row.status),
                found,
                i if live else 1000 + i,
                {"id": row.id, "slug": row.project_slug},
            )


def _tasks(query: Query, rows: Sequence[FactoryTask], visible_prs: set[str]) -> Iterable[_Hit]:
    for i, row in enumerate(sorted(rows, key=lambda t: -t.number)):
        found = score(query, ref=row.ref, title=row.title, keywords=[row.key, row.project_slug])
        if found:
            done = row.state in TASK_CLOSED_STATES
            yield _Hit(
                "task",
                row.ref,
                row.title,
                _join(f"#{row.project_slug}", row.state),
                found,
                1000 + i if done else i,
                {
                    "id": row.id,
                    "slug": row.project_slug,
                    "pr_id": row.pr_id if row.pr_id in visible_prs else None,
                },
            )


def _goals(query: Query, doc: Mapping[str, Any]) -> Iterable[_Hit]:
    goals = (doc.get("strategy") or {}).get("goals") or []
    for i, goal in enumerate(goals):
        ref = str(goal.get("id") or "")
        title = str(goal.get("statement") or ref)
        found = score(query, ref=ref, title=title, keywords=[goal.get("metric")])
        if found:
            yield _Hit("goal", ref, title, _join(ref, goal.get("metric")), found, i)


def _rules(query: Query, doc: Mapping[str, Any]) -> Iterable[_Hit]:
    for i, rule in enumerate(doc.get("rules") or []):
        ref = str(rule.get("id") or "")
        text = str(rule.get("text") or "")
        role = ROLE_WORDS.get(str(rule.get("role") or ""), rule.get("role"))
        found = score(query, ref=ref, title=text, keywords=[role, rule.get("project")])
        if found:
            project = rule.get("project")
            yield _Hit(
                "rule",
                ref,
                text,
                _join(role, f"#{project}" if project and project != "*" else None),
                found,
                i,
            )


def _sources(query: Query, doc: Mapping[str, Any]) -> Iterable[_Hit]:
    sources = (doc.get("discovery") or {}).get("sources") or []
    for i, source in enumerate(sources):
        ref = str(source.get("id") or "")
        title = str(source.get("label") or ref)
        found = score(query, ref=ref, title=title, keywords=[source.get("kind")])
        if found:
            yield _Hit(
                "source",
                ref,
                title,
                _join(source.get("kind"), source.get("state")),
                found,
                i,
                {"state": source.get("state"), "source_kind": source.get("kind")},
            )


def _opportunity_status(row: FactoryOpportunity) -> str:
    if row.merged_into is not None:
        return f"merged into O-{row.merged_into}"
    if row.kill:
        return "killed"
    if row.park:
        return "parked"
    return "open"


def _opportunities(query: Query, rows: Sequence[FactoryOpportunity]) -> Iterable[_Hit]:
    for i, row in enumerate(sorted(rows, key=lambda o: -o.number)):
        ref = f"O-{row.number}"
        found = score(
            query,
            ref=ref,
            title=row.title,
            keywords=[row.statement, row.problem, row.goal, row.area, *row.segments],
        )
        if found:
            status = _opportunity_status(row)
            yield _Hit(
                "opportunity",
                ref,
                row.title,
                _join(row.goal, status),
                found,
                i if status == "open" else 1000 + i,
                {"status": status},
            )


def _signals(query: Query, rows: Sequence[FactorySignal]) -> Iterable[_Hit]:
    for i, row in enumerate(rows):
        if row.withdrawn_at is not None or row.expired_at is not None or not row.excerpt:
            continue
        ref = f"S-{row.number}"
        title = _clip(row.excerpt, SIGNAL_TITLE_MAX)
        found = score(query, ref=ref, title=row.excerpt, keywords=[row.paraphrase])
        if found:
            yield _Hit(
                "signal",
                ref,
                title,
                _join(row.source, row.kind or "unlabelled", "flagged" if row.flags else None),
                found,
                i,
            )


def _settings(query: Query, paths: Sequence[str]) -> Iterable[_Hit]:
    old = {new: name for name, new in LEGACY_MODELS.items()}
    for i, path in enumerate(paths):
        aliases = [old[path]] if path in old else []
        found = score(
            query,
            ref=path,
            title=path,
            keywords=[path.replace(".", " ").replace("_", " ")],
            aliases=aliases,
        )
        if found:
            block = path.split(".", 1)[0]
            yield _Hit(
                "setting",
                path,
                path,
                block,
                found,
                i,
                {"path": path, "alias": aliases[0] if aliases else None},
            )


def _versions(
    query: Query,
    rows: Sequence[FactoryDefinition],
    proposals: Mapping[str, FactoryProposal],
    pinned: int | None,
) -> Iterable[_Hit]:
    for i, row in enumerate(rows):
        ref = f"v{row.version}"
        proposal = proposals.get(row.proposal_id or "")
        title = proposal.title if proposal is not None else VIA_WORDS.get(row.created_via, ref)
        found = score(
            query,
            ref=ref,
            title=title,
            keywords=[row.created_via, proposal.ref if proposal is not None else None],
        )
        if found:
            yield _Hit(
                "version",
                ref,
                title,
                _join(
                    proposal.ref if proposal is not None else None,
                    "in force" if row.version == pinned else None,
                ),
                found,
                i,
                {
                    "version": row.version,
                    "proposal": proposal.ref if proposal is not None else None,
                },
            )


def _threads(query: Query, threads: Sequence[tuple[str, str]]) -> Iterable[_Hit]:
    for i, (key, title) in enumerate(threads):
        found = score(query, ref=title, title=title, keywords=["room", "thread", key])
        if found:
            yield _Hit("thread", title, title, "Room thread", found, i, {"thread": key})


# ── Search ─────────────────────────────────────────────────────


def search(inputs: SearchInputs, raw_query: str | None, limit: int = LIMIT_DEFAULT) -> dict:
    """
    Every hit of the query in what the viewer may read, best first.

    :returns: ``{q, discovery, results, total}``: at most *limit* hits (and
        :data:`PER_KIND` of one kind); ``total`` counts them all.
    """
    query = Query.parse(raw_query)
    out: dict[str, Any] = {
        "q": (raw_query or "")[:Q_MAX],
        "discovery": inputs.discovery,
        "results": [],
        "total": 0,
    }
    if query is None:
        return out
    body = inputs.body
    visible_prs = {pr["id"] for pr in body.get("prs") or []}
    by_id = {p.id: p for p in inputs.proposals}
    hits: list[_Hit] = [
        *_projects(query, body),
        *_prs(query, body),
        *_decisions(query, body),
        *_agents(query, body),
        *_proposals(query, inputs.proposals),
        *_issues(query, inputs.issues),
        *_tasks(query, inputs.tasks, visible_prs),
        *_goals(query, inputs.doc),
        *_rules(query, inputs.doc),
        *_settings(query, inputs.paths),
        *_versions(query, inputs.versions, by_id, inputs.pinned),
        *_threads(query, inputs.threads),
    ]
    if inputs.discovery:
        hits += [
            *_opportunities(query, inputs.opportunities),
            *_sources(query, inputs.doc),
            *_signals(query, inputs.signals),
        ]
    rank = {kind: i for i, kind in enumerate(KINDS)}
    hits.sort(key=lambda h: (-h.score, rank.get(h.kind, len(KINDS)), h.order))
    kept: list[_Hit] = []
    per_kind: dict[str, int] = {}
    for hit in hits:
        if per_kind.get(hit.kind, 0) >= PER_KIND:
            continue
        per_kind[hit.kind] = per_kind.get(hit.kind, 0) + 1
        kept.append(hit)
        if len(kept) >= limit:
            break
    out["results"] = [h.view() for h in kept]
    out["total"] = len(hits)
    return out


def room_threads(body: Mapping[str, Any], pr_thread_keys: Iterable[str]) -> list[tuple[str, str]]:
    """
    ``(key, title)`` of the Room threads a viewer of *body* sees: the fixed
    ones, one per visible project, and each visible PR's thread with messages.
    """
    out = [(key, f"#{key}") for key in FIXED_THREADS]
    out += [(f"{THREAD_PROJECT_PREFIX}{p['slug']}", f"#{p['slug']}") for p in body["projects"]]
    numbers = {_hex(pr["id"]): pr["number"] for pr in body.get("prs") or []}
    for key in pr_thread_keys:
        number = numbers.get(_hex(key[len(THREAD_PR_PREFIX) :]))
        if number is not None:
            out.append((key, f"!{number}"))
    return out


def _hex(row_id: str) -> str:
    """A row id as bare lower-case hex (thread keys and snapshot ids may differ in dashes)."""
    return row_id.replace("-", "").lower()


def load_inputs(ctx: FleetContext, body: Mapping[str, Any], query: Query) -> SearchInputs:
    """Read what one search needs (synchronous: run it in a worker thread)."""
    from omnigent.server.factory.discovery.opportunities import effective_doc

    deps, factory_id = ctx.deps, ctx.root.id
    store = deps.factory_store
    doc, pinned = effective_doc(deps, factory_id)
    discovery = definition.discovery_enabled(deps, factory_id)
    visible = {p["slug"] for p in body["projects"]}
    issue_rows = [
        row
        for row in store.list_issues(factory_id, limit=ISSUE_SCAN)
        if issues.visible_to(
            row.project_slug,
            row.target,
            hidden=ctx.hidden,
            top_level_of=ctx.raw.top_level_of,
            readable=ctx.readable,
        )
    ]
    stats = store.chat_thread_stats(factory_id)
    inputs = SearchInputs(
        body=body,
        doc=doc,
        discovery=discovery,
        proposals=store.list_proposals(factory_id, limit=PROPOSAL_SCAN),
        issues=issue_rows,
        tasks=[t for t in store.list_tasks(factory_id) if t.project_slug in visible],
        versions=store.list_definitions(factory_id, limit=VERSION_SCAN),
        pinned=pinned,
        threads=room_threads(
            body, (s.thread for s in stats if s.thread.startswith(THREAD_PR_PREFIX))
        ),
        paths=[*definition.schema_paths(), "rules"],
    )
    if discovery:
        inputs.opportunities = store.list_opportunities(factory_id)
        signals: list[FactorySignal] = []
        exact = re.fullmatch(r"s-(\d+)", query.text)
        if exact:
            row = store.get_signal(factory_id, int(exact.group(1)))
            if row is not None:
                signals.append(row)
        if len(query.text) >= SIGNAL_MIN_CHARS and not exact:
            signals += store.list_signals(factory_id, limit=SIGNAL_SCAN)
        inputs.signals = signals
    return inputs


async def search_for_viewer(ctx: FleetContext, raw_query: str | None, limit: int) -> dict:
    """``GET …/search``: :func:`search` over what the caller of *ctx* may read."""
    query = Query.parse(raw_query)
    if query is None:
        discovery = await asyncio.to_thread(definition.discovery_enabled, ctx.deps, ctx.root.id)
        return search(SearchInputs(body={}, discovery=discovery), raw_query, limit)
    body = filter_snapshot(ctx.raw, ctx.readable)
    inputs = await asyncio.to_thread(load_inputs, ctx, body, query)
    return search(inputs, raw_query, limit)


__all__ = [
    "KINDS",
    "LIMIT_DEFAULT",
    "LIMIT_MAX",
    "PER_KIND",
    "Q_MAX",
    "Query",
    "SearchInputs",
    "bare_ref",
    "handle_name",
    "load_inputs",
    "room_threads",
    "score",
    "search",
    "search_for_viewer",
]
