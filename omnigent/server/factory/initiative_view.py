"""How an initiative looks: pure views built from one viewer's filtered snapshot.

:func:`initiative_view` is the initiative page's JSON, :func:`initiative_line`
the one line Home, the status summary and the Slack digest show. Both are
pure and deterministic (no I/O): a project the viewer cannot see is left out,
and the state comes from the projects, the PRs, the base env and the runs.

States: ``planned`` (nothing started), ``building``, ``on_base`` (the walking
skeleton merged and the base env serves it), ``accepting`` (a run is on its
way), ``failing`` (the last run failed a step), ``done`` (every project is
closed (status merged or done, or stage done), no planned wave is left and
the last run passed at least one step and failed none), ``closed`` (a person
closed it) and ``cancelled``.

This module imports nothing from the factory's service modules, so the
status summary and the "Needs you" list can import it at module level.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from omnigent.entities.factory import FactoryInitiative
from omnigent.server.factory.charter import change_dependency

DONE_SHOWN_S: Final = 3 * 86_400
"""A finished or closed initiative stays on Home and in the digest this long."""
ROLE_SKELETON: Final = "skeleton"
ROLE_SURFACE: Final = "surface"
ROLE_PART: Final = "part"
RUN_OPEN: Final = frozenset({"due", "requested"})
RUN_DONE: Final = frozenset({"PASS", "PARTIAL", "FAIL", "ERROR"})
RUN_PASSED: Final = frozenset({"PASS", "PARTIAL"})
_MERGED_STAGES: Final = frozenset({"merged", "done"})
_CLOSED_STATUSES: Final = frozenset({"merged", "done"})

_WAIT_PREFIX_RE: Final = re.compile(r"^.*?\bwaiting (?:on|for) ", re.IGNORECASE)
"""A wait's label without its ``T-16 waiting on`` lead-in (the line says "waiting on")."""

State = Literal[
    "planned", "building", "on_base", "accepting", "failing", "done", "closed", "cancelled"
]


def plan_of(row: FactoryInitiative | Mapping[str, Any]) -> dict[str, Any]:
    """:returns: The plan of a row (or its ``to_dict()``) with every key present."""
    raw = row.plan if isinstance(row, FactoryInitiative) else row.get("plan")
    plan = dict(raw or {})
    plan.setdefault("acceptance", [])
    plan.setdefault("projects", [])
    plan.setdefault("user_facing", [])
    plan.setdefault("why_wait", {})
    plan.setdefault("waves", [])
    plan.setdefault("history", [])
    return plan


def role_of(slug: str, plan: Mapping[str, Any]) -> str:
    """
    ``skeleton``, ``surface`` (faces users: named so, or owns an acceptance
    step, which is by definition what people try) or ``part`` of the initiative.
    """
    if slug == plan.get("skeleton"):
        return ROLE_SKELETON
    owners = {s.get("owner") for s in plan.get("acceptance") or [] if isinstance(s, Mapping)}
    return ROLE_SURFACE if slug in (plan.get("user_facing") or []) or slug in owners else ROLE_PART


def _merged(project: Mapping[str, Any] | None) -> bool:
    if project is None:
        return False
    return project.get("stage") in _MERGED_STAGES or project.get("status") in _CLOSED_STATUSES


def _project_prs(prs: Sequence[Mapping[str, Any]], slug: str) -> list[Mapping[str, Any]]:
    return [p for p in prs if p.get("project_slug") == slug]


def _first_merge(prs: Sequence[Mapping[str, Any]]) -> int | None:
    stamps = [
        int((p.get("merged") or {}).get("at") or 0)
        for p in prs
        if p.get("state") == "merged" and p.get("merged")
    ]
    stamps = [s for s in stamps if s]
    return min(stamps) if stamps else None


def _dep_met(dep: Mapping[str, Any] | None, prs: Sequence[Mapping[str, Any]], mode: str) -> bool:
    if dep is None:
        return False
    if _merged(dep):
        return True
    mine = _project_prs(prs, str(dep.get("slug")))
    if any(p.get("state") == "merged" for p in mine):
        return mode == "ready" or all(p.get("state") != "open" for p in mine)
    return mode == "ready" and any(p.get("ready") for p in mine)


def _item_met(
    item: str,
    by_slug: Mapping[str, Mapping[str, Any]],
    prs: Sequence[Mapping[str, Any]],
    mode: str,
) -> bool:
    """A ``depends_on`` item is met: its project's work is there, or its change merged."""
    number = change_dependency(item)
    if number is not None:
        return any(p.get("number") == number and p.get("state") == "merged" for p in prs)
    return _dep_met(by_slug.get(item), prs, mode)


def _waiting_on(
    project: Mapping[str, Any],
    by_slug: Mapping[str, Mapping[str, Any]],
    prs: Sequence[Mapping[str, Any]],
    decisions: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """
    What a live project waits on: the snapshot's own waits when it has them,
    else its unmet dependencies, a person's merge and open questions.
    """
    if project.get("status") not in (None, "active") or _merged(project):
        return []
    known = [
        _WAIT_PREFIX_RE.sub("", str(w.get("label") or ""))
        for w in project.get("waiting_on") or []
        if isinstance(w, Mapping) and w.get("label")
    ]
    if known:
        return known
    out: list[str] = []
    mode = str(project.get("depends_mode") or "merged")
    for dep in project.get("depends_on") or []:
        if not _item_met(dep, by_slug, prs, mode):
            out.append(dep)
    mine = _project_prs(prs, str(project.get("slug")))
    ready = [p for p in mine if p.get("state") == "open" and p.get("ready")]
    if ready:
        out.append(f"a merge of !{ready[0].get('number')}")
    for decision_id in project.get("open_decision_ids") or []:
        decision = decisions.get(decision_id)
        if decision is not None and decision.get("ref"):
            out.append(f"an answer to {decision['ref']}")
    return out


def _run_tally(
    plan: Mapping[str, Any], runs: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """``(tally, last finished run, open run)`` of the acceptance."""
    steps = plan.get("acceptance") or []
    finished = [r for r in runs if r.get("status") in RUN_DONE]
    last = finished[-1] if finished else None
    pending = next((r for r in reversed(runs) if r.get("status") in RUN_OPEN), None)
    tally: dict[str, Any] = {
        "total": len(steps),
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "status": last.get("status") if last else None,
        "run": last.get("n") if last else None,
        "running": pending is not None,
    }
    for step in (last or {}).get("steps") or []:
        status = step.get("status")
        if status == "pass":
            tally["passed"] += 1
        elif status == "fail":
            tally["failed"] += 1
        elif status == "skipped":
            tally["skipped"] += 1
    return tally, last, pending


def _closed(project: Mapping[str, Any]) -> bool:
    """A project that finished for good (a merged PR alone may be one of several)."""
    return project.get("status") in _CLOSED_STATUSES or project.get("stage") == "done"


def _state(
    row: Mapping[str, Any],
    projects: Sequence[Mapping[str, Any]],
    tally: Mapping[str, Any],
    skeleton_on_base: bool,
    *,
    waves_left: bool = False,
) -> State:
    status = row.get("status")
    if status == "cancelled":
        return "cancelled"
    if status == "done":
        return "closed"
    live = [p for p in projects if not p.get("hidden") and p.get("status") != "cancelled"]
    if projects and not live and all(not p.get("hidden") for p in projects):
        return "cancelled"
    if tally.get("status") == "FAIL" or (tally.get("status") == "ERROR" and tally.get("failed")):
        return "failing"
    if tally.get("running"):
        return "accepting"
    finished = bool(live) and all(_closed(p) for p in live) and not waves_left
    accepted = (
        tally.get("status") in RUN_PASSED and tally.get("passed") and not tally.get("failed")
    )
    if finished and (accepted or not tally.get("total")):
        return "done"
    if skeleton_on_base:
        return "on_base"
    if any(p.get("stage") not in (None, "queued") for p in live):
        return "building"
    return "planned"


def _next_wave(plan: Mapping[str, Any]) -> dict[str, Any] | None:
    current = max((int(h.get("wave") or 1) for h in plan.get("history") or []), default=0)
    for wave in sorted(plan.get("waves") or [], key=lambda w: int(w.get("n") or 0)):
        if int(wave.get("n") or 0) > current:
            return {
                "n": int(wave["n"]),
                "goal": str(wave.get("goal") or ""),
                "projects": list(wave.get("projects") or []),
            }
    return None


def _evidence(base_url: str, path: str | None) -> str | None:
    return f"{base_url}{path}" if path else None


def initiative_view(
    row: FactoryInitiative | Mapping[str, Any],
    body: Mapping[str, Any],
    *,
    now: float,
    base_url: str = "",
) -> dict[str, Any]:
    """
    One initiative as a viewer sees it (``InitiativeView`` JSON). Pure.

    :param row: The stored row (or its ``to_dict()``).
    :param body: The viewer's filtered snapshot (projects, PRs, decisions, the base).
    :param base_url: Prefix for evidence links (empty: cockpit-relative paths).
    """
    data = row.to_dict() if isinstance(row, FactoryInitiative) else dict(row)
    plan = plan_of(data)
    factory = body.get("factory") or {}
    fid = str(factory.get("id") or data.get("factory_id") or "")
    by_slug = {str(p.get("slug")): p for p in body.get("projects") or []}
    prs = list(body.get("prs") or [])
    decisions = {str(d.get("id")): d for d in body.get("decisions") or []}
    waves = {s: int(h.get("wave") or 1) for h in plan["history"] for s in h.get("added") or []}
    projects: list[dict[str, Any]] = []
    cost = 0.0
    priced = False
    for slug in plan["projects"]:
        view = by_slug.get(slug)
        if view is None:
            projects.append({"slug": slug, "hidden": True, "role": role_of(slug, plan)})
            continue
        mine = _project_prs(prs, slug)
        reasons = view.get("depends_why") or {}
        charter = view.get("charter") or {}
        projects.append(
            {
                "slug": slug,
                "title": view.get("title") or slug,
                "outcome": charter.get("outcome"),
                "stage": view.get("stage"),
                "status": view.get("status") or "active",
                "role": role_of(slug, plan),
                "wave": waves.get(slug, 1),
                "merged": _merged(view)
                or (
                    any(p.get("state") == "merged" for p in mine)
                    and not any(p.get("state") == "open" for p in mine)
                ),
                "depends_on": [
                    {
                        "slug": dep,
                        "why": reasons.get(dep),
                        "met": _item_met(
                            dep, by_slug, prs, str(view.get("depends_mode") or "merged")
                        ),
                    }
                    for dep in view.get("depends_on") or []
                ],
                "waiting_on": _waiting_on(view, by_slug, prs, decisions),
                "prs": {
                    "open": sum(1 for p in mine if p.get("state") == "open"),
                    "ready": sum(1 for p in mine if p.get("state") == "open" and p.get("ready")),
                    "merged": sum(1 for p in mine if p.get("state") == "merged"),
                },
                "cost_usd": view.get("cost_usd"),
                "href": f"/factory/{fid}/projects/{slug}",
            }
        )
        if isinstance(view.get("cost_usd"), int | float):
            cost += float(view["cost_usd"])
            priced = True
    base = factory.get("base") or None
    env = ((base or {}).get("env") or {}).get("reported") or {}
    skeleton = plan.get("skeleton")
    skeleton_prs = _project_prs(prs, str(skeleton)) if skeleton else []
    merged_at = _first_merge(skeleton_prs)
    runs = list(data.get("runs") or [])
    tally, last, _pending = _run_tally(plan, runs)
    skeleton_on_base = bool(
        merged_at
        and (
            (env.get("state") == "ready" and int(env.get("at") or 0) >= merged_at)
            or (last is not None and int(last.get("reported_at") or 0) >= merged_at)
        )
    )
    next_wave = _next_wave(plan)
    state = _state(data, projects, tally, skeleton_on_base, waves_left=next_wave is not None)
    last_steps = {int(s.get("n") or 0): s for s in (last or {}).get("steps") or []}
    acceptance = []
    for step in plan["acceptance"]:
        seen = last_steps.get(int(step.get("n") or 0))
        item = dict(step)
        item["last"] = (
            {
                "status": seen.get("status"),
                "note": seen.get("note"),
                "screenshot_url": _evidence(base_url, seen.get("screenshot")),
                "run": (last or {}).get("n"),
            }
            if seen is not None
            else None
        )
        acceptance.append(item)
    view_runs = [
        {
            "n": r.get("n"),
            "status": r.get("status"),
            "sha": r.get("sha"),
            "pr": r.get("pr"),
            "due_at": r.get("due_at"),
            "requested_at": r.get("requested_at"),
            "reported_at": r.get("reported_at"),
            "requested_of": r.get("requested_of_title"),
            "note": r.get("note"),
            "video_url": _evidence(base_url, r.get("video")),
            "demo_id": r.get("demo_id"),
            "tasks": list(r.get("tasks") or []),
            "app_url": r.get("app"),
        }
        for r in runs
    ]
    asked_at = int(plan.get("asked_at") or data.get("created_at") or 0)
    live = [p for p in projects if not p.get("hidden") and p.get("status") != "cancelled"]
    waiting = [
        f"{p['slug']} ({w})" if w != p["slug"] else w
        for p in live
        for w in p.get("waiting_on") or []
    ]
    return {
        "id": data["id"],
        "ref": data.get("ref") or f"I-{data['number']}",
        "number": data["number"],
        "title": data["title"],
        "ask": data.get("ask") or "",
        "asked_by": data.get("requested_by"),
        "asked_at": asked_at,
        "age_s": max(0, int(now) - asked_at) if asked_at else None,
        "ask_ref": data.get("ask_ref"),
        "thread": data.get("thread"),
        "outcome": data.get("outcome"),
        "flag": plan.get("flag"),
        "status": data.get("status") or "open",
        "state": state,
        "skeleton": skeleton,
        "skeleton_on_base": skeleton_on_base,
        "merged": sum(1 for p in live if p.get("merged")),
        "total": len(live),
        "hidden": sum(1 for p in projects if p.get("hidden")),
        "projects": projects,
        "acceptance": acceptance,
        "tally": tally,
        "runs": view_runs,
        "next_wave": next_wave,
        "waves": [
            {
                "n": int(h.get("wave") or 1),
                "proposal": h.get("proposal"),
                "added": list(h.get("added") or []),
                "at": h.get("at"),
            }
            for h in plan["history"]
        ],
        "waiting_on": waiting,
        "cost_usd": round(cost, 2) if priced else None,
        "base": {
            "repo": (base or {}).get("repo"),
            "branch": (base or {}).get("branch"),
            "head_sha": (base or {}).get("head_sha"),
            "env_state": env.get("state"),
            "env_sha": (env.get("deployed") or {}).get("sha"),
            "app_url": (env.get("urls") or {}).get("app"),
            "env_project": (base or {}).get("env_project"),
        }
        if base
        else None,
        "opportunity": plan.get("opportunity"),
        "created_by": data.get("created_by"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "closed_at": data.get("closed_at"),
        "closed_by": data.get("closed_by"),
        "href": f"/factory/{fid}/initiatives/{data.get('ref') or 'I-' + str(data['number'])}",
    }


def _acceptance_phrase(view: Mapping[str, Any]) -> str:
    tally = view["tally"]
    if not tally["total"]:
        return "no acceptance steps yet"
    if tally["running"] and tally["status"] is None:
        return "acceptance running"
    if tally["status"] is None:
        return "no acceptance run yet"
    if tally["status"] == "FAIL":
        failed = next(
            (s["n"] for s in view["acceptance"] if (s.get("last") or {}).get("status") == "fail"),
            None,
        )
        where = f" at step {failed}" if failed else ""
        return f"acceptance failing{where}"
    if tally["status"] == "ERROR":
        return "acceptance run broke (harness)"
    done = f"acceptance {tally['passed']}/{tally['total']} pass"
    if tally["skipped"]:
        done += f", {tally['skipped']} cannot run in envs"
    return done + (" (running again)" if tally["running"] else "")


def line_text(view: Mapping[str, Any]) -> str:
    """
    The one line Home, the status summary and the digest show, e.g. ``"Sample
    assistant: 2/4 merged, skeleton not on base env yet, waiting on core (a merge of !12)"``.
    """
    state = view["state"]
    head = f"{view['title']}: {view['merged']}/{view['total']} merged"
    if state == "cancelled":
        return f"{view['title']}: cancelled"
    if state == "closed":
        return f"{head}, closed"
    parts = [head]
    if state == "done":
        parts.append("done, " + _acceptance_phrase(view))
        return ", ".join(parts)
    if view.get("skeleton"):
        parts.append(
            "skeleton on base env" if view["skeleton_on_base"] else "skeleton not on base env yet"
        )
    parts.append(_acceptance_phrase(view))
    waiting = view.get("waiting_on") or []
    if waiting:
        more = f" +{len(waiting) - 2}" if len(waiting) > 2 else ""
        parts.append(f"waiting on {', '.join(waiting[:2])}{more}")
    return ", ".join(parts)


class InitiativeTally(BaseModel):
    """How the last acceptance run went."""

    model_config = ConfigDict(extra="forbid")

    total: int
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    status: str | None = None
    run: int | None = None
    running: bool = False


class InitiativeLine(BaseModel):
    """
    One initiative as one line (Home's card, the status summary, the digest).

    :param line: e.g. ``"Sample assistant: 2/4 merged, skeleton not on base env yet, …"``.
    :param since: When it was asked (epoch seconds).
    :param try_url: The base env's app once the skeleton is on it (people try it there).
    :param flag: The flag key it ships behind (turn it on to try it).
    """

    model_config = ConfigDict(extra="forbid")

    ref: str
    title: str
    state: str
    line: str
    merged: int
    total: int
    skeleton: str | None = None
    skeleton_on_base: bool = False
    acceptance: InitiativeTally
    waiting_on: list[str] = Field(default_factory=list)
    next_wave: str | None = None
    cost_usd: float | None = None
    since: int
    href: str
    try_url: str | None = None
    flag: str | None = None


def try_url(view: Mapping[str, Any]) -> str | None:
    """The base env's app link once the walking skeleton is on it, else ``None``."""
    base = view.get("base") or {}
    url = base.get("app_url")
    return url if view.get("skeleton_on_base") and isinstance(url, str) and url else None


def initiative_line(view: Mapping[str, Any]) -> dict[str, Any]:
    """:returns: The ``InitiativeLine`` JSON of an :func:`initiative_view`."""
    nxt = view.get("next_wave")
    return InitiativeLine(
        ref=view["ref"],
        title=view["title"],
        state=view["state"],
        line=line_text(view),
        merged=view["merged"],
        total=view["total"],
        skeleton=view.get("skeleton"),
        skeleton_on_base=view["skeleton_on_base"],
        acceptance=InitiativeTally(**view["tally"]),
        waiting_on=list(view.get("waiting_on") or [])[:5],
        next_wave=f"wave {nxt['n']}: {nxt['goal']}" if nxt else None,
        cost_usd=view.get("cost_usd"),
        since=int(view.get("asked_at") or 0),
        href=view["href"],
        try_url=try_url(view),
        flag=(view.get("flag") or {}).get("key"),
    ).model_dump(mode="json")


def shown(row: Mapping[str, Any], now: float) -> bool:
    """An open initiative, or one closed in the last :data:`DONE_SHOWN_S`."""
    if row.get("status") == "open":
        return True
    return int(row.get("closed_at") or row.get("updated_at") or 0) >= now - DONE_SHOWN_S


def lines_for(
    rows: Sequence[Mapping[str, Any]], body: Mapping[str, Any], *, now: float
) -> list[dict[str, Any]]:
    """
    The initiatives a viewer sees as lines, newest first; a finished one drops
    off after :data:`DONE_SHOWN_S`. Pure.

    :param rows: Initiative rows (``to_dict()``).
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not shown(row, now):
            continue
        view = initiative_view(row, body, now=now)
        if view["state"] == "done" and int(view.get("updated_at") or 0) < now - DONE_SHOWN_S:
            continue
        if view["total"] == 0 and view["hidden"]:
            continue
        out.append(initiative_line(view))
    return out


__all__ = [
    "DONE_SHOWN_S",
    "ROLE_PART",
    "ROLE_SKELETON",
    "ROLE_SURFACE",
    "RUN_DONE",
    "RUN_OPEN",
    "RUN_PASSED",
    "InitiativeLine",
    "InitiativeTally",
    "State",
    "initiative_line",
    "initiative_view",
    "line_text",
    "lines_for",
    "plan_of",
    "role_of",
    "shown",
    "try_url",
]
