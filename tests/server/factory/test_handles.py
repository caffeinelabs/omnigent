"""Stable short handles per member, vendor and model labels, and handle lookups.

Handles are assigned on first sight (the snapshot build), stored as the
``omnigent.factory.handle`` label, unique in the factory, and never change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.factory import identity, snapshot
from omnigent.server.factory.identity import HandleSubject, assign_handles
from omnigent.server.factory.membership import membership_of
from omnigent.util.session_lifecycle import CLOSED_LABEL_KEY, CLOSED_LABEL_VALUE
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FactoryWorld


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    snapshot.reset_cache()
    yield
    snapshot.reset_cache()


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in build_cockpit(world, db_uri, monkeypatch):
        yield value


def _subject(sid: str, role: str | None, at: float, **kw: object) -> HandleSubject:
    return HandleSubject(session_id=sid, role=role, created_at=at, **kw)  # type: ignore[arg-type]


# ── Pure assignment ─────────────────────────────────────────────


def test_leads_get_their_seat_names_and_others_draw_from_the_pool() -> None:
    handles = assign_handles(
        [
            _subject("a" * 32, "lead", 1, is_root=True),
            _subject("b" * 32, "lead", 2),
            _subject("c" * 32, "project_lead", 3),
            _subject("d" * 32, "ic", 4),
            _subject("e" * 32, "scout", 5),
        ]
    )

    assert handles["a" * 32] == "atlas"
    assert handles["b" * 32] == "sentinel"
    assert handles["e" * 32] == "scout"
    assert handles["c" * 32] in identity.POOL and handles["d" * 32] in identity.POOL
    assert len(set(handles.values())) == 5


def test_assignment_is_deterministic_and_keeps_stored_handles() -> None:
    subjects = [_subject(f"{i:032x}", "ic", i) for i in range(1, 9)]
    first = assign_handles(subjects)
    again = assign_handles(list(reversed(subjects)))
    assert first == again

    stored = [_subject(s.session_id, "ic", s.created_at, current="quill") for s in subjects[:1]]
    kept = assign_handles([*stored, *subjects[1:]])
    assert kept[subjects[0].session_id] == "quill"
    assert "quill" not in [kept[s.session_id] for s in subjects[1:]]


def test_a_duplicate_stored_handle_stays_with_the_older_session() -> None:
    older = _subject("1" * 32, "ic", 1, current="dot")
    newer = _subject("2" * 32, "ic", 2, current="dot")

    handles = assign_handles([newer, older])

    assert handles[older.session_id] == "dot"
    assert handles[newer.session_id] != "dot"


def test_a_full_pool_falls_back_to_the_role_word() -> None:
    taken = [_subject(f"{i:032x}", "ic", i, current=name) for i, name in enumerate(identity.POOL)]
    extra = [_subject("f" * 32, "ic", 999), _subject("e" * 32, "reviewer", 1000)]

    handles = assign_handles([*taken, *extra])

    assert handles["f" * 32] == "builder-2"
    assert handles["e" * 32] == "reviewer-2"
    assert identity.handle_name("builder-2") == "Builder 2"
    assert identity.handle_name("dot") == "Dot"


def test_invalid_stored_values_are_replaced() -> None:
    handles = assign_handles([_subject("9" * 32, "ic", 1, current="Not A Handle")])
    assert identity.is_handle(handles["9" * 32])


# ── Vendor and model ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("harness", "model", "vendor"),
    [
        ("claude-native", None, "claude"),
        ("codex-native", "gpt-5-codex", "codex"),
        (None, "anthropic/claude-sonnet-4-5-20250929", "claude"),
        (None, "opus", "claude"),
        (None, "gpt-5", "codex"),
        ("ic_codex", "", "codex"),
        (None, None, None),
    ],
)
def test_vendor_of(harness: str | None, model: str | None, vendor: str | None) -> None:
    assert identity.vendor_of(harness, model) == vendor


@pytest.mark.parametrize(
    ("model", "label"),
    [
        ("anthropic/claude-sonnet-4-5-20250929", "sonnet"),
        ("claude-opus-4-1", "opus"),
        ("opus", "opus"),
        ("claude-sonnet-4-5[1m]", "sonnet"),
        ("gpt-5-codex", "gpt-5-codex"),
        ("", None),
        (None, None),
    ],
)
def test_model_label(model: str | None, label: str | None) -> None:
    assert identity.model_label(model) == label


# ── Through the snapshot and the fleet API ─────────────────────


async def test_the_snapshot_stamps_and_stores_handles(cockpit: Cockpit) -> None:
    w = cockpit.world
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}", headers={"X-Test-User": "alice"}
    )
    assert resp.status_code == 200, resp.text
    members = {m["session_id"]: m for m in resp.json()["members"]}

    assert members[w.id("lead_a")]["handle"] == "atlas"
    assert members[w.id("lead_b")]["handle"] == "sentinel"
    handles = [m["handle"] for m in members.values()]
    assert all(identity.is_handle(h) for h in handles)
    assert len(set(handles)) == len(handles)
    stored = w.get("ic").labels.get(F.LABEL_HANDLE)
    assert stored == members[w.id("ic")]["handle"]
    assert membership_of(w.get("pl"), w.deps).handle == members[w.id("pl")]["handle"]

    snapshot.reset_cache()
    again = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}", headers={"X-Test-User": "alice"}
    )
    assert {m["session_id"]: m["handle"] for m in again.json()["members"]} == {
        sid: m["handle"] for sid, m in members.items()
    }


async def test_a_closed_members_handle_is_not_handed_out_again(cockpit: Cockpit) -> None:
    w = cockpit.world
    w.store.set_labels(w.id("pl"), {F.LABEL_HANDLE: "nova"})
    w.store.set_labels(w.id("pl"), {CLOSED_LABEL_KEY: CLOSED_LABEL_VALUE})
    w.make_session(
        "pl2",
        agent="pl",
        title="PL · api-version",
        labels={
            F.LABEL_ID: cockpit.fid,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: "api-version",
            F.LABEL_STATE: "active",
        },
    )
    raw = await snapshot.get_snapshot(cockpit.app.state, w.get("lead_a"))

    shown = {m["session_id"]: m["handle"] for m in raw.body["members"]}
    assert shown[w.id("pl2")] != "nova"


async def test_fleet_rows_carry_handle_vendor_and_model(cockpit: Cockpit) -> None:
    w = cockpit.world
    w.store.set_labels(w.id("ic"), {F.LABEL_HANDLE: "rex"})
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/agents", headers={"X-Test-User": "alice"}
    )
    assert resp.status_code == 200, resp.text
    rows = {r["session_id"]: r for r in resp.json()["data"]}

    assert rows[w.id("ic")]["handle"] == "rex"
    assert rows[w.id("ic")]["vendor"] == "claude"
    assert rows[w.id("reviewer")]["vendor"] == "codex"
    assert rows[w.id("ic")]["stuck"] is False and "stuck" not in rows[w.id("ic")]["flags"]


async def test_an_agent_is_found_by_its_handle(cockpit: Cockpit) -> None:
    w = cockpit.world
    w.store.set_labels(w.id("ic"), {F.LABEL_HANDLE: "rex"})
    w.store.set_labels(w.id("lead_b"), {F.LABEL_HANDLE: "sentinel"})

    for ref in ("rex", "@rex", "REX"):
        resp = await cockpit.client.get(
            f"/v1/factory/factories/{cockpit.fid}/agents/{ref}",
            headers={"X-Test-User": "carol"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["agent"]["session_id"] == w.id("ic")

    # carol cannot read Lead B: its handle answers like an unknown one.
    hidden = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/agents/sentinel", headers={"X-Test-User": "carol"}
    )
    unknown = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/agents/nobody", headers={"X-Test-User": "carol"}
    )
    assert hidden.status_code == unknown.status_code == 404
