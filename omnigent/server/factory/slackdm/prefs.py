"""Per-person Slack DM settings, stored in ``factory_people.notify``.

The stored JSON holds what the person chose plus their Slack link; anything
missing falls back to the defaults of their role (:func:`role_defaults`).
:class:`NotifyPrefs` validates it. Quiet hours and the digest hour are local
times in the person's IANA time zone (DST-correct through :mod:`zoneinfo`); a
quiet window may wrap midnight.

The roster (``people.person_view``) only ever sees :func:`summary`; member ids,
DM channels and verification hashes stay behind ``notify/me``.
"""

from __future__ import annotations

import copy
import re
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import TYPE_CHECKING, Any, Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryPerson
    from omnigent.server.factory.context import FactoryDeps

VERSION: Final = 1
EVENT_GROUPS: Final = (
    "decisions",
    "reminders",
    "prs",
    "incidents",
    "factory",
    "budget",
    "mentions",
    "retros",
)
LINK_STATUSES: Final = ("unlinked", "pending", "verified", "error")
_HHMM: Final = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")

ROLE_VIEWER: Final = "viewer"
ROLE_OPERATOR: Final = "operator"
ROLE_OWNER: Final = "owner"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerifyState(_Model):
    """A pending verification code (server only: never serialized to anyone)."""

    hash: str
    expires_at: int
    tries: int = 0
    by: str | None = None


class SlackLink(_Model):
    """The person's Slack account and its link status."""

    enabled: bool = True
    status: Literal["unlinked", "pending", "verified", "error"] = "unlinked"
    user_id: str | None = None
    team_id: str | None = None
    name: str | None = None
    handle: str | None = None
    email: str | None = None
    tz: str | None = None
    dm_channel: str | None = None
    linked_at: int | None = None
    error: str | None = None
    verify: VerifyState | None = None


class EventPrefs(_Model):
    """Which items reach the person as DMs."""

    decisions: bool = True
    reminders: bool = True
    prs: bool = True
    incidents: bool = True
    factory: bool = True
    budget: bool = True
    mentions: bool = True
    retros: bool = False


class DigestPrefs(_Model):
    """The daily digest: on/off, local hour, every day or weekdays."""

    enabled: bool = True
    hour: int = Field(default=9, ge=0, le=23)
    days: Literal["daily", "weekdays"] = "daily"


def _hhmm(value: str) -> str:
    if not isinstance(value, str) or _HHMM.fullmatch(value) is None:
        raise ValueError("times are HH:MM (00:00-23:59)")
    return value


class QuietPrefs(_Model):
    """Quiet hours: DMs wait for the end (critical incidents may break through)."""

    enabled: bool = True
    start: str = "22:00"
    end: str = "08:00"
    critical_through: bool = False

    @field_validator("start", "end")
    @classmethod
    def _check_times(cls, value: str) -> str:
        return _hhmm(value)


class UiPrefs(_Model):
    """
    The person's cockpit choices in this factory (not Slack; kept with the same row).

    :param cockpit: ``v2`` to use the new cockpit, ``v1`` for the classic one,
        ``None`` until they choose (the classic one while it is the default).
    """

    cockpit: Literal["v1", "v2"] | None = None


def valid_tz(value: str | None) -> str | None:
    """:returns: *value* when ``zoneinfo`` knows it. :raises ValueError: Otherwise."""
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("tz must be an IANA time zone such as Europe/Zagreb")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown time zone {value!r}") from None
    return value


class NotifyPrefs(_Model):
    """One person's Slack DM settings (``factory_people.notify``)."""

    v: int = VERSION
    slack: SlackLink = Field(default_factory=SlackLink)
    events: EventPrefs = Field(default_factory=EventPrefs)
    incident_min: Literal["warning", "critical"] = "warning"
    digest: DigestPrefs = Field(default_factory=DigestPrefs)
    quiet: QuietPrefs = Field(default_factory=QuietPrefs)
    tz: str | None = None
    hold_while_active: bool = True
    ui: UiPrefs = Field(default_factory=UiPrefs)

    @field_validator("tz")
    @classmethod
    def _check_tz(cls, value: str | None) -> str | None:
        return valid_tz(value)

    @property
    def reachable(self) -> bool:
        """:returns: Whether DMs can go to this person (linked, verified, switched on)."""
        return (
            self.slack.enabled
            and self.slack.status == "verified"
            and bool(self.slack.user_id)
            and self.slack.linked_at is not None
        )

    def wants(self, group: str) -> bool:
        """:returns: Whether the event *group* is switched on."""
        return bool(getattr(self.events, group, False))


class PrefsUpdate(_Model):
    """``PUT …/notify/me``: the fields a person changes (each optional; ``ui`` too)."""

    enabled: bool | None = None
    events: EventPrefs | None = None
    incident_min: Literal["warning", "critical"] | None = None
    digest: DigestPrefs | None = None
    quiet: QuietPrefs | None = None
    tz: str | None = None
    hold_while_active: bool | None = None
    ui: UiPrefs | None = None

    @field_validator("tz")
    @classmethod
    def _check_tz(cls, value: str | None) -> str | None:
        return valid_tz(value)


# ── Role defaults ──────────────────────────────────────────────


def role_key(role: str | None) -> str:
    """:returns: ``viewer`` / ``operator`` / ``owner`` (the creator is an owner)."""
    if role in (ROLE_OWNER, "creator"):
        return ROLE_OWNER
    if role == ROLE_OPERATOR:
        return ROLE_OPERATOR
    return ROLE_VIEWER


def role_defaults(role: str | None) -> dict[str, Any]:
    """
    The settings a person of *role* starts with.

    Viewers cannot answer, so only mentions reach them; budgets are the owners'.
    """
    key = role_key(role)
    acts = key != ROLE_VIEWER
    return {
        "events": {
            "decisions": acts,
            "reminders": acts,
            "prs": acts,
            "incidents": acts,
            "factory": acts,
            "budget": key == ROLE_OWNER,
            "mentions": True,
            "retros": False,
        },
        "incident_min": "warning",
        "digest": {"enabled": acts, "hour": 9, "days": "daily"},
        "quiet": {"enabled": True, "start": "22:00", "end": "08:00", "critical_through": False},
        "tz": None,
        "hold_while_active": True,
    }


def _merge(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load(raw: Mapping[str, Any] | None, role: str | None) -> NotifyPrefs:
    """
    Parse a stored ``notify`` value over the role's defaults.

    A section that does not validate falls back to its default; the Slack link
    is kept whenever it validates.
    """
    defaults = role_defaults(role)
    stored = dict(raw or {})
    try:
        return NotifyPrefs.model_validate(_merge(defaults, stored))
    except ValidationError:
        pass
    merged: dict[str, Any] = dict(defaults)
    for key, value in stored.items():
        trial = _merge(merged, {key: value})
        try:
            NotifyPrefs.model_validate(trial)
        except ValidationError:
            continue
        merged = trial
    return NotifyPrefs.model_validate(merged)


def for_person(person: FactoryPerson) -> NotifyPrefs:
    """:returns: :func:`load` of a roster row."""
    role = "creator" if person.is_creator else person.role
    return load(person.notify, role)


def settings_view(prefs: NotifyPrefs) -> dict[str, Any]:
    """:returns: The settings a person edits (no link, no verification state)."""
    return {
        "enabled": prefs.slack.enabled,
        "events": prefs.events.model_dump(),
        "incident_min": prefs.incident_min,
        "digest": prefs.digest.model_dump(),
        "quiet": prefs.quiet.model_dump(),
        "tz": prefs.tz,
        "hold_while_active": prefs.hold_while_active,
        "ui": prefs.ui.model_dump(),
    }


def link_view(prefs: NotifyPrefs) -> dict[str, Any]:
    """
    :returns: The person's own link (their member id, never the hash or DM channel);
        the account's name and email only once they proved it is theirs.
    """
    link = prefs.slack
    proven = link.status in ("verified", "error")
    return {
        "status": link.status,
        "enabled": link.enabled,
        "user_id": link.user_id,
        "team_id": link.team_id,
        "name": link.name if proven else link.handle,
        "handle": link.handle,
        "email": link.email if proven else None,
        "linked_at": link.linked_at,
        "error": link.error,
        "code_expires_at": link.verify.expires_at if link.verify is not None else None,
    }


def summary(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """:returns: What the roster may show of anyone's settings: ``{slack, enabled}``."""
    slack = (raw or {}).get("slack") if isinstance(raw, Mapping) else None
    slack = slack if isinstance(slack, Mapping) else {}
    status = slack.get("status")
    return {
        "slack": status if status in LINK_STATUSES else "unlinked",
        "enabled": bool(slack.get("enabled", True)),
    }


def apply_update(raw: Mapping[str, Any] | None, update: PrefsUpdate) -> dict[str, Any]:
    """:returns: *raw* with the fields *update* sets (the link is left alone)."""
    out = copy.deepcopy(dict(raw or {}))
    out["v"] = VERSION
    if update.enabled is not None:
        slack = dict(out.get("slack") or {})
        slack["enabled"] = update.enabled
        out["slack"] = slack
    for key in ("events", "digest", "quiet"):
        value = getattr(update, key)
        if value is not None:
            out[key] = value.model_dump()
    if update.incident_min is not None:
        out["incident_min"] = update.incident_min
    if "tz" in update.model_fields_set:
        out["tz"] = update.tz
    if update.hold_while_active is not None:
        out["hold_while_active"] = update.hold_while_active
    if update.ui is not None:
        kept = out.get("ui") if isinstance(out.get("ui"), dict) else {}
        out["ui"] = {**kept, **update.ui.model_dump(exclude_unset=True)}
    return out


# ── Time zones, quiet hours, the digest clock ─────────────────


def zone(prefs: NotifyPrefs) -> ZoneInfo:
    """:returns: The person's time zone (UTC when unknown)."""
    for name in (prefs.tz, prefs.slack.tz):
        if name:
            try:
                return ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo("UTC")


def tz_name(prefs: NotifyPrefs) -> str:
    """:returns: The IANA name :func:`zone` uses."""
    return str(zone(prefs).key)


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _at(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    minutes = _minutes(hhmm)
    return datetime.combine(day, dtime(minutes // 60, minutes % 60), tzinfo=tz)


def in_quiet(prefs: NotifyPrefs, at: float) -> bool:
    """:returns: Whether epoch second *at* is inside the person's quiet hours."""
    quiet = prefs.quiet
    if not quiet.enabled or quiet.start == quiet.end:
        return False
    local = datetime.fromtimestamp(at, tz=zone(prefs))
    now_m = local.hour * 60 + local.minute
    start, end = _minutes(quiet.start), _minutes(quiet.end)
    if start < end:
        return start <= now_m < end
    return now_m >= start or now_m < end


def quiet_end(prefs: NotifyPrefs, at: float) -> float:
    """:returns: When the quiet window containing *at* ends (*at* when not quiet)."""
    if not in_quiet(prefs, at):
        return at
    tz = zone(prefs)
    local = datetime.fromtimestamp(at, tz=tz)
    for offset in (0, 1):
        end = _at(local.date() + timedelta(days=offset), prefs.quiet.end, tz)
        if end.timestamp() > at:
            return end.timestamp()
    return at


def quiet_start(prefs: NotifyPrefs, at: float) -> float:
    """:returns: When the quiet window containing *at* began (*at* when not quiet)."""
    if not in_quiet(prefs, at):
        return at
    tz = zone(prefs)
    local = datetime.fromtimestamp(at, tz=tz)
    for offset in (0, -1):
        start = _at(local.date() + timedelta(days=offset), prefs.quiet.start, tz)
        if start.timestamp() <= at:
            return start.timestamp()
    return at


def local_day(prefs: NotifyPrefs, at: float) -> date:
    """:returns: The person's local date at *at*."""
    return datetime.fromtimestamp(at, tz=zone(prefs)).date()


def digest_time(prefs: NotifyPrefs, day: date) -> float:
    """:returns: Epoch second of the digest hour on the person's local *day*."""
    return datetime.combine(day, dtime(prefs.digest.hour, 0), tzinfo=zone(prefs)).timestamp()


def digest_day_on(prefs: NotifyPrefs, day: date) -> bool:
    """:returns: Whether the digest goes out on *day* (every day, or weekdays)."""
    return prefs.digest.days == "daily" or day.weekday() < 5


def clock_label(prefs: NotifyPrefs, at: float) -> str:
    """:returns: ``"16:00"`` in the person's zone (``"Tue 16:00"`` when not today)."""
    tz = zone(prefs)
    local = datetime.fromtimestamp(at, tz=tz)
    today = datetime.fromtimestamp(time.time(), tz=tz).date()
    if local.date() == today:
        return local.strftime("%H:%M")
    return local.strftime("%a %H:%M")


def utc_day(at: float) -> str:
    """:returns: ``YYYY-MM-DD`` in UTC."""
    return datetime.fromtimestamp(at, tz=UTC).strftime("%Y-%m-%d")


# ── Storage (read-modify-write under a per-person lock) ────────

_locks: dict[tuple[str, str], threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(factory_id: str, user_id: str) -> threading.Lock:
    key = (norm_id(factory_id), user_id)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def update_notify(
    deps: FactoryDeps,
    factory_id: str,
    user_id: str,
    change: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> FactoryPerson | None:
    """
    Change one person's stored ``notify`` (synchronous).

    :param change: Gets a copy of the stored value; returns the new one, or
        ``None`` to leave it unchanged.
    :returns: The roster row afterwards, or ``None`` when they are not on it.
    """
    store = deps.factory_store
    with _lock(factory_id, user_id):
        person = store.get_person(factory_id, user_id)
        if person is None:
            return None
        new = change(copy.deepcopy(dict(person.notify or {})))
        if new is None:
            return person
        new["v"] = VERSION
        return store.update_person(factory_id, user_id, notify=new) or person


def set_link(raw: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """:returns: *raw* with ``slack`` fields set (``None`` clears one)."""
    slack = dict(raw.get("slack") or {})
    slack.update(fields)
    raw["slack"] = {k: v for k, v in slack.items() if v is not None or k == "error"}
    return raw


def unlinked(raw: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
    """:returns: *raw* with the link cleared (the settings stay)."""
    enabled = (raw.get("slack") or {}).get("enabled", True)
    raw["slack"] = {"enabled": enabled, "status": "unlinked", "error": reason}
    return raw


def reset_links(
    deps: FactoryDeps, factory_id: str, reason: str, *, keep_team: str | None = None
) -> int:
    """
    Unlink everyone (the bot token moved to another workspace).

    :param keep_team: Keep the links made in this workspace.
    :returns: How many were unlinked.
    """
    count = 0
    for person in deps.factory_store.list_people(factory_id):
        slack = (person.notify or {}).get("slack") or {}
        if slack.get("status") in (None, "unlinked"):
            continue
        if keep_team and slack.get("team_id") == keep_team:
            continue
        update_notify(deps, factory_id, person.user_id, lambda raw: unlinked(raw, reason))
        count += 1
    return count


def reset() -> None:
    """Forget the per-person locks (tests)."""
    with _locks_guard:
        _locks.clear()


__all__ = [
    "EVENT_GROUPS",
    "DigestPrefs",
    "EventPrefs",
    "NotifyPrefs",
    "PrefsUpdate",
    "QuietPrefs",
    "SlackLink",
    "apply_update",
    "clock_label",
    "digest_day_on",
    "digest_time",
    "for_person",
    "in_quiet",
    "link_view",
    "load",
    "local_day",
    "quiet_end",
    "quiet_start",
    "reset_links",
    "role_defaults",
    "role_key",
    "set_link",
    "settings_view",
    "summary",
    "tz_name",
    "unlinked",
    "update_notify",
    "valid_tz",
    "zone",
]
