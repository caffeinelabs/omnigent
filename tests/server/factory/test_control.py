"""``POST /v1/factory/factories/{fid}/control``: labels, texts, ordering,
cadence, relaunch, access, audit events, acks and keep_previews."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.factory import events
from omnigent.server.factory.launch import spec_launch_args
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit, error_code
from tests.server.factory.conftest import FactoryWorld, _register_agent
from tests.server.helpers import build_agent_bundle

HEAD = "9be04c1" + "a" * 33


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in build_cockpit(world, db_uri, monkeypatch):
        yield value


async def _control(
    cockpit: Cockpit,
    action: str,
    target: str = "project:api-version",
    *,
    user: str = "alice",
    note: str | None = None,
    options: dict[str, Any] | None = None,
) -> Any:
    body: dict[str, Any] = {"action": action, "target": target}
    if note is not None:
        body["note"] = note
    if options is not None:
        body["options"] = options
    return await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/control", body, user=user)


def _labels(cockpit: Cockpit, key: str) -> dict[str, str]:
    return cockpit.world.get(key).labels


# ── Per-action labels and texts ────────────────────────────────


async def test_pause_keeps_previews_by_default(cockpit: Cockpit) -> None:
    pl = cockpit.world.id("pl")

    resp = await _control(cockpit, "pause", note="Wait for the migration answer.")

    assert resp.status_code == 202, resp.text
    labels = _labels(cockpit, "pl")
    assert labels[F.LABEL_STATE] == "paused"
    assert labels[F.LABEL_KEEP_PREVIEWS] == "1"
    [item] = cockpit.mailer.to(pl)
    assert item.restart is False
    assert item.text == (
        "[Factory control · PAUSE · by alice] Pause the project: stop dispatching, cancel "
        "running ICs, take the dev env DOWN, keep your factory previews up for human review, "
        'checkpoint plan.md, then reply once: sys_session_post(to="parent", kind="reply") and '
        "wait for RESUME.\nWait for the migration answer."
    )
    [event] = resp.json()["events"]
    assert event["kind"] == "control.pause"
    assert event["data"]["by"] == "alice"
    assert event["data"]["note"] == "Wait for the migration answer."
    assert event["data"]["options"] == {"keep_previews": True}
    assert resp.json()["mail_ids"] == {pl: item.mail_id}


async def test_pause_with_null_keep_previews_keeps_them(cockpit: Cockpit) -> None:
    resp = await _control(cockpit, "pause", options={"keep_previews": None})

    assert resp.status_code == 202, resp.text
    assert _labels(cockpit, "pl")[F.LABEL_KEEP_PREVIEWS] == "1"
    assert resp.json()["events"][0]["data"]["options"] == {"keep_previews": True}


async def test_pause_can_tear_down_previews(cockpit: Cockpit) -> None:
    resp = await _control(cockpit, "pause", options={"keep_previews": False})

    assert resp.status_code == 202
    assert _labels(cockpit, "pl")[F.LABEL_KEEP_PREVIEWS] == "0"
    [text] = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    assert "tear down your factory previews (env_keeper PREVIEW_DOWN" in text


@pytest.mark.parametrize(
    ("action", "state", "header", "restart"),
    [
        (
            "resume",
            "active",
            "[Factory control · RESUME · by alice] Resume where you stopped",
            False,
        ),
        ("stop", "done", "[Factory control · STOP · by alice] Stop the project", False),
        ("restart", "active", "[Factory control · RESTART · by alice] You were restarted", True),
        (
            "env_status",
            "active",
            "[Factory control · ENV STATUS · by alice] Report env status",
            False,
        ),
    ],
)
async def test_project_actions_write_labels_and_texts(
    cockpit: Cockpit, action: str, state: str, header: str, restart: bool
) -> None:
    pl = cockpit.world.id("pl")

    resp = await _control(cockpit, action)

    assert resp.status_code == 202, resp.text
    assert _labels(cockpit, "pl")[F.LABEL_STATE] == state
    [item] = cockpit.mailer.to(pl)
    assert item.text.startswith(header)
    assert item.restart is restart
    assert [e.kind for e in cockpit.events() if e.kind.startswith("control.")] == [
        f"control.{action}"
    ]


async def test_every_action_notifies_the_root_unless_it_got_the_control(cockpit: Cockpit) -> None:
    root = cockpit.fid

    await _control(cockpit, "pause", note="lunch")
    [notice] = cockpit.mailer.texts_to(root)
    assert notice == "[Factory notice · alice paused project api-version via cockpit]\nlunch"

    await _control(cockpit, "pause", target=f"member:{root}")
    assert len(cockpit.mailer.texts_to(root)) == 2
    assert cockpit.mailer.texts_to(root)[-1].startswith("[Factory control · PAUSE · by alice]")


async def test_preview_up_and_down(cockpit: Cockpit) -> None:
    pl = cockpit.world.id("pl")
    pr, _ = cockpit.store.upsert_pr(
        cockpit.fid, "caffeinelabs/app", 9653, project_slug="api-version", head_sha=HEAD
    )

    up = await _control(cockpit, "preview", options={"pr": 9653})
    down = await _control(cockpit, "preview", options={"pr_id": pr.id, "mode": "down"})

    assert up.status_code == 202 and down.status_code == 202, (up.text, down.text)
    first, second = cockpit.mailer.texts_to(pl)
    assert first.startswith(
        "[Factory control · PREVIEW pr=#9653 · by alice] Deploy the factory preview of PR #9653 "
        f"at its head 9be04c1: send env_keeper PREVIEW pr=9653 head={HEAD}"
    )
    assert second.startswith(
        "[Factory control · PREVIEW_DOWN pr=#9653 · by alice] Take down the factory preview of "
        "PR #9653: send env_keeper PREVIEW_DOWN pr=9653 reason=human."
    )
    notices = cockpit.mailer.texts_to(cockpit.fid)
    assert notices == [
        "[Factory notice · alice previewed PR #9653 in project api-version via cockpit]",
        "[Factory notice · alice stopped the preview of PR #9653 in project api-version "
        "via cockpit]",
    ]
    [event, _] = cockpit.events("control.preview")
    assert event.subject == "pr:caffeinelabs/app#9653"
    assert event.data["options"] == {"mode": "up", "pr_id": pr.id, "pr": 9653}


async def test_preview_refusals(cockpit: Cockpit) -> None:
    cockpit.store.upsert_pr(
        cockpit.fid, "caffeinelabs/app", 9001, project_slug="api-version", state="merged"
    )

    no_pr = await _control(cockpit, "preview")
    unknown = await _control(cockpit, "preview", options={"pr": 1})
    merged = await _control(cockpit, "preview", options={"pr": 9001})
    factory = await _control(cockpit, "preview", "factory", options={"pr": 9001})
    lead = await _control(cockpit, "preview", f"member:{cockpit.fid}", options={"pr": 9001})

    assert (no_pr.status_code, error_code(no_pr)) == (400, "invalid_option")
    assert (unknown.status_code, error_code(unknown)) == (404, "unknown_pr")
    assert (merged.status_code, error_code(merged)) == (409, "pr_not_open")
    assert (factory.status_code, error_code(factory)) == (400, "invalid_target")
    assert (lead.status_code, error_code(lead)) == (400, "invalid_target")
    assert cockpit.mailer.items == []
    # Taking a merged PR's preview down is still allowed.
    assert (
        await _control(cockpit, "preview", options={"pr": 9001, "mode": "down"})
    ).status_code == 202


# ── Factory-wide ordering and cadence ──────────────────────────


async def test_factory_pause_orders_pls_then_sentinel_then_atlas_and_resume_reverses(
    cockpit: Cockpit,
) -> None:
    world = cockpit.world
    order = [world.id("pl"), world.id("lead_b"), world.id("lead_a")]

    paused = await _control(cockpit, "pause", "factory")

    assert paused.status_code == 202, paused.text
    assert [i.target_id for i in cockpit.mailer.items] == order
    assert all(
        i.text.startswith("[Factory control · PAUSE · by alice]") for i in cockpit.mailer.items
    )
    for key in ("pl", "lead_b", "lead_a"):
        assert _labels(cockpit, key)[F.LABEL_STATE] == "paused"
    assert F.LABEL_KEEP_PREVIEWS not in _labels(cockpit, "lead_a")
    # The root got the control itself, so no extra notice.
    assert len(cockpit.mailer.texts_to(world.id("lead_a"))) == 1
    root_text = cockpit.mailer.texts_to(world.id("lead_a"))[0]
    assert 'sys_session_post(to="supervisor", kind="reply")' in root_text
    assert paused.json()["events"][0]["data"]["recipients"] == order

    cockpit.mailer.items.clear()
    resumed = await _control(cockpit, "resume", "factory")

    assert resumed.status_code == 202
    assert [i.target_id for i in cockpit.mailer.items] == list(reversed(order))
    for key in ("pl", "lead_b", "lead_a"):
        assert _labels(cockpit, key)[F.LABEL_STATE] == "active"


async def test_factory_resume_reaches_only_paused_members(cockpit: Cockpit) -> None:
    world = cockpit.world
    await _control(cockpit, "pause")
    cockpit.mailer.items.clear()

    resp = await _control(cockpit, "resume", "factory")

    assert resp.status_code == 202
    assert [i.target_id for i in cockpit.mailer.items if "RESUME" in i.text] == [world.id("pl")]
    # The root was not resumed itself, so it gets the info notice.
    assert cockpit.mailer.texts_to(world.id("lead_a")) == [
        "[Factory notice · alice resumed the factory via cockpit]"
    ]
    again = await _control(cockpit, "resume", "factory")
    assert (again.status_code, error_code(again)) == (409, "nothing_to_do")


@pytest.mark.parametrize("by", ["member", "project"])
async def test_resuming_a_stopped_project_revives_it(cockpit: Cockpit, by: str) -> None:
    pl = cockpit.world.id("pl")
    await _control(cockpit, "stop")
    assert _labels(cockpit, "pl")[F.LABEL_STATE] == "done"
    target = f"member:{pl}" if by == "member" else "project:api-version"

    resp = await _control(cockpit, "resume", target)

    assert resp.status_code == 202, resp.text
    assert _labels(cockpit, "pl")[F.LABEL_STATE] == "active"
    assert cockpit.mailer.texts_to(pl)[-1].startswith("[Factory control · RESUME · by alice]")


async def test_only_resume_reaches_a_stopped_project(cockpit: Cockpit) -> None:
    cockpit.store.upsert_project(cockpit.fid, "api-version", title="API version")
    await _control(cockpit, "stop")

    pause = await _control(cockpit, "pause")

    assert (pause.status_code, error_code(pause)) == (409, "no_project_lead")


async def test_factory_stop_skips_done_members(cockpit: Cockpit) -> None:
    world = cockpit.world
    world.store.set_labels(world.id("lead_b"), {F.LABEL_STATE: F.STATE_DONE})

    resp = await _control(cockpit, "stop", "factory")

    assert resp.status_code == 202
    assert [i.target_id for i in cockpit.mailer.items] == [world.id("pl"), world.id("lead_a")]
    assert _labels(cockpit, "pl")[F.LABEL_STATE] == "done"
    assert _labels(cockpit, "lead_a")[F.LABEL_STATE] == "done"


@pytest.mark.parametrize(
    ("cadence", "lead_s", "pl_s"), [("normal", 3600, 7200), ("demo", 300, 600)]
)
async def test_cadence_sets_heartbeats_and_the_root_label(
    cockpit: Cockpit, cadence: str, lead_s: int, pl_s: int
) -> None:
    resp = await _control(cockpit, "cadence", "factory", options={"cadence": cadence})

    assert resp.status_code == 202, resp.text
    assert _labels(cockpit, "lead_a")[F.LABEL_HEARTBEAT_S] == str(lead_s)
    assert _labels(cockpit, "lead_b")[F.LABEL_HEARTBEAT_S] == str(lead_s)
    assert _labels(cockpit, "pl")[F.LABEL_HEARTBEAT_S] == str(pl_s)
    assert _labels(cockpit, "lead_a")[F.LABEL_CADENCE] == cadence
    assert F.LABEL_HEARTBEAT_S not in _labels(cockpit, "other_lead")
    # No member text; the root gets the info notice only.
    assert [i.target_id for i in cockpit.mailer.items] == [cockpit.fid]
    assert cockpit.mailer.items[0].text == (
        f"[Factory notice · alice updated the cadence of the factory to {cadence} via cockpit]"
    )


async def test_cadence_refusals(cockpit: Cockpit) -> None:
    bad = await _control(cockpit, "cadence", "factory", options={"cadence": "turbo"})
    project = await _control(cockpit, "cadence", options={"cadence": "demo"})

    assert (bad.status_code, error_code(bad)) == (400, "invalid_option")
    assert (project.status_code, error_code(project)) == (400, "invalid_target")


# ── Relaunch ───────────────────────────────────────────────────


def _bypass_pl(world: FactoryWorld, tmp_path: Path) -> str:
    from omnigent.stores.artifact_store.local import LocalArtifactStore

    artifacts = LocalArtifactStore(str(tmp_path / "artifacts"))
    executor = {
        "type": "omnigent",
        "config": {
            "harness": "claude-native",
            "permission_mode": "bypassPermissions",
            "factory_role": "project_lead",
        },
    }
    bundle = build_agent_bundle("factory-pl-bypass", executor=executor, include_llm=False)
    world.agents["bypass_pl"] = _register_agent(
        world.agent_store, artifacts, "factory-pl-bypass", bundle
    )
    pl = world.make_session(
        "pl_db",
        agent="bypass_pl",
        title="PL · db-version",
        labels={
            F.LABEL_ID: world.id("lead_a"),
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: "db-version",
            F.LABEL_STATE: "active",
        },
    )
    world.store.update_conversation(pl.id, terminal_launch_args=["--permission-mode", "auto"])
    return pl.id


async def test_relaunch_writes_the_spec_launch_args_then_restarts(
    cockpit: Cockpit, tmp_path: Path
) -> None:
    pl_id = _bypass_pl(cockpit.world, tmp_path)
    cockpit.permissions.grant("alice", pl_id, 4)
    expected = spec_launch_args(cockpit.world.get("pl_db"), cockpit.deps)
    assert expected == ["--permission-mode", "bypassPermissions"]

    resp = await _control(cockpit, "relaunch", "project:db-version")

    assert resp.status_code == 202, resp.text
    assert cockpit.world.get("pl_db").terminal_launch_args == expected
    [item] = cockpit.mailer.to(pl_id)
    assert item.restart is True
    assert item.text.startswith(
        "[Factory control · RELAUNCHED · by alice] You were relaunched with your spec's launch "
        "args (--permission-mode bypassPermissions)."
    )


async def test_relaunch_refuses_sub_agents_and_specs_without_args(cockpit: Cockpit) -> None:
    ic = await _control(cockpit, "relaunch", f"member:{cockpit.world.id('ic')}")
    no_args = await _control(cockpit, "relaunch")

    assert (ic.status_code, error_code(ic)) == (400, "top_level_only")
    assert (no_args.status_code, error_code(no_args)) == (409, "no_spec_launch_args")
    assert cockpit.world.get("pl").terminal_launch_args is None
    assert cockpit.mailer.items == []


# ── Access ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("user", "action", "target", "status"),
    [
        ("carol", "pause", "project:api-version", 202),
        ("carol", "resume", "project:api-version", 202),
        ("carol", "env_status", "project:api-version", 202),
        ("carol", "stop", "project:api-version", 202),
        ("carol", "restart", "project:api-version", 403),
        ("carol", "relaunch", "project:api-version", 403),
        ("carol", "stop", "factory", 403),
        ("alice", "restart", "project:api-version", 202),
        ("alice", "stop", "factory", 202),
        ("bob", "pause", "project:api-version", 404),
        ("bob", "pause", "factory", 404),
    ],
)
async def test_edit_vs_owner_matrix(
    cockpit: Cockpit, user: str, action: str, target: str, status: int
) -> None:
    resp = await _control(cockpit, action, target, user=user)

    assert resp.status_code == status, resp.text
    if status != 202:
        assert cockpit.mailer.items == []
        assert _labels(cockpit, "pl")[F.LABEL_STATE] == "active"
        assert [e for e in cockpit.events() if e.kind.startswith("control.")] == []


async def test_factory_wide_edit_needs_every_member(cockpit: Cockpit) -> None:
    resp = await _control(cockpit, "pause", "factory", user="carol")

    assert resp.status_code in (403, 404)
    assert cockpit.mailer.items == []
    assert _labels(cockpit, "pl")[F.LABEL_STATE] == "active"


async def test_only_cadence_may_omit_the_target(cockpit: Cockpit) -> None:
    path = f"/v1/factory/factories/{cockpit.fid}/control"

    pause = await cockpit.post(path, {"action": "pause"})
    blank = await cockpit.post(path, {"action": "resume", "target": " "})
    cadence = await cockpit.post(path, {"action": "cadence", "options": {"cadence": "normal"}})

    assert (pause.status_code, error_code(pause)) == (400, "invalid_target")
    assert pause.json()["field"] == "target"
    assert (blank.status_code, error_code(blank)) == (400, "invalid_target")
    assert cadence.status_code == 202, cadence.text
    assert cadence.json()["events"][0]["data"]["target"] == "factory"
    for key in ("pl", "lead_b", "lead_a"):
        assert _labels(cockpit, key)[F.LABEL_STATE] == "active"


async def test_target_and_action_validation(cockpit: Cockpit) -> None:
    other = cockpit.world.id("other_lead")
    cockpit.store.upsert_project(cockpit.fid, "queued-only", title="Queued")

    bad_action = await _control(cockpit, "explode")
    bad_target = await _control(cockpit, "pause", "everything")
    unknown = await _control(cockpit, "pause", "project:nope")
    no_pl = await _control(cockpit, "pause", "project:queued-only")
    foreign = await _control(cockpit, "pause", f"member:{other}")
    long_note = await _control(cockpit, "pause", note="x" * 2001)
    restart_all = await _control(cockpit, "restart", "factory")
    not_root = await cockpit.post(
        f"/v1/factory/factories/{cockpit.world.id('pl')}/control", {"action": "pause"}
    )

    assert (bad_action.status_code, error_code(bad_action)) == (400, "invalid_action")
    assert (bad_target.status_code, error_code(bad_target)) == (400, "invalid_target")
    assert (unknown.status_code, error_code(unknown)) == (404, "unknown_project")
    assert (no_pl.status_code, error_code(no_pl)) == (409, "no_project_lead")
    assert (foreign.status_code, error_code(foreign)) == (404, "unknown_member")
    assert (long_note.status_code, error_code(long_note)) == (400, "note_too_long")
    assert (restart_all.status_code, error_code(restart_all)) == (400, "invalid_target")
    assert (not_root.status_code, error_code(not_root)) == (404, "not_a_factory")


# ── Audit and ack ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("action", "target", "options"),
    [
        ("pause", "project:api-version", None),
        ("resume", "project:api-version", None),
        ("stop", "project:api-version", None),
        ("restart", "project:api-version", None),
        ("relaunch", "project:db-version", None),
        ("env_status", "project:api-version", None),
        ("cadence", "factory", {"cadence": "normal"}),
        ("preview", "project:api-version", {"pr": 9653}),
    ],
)
async def test_one_audit_event_per_action(
    cockpit: Cockpit,
    tmp_path: Path,
    action: str,
    target: str,
    options: dict[str, Any] | None,
) -> None:
    cockpit.store.upsert_pr(
        cockpit.fid, "caffeinelabs/app", 9653, project_slug="api-version", head_sha=HEAD
    )
    if action == "relaunch":
        cockpit.permissions.grant("alice", _bypass_pl(cockpit.world, tmp_path), 4)

    resp = await _control(cockpit, action, target, note="why", options=options)

    assert resp.status_code == 202, resp.text
    [event] = [e for e in cockpit.events() if e.kind.startswith("control.")]
    assert event.kind == f"control.{action}"
    assert event.severity == "notice"
    assert event.data["by"] == "alice"
    assert event.data["note"] == "why"
    assert event.data["from"] == "alice"
    assert resp.json()["events"][0]["id"] == event.id


async def _reply(cockpit: Cockpit, sender: str, kind: str = "reply") -> None:
    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "message.queued",
        from_id=sender,
        to_id=cockpit.fid,
        summary="reply",
        data={"mail_id": "mail_x", "kind": kind, "sender_id": sender},
    )


async def test_a_reply_from_the_target_acks_the_control_once(cockpit: Cockpit) -> None:
    world = cockpit.world
    pl = world.id("pl")
    resp = await _control(cockpit, "pause")
    control_id = resp.json()["events"][0]["id"]

    await _reply(cockpit, pl, kind="info")
    await _reply(cockpit, world.id("lead_b"))
    assert cockpit.events("control.ack") == []

    await _reply(cockpit, pl)
    await _reply(cockpit, pl)

    [ack] = cockpit.events("control.ack")
    assert ack.from_id == pl
    assert ack.data["control_event_id"] == control_id
    assert ack.data["action"] == "pause"
    assert ack.project_slug == "api-version"


async def test_factory_wide_acks_are_per_member(cockpit: Cockpit) -> None:
    world = cockpit.world
    await _control(cockpit, "pause", "factory")

    await _reply(cockpit, world.id("pl"))
    await _reply(cockpit, world.id("lead_b"))

    acks = cockpit.events("control.ack")
    assert sorted(a.from_id for a in acks) == sorted([world.id("pl"), world.id("lead_b")])


async def test_cadence_is_not_acked(cockpit: Cockpit) -> None:
    await _control(cockpit, "cadence", "factory", options={"cadence": "normal"})

    await _reply(cockpit, cockpit.world.id("pl"))

    assert cockpit.events("control.ack") == []


# ── The root's reply address ───────────────────────────────────


def _solo_root(cockpit: Cockpit) -> str:
    """Drop mutual supervision and Sentinel, as right after a factory start."""
    world = cockpit.world
    world.store.delete_label(cockpit.fid, F.LABEL_SUPERVISOR)
    world.store.set_labels(world.id("lead_b"), {F.LABEL_STATE: F.STATE_DONE})
    return cockpit.fid


async def test_a_root_without_supervisor_replies_to_its_peer_lead(cockpit: Cockpit) -> None:
    root = cockpit.fid
    cockpit.world.store.delete_label(root, F.LABEL_SUPERVISOR)

    resp = await _control(cockpit, "pause", f"member:{root}")

    assert resp.status_code == 202, resp.text
    [text] = cockpit.mailer.texts_to(root)
    assert 'sys_session_post(to="peer_lead", kind="reply")' in text
    assert "ack_on_any_post" not in resp.json()["events"][0]["data"]


@pytest.mark.parametrize("action", ["pause", "resume", "stop", "restart"])
async def test_a_solo_root_gets_no_reply_instruction(cockpit: Cockpit, action: str) -> None:
    root = _solo_root(cockpit)
    if action == "resume":
        cockpit.world.store.set_labels(root, {F.LABEL_STATE: F.STATE_PAUSED})

    resp = await _control(cockpit, action, f"member:{root}")

    assert resp.status_code == 202, resp.text
    [text] = cockpit.mailer.texts_to(root)
    assert "sys_session_post" not in text
    assert "; ." not in text and ", ," not in text and "then and" not in text
    assert resp.json()["events"][0]["data"]["ack_on_any_post"] == [root]


async def test_a_solo_root_acks_with_its_next_post_of_any_kind(cockpit: Cockpit) -> None:
    root = _solo_root(cockpit)
    pl = cockpit.world.id("pl")
    resp = await _control(cockpit, "pause", "factory")
    assert resp.status_code == 202, resp.text
    control_id = resp.json()["events"][0]["id"]
    root_text = cockpit.mailer.texts_to(root)[0]
    assert root_text == (
        "[Factory control · PAUSE · by alice] Pause: stop spawning and dispatching, checkpoint "
        "your ledger, then wait for RESUME."
    )

    # A PL's non-reply post still does not ack; the root's does.
    await _reply(cockpit, pl, kind="info")
    assert cockpit.events("control.ack") == []
    await _reply(cockpit, root, kind="request")
    await _reply(cockpit, root, kind="info")

    [ack] = cockpit.events("control.ack")
    assert ack.from_id == root
    assert ack.data["control_event_id"] == control_id


async def test_a_root_with_a_supervisor_still_acks_only_on_reply(cockpit: Cockpit) -> None:
    root = cockpit.fid
    await _control(cockpit, "pause", f"member:{root}")

    await _reply(cockpit, root, kind="info")
    assert cockpit.events("control.ack") == []
    await _reply(cockpit, root)

    assert [a.from_id for a in cockpit.events("control.ack")] == [root]
