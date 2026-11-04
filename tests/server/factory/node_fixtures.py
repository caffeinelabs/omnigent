"""
(sample) Facts for the level projection's tests. Not a test module.

:func:`factory_1340` is the shape of a real factory at 13:40 on a working
day, with neutral names (the web's v3 sample tells the same story):

- one person's ask became the initiative I-1 (four wave-1 projects from
  C-11, two later waves held, one follow-up proposed as C-19 / D-49);
  assistant-core is held for C-16 (D-45) with T-15 merged as #9876 and T-16
  done locally; assistant-panel-ux waits on assistant-core;
- a shortlist proposal (C-13) the person picked created four small projects
  that all merged at 13:25 (one implicit feature under the person's ask);
- an Atlas proposal (C-12) created build-tier-drafts-3, whose two PRs have
  waited since 12:00 for a person to merge them into ``main`` (its implicit
  need and feature each hold one child, so both fold);
- the base env's setup project (``base-env``, an integration project) and
  two rule proposals belong to the factory tree; the base env runs the head
  the watcher saw at 13:25:30 (every merge so far is in it);
- the server closed three alarms today: D-48 (T-16 stalled, but it is held on
  purpose for C-16; the real ask is D-45), D-46 and D-47 (skill-discovery's
  stall and claim alarms, closed when #9881 merged);
- assistant-core's builder spent $2.50 in the last hour;
- the factory has no goals, so the Goal level is hidden.

:func:`scale_factory` is a synthetic factory of a given size (13 features,
45 projects and 360 tasks by default) for the paging and timing tests.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, Final

from omnigent.entities.factory import (
    FactoryDecision,
    FactoryInitiative,
    FactoryPr,
    FactoryProject,
    FactoryProposal,
    FactorySpan,
    FactoryTask,
)
from omnigent.server.factory import waits
from omnigent.server.factory.alarms import ClosedAlarm
from omnigent.server.factory.definition_blocks import StructureDef
from omnigent.server.factory.nodes import NodeFacts

FID: Final = "f" * 32
REPO: Final = "sample-org/sample-app"
BASE: Final = "sample-main"
NOW: Final = 1793713200
"""13:40 UTC on the sample day."""
AT_0555: Final = 1793685300
AT_0601: Final = 1793685660
AT_0612: Final = 1793686320
AT_0655: Final = 1793688900
AT_0746: Final = 1793691960
AT_0847: Final = 1793695620
AT_1200: Final = 1793707200
AT_1303: Final = 1793710980
AT_1322: Final = 1793712120
AT_1325: Final = 1793712300
HEAD_AT: Final = AT_1325 + 30
"""When the base watcher saw the deployed commit become the head."""
ASK_MESSAGE: Final = "a" * 31 + "1"
SHORTLIST_MESSAGE: Final = "a" * 31 + "2"
DEPLOYED_SHA: Final = "817480d" + "0" * 33
SHORTLIST: Final = ("ux-env-badge-copy", "ux-slash-focus", "ux-status-page", "api-request-id")
WAVE_1: Final = ("assistant-core", "connectors-core", "skill-discovery", "assistant-panel-ux")


def hex_id(n: int) -> str:
    """:returns: A row id (bare hex) for sample row *n*."""
    return f"{n:032x}"


def structure() -> dict[str, Any]:
    """:returns: The default ``structure`` block, as a definition stores it."""
    return StructureDef().model_dump(mode="json")


def _project(
    n: int,
    slug: str,
    *,
    stage: str = "building",
    status: str = "active",
    charter: Mapping[str, Any] | None = None,
    base: str = BASE,
    feature: str | None = None,
    priority: int = 2,
) -> FactoryProject:
    return FactoryProject(
        id=hex_id(100 + n),
        factory_id=FID,
        slug=slug,
        title=slug,
        repo=REPO,
        base_branch=base,
        charter=dict(charter or {}),
        priority=priority,
        stage=stage,
        status=status,
        created_by="server",
        created_at=AT_0601 + n,
        updated_at=AT_0601 + n,
        feature=feature,
    )


def _view(
    slug: str,
    *,
    stage: str,
    cost: float | None = None,
    pl: str | None = None,
    team: list[tuple[str, bool]] | None = None,
    status: str = "active",
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "slug": slug,
        "stage": stage,
        "status": status,
        "cost_usd": cost,
        "team": [
            {"session_id": hex_id(900 + i), "title": title, "running": running}
            for i, (title, running) in enumerate(team or [])
        ],
        "waiting_on": [],
    }
    if pl:
        out["pl"] = {"session_id": hex_id(800), "title": pl}
    return out


def _pr(
    number: int,
    slug: str,
    *,
    state: str = "merged",
    merged_at: int | None = AT_1325,
    task_id: str | None = None,
    ready: bool = False,
    ready_at: int | None = None,
    base: str = BASE,
    title: str | None = None,
) -> FactoryPr:
    return FactoryPr(
        id=hex_id(number),
        factory_id=FID,
        repo=REPO,
        number=number,
        url=f"https://github.com/{REPO}/pull/{number}",
        title=title or slug,
        base_ref=base,
        state=state,
        project_slug=slug,
        ready=ready,
        ready_at=ready_at,
        merged_at=merged_at if state == "merged" else None,
        merged_sha=f"{number:07x}" + "0" * 33 if state == "merged" else None,
        task_id=task_id,
        created_at=AT_0601,
        updated_at=merged_at or AT_0601,
    )


def _proposal(
    number: int,
    *,
    kind: str = "projects",
    status: str = "applied",
    title: str = "",
    ops: list[dict[str, Any]] | None = None,
    created: list[str] | None = None,
    reply_to_chat: str | None = None,
    on_behalf_of: str | None = None,
    analysis: Any = None,
    author: str = "atlas",
) -> FactoryProposal:
    return FactoryProposal(
        id=hex_id(300 + number),
        factory_id=FID,
        number=number,
        kind=kind,
        title=title or f"Proposal {number}",
        status=status,
        ops=list(ops or []),
        result={"created": list(created)} if created is not None else None,
        reply_to_chat=reply_to_chat,
        on_behalf_of=on_behalf_of,
        analysis=analysis,
        author=author,
        created_at=AT_0601,
        updated_at=AT_0601,
    )


APPLY_OPTIONS: Final = (
    {"id": "apply", "label": "Apply"},
    {"id": "edit", "label": "Edit & apply"},
    {"id": "reject", "label": "Reject"},
)


def _decision(
    number: int,
    subject: str,
    *,
    blocking: bool | None = None,
    question: str = "",
    source: str = "proposal",
    context: str | None = None,
    created_at: int = AT_0746,
) -> FactoryDecision:
    """A decision as the server stores it: a proposal's is mirrored as blocking nothing."""
    if blocking is None:
        blocking = source != "proposal"
    return FactoryDecision(
        id=hex_id(500 + number),
        factory_id=FID,
        number=number,
        question=question or f"Decision {number}",
        source=source,
        blocking=blocking,
        subject=subject,
        options=[dict(o) for o in APPLY_OPTIONS] if source == "proposal" else [],
        context=context,
        created_at=created_at,
        updated_at=created_at,
    )


def factory_1340() -> NodeFacts:
    """:returns: The sample factory at 13:40 (see the module docstring)."""
    core_why = {"proposal": "C-11", "outcome": "Sessions, tool registry, flagged chat API"}
    wave = {"ref": "I-1", "title": "Agent assistant (sample)", "role": "part", "wave": 1}
    projects = [
        _project(
            1,
            "assistant-core",
            priority=0,
            charter={
                "why": core_why,
                "outcome": "The shared core the panel, connectors and skills build on",
                "budgets": {"project_usd": 150},
                "initiative": {**wave, "role": "skeleton"},
            },
        ),
        _project(
            2,
            "connectors-core",
            stage="merged",
            status="done",
            charter={
                "why": {"proposal": "C-11"},
                "depends_on": ["assistant-core"],
                "initiative": wave,
            },
        ),
        _project(
            3,
            "skill-discovery",
            stage="merged",
            status="done",
            charter={
                "why": {"proposal": "C-11"},
                "depends_on": ["assistant-core"],
                "initiative": wave,
            },
        ),
        _project(
            4,
            "assistant-panel-ux",
            stage="queued",
            charter={
                "why": {"proposal": "C-11"},
                "outcome": "The assistant panel inside the app",
                "depends_on": ["assistant-core", *SHORTLIST[:3]],
                "depends_why": {"assistant-core": "needs the session API and per-turn debit"},
                "budgets": {"project_usd": 150},
                "initiative": {**wave, "role": "surface"},
            },
        ),
        _project(
            5,
            "build-tier-drafts-3",
            stage="ready",
            base="main",
            charter={"requested_by": {"kind": "agent", "name": "Atlas", "proposal": "C-12"}},
        ),
        *(
            _project(
                6 + i,
                slug,
                stage="merged",
                status="done",
                charter={
                    "requested_by": {"kind": "human", "name": "ana (sample)", "proposal": "C-13"}
                },
            )
            for i, slug in enumerate(SHORTLIST)
        ),
        _project(10, "base-env", stage="planning", charter={"kind": "integration"}),
    ]
    views = {
        "assistant-core": _view(
            "assistant-core",
            stage="building",
            cost=25.13,
            pl="Juno",
            team=[("Juno", False), ("Luz", False), ("Mae", False)],
        ),
        "connectors-core": _view("connectors-core", stage="merged", cost=14.2, status="done"),
        "skill-discovery": _view("skill-discovery", stage="merged", cost=11.8, status="done"),
        "assistant-panel-ux": _view("assistant-panel-ux", stage="queued", pl="Ori"),
        "build-tier-drafts-3": _view(
            "build-tier-drafts-3",
            stage="ready",
            cost=38.4,
            pl="Nova",
        ),
        **{
            slug: _view(slug, stage="merged", cost=cost, status="done")
            for slug, cost in zip(SHORTLIST, (6.4, 5.1, 8.9, 7.2), strict=True)
        },
        "base-env": _view("base-env", stage="planning", pl="Mae"),
    }
    tasks = [
        FactoryTask(
            id=hex_id(215),
            factory_id=FID,
            number=15,
            project_slug="assistant-core",
            key="core",
            title="Sessions, tool registry, flagged chat API",
            state="done",
            estimate_min=120,
            session_ids=[hex_id(701)],
            pr_id=hex_id(9876),
            created_at=AT_0601,
            updated_at=AT_0655,
        ),
        FactoryTask(
            id=hex_id(216),
            factory_id=FID,
            number=16,
            project_slug="assistant-core",
            key="debit",
            title="Debit each turn",
            state="ready",
            deps=["core"],
            estimate_min=60,
            session_ids=[hex_id(702)],
            cost_usd=1.66,
            hold={"on": "C-16", "kind": "proposal", "ref": "C-16", "until": NOW + 86400},
            created_at=AT_0601,
            updated_at=AT_0746,
        ),
        FactoryTask(
            id=hex_id(217),
            factory_id=FID,
            number=17,
            project_slug="connectors-core",
            key="connectors",
            title="Connector core and first connector",
            state="done",
            estimate_min=120,
            pr_id=hex_id(9882),
            created_at=AT_0601,
            updated_at=AT_1325,
        ),
        FactoryTask(
            id=hex_id(218),
            factory_id=FID,
            number=18,
            project_slug="skill-discovery",
            key="skill",
            title="Discovery skill with live search",
            state="done",
            estimate_min=120,
            pr_id=hex_id(9881),
            created_at=AT_0601,
            updated_at=AT_1303,
        ),
    ]
    prs = [
        _pr(
            9876, "assistant-core", merged_at=AT_0655, task_id=hex_id(215), title="Assistant core"
        ),
        _pr(9882, "connectors-core", task_id=hex_id(217)),
        _pr(9881, "skill-discovery", merged_at=AT_1303, task_id=hex_id(218)),
        _pr(
            9856,
            "build-tier-drafts-3",
            state="open",
            ready=True,
            ready_at=AT_1200,
            base="main",
            title="build-tier-drafts-3, part 1",
        ),
        _pr(
            9857,
            "build-tier-drafts-3",
            state="open",
            ready=True,
            ready_at=AT_1200,
            base="main",
            title="build-tier-drafts-3, part 2",
        ),
        *(
            _pr(number, slug)
            for number, slug in zip((9826, 9828, 9829, 9827), SHORTLIST, strict=True)
        ),
    ]
    initiative = FactoryInitiative(
        id=hex_id(401),
        factory_id=FID,
        number=1,
        title="Agent assistant (sample)",
        ask="I want an assistant in the app that helps with building, and later with "
        "discovery and sales too.",
        requested_by={"kind": "human", "id": "ana", "name": "ana (sample)"},
        ask_ref=f"chat_{ASK_MESSAGE}",
        outcome="An assistant inside the app that helps users build",
        plan={
            "acceptance": [
                {
                    "n": 1,
                    "step": "A flag-on user opens the assistant panel",
                    "owner": "assistant-panel-ux",
                },
                {
                    "n": 2,
                    "step": "Ask how the app is doing",
                    "expect": "the answer uses analytics",
                    "owner": "assistant-core",
                },
                {"n": 3, "step": "A flag-off user gets no assistant", "owner": "assistant-core"},
                {"n": 4, "step": "Each turn debits the user's credits", "owner": "assistant-core"},
                {
                    "n": 5,
                    "step": "The assistant calls a connected service",
                    "owner": "connectors-core",
                },
                {
                    "n": 6,
                    "step": "The discovery skill runs a live web search",
                    "owner": "skill-discovery",
                },
            ],
            "skeleton": "assistant-core",
            "user_facing": ["assistant-panel-ux"],
            "projects": list(WAVE_1),
            "history": [{"wave": 1, "proposal": "C-11", "added": list(WAVE_1), "at": AT_0601}],
            "waves": [
                {
                    "n": 2,
                    "goal": "ads network and growth",
                    "projects": ["ads-integration", "growth-seo"],
                },
                {
                    "n": 3,
                    "goal": "sales and promotion",
                    "projects": ["sales-skill", "promotion-skill"],
                },
            ],
            "asked_at": AT_0555,
        },
        runs=[
            {
                "n": 1,
                "status": "PARTIAL",
                "reported_at": AT_1322,
                "steps": [
                    {"n": 2, "status": "pass", "note": "demo of #9876"},
                    {"n": 3, "status": "pass", "note": "demo of #9876"},
                    {"n": 6, "status": "fail", "note": "live search fails on the base env"},
                ],
            }
        ],
        created_by="C-11",
        created_at=AT_0601,
        updated_at=AT_1322,
    )
    proposals = [
        _proposal(
            11,
            title="Agent assistant, wave 1",
            created=list(WAVE_1),
            reply_to_chat=ASK_MESSAGE,
            on_behalf_of="ana",
        ),
        _proposal(12, title="Third round of build-tier drafts", created=["build-tier-drafts-3"]),
        _proposal(
            13,
            title="Shortlist fixes",
            created=list(SHORTLIST),
            reply_to_chat=SHORTLIST_MESSAGE,
            on_behalf_of="ana",
        ),
        _proposal(14, status="pending", ops=[{"op": "update", "slug": "connectors-core"}]),
        _proposal(15, status="pending", ops=[{"op": "update", "slug": "skill-discovery"}]),
        _proposal(
            16,
            status="pending",
            title="Update assistant-core (constraints, DoD)",
            ops=[{"op": "update", "slug": "assistant-core"}],
        ),
        _proposal(
            19,
            status="pending",
            title="Fix live search on the base env",
            ops=[
                {
                    "op": "create",
                    "project": {"slug": "live-search-fix", "title": "Fix live search"},
                }
            ],
            analysis={"initiative": {"id": "I-1"}},
        ),
        _proposal(
            17, kind="rules", status="pending", title="Spike unproven external capabilities first"
        ),
        _proposal(
            18, kind="rules", status="pending", title="Name pending scope decisions in READY posts"
        ),
    ]
    sentinel = hex_id(603)
    decisions = [
        _decision(
            45,
            "proposal:C-16",
            question="Atlas proposes C-16: update assistant-core (constraints, DoD)",
            context="Implements the D-42 answer: debit each turn. T-16 waits for it.",
        ),
        _decision(43, "proposal:C-14"),
        _decision(44, "proposal:C-15"),
        _decision(
            49,
            "proposal:C-19",
            question="Start the live-search fix?",
            created_at=AT_1303 + 120,
        ),
        _decision(50, "proposal:C-17"),
        _decision(51, "proposal:C-18"),
        _decision(37, f"member:{sentinel}", blocking=False, source="agent"),
    ]
    members = [
        {
            "session_id": hex_id(601),
            "title": "Atlas",
            "role": "lead",
            "is_top_level": True,
            "running": True,
        },
        {
            "session_id": hex_id(602),
            "title": "Juno",
            "role": "project_lead",
            "is_top_level": True,
            "running": False,
        },
        {
            "session_id": sentinel,
            "title": "Sentinel",
            "role": "lead",
            "is_top_level": True,
            "running": False,
        },
        {
            "session_id": hex_id(701),
            "title": "Luz · builder",
            "role": "ic",
            "is_top_level": False,
            "running": False,
        },
        {
            "session_id": hex_id(702),
            "title": "Luz · builder",
            "role": "ic",
            "is_top_level": False,
            "running": False,
        },
    ]
    return snapshot_waits(
        NodeFacts(
            factory_id=FID,
            product="Sample app (sample)",
            repo=REPO,
            base=BASE,
            structure=structure(),
            definition_version=14,
            initiatives=[initiative],
            proposals=proposals,
            projects=projects,
            views=views,
            tasks=tasks,
            prs=prs,
            decisions=decisions,
            members=members,
            asks={
                SHORTLIST_MESSAGE: (
                    "Your shortlist pick: env badge copy, / to chat, status page, request id.",
                    "ana",
                )
            },
            base_env={
                "repo": REPO,
                "branch": BASE,
                "head_sha": DEPLOYED_SHA,
                "moved_at": HEAD_AT,
                "env": {
                    "reported": {
                        "state": "ready",
                        "at": AT_1322 + 300,
                        "urls": {
                            "app": "https://app.sample.test",
                            "api": "https://api.sample.test",
                        },
                        "deployed": {"sha": DEPLOYED_SHA[:7]},
                    }
                },
                "env_project": "base-env",
                "deployed": {"sha": DEPLOYED_SHA, "at": AT_1322 + 300, "head_at": HEAD_AT},
            },
            cost=444.0,
            burn=9.37,
            now=NOW,
            turns=[
                FactorySpan(
                    id=hex_id(7001),
                    factory_id=FID,
                    trace_key="state:juno",
                    kind="turn",
                    name="working",
                    started_at=(NOW - 2400) * 1000,
                    ended_at=(NOW - 600) * 1000,
                    outcome="ok",
                    project_slug="assistant-core",
                    session_id=hex_id(802),
                    attrs={"segment": "working", "cost_start": 22.63, "cost_end": 25.13},
                )
            ],
            closed_alarms=closed_alarms(),
        )
    )


LIVE_ASK: Final = (
    "i want to build a chat agent like feature. those agents can not only help with "
    "building, but also discovery, sales etc. for stuff like sales, promotions we might "
    "also need some integrations such as an ads network. you need to scope these as "
    "projects appropriately, and build on the idea"
)
"""The person's ask at 05:55, as typed (lower case, run on)."""
DRAFTS_MESSAGE: Final = "a" * 31 + "3"
DRAFTS_ASK: Final = (
    "@Atlas build-tier-drafts-2 hit the same 300 s sandbox launch timeout: the cluster "
    "was full again, so it waited for a new slot. Can you start a third round?"
)
SHORTLIST_ASK: Final = (
    "@Atlas thanks. Please start 2, 3, 4 and 6 as four projects on "
    "sample-org/sample-app#sample-main (previews + demos on, as you do)"
)
FOLLOW_UP_OPTIONS: Final = (
    {"id": "follow-up", "label": "Follow-up project now", "recommended": True},
    {"id": "park", "label": "Park it"},
    {"id": "revert", "label": "Revert #9881"},
)


def factory_live() -> NodeFacts:
    """
    :returns: The sample factory as the live cockpit showed it: no initiative
        was stored, so C-11's four projects sit under the implicit feature
        "From C-11: create … · create …" below the person's raw ask; the drafts
        project is titled "Build-tier drafts render (main) · v3" under another
        raw ask; and D-49 is Atlas's own question on the product (start a
        follow-up for live search?), not a proposal.
    """
    facts = factory_1340()
    wave_1 = " · ".join(f"create {slug}" for slug in WAVE_1)
    shortlist = " · ".join(f"create {slug}" for slug in SHORTLIST)
    titles = {
        "assistant-core": "Assistant core: sessions, tool registry, flagged chat API",
        "build-tier-drafts-3": "Build-tier drafts render (main) · v3",
    }
    projects = [
        dataclasses.replace(
            p,
            title=titles.get(p.slug, p.title),
            charter={k: v for k, v in p.charter.items() if k != "initiative"},
        )
        for p in facts.projects
    ]
    proposals = []
    for row in facts.proposals:
        if row.number == 19:
            continue
        if row.number == 11:
            row = dataclasses.replace(row, title=wave_1)
        elif row.number == 12:
            row = dataclasses.replace(
                row,
                title="create build-tier-drafts-3",
                reply_to_chat=DRAFTS_MESSAGE,
                on_behalf_of="ana",
            )
        elif row.number == 13:
            row = dataclasses.replace(row, title=shortlist)
        proposals.append(row)
    question = FactoryDecision(
        id=hex_id(549),
        factory_id=FID,
        number=49,
        question="Discovery is on the base env but live search does not work. "
        "Start a follow-up project to fix it?",
        source="agent",
        blocking=True,
        options=[dict(o) for o in FOLLOW_UP_OPTIONS],
        assignee="owners",
        created_at=AT_1303 + 120,
        updated_at=AT_1303 + 120,
    )
    decisions = [d for d in facts.decisions if d.number != 49] + [question]
    facts = dataclasses.replace(
        facts,
        initiatives=[],
        projects=projects,
        proposals=proposals,
        decisions=decisions,
        asks={
            ASK_MESSAGE: (LIVE_ASK, "ana"),
            DRAFTS_MESSAGE: (DRAFTS_ASK, "ana"),
            SHORTLIST_MESSAGE: (SHORTLIST_ASK, "ana"),
        },
    )
    return snapshot_waits(facts)


def snapshot_waits(facts: NodeFacts) -> NodeFacts:
    """
    Each project view's ``waiting_on`` as the snapshot builds it, from the
    facts (:func:`.waits.project_waits`, the view's ``start`` included): call
    again after changing the rows a test reads.
    """
    found = waits.Facts(
        projects={p.slug: p for p in facts.projects},
        prs=facts.prs,
        decisions=facts.decisions,
        proposals=facts.proposals,
        tasks=[t for t in facts.tasks if t.state in waits.OPEN_TASK_STATES],
        loops=facts.loops,
        leads=facts.leads,
        now=facts.now,
        targets=facts.targets,
    )
    views: dict[str, Mapping[str, Any]] = {}
    for slug, view in facts.views.items():
        row = found.projects.get(slug)
        start = view.get("start") if isinstance(view.get("start"), Mapping) else None
        listed = waits.project_waits(row, found, start=start) if row is not None else []
        views[slug] = {**view, "waiting_on": [w.to_json() for w in listed]}
    facts.views = views
    return facts


def closed_alarms() -> list[ClosedAlarm]:
    """:returns: The three alarms the server closed by 13:40, newest first."""
    return [
        ClosedAlarm(
            ref="D-47",
            was="PR #9881: claim 1/3 stalled - what next?",
            cause="PR #9881 merged",
            at=AT_1303 + 60,
            about="skill-discovery",
            project="skill-discovery",
        ),
        ClosedAlarm(
            ref="D-46",
            was="T-18 Discovery skill with live search: implement 2/3 stalled - what next?",
            cause="PR #9881 merged",
            at=AT_1303 + 60,
            about="skill-discovery",
            project="skill-discovery",
        ),
        ClosedAlarm(
            ref="D-48",
            was="T-16 Debit each turn: implement 1/3 stalled - what next?",
            cause="T-16 is held on purpose for C-16; a held task is not stalled",
            at=AT_0847,
            real_ask="D-45",
            about="T-16",
            project="assistant-core",
        ),
    ]


def _span(
    n: int,
    kind: str,
    name: str,
    start: int,
    end: int | None = None,
    *,
    slug: str | None = None,
    session: str | None = None,
    pr: int | None = None,
    attrs: dict[str, Any] | None = None,
) -> FactorySpan:
    return FactorySpan(
        id=hex_id(70_000 + n),
        factory_id=FID,
        trace_key=f"sample:{n}",
        kind=kind,
        name=name,
        started_at=start * 1000,
        ended_at=end * 1000 if end is not None else None,
        outcome="ok" if end is not None else "open",
        project_slug=slug,
        session_id=session,
        pr_id=hex_id(pr) if pr is not None else None,
        attrs=attrs,
    )


def spans_1340() -> list[FactorySpan]:
    """
    :returns: The spans the sample factory recorded by 13:40: each project's
        stages, the builders' task sessions (T-15, then T-16 until its hold),
        the asks (D-45 since 07:46, D-49 since 13:05; D-48 was an alarm the
        server closed) and the two PRs ready for a person to merge since 12:00.
    """
    core = "assistant-core"
    proposal = {"source": "proposal", "blocking": False}
    return [
        _span(1, "stage", "planning", AT_0601, AT_0612, slug=core),
        _span(2, "stage", "building", AT_0612, slug=core),
        _span(
            3, "task", "Sessions, tool registry", AT_0612, AT_0655, slug=core, session=hex_id(701)
        ),
        _span(4, "task", "Debit each turn", AT_0655, slug=core, session=hex_id(702)),
        _span(5, "stage", "queued", AT_0601, AT_0655, slug="connectors-core"),
        _span(6, "stage", "building", AT_0655, AT_1325, slug="connectors-core"),
        _span(7, "stage", "merged", AT_1325, slug="connectors-core"),
        _span(8, "stage", "queued", AT_0601, AT_0655, slug="skill-discovery"),
        _span(9, "stage", "building", AT_0655, AT_1303, slug="skill-discovery"),
        _span(10, "stage", "merged", AT_1303, slug="skill-discovery"),
        _span(11, "stage", "queued", AT_0601, slug="assistant-panel-ux"),
        _span(
            12,
            "decision",
            "D-45",
            AT_0746,
            slug=core,
            attrs={**proposal, "subject": "proposal:C-16"},
        ),
        _span(
            13,
            "decision",
            "D-48",
            AT_0847,
            AT_0847 + 60,
            slug=core,
            attrs={"source": "loop", "blocking": True, "subject": "task:T-16"},
        ),
        _span(
            14, "decision", "D-49", AT_1303 + 120, attrs={**proposal, "subject": "proposal:C-19"}
        ),
        _span(15, "stage", "building", AT_0601, AT_1200, slug="build-tier-drafts-3"),
        _span(16, "stage", "ready", AT_1200, slug="build-tier-drafts-3"),
        _span(17, "ready_wait", "ready", AT_1200, slug="build-tier-drafts-3", pr=9856),
        _span(18, "ready_wait", "ready", AT_1200, slug="build-tier-drafts-3", pr=9857),
    ]


def scale_factory(features: int = 13, projects: int = 45, tasks: int = 360) -> NodeFacts:
    """
    :returns: A synthetic factory: *features* initiatives under three asks,
        *projects* spread over them (a chain of dependencies in each), and
        *tasks* spread over the projects, each done one with a merged PR.
    """
    asks = [hex_id(10_000 + i) for i in range(3)]
    initiatives = []
    rows: list[FactoryProject] = []
    by_feature: dict[int, list[str]] = {}
    for p in range(projects):
        feature = p % features + 1
        slug = f"proj-{p:02d}"
        siblings = by_feature.setdefault(feature, [])
        depends = [siblings[-1]] if siblings else []
        siblings.append(slug)
        rows.append(
            _project(
                1000 + p,
                slug,
                stage="building" if p % 4 else "queued",
                charter={"depends_on": depends},
            )
        )
    for f in range(1, features + 1):
        initiatives.append(
            FactoryInitiative(
                id=hex_id(20_000 + f),
                factory_id=FID,
                number=f,
                title=f"Feature {f} (sample)",
                ask=f"Ask number {f % 3}",
                requested_by={"kind": "human", "id": "ana", "name": "ana (sample)"},
                ask_ref=f"chat_{asks[f % 3]}",
                plan={
                    "projects": by_feature.get(f, []),
                    "acceptance": [{"n": 1, "step": "It works"}, {"n": 2, "step": "It is fast"}],
                    "history": [
                        {
                            "wave": 1,
                            "proposal": f"C-{f}",
                            "added": by_feature.get(f, []),
                            "at": AT_0601,
                        }
                    ],
                },
                created_by=f"C-{f}",
                created_at=AT_0601,
                updated_at=AT_0601,
            )
        )
    task_rows: list[FactoryTask] = []
    prs: list[FactoryPr] = []
    states = ("done", "running", "ready", "planned", "review", "blocked")
    for t in range(tasks):
        slug = rows[t % projects].slug
        state = states[t % len(states)]
        number = t + 1
        task_id = hex_id(30_000 + number)
        task_rows.append(
            FactoryTask(
                id=task_id,
                factory_id=FID,
                number=number,
                project_slug=slug,
                key=f"k{number}",
                title=f"Task {number}",
                state=state,
                estimate_min=30 + number % 90,
                deps=[f"k{number - projects}"] if number > projects else [],
                session_ids=[hex_id(40_000 + number)],
                pr_id=hex_id(50_000 + number) if state == "done" else None,
                created_at=AT_0601,
                updated_at=AT_0601 + number,
            )
        )
        if state == "done":
            prs.append(
                FactoryPr(
                    id=hex_id(50_000 + number),
                    factory_id=FID,
                    repo=REPO,
                    number=20_000 + number,
                    url=f"https://github.com/{REPO}/pull/{20_000 + number}",
                    title=f"Task {number}",
                    state="merged",
                    project_slug=slug,
                    merged_at=AT_1303,
                    task_id=task_id,
                    created_at=AT_0601,
                    updated_at=AT_1303,
                )
            )
    return NodeFacts(
        factory_id=FID,
        product="Big sample app (sample)",
        repo=REPO,
        base=BASE,
        structure=structure(),
        initiatives=initiatives,
        projects=rows,
        views={p.slug: _view(p.slug, stage=p.stage, cost=1.5) for p in rows},
        tasks=task_rows,
        prs=prs,
        cost=999.0,
        now=NOW,
    )


__all__ = [
    "ASK_MESSAGE",
    "AT_0601",
    "AT_0612",
    "AT_0655",
    "AT_0746",
    "AT_1200",
    "AT_1303",
    "AT_1325",
    "BASE",
    "DEPLOYED_SHA",
    "FID",
    "HEAD_AT",
    "NOW",
    "REPO",
    "SHORTLIST",
    "SHORTLIST_MESSAGE",
    "WAVE_1",
    "closed_alarms",
    "factory_1340",
    "hex_id",
    "scale_factory",
    "snapshot_waits",
    "spans_1340",
    "structure",
]
