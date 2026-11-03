"""Redact, then read (Tier 0): the regex stage every signal passes before it is stored.

Personal and secret data becomes a typed placeholder before any person or
model reads the text: emails ``[email]``, phone numbers ``[phone]``, tokens
and keys ``[token]``, IP addresses ``[ip]``, URLs that carry a secret
``[url]`` and card-like numbers ``[card]`` (a Luhn-valid run of 13-19
digits). The six kinds are the floor every source applies
(:data:`~omnigent.server.factory.definition_blocks.REDACT_FLOOR`); names and
addresses need a detector this server does not have yet, so sources that
would carry them (``personal``, ``sensitive``), or that ask for them
(:func:`unsupported`), stay refused.

The stage is code, never configuration: a source can only add kinds.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Final

from omnigent.server.factory.definition_blocks import REDACT_FLOOR, secret_text

PLACEHOLDERS: Final[dict[str, str]] = {
    "email": "[email]",
    "phone": "[phone]",
    "token": "[token]",
    "ip": "[ip]",
    "url_secret": "[url]",
    "card": "[card]",
}

_URL_RE: Final = re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^\s<>\"'`)\]]+", re.IGNORECASE)
_USERINFO_RE: Final = re.compile(r"://[^/@\s?#:]*:[^/@\s?#]+@")
"""A URL with a password in it, the user name possibly empty (``redis://:pass@host``)."""
_EMAIL_RE: Final = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}\b"
)
_TOKEN_RE: Final = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|sk-[A-Za-z0-9_-]{20,}|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}"
    r"|glpat-[A-Za-z0-9_-]{20,}|\bnpm_[A-Za-z0-9]{36}\b|\bhf_[A-Za-z0-9]{30,}\b"
    r"|AIza[0-9A-Za-z_-]{35}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
    r"|\bBearer\s+[A-Za-z0-9._~+/=-]{16,})"
)
_ASSIGNED_SECRET_RE: Final = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?key|secret|token|password|passwd|pwd|auth)"
    r"(\s*[:=]\s*)(\"[^\"]{4,}\"|'[^']{4,}'|[^\s,;]{4,})"
)
_CARD_RE: Final = re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])")
_IPV4_RE: Final = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6_RE: Final = re.compile(
    r"(?<![:\w])(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{1,4}){0,6}(?![:\w])"
)
_PHONE_RE: Final = re.compile(
    r"(?<![\w+])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{2,4}(?:[\s.-]\d{2,5}){1,4}(?![\w])"
    r"|(?<![\w+])\+\d{8,15}(?!\w)"
)
_BARE_PHONE_RE: Final = re.compile(
    r"(?i)(\b(?:phone|tel|telephone|mobile|cell|call|text|sms|whatsapp|fax)\b[^\d\n]{0,20}?)"
    r"(\+?\d{10,15})(?!\d)"
)
"""An unbroken run of 10-15 digits right after a phone word (``call me 4155550134``)."""
_DATE_RE: Final = re.compile(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}$|^\d{1,2}[-./]\d{1,2}[-./]\d{2,4}$")
_MIN_PHONE_DIGITS: Final = 9
_MAX_PHONE_DIGITS: Final = 15


@dataclass(frozen=True)
class Redacted:
    """
    The text after redaction.

    :param counts: Placeholders written per kind, e.g. ``{"email": 2}``.
    """

    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        """:returns: How many placeholders were written."""
        return sum(self.counts.values())


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _card(match: re.Match[str]) -> bool:
    digits = re.sub(r"\D", "", match.group(0))
    return 13 <= len(digits) <= 19 and len(set(digits)) > 1 and _luhn(digits)


def _ip(match: re.Match[str]) -> bool:
    try:
        ipaddress.ip_address(match.group(0))
    except ValueError:
        return False
    return True


def _phone(match: re.Match[str]) -> bool:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    if not _MIN_PHONE_DIGITS <= len(digits) <= _MAX_PHONE_DIGITS:
        return False
    if _DATE_RE.match(raw.strip()):
        return False
    # Versions and IP-like runs are dots only; a phone has a space, dash, plus or bracket.
    return raw.startswith("+") or bool(re.search(r"[\s()-]", raw))


def _url(match: re.Match[str]) -> bool:
    return bool(_USERINFO_RE.search(match.group(0))) or secret_text(match.group(0))


_Stage = tuple[str, re.Pattern[str], Callable[[re.Match[str]], bool] | None]
_STAGES: Final[tuple[_Stage, ...]] = (
    ("email", _EMAIL_RE, None),
    ("token", _TOKEN_RE, None),
    ("card", _CARD_RE, _card),
    ("ip", _IPV4_RE, _ip),
    ("ip", _IPV6_RE, _ip),
    ("phone", _PHONE_RE, _phone),
)


def _assigned(text: str, counts: dict[str, int]) -> str:
    """``password=hunter2`` → ``password=[token]`` (the name stays readable)."""

    def repl(match: re.Match[str]) -> str:
        if match.group(3).startswith("[") and match.group(3).endswith("]"):
            return match.group(0)
        counts["token"] = counts.get("token", 0) + 1
        return f"{match.group(1)}{match.group(2)}{PLACEHOLDERS['token']}"

    return _ASSIGNED_SECRET_RE.sub(repl, text)


def _bare_phones(text: str, counts: dict[str, int]) -> str:
    """``call me 4155550134`` → ``call me [phone]`` (the phone word stays)."""

    def repl(match: re.Match[str]) -> str:
        counts["phone"] = counts.get("phone", 0) + 1
        return f"{match.group(1)}{PLACEHOLDERS['phone']}"

    return _BARE_PHONE_RE.sub(repl, text)


def _stage(
    text: str,
    kind: str,
    pattern: re.Pattern[str],
    accept: Callable[[re.Match[str]], bool] | None,
    counts: dict[str, int],
) -> str:
    def repl(match: re.Match[str]) -> str:
        if accept is not None and not accept(match):
            return match.group(0)
        counts[kind] = counts.get(kind, 0) + 1
        return PLACEHOLDERS[kind]

    return pattern.sub(repl, text)


def unsupported(kinds: Iterable[str]) -> list[str]:
    """:returns: The kinds in *kinds* this stage cannot redact yet (``name``, ``address``)."""
    return sorted({k for k in kinds if k not in PLACEHOLDERS})


def redact(text: str, kinds: Iterable[str] = REDACT_FLOOR) -> Redacted:
    """
    Replace personal and secret data in *text* with typed placeholders.

    Secret-bearing URLs go first, whole, so a ``?token=…`` inside one never
    leaves half a URL behind.

    :param kinds: The kinds to redact; the Tier-0 floor is always applied.
    :returns: The redacted text and the placeholders written per kind.
    """
    wanted = set(kinds) | set(REDACT_FLOOR)
    counts: dict[str, int] = {}
    out = _stage(text, "url_secret", _URL_RE, _url, counts)
    if "token" in wanted:
        out = _assigned(out, counts)
    for kind, pattern, accept in _STAGES:
        if kind in wanted:
            out = _stage(out, kind, pattern, accept, counts)
    if "phone" in wanted:
        out = _bare_phones(out, counts)
    return Redacted(out, counts)


__all__ = ["PLACEHOLDERS", "Redacted", "redact", "unsupported"]
