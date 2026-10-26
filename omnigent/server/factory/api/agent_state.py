"""Runner-facing cockpit routes: what agents report to the board.

``sys_factory_ask`` opens decisions, ``sys_factory_pr`` registers PRs and
claims, ``sys_factory_env`` reports dev envs and factory previews, and
``sys_factory_spawn`` reads a project's stored charter. Callers authenticate
as ``from_session_id`` like ``/v1/factory/messages``; the project always comes
from the caller's root PL, so a member only writes its own project's records.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from typing import Any, Final

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from omnigent.entities.factory import FactoryDecision, FactoryPr
from omnigent.factory import (
    DECISION_CONTEXT_MAX,
    DECISION_MAX_OPEN_PER_ASKER,
    DECISION_MAX_OPTIONS,
    DECISION_OPTION_DESCRIPTION_MAX,
    DECISION_OPTION_LABEL_MAX,
    DECISION_QUESTION_MAX,
    ENV_STATES,
    PR_CLAIMS,
    PREVIEW_REPORT_STATES,
    ROLE_ENV_KEEPER,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    SENTINEL_VERDICTS,
    parse_pr_url,
)
from omnigent.server.factory import events, nudge, status
from omnigent.server.factory.agent_scope import (
    AgentCaller,
    announce_new_pr,
    authorize_agent,
    ensure_pr,
    env_url_error,
    factory_repos,
    require_factory_repo,
)
from omnigent.server.factory.charter import render_pl_charter
from omnigent.server.factory.routes import FactoryRouteError
from omnigent.stores.factory_store import DecisionNotOpen, norm_id

router = APIRouter(prefix="/v1/factory/agent", tags=["factory"])

MAX_PREVIEWS_ENV: Final = "OMNIGENT_FACTORY_MAX_PREVIEWS"
DEFAULT_MAX_PREVIEWS: Final = 8
PR_REFRESH_WAIT_S: Final = 10.0
_REFRESH_POLL_S: Final = 0.25
# Previews that hold a namespace and count toward the factory cap.
LIVE_PREVIEW_STATES: Final = frozenset({"deploying", "starting", "ready", "stale", "degraded"})
_ENV_URL_KEYS: Final = ("app", "api", "minio", "tilt_ui")
_DECISION_REF_RE: Final = re.compile(r"(?:[Dd]-)?([1-9][0-9]{0,8})")
_SHA_RE: Final = re.compile(r"[0-9a-f]{7,40}")


# ── Bodies ─────────────────────────────────────────────────────


class AskOption(BaseModel):
    """One answer option an agent offers."""

    label: str
    description: str | None = None
    recommended: bool = False


class AskBody(BaseModel):
    """``POST /v1/factory/agent/decisions``."""

    from_session_id: str
    question: str
    options: list[AskOption] = Field(default_factory=list)
    context: str | None = None
    blocking: bool = True
    allow_text: bool = True
    dedupe_key: str | None = None


class WithdrawBody(BaseModel):
    """``POST /v1/factory/agent/decisions/{ref}/withdraw``."""

    from_session_id: str
    reason: str | None = None


class PrBody(BaseModel):
    """``POST /v1/factory/agent/prs``."""

    from_session_id: str
    url: str
    task: str | None = None
    claim: str | None = None
    sentinel_verdict: str | None = None
    head_sha: str | None = None
    note: str | None = None


class ResourceReport(BaseModel):
    """One Tilt resource as an agent reports it."""

    name: str
    status: str | None = None
    ready: bool | None = None
    restarts: int | None = None
    detail: str | None = None


class EnvBody(BaseModel):
    """``PUT /v1/factory/agent/envs``: a dev env (no ``pr``) or a PR's preview."""

    from_session_id: str
    state: str
    env_id: str
    urls: dict[str, str] = Field(default_factory=dict)
    deployed: dict[str, str] = Field(default_factory=dict)
    pr: int | None = None
    slot: int | None = None
    namespace: str | None = None
    resources: list[ResourceReport] = Field(default_factory=list)
    note: str | None = None


# ── Decisions ──────────────────────────────────────────────────


def default_dedupe_key(project: str | None, question: str) -> str:
    """
    The dedupe key of a question asked without one.

    :param project: The asker's project slug, if any.
    :param question: The question text.
    :returns: ``sha256(project|casefolded collapsed question)[:32]``.
    """
    collapsed = " ".join(question.casefold().split())
    return hashlib.sha256(f"{project or ''}|{collapsed}".encode()).hexdigest()[:32]


def _decision_reply(row: FactoryDecision, *, deduped: bool) -> dict[str, Any]:
    return {"decision": row.ref, "id": row.id, "status": row.status, "deduped": deduped}


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _options(body: AskBody) -> list[dict[str, Any]]:
    if len(body.options) > DECISION_MAX_OPTIONS:
        raise FactoryRouteError(400, "invalid_options", f"at most {DECISION_MAX_OPTIONS} options")
    options: list[dict[str, Any]] = []
    for index, option in enumerate(body.options, start=1):
        label = option.label.strip()
        if not label or len(label) > DECISION_OPTION_LABEL_MAX:
            raise FactoryRouteError(
                400,
                "invalid_options",
                f"option labels are 1-{DECISION_OPTION_LABEL_MAX} characters",
            )
        entry: dict[str, Any] = {"id": str(index), "label": label}
        description = _clip(option.description, DECISION_OPTION_DESCRIPTION_MAX)
        if description:
            entry["description"] = description
        if option.recommended:
            entry["recommended"] = True
        options.append(entry)
    return options


@router.post("/decisions")
async def ask_decision(request: Request, body: AskBody) -> dict[str, Any]:
    """Open a decision for the human, or return the open one it repeats."""
    caller = await authorize_agent(request, body.from_session_id)
    question = " ".join(body.question.split())
    if not question or len(question) > DECISION_QUESTION_MAX:
        raise FactoryRouteError(
            400, "invalid_question", f"question must be 1-{DECISION_QUESTION_MAX} characters"
        )
    options = _options(body)
    if not options and not body.allow_text:
        raise FactoryRouteError(400, "invalid_options", "options are needed without allow_text")
    project = caller.membership.project
    key = (body.dedupe_key or "").strip()[:64] or default_dedupe_key(project, question)
    store = caller.deps.factory_store
    asker = norm_id(caller.session.id)

    def _open_or_create() -> tuple[FactoryDecision, bool]:
        open_rows = store.list_decisions(factory_id=caller.factory_id, status="open", limit=1000)
        for row in open_rows:
            if row.dedupe_key == key:
                return row, True
        mine = sum(1 for row in open_rows if row.asked_by_session_id == asker)
        if mine >= DECISION_MAX_OPEN_PER_ASKER:
            raise FactoryRouteError(
                409,
                "too_many_open_decisions",
                f"you already have {mine} open decisions; wait for answers or withdraw one",
            )
        return store.create_decision(
            caller.factory_id,
            dedupe_key=key,
            question=question,
            source="agent",
            options=options,
            blocking=body.blocking,
            allow_text=body.allow_text,
            project_slug=project,
            asked_by_session_id=caller.session.id,
            context=_clip(body.context, DECISION_CONTEXT_MAX),
        )

    row, deduped = await asyncio.to_thread(_open_or_create)
    if not deduped:
        await events.emit(
            request.app.state,
            caller.factory_id,
            "decision.opened",
            severity="warning" if row.blocking else "notice",
            project=project,
            from_id=caller.session.id,
            subject=f"decision:{row.ref}",
            summary=f"{caller.title} asks {row.ref}: {question}",
            data={"decision_id": row.id, "number": row.number, "blocking": row.blocking},
        )
    return _decision_reply(row, deduped=deduped)


def _find_decision(caller: AgentCaller, ref: str) -> FactoryDecision | None:
    store = caller.deps.factory_store
    match = _DECISION_REF_RE.fullmatch(ref.strip())
    if match is not None:
        return store.get_decision_by_number(caller.factory_id, int(match.group(1)))
    try:
        row = store.get_decision(ref)
    except ValueError:
        return None
    return row if row is not None and row.factory_id == norm_id(caller.factory_id) else None


@router.post("/decisions/{ref}/withdraw")
async def withdraw_decision(request: Request, ref: str, body: WithdrawBody) -> dict[str, Any]:
    """Withdraw a decision the caller asked."""
    caller = await authorize_agent(request, body.from_session_id)
    row = await asyncio.to_thread(_find_decision, caller, ref)
    if row is None:
        raise FactoryRouteError(404, "decision_not_found", f"no decision {ref!r} in your factory")
    if row.asked_by_session_id != norm_id(caller.session.id):
        raise FactoryRouteError(403, "not_allowed", "only the agent that asked may withdraw it")
    store = caller.deps.factory_store
    try:
        row = await asyncio.to_thread(store.set_decision_status, row.id, "withdrawn")
    except DecisionNotOpen as exc:
        raise FactoryRouteError(
            409, "decision_not_open", f"{exc.decision.ref} is {exc.decision.status}"
        ) from exc
    reason = _clip(body.reason, 300)
    await events.emit(
        request.app.state,
        caller.factory_id,
        "decision.withdrawn",
        project=row.project_slug,
        from_id=caller.session.id,
        subject=f"decision:{row.ref}",
        summary=f"{caller.title} withdrew {row.ref}" + (f": {reason}" if reason else ""),
        data={"decision_id": row.id, "reason": reason},
    )
    return _decision_reply(row, deduped=False)


# ── PRs ────────────────────────────────────────────────────────


def _poller_running() -> bool:
    """Whether a GitHub poller is up to refresh a PR (its status says so)."""
    entry = status.get_status("github")
    return entry is not None and entry.get("state") not in (None, "off", "disconnected")


async def _await_refresh(caller: AgentCaller, pr_id: str) -> FactoryPr | None:
    """Wait up to :data:`PR_REFRESH_WAIT_S` for the poller to refresh *pr_id*."""
    if not _poller_running():
        return None
    store = caller.deps.factory_store
    deadline = time.monotonic() + PR_REFRESH_WAIT_S
    while time.monotonic() < deadline:
        await asyncio.sleep(_REFRESH_POLL_S)
        row = await asyncio.to_thread(store.get_pr, pr_id)
        if row is not None and row.last_polled_at is not None:
            return row
    return None


def _sha(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    if not _SHA_RE.fullmatch(value):
        raise FactoryRouteError(400, "invalid_sha", "head_sha must be 7-40 hex characters")
    return value


@router.post("/prs")
async def register_pr(request: Request, body: PrBody) -> dict[str, Any]:
    """Track a PR (and a claim about it), then answer with fresh server gates."""
    caller = await authorize_agent(request, body.from_session_id)
    parsed = parse_pr_url(body.url)
    if parsed is None:
        raise FactoryRouteError(400, "not_a_pr_url", f"{body.url!r} is not a GitHub PR URL")
    repo, number = parsed
    if body.claim is not None and body.claim not in PR_CLAIMS:
        raise FactoryRouteError(400, "invalid_claim", f"claim must be one of {list(PR_CLAIMS)}")
    if body.sentinel_verdict is not None:
        if body.sentinel_verdict not in SENTINEL_VERDICTS:
            raise FactoryRouteError(
                400,
                "invalid_verdict",
                f"sentinel_verdict must be one of {list(SENTINEL_VERDICTS)}",
            )
        if caller.membership.role != ROLE_LEAD:
            raise FactoryRouteError(
                403, "sentinel_lead_only", "only a lead records a Sentinel verdict"
            )
    head_sha = _sha(body.head_sha)
    note = _clip(body.note, 1000)
    slugs = await asyncio.to_thread(require_factory_repo, caller, repo)
    task = _clip(body.task, 64)
    row, created = await asyncio.to_thread(
        ensure_pr, caller, repo, number, slugs=slugs, refresh=True, task=task
    )
    now = int(time.time())
    app_state = request.app.state
    subject = f"pr:{repo}#{number}"
    if body.sentinel_verdict is not None:
        sentinel = {
            "verdict": body.sentinel_verdict,
            "head_sha": head_sha,
            "note": note,
            "by": caller.title,
            "at": now,
        }
        updated = await asyncio.to_thread(
            caller.deps.factory_store.update_pr, row.id, sentinel=sentinel
        )
        row = updated or row
    if created:
        await announce_new_pr(app_state, caller, row)
    if body.claim is not None or body.sentinel_verdict is not None:
        what = body.claim or f"sentinel {body.sentinel_verdict}"
        await events.emit(
            app_state,
            caller.factory_id,
            "pr.claim",
            severity="notice",
            project=row.project_slug,
            from_id=caller.session.id,
            subject=subject,
            summary=f"{caller.title} claims {repo}#{number} {what}",
            data={
                "pr_id": row.id,
                "claim": body.claim,
                "sentinel_verdict": body.sentinel_verdict,
                "head_sha": head_sha,
                "note": note,
            },
        )
    nudge.nudge("prs", row.id)
    refreshed = await _await_refresh(caller, row.id)
    if refreshed is not None:
        row = refreshed
    gates = dict(row.gates or {})
    reply: dict[str, Any] = {
        "pr_id": row.id,
        "url": row.url,
        "repo": row.repo,
        "number": row.number,
        "project": row.project_slug,
        "state": row.state,
        "head_sha": row.head_sha or None,
        "ready": row.ready,
        "gates": gates,
        "reasons": list(gates.get("reasons") or []),
        "refreshed": refreshed is not None,
        "claim_mismatch": body.claim == "ready_for_human_merge" and not row.ready,
    }
    if not gates:
        reply["note"] = "gates come from the server's GitHub poller and are not computed yet"
    return reply


# ── Envs and previews ──────────────────────────────────────────


def max_previews() -> int:
    """:returns: The factory-wide cap on live previews (``OMNIGENT_FACTORY_MAX_PREVIEWS``)."""
    raw = os.environ.get(MAX_PREVIEWS_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_PREVIEWS
    return value if value > 0 else DEFAULT_MAX_PREVIEWS


def _is_live(pr: FactoryPr) -> bool:
    preview = pr.preview or {}
    return pr.state == "open" and preview.get("state") in LIVE_PREVIEW_STATES


def park_candidates(prs: list[FactoryPr]) -> list[dict[str, Any]]:
    """
    Live previews to park first: PRs that are not ready, then the oldest report.

    :param prs: Open PRs holding a live preview.
    """
    ordered = sorted(
        prs,
        key=lambda p: (p.ready, int((p.preview or {}).get("reported_at") or 0)),
    )
    return [
        {
            "pr": p.number,
            "repo": p.repo,
            "project": p.project_slug,
            "preview": (p.preview or {}).get("name") or f"p{p.number}",
            "state": (p.preview or {}).get("state"),
            "pr_ready": p.ready,
            "reported_at": (p.preview or {}).get("reported_at"),
        }
        for p in ordered
    ]


def _check_urls(urls: dict[str, str]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for key, value in urls.items():
        if key not in _ENV_URL_KEYS:
            raise FactoryRouteError(
                400, "invalid_url", f"urls has unknown key {key!r} (use {list(_ENV_URL_KEYS)})"
            )
        url = value.strip()
        if not url:
            continue
        problem = env_url_error(key, url)
        if problem is not None:
            raise FactoryRouteError(400, "invalid_url", f"urls.{key} {problem}")
        clean[key] = url
    return clean


def _deployed(body: EnvBody, previous: dict[str, Any] | None, now: int) -> dict[str, Any]:
    deployed: dict[str, Any] = {}
    sha = _sha(body.deployed.get("sha"))
    if sha:
        deployed["sha"] = sha
    ref = _clip(body.deployed.get("ref"), 255)
    if ref:
        deployed["ref"] = ref
    overlay = _sha(body.deployed.get("tooling_overlay"))
    if overlay:
        deployed["tooling_overlay"] = overlay
    if sha:
        same = previous is not None and previous.get("sha") == sha and previous.get("at")
        deployed["at"] = previous["at"] if same and previous is not None else now
    return deployed


def _resources(body: EnvBody) -> list[dict[str, Any]]:
    return [r.model_dump(exclude_none=True) for r in body.resources[:100]]


def _report_dev_env(caller: AgentCaller, body: EnvBody, urls: dict[str, str]) -> dict[str, Any]:
    project_slug = caller.membership.project
    assert project_slug is not None
    store = caller.deps.factory_store
    current = store.get_project(caller.factory_id, project_slug)
    env = dict(current.env or {}) if current is not None else {}
    previous = env.get("reported") if isinstance(env.get("reported"), dict) else None
    now = int(time.time())
    reported: dict[str, Any] = {
        "state": body.state,
        "env_id": body.env_id.strip(),
        "urls": urls,
        "deployed": _deployed(body, previous.get("deployed") if previous else None, now),
        "resources": _resources(body),
        "note": _clip(body.note, 500),
        "at": now,
    }
    env["reported"] = reported
    fields: dict[str, Any] = {"env": env}
    if current is None:
        fields["pl_session_id"] = caller.membership.root_session_id
    row = store.upsert_project(caller.factory_id, project_slug, **fields)
    return {"project": row.slug, "env": reported}


def _preview_repo(caller: AgentCaller, number: int) -> tuple[str, set[str]]:
    """The repo of PR *number* in the caller's project."""
    store = caller.deps.factory_store
    project_slug = caller.membership.project
    project = store.get_project(caller.factory_id, project_slug) if project_slug else None
    repos = factory_repos(caller)
    if project is not None and project.repo:
        repo = project.repo.lower()
        return repo, repos.get(repo, {project.slug})
    tracked = [
        pr
        for pr in store.list_prs(caller.factory_id)
        if pr.number == number and (not project_slug or pr.project_slug in (None, project_slug))
    ]
    if len(tracked) == 1:
        return tracked[0].repo, repos.get(tracked[0].repo, set())
    mine = [repo for repo, slugs in repos.items() if project_slug in slugs]
    if len(mine) == 1:
        return mine[0], repos[mine[0]]
    raise FactoryRouteError(
        400,
        "unknown_repo",
        f"cannot tell which repo PR #{number} is on; register it with sys_factory_pr first",
    )


class _PreviewCap(Exception):
    def __init__(self, live: list[FactoryPr], cap: int) -> None:
        super().__init__("preview_cap")
        self.live = live
        self.cap = cap


def _report_preview(
    caller: AgentCaller, body: EnvBody, urls: dict[str, str]
) -> tuple[FactoryPr, dict[str, Any], bool]:
    assert body.pr is not None
    repo, slugs = _preview_repo(caller, body.pr)
    store = caller.deps.factory_store
    row, created = ensure_pr(caller, repo, body.pr, slugs=slugs)
    previous = dict(row.preview or {})
    if body.state == "deploying" and not _is_live(row):
        live = [
            pr
            for pr in store.list_prs(caller.factory_id, states=["open"])
            if pr.id != row.id and _is_live(pr)
        ]
        cap = max_previews()
        if len(live) >= cap:
            raise _PreviewCap(live, cap)
    now = int(time.time())
    preview = dict(previous)
    preview.update(
        {
            "name": f"p{body.pr}",
            "env_id": body.env_id.strip(),
            "state": body.state,
            "urls": urls or previous.get("urls") or {},
            "deployed": _deployed(body, previous.get("deployed"), now)
            or previous.get("deployed")
            or {},
            "resources": _resources(body) or previous.get("resources") or [],
            "note": _clip(body.note, 500),
            "reported_at": now,
        }
    )
    if body.slot is not None:
        preview["slot"] = body.slot
    if body.namespace:
        preview["namespace"] = body.namespace.strip()[:63]
    updated = store.update_pr(row.id, preview=preview)
    return updated or row, preview, created


@router.put("/envs")
async def report_env(request: Request, body: EnvBody) -> Any:
    """Record the caller's project dev env, or one PR's factory preview."""
    caller = await authorize_agent(request, body.from_session_id)
    if caller.membership.role not in (ROLE_ENV_KEEPER, ROLE_PROJECT_LEAD):
        raise FactoryRouteError(
            403, "not_allowed", "only env_keeper and the project lead report envs"
        )
    if not caller.membership.project:
        raise FactoryRouteError(
            403, "not_your_project", "envs are reported for a project; yours has none"
        )
    allowed = PREVIEW_REPORT_STATES if body.pr is not None else ENV_STATES
    state = "deploying" if body.pr is not None and body.state == "starting" else body.state
    if state not in allowed:
        raise FactoryRouteError(400, "invalid_state", f"state must be one of {list(allowed)}")
    if not body.env_id.strip():
        raise FactoryRouteError(400, "invalid_env_id", "env_id is required")
    body = body.model_copy(update={"state": state})
    urls = _check_urls(body.urls)
    app_state = request.app.state
    project = caller.membership.project
    if body.pr is None:
        reply = await asyncio.to_thread(_report_dev_env, caller, body, urls)
        sha = (reply["env"]["deployed"] or {}).get("sha")
        await events.emit(
            app_state,
            caller.factory_id,
            "env.reported",
            project=project,
            from_id=caller.session.id,
            subject=f"env:{project}",
            summary=f"dev env {state}" + (f" at {sha[:7]}" if sha else ""),
            data={"state": state, "env_id": body.env_id, "sha": sha},
        )
        nudge.nudge("envs", f"project:{project}")
        return reply
    try:
        row, preview, created = await asyncio.to_thread(_report_preview, caller, body, urls)
    except _PreviewCap as cap:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "preview_cap",
                    "message": (
                        f"the factory already runs {len(cap.live)} live previews "
                        f"(max {cap.cap}); park one of park_candidates first"
                    ),
                },
                "max_previews": cap.cap,
                "live_previews": len(cap.live),
                "park_candidates": park_candidates(cap.live),
            },
        )
    if created:
        await announce_new_pr(app_state, caller, row)
    sha = (preview.get("deployed") or {}).get("sha")
    kind = "preview.parked" if state == "parked" else "preview.reported"
    await events.emit(
        app_state,
        caller.factory_id,
        kind,
        severity="notice" if state in ("failed", "parked") else "info",
        project=row.project_slug or project,
        from_id=caller.session.id,
        subject=f"pr:{row.repo}#{row.number}",
        summary=f"preview p{row.number} {state}" + (f" at {sha[:7]}" if sha else ""),
        data={"pr_id": row.id, "state": state, "env_id": body.env_id, "sha": sha},
    )
    nudge.nudge("envs", row.id)
    return {"pr_id": row.id, "preview": preview}


# ── Charters ───────────────────────────────────────────────────


@router.get("/charter")
async def get_charter(
    request: Request,
    from_session_id: str = Query(...),
    project: str = Query(...),
) -> dict[str, Any]:
    """The stored charter of a project, rendered for its PL's brief (leads only)."""
    caller = await authorize_agent(request, from_session_id)
    if caller.membership.role != ROLE_LEAD:
        raise FactoryRouteError(403, "not_allowed", "only a lead reads charters to spawn PLs")
    row = await asyncio.to_thread(
        caller.deps.factory_store.get_project, caller.factory_id, project.strip()
    )
    if row is None or row.charter is None:
        raise FactoryRouteError(
            404, "charter_not_found", f"project {project!r} has no stored charter"
        )
    return {"project": row.slug, "text": render_pl_charter(row)}
