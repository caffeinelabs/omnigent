"""
(sample) A factory the size of a live one, for the cockpit's read benchmark.

:func:`seed_live_factory` writes the 13:40 story (``tests/e2e_ui/factory_v3``)
and then scales it to a live factory's size: more projects with their leads and
sub-agents, tasks and PRs, a long decision and proposal history, thousands of
events and chat messages, demos and spans. :func:`build_app` mounts the factory
routes over a SQLite database the way the server does (SQL factory store, real
grants, header identities). ``dev/benchmarks/factory_cockpit.py`` and
``test_cockpit_perf.py`` read it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

from omnigent import factory as F
from omnigent.db.utils import builtin_agent_id
from omnigent.entities.factory import EventDraft
from omnigent.server.auth import LEVEL_OWNER
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.factory_store.sqlalchemy_store import SqlAlchemyFactoryStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from tests.server.helpers import build_agent_bundle

OWNER: Final = "ana@perf.test"
REPO: Final = "sample-org/sample-app"
PROPOSAL_KINDS: Final = ("projects", "rules", "tuning")
TASK_STATES: Final = ("done", "done", "running", "ready", "planned", "review", "blocked")


@dataclass(frozen=True)
class Scale:
    """How big the seeded factory is (the defaults are a live factory's size)."""

    projects: int = 20
    subagents_per_pl: int = 3
    tasks_per_project: int = 8
    prs_per_project: int = 4
    events: int = 5000
    decisions: int = 300
    proposals: int = 300
    demos: int = 30
    chat: int = 2000
    spans: int = 600


SMALL: Final = Scale(
    projects=12,
    subagents_per_pl=2,
    tasks_per_project=5,
    prs_per_project=2,
    events=1500,
    decisions=120,
    proposals=120,
    demos=12,
    chat=600,
    spans=200,
)
"""A smaller factory for the test suite's perf budget."""


class EmailAuth:
    """Header identity, as the SSO proxy hands it (``X-Forwarded-Email``)."""

    def get_user_id(self, request: Any) -> str | None:
        return request.headers.get("X-Forwarded-Email")


def _native(role: str) -> dict[str, Any]:
    return {"type": "omnigent", "config": {"harness": "claude-native", "factory_role": role}}


def build_app(db_uri: str, tmp: Path) -> Any:
    """
    The factory routes over *db_uri* as the server mounts them (plus ``/ping``,
    a request that does nothing, to see whether the event loop is free).

    The ``hello_world`` agent the 13:40 seed runs is registered.
    """
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from omnigent.errors import OmnigentError
    from omnigent.runtime.agent_cache import AgentCache
    from omnigent.server.factory import register_factory_routes

    conv = SqlAlchemyConversationStore(db_uri)
    agents = SqlAlchemyAgentStore(db_uri)
    perms = SqlAlchemyPermissionStore(db_uri)
    artifacts = LocalArtifactStore(str(tmp / "artifacts"))
    cache = AgentCache(artifacts, tmp / "agent-cache")
    if agents.get_by_name("hello_world") is None:
        agent_id = builtin_agent_id("hello_world")
        bundle = build_agent_bundle(
            "hello_world",
            executor=_native("project_lead"),
            include_llm=False,
            sub_agents=[
                {"name": "ic", "description": "implementer", "executor": _native("ic")},
                {"name": "reviewer", "description": "reviewer", "executor": _native("reviewer")},
            ],
        )
        artifacts.put(f"{agent_id}/v1", bundle)
        agents.create(agent_id, "hello_world", f"{agent_id}/v1")
    app = FastAPI()
    register_factory_routes(
        app,
        conversation_store=conv,
        agent_store=agents,
        agent_cache=cache,
        permission_store=perms,
        auth_provider=EmailAuth(),
        factory_store=SqlAlchemyFactoryStore(db_uri),
    )

    async def _omnigent_error(_request: Any, exc: Exception) -> JSONResponse:
        assert isinstance(exc, OmnigentError)
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.add_exception_handler(OmnigentError, _omnigent_error)

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    return app


def seed_live_factory(db_uri: str, app: Any, scale: Scale = Scale(), *, seed: int = 7) -> str:
    """
    Write the 13:40 factory, then grow it to *scale*. :returns: The factory id.

    The 13:40 seed files one proposal through the route; it goes to *app* in process.
    """
    import time

    from fastapi.testclient import TestClient

    from tests.e2e_ui.factory_v3 import seed as story

    client = TestClient(app)
    real_httpx = story.httpx

    def _post(url: str, **kwargs: Any) -> Any:
        path = url.split("://", 1)[-1].split("/", 1)[1]
        kwargs.pop("timeout", None)
        return client.post(f"/{path}", **kwargs)

    story.httpx = SimpleNamespace(post=_post)  # type: ignore[assignment]
    try:
        seeded = story.seed_factory_1340(db_uri, "http://bench", OWNER)
    finally:
        story.httpx = real_httpx  # type: ignore[assignment]
    _grow(db_uri, seeded.fid, scale, random.Random(seed), time.time())
    return seeded.fid


def _grow(db_uri: str, fid: str, scale: Scale, rnd: random.Random, clock: float) -> None:
    conv = SqlAlchemyConversationStore(db_uri)
    perms = SqlAlchemyPermissionStore(db_uri)
    fs = SqlAlchemyFactoryStore(db_uri)
    agent = SqlAlchemyAgentStore(db_uri).get_by_name("hello_world")
    assert agent is not None
    root = conv.get_conversation(fid)
    assert root is not None
    sentinel = root.labels.get(F.LABEL_SUPERVISOR) or fid
    member = {F.LABEL_ID: fid, F.LABEL_PARENT: fid, F.LABEL_STATE: "active"}
    for n in range(4):
        fs.upsert_person(fid, f"person{n}@perf.test", role="operator", invited_by=OWNER)
        perms.ensure_user(f"person{n}@perf.test")

    existing = {p.slug for p in fs.list_projects(fid)}
    extra = max(0, scale.projects - len(existing))
    sessions: list[str] = [fid, sentinel]
    leads: dict[str, str] = {}
    for i in range(extra):
        slug = f"perf-project-{i:02d}"
        pl = conv.create_conversation(kind="default", title=f"PL · {slug}", agent_id=agent.id)
        conv.set_labels(
            pl.id,
            {
                **member,
                F.LABEL_ROLE: F.ROLE_PROJECT_LEAD,
                F.LABEL_PROJECT: slug,
                F.LABEL_SUPERVISOR: sentinel,
                F.LABEL_HEARTBEAT_S: "600",
            },
        )
        perms.grant(OWNER, pl.id, LEVEL_OWNER)
        conv.set_session_usage(pl.id, {"total_cost_usd": round(rnd.uniform(1, 60), 2)})
        leads[slug] = pl.id
        sessions.append(pl.id)
        for k in range(scale.subagents_per_pl):
            name = ("ic", "reviewer")[k % 2]
            child = conv.create_conversation(
                kind="sub_agent",
                title=f"{name}:task-{k}",
                parent_conversation_id=pl.id,
                agent_id=agent.id,
                sub_agent_name=name,
            )
            sessions.append(child.id)
        stage = rnd.choice(["building", "planning", "ready", "reviewing", "merged"])
        fs.upsert_project(
            fid,
            slug,
            title=f"Perf project {i}",
            repo=REPO,
            base_branch="sample-main",
            created_by="server",
            stage=stage,
            priority=i % 3,
            pl_session_id=pl.id,
            feature="I-1" if i % 4 == 0 else None,
            charter={
                "outcome": f"Outcome of perf project {i}",
                "goal": "a goal",
                "dod": ["it works", "it is tested"],
                "depends_on": [f"perf-project-{i - 1:02d}"] if i % 5 == 4 else [],
                "budgets": {"project_usd": 150},
            },
        )

    pr_number = 20000
    for slug, pl in leads.items():
        keys = [f"step-{k}" for k in range(scale.tasks_per_project)]
        task_ids: list[str] = []
        for k, key in enumerate(keys):
            state = rnd.choice(TASK_STATES)
            task, _ = fs.upsert_task(
                fid,
                slug,
                key,
                title=f"{slug} {key}",
                state=state,
                deps=[keys[k - 1]] if k else [],
                estimate_min=rnd.choice([30, 60, 120]),
                session_ids=[pl],
                cost_usd=round(rnd.uniform(0, 5), 2),
            )
            task_ids.append(task.id)
        for k in range(scale.prs_per_project):
            pr_number += 1
            state = rnd.choice(["open", "merged", "merged", "closed"])
            pr, _ = fs.upsert_pr(
                fid,
                REPO,
                pr_number,
                project_slug=slug,
                title=f"{slug}: part {k}",
                head_sha=f"{pr_number:07x}" + "a" * 33,
                head_ref=f"factory/{slug}/{k}",
                base_ref="sample-main",
                state=state,
                ready=state == "open" and k % 2 == 0,
                task_id=task_ids[k % len(task_ids)],
                gates={
                    "draft": "pass",
                    "checks": rnd.choice(["pass", "pending", "fail"]),
                    "review": "pass",
                    "mergeable": "pass",
                    "reasons": [],
                    "evidence": {"checks": {"passing": 12, "total": 14}},
                },
                **({"merged_at": int(clock - rnd.uniform(0, 86400))} if state == "merged" else {}),
            )
            fs.update_task(task_ids[k % len(task_ids)], pr_id=pr.id)

    slugs = [*existing, *leads]
    for n in range(scale.proposals):
        status = rnd.choice(["rejected", "applied", "applied", "withdrawn"])
        fs.create_proposal(
            fid,
            kind="projects"
            if status == "applied"
            else rnd.choice(["projects", "rules", "tuning"]),
            title=f"History proposal {n} (sample)",
            author_kind="agent",
            author=rnd.choice(sessions),
            status=status,
            ops=[{"op": "update", "slug": rnd.choice(slugs), "patch": {"constraints": "x"}}]
            if status != "applied"
            else None,
            result={"created": []} if status == "applied" else None,
        )
    for n in range(scale.decisions):
        row, _ = fs.create_decision(
            fid,
            dedupe_key=f"history-{n}",
            question=f"History question {n} (sample)?",
            source=rnd.choice(["agent", "loop"]),
            project_slug=rnd.choice(slugs),
            asked_by_session_id=rnd.choice(sessions),
        )
        if n % 3:
            fs.answer_decision(row.id, option_id=None, text="done", by=OWNER)
        else:
            fs.set_decision_status(row.id, "expired")
    for n in range(8):
        fs.create_decision(
            fid,
            dedupe_key=f"open-{n}",
            question=f"Open question {n} (sample)?",
            source="agent",
            project_slug=rnd.choice(list(leads) or slugs),
            asked_by_session_id=rnd.choice(sessions),
        )

    kinds = [
        "message.queued",
        "message.delivered",
        "task.state",
        "task.claimed",
        "pr.updated",
        "pr.gates",
        "heartbeat.sent",
        "member.state",
        "chat.posted",
        "presence.seen",
        "loop.tick",
        "decision.opened",
    ]
    for n in range(scale.events):
        kind = kinds[n % len(kinds)]
        ts = int((clock - (scale.events - n) * 17) * 1000)
        data: dict[str, Any] = {"n": n}
        if kind.startswith("message."):
            data["mail_id"] = f"mail-{n // 2}"
        fs.append_event(
            EventDraft(
                factory_id=fid,
                kind=kind,
                summary=f"{kind} #{n} (sample)",
                project_slug=rnd.choice(slugs),
                from_id=rnd.choice(sessions),
                to_id=rnd.choice(sessions),
                subject=f"task:T-{rnd.randint(1, 100)}",
                data=data,
                ts=ts,
            )
        )

    all_prs = fs.list_prs(fid)
    for n in range(scale.demos):
        slug = rnd.choice(list(leads) or slugs)
        prs = [p for p in all_prs if p.project_slug == slug]
        pr = prs[n % len(prs)] if prs else None
        fs.create_demo(
            factory_id=fid,
            project_slug=slug,
            repo=REPO,
            pr_number=pr.number if pr else 0,
            head_sha=pr.head_sha if pr and pr.head_sha else "abc1234",
            session_id=leads.get(slug, fid),
            created_by_session=leads.get(slug, fid),
            title=f"Demo {n}",
            summary="It renders (sample).",
            files=[
                {"file_id": f"f-{n}-v", "name": "demo.webm", "kind": "video", "bytes": 4_000_000},
                {"file_id": f"f-{n}-s", "name": "shot.png", "kind": "image", "bytes": 200_000},
            ],
            manifest={"steps": [{"n": 1, "text": "open the app", "status": "pass"}]},
            pr_id=pr.id if pr else None,
        )

    threads = [F.THREAD_FACTORY, *(f"{F.THREAD_PROJECT_PREFIX}{s}" for s in slugs[:10])]
    for n in range(scale.chat):
        human = n % 4 == 0
        fs.append_chat(
            fid,
            thread=rnd.choice(threads),
            author_kind="human" if human else "agent",
            author=OWNER if human else rnd.choice(sessions),
            author_role="owner" if human else None,
            body=f"Chat message {n} about the work (sample).",
            created_at=int(clock - (scale.chat - n) * 40),
        )

    for n in range(scale.spans):
        start = int((clock - rnd.uniform(600, 86400)) * 1000)
        slug = rnd.choice(list(leads) or slugs)
        fs.insert_span(
            fid,
            kind="turn",
            name="working",
            trace_key=f"state:{n % 40}",
            started_at=start,
            ended_at=start + rnd.randint(60_000, 1_800_000),
            outcome="ok",
            project_slug=slug,
            session_id=leads.get(slug, fid),
            attrs={"segment": "working", "cost_start": 1.0, "cost_end": 1.5},
        )


__all__ = ["OWNER", "SMALL", "EmailAuth", "Scale", "build_app", "seed_live_factory"]
