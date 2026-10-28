"""The managed create that starts a top-level factory member.

``sys_factory_spawn`` (runner) and the server's own PL spawn
(:mod:`omnigent.server.factory.spawn`) send the same ``POST /v1/sessions``
body, so a server-spawned PL is indistinguishable from a lead-spawned one:
a top-level managed session joined to the factory by labels, never a
parent-linked child and never seeded with ``initial_items``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from omnigent.factory import (
    DEFAULT_HEARTBEAT_S,
    LABEL_HEARTBEAT_S,
    LABEL_ID,
    LABEL_PARENT,
    LABEL_PROJECT,
    LABEL_ROLE,
    LABEL_STATE,
    LABEL_SUPERVISOR,
    STATE_ACTIVE,
)


def member_labels(
    *,
    factory_id: str,
    role: str,
    parent: str,
    supervisor: str,
    heartbeat_s: int | None = None,
    project: str | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """
    The factory labels a new top-level member is created with.

    :param factory_id: The factory root session id.
    :param role: ``project_lead`` or ``lead``.
    :param parent: The member (or root) that spawned it.
    :param supervisor: Who receives its alerts.
    :param heartbeat_s: Heartbeat seconds; ``None`` takes the role's default.
    :param project: Project slug of a PL.
    :param extra: More factory labels, e.g. ``def_version`` (never overrides the above).
    :returns: Label key -> value.
    """
    labels: dict[str, str] = {
        LABEL_ID: factory_id,
        LABEL_ROLE: role,
        LABEL_PARENT: parent,
        LABEL_SUPERVISOR: supervisor,
        LABEL_STATE: STATE_ACTIVE,
        LABEL_HEARTBEAT_S: str(
            heartbeat_s if heartbeat_s is not None else DEFAULT_HEARTBEAT_S[role]
        ),
    }
    if project:
        labels[LABEL_PROJECT] = project
    for key, value in (extra or {}).items():
        labels.setdefault(key, value)
    return labels


def member_create_body(
    *,
    agent_id: str,
    title: str,
    labels: Mapping[str, str],
    workspaces: Sequence[str] = (),
) -> dict[str, Any]:
    """
    The JSON ``POST /v1/sessions`` body of a member spawn.

    :param agent_id: The built-in agent's id, e.g. the ``factory-pl`` row.
    :param title: e.g. ``"PL · api-version"``.
    :param labels: From :func:`member_labels`.
    :param workspaces: Repository URLs, e.g. ``["https://github.com/org/repo#main"]``.
    :returns: The body.
    """
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "host_type": "managed",
        "title": title,
        "labels": dict(labels),
    }
    if workspaces:
        body["workspaces"] = list(workspaces)
    return body


__all__ = ["member_create_body", "member_labels"]
