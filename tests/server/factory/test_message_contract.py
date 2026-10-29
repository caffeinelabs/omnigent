"""The server's factory texts and the web transcript parser agree.

Every ``[Factory …]`` / ``[NEEDS HUMAN …]`` text the server stamps is rendered
here with its real renderer and compared to
``web/src/lib/__fixtures__/factoryServerTexts.json``; the web test
``factoryServerTexts.test.ts`` parses each entry into its typed card. A changed
renderer fails here until the fixture is regenerated with
``OMNIGENT_UPDATE_FACTORY_TEXTS=1``, and then fails on the web side if the
parser no longer reads it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryDecision, FactoryPr
from omnigent.server.factory import control, decisions, prs, start
from omnigent.server.factory.background.env_prober import Hit, Probe, _gone_notice
from omnigent.server.factory.envelope import format_envelope
from omnigent.server.factory.mailer import bounce_text
from omnigent.server.factory.notify import failed_alert_text, reply_notice_text
from omnigent.server.factory.previews import ttl_notice_text
from omnigent.server.factory.supervisor import (
    blocked_escalation_text,
    dead_alert_text,
    heartbeat_text,
    stuck_alert_text,
)

FIXTURE = Path(__file__).resolve().parents[3] / "web/src/lib/__fixtures__/factoryServerTexts.json"
UPDATE_ENV = "OMNIGENT_UPDATE_FACTORY_TEXTS"

ATLAS = "f4f4ba9c77725331b12f0fa7072dc4d3"
PL = "b408c5f09ff659e6b907fd9f688829e9"
HEAD = "7767f0d1c2a94b8e3f50d6a71b2c9e84f03d5a16"
NOW = 1793017800.0


def _conv(conv_id: str, title: str) -> Conversation:
    return Conversation(
        id=conv_id, created_at=0, updated_at=0, title=title, root_conversation_id=None
    )


def _pr(**fields: Any) -> FactoryPr:
    base: dict[str, Any] = {
        "id": "f74d5967c53455139e85ba1b402a5bd4",
        "factory_id": ATLAS,
        "repo": "caffeinelabs/app",
        "number": 9630,
        "url": "https://github.com/caffeinelabs/app/pull/9630",
        "head_ref": "factory/api-version/version-endpoint",
        "head_sha": HEAD,
        "project_slug": "api-version",
        "preview": {"name": "p9630", "state": "ready"},
    }
    return FactoryPr(**{**base, **fields})


def _decision() -> FactoryDecision:
    return FactoryDecision(
        id="039a2b237a60529588e6676f6ea0aa66",
        factory_id=ATLAS,
        number=3,
        question="Where should the ENV badge go?",
    )


def render_texts() -> list[dict[str, Any]]:
    """Every server text kind, with what the web parser must read from it."""
    pl = _conv(PL, "PL · api-version")
    pr = _pr()
    decision = _decision()
    app_hit, api_hit = Hit(code=None, ms=3000, connect_error=True), Hit(code=502, ms=40)
    probe = Probe(app=app_hit, api=api_hit, sha=None, hits=(app_hit, api_hit))
    texts: list[tuple[str, str, dict[str, Any]]] = [
        (
            "envelope",
            format_envelope(
                sender_id=PL,
                sender_title="PL · api-version",
                sender_role="project_lead",
                sender_project="api-version",
                kind="request",
                body="VERIFY #9630 @7767f0d",
                mail_id="mail_0123456789abcdef",
            ),
            {"kind": "message", "msgKind": "request", "mailId": "mail_0123456789abcdef"},
        ),
        (
            "brief",
            start.brief_header("Luka", "mail_00000000000000aa") + "\nStart the factory.",
            {"kind": "brief", "by": "Luka", "mailId": "mail_00000000000000aa"},
        ),
        ("heartbeat", heartbeat_text(12, NOW), {"kind": "heartbeat", "seq": 12}),
        (
            "heartbeat_delta",
            heartbeat_text(13, NOW, "2 changes since #12: • PR #9630 ready • D-3 answered"),
            {"kind": "heartbeat", "seq": 13, "deltaSince": 12},
        ),
        (
            "control_pause",
            f"{control.control_header('PAUSE', 'Luka')} Pause the project.",
            {"kind": "control", "action": "PAUSE", "by": "Luka"},
        ),
        (
            "control_preview",
            f"{control.control_header('PREVIEW pr=#9630', 'Luka')} Deploy the preview.",
            {"kind": "control", "action": "PREVIEW", "pr": 9630, "by": "Luka"},
        ),
        (
            "control_preview_down",
            f"{control.control_header('PREVIEW_DOWN pr=#9630', 'Luka')} Take it down.",
            {"kind": "control", "action": "PREVIEW_DOWN", "pr": 9630},
        ),
        (
            "control_env_status",
            f"{control.control_header('ENV STATUS', 'Luka')} Report env status.",
            {"kind": "control", "action": "ENV STATUS"},
        ),
        (
            "control_charter_update",
            start.charter_update_text("Luka", "-goal: a\n+goal: b"),
            {"kind": "control", "action": "CHARTER UPDATE", "by": "Luka"},
        ),
        (
            "decision_answered",
            decisions.answer_text(decision, "Luka", "Header, right", "Use the right side."),
            {"kind": "decision", "seq": 3, "by": "Luka", "option": "Header, right"},
        ),
        (
            "decision_withdrawn",
            decisions.withdraw_text(decision, "Luka", "No longer needed."),
            {"kind": "decision", "seq": 3, "withdrawn": True},
        ),
        (
            "decision_info_copy",
            decisions.info_copy_text(decision, "Luka", "Header, right", pl),
            {"kind": "notice", "decisionId": "D-3"},
        ),
        (
            "review_changes_cockpit",
            control.review_text(pr, "changes_requested", "Luka", "Make it uppercase."),
            {"kind": "review", "verdict": "changes_requested", "pr": 9630, "source": "cockpit"},
        ),
        (
            "review_approved",
            control.review_text(pr, "approved", "Luka", None),
            {"kind": "review", "verdict": "approved", "pr": 9630},
        ),
        (
            "review_changes_github",
            prs.changes_requested_notice(9630, HEAD, "luka", "Please rename it."),
            {"kind": "review", "verdict": "changes_requested", "pr": 9630, "source": "GitHub"},
        ),
        (
            "review_approved_github",
            prs.approved_notice(9630, HEAD, "luka", "LGTM after all."),
            {"kind": "review", "verdict": "approved", "pr": 9630, "source": "GitHub"},
        ),
        (
            "notice_change_request_dismissed",
            prs.dismissed_notice(pr, HEAD, "luka"),
            {"kind": "notice", "pr": 9630},
        ),
        ("notice_ready", prs.ready_notice(pr, 6), {"kind": "notice", "pr": 9630}),
        (
            "notice_checks_failed",
            prs.checks_failed_notice(pr, ["lint", "unit"]),
            {"kind": "notice", "pr": 9630},
        ),
        (
            "notice_merged",
            prs.merged_notice(_pr(merged_by="luka", merged_sha="9c8b7a6f5e4d3c2b1a0f")),
            {"kind": "notice", "pr": 9630},
        ),
        (
            "notice_closed",
            prs.closed_notice(pr, "luka", "D-4"),
            {"kind": "notice", "pr": 9630},
        ),
        ("notice_preview_ttl", ttl_notice_text(pr, 72), {"kind": "notice", "pr": 9630}),
        (
            "notice_preview_gone",
            _gone_notice(pr, "p9630", probe),
            {"kind": "notice", "pr": 9630},
        ),
        (
            "notice_control",
            control.notice_text("Luka paused project api-version", "back at 3"),
            {"kind": "notice"},
        ),
        ("notice_reply", reply_notice_text(pl, "done: PR #9630 opened"), {"kind": "notice"}),
        ("alert_failed", failed_alert_text(pl, "runner_unavailable"), {"kind": "alert"}),
        ("alert_stuck", stuck_alert_text(pl, 5520), {"kind": "alert"}),
        ("needs_human_dead", dead_alert_text(pl, "runner_unavailable"), {"kind": "needs_human"}),
        (
            "needs_human_blocked",
            blocked_escalation_text("PL · api-version", NOW - 1800, "approval for gh auth"),
            {"kind": "needs_human"},
        ),
        (
            "delivery_failure",
            bounce_text("PL · api-version", PL, "session_closed"),
            {"kind": "delivery"},
        ),
    ]
    return [{"name": name, "text": text, "expect": expect} for name, text, expect in texts]


def test_the_web_fixture_holds_every_server_text() -> None:
    rendered = render_texts()
    if os.environ.get(UPDATE_ENV) == "1":
        FIXTURE.write_text(json.dumps(rendered, ensure_ascii=False, indent=2) + "\n")
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert stored == rendered, (
        f"a server text changed: regenerate the web fixture with {UPDATE_ENV}=1 "
        "and check that web/src/lib/factoryServerTexts.test.ts still passes"
    )


def test_every_text_has_a_unique_name() -> None:
    names = [entry["name"] for entry in render_texts()]
    assert len(names) == len(set(names))
