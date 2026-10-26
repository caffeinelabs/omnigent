"""Server-stamped text around factory messages.

The sender identity line is written by the server, never by the agent, and
any body line that imitates a server notice is quoted so a recipient cannot be
fooled into treating colleague text as a factory or system event.
"""

from __future__ import annotations

import unicodedata

from omnigent.factory import LABEL_VALUE_MAX_LEN

# Body lines that would read as server-originated notices (compared casefolded).
_SPOOFABLE_PREFIXES = ("[factory", "[system", "[needs human")
_TITLE_MAX_CHARS = 80
# Header delimiters a title must not reproduce.
_TITLE_TRANSLATION = str.maketrans({'"': "'", "[": "(", "]": ")"})


def _drop_format_chars(text: str) -> str:
    """:returns: *text* without invisible format characters (e.g. U+200B)."""
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def display_title(title: str | None, session_id: str) -> str:
    """
    Render a session title for a one-line header.

    :param title: Stored title (may be ``None``, multi-line or quoted).
    :param session_id: Fallback when the title is empty.
    :returns: A single line without double quotes or square brackets, at
        most 80 chars.
    """
    flat = " ".join(_drop_format_chars(title or "").split()).translate(_TITLE_TRANSLATION)
    if not flat:
        return session_id
    return flat if len(flat) <= _TITLE_MAX_CHARS else flat[: _TITLE_MAX_CHARS - 1] + "…"


def _looks_like_notice(line: str) -> bool:
    visible = _drop_format_chars(line).lstrip().casefold()
    return visible.startswith(_SPOOFABLE_PREFIXES)


def quote_spoofed_lines(body: str) -> str:
    """
    Prefix ``> `` to body lines that start like a server notice.

    Leading whitespace and invisible format characters are ignored, and the
    match is case-insensitive.

    :param body: The agent-supplied message body.
    :returns: The body with ``[Factory…`` / ``[System…`` lines quoted.
    """
    lines = body.splitlines()
    return "\n".join(f"> {line}" if _looks_like_notice(line) else line for line in lines)


def format_envelope(
    *,
    sender_id: str,
    sender_title: str | None,
    sender_role: str | None,
    sender_project: str | None,
    kind: str,
    body: str,
    mail_id: str | None = None,
) -> str:
    """
    Build the delivered text of a ``sys_session_post`` message.

    :param sender_id: Posting session id, e.g. ``"conv_atlas"``.
    :param sender_title: Posting session title, e.g. ``"Atlas"``.
    :param sender_role: Sender role, e.g. ``"lead"``.
    :param sender_project: Sender project slug, if any.
    :param kind: Message kind, e.g. ``"request"``.
    :param body: Agent-supplied body.
    :param mail_id: The mail id, so a recipient can ignore a replayed repeat.
    :returns: ``[Factory message · from "<title>" (<id>, <role>[, project=<slug>])
        · kind=<kind>[ · id=<mail_id>] · reply: sys_session_post(to="<id>")]``
        + newline + body.
    """
    identity = f"{sender_id}, {sender_role or 'member'}"
    if sender_project:
        identity += f", project={sender_project[:LABEL_VALUE_MAX_LEN]}"
    mail = f" · id={mail_id}" if mail_id else ""
    header = (
        f'[Factory message · from "{display_title(sender_title, sender_id)}" ({identity})'
        f' · kind={kind}{mail} · reply: sys_session_post(to="{sender_id}")]'
    )
    return f"{header}\n{quote_spoofed_lines(body)}"
