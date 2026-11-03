"""Quotes are real (Tier 0): quoted words in agent text must match a stored excerpt.

Any field an agent writes (an opportunity's statement, a bet's hypothesis,
a decline's reason) may quote what people said only by citing the signals
(``S-n``) that hold those words. A quote matches when, after normalisation
(case, spacing, quote marks, dashes and edge punctuation), it appears in the
stored, redacted excerpt of one of the cited signals; an ellipsis splits a
quote into parts that must appear in order. Anything else is refused and
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
_MIN_PART: Final = 3
"""A part of an elided quote shorter than this (characters) is not checked on its own."""


def normalize(text: str) -> str:
    """:returns: *text* as quotes are compared: NFKC, casefolded, one space, plain marks."""
    flat = unicodedata.normalize("NFKC", text).translate(_TRANSLATE).casefold()
    return " ".join(flat.split()).strip(_PUNCT_EDGE)


def _parts(quote: str) -> list[str]:
    parts = [normalize(p) for p in _ELLIPSIS_RE.split(quote)]
    return [p for p in parts if len(p) >= _MIN_PART]


def matches(quote: str, excerpt: str) -> bool:
    """:returns: Whether *quote* (parts in order, around any ellipsis) is in *excerpt*."""
    haystack = normalize(excerpt)
    parts = _parts(quote)
    if not parts:
        return False
    at = 0
    for part in parts:
        found = haystack.find(part, at)
        if found < 0:
            return False
        at = found + len(part)
    return True


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


__all__ = ["check", "matches", "normalize", "unmatched"]
