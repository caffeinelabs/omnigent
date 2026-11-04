"""Acceptance runs: after merges into the base, run an initiative's steps on the base env.

The factory already watches its bases (:mod:`.base`) and tells the base env's
lead (the integration project, ``KIND: integration``) to redeploy when a base
moves. This module adds the user-visible check on top:

1. **Due.** A base move that brings in a merged PR of a project of an open
   initiative with acceptance steps (the PR at the new head, or any tracked
   PR merged since the previous head) records a ``due`` run at the new head;
   an initiative keeps at most one due run, at the newest head.
2. **Requested.** Once the base env reports ready at that head
   (``env.reported``), the server asks a demo-capable member to run the steps
   there: the base env's lead, else the walking skeleton's lead. The request
   is one ``[Factory acceptance · …]`` message carrying an ``ACCEPT`` command
   for its demo agent. One run per initiative is on its way at a time: a due
   run waits until the requested one reports or expires. A due run whose env
   never reports ready is sent anyway after :data:`ENV_WAIT_S`; a requested
   run without a result expires after :data:`RUN_EXPIRE_S`. A person can
   also run one now.
3. **Reported.** The demo agent publishes the run like any demo, titled
   ``Acceptance I-<n> run <k>`` (the title starts with it) and published for
   the run's PR and head (:func:`match_report`), from the member it was asked
   of or the base env's or skeleton's project (:func:`may_report`); the
   server stores the evidence as a demo of no PR and the per-step result on
   the initiative (:func:`record_report`).
4. **Failures.** Each failed step becomes a task of the project that owns it
   (the step's ``owner``, else the skeleton), and its lead is told once; a
   later failing run updates that open task instead of adding one. A closed
   owner (not active, done, or merged with no lead left) goes to the lead to
   plan a follow-up project, and so does a step that still fails after
   :data:`ESCALATE_AFTER` fix tasks. No person is asked to decide anything.

Steps the server drafted from definitions of done for an initiative it
backfilled (``plan.acceptance_note``) get no due run: a merge waits until the
lead's next wave or a person restates them (a person may still run them now).

Steps that need something an env lacks (a synthetic sign-in the base env has
no secret for, the build or AI tier, an outside provider) are skipped by the
demo agent with the reason, and the cockpit shows them as "cannot run in envs".
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import PROJECT_KIND_INTEGRATION, TASK_CLOSED_STATES
from omnigent.server.factory import events
from omnigent.server.factory.charter import project_base_branch, project_kind
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.envelope import quote_spoofed_lines
from omnigent.server.factory.initiative_view import ACCEPTANCE_DRAFTED
from omnigent.server.factory.initiatives import (
    RUN_DONE,
    RUNS_KEPT,
    find,
    mutate,
    plan_of,
)
from omnigent.stores.factory_store import default_pr_url, now_s

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import (
        FactoryDemo,
        FactoryInitiative,
        FactoryPr,
        FactoryProject,
        FactoryTask,
    )
    from omnigent.server.factory.actor import Actor
    from omnigent.server.factory.base import BaseMove
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

ENV_WAIT_S: Final = 30 * 60
"""A due run waits this long for the base env to report ready at its head."""
RUN_EXPIRE_S: Final = 3 * 3600
"""A requested run with no result by then is expired (the next merge asks again)."""
ESCALATE_AFTER: Final = 3
"""Fix tasks of one step that closed while it still fails before the lead re-plans it."""
TASK_PREFIX: Final = "accept"
_TAG_RE: Final = re.compile(
    r"^\s*Acceptance\s+I-([1-9][0-9]{0,8})\s+run\s+([1-9][0-9]{0,4})\b", re.IGNORECASE
)
_LINE_MAX: Final = 300


def run_tag(ref: str, n: int) -> str:
    """:returns: The words a run's demo title carries, e.g. ``"Acceptance I-3 run 2"``."""
    return f"Acceptance {ref} run {n}"


def _one_line(text: Any, limit: int = _LINE_MAX) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _last(row: FactoryInitiative, statuses: frozenset[str] | set[str]) -> dict[str, Any] | None:
    return next((dict(r) for r in reversed(row.runs) if r.get("status") in statuses), None)


def _in_flight(row: FactoryInitiative, at: int | None = None) -> dict[str, Any] | None:
    """The requested run still on its way (not yet expired), if any."""
    now = now_s() if at is None else at
    asked = _last(row, {"requested"})
    if asked is None or now - int(asked.get("requested_at") or now) >= RUN_EXPIRE_S:
        return None
    return asked


def _set_run(
    n: int, update: Mapping[str, Any]
) -> Callable[[FactoryInitiative], dict[str, Any] | None]:
    def change(row: FactoryInitiative) -> dict[str, Any] | None:
        runs = [dict(r) for r in row.runs]
        for run in runs:
            if run.get("n") == n:
                run.update(update)
                return {"runs": runs}
        return None

    return change


# ── Where it runs ──────────────────────────────────────────────


def integration_for(
    deps: FactoryDeps, factory_id: str, repo: str | None, base: str | None
) -> FactoryProject | None:
    """The live base env project of ``repo#base`` (the factory's first one when unknown)."""
    rows = [
        p
        for p in deps.factory_store.list_projects(factory_id)
        if project_kind(p) == PROJECT_KIND_INTEGRATION and p.status == "active"
    ]
    for project in rows:
        if (
            repo
            and base
            and (project.repo or "").lower() == repo.lower()
            and project_base_branch(project) == base
        ):
            return project
    return rows[0] if rows and not (repo and base) else None


def env_of(project: FactoryProject | None) -> dict[str, Any]:
    """The base env as its keeper reported it (``{state, urls, deployed, at}``), or ``{}``."""
    if project is None or not isinstance(project.env, Mapping):
        return {}
    reported = project.env.get("reported")
    return dict(reported) if isinstance(reported, Mapping) else {}


def _target(
    deps: FactoryDeps, factory_id: str, row: FactoryInitiative, integ: FactoryProject
) -> Conversation | None:
    """The base env's lead, else the walking skeleton's (each has a demo agent)."""
    from omnigent.server.factory.control import find_project_pl

    for slug in (integ.slug, plan_of(row).get("skeleton")):
        if slug:
            pl = find_project_pl(deps, factory_id, slug)
            if pl is not None:
                return pl
    return None


# ── The request ────────────────────────────────────────────────


def request_text(
    row: FactoryInitiative, run: Mapping[str, Any], *, app: str | None, api: str | None
) -> str:
    """The ``[Factory acceptance · I-3 run 2 · base develop at abc1234]`` request."""
    plan = plan_of(row)
    sha = str(run.get("sha") or "")
    flag = (plan.get("flag") or {}).get("key") or "none"
    tag = run_tag(row.ref, int(run["n"]))
    lines = [
        f"[Factory acceptance · {row.ref} run {run['n']} · base {run.get('base') or '?'} at "
        f"{sha[:7]}] Run the acceptance steps of {row.ref} on the base env (not a preview) "
        f'and record them: send your demo agent (title "accept-{row.ref.lower()}-r{run["n"]}"):',
        f"ACCEPT initiative={row.ref} run={run['n']} pr={run.get('pr_url') or '?'} sha={sha} "
        f"app={app or '?'} api={api or '?'} flag={flag}",
        f"Title: {_one_line(row.title, 80)}",
        "Steps (one spec step each, in this order; step.skip(title, reason) for a step this "
        "env cannot run, e.g. no synthetic sign-in on the base env):",
    ]
    for step in plan["acceptance"]:
        expect = f" → {_one_line(step['expect'])}" if step.get("expect") else ""
        needs = f" [needs: {', '.join(step['needs'])}]" if step.get("needs") else ""
        lines.append(f"{step['n']}. {_one_line(step['step'])}{expect}{needs}")
    if run.get("note"):
        lines.append(f"Note: {_one_line(run['note'])}")
    lines.append(
        f'Title the demo "{tag}" and publish it with sys_factory_demo(pr_url=<pr>, '
        f"head_sha=<sha>, …) as the ACCEPT line gives them; the server records it on "
        f"{row.ref} and failed steps come back to their projects as tasks. No reply needed."
    )
    # Steps are people's and agents' words: a line that looks like a notice is quoted.
    return lines[0] + "\n" + quote_spoofed_lines("\n".join(lines[1:]))


async def dispatch(
    app_state: Any,
    factory_id: str,
    row: FactoryInitiative,
    *,
    sha: str | None = None,
    now: int | None = None,
) -> FactoryInitiative | None:
    """
    Ask a demo-capable member to run the initiative's due run now.

    :param sha: The head the base env serves now (else the run's own head).
    :returns: The stored row, or ``None`` when nothing was due or a run is
        still on its way (the due run waits for its result or expiry).
    """
    from omnigent.server.factory import control
    from omnigent.server.factory.context import factory_deps
    from omnigent.server.factory.envelope import display_title

    deps = factory_deps(app_state)
    run = _last(row, {"due"})
    if run is None or _in_flight(row, now) is not None:
        return None
    integ = await asyncio.to_thread(
        integration_for, deps, factory_id, run.get("repo"), run.get("base")
    )
    target = (
        await asyncio.to_thread(_target, deps, factory_id, row, integ)
        if integ is not None
        else None
    )
    env = env_of(integ)
    urls = env.get("urls") if isinstance(env.get("urls"), Mapping) else {}
    now = now_s()
    if sha:
        run["sha"] = sha
    if target is None:
        why = (
            "no base env: the factory has no integration project to run it on"
            if integ is None
            else f"{integ.slug} has no running lead to run it"
        )
        stored = await asyncio.to_thread(
            mutate, deps, row.id, _set_run(int(run["n"]), {"status": "blocked", "note": why})
        )
        await _emit(app_state, factory_id, row, "acceptance.blocked", f"cannot run: {why}", run)
        return stored
    update = {
        "status": "requested",
        "sha": run["sha"],
        "requested_at": now,
        "requested_of": target.id,
        "requested_of_title": display_title(target.title, target.id),
        "app": urls.get("app"),
        "api": urls.get("api"),
    }
    text = request_text(row, {**run, **update}, app=urls.get("app"), api=urls.get("api"))
    control.send_text(app_state, target.id, text, kind="request")
    stored = await asyncio.to_thread(mutate, deps, row.id, _set_run(int(run["n"]), update))
    await _emit(
        app_state,
        factory_id,
        row,
        "acceptance.requested",
        f"run {run['n']} asked of {update['requested_of_title']} at {str(run['sha'])[:7]}",
        {**run, **update},
    )
    return stored


async def _emit(
    app_state: Any,
    factory_id: str,
    row: FactoryInitiative,
    kind: str,
    what: str,
    run: Mapping[str, Any],
    *,
    severity: str = "info",
    extra: Mapping[str, Any] | None = None,
) -> None:
    await events.emit(
        app_state,
        factory_id,
        kind,
        severity=severity,
        subject=f"initiative:{row.ref}",
        summary=f"{row.ref} acceptance: {what}",
        data={
            "initiative_id": row.id,
            "ref": row.ref,
            "run": run.get("n"),
            "sha": run.get("sha"),
            "status": run.get("status"),
            **(extra or {}),
        },
    )


# ── Triggers ───────────────────────────────────────────────────


def _due_change(
    move: BaseMove, pr: FactoryPr
) -> Callable[[FactoryInitiative], dict[str, Any] | None]:
    def change(row: FactoryInitiative) -> dict[str, Any] | None:
        runs = [dict(r) for r in row.runs]
        entry = {
            "status": "due",
            "sha": move.to_sha,
            "pr": pr.number,
            "pr_url": pr.url or default_pr_url(pr.repo, pr.number),
            "repo": move.repo,
            "base": move.base,
            "due_at": move.at,
        }
        # At most one due run, at the newest head (it holds every earlier merge).
        due = next((r for r in reversed(runs) if r.get("status") == "due"), None)
        if due is not None:
            due.update(entry)
        else:
            n = max((int(r.get("n") or 0) for r in runs), default=0) + 1
            runs.append({"n": n, "by": "server", **entry})
        return {"runs": runs[-RUNS_KEPT:]}

    return change


def _merged_in(deps: FactoryDeps, move: BaseMove) -> list[FactoryPr]:
    """
    The tracked PRs a base move brings in (sync): the one at its new head and
    every one merged into the base since the server saw the previous head
    (two merges between polls leave only one at the head). The head's first.
    """
    out: dict[int, FactoryPr] = {}
    for pr in deps.factory_store.list_prs(move.factory_id, ["merged"]):
        if pr.repo.lower() != move.repo.lower() or not pr.project_slug:
            continue
        at_head = move.pr is not None and pr.number == move.pr
        since = bool(
            move.from_at
            and pr.base_ref == move.base
            and pr.merged_at
            and move.from_at < pr.merged_at <= move.at
        )
        if at_head or since:
            out[pr.number] = pr
    return sorted(
        out.values(), key=lambda p: (p.number == move.pr, p.merged_at or 0), reverse=True
    )


def _serves(integ: FactoryProject | None, sha: Any) -> bool:
    """Whether the base env reported ready at *sha*."""
    env = env_of(integ)
    deployed = env.get("deployed")
    served = deployed.get("sha") if isinstance(deployed, Mapping) else None
    return env.get("state") == "ready" and bool(sha) and served == sha


async def on_base_moved(app_state: Any, move: BaseMove) -> None:
    """A merge moved a base: an initiative whose project merged gets a due run."""
    from omnigent.server.factory.context import factory_deps

    deps = factory_deps(app_state)

    def run() -> list[tuple[FactoryInitiative, int]]:
        store = deps.factory_store
        merged = _merged_in(deps, move)
        if not merged:
            return []
        out: list[tuple[FactoryInitiative, int]] = []
        for row in store.list_initiatives(move.factory_id, status="open"):
            plan = plan_of(row)
            pr = next((p for p in merged if p.project_slug in plan["projects"]), None)
            if pr is None or not plan["acceptance"]:
                continue
            if plan.get("acceptance_note") == ACCEPTANCE_DRAFTED:
                continue  # steps drafted from DoDs run once a wave or a person restates them
            stored = mutate(deps, row.id, _due_change(move, pr))
            if stored is not None:
                out.append((stored, pr.number))
        return out

    for row, number in await asyncio.to_thread(run):
        due = _last(row, {"due"}) or {}
        await _emit(
            app_state,
            move.factory_id,
            row,
            "acceptance.due",
            f"run {due.get('n')} due at {move.to_sha[:7]} after !{number} merged",
            due,
        )
        integ = await asyncio.to_thread(
            integration_for, deps, move.factory_id, move.repo, move.base
        )
        if integ is None or _serves(integ, move.to_sha):
            await dispatch(app_state, move.factory_id, row, sha=move.to_sha)


async def on_event(app_state: Any, event: Any) -> None:
    """The base env reported ready at a head: send the runs that waited for it."""
    if event.kind != "env.reported" or not event.project_slug:
        return
    data = event.data or {}
    sha = data.get("sha")
    if data.get("state") != "ready" or not isinstance(sha, str) or not sha:
        return
    from omnigent.server.factory import base as base_mod
    from omnigent.server.factory.context import factory_deps

    deps = factory_deps(app_state)
    fid = event.factory_id

    def due_rows() -> list[FactoryInitiative]:
        store = deps.factory_store
        project = store.get_project(fid, event.project_slug)
        if project is None or project_kind(project) != PROJECT_KIND_INTEGRATION:
            return []
        branch = project_base_branch(project)
        root = deps.conversation_store.get_conversation(fid)
        head = base_mod.read_head(root.labels, project.repo or "", branch) if root else None
        out: list[FactoryInitiative] = []
        for row in store.list_initiatives(fid, status="open"):
            run = _last(row, {"due"})
            if run is None or (run.get("repo") or "").lower() != (project.repo or "").lower():
                continue
            if run.get("base") != branch:
                continue
            # The newest head holds every earlier merge, so it settles an older due run too.
            if run.get("sha") == sha or (head is not None and head.sha == sha):
                out.append(row)
        return out

    for row in await asyncio.to_thread(due_rows):
        await dispatch(app_state, fid, row, sha=sha)


async def sweep(app_state: Any, factory_id: str, *, now: int | None = None) -> list[str]:
    """
    Expire requested runs with no result; send a due run once nothing is on its
    way and its env serves its head, or after :data:`ENV_WAIT_S` regardless.

    :returns: ``"I-3 run 2 sent"`` / ``"I-3 run 2 expired"`` lines (for logs and tests).
    """
    from omnigent.server.factory.context import factory_deps

    deps = factory_deps(app_state)
    at = now_s() if now is None else now
    rows = await asyncio.to_thread(deps.factory_store.list_initiatives, factory_id, status="open")
    out: list[str] = []
    note = f"no result came back within {RUN_EXPIRE_S // 3600} h; the next merge asks again"
    for row in rows:
        stale = [
            dict(r)
            for r in row.runs
            if r.get("status") == "requested"
            and at - int(r.get("requested_at") or at) >= RUN_EXPIRE_S
        ]
        for asked in stale:
            row = (
                await asyncio.to_thread(
                    mutate,
                    deps,
                    row.id,
                    _set_run(int(asked["n"]), {"status": "expired", "note": note}),
                )
                or row
            )
            await _emit(
                app_state,
                factory_id,
                row,
                "acceptance.expired",
                f"run {asked['n']} expired: {note}",
                {**asked, "status": "expired"},
                severity="notice",
            )
            out.append(f"{row.ref} run {asked['n']} expired")
        due = _last(row, {"due"})
        if due is None or _in_flight(row, at) is not None:
            continue
        waited = at - int(due.get("due_at") or at) >= ENV_WAIT_S
        integ = await asyncio.to_thread(
            integration_for, deps, factory_id, due.get("repo"), due.get("base")
        )
        if not waited and not _serves(integ, due.get("sha")):
            continue
        if not _serves(integ, due.get("sha")):
            late = (
                f"the base env did not report ready at {str(due.get('sha'))[:7]} within "
                f"{ENV_WAIT_S // 60} min: check it serves that head first"
            )
            row = (
                await asyncio.to_thread(
                    mutate, deps, row.id, _set_run(int(due["n"]), {"note": late})
                )
                or row
            )
        if await dispatch(app_state, factory_id, row, now=at) is not None:
            out.append(f"{row.ref} run {due['n']} sent")
    return out


async def run_now(
    app_state: Any, factory_id: str, row: FactoryInitiative, actor: Actor
) -> FactoryInitiative:
    """
    A person asks for an acceptance run now, at the head the base env serves.

    :raises CockpitError: 409 ``no_steps``, ``no_pr_yet`` (the runner needs a
        PR of the initiative to file the run under) or ``run_open``.
    """
    from omnigent.server.factory import base as base_mod
    from omnigent.server.factory.context import factory_deps
    from omnigent.server.factory.projects import actor_name

    deps = factory_deps(app_state)
    plan = plan_of(row)
    if not plan["acceptance"]:
        raise CockpitError(409, "no_steps", f"{row.ref} has no acceptance steps yet")
    if _last(row, {"due", "requested"}) is not None:
        raise CockpitError(409, "run_open", f"a run of {row.ref} is already on its way")

    def facts() -> tuple[Any, str | None, str | None, str | None]:
        store = deps.factory_store
        prs = [p for p in store.list_prs(factory_id) if p.project_slug in plan["projects"]]
        merged = sorted(
            (p for p in prs if p.state == "merged"), key=lambda p: p.merged_at or 0, reverse=True
        )
        pick = merged[0] if merged else (sorted(prs, key=lambda p: p.number)[-1] if prs else None)
        project = next((store.get_project(factory_id, s) for s in plan["projects"]), None)
        repo = pick.repo if pick else (project.repo if project else None)
        branch = (pick.base_ref if pick and pick.base_ref else None) or (
            project_base_branch(project) if project else None
        )
        integ = integration_for(deps, factory_id, repo, branch)
        served = (env_of(integ).get("deployed") or {}).get("sha")
        root = deps.conversation_store.get_conversation(factory_id)
        head = base_mod.read_head(root.labels, repo or "", branch or "") if root else None
        return pick, repo, branch, served or (head.sha if head else None)

    pr, repo, branch, sha = await asyncio.to_thread(facts)
    if pr is None:
        raise CockpitError(
            409, "no_pr_yet", f"no project of {row.ref} has a pull request yet; run it after one"
        )
    if not sha:
        raise CockpitError(409, "no_base_head", "the server has not seen the base head yet")
    by = actor_name(actor)

    def change(current: FactoryInitiative) -> dict[str, Any]:
        runs = [dict(r) for r in current.runs]
        n = max((int(r.get("n") or 0) for r in runs), default=0) + 1
        runs.append(
            {
                "n": n,
                "status": "due",
                "sha": sha,
                "pr": pr.number,
                "pr_url": pr.url or default_pr_url(pr.repo, pr.number),
                "repo": repo,
                "base": branch,
                "due_at": now_s(),
                "by": by,
                "note": f"asked by {by}",
            }
        )
        return {"runs": runs[-RUNS_KEPT:]}

    stored = await asyncio.to_thread(mutate, deps, row.id, change) or row
    return await dispatch(app_state, factory_id, stored) or stored


# ── The report ─────────────────────────────────────────────────


def match_report(
    deps: FactoryDeps, factory_id: str, title: str, head_sha: str
) -> tuple[FactoryInitiative, dict[str, Any]] | None:
    """
    The acceptance run a published demo reports: its title starts with
    ``Acceptance I-<n> run <k>`` (sync). A demo that only mentions a run
    elsewhere (a fix's summary) is an ordinary PR demo.

    :returns: ``(initiative, run)``, or ``None`` for an ordinary PR demo.
    :raises CockpitError: 404 ``unknown_acceptance_run``, 409 ``run_reported``,
        400 ``sha_mismatch``.
    """
    match = _TAG_RE.match(title or "")
    if match is None:
        return None
    row = find(deps, factory_id, f"I-{match.group(1)}")
    n = int(match.group(2))
    run = next((dict(r) for r in (row.runs if row else []) if r.get("n") == n), None)
    if row is None or run is None:
        raise CockpitError(
            404, "unknown_acceptance_run", f"no acceptance run I-{match.group(1)} run {n} here"
        )
    if run.get("status") in RUN_DONE and run.get("status") != "ERROR":
        raise CockpitError(
            409,
            "run_reported",
            f"{row.ref} run {n} is already reported ({run['status']}); the next merge or a "
            "person starts the next run",
        )
    if str(run.get("sha") or "").lower() != head_sha.lower():
        raise CockpitError(
            400,
            "sha_mismatch",
            f"{row.ref} run {n} runs at {str(run.get('sha'))[:12]}; publish it with "
            f"head_sha={run.get('sha')}",
        )
    return row, run


def may_report(
    deps: FactoryDeps,
    factory_id: str,
    row: FactoryInitiative,
    run: Mapping[str, Any],
    *,
    root_session_id: str,
    project: str | None,
) -> bool:
    """
    Whether a member may report *run* (sync): the run was asked of its tree's
    lead, or it works for the base env or the walking skeleton.
    """
    from omnigent.server.factory.control import same_id

    if same_id(root_session_id, run.get("requested_of")):
        return True
    if not project:
        return False
    integ = integration_for(deps, factory_id, run.get("repo"), run.get("base"))
    return project in {integ.slug if integ is not None else None, plan_of(row).get("skeleton")}


def _file_path(demo_id: str, file_id: str) -> str:
    return f"/v1/factory/demos/{demo_id}/files/{file_id}"


def run_steps(demo: FactoryDemo) -> list[dict[str, Any]]:
    """The demo's steps as a run stores them (``{n, title, status, note?, screenshot?}``)."""
    steps = (demo.manifest or {}).get("steps")
    out: list[dict[str, Any]] = []
    for index, step in enumerate(steps if isinstance(steps, list) else [], start=1):
        if not isinstance(step, Mapping):
            continue
        n = step.get("n")
        item: dict[str, Any] = {
            "n": n if isinstance(n, int) and not isinstance(n, bool) else index,
            "title": _one_line(step.get("title"), 200),
            "status": str(step.get("status") or "").lower() or None,
        }
        if step.get("note"):
            item["note"] = _one_line(step["note"])
        shot = step.get("screenshot_file_id")
        if isinstance(shot, str):
            item["screenshot"] = _file_path(demo.id, shot)
        out.append(item)
    return out


def _video(demo: FactoryDemo) -> str | None:
    video = next((f for f in demo.files if f.get("kind") == "video"), None)
    return _file_path(demo.id, str(video["file_id"])) if video else None


def _task_text(
    row: FactoryInitiative,
    run: Mapping[str, Any],
    step: Mapping[str, Any],
    *,
    first: int | None = None,
) -> str:
    """The fix task's text; *first* is the run that first found it failing (an update)."""
    plan = plan_of(row)
    accept = next((s for s in plan["acceptance"] if s.get("n") == step.get("n")), {})
    flag = (plan.get("flag") or {}).get("key")
    still = f", still failing since run {first}" if first and first != run.get("n") else ""
    parts = [
        f"Acceptance step {step.get('n')} of {row.ref} ({_one_line(row.title, 80)}) fails on "
        f"the base env at {str(run.get('sha'))[:7]} (run {run.get('n')}{still}).",
        f"Step: {_one_line(accept.get('step') or step.get('title'))}.",
    ]
    if accept.get("expect"):
        parts.append(f"Expected: {_one_line(accept['expect'])}.")
    if step.get("note"):
        parts.append(f"Seen: {_one_line(step['note'])}.")
    if step.get("screenshot"):
        parts.append(f"Screenshot: {step['screenshot']}.")
    parts.append(
        "Fix it" + (f" behind the flag {flag}" if flag else "") + "; the next merge into the "
        "base runs the acceptance again."
    )
    return " ".join(parts)[:4000]


def _family(tasks: Sequence[FactoryTask], key: str) -> list[FactoryTask]:
    """The fix tasks of one step (``accept-i-1-s2`` and its ``-r<n>`` re-runs), oldest first."""
    mine = [t for t in tasks if t.key == key or t.key.startswith(f"{key}-r")]
    return sorted(mine, key=lambda t: t.number)


def _passed_at(row: FactoryInitiative, n: Any) -> int:
    """When step *n* last passed (``0``: never)."""
    for run in reversed(row.runs):
        for step in run.get("steps") or []:
            if step.get("n") == n and step.get("status") == "pass":
                return int(run.get("reported_at") or 0)
    return 0


def _first_failing(row: FactoryInitiative, n: Any, since: int) -> int | None:
    """The first run since *since* that found step *n* failing."""
    for run in row.runs:
        if int(run.get("reported_at") or 0) < since:
            continue
        if any(s.get("n") == n and s.get("status") == "fail" for s in run.get("steps") or []):
            return int(run.get("n") or 0) or None
    return None


async def _to_lead(app_state: Any, factory_id: str, head: str, why: str, body: str) -> None:
    from omnigent.server.factory import control

    text = f"{head} {why} No person needs to decide this.\n" + quote_spoofed_lines(body)
    control.send_text(app_state, factory_id, text, kind="request")


async def _failures_to_tasks(
    app_state: Any, factory_id: str, row: FactoryInitiative, run: Mapping[str, Any]
) -> tuple[list[str], list[int]]:
    """
    Each failed step becomes (or updates) a task of its project; returns
    ``(refs, steps the lead was told to re-plan)``.

    A step whose open fix task exists updates it (no new task, no new ping);
    once :data:`ESCALATE_AFTER` fix tasks closed since it last passed, the
    lead re-plans it, told once.
    """
    from omnigent.server.factory import control, tasks
    from omnigent.server.factory.actor import Actor
    from omnigent.server.factory.context import factory_deps

    deps = factory_deps(app_state)
    plan = plan_of(row)
    refs: list[str] = []
    escalated: list[int] = []
    for step in run.get("steps") or []:
        if step.get("status") != "fail":
            continue
        n = step.get("n")
        accept = next((s for s in plan["acceptance"] if s.get("n") == n), {})
        owner = accept.get("owner") or plan.get("skeleton") or next(iter(plan["projects"]), None)
        if not owner:
            continue
        project = await asyncio.to_thread(deps.factory_store.get_project, factory_id, owner)
        live = project is not None and project.status == "active" and project.stage != "done"
        pl = (
            await asyncio.to_thread(control.find_project_pl, deps, factory_id, owner)
            if live
            else None
        )
        head = f"[Factory acceptance · {row.ref} run {run.get('n')} FAIL · step {n}"
        if not live or (pl is None and project is not None and project.stage == "merged"):
            await _to_lead(
                app_state,
                factory_id,
                f"{head} · {owner} is closed]",
                f"Plan the fix as a follow-up project of {row.ref}: sys_factory_project with the "
                f'analysis initiative {{"id": "{row.ref}"}} (it applies for the person who '
                "asked).",
                _task_text(row, run, step),
            )
            refs.append(f"lead:{owner}")
            continue
        base = f"{TASK_PREFIX}-{row.ref.lower()}-s{n}"
        family = _family(
            await asyncio.to_thread(deps.factory_store.list_tasks, factory_id, project_slug=owner),
            base,
        )
        since = _passed_at(row, n)
        first = _first_failing(row, n, since)
        open_ = [t for t in family if t.state not in TASK_CLOSED_STATES]
        if open_:
            task = open_[-1]
            item = tasks.TaskIn(key=task.key, do=_task_text(row, run, step, first=first))
            try:
                await tasks.upsert_tasks(app_state, factory_id, owner, [item], Actor.server())
            except CockpitError as exc:
                _logger.warning("factory: could not update %s: %s", task.ref, exc)
            refs.append(task.ref)
            continue
        tried = [t for t in family if t.created_at >= since]
        if len(tried) >= ESCALATE_AFTER:
            told = any(
                n in (r.get("escalated") or []) and int(r.get("reported_at") or 0) >= since
                for r in row.runs
            )
            if not told:
                await _to_lead(
                    app_state,
                    factory_id,
                    f"{head} · fixes did not hold]",
                    f"Step {n} still fails after {len(tried)} fix tasks of {owner} "
                    f"({', '.join(t.ref for t in tried)}): re-plan it (a follow-up project of "
                    f'{row.ref} with {{"id": "{row.ref}"}}, or a smaller step), or tell the '
                    "person who asked what blocks it. The server adds no more tasks for it.",
                    _task_text(row, run, step, first=first),
                )
                escalated.append(int(n))
            refs.append(f"lead:{owner}")
            continue
        key = base if not family else f"{base}-r{run.get('n')}"
        item = tasks.TaskIn(
            key=key,
            title=f"Acceptance {row.ref} step {n} fails on the base env",
            kind="implement",
            do=_task_text(row, run, step, first=first),
            priority=1,
        )
        try:
            await tasks.upsert_tasks(app_state, factory_id, owner, [item], Actor.server())
        except CockpitError as exc:
            _logger.warning("factory: no task for %s step %s: %s", row.ref, n, exc)
            continue
        task = await asyncio.to_thread(deps.factory_store.get_task_by_key, factory_id, owner, key)
        ref = task.ref if task is not None else key
        refs.append(ref)
        if pl is not None:
            text = (
                f"{head}] {ref} {key} is in your work graph: the step fails on the base env. "
                'Call sys_factory_task(op="ready") and dispatch it when a slot is free. '
                "No reply needed.\n" + quote_spoofed_lines(_task_text(row, run, step))
            )
            control.send_text(app_state, pl.id, text, kind="request")
    return refs, escalated


async def _send_waiting(app_state: Any, factory_id: str, row: FactoryInitiative) -> None:
    """A due run that waited for the reported one goes out once its env serves its head."""
    from omnigent.server.factory.context import factory_deps

    due = _last(row, {"due"})
    if due is None:
        return
    deps = factory_deps(app_state)
    integ = await asyncio.to_thread(
        integration_for, deps, factory_id, due.get("repo"), due.get("base")
    )
    if integ is None or _serves(integ, due.get("sha")):
        await dispatch(app_state, factory_id, row)


async def record_report(
    app_state: Any,
    factory_id: str,
    row: FactoryInitiative,
    run: Mapping[str, Any],
    demo: FactoryDemo,
    *,
    by: str,
) -> FactoryInitiative:
    """Store a published run on the initiative and turn its failed steps into tasks."""
    from omnigent.server.factory.context import factory_deps

    deps = factory_deps(app_state)
    n = int(run["n"])
    steps = run_steps(demo)
    update = {
        "status": demo.status,
        "reported_at": now_s(),
        "demo_id": demo.id,
        "steps": steps,
        "video": _video(demo),
        "summary": _one_line(demo.summary, 300) or None,
        "reported_by": by,
    }
    stored = await asyncio.to_thread(mutate, deps, row.id, _set_run(n, update)) or row
    refs, escalated = await _failures_to_tasks(app_state, factory_id, stored, {**run, **update})
    if refs or escalated:
        extra: dict[str, Any] = {"tasks": refs}
        if escalated:
            extra["escalated"] = escalated
        stored = await asyncio.to_thread(mutate, deps, row.id, _set_run(n, extra)) or stored
    failed = [s["n"] for s in steps if s.get("status") == "fail"]
    skipped = [s["n"] for s in steps if s.get("status") == "skipped"]
    what = f"run {n} {demo.status}"
    if failed:
        steps_text = ", ".join(str(f) for f in failed)
        what += f" (failed step {steps_text}; tasks {', '.join(refs) or 'none'})"
    elif skipped:
        what += f" ({len(skipped)} cannot run in envs)"
    await _emit(
        app_state,
        factory_id,
        stored,
        "acceptance.reported",
        what,
        {**run, **update},
        severity="notice",
        extra={"demo_id": demo.id, "failed": failed, "tasks": refs},
    )
    await _send_waiting(app_state, factory_id, stored)
    return stored


def register() -> None:
    """Listen for base moves and env reports (idempotent)."""
    from omnigent.server.factory import base

    if on_event not in events._listeners:
        events.add_listener(on_event)
    if on_base_moved not in base._hooks:
        base.on_base_moved(on_base_moved)


register()


__all__ = [
    "ENV_WAIT_S",
    "ESCALATE_AFTER",
    "RUN_EXPIRE_S",
    "TASK_PREFIX",
    "dispatch",
    "env_of",
    "integration_for",
    "match_report",
    "may_report",
    "on_base_moved",
    "on_event",
    "record_report",
    "register",
    "request_text",
    "run_now",
    "run_steps",
    "run_tag",
    "sweep",
]
