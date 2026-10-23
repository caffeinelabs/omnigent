"""Server-stamped text around factory messages.

The sender identity line is written by the server, never by the agent, and
any body line that imitates a server notice is quoted so a recipient cannot be
fooled into treating colleague text as a factory or system event.
"""

from __future__ import annotations

from omnigent.factory import LABEL_VALUE_MAX_LEN

# Body lines that would read as server-originated notices.
_SPOOFABLE_PREFIXES = ("[Factory", "[System", "[NEEDS HUMAN")
_TITLE_MAX_CHARS = 80


def display_title(title: str | None, session_id: str) -> str:
    """
    Render a session title for a one-line header.

    :param title: Stored title (may be ``None``, multi-line or quoted).
    :param session_id: Fallback when the title is empty.
    :returns: A single line without double quotes, at most 80 chars.
    """
    flat = " ".join((title or "").split()).replace('"', "'")
    if not flat:
        return session_id
    return flat if len(flat) <= _TITLE_MAX_CHARS else flat[: _TITLE_MAX_CHARS - 1] + "…"


def quote_spoofed_lines(body: str) -> str:
    """
    Prefix ``> `` to body lines that start like a server notice.

    :param body: The agent-supplied message body.
    :returns: The body with ``[Factory…`` / ``[System…`` lines quoted.
    """
    lines = body.splitlines()
    return "\n".join(
        f"> {line}" if line.lstrip().startswith(_SPOOFABLE_PREFIXES) else line for line in lines
    )


def format_envelope(
    *,
    sender_id: str,
    sender_title: str | None,
    sender_role: str | None,
    sender_project: str | None,
    kind: str,
    body: str,
) -> str:
    """
    Build the delivered text of a ``sys_session_post`` message.

    :param sender_id: Posting session id, e.g. ``"conv_atlas"``.
    :param sender_title: Posting session title, e.g. ``"Atlas"``.
    :param sender_role: Sender role, e.g. ``"lead"``.
    :param sender_project: Sender project slug, if any.
    :param kind: Message kind, e.g. ``"request"``.
    :param body: Agent-supplied body.
    :returns: ``[Factory message · from "<title>" (<id>, <role>[, project=<slug>])
        · kind=<kind> · reply: sys_session_post(to="<id>")]`` + newline + body.
    """
    identity = f"{sender_id}, {sender_role or 'member'}"
    if sender_project:
        identity += f", project={sender_project[:LABEL_VALUE_MAX_LEN]}"
    header = (
        f'[Factory message · from "{display_title(sender_title, sender_id)}" ({identity})'
        f' · kind={kind} · reply: sys_session_post(to="{sender_id}")]'
    )
    return f"{header}\n{quote_spoofed_lines(body)}"
