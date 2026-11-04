"""
Short names for the nodes of the cockpit's level tree.

Every node shows a title of two to six words ("Agent assistant", "Debit each
turn"). A person's raw ask, a proposal's machine label ("From C-11: create
… · create …") and a title longer than six words never show as a title: they
stay in the node's details (``full_title``) and the node shows its short name.

Where the short name comes from, first that applies:

1. A name a person gave (the "name it" verb): kept whatever the text says.
2. The factory's name (``factory_node_names``, ``named_by`` empty), made from
   the node's text by the server's cheap title path: the runner bound to the
   factory's root session runs the economy-tier title model
   (:class:`~omnigent.server.background_session_titles.RunnerBackgroundTitleGenerator`),
   with :data:`NAMING_INSTRUCTIONS`. It is made again only when the text it was
   made from changes (``source_hash``).
3. Until then, the first words of the text, trimmed sensibly (:func:`fallback`):
   a page never waits on a name.

:func:`wants_name` decides which titles need a name; :func:`schedule` names
them in the background, a few at a time per factory, never twice for the same
text in one process, and backs off when the runner cannot answer. Each name
stored drops the factory's projection (:func:`.nodes.invalidate`) and tells
the cockpits to refresh.

Nothing here names a product: the words come from the factory's own records.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

WORDS_MAX: Final = 6
"""A title shows at most this many words."""
NAME_CHARS_MAX: Final = 60
"""A short name's length cap (a person's or the factory's)."""
NAMER_ATTR: Final = "factory_node_namer"
"""``app.state`` attribute replacing the runner's title model: ``async (text) -> str | None``."""
NAMED_LEVELS: Final = frozenset({"need", "feature", "project", "task"})
"""Levels the factory asks the title model to name; the others keep their first words."""
PER_PASS: Final = 12
"""Names one projection queues at most (the next projection queues the rest)."""
BACKOFF_S: Final = 600.0
"""After :data:`FAILURES_MAX` unanswered names in a row, a factory waits this long."""
FAILURES_MAX: Final = 3
TIMEOUT_S: Final = 70.0
NAMING_INSTRUCTIONS: Final = (
    "Name the piece of work the message describes, as it would read on one tile of a "
    "dashboard: 2 to 5 words, a noun phrase a person would say (like 'Agent assistant' "
    "or 'Shortlist fixes'). No ids, codes, ticket or PR numbers, slugs, file names, "
    "dates, quotes or trailing punctuation, and do not start with a verb such as "
    "'create', 'add' or 'build'."
)

_WORD_RE: Final = re.compile(r"[0-9A-Za-zÀ-￿]")
_CODE_PREFIX_RE: Final = re.compile(r"^From\s+[A-Z]{1,3}-\d+\s*:?\s*", re.IGNORECASE)
_CODED_LABEL_RE: Final = re.compile(r"^From\s+[A-Z]{1,3}-\d+\b", re.IGNORECASE)
_COMMIT_PREFIX_RE: Final = re.compile(r"^[a-z]+(\([^)]{0,40}\))?!?:\s+")
_MENTION_RE: Final = re.compile(r"^@[\w.\-]+[,:]?\s*")
_FILLERS: Final = (
    "thanks",
    "thank you",
    "hi",
    "hey",
    "hello",
    "ok",
    "okay",
    "so",
    "please",
    "pls",
    "i want to",
    "i want",
    "i would like to",
    "i'd like to",
    "we want to",
    "we need to",
    "i need to",
    "i need",
    "we need",
    "can you",
    "could you",
    "would you",
    "let's",
    "lets",
    "let us",
)
_FILLER_RE: Final = re.compile(
    r"^(?:"
    + "|".join(re.escape(f) for f in sorted(_FILLERS, key=len, reverse=True))
    + r")\b[\s,.!:;-]*",
    re.IGNORECASE,
)
_MACHINE_VERBS_RE: Final = re.compile(
    r"^(?:create|update|add|split|merge|cancel)\s+", re.IGNORECASE
)
_STOP_TAIL: Final = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "for",
        "with",
        "in",
        "on",
        "at",
        "by",
        "from",
        "as",
        "like",
        "that",
        "which",
        "but",
        "so",
        "is",
        "are",
        "be",
    }
)
_SLUG_RE: Final = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)+$")
_SENTENCE_RE: Final = re.compile(r"(?<=[.!?;])\s+|\n+")
_QUOTES: Final = "'\"`“”‘’«»"


# ── Which titles need a name ───────────────────────────────────


def words(text: str) -> list[str]:
    """:returns: The words of *text* (tokens with a letter or digit; ``·`` is no word)."""
    return [w for w in str(text or "").split() if _WORD_RE.search(w)]


def word_count(text: str) -> int:
    """:returns: How many words *text* holds."""
    return len(words(text))


def machine_label(title: str) -> bool:
    """A label the factory made: ``From C-11: …``, or pieces joined with `` · ``."""
    text = str(title or "")
    return bool(_CODED_LABEL_RE.match(text.strip())) or " · " in text


def wants_name(title: str, *, kind: str = "") -> bool:
    """
    Whether *title* may not show as a title: a person's ask, a machine label,
    or more than :data:`WORDS_MAX` words.

    :param kind: The node's kind (``ask`` is always a person's raw text).
    """
    if kind == "ask":
        return True
    if machine_label(title):
        return True
    return word_count(title) > WORDS_MAX


def digest(text: str) -> str:
    """:returns: The ``source_hash`` of *text* (whitespace does not count)."""
    flat = " ".join(str(text or "").split())
    return hashlib.sha256(flat.encode()).hexdigest()[:16]


# ── The first words, trimmed sensibly ──────────────────────────


def _humanize(phrase: str) -> str:
    """``assistant-core`` -> ``assistant core`` (a slug read as words)."""
    return phrase.replace("-", " ").replace("_", " ") if _SLUG_RE.match(phrase) else phrase


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _strip_fillers(text: str) -> str:
    out = text.strip()
    for _ in range(6):
        before = out
        out = _MENTION_RE.sub("", out).strip()
        out = _FILLER_RE.sub("", out).strip()
        if out == before:
            break
    return out


def _clause(text: str) -> str:
    """The first sentence of *text* that says something (two words or more, fillers dropped)."""
    for sentence in _SENTENCE_RE.split(text):
        found = _strip_fillers(sentence.strip(" .!?;"))
        if word_count(found) >= 2:
            return found
    return _strip_fillers(text)


def _cut(text: str, limit: int) -> str:
    """The first *limit* words of *text*, cut before a natural break and never on a stopword."""
    for mark in (" — ", " - ", ": ", " (", ", "):
        head = text.split(mark, 1)[0]
        if mark in text and word_count(head) >= 2:
            text = head
            break
    kept: list[str] = []
    count = 0
    for token in text.split():
        if _WORD_RE.search(token):
            if count == limit:
                break
            count += 1
        kept.append(token)
    while kept and (
        kept[-1].lower().strip(",.;:!?") in _STOP_TAIL or not _WORD_RE.search(kept[-1])
    ):
        kept.pop()
    return " ".join(kept).strip(" ,.;:!?-·/")


def fallback(text: str) -> str:
    """
    A short name from the first words of *text*, for a page that cannot wait.

    ``From C-11: create assistant-core · create panel`` -> ``Assistant core and 1 more``;
    ``@lead thanks. Please start the four fixes …`` -> ``Start the four fixes``.
    """
    first = next((line for line in str(text or "").splitlines() if line.strip()), "").strip()
    first = _CODE_PREFIX_RE.sub("", first).strip()
    pieces = [p.strip() for p in first.split(" · ") if p.strip()]
    listed = [p for p in pieces if _MACHINE_VERBS_RE.match(p)]
    if len(listed) > 1:
        # A machine list ("create a · create b"): its first item and how many more.
        head = _humanize(_MACHINE_VERBS_RE.sub("", listed[0]).strip())
        name = _cut(head, WORDS_MAX - 3)
        if name:
            return f"{_cap(name)} and {len(listed) - 1} more"
    # Otherwise the pieces after the first qualify it (a base, a version): dropped.
    first = pieces[0] if pieces else first
    body = _COMMIT_PREFIX_RE.sub("", first).strip()
    bare = _MACHINE_VERBS_RE.sub("", body).strip()
    if _SLUG_RE.match(bare):
        body = _humanize(bare)
    name = _cut(_clause(body), WORDS_MAX) or _cut(first, WORDS_MAX)
    if not name:
        # Nothing but a code ("From C-12"): it stays as it is.
        return " ".join(str(text or "").split())[:NAME_CHARS_MAX]
    return _cap(name)[:NAME_CHARS_MAX].rstrip()


def question_words(text: str) -> str:
    """
    A question in a few words: the sentence that asks (ends with ``?``), else the
    first one, trimmed like :func:`fallback` (``Start a follow-up project to fix``).
    """
    sentences = [s.strip() for s in _SENTENCE_RE.split(str(text or "")) if s.strip()]
    asking = [s for s in sentences if s.endswith("?")]
    return fallback((asking or sentences or [""])[-1 if asking else 0].rstrip("?"))


def clean(value: str | None) -> str | None:
    """
    A name as a title shows it, or ``None`` when *value* is no usable name:
    its first line without quotes or trailing punctuation, 1 to
    :data:`WORDS_MAX` words, at most :data:`NAME_CHARS_MAX` characters.
    """
    if not value:
        return None
    line = next((ln.strip() for ln in str(value).splitlines() if ln.strip()), "")
    line = " ".join(line.strip(_QUOTES).split()).strip(_QUOTES)
    line = line.rstrip(" .!?;:,").strip()
    if not line or not 1 <= word_count(line) <= WORDS_MAX or len(line) > NAME_CHARS_MAX:
        return None
    return _cap(line)


# ── Naming in the background ───────────────────────────────────


@dataclass(frozen=True)
class Want:
    """
    A node that needs the factory to name it.

    :param ref: The node's ref.
    :param text: What the name is made from (the full text).
    :param hash: :func:`digest` of *text*.
    :param level: The node's level (higher levels are named first).
    """

    ref: str
    text: str
    hash: str
    level: str


@dataclass
class _Queue:
    pending: dict[str, Want] = field(default_factory=dict)
    tried: dict[str, tuple[str, float]] = field(default_factory=dict)
    """Ref -> (the text's hash, when): a text the namer could not name waits :data:`BACKOFF_S`."""
    task: asyncio.Task[None] | None = None
    failures: int = 0
    backoff_until: float = 0.0


_queues: dict[tuple[int, str], _Queue] = {}
Namer = Callable[[str], Awaitable[str | None]]


def reset() -> None:
    """Forget every queue (tests)."""
    for queue in _queues.values():
        if queue.task is not None and not queue.task.done():
            queue.task.cancel()
    _queues.clear()


def schedule(
    app_state: Any, deps: FactoryDeps, root: Conversation, wants: Sequence[Want]
) -> asyncio.Task[None] | None:
    """
    Name *wants* in the background (never raises, never waits).

    A text is asked for again only after :data:`BACKOFF_S` (a stored name is no
    longer wanted); a factory whose namer did not answer :data:`FAILURES_MAX`
    times in a row waits :data:`BACKOFF_S`.

    :returns: The task draining this factory's queue, when one runs.
    """
    if app_state is None or not wants:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    from omnigent.stores.factory_store import norm_id

    fid = norm_id(root.id)
    queue = _queues.setdefault((id(loop), fid), _Queue())
    now = time.monotonic()
    if now < queue.backoff_until:
        return None
    for want in wants[:PER_PASS]:
        tried = queue.tried.get(want.ref)
        if tried is None or tried[0] != want.hash or now - tried[1] >= BACKOFF_S:
            queue.pending.setdefault(want.ref, want)
    if not queue.pending:
        return queue.task if queue.task is not None and not queue.task.done() else None
    if queue.task is None or queue.task.done():
        queue.task = loop.create_task(
            _drain(app_state, deps, root, queue), name=f"factory-node-names-{fid}"
        )
    return queue.task


async def _drain(app_state: Any, deps: FactoryDeps, root: Conversation, queue: _Queue) -> None:
    from omnigent.server.factory import live, nodes

    while queue.pending:
        ref = next(iter(queue.pending))
        want = queue.pending.pop(ref)
        queue.tried[ref] = (want.hash, time.monotonic())
        try:
            name = clean(await generate(app_state, root, want.text))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a name is cosmetic; the first words stay
            _logger.info("factory %s: naming %s failed", root.id, ref, exc_info=True)
            name = None
        if name is None:
            queue.failures += 1
            if queue.failures >= FAILURES_MAX:
                queue.backoff_until = time.monotonic() + BACKOFF_S
                queue.pending.clear()
                queue.failures = 0
                return
            continue
        queue.failures = 0
        try:
            await asyncio.to_thread(
                deps.factory_store.set_node_name, root.id, ref, name, source_hash=want.hash
            )
            nodes.invalidate(root.id)
            live.announce_factory_changed(app_state, root.id, ["names"])
        except Exception:  # noqa: BLE001 - the next projection asks again
            _logger.warning("factory %s: could not store the name of %s", root.id, ref)


async def generate(app_state: Any, root: Conversation, text: str) -> str | None:
    """
    One name for *text*: the namer :data:`NAMER_ATTR` sets, else the economy title
    model the runner bound to the factory's root session runs.

    :returns: The model's answer as it came (see :func:`clean`), or ``None``.
    """
    namer: Namer | None = getattr(app_state, NAMER_ATTR, None)
    if namer is not None:
        return await asyncio.wait_for(namer(text), timeout=TIMEOUT_S)
    router = getattr(app_state, "runner_router", None)
    if router is None:
        return None
    from omnigent.server.background_session_titles import (
        BackgroundTitleRequest,
        RunnerBackgroundTitleGenerator,
    )

    generator = RunnerBackgroundTitleGenerator(router, timeout_seconds=TIMEOUT_S - 5)
    request = BackgroundTitleRequest(
        session_id=root.id,
        prompt=text[:4000],
        agent_id=root.agent_id,
        harness_override=root.harness_override,
        model_override=root.model_override,
        sub_agent_name=root.sub_agent_name,
        additional_instructions=NAMING_INSTRUCTIONS,
    )
    return await asyncio.wait_for(generator(request), timeout=TIMEOUT_S)


__all__ = [
    "BACKOFF_S",
    "NAMED_LEVELS",
    "NAMER_ATTR",
    "NAMING_INSTRUCTIONS",
    "WORDS_MAX",
    "Want",
    "clean",
    "digest",
    "fallback",
    "generate",
    "machine_label",
    "reset",
    "schedule",
    "wants_name",
    "word_count",
    "words",
]
