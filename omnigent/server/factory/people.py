"""The factory roster: people, their roles, and the session grants roles imply.

Each roster role is copied as an ordinary session grant onto every top-level
member (the root lead, Sentinel, every PL, PLs spawned later); sub-agents
inherit through their PL, so the permission resolver does not change.

- :func:`set_person` puts a person on the roster (or changes their role) and
  grants the role's level on every member.
- :func:`remove_person` takes them off and revokes those grants.
- :func:`mirror_member` applies the whole roster to one session (a new member).
- :func:`repair_drift` diffs the grants the roster wants against the stored
  ones and repairs them (the reconcile loop runs it every minute).

None of them ever touches an owner grant, or the grants of a user who is not
on the roster, so the creator's ownership and manual shares survive. The
creator is on the roster as an ``owner`` flagged ``is_creator``.

Only sessions the creator owns carry roster grants: anyone can label a session
with a factory id, but the lead's runner and the server spawn run as the
creator. Writes for one factory are serialized (:func:`_roster_lock`), so a
drift repair never re-grants someone a concurrent removal just revoked.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import LABEL_ID, LABEL_NAME, PERSON_ROLES
from omnigent.server.auth import (
    LEVEL_EDIT,
    LEVEL_MANAGE,
    LEVEL_OWNER,
    LEVEL_READ,
    RESERVED_USER_LOCAL,
    RESERVED_USER_PUBLIC,
    SharingMode,
)
from omnigent.server.factory import events, live
from omnigent.server.factory import presence as factory_presence
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.membership import factory_members, is_top_level
from omnigent.server.routes._sharing import sharing_mode, sharing_refusal
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryPerson
    from omnigent.server.factory.routes import FactoryRouteError

_logger = logging.getLogger(__name__)

ROLE_VIEWER: Final = "viewer"
ROLE_OPERATOR: Final = "operator"
ROLE_OWNER: Final = "owner"
ROLE_CREATOR: Final = "creator"
ROLE_LEVELS: Final[dict[str, int]] = {
    ROLE_VIEWER: LEVEL_READ,
    ROLE_OPERATOR: LEVEL_EDIT,
    ROLE_OWNER: LEVEL_MANAGE,
}
"""Session grant level each roster role is copied as."""
RESERVED_USERS: Final = frozenset({RESERVED_USER_LOCAL, RESERVED_USER_PUBLIC})
SCOPES: Final = ("snapshot", "people")
"""Frame scopes a roster change announces."""


@dataclass(frozen=True)
class PersonChange:
    """
    What :func:`set_person` or :func:`remove_person` did.

    :param person: The roster row afterwards (``None`` once removed).
    :param previous_role: The role before the change, ``None`` for a new person.
    :param granted: Sessions whose grant was written.
    :param revoked: Sessions whose grant was removed.
    """

    person: FactoryPerson | None
    previous_role: str | None
    granted: list[str] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)

    @property
    def created(self) -> bool:
        """:returns: Whether the person was not on the roster before."""
        return self.person is not None and self.previous_role is None


# ── Roles and levels ───────────────────────────────────────────


def level_role(level: int | None, *, is_creator: bool = False) -> str | None:
    """
    The factory role a session level on the root amounts to.

    :param level: The user's level on the root (``None`` = no access).
    :param is_creator: The user started the factory.
    :returns: ``creator`` / ``owner`` / ``operator`` / ``viewer``, or ``None``.
    """
    if level is None or level < LEVEL_READ:
        return None
    if is_creator:
        return ROLE_CREATOR
    if level >= LEVEL_MANAGE:
        return ROLE_OWNER
    return ROLE_OPERATOR if level >= LEVEL_EDIT else ROLE_VIEWER


def person_role(person: FactoryPerson) -> str:
    """:returns: The role a roster row shows: ``creator`` for the creator."""
    return ROLE_CREATOR if person.is_creator else person.role


def creator_of(deps: FactoryDeps, factory_id: str) -> str | None:
    """:returns: The user holding the owner grant on the root, or ``None``."""
    from omnigent.server.routes._auth_helpers import get_session_owner_id

    return get_session_owner_id(factory_id, deps.permission_store)


def _same_id(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    try:
        return norm_id(a) == norm_id(b)
    except ValueError:
        return a == b


_ROSTER_LOCKS: dict[str, threading.Lock] = {}
_ROSTER_LOCKS_GUARD = threading.Lock()


def _roster_lock(factory_id: str) -> threading.Lock:
    """The (per process) lock serializing one factory's roster and grant writes."""
    try:
        key = norm_id(factory_id)
    except ValueError:
        key = factory_id
    with _ROSTER_LOCKS_GUARD:
        return _ROSTER_LOCKS.setdefault(key, threading.Lock())


def _creator_owned(deps: FactoryDeps, factory_id: str, session_ids: Sequence[str]) -> list[str]:
    """
    Keep the root and the sessions the factory's creator owns, in order.

    A session labelled with the factory id by anyone else is not a member
    whose grants the roster manages.
    """
    ids = list(dict.fromkeys(session_ids))
    if deps.permission_store is None:
        return ids
    current = _grant_levels(deps, [factory_id, *ids])
    creator = next(
        (u for u, level in current[factory_id].items() if level >= LEVEL_OWNER),
        None,
    )
    return [
        sid
        for sid in ids
        if _same_id(sid, factory_id)
        or (creator is not None and current[sid].get(creator, 0) >= LEVEL_OWNER)
    ]


def managed_sessions(deps: FactoryDeps, factory_id: str) -> list[str]:
    """
    The sessions roster grants live on: the root and every live top-level
    member the creator owns.

    :returns: Session ids, root first.
    """
    ids = [factory_id]
    for member in factory_members(factory_id, deps.conversation_store):
        if member.id not in ids:
            ids.append(member.id)
    return _creator_owned(deps, factory_id, ids)


def _person_sessions(deps: FactoryDeps, factory_id: str, user_id: str) -> list[str]:
    """
    The managed sessions plus archived members where *user_id* still holds a grant.

    :returns: Session ids, root first.
    """
    perms = deps.permission_store
    ids = managed_sessions(deps, factory_id)
    if perms is None:
        return ids
    held = [g.conversation_id for g in perms.list_for_user(user_id, limit=10_000)]
    rows = deps.conversation_store.get_conversations([sid for sid in held if sid not in ids])
    archived = [
        sid
        for sid, conv in rows.items()
        if is_top_level(conv) and _same_id(conv.labels.get(LABEL_ID), factory_id)
    ]
    return ids + _creator_owned(deps, factory_id, sorted(archived))


def ensure_creator(deps: FactoryDeps, factory_id: str) -> FactoryPerson | None:
    """
    Put the root's owner on the roster as the creator, once.

    :returns: The creator's row, or ``None`` when the root has no owner.
    """
    store = deps.factory_store
    creator = creator_of(deps, factory_id)
    if creator is None or creator in RESERVED_USERS:
        return None
    row = store.get_person(factory_id, creator)
    if row is not None:
        return row
    row, _ = store.upsert_person(
        factory_id, creator, role=ROLE_OWNER, invited_by=creator, is_creator=True
    )
    return row


def list_people(deps: FactoryDeps, factory_id: str) -> list[FactoryPerson]:
    """:returns: The roster, creator included (oldest invite first)."""
    if deps.permission_store is not None:
        ensure_creator(deps, factory_id)
    return deps.factory_store.list_people(factory_id)


def people_count(deps: FactoryDeps, factory_id: str) -> int:
    """:returns: How many humans are on the roster (the creator always counts)."""
    rows = deps.factory_store.list_people(factory_id)
    return len(rows) + (0 if any(r.is_creator for r in rows) else 1)


def viewer_level(deps: FactoryDeps, factory_id: str, user_id: str | None) -> int:
    """:returns: *user_id*'s level on the root; owner when auth is off."""
    if deps.permission_store is None or user_id is None:
        return LEVEL_OWNER
    return deps.permission_store.get_permission_level(user_id, factory_id) or 0


def role_of(deps: FactoryDeps, factory_id: str, user_id: str | None) -> str | None:
    """:returns: *user_id*'s factory role (by their level on the root), or ``None``."""
    level = viewer_level(deps, factory_id, user_id)
    is_creator = user_id is None or (
        deps.permission_store is not None and creator_of(deps, factory_id) == user_id
    )
    return level_role(level, is_creator=is_creator)


def human_label(deps: FactoryDeps, factory_id: str, user_id: str | None) -> str:
    """
    How server-stamped texts name a human: ``"<name> (<role>)"``.

    A factory with one person keeps the bare name; the role only tells
    people apart once there are several.
    """
    from omnigent.server.factory.control import human_name

    if user_id is None or user_id == RESERVED_USER_LOCAL:
        return human_name(user_id)
    try:
        if people_count(deps, factory_id) < 2:
            return human_name(user_id)
        return human_name(user_id, role_of(deps, factory_id, user_id))
    except Exception:  # noqa: BLE001 - a name without its role still attributes the act
        _logger.warning("factory: could not resolve the role of %s", user_id, exc_info=True)
        return human_name(user_id)


VIEWER_CAN_LEVELS: Final[dict[str, int]] = {
    "post": LEVEL_EDIT,
    "answer": LEVEL_EDIT,
    "review": LEVEL_EDIT,
    "control": LEVEL_EDIT,
    "intervene": LEVEL_EDIT,
    "apply_projects": LEVEL_EDIT,
    "apply_tuning": LEVEL_MANAGE,
    "restart": LEVEL_MANAGE,
    "stop": LEVEL_MANAGE,
    "invite": LEVEL_MANAGE,
    "settings": LEVEL_MANAGE,
    "terminal": LEVEL_OWNER,
    "start": LEVEL_OWNER,
}
"""The level on the root each cockpit lever needs."""


def viewer_block(deps: FactoryDeps, factory_id: str, user_id: str | None) -> dict[str, Any]:
    """
    The snapshot's ``viewer`` block for *user_id* (never cached).

    :returns: ``ViewerBlock`` JSON: role, level, creator flag and ``can.*``.
    """
    level = viewer_level(deps, factory_id, user_id)
    is_creator = user_id is None or (
        deps.permission_store is not None and creator_of(deps, factory_id) == user_id
    )
    role = level_role(level, is_creator=is_creator) or ROLE_VIEWER
    return {
        "user_id": user_id,
        "role": role,
        "level": max(0, min(level, LEVEL_OWNER)),
        "is_creator": is_creator,
        "can": {lever: level >= need for lever, need in VIEWER_CAN_LEVELS.items()},
    }


# ── Grants ─────────────────────────────────────────────────────


def _grant_levels(deps: FactoryDeps, session_ids: Sequence[str]) -> dict[str, dict[str, int]]:
    """:returns: Session id -> user -> stored grant level."""
    assert deps.permission_store is not None
    rows = deps.permission_store.list_for_sessions(list(session_ids))
    return {sid: {g.user_id: g.level for g in rows.get(sid, [])} for sid in session_ids}


def _workspaces(
    deps: FactoryDeps, session_ids: Sequence[str], mode: SharingMode
) -> dict[str, str | None]:
    """:returns: Session id -> working directory, read only when *mode* checks it."""
    if mode != SharingMode.RESTRICTED_READ_ONLY:
        return {}
    rows = deps.conversation_store.get_conversations(list(session_ids))
    return {sid: conv.workspace for sid, conv in rows.items()}


def grant_caps(
    deps: FactoryDeps, session_ids: Sequence[str], mode: SharingMode | None
) -> dict[str, int | None]:
    """
    The highest new grant the sharing mode allows on each session.

    :param mode: The server's sharing mode (``None`` = no cap).
    :returns: Session id -> level, or ``None`` where no new grant is allowed.
    """
    if mode is None or mode == SharingMode.ON:
        return dict.fromkeys(session_ids, LEVEL_MANAGE)
    workspaces = _workspaces(deps, session_ids, mode)
    levels = (LEVEL_MANAGE, LEVEL_EDIT, LEVEL_READ)
    return {
        sid: next(
            (lv for lv in levels if sharing_refusal(mode, lv, workspaces.get(sid)) is None), None
        )
        for sid in session_ids
    }


def sharing_refusal_for(
    deps: FactoryDeps, factory_id: str, level: int, mode: SharingMode
) -> str | None:
    """
    Why the sharing mode refuses a roster grant at *level*, if it does.

    Synchronous; run it in a worker thread.

    :returns: The refusal for the first managed session that refuses, else ``None``.
    """
    if mode == SharingMode.ON:
        return None
    sessions = managed_sessions(deps, factory_id)
    workspaces = _workspaces(deps, sessions, mode)
    for sid in sessions:
        refusal = sharing_refusal(mode, level, workspaces.get(sid))
        if refusal is not None:
            return refusal
    return None


async def require_roster_sharing(app_state: Any, factory_id: str, role: str) -> None:
    """
    Refuse a roster *role* the sharing mode does not allow on every managed session.

    :raises CockpitError: 403 ``sharing_refused``.
    """
    from omnigent.server.factory.control import CockpitError

    mode = sharing_mode(app_state)
    level = ROLE_LEVELS[role]
    deps = _deps(app_state)
    refusal = await asyncio.to_thread(sharing_refusal_for, deps, factory_id, level, mode)
    if refusal is not None:
        raise CockpitError(403, "sharing_refused", refusal)


def _target(level: int, have: int | None, cap: int | None) -> int | None:
    """
    The grant to write for a roster *level*, or ``None`` to leave *have* as is.

    Owner grants stay; lowering access is always allowed; raising it is a new
    share, capped by the sharing mode (*cap*, ``None`` = no new grants).
    """
    if have is not None and (have >= LEVEL_OWNER or have == level):
        return None
    if have is not None and have > level:
        return level
    if cap is None:
        return None
    want = min(level, cap)
    return want if have is None or have < want else None


def _apply(
    deps: FactoryDeps,
    wanted: dict[str, int],
    session_ids: Sequence[str],
    caps: dict[str, int | None] | None = None,
) -> list[tuple[str, str, int]]:
    """
    Write *wanted* (user -> level) onto *session_ids*, skipping owner grants.

    :param caps: The sharing mode's cap per session (:func:`grant_caps`), or
        ``None`` when the caller already checked the mode.
    :returns: ``(user, session id, level)`` for each grant written.
    """
    perms = deps.permission_store
    if perms is None or not wanted:
        return []
    current = _grant_levels(deps, session_ids)
    written: list[tuple[str, str, int]] = []
    for user, level in wanted.items():
        perms.ensure_user(user)
        for sid in session_ids:
            have = current.get(sid, {}).get(user)
            cap = caps.get(sid) if caps is not None else LEVEL_MANAGE
            want = _target(level, have, cap)
            if want is None:
                continue
            perms.grant(user, sid, want)
            written.append((user, sid, want))
    return written


def _revoke(deps: FactoryDeps, user_id: str, session_ids: Sequence[str]) -> list[str]:
    """Revoke *user_id* on *session_ids*, never an owner grant. :returns: Revoked ids."""
    perms = deps.permission_store
    if perms is None:
        return []
    current = _grant_levels(deps, session_ids)
    revoked = []
    for sid in session_ids:
        have = current.get(sid, {}).get(user_id)
        if have is None or have >= LEVEL_OWNER:
            continue
        perms.revoke(user_id, sid)
        revoked.append(sid)
    return revoked


def wanted_levels(people: Sequence[FactoryPerson]) -> dict[str, int]:
    """:returns: User -> grant level the roster wants (the creator keeps their owner grant)."""
    return {
        p.user_id: ROLE_LEVELS[p.role]
        for p in people
        if not p.is_creator and p.role in ROLE_LEVELS and p.user_id not in RESERVED_USERS
    }


def mirror_member(
    deps: FactoryDeps, factory_id: str, session_id: str, *, mode: SharingMode | None = None
) -> int:
    """
    Apply the whole roster to one session (a member that just joined).

    Only a top-level session labelled with *factory_id* and owned by the
    factory's creator is a member. Synchronous; run it in a worker thread.

    :param mode: The server's sharing mode, which caps the grants.
    :returns: Grants written.
    """
    if deps.permission_store is None:
        return 0
    conv = deps.conversation_store.get_conversation(session_id)
    if conv is None or not is_top_level(conv):
        return 0
    if not _same_id(conv.labels.get(LABEL_ID), factory_id):
        return 0
    with _roster_lock(factory_id):
        if session_id not in _creator_owned(deps, factory_id, [session_id]):
            return 0
        people = deps.factory_store.list_people(factory_id)
        caps = grant_caps(deps, [session_id], mode)
        return len(_apply(deps, wanted_levels(people), [session_id], caps))


async def mirror_member_on_create(app_state: Any, conv: Conversation) -> None:
    """
    Give a just-created factory member the roster's grants. Never raises.

    :param app_state: ``app.state`` (the factory deps may be missing).
    :param conv: The created session, labelled with its factory id.
    """
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    factory_id = conv.labels.get(LABEL_ID)
    if not isinstance(deps, FactoryDeps) or not factory_id or deps.permission_store is None:
        return
    try:
        mode = sharing_mode(app_state)
        written = await asyncio.to_thread(mirror_member, deps, factory_id, conv.id, mode=mode)
    except Exception:  # noqa: BLE001 - reconcile repairs a missed grant within a minute
        _logger.warning("factory: could not copy the roster onto %s", conv.id, exc_info=True)
        return
    if written:
        _logger.info("factory: copied %d roster grant(s) onto new member %s", written, conv.id)


# ── Roster writes ──────────────────────────────────────────────


def _factory_name(deps: FactoryDeps, factory_id: str) -> str:
    root = deps.conversation_store.get_conversation(factory_id)
    if root is None:
        return factory_id
    return root.labels.get(LABEL_NAME) or display_title(root.title, root.id)


def _notice(app_state: Any, factory_id: str, text: str) -> None:
    """Mail the root lead an info notice (best effort)."""
    from omnigent.server.factory.control import send_text

    try:
        send_text(app_state, factory_id, text, kind="info")
    except Exception:  # noqa: BLE001 - the event log still records the change
        _logger.warning("factory: could not notify %s of a roster change", factory_id)


def _by_data(deps: FactoryDeps, factory_id: str, by_user: str | None) -> dict[str, Any]:
    return {
        "by": human_label(deps, factory_id, by_user),
        "by_kind": "human" if by_user is not None else "server",
        "by_role": role_of(deps, factory_id, by_user) if by_user is not None else None,
    }


def _set_sync(
    deps: FactoryDeps, factory_id: str, user_id: str, role: str, by_user: str | None
) -> PersonChange:
    store = deps.factory_store
    with _roster_lock(factory_id):
        ensure_creator(deps, factory_id)
        before = store.get_person(factory_id, user_id)
        row, _ = store.upsert_person(
            factory_id, user_id, role=role, invited_by=by_user or RESERVED_USER_LOCAL
        )
        sessions = _person_sessions(deps, factory_id, user_id)
        written = _apply(deps, {user_id: ROLE_LEVELS[role]}, sessions)
    return PersonChange(
        person=row,
        previous_role=before.role if before is not None else None,
        granted=[sid for _, sid, _ in written],
    )


async def set_person(
    app_state: Any, factory_id: str, user_id: str, role: str, *, by_user: str | None
) -> PersonChange:
    """
    Put *user_id* on the roster as *role* (or change their role) and copy the grant.

    Emits ``people.invited`` or ``people.role_changed``, mails the root lead an
    info notice and announces the ``snapshot`` and ``people`` scopes. Callers
    check the caller's level, the sharing mode and the target first.

    :param role: ``viewer`` / ``operator`` / ``owner``.
    :param by_user: Who made the change (``None`` for the server).
    :raises ValueError: On an unknown role.
    """
    if role not in ROLE_LEVELS:
        raise ValueError(f"role must be one of {list(PERSON_ROLES)}")
    deps = _deps(app_state)
    change = await asyncio.to_thread(_set_sync, deps, factory_id, user_id, role, by_user)
    by = await asyncio.to_thread(_by_data, deps, factory_id, by_user)
    data = {**by, "user": user_id, "role": role, "from_role": change.previous_role}
    data["granted"] = len(change.granted)
    name = display_title(user_id, user_id)
    if change.created:
        kind, summary = "people.invited", f"{by['by']} invited {name} as {role}"
        _notice(app_state, factory_id, f"[Factory notice · {name} joined as {role}]")
    elif change.previous_role != role:
        kind = "people.role_changed"
        summary = f"{by['by']} changed {name} from {change.previous_role} to {role}"
        _notice(app_state, factory_id, f"[Factory notice · {name} is now {role}]")
    else:
        kind, summary = "", ""
    if kind:
        await events.emit(
            app_state,
            factory_id,
            kind,
            severity="notice",
            subject=f"person:{user_id}",
            summary=summary,
            data=data,
        )
    _announce_access(app_state, factory_id, user_id, change.granted)
    return change


def _remove_sync(deps: FactoryDeps, factory_id: str, user_id: str) -> PersonChange:
    store = deps.factory_store
    with _roster_lock(factory_id):
        before = store.get_person(factory_id, user_id)
        if before is not None:
            store.delete_person(factory_id, user_id)
        revoked = _revoke(deps, user_id, _person_sessions(deps, factory_id, user_id))
    return PersonChange(
        person=None,
        previous_role=before.role if before is not None else None,
        revoked=revoked,
    )


async def remove_person(
    app_state: Any, factory_id: str, user_id: str, *, by_user: str | None
) -> PersonChange:
    """
    Take *user_id* off the roster and revoke their grants on every member,
    archived ones included.

    Leaving (``by_user == user_id``) emits ``people.left``, otherwise
    ``people.removed``. The removed user gets the frame too (their refetch
    answers 404) and their open session streams are closed, since a stream
    checks access only when it opens.
    """
    deps = _deps(app_state)
    # Named while still on the roster, so the role matches the act.
    by = await asyncio.to_thread(_by_data, deps, factory_id, by_user)
    change = await asyncio.to_thread(_remove_sync, deps, factory_id, user_id)
    left = by_user == user_id
    if change.previous_role is not None or change.revoked:
        kind = "people.left" if left else "people.removed"
        summary = f"{user_id} left" if left else f"{by['by']} removed {user_id}"
        await events.emit(
            app_state,
            factory_id,
            kind,
            severity="notice",
            subject=f"person:{user_id}",
            summary=summary,
            data={**by, "user": user_id, "from_role": change.previous_role},
        )
        verb = "left the factory" if left else f"was removed by {by['by']}"
        _notice(
            app_state, factory_id, f"[Factory notice · {display_title(user_id, user_id)} {verb}]"
        )
    factory_presence.registry.leave(factory_id, user_id)
    _close_streams(change.revoked, user_id)
    live.announce_factory_changed(app_state, factory_id, SCOPES)
    live.publish(factory_id, SCOPES, [user_id])
    return change


def _announce_sessions(user_id: str, session_ids: Sequence[str]) -> None:
    """Push the sessions *user_id* can now open to their sidebar."""
    from omnigent.server.routes._sessions.helpers import _announce_session_added

    for sid in session_ids:
        _announce_session_added(user_id, sid)


def _announce_access(
    app_state: Any, factory_id: str, user_id: str, session_ids: Sequence[str]
) -> None:
    _announce_sessions(user_id, session_ids)
    live.announce_factory_changed(app_state, factory_id, SCOPES)


def _close_streams(session_ids: Sequence[str], user_id: str) -> None:
    """End the session streams *user_id* has open in these member trees."""
    from omnigent.runtime import session_stream
    from omnigent.server import presence as session_presence

    for sid in session_ids:
        for conversation_id in session_presence.viewer_conversations(sid, user_id):
            # Every viewer of that stream reconnects; the reconnect re-checks access.
            session_stream.close(conversation_id)


# ── Drift ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Drift:
    """One grant the roster wants that the store does not have."""

    user_id: str
    session_id: str
    want: int
    have: int | None


def find_drift(
    deps: FactoryDeps, factory_id: str, *, mode: SharingMode | None = None
) -> list[Drift]:
    """
    Grants the roster wants that differ from the stored ones (owner grants excepted).

    Synchronous; run it in a worker thread.

    :param mode: The server's sharing mode, which caps the grants.
    """
    if deps.permission_store is None:
        return []
    wanted = wanted_levels(list_people(deps, factory_id))
    if not wanted:
        return []
    sessions = managed_sessions(deps, factory_id)
    current = _grant_levels(deps, sessions)
    caps = grant_caps(deps, sessions, mode)
    out = []
    for user, level in wanted.items():
        for sid in sessions:
            have = current.get(sid, {}).get(user)
            want = _target(level, have, caps.get(sid))
            if want is not None:
                out.append(Drift(user, sid, want, have))
    return out


def _repair_sync(deps: FactoryDeps, factory_id: str, mode: SharingMode | None) -> list[Drift]:
    with _roster_lock(factory_id):
        drift = find_drift(deps, factory_id, mode=mode)
        perms = deps.permission_store
        for item in drift:
            assert perms is not None
            perms.ensure_user(item.user_id)
            perms.grant(item.user_id, item.session_id, item.want)
    return drift


async def repair_drift(app_state: Any, root: Conversation) -> int:
    """
    Repair roster grants that drifted (a missed new member, a manual edit).

    Emits ``people.drift_repaired`` when anything was written.

    :returns: Grants repaired.
    """
    deps = _deps(app_state)
    if deps.permission_store is None:
        return 0
    drift = await asyncio.to_thread(_repair_sync, deps, root.id, sharing_mode(app_state))
    if not drift:
        return 0
    users = sorted({d.user_id for d in drift})
    await events.emit(
        app_state,
        root.id,
        "people.drift_repaired",
        severity="notice",
        summary=f"Repaired {len(drift)} roster grant(s) for {', '.join(users)[:200]}",
        data={
            "by": "server",
            "by_kind": "server",
            "repairs": [
                {"user": d.user_id, "session_id": d.session_id, "want": d.want, "had": d.have}
                for d in drift[:50]
            ],
            "count": len(drift),
        },
    )
    for user in users:
        added = sorted({d.session_id for d in drift if d.user_id == user and d.have is None})
        _announce_sessions(user, added)
    live.announce_factory_changed(app_state, root.id, SCOPES)
    return len(drift)


# ── The managed-grant guard ────────────────────────────────────


def _managed_by(deps: FactoryDeps, session_id: str, user_id: str) -> tuple[str, str] | None:
    conv = deps.conversation_store.get_conversation(session_id)
    if conv is None or not is_top_level(conv):
        return None
    factory_id = conv.labels.get(LABEL_ID)
    if not factory_id:
        return None
    person = deps.factory_store.get_person(factory_id, user_id)
    if person is None or person.is_creator:
        return None
    if session_id not in _creator_owned(deps, factory_id, [session_id]):
        return None
    return factory_id, _factory_name(deps, factory_id)


async def managed_grant_conflict(
    app_state: Any, session_id: str, user_id: str
) -> FactoryRouteError | None:
    """
    The 409 a revoke (or leave) of a factory-managed grant answers with.

    :param session_id: The session whose grant is being removed.
    :param user_id: Whose grant.
    :returns: ``409 managed_by_factory`` when *user_id* is on the roster of the
        factory *session_id* belongs to, else ``None``.
    """
    from omnigent.server.factory.routes import FactoryRouteError

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return None
    try:
        managed = await asyncio.to_thread(_managed_by, deps, session_id, user_id)
    except ValueError:
        return None
    if managed is None:
        return None
    factory_id, name = managed
    return FactoryRouteError(
        409,
        "managed_by_factory",
        f"{user_id}'s access to this session is managed by factory {name!r} "
        f"({norm_id(factory_id)}); use the factory's People page.",
    )


# ── Read models ────────────────────────────────────────────────


def person_view(person: FactoryPerson) -> dict[str, Any]:
    """:returns: The ``PersonView`` JSON of a roster row, with live presence."""
    entries = {e.user_id: e for e in factory_presence.registry.entries(person.factory_id)}
    entry = entries.get(person.user_id)
    return {
        "user_id": person.user_id,
        "name": person.user_id,
        "role": person.role,
        "is_creator": person.is_creator,
        "invited_by": person.invited_by or None,
        "invited_at": person.invited_at,
        "last_seen_at": _last_seen(person),
        "present": entry is not None,
        "location": entry.location if entry is not None else None,
        "idle": entry.idle if entry is not None else False,
        "notify": person.notify,
    }


def _last_seen(person: FactoryPerson) -> int | None:
    live_seen = factory_presence.registry.last_seen(person.factory_id, person.user_id)
    values = [v for v in (person.last_seen_at, live_seen) if v is not None]
    return int(max(values)) if values else None


def last_seen_at(deps: FactoryDeps, factory_id: str, user_id: str) -> float | None:
    """:returns: When *user_id* was last seen in the factory (cockpit or stored)."""
    row = deps.factory_store.get_person(factory_id, user_id)
    if row is not None:
        return _last_seen(row)
    return factory_presence.registry.last_seen(factory_id, user_id)


async def roster_humans(app_state: Any, factory_id: str) -> list[dict[str, Any]]:
    """
    The humans block of ``GET /v1/factory/roster`` (what agents know of people).

    :returns: ``[{name, role, present, last_seen}]``, creator first.
    """
    deps = _deps(app_state)
    if deps.permission_store is None:
        return []
    people = await asyncio.to_thread(list_people, deps, factory_id)
    present = {e.user_id for e in factory_presence.registry.entries(factory_id)}
    rows = sorted(people, key=lambda p: (not p.is_creator, p.invited_at, p.user_id))
    return [
        {
            "name": p.user_id,
            "role": person_role(p),
            "present": p.user_id in present,
            "last_seen": _last_seen(p),
        }
        for p in rows
    ]


def _deps(app_state: Any) -> FactoryDeps:
    from omnigent.server.factory.context import factory_deps

    return factory_deps(app_state)


__all__ = [
    "RESERVED_USERS",
    "ROLE_CREATOR",
    "ROLE_LEVELS",
    "ROLE_OPERATOR",
    "ROLE_OWNER",
    "ROLE_VIEWER",
    "VIEWER_CAN_LEVELS",
    "Drift",
    "PersonChange",
    "creator_of",
    "ensure_creator",
    "find_drift",
    "grant_caps",
    "human_label",
    "last_seen_at",
    "level_role",
    "list_people",
    "managed_grant_conflict",
    "managed_sessions",
    "mirror_member",
    "mirror_member_on_create",
    "people_count",
    "person_role",
    "person_view",
    "remove_person",
    "repair_drift",
    "require_roster_sharing",
    "role_of",
    "roster_humans",
    "set_person",
    "sharing_refusal_for",
    "viewer_block",
    "viewer_level",
    "wanted_levels",
]
