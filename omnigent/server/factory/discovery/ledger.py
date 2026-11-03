"""The signal ledger: the only code that writes a signal's text (Tier 0).

Every piece of evidence reaches ``factory_signals`` through :func:`ingest`,
in a fixed order: redact (:mod:`.redact`), hash the account id with the
factory's salt, score the text for prompt injection, clip it to the source's
``quote_max``, and store it as the factory's next ``S-n``. A signal already
stored under the same ``(source, pointer)`` is not stored again.

No agent route writes here. Sources (the gateway, later) hand raw items to
:func:`ingest`; people add hunches through :func:`add_hunch`; agents may
only set labels (:func:`label`) and cite signal ids. A signal's evidence
never changes afterwards: retention (:func:`expire_due`) and a person
withdrawing their own hunch (:func:`withdraw`) drop the text, and the row,
its links and its counts stay.

The factory's own people are not users: everything from the ``team`` source,
and repo items whose author is on the team (``team_author``), is stored as a
``hunch``, which weighs 0.1 in the evidence meter.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import threading
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory import definition, events
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.definition_blocks import (
    CONSENT,
    KEEP_DAYS,
    PRIVACY_RANK,
    REDACT_FLOOR,
    SOURCE_PRIVACY,
)
from omnigent.server.factory.discovery.redact import redact
from omnigent.stores.factory_store import now_s

if TYPE_CHECKING:
    from omnigent.entities.factory_discovery import FactorySignal
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

TEAM_SOURCE: Final = "team"
"""The source id of the factory's own people when ``discovery.sources`` lists none."""
SERVER_SOURCE: Final = "factory"
"""The source id of signals the server writes itself (readouts, later)."""
SALT_ROW: Final = "~factory"
"""The ``factory_source_state`` row that keeps the factory's account salt (never a source id)."""
SIGNAL_KINDS: Final = (
    "request",
    "pain",
    "praise",
    "bug",
    "churn_reason",
    "behavior",
    "market",
    "test_result",
    "hunch",
    "idea",
    "simulation",
)
LABEL_KINDS: Final = frozenset({"request", "pain", "praise", "bug", "churn_reason", "market"})
"""Kinds a labeller may give a user-text signal (the others are the server's)."""
DO_KINDS: Final = frozenset({"behavior", "test_result"})
CONVICTION_KINDS: Final = frozenset({"hunch", "idea"})
INTENSITIES: Final = ("blocker", "workaround", "annoyance", "nice")
FORCES: Final = ("push", "pull", "anxiety", "habit")
INJECTION_SUSPECT: Final = "injection_suspect"
TEAM_AUTHOR: Final = "team_author"
SUSPECT_AT: Final = 50
"""Injection score (0-100) from which a signal is flagged and left out of scoring."""
BATCH_MAX: Final = 200
HUNCH_MAX: Final = 1000
PARAPHRASE_MAX: Final = 200
_POINTER_MAX: Final = 500
_DAY_S: Final = 86400

_INJECTION_STRONG: Final = (
    re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(instructions?|prompts?|rules|"
        r"directions|guidelines|system message)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(system|developer)\s+(prompt|message|instructions?)\b", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\b|\bact\s+as\s+(an?\s+)?(admin|developer|system)", re.I),
    re.compile(r"<\|im_(start|end)\|>|\[/?INST\]|^\s*#{2,}\s*(system|instruction)", re.I | re.M),
    re.compile(r"^\s*\[(factory|system|needs human)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bsys_(factory|discovery|session)_\w+", re.IGNORECASE),
)
_INJECTION_WEAK: Final = (
    re.compile(
        r"\b(rank|score|rate|prioriti[sz]e|move|put)\b[^.\n]{0,30}\b(first|top|highest|#1)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(email|message|contact|dm|notify|text)\b[^.\n]{0,20}\b(all|every)\b[^.\n]{0,15}"
        r"\b(users?|customers?|people|accounts?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(jailbreak|DAN mode|do anything now)\b", re.IGNORECASE),
    re.compile(r"\b(curl|wget|rm\s+-rf|base64\s+-d)\b", re.IGNORECASE),
    re.compile(r"\b(as an ai|as a language model|new instructions?)\b", re.IGNORECASE),
)

_salt_lock = threading.Lock()


@dataclass(frozen=True)
class SourceRef:
    """
    What the ledger needs to know of a source.

    :param id: The source id (a ``discovery.sources`` slug, ``team`` or ``factory``).
    :param kind: Its kind (``repo``, ``team``, ``web``, … or ``factory``).
    :param state: ``trial``, ``active`` or ``paused`` (a paused source stores nothing).
    """

    id: str
    kind: str
    privacy: str = "public"
    redact: tuple[str, ...] = REDACT_FLOOR
    quote_max: int = 280
    keep_days: int = 365
    consent: str = "public"
    state: str = "active"


@dataclass(frozen=True)
class Item:
    """
    One raw item a source hands the ledger (raw text; never stored as it is).

    :param account: The provider's account id, hashed here; ``None`` for public
        or aggregate items.
    :param kind: A kind the source can tell (``market``, ``bug``, ``behavior``,
        ``test_result``, ``simulation``); hunches and ideas go through
        :func:`add_hunch`.
    :param team_author: The author is on the factory's team (stored as a hunch).
    :param meta: Server-written structure (an aggregate's ``n`` and ``reach``, a
        readout's ``verdict``).
    """

    text: str
    pointer: str | None = None
    observed_at: int | None = None
    account: str | None = None
    segments: Sequence[str] = ()
    kind: str | None = None
    team_author: bool = False
    meta: Mapping[str, Any] | None = None


@dataclass
class Ingested:
    """What one :func:`ingest` call stored."""

    source: SourceRef
    signals: list[FactorySignal] = field(default_factory=list)
    new: list[int] = field(default_factory=list)
    existing: list[int] = field(default_factory=list)
    flagged: list[int] = field(default_factory=list)
    redactions: dict[str, int] = field(default_factory=dict)


# ── Sources ────────────────────────────────────────────────────


def _team_default() -> SourceRef:
    level = SOURCE_PRIVACY["team"]
    return SourceRef(
        id=TEAM_SOURCE,
        kind="team",
        privacy=level,
        keep_days=KEEP_DAYS[level],
        consent=CONSENT[level],
    )


SERVER_REF: Final = SourceRef(
    id=SERVER_SOURCE,
    kind="factory",
    privacy="internal",
    keep_days=KEEP_DAYS["internal"],
    consent="internal",
)


def _ref(found: Mapping[str, Any]) -> SourceRef:
    privacy = found.get("privacy") or {}
    return SourceRef(
        id=str(found["id"]),
        kind=str(found.get("kind") or "web"),
        privacy=str(privacy.get("level") or "public"),
        redact=tuple(privacy.get("redact") or REDACT_FLOOR),
        quote_max=int(privacy.get("quote_max", 280)),
        keep_days=int(privacy.get("keep_days", 365)),
        consent=str(found.get("consent") or "public"),
        state=str(found.get("state") or "trial"),
    )


def sources_of(deps: FactoryDeps, factory_id: str) -> list[SourceRef]:
    """:returns: The factory's ``discovery.sources`` as :class:`SourceRef` rows."""
    found = definition.value(deps, factory_id, "discovery.sources", []) or []
    return [_ref(s) for s in found if isinstance(s, Mapping) and s.get("id")]


def source_ref(deps: FactoryDeps, factory_id: str, source_id: str) -> SourceRef | None:
    """
    :returns: Source *source_id* of the factory; the built-in team source for
        ``team`` when none is listed, and the server's own for ``factory``.
    """
    for ref in sources_of(deps, factory_id):
        if ref.id == source_id:
            return ref
    if source_id == TEAM_SOURCE:
        return _team_default()
    if source_id == SERVER_SOURCE:
        return SERVER_REF
    return None


def team_source(deps: FactoryDeps, factory_id: str) -> SourceRef:
    """:returns: The factory's team source: the listed ``team`` kind, else the built-in one."""
    for ref in sources_of(deps, factory_id):
        if ref.kind == "team":
            return ref
    return _team_default()


# ── Accounts and injection ─────────────────────────────────────


def _salt(deps: FactoryDeps, factory_id: str) -> bytes:
    store = deps.factory_store
    row = store.get_source_state(factory_id, SALT_ROW)
    salt = (row.data or {}).get("salt") if row is not None else None
    if isinstance(salt, str) and salt:
        return bytes.fromhex(salt)
    with _salt_lock:
        row = store.get_source_state(factory_id, SALT_ROW)
        salt = (row.data or {}).get("salt") if row is not None else None
        if not (isinstance(salt, str) and salt):
            salt = os.urandom(16).hex()
            store.put_source_state(factory_id, SALT_ROW, data={"salt": salt})
    return bytes.fromhex(salt)


def account_hash(deps: FactoryDeps, factory_id: str, raw: str | None) -> str | None:
    """
    :returns: ``acct_<24 hex>``: *raw* hashed with the factory's salt (the same
        account always hashes the same within one factory, and never across two).
    """
    flat = " ".join((raw or "").split()).casefold()
    if not flat:
        return None
    digest = hmac.new(_salt(deps, factory_id), flat.encode("utf-8"), hashlib.sha256)
    return f"acct_{digest.hexdigest()[:24]}"


def injection_score(text: str) -> tuple[int, list[str]]:
    """
    How much *text* reads like instructions to an agent rather than a person's words.

    :returns: ``(score 0-100, the patterns that hit)``; :data:`SUSPECT_AT` flags it.
    """
    flat = unicodedata.normalize("NFKC", text)
    hits: list[str] = []
    score = 0
    for pattern in _INJECTION_STRONG:
        if pattern.search(flat):
            hits.append(pattern.pattern[:40])
            score += 50
    for pattern in _INJECTION_WEAK:
        if pattern.search(flat):
            hits.append(pattern.pattern[:40])
            score += 25
    return min(score, 100), hits


def clip_excerpt(text: str, limit: int) -> str | None:
    """:returns: *text* on one paragraph, cut at a word boundary to *limit* chars (``…``)."""
    flat = " ".join(text.split())
    if limit <= 0 or not flat:
        return None
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def excerpt_sha(text: str | None) -> str | None:
    """:returns: sha256 hex of an excerpt as stored."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None


# ── Writing ────────────────────────────────────────────────────


def _segments(deps: FactoryDeps, factory_id: str, wanted: Sequence[str]) -> list[str]:
    known = {
        str(s.get("id"))
        for s in definition.value(deps, factory_id, "product.segments", []) or []
        if isinstance(s, Mapping)
    }
    return [s for s in dict.fromkeys(wanted) if s in known][:10]


def _refuse_source(source: SourceRef) -> None:
    if PRIVACY_RANK.get(source.privacy, 0) >= PRIVACY_RANK["personal"]:
        raise CockpitError(
            422,
            "source_not_allowed",
            f"source {source.id} is {source.privacy}: it stays off until the server can "
            "detect names in it",
        )
    if source.state == "paused":
        raise CockpitError(409, "source_paused", f"source {source.id} is paused")


def _kind(source: SourceRef, item: Item) -> str | None:
    if source.kind == "team" or item.team_author:
        return "hunch"
    if item.kind in SIGNAL_KINDS and item.kind not in CONVICTION_KINDS:
        return item.kind
    return None


def _store_one(
    deps: FactoryDeps,
    factory_id: str,
    source: SourceRef,
    item: Item,
    *,
    query_event: str | None,
    now: int,
    author: str | None = None,
    author_kind: str | None = None,
    kind: str | None = None,
    account: str | None = None,
) -> tuple[FactorySignal, bool, dict[str, int]]:
    cleaned = redact(item.text, source.redact)
    excerpt = clip_excerpt(cleaned.text, source.quote_max)
    score, _hits = injection_score(cleaned.text)
    flags: list[str] = []
    if score >= SUSPECT_AT:
        flags.append(INJECTION_SUSPECT)
    if item.team_author:
        flags.append(TEAM_AUTHOR)
    kind = kind or _kind(source, item)
    pointer = (item.pointer or "").strip()[:_POINTER_MAX] or None
    if pointer is not None:
        pointer = redact(pointer, ("url_secret", "token", "email")).text
    row, existed = deps.factory_store.add_signal(
        factory_id,
        source=source.id,
        source_kind=source.kind,
        kind=kind,
        say_do="do" if kind in DO_KINDS else "say",
        excerpt=excerpt,
        excerpt_sha=excerpt_sha(excerpt),
        pointer=pointer,
        query_event=query_event,
        observed_at=item.observed_at,
        ingested_at=now,
        account=account if account is not None else account_hash(deps, factory_id, item.account),
        segments=_segments(deps, factory_id, item.segments),
        redactions=cleaned.counts,
        privacy=source.privacy,
        expires_at=now + source.keep_days * _DAY_S,
        injection=score,
        flags=flags,
        author=author,
        author_kind=author_kind,
        meta=dict(item.meta) if item.meta else {},
    )
    return row, existed, cleaned.counts


def ingest(
    deps: FactoryDeps,
    factory_id: str,
    source: SourceRef,
    items: Sequence[Item],
    *,
    query_event: str | None = None,
    now: int | None = None,
) -> Ingested:
    """
    Store *items* from *source* as signals (synchronous; run in a thread).

    :param query_event: The ``source.query`` event that fetched them.
    :raises CockpitError: 422 ``source_not_allowed`` (a personal or sensitive
        source), 409 ``source_paused``, 400 ``invalid_body`` (too many items).
    """
    _refuse_source(source)
    if len(items) > BATCH_MAX:
        raise CockpitError(400, "invalid_body", f"at most {BATCH_MAX} items per call")
    when = now if now is not None else now_s()
    out = Ingested(source=source)
    for item in items:
        if not item.text.strip():
            continue
        row, existed, counts = _store_one(
            deps, factory_id, source, item, query_event=query_event, now=when
        )
        out.signals.append(row)
        (out.existing if existed else out.new).append(row.number)
        if not existed:
            if INJECTION_SUSPECT in row.flags:
                out.flagged.append(row.number)
            for kind, n in counts.items():
                out.redactions[kind] = out.redactions.get(kind, 0) + n
    if out.new:
        state = deps.factory_store.get_source_state(factory_id, source.id)
        total = (state.signals_total if state is not None else 0) + len(out.new)
        deps.factory_store.put_source_state(factory_id, source.id, signals_total=total)
    return out


async def announce(app_state: Any, factory_id: str, stored: Ingested) -> None:
    """Emit ``signal.ingested`` (and ``signal.flagged`` per suspect) for what was stored."""
    if not stored.new:
        return
    refs = [f"S-{n}" for n in stored.new]
    await events.emit(
        app_state,
        factory_id,
        "signal.ingested",
        subject=f"source:{stored.source.id}",
        summary=f"{len(refs)} new signal{'s' if len(refs) != 1 else ''} from {stored.source.id}",
        data={
            "source": stored.source.id,
            "signals": refs[:200],
            "existing": [f"S-{n}" for n in stored.existing][:200],
            "flagged": [f"S-{n}" for n in stored.flagged],
            "redactions": stored.redactions,
        },
    )
    for number in stored.flagged:
        await events.emit(
            app_state,
            factory_id,
            "signal.flagged",
            severity="warning",
            subject=f"signal:S-{number}",
            summary=f"S-{number} from {stored.source.id} reads like instructions; "
            "it is kept out of scoring",
            data={"signal": f"S-{number}", "flag": INJECTION_SUSPECT, "source": stored.source.id},
        )


def add_conviction(
    deps: FactoryDeps,
    factory_id: str,
    *,
    text: str,
    author: str,
    author_kind: str,
    kind: str = "hunch",
    pointer: str | None = None,
    now: int | None = None,
) -> tuple[FactorySignal, bool]:
    """
    Store a person's hunch (or an agent's idea) as a team signal (synchronous).

    The author's account is the hash of their id, so a loud teammate counts once.

    :raises CockpitError: 400 ``invalid_body`` (empty or too long), 409 ``source_paused``.
    """
    if kind not in CONVICTION_KINDS:
        raise CockpitError(400, "invalid_body", "a conviction is a hunch or an idea")
    body = " ".join(text.split())
    if not body or len(body) > HUNCH_MAX:
        raise CockpitError(
            400, "invalid_body", f"a hunch is 1-{HUNCH_MAX} characters", field="text"
        )
    source = team_source(deps, factory_id)
    _refuse_source(source)
    when = now if now is not None else now_s()
    row, existed, _counts = _store_one(
        deps,
        factory_id,
        source,
        Item(text=body, pointer=pointer, observed_at=when),
        query_event=None,
        now=when,
        author=author[:128],
        author_kind=author_kind,
        kind=kind,
        account=account_hash(deps, factory_id, f"{author_kind}:{author}"),
    )
    if not existed:
        state = deps.factory_store.get_source_state(factory_id, source.id)
        total = (state.signals_total if state is not None else 0) + 1
        deps.factory_store.put_source_state(factory_id, source.id, signals_total=total)
    return row, existed


def label(
    deps: FactoryDeps,
    factory_id: str,
    number: int,
    *,
    by: str,
    paraphrase: str | None = None,
    kind: str | None = None,
    intensity: str | None = None,
    force: str | None = None,
    flags: Sequence[str] = (),
) -> FactorySignal:
    """
    Set a signal's labels (its evidence stays as stored).

    A labeller may give a user-text signal one of :data:`LABEL_KINDS`; the
    server's kinds (hunch, idea, behaviour, test result, simulation) never change.

    :raises CockpitError: 404 ``unknown_signal``, 400 ``invalid_body``.
    """
    store = deps.factory_store
    row = store.get_signal(factory_id, number)
    if row is None:
        raise CockpitError(404, "unknown_signal", f"no signal S-{number} in this factory")
    labels: dict[str, Any] = {}
    if kind is not None:
        if kind not in LABEL_KINDS:
            raise CockpitError(
                400, "invalid_body", f"a label kind is one of {', '.join(sorted(LABEL_KINDS))}"
            )
        if row.kind is not None and row.kind not in LABEL_KINDS:
            raise CockpitError(400, "invalid_body", f"S-{number} is a {row.kind}; it keeps it")
        labels["kind"] = kind
    if paraphrase is not None:
        flat = " ".join(paraphrase.split())
        if len(flat) > PARAPHRASE_MAX:
            raise CockpitError(400, "invalid_body", f"a paraphrase is ≤ {PARAPHRASE_MAX} chars")
        labels["paraphrase"] = flat or None
    if intensity is not None:
        if intensity not in INTENSITIES:
            raise CockpitError(
                400, "invalid_body", f"intensity is one of {', '.join(INTENSITIES)}"
            )
        labels["intensity"] = intensity
    if force is not None:
        if force not in FORCES:
            raise CockpitError(400, "invalid_body", f"force is one of {', '.join(FORCES)}")
        labels["force"] = force
    extra = [f for f in flags if f == "out_of_scope" or f.startswith("duplicate_of:S-")]
    if extra:
        labels["flags"] = list(dict.fromkeys([*row.flags, *extra]))[:10]
    if not labels:
        return row
    out = store.label_signal(factory_id, number, labelled_by=by, **labels)
    assert out is not None
    return out


def withdraw(deps: FactoryDeps, factory_id: str, number: int, *, by: str) -> FactorySignal:
    """
    Drop a hunch's text at its author's request (the row and its links stay).

    :raises CockpitError: 404 ``unknown_signal``.
    """
    out = deps.factory_store.clear_signal_text(factory_id, number, reason="withdrawn", by=by)
    if out is None:
        raise CockpitError(404, "unknown_signal", f"no signal S-{number} in this factory")
    return out


def expire_due(deps: FactoryDeps, *, now: int | None = None, limit: int = 500) -> int:
    """Drop the text of signals past their retention (every factory). :returns: How many."""
    return deps.factory_store.expire_signal_texts(now if now is not None else now_s(), limit=limit)


__all__ = [
    "BATCH_MAX",
    "CONVICTION_KINDS",
    "DO_KINDS",
    "FORCES",
    "INJECTION_SUSPECT",
    "INTENSITIES",
    "LABEL_KINDS",
    "SALT_ROW",
    "SERVER_REF",
    "SERVER_SOURCE",
    "SIGNAL_KINDS",
    "SUSPECT_AT",
    "TEAM_AUTHOR",
    "TEAM_SOURCE",
    "Ingested",
    "Item",
    "SourceRef",
    "account_hash",
    "add_conviction",
    "announce",
    "clip_excerpt",
    "excerpt_sha",
    "expire_due",
    "ingest",
    "injection_score",
    "label",
    "source_ref",
    "sources_of",
    "team_source",
    "withdraw",
]
