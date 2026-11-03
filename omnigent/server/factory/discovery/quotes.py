"""Quotes are real (Tier 0): quoted words in agent text must match a stored excerpt.

Any field an agent writes (an opportunity's statement, a bet's hypothesis,
a decline's reason) may quote what people said only by citing the signals
(``S-n``) that hold those words. A quote matches when, after normalisation
(case, spacing, quote marks, dashes and punctuation), its words appear as a
run of whole words in the stored, redacted excerpt of one of the cited
signals, so "happy" never matches "unhappy" nor "ever" "never".

An ellipsis splits a quote into at most three parts of at least three words
each, which must appear in that order, and the words it leaves out (or the
two words just before the quote) may not hold a negation: a quote edited
until it means something else is refused. Anything else is refused and
logged: :func:`check` raises ``quote_unverified`` (the caller emits the
refusal), and :func:`unmatched` is the hook :mod:`.bets` verifies bets with.

The cockpit picks the quotes it shows from the ledger, never from agent text.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from omnigent.server.factory import bets
from omnigent.server.factory.control import CockpitError

if TYPE_CHECKING:
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

_ELLIPSIS_RE: Final = re.compile(r"\s*(?:…|\.\.\.+|\[…\]|\[\.\.\.\])\s*")
_PUNCT_EDGE: Final = " \t\n\"'“”‘’«»„.,;:!?()[]"
_TRANSLATE: Final = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "ʼ": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "«": '"',
        "»": '"',
        "–": "-",
        "—": "-",
        "‐": "-",
        " ": " ",
    }
)
_WORD_RE: Final = re.compile(r"\w+(?:'\w+)*")
MIN_PART_WORDS: Final = 3
"""Each part of an elided quote is at least this many words."""
MAX_ELLIPSES: Final = 2
"""An elided quote leaves words out in at most this many places."""
_LOOKBACK: Final = 2
"""Words before a quote's start that may not negate it."""
_NEGATIONS: Final = frozenset(
    {
        "not",
        "no",
        "never",
        "nor",
        "neither",
        "none",
        "nothing",
        "nobody",
        "nowhere",
        "without",
        "cannot",
        "dont",
        "doesnt",
        "didnt",
        "cant",
        "wont",
        "isnt",
        "arent",
        "wasnt",
        "werent",
        "couldnt",
        "wouldnt",
        "shouldnt",
        "havent",
        "hasnt",
        "hadnt",
    }
)


def normalize(text: str) -> str:
    """:returns: *text* as quotes are compared: NFKC, casefolded, one space, plain marks."""
    flat = unicodedata.normalize("NFKC", text).translate(_TRANSLATE).casefold()
    return " ".join(flat.split()).strip(_PUNCT_EDGE)


def words(text: str) -> list[str]:
    """:returns: The words of *text* as quotes compare them (``don't`` is one word)."""
    return _WORD_RE.findall(normalize(text))


def _negates(run: Sequence[str]) -> bool:
    return any(w in _NEGATIONS or w.endswith("n't") for w in run)


def _parts(quote: str) -> list[list[str]] | None:
    """:returns: The word runs of *quote* around its ellipses; ``None`` when it is refused."""
    parts = [words(p) for p in _ELLIPSIS_RE.split(quote)]
    # A leading or trailing ellipsis only says the quote is cut there.
    while parts and not parts[0]:
        parts.pop(0)
    while parts and not parts[-1]:
        parts.pop()
    if not parts or len(parts) > MAX_ELLIPSES + 1:
        return None
    if len(parts) > 1 and any(len(p) < MIN_PART_WORDS for p in parts):
        return None
    return parts


def _find(haystack: Sequence[str], run: Sequence[str], start: int) -> int:
    width = len(run)
    for i in range(start, len(haystack) - width + 1):
        if haystack[i : i + width] == run:
            return i
    return -1


def _place(haystack: Sequence[str], parts: Sequence[Sequence[str]], at: int, first: bool) -> bool:
    if not parts:
        return True
    run = parts[0]
    found = _find(haystack, run, at)
    while found >= 0:
        skipped = haystack[max(0, found - _LOOKBACK) : found] if first else haystack[at:found]
        if not _negates(skipped) and _place(haystack, parts[1:], found + len(run), False):
            return True
        found = _find(haystack, run, found + 1)
    return False


def matches(quote: str, excerpt: str) -> bool:
    """
    :returns: Whether *quote* is in *excerpt*: its words as whole-word runs, in
        order around any ellipsis, with no negation left out before or between them.
    """
    parts = _parts(quote)
    if parts is None:
        return False
    return _place(words(excerpt), parts, 0, True)


def _numbers(refs: Sequence[str]) -> list[int]:
    out: list[int] = []
    for ref in refs:
        head, _, tail = ref.partition("-")
        if head == "S" and tail.isdigit():
            out.append(int(tail))
    return out


def unmatched(
    deps: FactoryDeps, factory_id: str, quotes: Sequence[str], cited: Sequence[str]
) -> list[str]:
    """
    The quotes no stored excerpt of a cited signal holds (the :mod:`.bets` quote hook).

    A signal whose text expired or was withdrawn holds no words any more.

    :param cited: Signal ids the text cites, e.g. ``["S-12", "S-40"]``.
    """
    excerpts: list[str] = []
    for number in dict.fromkeys(_numbers(cited)):
        row = deps.factory_store.get_signal(factory_id, number)
        if row is not None and row.excerpt and not row.gone:
            excerpts.append(row.excerpt)
    return [q for q in quotes if not any(matches(q, e) for e in excerpts)]


def check(
    deps: FactoryDeps,
    factory_id: str,
    texts: Sequence[tuple[str, str | None]],
    *,
    cited: Sequence[str] = (),
) -> None:
    """
    Refuse agent text whose quotes no cited signal holds (synchronous).

    :param texts: ``(field path, text)`` pairs an agent wrote.
    :param cited: Signal ids the write cites besides those named in the texts.
    :raises CockpitError: 422 ``quote_unverified`` with the refused ``quotes``.
    """
    quotes = [q for _, text in texts for q in bets.find_quotes(text)]
    if not quotes:
        return
    named = {ref for _, text in texts for ref in bets.SIGNAL_REF_RE.findall(text or "")}
    cited = sorted(named | set(cited))
    refused = unmatched(deps, factory_id, quotes, cited)
    if not refused:
        return
    _logger.warning(
        "factory %s: refused %d unverified quote(s) citing %s", factory_id, len(refused), cited
    )
    raise CockpitError(
        422,
        "quote_unverified",
        "quoted words must match a stored excerpt of a cited signal (S-n); cite the "
        "signal and paraphrase instead",
        field=next(where for where, text in texts if bets.find_quotes(text)),
        quotes=[q[:120] for q in refused[:5]],
    )


__all__ = ["MAX_ELLIPSES", "MIN_PART_WORDS", "check", "matches", "normalize", "unmatched", "words"]
