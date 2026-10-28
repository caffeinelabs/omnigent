"""The autonomy matrix: which project changes apply on their own, and which wait.

Every cell of the spec's table, for each mode (``confirm``, ``requested``,
``grace``), plus how a proposal combines its ops and where the mode is read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from omnigent.server.factory import autonomy
from omnigent.server.factory.autonomy import Verdict, decide, update_class, verdict
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import header_cockpit, project, propose


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value


AUTO = Verdict("auto")
AUTO_INFO = Verdict("auto", info_line=True)
OPERATOR = Verdict("operator")
GRACE = Verdict("operator", grace=True)
OWNER = Verdict("owner")

# (op class, a human asked, the agent acted on its own) for each mode.
MATRIX: dict[str, dict[str, tuple[Verdict, Verdict]]] = {
    "requested": {
        "create": (AUTO, OPERATOR),
        "update_minor": (AUTO, AUTO_INFO),
        "update_major": (AUTO, OPERATOR),
        "structural": (OPERATOR, OPERATOR),
        "owner": (OWNER, OWNER),
    },
    "grace": {
        "create": (AUTO, GRACE),
        "update_minor": (AUTO, AUTO_INFO),
        "update_major": (AUTO, OPERATOR),
        "structural": (OPERATOR, OPERATOR),
        "owner": (OWNER, OWNER),
    },
    "confirm": {
        "create": (OPERATOR, OPERATOR),
        "update_minor": (OPERATOR, OPERATOR),
        "update_major": (OPERATOR, OPERATOR),
        "structural": (OPERATOR, OPERATOR),
        "owner": (OWNER, OWNER),
    },
}

CELLS = [
    (mode, op_class, requested, cells[0] if requested else cells[1])
    for mode, row in MATRIX.items()
    for op_class, cells in row.items()
    for requested in (True, False)
]


@pytest.mark.parametrize(("mode", "op_class", "requested", "expected"), CELLS)
def test_every_cell_of_the_matrix(
    mode: str, op_class: autonomy.OpClass, requested: bool, expected: Verdict
) -> None:
    assert verdict(op_class, requested=requested, mode=mode) == expected


def test_the_matrix_covers_every_mode_and_class() -> None:
    assert set(MATRIX) == set(autonomy.AUTONOMY_MODES)
    for row in MATRIX.values():
        assert set(row) == set(autonomy.OP_CLASSES)


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"priority"}, "update_minor"),
        ({"demo_focus", "preview", "budget_ics"}, "update_minor"),
        ({"goal"}, "update_major"),
        ({"dod", "priority"}, "update_major"),
        ({"scope_paths"}, "update_major"),
        ({"depends_on"}, "update_major"),
    ],
)
def test_update_classes(fields: set[str], expected: str) -> None:
    assert update_class(fields) == expected


def test_a_proposal_needs_its_strictest_op() -> None:
    assert decide(["create", "update_minor"], requested=True) == AUTO
    assert decide(["update_minor"], requested=False) == AUTO_INFO
    assert decide(["create", "structural"], requested=True) == OPERATOR
    assert decide(["create", "owner"], requested=True) == OWNER
    # Grace holds only when every gated op is graceful.
    assert decide(["create", "create"], requested=False, mode="grace") == GRACE
    assert decide(["create", "update_major"], requested=False, mode="grace") == OPERATOR
    assert decide([], requested=False) == OPERATOR


def test_the_mode_is_read_from_the_root_label() -> None:
    assert autonomy.autonomy_mode({}) == "requested"
    assert autonomy.autonomy_mode({autonomy.LABEL_AUTONOMY: "confirm"}) == "confirm"
    assert autonomy.autonomy_mode({autonomy.LABEL_AUTONOMY: "yolo"}) == "requested"


@pytest.mark.parametrize(
    ("mode", "status"),
    [("requested", "applied"), ("grace", "applied"), ("confirm", "pending")],
)
async def test_a_requested_create_follows_the_factory_mode(
    cockpit: Cockpit, mode: str, status: str
) -> None:
    cockpit.world.store.set_labels(cockpit.fid, {autonomy.LABEL_AUTONOMY: mode})
    chat = cockpit.store.append_chat(
        cockpit.fid, thread="factory", author_kind="human", author="alice", body="add db"
    )

    resp = await propose(
        cockpit, {"op": "create", "project": project("db-version")}, reply_to=f"chat_{chat.id}"
    )

    assert resp.json()["status"] == status, resp.text


async def test_an_old_or_agent_message_is_no_request(cockpit: Cockpit) -> None:
    agent = cockpit.store.append_chat(
        cockpit.fid, thread="factory", author_kind="agent", author="x", body="add db"
    )
    old = cockpit.store.append_chat(
        cockpit.fid,
        thread="factory",
        author_kind="human",
        author="alice",
        body="add db",
        created_at=1,
    )

    by_agent = await propose(
        cockpit, {"op": "create", "project": project("a")}, reply_to=f"chat_{agent.id}"
    )
    too_old = await propose(
        cockpit, {"op": "create", "project": project("b")}, reply_to=f"chat_{old.id}"
    )

    assert by_agent.json()["status"] == "pending"
    assert too_old.json()["status"] == "pending"


async def test_an_agent_minor_update_applies_with_an_info_line(cockpit: Cockpit) -> None:
    resp = await propose(
        cockpit, {"op": "update", "slug": "api-version", "patch": {"priority": 1}}
    )

    assert resp.json()["status"] == "applied", resp.text
    [applied] = cockpit.events("proposal.applied")
    assert applied.data["info_line"] is True


# ── Money is the owners' ───────────────────────────────────────


@pytest.mark.parametrize(
    ("before", "sent", "raises"),
    [
        ({}, {"task_usd": 5}, True),
        ({"task_usd": 5}, {"task_usd": 9}, True),
        ({"task_usd": 5}, {"task_usd": 5}, False),
        ({"task_usd": 5, "project_usd": 40}, {"task_usd": 2, "project_usd": 10}, False),
        ({"task_usd": 5, "project_usd": 40}, {"task_usd": 2}, True),
        ({"task_usd": 5}, None, True),
        ({}, None, False),
    ],
)
def test_raising_budgets(before: dict, sent: dict | None, raises: bool) -> None:
    from omnigent.server.factory.start import CharterPatchIn

    patch = CharterPatchIn.model_validate({"budgets": sent})
    assert autonomy.raises_budgets({"budgets": before}, patch) is raises
    assert autonomy.raises_budgets({"budgets": before}, CharterPatchIn(goal="g")) is False


async def test_raising_budgets_needs_an_owner_even_when_asked(cockpit: Cockpit) -> None:
    chat = cockpit.store.append_chat(
        cockpit.fid, thread="factory", author_kind="human", author="carol", body="go faster"
    )
    asked = f"chat_{chat.id}"

    raise_ = await propose(
        cockpit,
        {"op": "update", "slug": "api-version", "patch": {"budgets": {"project_usd": 99999}}},
        reply_to=asked,
    )
    create = await propose(
        cockpit,
        {"op": "create", "project": project("db-version", budgets={"task_usd": 5})},
        reply_to=asked,
    )

    assert (raise_.json()["status"], raise_.json()["approval"]) == ("pending", "owner")
    assert (create.json()["status"], create.json()["approval"]) == ("pending", "owner")
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None and "budgets" not in (row.charter or {})
    decision = cockpit.store.get_decision(
        cockpit.store.get_proposal_by_number(cockpit.fid, 1).decision_id
    )
    assert decision is not None and decision.assignee == "owners"


async def test_lowering_a_budget_is_an_ordinary_update(cockpit: Cockpit) -> None:
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None
    cockpit.store.update_project(
        row.id, charter={**(row.charter or {}), "budgets": {"task_usd": 5.0}}
    )

    resp = await propose(
        cockpit, {"op": "update", "slug": "api-version", "patch": {"budgets": {"task_usd": 2}}}
    )

    assert resp.json()["approval"] == "operator", resp.text


async def test_the_human_routes_need_manage_to_raise_budgets(cockpit: Cockpit) -> None:
    url = f"/v1/factory/factories/{cockpit.fid}/projects"
    by_operator = await cockpit.patch(
        f"{url}/api-version", {"budgets": {"project_usd": 500}}, user="carol"
    )
    add_by_operator = await cockpit.post(
        url, project("db-version", budgets={"task_usd": 5}), user="carol"
    )
    goal_by_operator = await cockpit.patch(f"{url}/api-version", {"goal": "g2"}, user="carol")
    by_owner = await cockpit.patch(f"{url}/api-version", {"budgets": {"project_usd": 500}})
    lower_by_operator = await cockpit.patch(
        f"{url}/api-version", {"budgets": {"project_usd": 100}}, user="carol"
    )

    assert by_operator.status_code == 403, by_operator.text
    assert add_by_operator.status_code == 403, add_by_operator.text
    assert goal_by_operator.status_code == 200, goal_by_operator.text
    assert by_owner.status_code == 200, by_owner.text
    assert lower_by_operator.status_code == 200, lower_by_operator.text
    assert cockpit.store.get_project(cockpit.fid, "db-version") is None


# ── Requests are scoped to their thread ────────────────────────


async def test_a_request_counts_only_in_its_thread(cockpit: Cockpit) -> None:
    chat = cockpit.store.append_chat(
        cockpit.fid,
        thread="project:api-version",
        author_kind="human",
        author="alice",
        author_role="owner",
        body="add a db project",
    )

    elsewhere = await propose(
        cockpit,
        {"op": "create", "project": project("a")},
        reply_to=f"chat_{chat.id}",
        thread="factory",
    )
    here = await propose(
        cockpit, {"op": "create", "project": project("b")}, reply_to=f"chat_{chat.id}"
    )

    assert elsewhere.json()["status"] == "pending", elsewhere.text
    assert here.json()["status"] == "applied", here.text
    applied = cockpit.store.get_proposal_by_number(cockpit.fid, 2)
    assert applied.thread == "project:api-version" and applied.on_behalf_of == "alice"


async def test_the_start_brief_request_names_the_user(cockpit: Cockpit) -> None:
    from omnigent.server.factory import events

    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "factory.started",
        summary="started",
        data={"by": "Alice Example", "by_id": "alice", "mail_id": "mail_0123456789abcdef"},
    )

    resp = await propose(
        cockpit,
        {"op": "create", "project": project("db-version")},
        reply_to="mail_0123456789abcdef",
    )

    assert resp.json()["status"] == "applied", resp.text
    assert cockpit.store.get_proposal_by_number(cockpit.fid, 1).on_behalf_of == "alice"


async def test_grace_stops_when_owners_leave_grace(cockpit: Cockpit) -> None:
    from omnigent.server.factory import proposals
    from omnigent.stores.factory_store import now_s

    cockpit.world.store.set_labels(cockpit.fid, {autonomy.LABEL_AUTONOMY: "grace"})
    await propose(cockpit, {"op": "create", "project": project("db-version")})
    cockpit.world.store.set_labels(cockpit.fid, {autonomy.LABEL_AUTONOMY: "confirm"})

    late = await proposals.apply_graceful(
        cockpit.app.state, cockpit.fid, now=now_s() + proposals.GRACE_S + 1
    )

    assert late == []
    assert cockpit.store.get_proposal_by_number(cockpit.fid, 1).status == "pending"
