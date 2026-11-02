"""Who an agent is, in short: its stable handle, its vendor and its model.

- **Handles** (``omnigent.factory.handle``): one short lowercase name per
  member and sub-agent, unique in its factory and never changed once stored.
  The factory root is ``atlas``, its other lead ``sentinel``, a discovery lead
  ``scout``; everyone else draws a name from a neutral pool, starting at a
  place hashed from its session id. A full pool falls back to the role word
  and a number (``builder-2``).
- **Vendor** is the harness family that runs the agent (``claude``, ``codex``,
  …), read from the harness first and the model name second.
- **Model labels** are short: ``claude-sonnet-4-5-20250929`` and the bundle
  alias ``sonnet`` both read ``sonnet``; other ids keep their name.

Everything here is pure except :func:`ensure_handles`, which writes labels.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    LABEL_HANDLE,
    ROLE_DEMO,
    ROLE_ENV_KEEPER,
    ROLE_IC,
    ROLE_LEAD,
    ROLE_PLANNER,
    ROLE_PROJECT_LEAD,
    ROLE_REVIEWER,
)

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.stores import ConversationStore

_logger = logging.getLogger(__name__)

HANDLE_RE: Final = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
ROOT_HANDLE: Final = "atlas"
PEER_LEAD_HANDLE: Final = "sentinel"
SCOUT_ROLE: Final = "scout"
SCOUT_HANDLE: Final = "scout"
RESERVED: Final = frozenset({ROOT_HANDLE, PEER_LEAD_HANDLE, SCOUT_HANDLE})

POOL: Final[tuple[str, ...]] = (
    "ada", "alba", "ash", "bea", "bo", "cal", "cleo", "cy", "dot", "eli",
    "ember", "eve", "fay", "fen", "fern", "fig", "gil", "gus", "hal", "hazel",
    "ida", "ike", "iris", "ivy", "jade", "jo", "juno", "kai", "kip", "lark",
    "lea", "lou", "luz", "mae", "mira", "nell", "nico", "nova", "oak", "ola",
    "oro", "otis", "pax", "pip", "quill", "rae", "remy", "rex", "rio", "rue",
    "sage", "sol", "tam", "tess", "theo", "uma", "una", "val", "vera", "wren",
    "wyn", "yara", "yuki", "zane", "zia", "zoe",
)  # fmt: skip
"""Neutral short names handed out in hashed order (no product, no person's surname)."""

ROLE_WORDS: Final[dict[str, str]] = {
    ROLE_LEAD: "lead",
    ROLE_PROJECT_LEAD: "lead",
    ROLE_IC: "builder",
    ROLE_REVIEWER: "reviewer",
    ROLE_DEMO: "demo",
    ROLE_ENV_KEEPER: "env",
    ROLE_PLANNER: "planner",
    SCOUT_ROLE: "scout",
}
"""The fallback handle's stem per role (``builder-2``)."""

_VENDOR_HINTS: Final = (
    ("claude", "claude"),
    ("codex", "codex"),
    ("cursor", "cursor"),
    ("copilot", "copilot"),
    ("antigravity", "gemini"),
    ("gemini", "gemini"),
    ("opencode", "opencode"),
)
_CLAUDE_FAMILIES: Final = ("opus", "sonnet", "haiku")
_DATE_SUFFIX: Final = re.compile(r"[-@]\d{8}$")
_CONTEXT_SUFFIX: Final = re.compile(r"\[[^\]]*\]$")


# ── Vendor and model ───────────────────────────────────────────


def vendor_of(harness: str | None, model: str | None = None) -> str | None:
    """
    The vendor that runs an agent.

    :param harness: e.g. ``"claude-native"`` or ``"codex-native"``.
    :param model: e.g. ``"gpt-5-codex"``; read when the harness says nothing.
    :returns: e.g. ``"claude"`` or ``"codex"``; ``None`` when neither tells.
    """
    for text in (harness, model):
        lowered = (text or "").lower()
        for needle, vendor in _VENDOR_HINTS:
            if needle in lowered:
                return vendor
    name = _bare_model(model)
    if name.startswith(_CLAUDE_FAMILIES):
        return "claude"
    if name.startswith("gpt") or re.match(r"^o\d", name):
        return "codex"
    return None


def _bare_model(model: str | None) -> str:
    text = (model or "").strip().lower()
    text = text.rsplit("/", 1)[-1]
    text = _CONTEXT_SUFFIX.sub("", text)
    return _DATE_SUFFIX.sub("", text)


def model_label(model: str | None) -> str | None:
    """
    A short model name: the family for Claude models, the bare id otherwise.

    Claude keeps only its family so a bundle alias (``"sonnet"``) and the id a
    session reports (``"claude-sonnet-4-5-20250929"``) group together.

    :param model: e.g. ``"anthropic/claude-sonnet-4-5-20250929"``, ``"opus"``
        or ``"gpt-5-codex"``.
    :returns: e.g. ``"sonnet"``, ``"opus"`` or ``"gpt-5-codex"``; ``None`` for no model.
    """
    name = _bare_model(model)
    if not name:
        return None
    family = next((p for p in name.split("-") if p in _CLAUDE_FAMILIES), None)
    return family or name


def harness_of(conv: Conversation, spec: Any = None) -> str | None:
    """:returns: A session's harness: its override, else its spec's, e.g. ``"claude-native"``."""
    if conv.harness_override:
        return conv.harness_override
    config = getattr(getattr(spec, "executor", None), "config", None)
    harness = config.get("harness") if isinstance(config, dict) else None
    return harness if isinstance(harness, str) else None


def session_model(conv: Conversation, spec: Any = None) -> str | None:
    """
    The model a session runs: its override, else what it reported, else its spec's.

    :param conv: The session row.
    :param spec: Its loaded :class:`~omnigent.spec.types.AgentSpec`, if any.
    """
    llm = getattr(spec, "llm", None)
    spec_model = getattr(llm, "model", None)
    for value in (conv.model_override, conv.reported_model, spec_model):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def vendor_model(deps: FactoryDeps, conv: Conversation) -> tuple[str | None, str | None]:
    """
    ``(vendor, model)`` of a session, from its harness, model override, report or spec.

    Synchronous (loads the session's spec through the agent cache); never raises.
    """
    from omnigent.server.factory.membership import load_session_spec

    try:
        spec = load_session_spec(conv, deps)
    except Exception:  # noqa: BLE001 - an unknown model only thins the bench
        spec = None
    model = session_model(conv, spec)
    return vendor_of(harness_of(conv, spec), model), model


# ── Handles ────────────────────────────────────────────────────


def is_handle(value: str | None) -> bool:
    """:returns: Whether *value* is shaped like a handle (``"dot"``, ``"builder-2"``)."""
    return bool(value) and HANDLE_RE.fullmatch(value or "") is not None


def handle_name(handle: str | None) -> str | None:
    """:returns: A display name: ``"dot"`` → ``"Dot"``, ``"builder-2"`` → ``"Builder 2"``."""
    if not handle:
        return None
    return handle.replace("-", " ").capitalize()


@dataclass(frozen=True)
class HandleSubject:
    """
    One session that needs a handle.

    :param session_id: Its id.
    :param role: Its factory role (``lead``, ``project_lead``, ``ic``, …).
    :param created_at: Epoch seconds (older sessions pick first).
    :param current: Its stored handle label, if any.
    :param is_root: The factory root (``atlas``).
    """

    session_id: str
    role: str | None
    created_at: float
    current: str | None = None
    is_root: bool = False


def _start(session_id: str) -> int:
    digest = hashlib.sha256(session_id.replace("-", "").lower().encode()).digest()
    return int.from_bytes(digest[:4], "big") % len(POOL)


def _fallback(role: str | None, taken: set[str]) -> str:
    stem = ROLE_WORDS.get(role or "", "agent")
    n = 2
    while f"{stem}-{n}" in taken:
        n += 1
    return f"{stem}-{n}"


def _pick(subject: HandleSubject, taken: set[str], peer_lead_free: bool) -> str:
    if subject.is_root and ROOT_HANDLE not in taken:
        return ROOT_HANDLE
    if subject.role == SCOUT_ROLE and SCOUT_HANDLE not in taken:
        return SCOUT_HANDLE
    if subject.role == ROLE_LEAD and not subject.is_root and peer_lead_free:
        return PEER_LEAD_HANDLE
    start = _start(subject.session_id)
    for step in range(len(POOL)):
        name = POOL[(start + step) % len(POOL)]
        if name not in taken:
            return name
    return _fallback(subject.role, taken)


def assign_handles(subjects: Iterable[HandleSubject]) -> dict[str, str]:
    """
    Handles for every subject: kept when stored and unique, else picked.

    Deterministic for one input: stored handles keep their owner (the oldest
    holder of a duplicate keeps it), then the rest pick oldest first.

    :param subjects: Every session of one factory that may hold a handle.
    :returns: Session id → handle, for every subject.
    """
    ordered = sorted(subjects, key=lambda s: (s.created_at, s.session_id))
    taken: set[str] = set()
    out: dict[str, str] = {}
    for subject in ordered:
        current = subject.current
        if is_handle(current) and current not in taken:
            taken.add(current)  # type: ignore[arg-type]
            out[subject.session_id] = current  # type: ignore[assignment]
    peer_lead_free = PEER_LEAD_HANDLE not in taken
    for subject in ordered:
        if subject.session_id in out:
            continue
        handle = _pick(subject, taken, peer_lead_free)
        if handle == PEER_LEAD_HANDLE:
            peer_lead_free = False
        taken.add(handle)
        out[subject.session_id] = handle
    return out


def ensure_handles(
    store: ConversationStore,
    factory_id: str,
    sessions: Iterable[tuple[Conversation, str | None]],
) -> dict[str, str]:
    """
    Give every session of a factory its handle, storing the new ones (synchronous).

    A failed label write keeps the handle for this read only; the next read
    picks the same one again.

    :param store: Store for the label writes.
    :param factory_id: The factory root session id.
    :param sessions: ``(session row, factory role)`` of every member and
        sub-agent the caller loaded (closed members included, so their
        handles are not handed out again).
    :returns: Session id → handle.
    """
    rows = list(sessions)
    subjects = [
        HandleSubject(
            session_id=conv.id,
            role=role,
            created_at=float(conv.created_at or 0),
            current=conv.labels.get(LABEL_HANDLE),
            is_root=conv.id == factory_id,
        )
        for conv, role in rows
    ]
    handles = assign_handles(subjects)
    for conv, _role in rows:
        handle = handles.get(conv.id)
        if handle is None or conv.labels.get(LABEL_HANDLE) == handle:
            continue
        try:
            store.set_labels(conv.id, {LABEL_HANDLE: handle})
        except Exception:  # noqa: BLE001 - a missing label only costs a recompute
            _logger.warning("factory: could not store handle %s on %s", handle, conv.id)
    return handles


__all__ = [
    "HANDLE_RE",
    "PEER_LEAD_HANDLE",
    "POOL",
    "ROOT_HANDLE",
    "HandleSubject",
    "assign_handles",
    "ensure_handles",
    "handle_name",
    "harness_of",
    "is_handle",
    "model_label",
    "session_model",
    "vendor_model",
    "vendor_of",
]
