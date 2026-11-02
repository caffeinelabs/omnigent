"""Per-person Slack DM settings: role defaults, validation, quiet hours (DST, midnight wrap)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from omnigent.server.factory.slackdm import prefs


def _at(text: str, tz: str = "UTC") -> float:
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(tz)).timestamp()


def test_role_defaults() -> None:
    viewer = prefs.load(None, "viewer")
    operator = prefs.load(None, "operator")
    owner = prefs.load(None, "creator")

    assert [g for g in prefs.EVENT_GROUPS if viewer.wants(g)] == ["mentions"]
    assert not viewer.digest.enabled
    assert operator.wants("decisions") and operator.wants("prs") and not operator.wants("budget")
    assert operator.digest.enabled and operator.digest.hour == 9
    assert owner.wants("budget") and not owner.wants("retros")
    assert (owner.quiet.start, owner.quiet.end, owner.quiet.critical_through) == (
        "22:00",
        "08:00",
        False,
    )
    assert not owner.reachable  # nothing linked yet


def test_stored_choices_override_defaults_and_bad_sections_fall_back() -> None:
    raw = {
        "events": {"decisions": False},
        "digest": {"hour": 99},
        "quiet": {"start": "23:30", "end": "07:00"},
        "tz": "Mars/Olympus",
    }

    loaded = prefs.load(raw, "owner")

    assert not loaded.wants("decisions") and loaded.wants("budget")
    assert loaded.digest.hour == 9  # 99 is no hour: the default stays
    assert (loaded.quiet.start, loaded.quiet.end) == ("23:30", "07:00")
    assert loaded.tz is None


def test_updates_are_validated_and_reject_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        prefs.PrefsUpdate.model_validate({"tz": "Nowhere/Land"})
    with pytest.raises(ValidationError):
        prefs.PrefsUpdate.model_validate({"quiet": {"start": "25:00", "end": "07:00"}})
    with pytest.raises(ValidationError):
        prefs.PrefsUpdate.model_validate({"slack": {"status": "verified"}})

    update = prefs.PrefsUpdate.model_validate(
        {"enabled": False, "tz": "Europe/Zagreb", "incident_min": "critical"}
    )
    raw = prefs.apply_update({"slack": {"status": "verified", "user_id": "U1"}}, update)

    assert raw["slack"] == {"status": "verified", "user_id": "U1", "enabled": False}
    assert (raw["tz"], raw["incident_min"]) == ("Europe/Zagreb", "critical")


def test_quiet_hours_wrap_midnight() -> None:
    p = prefs.load({"tz": "UTC"}, "owner")

    assert prefs.in_quiet(p, _at("2026-11-02T23:00"))
    assert prefs.in_quiet(p, _at("2026-11-03T07:59"))
    assert not prefs.in_quiet(p, _at("2026-11-03T08:00"))
    assert not prefs.in_quiet(p, _at("2026-11-02T21:59"))
    assert prefs.quiet_end(p, _at("2026-11-02T23:00")) == _at("2026-11-03T08:00")
    assert prefs.quiet_start(p, _at("2026-11-03T03:00")) == _at("2026-11-02T22:00")
    assert prefs.quiet_end(p, _at("2026-11-03T12:00")) == _at("2026-11-03T12:00")


def test_quiet_hours_follow_the_local_zone_across_dst() -> None:
    p = prefs.load({"tz": "Europe/Zagreb"}, "owner")
    # Europe/Zagreb leaves summer time on 2026-10-25 at 03:00 local.
    before = _at("2026-10-24T22:30", "Europe/Zagreb")
    end = prefs.quiet_end(p, before)

    assert end == _at("2026-10-25T08:00", "Europe/Zagreb")
    assert end - before == 10.5 * 3600  # one hour longer than on a normal night
    summer = prefs.quiet_start(p, _at("2026-07-01T05:00", "Europe/Zagreb"))
    assert datetime.fromtimestamp(summer, tz=UTC).hour == 20  # 22:00 CEST


def test_daytime_window_and_a_disabled_window() -> None:
    p = prefs.load({"tz": "UTC", "quiet": {"start": "12:00", "end": "13:00"}}, "owner")
    off = prefs.load({"tz": "UTC", "quiet": {"enabled": False}}, "owner")

    assert prefs.in_quiet(p, _at("2026-11-02T12:30"))
    assert not prefs.in_quiet(p, _at("2026-11-02T13:30"))
    assert not prefs.in_quiet(off, _at("2026-11-02T23:00"))


def test_digest_clock_and_weekdays() -> None:
    p = prefs.load({"tz": "Europe/Zagreb", "digest": {"hour": 9, "days": "weekdays"}}, "owner")
    monday = date(2026, 11, 2)
    sunday = date(2026, 11, 1)

    assert prefs.digest_time(p, monday) == _at("2026-11-02T09:00", "Europe/Zagreb")
    assert prefs.digest_day_on(p, monday) and not prefs.digest_day_on(p, sunday)


def test_the_roster_summary_shows_only_the_status() -> None:
    raw = {
        "slack": {
            "status": "pending",
            "user_id": "U0SECRET",
            "dm_channel": "D0SECRET",
            "verify": {"hash": "abc", "expires_at": 1, "tries": 0},
        }
    }

    assert prefs.summary(raw) == {"slack": "pending", "enabled": True}
    assert prefs.summary(None) == {"slack": "unlinked", "enabled": True}
