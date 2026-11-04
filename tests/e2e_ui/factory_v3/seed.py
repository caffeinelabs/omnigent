"""
(sample) A factory shaped like a real one at 13:40 on a working day, written
into a running server's database. Not a test module.

The story is the one the level projection's contract tests tell
(``tests/server/factory/node_fixtures.py``), with neutral names, and every time
relative to now:

- a person's ask became the initiative I-1 with four wave-1 projects (C-11);
  ``assistant-core`` merged its first task (T-15, #9876) and holds T-16 for
  C-16, which waits for a person to apply it (D-45); ``assistant-panel-ux``
  has waited 7 h on ``assistant-core``; two later waves are held;
- a shortlist the person picked (C-13) became four small projects, all merged;
- an Atlas proposal (C-12) made ``build-tier-drafts-3``, whose two PRs wait for
  a person to merge them into ``main``;
- C-14 and C-15 ask things that block nothing; C-19 (D-49) proposes a follow-up
  under I-1; C-17 and C-18 are rule proposals for the factory tree, each adding
  a rule (Apply makes a definition version);
- the server closed three alarms today (D-46, D-47, D-48);
- the base env (the integration project ``base-env``) runs the base's head,
  which holds every merge, and answers its smoke checks;
- the demo agent recorded #9876 (PASS, 0:14, after a FAIL at an earlier
  commit; its card is in the PR's thread), #9881, #9882, the drafts' two PRs
  and one shortlist fix, and I-1's acceptance run 1 (PARTIAL, step 6), each
  with a poster and, where ``ffmpeg`` is installed, a recording;
- Agent assistant's later waves hold "Google Ads integration" and a sales skill.

Rows are written with the server's own stores (the same SQL tables the server
reads), and C-16 is filed through ``POST …/proposals`` so its decision (D-45)
is a real proposal decision that the answer route applies. Numbers below the
story's (C-1…C-10, D-1…D-42, T-1…T-14) are filler that no page shows.

Run it against a local server for a manual look::

    uv run python -m tests.e2e_ui.factory_v3.seed \\
        --db sqlite:////tmp/v3/test.db --base-url http://127.0.0.1:8765 --owner ana@ui.test
"""

from __future__ import annotations

import argparse
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx
from sqlalchemy import create_engine, update

from omnigent import factory as F
from omnigent.db.db_models import (
    SqlFactoryDecision,
    SqlFactoryDemo,
    SqlFactoryIssue,
    SqlFactoryProject,
    SqlFactoryProposal,
)
from omnigent.entities.factory import EventDraft
from omnigent.server.auth import LEVEL_OWNER, RESERVED_USER_LOCAL
from omnigent.server.factory.actor import pr_thread
from omnigent.server.factory.base import (
    BaseHead,
    DeployedHead,
    deployed_label,
    deployed_value,
    head_label,
    head_value,
)
from omnigent.server.factory.demo_storage import demo_object_key
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store import create_artifact_store
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.factory_store.sqlalchemy_store import SqlAlchemyFactoryStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from tests.e2e_ui.factory_v3 import demo_media

REPO: Final = "sample-org/sample-app"
BASE: Final = "sample-main"
PRODUCT: Final = "Sample app (sample)"
AGENT: Final = "hello_world"
"""The agent every seeded session runs (the e2e server registers it)."""
WAVE_1: Final = ("assistant-core", "connectors-core", "skill-discovery", "assistant-panel-ux")
SHORTLIST: Final = ("ux-env-badge-copy", "ux-slash-focus", "ux-status-page", "api-request-id")
ASK: Final = (
    "I want an assistant in the app that helps with building, and later with "
    "discovery and sales too."
)
H: Final = 3600
DEPLOYED_SHA: Final = "5a3c9e1" + "0" * 33
"""The base head the base env runs (it holds every merge of the story)."""


@dataclass(frozen=True)
class Seeded:
    """
    What the seed wrote, for the test to address.

    :param fid: The factory's root session id.
    :param owner: The person who owns it (the browser's identity).
    :param apply_decision: The decision the status lines end at (``D-45``).
    :param proposal: What it applies (``C-16``).
    :param held_task: The task held for it (``T-16``).
    :param feature: The initiative (``I-1``).
    :param project: The project the task sits in (``assistant-core``).
    """

    fid: str
    owner: str
    apply_decision: str
    proposal: str
    held_task: str
    feature: str = "I-1"
    project: str = "assistant-core"


def _conversation(
    store: SqlAlchemyConversationStore,
    agent_id: str,
    title: str,
    labels: dict[str, str],
    *,
    parent: str | None = None,
    sub_agent: str | None = None,
) -> str:
    conv = store.create_conversation(
        kind="sub_agent" if parent else "default",
        title=title,
        parent_conversation_id=parent,
        agent_id=agent_id,
        sub_agent_name=sub_agent,
    )
    if labels:
        store.set_labels(conv.id, labels)
    return conv.id


def _proposal(
    fs: SqlAlchemyFactoryStore, fid: str, title: str, *, kind: str = "projects", **fields: Any
) -> Any:
    """An agent's proposal: Atlas (the root session) wrote it unless *author* says otherwise."""
    row, _ = fs.create_proposal(
        fid,
        kind=kind,
        title=title,
        author_kind=fields.pop("author_kind", "agent"),
        author=fields.pop("author", fid),
        **fields,
    )
    return row


def _decision(fs: SqlAlchemyFactoryStore, fid: str, key: str, question: str, **fields: Any) -> Any:
    row, _ = fs.create_decision(fid, dedupe_key=key, question=question, **fields)
    return row


_APPLY_OPTIONS: Final = (
    {"id": "apply", "label": "Apply", "action": "apply_proposal"},
    {"id": "edit", "label": "Edit & apply", "action": "edit_proposal"},
    {"id": "reject", "label": "Reject", "action": "reject_proposal"},
)


def _backdate(db_uri: str, model: Any, at: dict[str, float]) -> None:
    """Set ``created_at`` (and ``updated_at``) of rows by id: the stores stamp now on insert."""
    engine = create_engine(db_uri)
    try:
        with engine.begin() as conn:
            for row_id, when in at.items():
                conn.execute(
                    update(model)
                    .where(model.id == row_id)
                    .values(created_at=int(when), updated_at=int(when))
                )
    finally:
        engine.dispose()


def _set_times(db_uri: str, model: Any, at: dict[str, dict[str, float]]) -> None:
    """Set time columns of rows by id (``{id: {column: when}}``): the stores stamp now."""
    engine = create_engine(db_uri)
    try:
        with engine.begin() as conn:
            for row_id, values in at.items():
                conn.execute(
                    update(model)
                    .where(model.id == row_id)
                    .values({k: int(v) for k, v in values.items()})
                )
    finally:
        engine.dispose()


def _thread_activity(
    db_uri: str,
    fs: SqlAlchemyFactoryStore,
    fid: str,
    clock: float,
    closed: Any,
    *,
    lead: str,
) -> None:
    """
    What the factory's thread holds besides the conversation, as on a live
    factory: base moves, a member spawned, three incidents resolved days ago
    and a proposal closed yesterday (the chat folds them into its Activity
    line), and one message from the lead. Their times are backdated as a live
    factory's are (an incident resolved 6 days ago says so, not "1m ago").
    """

    def system(body: str, at: float, card: dict[str, Any]) -> None:
        fs.append_chat(
            fid,
            thread=F.THREAD_FACTORY,
            author_kind="system",
            author="server",
            body=body,
            card=card,
            created_at=int(at),
        )

    for n, (rule, summary) in enumerate(
        (
            ("cost.burn", "the factory spends $21.00/h (alert above $15/h)"),
            ("session.idle", "Sentinel running with no new activity for 16 min"),
            ("session.idle", "Atlas running with no new activity for 41 min"),
        )
    ):
        at = clock - 6 * 24 * H - n * 600
        issue, _ = fs.upsert_issue(
            fid,
            class_="incident",
            rule=rule,
            target=f"sample-{n}",
            summary=summary,
            dedupe_key=f"{rule}:sample-{n}",
            severity="warning",
            status="resolved",
            resolved_at=int(at + 20 * 60),
            resolved_by="server",
        )
        _set_times(
            db_uri,
            SqlFactoryIssue,
            {issue.id: {"first_seen": at - H, "last_seen": at, "resolved_at": at + 20 * 60}},
        )
        meta = {"kind": "issue", "severity": "warning", "reopened": 0, "reopened_at": None}
        ref = f"INC-{issue.number}"
        system(summary, at, {"type": "issue", "ref": ref, "id": issue.id, "meta": meta})
    rejected_at = clock - 23 * H
    _set_times(
        db_uri,
        SqlFactoryProposal,
        {
            closed.id: {
                "created_at": rejected_at - 2 * H,
                "updated_at": rejected_at,
                "decided_at": rejected_at,
            }
        },
    )
    system(
        f"C-{closed.number} rejected",
        rejected_at,
        {"type": "proposal", "ref": f"C-{closed.number}", "id": closed.id},
    )
    system(
        "the server spawned PL · assistant-panel-ux",
        clock - 3 * H,
        {"type": "system", "ref": None, "id": None},
    )
    moves = [
        {
            "at": int(clock - (90 - 20 * i) * 60),
            "from_sha": None,
            "to_sha": f"{i + 1:07x}",
            "pr": None,
        }
        for i in range(3)
    ]
    system(
        f"base {BASE} moved 3 times since earlier today → {moves[-1]['to_sha']}",
        clock - 50 * 60,
        {
            "type": "system",
            "ref": None,
            "id": None,
            "meta": {"kind": "base.moved", "repo": REPO, "base": BASE, "moves": moves},
        },
    )
    fs.append_chat(
        fid,
        thread=F.THREAD_FACTORY,
        author_kind="agent",
        author=lead,
        body="Wave 1 is under way: connectors-core and skill-discovery merged. "
        "assistant-core holds its next task until the change it waits on is applied.",
        created_at=int(clock - 25 * 60),
    )


def _head(number: int) -> str:
    """The head a seeded PR was at (its demos ran there)."""
    return f"{number:07x}" + "0" * 33


@dataclass(frozen=True)
class _DemoSpec:
    """One demo the seed records: its PR (0: an acceptance run), verdict, length and steps."""

    pr: int
    slug: str
    ago_s: float
    title: str
    seconds: int
    accent: str
    status: str = "PASS"
    steps: tuple[tuple[int, str, str, str | None], ...] = ()
    head: str | None = None
    run: int | None = None


def _demo_specs() -> list[_DemoSpec]:
    passed = tuple((n, f"Step {n}", "pass", None) for n in range(1, 6))
    return [
        _DemoSpec(
            0,
            "base-env",
            18 * 60,
            "Acceptance I-1 run 1 PARTIAL",
            42,
            "#c58a1a",
            status="PARTIAL",
            steps=(
                (2, "Ask how the app is doing", "pass", None),
                (3, "A flag-off user gets no assistant", "pass", None),
                (
                    6,
                    "The discovery skill runs a live web search",
                    "fail",
                    "live search fails on the base env",
                ),
            ),
            head=DEPLOYED_SHA,
            run=1,
        ),
        _DemoSpec(9882, "connectors-core", 20 * 60, "Connector core", 18, "#3b82f6"),
        _DemoSpec(9826, SHORTLIST[0], 25 * 60, "Env badge copy", 9, "#8b5cf6"),
        _DemoSpec(9881, "skill-discovery", 40 * 60, "Discovery skill", 21, "#0ea5a4"),
        _DemoSpec(9857, "build-tier-drafts-3", 2 * H + 5 * 60, "Part 2", 11, "#ef6c3a"),
        _DemoSpec(9856, "build-tier-drafts-3", 2 * H + 6 * 60, "Part 1", 9, "#ef6c3a"),
        _DemoSpec(
            9876,
            "assistant-core",
            6 * H + 55 * 60,
            "Sessions, tool registry, flagged chat API",
            14,
            "#16a34a",
            steps=passed,
        ),
        _DemoSpec(
            9876,
            "assistant-core",
            7 * H + 28 * 60,
            "Sessions and tool registry",
            12,
            "#dc2626",
            status="FAIL",
            steps=(*passed[:3], (4, "Flagged chat API", "fail", "404 on /chat")),
            head="01d9876" + "0" * 33,
        ),
    ]


def _demo_files(
    store: Any, fid: str, demo_id: str, spec: _DemoSpec, variant: int, work: Path
) -> list[dict[str, Any]]:
    """Write a demo's poster (and recording) into *store*; the demo row's file entries."""
    media = [("poster.png", "poster", "image/png", demo_media.frame(spec.accent, variant))]
    video = demo_media.recording(spec.accent, spec.seconds, work, variant)
    if video is not None:
        media.append(("demo.webm", "video", "video/webm", video))
    entries: list[dict[str, Any]] = []
    for filename, kind, content_type, content in media:
        file_id = uuid.uuid4().hex
        key = demo_object_key(fid, demo_id, file_id, filename)
        store.put(key, content)
        entries.append(
            {
                "file_id": file_id,
                "name": filename,
                "kind": kind,
                "caption": None,
                "content_type": content_type,
                "bytes": len(content),
                "filename": filename,
                "store": store.storage_location,
                "key": key,
            }
        )
    return entries


def _demos(
    db_uri: str,
    fs: SqlAlchemyFactoryStore,
    fid: str,
    clock: float,
    prs: dict[int, Any],
    owners: dict[str, str],
) -> str | None:
    """
    Record the story's demos with their files in a store next to the database
    (a server opens a recorded store by its location). :returns: #9876's
    current demo id (its card goes to the PR's thread), else ``None`` when
    the database is not a file.
    """
    if not db_uri.startswith("sqlite:///"):
        return None
    root = Path(db_uri.removeprefix("sqlite:///")).parent
    store = create_artifact_store(str(root / "demo-files"))
    work = root / "demo-media"
    times: dict[str, dict[str, float]] = {}
    core: str | None = None
    for variant, spec in enumerate(_demo_specs()):
        demo_id = uuid.uuid4().hex
        pr = prs.get(spec.pr)
        manifest: dict[str, Any] = {
            "duration_s": float(spec.seconds),
            "steps": [
                {"n": n, "title": title, "kind": "ui", "status": status, "note": note}
                for n, title, status, note in spec.steps
            ],
        }
        if spec.run is not None:
            manifest["acceptance"] = {"initiative": "I-1", "run": spec.run}
        row = fs.create_demo(
            factory_id=fid,
            project_slug=spec.slug,
            repo=REPO,
            pr_number=spec.pr,
            head_sha=spec.head or _head(spec.pr),
            session_id=owners.get(spec.slug, fid),
            created_by_session=fid,
            status=spec.status,
            title=spec.title,
            summary=f"{spec.title} (sample)",
            files=_demo_files(store, fid, demo_id, spec, variant % 4, work),
            manifest=manifest,
            pr_id=pr.id if pr is not None else None,
            demo_id=demo_id,
        )
        times[row.id] = {"created_at": clock - spec.ago_s}
        if spec.pr == 9876 and spec.status == "PASS":
            core = row.id
    _set_times(db_uri, SqlFactoryDemo, times)
    return core


def seed_factory_1340(
    db_uri: str, base_url: str, owner: str, *, now: float | None = None
) -> Seeded:
    """
    Write the 13:40 factory into the server at *base_url* (its database is *db_uri*).

    :param owner: The identity (``X-Forwarded-Email``) that owns the factory;
        ``local`` on a single-user server (no header).
    :param now: The server's clock (epoch seconds); now when ``None``.
    :returns: The refs the test addresses.
    """
    clock = time.time() if now is None else now
    conv = SqlAlchemyConversationStore(db_uri)
    agents = SqlAlchemyAgentStore(db_uri)
    perms = SqlAlchemyPermissionStore(db_uri)
    fs = SqlAlchemyFactoryStore(db_uri)
    agent = agents.get_by_name(AGENT)
    if agent is None:
        raise RuntimeError(f"the server has no {AGENT!r} agent to seed sessions with")

    # ── Sessions: Atlas (the root), Sentinel, the leads and one builder ──
    fid = _conversation(conv, agent.id, "Atlas", {})
    conv.set_labels(
        fid,
        {
            F.LABEL_ID: fid,
            F.LABEL_ROLE: F.ROLE_LEAD,
            F.LABEL_STATE: "active",
            F.LABEL_NAME: PRODUCT,
            F.LABEL_DEFAULT_REPO: REPO,
            F.LABEL_DEFAULT_BASE: BASE,
            F.LABEL_STARTED_AT: str(int(clock - 8 * H)),
        },
    )
    member = {F.LABEL_ID: fid, F.LABEL_PARENT: fid, F.LABEL_STATE: "active"}
    sentinel = _conversation(conv, agent.id, "Sentinel", {**member, F.LABEL_ROLE: F.ROLE_LEAD})
    conv.set_labels(fid, {F.LABEL_SUPERVISOR: sentinel})
    leads: dict[str, str] = {}
    for slug, title in (("assistant-core", "Juno"), ("build-tier-drafts-3", "Nova")):
        leads[slug] = _conversation(
            conv,
            agent.id,
            title,
            {
                **member,
                F.LABEL_ROLE: F.ROLE_PROJECT_LEAD,
                F.LABEL_PROJECT: slug,
                F.LABEL_SUPERVISOR: sentinel,
            },
        )
    builder = _conversation(
        conv, agent.id, "ic:T-16", {}, parent=leads["assistant-core"], sub_agent="ic"
    )
    perms.ensure_user(owner)
    for session in (fid, sentinel, *leads.values()):
        perms.grant(owner, session, LEVEL_OWNER)
    fs.upsert_person(fid, owner, role="owner", invited_by=owner, is_creator=True)
    # What the sessions spent so far: the money line and the projects' cost read it.
    conv.set_session_usage(leads["assistant-core"], {"total_cost_usd": 25.13})
    conv.set_session_usage(leads["build-tier-drafts-3"], {"total_cost_usd": 38.4})

    # ── The ask, the shortlist pick ──
    ask = fs.append_chat(
        fid,
        thread=F.THREAD_FACTORY,
        author_kind="human",
        author=owner,
        author_role="owner",
        body=ASK,
        created_at=int(clock - 7 * H - 45 * 60),
    )
    pick = fs.append_chat(
        fid,
        thread=F.THREAD_FACTORY,
        author_kind="human",
        author=owner,
        author_role="owner",
        body="Your shortlist pick: env badge copy, / to chat, status page, request id.",
        created_at=int(clock - 7 * H),
    )

    # ── Proposals C-1…C-15 (C-1…C-10 are filler nobody sees) ──
    for n in range(1, 11):
        _proposal(fs, fid, f"Earlier idea {n} (sample)", status="rejected")
    _proposal(
        fs,
        fid,
        "Agent assistant, wave 1",
        status="applied",
        result={"created": list(WAVE_1)},
        reply_to_chat=ask.id,
        on_behalf_of=owner,
    )
    _proposal(
        fs,
        fid,
        "Third round of build-tier drafts",
        status="applied",
        result={"created": ["build-tier-drafts-3"]},
    )
    _proposal(
        fs,
        fid,
        "Shortlist fixes",
        status="applied",
        result={"created": list(SHORTLIST)},
        reply_to_chat=pick.id,
        on_behalf_of=owner,
    )
    c14 = _proposal(
        fs,
        fid,
        "Update connectors-core (sample)",
        ops=[{"op": "update", "slug": "connectors-core", "patch": {"constraints": "sample"}}],
    )
    c15 = _proposal(
        fs,
        fid,
        "Update skill-discovery (sample)",
        ops=[{"op": "update", "slug": "skill-discovery", "patch": {"constraints": "sample"}}],
    )

    # ── Projects ──
    wave = {"ref": "I-1", "title": "Agent assistant (sample)", "role": "part", "wave": 1}
    work: dict[str, dict[str, Any]] = {
        "assistant-core": {
            "stage": "building",
            "priority": 0,
            "feature": "I-1",
            "pl_session_id": leads["assistant-core"],
            "charter": {
                "why": {
                    "proposal": "C-11",
                    "outcome": "Sessions, tool registry, flagged chat API",
                },
                "outcome": "The shared core the panel, connectors and skills build on",
                "budgets": {"project_usd": 150},
                "initiative": {**wave, "role": "skeleton"},
            },
        },
        "connectors-core": {
            "stage": "merged",
            "status": "done",
            "feature": "I-1",
            "charter": {
                "why": {"proposal": "C-11"},
                "depends_on": ["assistant-core"],
                "initiative": wave,
            },
        },
        "skill-discovery": {
            "stage": "merged",
            "status": "done",
            "feature": "I-1",
            "charter": {
                "why": {"proposal": "C-11"},
                "depends_on": ["assistant-core"],
                "initiative": wave,
            },
        },
        "assistant-panel-ux": {
            "stage": "queued",
            "feature": "I-1",
            "stage_changed_at": int(clock - 7 * H),
            "charter": {
                "why": {"proposal": "C-11"},
                "outcome": "The assistant panel inside the app",
                "depends_on": ["assistant-core", *SHORTLIST[:3]],
                "depends_why": {"assistant-core": "needs the session API and per-turn debit"},
                "budgets": {"project_usd": 150},
                "initiative": {**wave, "role": "surface"},
            },
        },
        "build-tier-drafts-3": {
            "stage": "ready",
            "base_branch": "main",
            "pl_session_id": leads["build-tier-drafts-3"],
            "charter": {
                "requested_by": {"kind": "agent", "name": "Atlas", "proposal": "C-12"},
            },
        },
        **{
            slug: {
                "stage": "merged",
                "status": "done",
                "charter": {
                    "requested_by": {"kind": "human", "name": "ana (sample)", "proposal": "C-13"}
                },
            }
            for slug in SHORTLIST
        },
        "base-env": {
            "stage": "planning",
            "charter": {"kind": "integration"},
            "env": {
                "reported": {
                    "state": "ready",
                    "at": int(clock - 12 * 60),
                    "urls": {"app": "https://app.example.com", "api": "https://api.example.com"},
                    "deployed": {"sha": DEPLOYED_SHA[:7]},
                    "note": "Sign in with a test account (sample).",
                },
                "probe": {
                    "health": "ok",
                    "app": {"code": 200},
                    "api": {"code": 200},
                    "at": int(clock - 60),
                },
            },
        },
    }
    made: dict[str, float] = {}
    for slug, fields in work.items():
        row = fs.upsert_project(
            fid,
            slug,
            title=slug,
            repo=REPO,
            base_branch=fields.pop("base_branch", BASE),
            created_by="server",
            **fields,
        )
        made[row.id] = clock - (7 * H if slug == "assistant-panel-ux" else 7 * H + 40 * 60)
    _backdate(db_uri, SqlFactoryProject, made)

    # ── The feature ──
    fs.create_initiative(
        fid,
        title="Agent assistant (sample)",
        ask=ASK,
        requested_by={"kind": "human", "id": owner, "name": "ana (sample)"},
        ask_ref=f"chat_{ask.id}",
        outcome="An assistant inside the app that helps users build",
        created_by="C-11",
        plan={
            "acceptance": [
                {"n": 1, "step": "A flag-on user opens the assistant panel", "owner": WAVE_1[3]},
                {
                    "n": 2,
                    "step": "Ask how the app is doing",
                    "expect": "the answer uses analytics",
                    "owner": WAVE_1[0],
                },
                {"n": 3, "step": "A flag-off user gets no assistant", "owner": WAVE_1[0]},
                {"n": 4, "step": "Each turn debits the user's credits", "owner": WAVE_1[0]},
                {"n": 5, "step": "The assistant calls a connected service", "owner": WAVE_1[1]},
                {"n": 6, "step": "The discovery skill runs a live web search", "owner": WAVE_1[2]},
            ],
            "skeleton": WAVE_1[0],
            "user_facing": [WAVE_1[3]],
            "projects": list(WAVE_1),
            "history": [
                {"wave": 1, "proposal": "C-11", "added": list(WAVE_1), "at": int(clock - 7 * H)}
            ],
            "waves": [
                {
                    "n": 2,
                    "goal": "ads network and growth",
                    "projects": ["google-ads-integration"],
                },
                {"n": 3, "goal": "sales and promotion", "projects": ["sales-skill"]},
            ],
            "asked_at": int(clock - 7 * H - 45 * 60),
        },
        runs=[
            {
                "n": 1,
                "status": "PARTIAL",
                "reported_at": int(clock - 18 * 60),
                "steps": [
                    {"n": 2, "status": "pass", "note": "demo of #9876"},
                    {"n": 3, "status": "pass", "note": "demo of #9876"},
                    {"n": 6, "status": "fail", "note": "live search fails on the base env"},
                ],
            }
        ],
    )

    # ── Tasks T-1…T-18 (T-1…T-14 are cancelled filler) and their PRs ──
    for n in range(1, 15):
        fs.upsert_task(fid, "base-env", f"filler-{n}", title=f"Filler {n}", state="cancelled")
    t15, _ = fs.upsert_task(
        fid,
        "assistant-core",
        "core",
        title="Sessions, tool registry, flagged chat API",
        state="done",
        estimate_min=120,
        session_ids=[builder],
    )
    t16, _ = fs.upsert_task(
        fid,
        "assistant-core",
        "debit",
        title="Debit each turn",
        state="ready",
        deps=["core"],
        estimate_min=60,
        session_ids=[builder],
        cost_usd=1.66,
    )
    t17, _ = fs.upsert_task(
        fid,
        "connectors-core",
        "connectors",
        title="Connector core and first connector",
        state="done",
        estimate_min=120,
    )
    t18, _ = fs.upsert_task(
        fid,
        "skill-discovery",
        "skill",
        title="Discovery skill with live search",
        state="done",
        estimate_min=120,
    )
    merged = {
        9876: ("assistant-core", t15.id, "Assistant core", clock - 6 * H - 45 * 60),
        9882: ("connectors-core", t17.id, "Connector core", clock - 15 * 60),
        9881: ("skill-discovery", t18.id, "Discovery skill", clock - 37 * 60),
        **{
            n: (slug, None, slug, clock - 15 * 60)
            for n, slug in zip((9826, 9828, 9829, 9827), SHORTLIST, strict=True)
        },
    }
    prs: dict[int, Any] = {}
    for number, (slug, task_id, title, at) in merged.items():
        pr, _ = fs.upsert_pr(
            fid,
            REPO,
            number,
            project_slug=slug,
            title=title,
            base_ref=BASE,
            state="merged",
            merged_at=int(at),
            merged_sha=f"{number:07x}" + "0" * 33,
            head_sha=_head(number),
            task_id=task_id,
        )
        prs[number] = pr
        if task_id:
            fs.update_task(task_id, pr_id=pr.id)
    for number, part in ((9856, 1), (9857, 2)):
        prs[number], _ = fs.upsert_pr(
            fid,
            REPO,
            number,
            project_slug="build-tier-drafts-3",
            title=f"build-tier-drafts-3, part {part}",
            base_ref="main",
            state="open",
            ready=True,
            ready_at=int(clock - 2 * H),
            head_sha=_head(number),
        )

    # ── Decisions D-1…D-44 (D-1…D-42 filler; D-37 an ask that blocks nothing) ──
    for n in range(1, 43):
        if n == 37:
            _decision(
                fs,
                fid,
                "sentinel-ask",
                "Keep the nightly sweep at 02:00? (sample)",
                source="agent",
                blocking=False,
                asked_by_session_id=sentinel,
                subject=f"member:{sentinel}",
            )
            continue
        row = _decision(fs, fid, f"filler-{n}", f"Earlier question {n} (sample)", source="agent")
        fs.set_decision_status(row.id, "expired")
    for proposal in (c14, c15):
        row = _decision(
            fs,
            fid,
            f"proposal:C-{proposal.number}",
            f"Atlas proposes C-{proposal.number}: {proposal.title}",
            source="proposal",
            blocking=False,
            subject=f"proposal:C-{proposal.number}",
            options=[dict(o) for o in _APPLY_OPTIONS],
        )
        fs.update_proposal(proposal.id, decision_id=row.id)

    # ── C-16 and D-45, filed through the route like a person's change ──
    resp = httpx.post(
        f"{base_url}/v1/factory/factories/{fid}/proposals",
        json={
            "changes": [
                {
                    "op": "update",
                    "slug": "assistant-core",
                    "patch": {
                        "constraints": "Debit the user's credits on each turn.",
                        "dod": ["Each turn debits the user's credits"],
                    },
                }
            ],
            "rationale": "Implements the D-42 answer: debit each turn. T-16 waits for it.",
        },
        headers={} if owner == RESERVED_USER_LOCAL else {"X-Forwarded-Email": owner},
        timeout=30.0,
    )
    resp.raise_for_status()
    filed = resp.json()
    proposal = f"C-{filed['proposal']['number']}"
    decision = str(filed["decision"])
    asked = fs.get_decision_by_number(fid, int(decision.removeprefix("D-")))
    if asked is not None:
        _backdate(db_uri, SqlFactoryDecision, {asked.id: clock - 6 * H - 14 * 60})
    fs.update_task(
        t16.id,
        hold={"on": proposal, "kind": "proposal", "ref": proposal, "until": int(clock + 86400)},
    )

    # ── D-46…D-48: alarms the server closed today ──
    real_ask = {"real_ask": decision, "real_ask_verb": f"Apply {proposal}"}
    for key, question, cause, about, slug, extra in (
        (
            "stall-skill",
            "skill-discovery: implement stalled - what next?",
            "skill-discovery merged (#9881); the stall closed with it.",
            "skill-discovery",
            "skill-discovery",
            {},
        ),
        (
            "claim-skill",
            "skill-discovery: the claim was not verified",
            "skill-discovery merged (#9881); the claim closed with it.",
            "skill-discovery",
            "skill-discovery",
            {},
        ),
        (
            "stall-debit",
            "T-16: implement 1/3 stalled - what next?",
            f"T-16 is held on purpose for {proposal}; a held task is not stalled.",
            "T-16",
            "assistant-core",
            real_ask,
        ),
    ):
        row = _decision(fs, fid, key, question, source="loop", project_slug=slug)
        fs.answer_decision(row.id, option_id=None, text=cause, by="server")
        fs.append_event(
            EventDraft(
                factory_id=fid,
                kind="decision.answered",
                summary=f"{row.ref}: {cause}",
                project_slug=slug,
                subject=f"decision:{row.ref}",
                data={
                    "decision_id": row.id,
                    "source": "loop",
                    "by_kind": "server",
                    "cause": cause,
                    "was": question,
                    "about": about,
                    **extra,
                },
                ts=int((clock - 30 * 60) * 1000),
            )
        )

    # ── C-17, C-18 (rules), C-19 (a follow-up under I-1); D-49…D-51 ──
    # Each adds a real rule, so Apply makes a definition version (no Undo token).
    rules = [
        _proposal(
            fs,
            fid,
            title,
            kind="rules",
            author=sentinel,
            ops=[{"op": "add", "path": "rules", "value": {"role": role, "text": text}}],
        )
        for title, role, text in (
            (
                "Spike unproven external capabilities first",
                F.ROLE_LEAD,
                "Spike an unproven external capability before planning work on it (sample).",
            ),
            (
                "Name pending scope decisions in READY posts",
                F.ROLE_PROJECT_LEAD,
                "Name the pending scope decision a READY post waits on (sample).",
            ),
        )
    ]
    follow_up = _proposal(
        fs,
        fid,
        "Fix live search on the base env",
        ops=[{"op": "create", "project": {"slug": "live-search-fix", "title": "Fix live search"}}],
        analysis={"initiative": {"id": "I-1"}},
    )
    for proposal_row, question in (
        (follow_up, "Start the live-search fix?"),
        *((r, f"Sentinel proposes C-{r.number}: {r.title}") for r in rules),
    ):
        ref = f"C-{proposal_row.number}"
        row = _decision(
            fs,
            fid,
            f"proposal:{ref}",
            question,
            source="proposal",
            subject=f"proposal:{ref}",
            options=[dict(o) for o in _APPLY_OPTIONS],
        )
        fs.update_proposal(proposal_row.id, decision_id=row.id)

    # ── The factory thread's system events (the chat's Activity) and a lead's message ──
    rejected = fs.list_proposals(fid, status="rejected", limit=1)[0]
    _thread_activity(db_uri, fs, fid, clock, rejected, lead=fid)

    # ── The base env: the base's head after the last merge, deployed and answering ──
    moved = int(clock - 14 * 60)
    conv.set_labels(
        fid,
        {
            head_label(REPO, BASE): head_value(BaseHead(DEPLOYED_SHA, moved), REPO, BASE),
            deployed_label(REPO, BASE): deployed_value(
                DeployedHead(DEPLOYED_SHA, int(clock - 12 * 60), moved), REPO, BASE
            ),
        },
    )

    # ── The demos (posters and recordings) and #9876's card in its PR's thread ──
    core_demo = _demos(db_uri, fs, fid, clock, prs, leads)
    if core_demo is not None:
        fs.append_chat(
            fid,
            thread=pr_thread(prs[9876].id),
            author_kind="system",
            author="server",
            body="demo PASS on #9876: sessions, tool registry, flagged chat API",
            card={"type": "demo", "ref": "!9876", "id": core_demo},
            created_at=int(clock - 6 * H - 55 * 60),
        )

    # ── An hour of work on assistant-core (its burn) ──
    fs.insert_span(
        fid,
        kind="turn",
        name="working",
        trace_key="state:juno",
        started_at=int((clock - 2400) * 1000),
        ended_at=int((clock - 600) * 1000),
        outcome="ok",
        project_slug="assistant-core",
        session_id=leads["assistant-core"],
        attrs={"segment": "working", "cost_start": 22.63, "cost_end": 25.13},
    )
    return Seeded(
        fid=fid,
        owner=owner,
        apply_decision=decision,
        proposal=proposal,
        held_task=t16.ref,
    )


def main() -> None:
    """Seed a running local server (see the module docstring)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", required=True, help="the server's --database-uri")
    parser.add_argument("--base-url", required=True, help="e.g. http://127.0.0.1:8765")
    parser.add_argument("--owner", required=True, help="the X-Forwarded-Email that owns it")
    args = parser.parse_args()
    seeded = seed_factory_1340(args.db, args.base_url, args.owner)
    print(f"{args.base_url}/factory/{seeded.fid}?cockpit=v3")
    print(f"apply {seeded.apply_decision} ({seeded.proposal}); held task {seeded.held_task}")


if __name__ == "__main__":
    main()
