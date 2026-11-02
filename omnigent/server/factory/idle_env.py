"""A project that only waits for a human merge gives its dev env back.

A project's dev env (one long-running Tilt env at origin/<base>, for its ICs,
about 8 pods) holds cluster capacity even when the project only waits for a
person to merge its PRs. Once every open PR of a project is ready for a human
merge and nothing else is left to do, the supervisor asks the PL to take the
dev env down (env_keeper ``DOWN reason=idle``). Each PR's preview stays up: it
is what the human reviews. When work comes back (a human change request, a
failed gate, a new or restarted task) it asks the PL to bring the dev env back
(env_keeper ``UP``). The server never touches a namespace itself; the PL's
env_keeper does, as for a pause.

The asks live in the event log, so a server roll keeps them: ``env.idle_down``
says the PL was asked to take the dev env down, ``env.idle_up`` that it was
asked to bring it back. ``OMNIGENT_FACTORY_IDLE_ENV_S`` (default 900; ``0``
turns this off) is how long every PR must have been ready before the ask.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    LABEL_ID,
    LABEL_STATE,
    PROJECT_KIND_INTEGRATION,
    STATE_ACTIVE,
    TASK_CLOSED_STATES,
)
from omnigent.factory_gates import GATE_FAIL
from omnigent.server.factory import events
from omnigent.server.factory.charter import project_base_branch, project_kind
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.mailer import get_mailer
from omnigent.server.factory.stage import changes_requested_on_head, ready_for_human
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryEvent, FactoryPr, FactoryProject, FactoryTask
    from omnigent.stores.factory_store import FactoryStore

_logger = logging.getLogger(__name__)

IDLE_ENV_ENV: Final = "OMNIGENT_FACTORY_IDLE_ENV_S"
DEFAULT_IDLE_ENV_S: Final = 900.0
DOWN_KIND: Final = "env.idle_down"
"""The PL was asked to take its dev env down (the project only waits for a merge)."""
UP_KIND: Final = "env.idle_up"
"""The PL was asked to bring its dev env back (work came back)."""

ENV_UP_STATES: Final = frozenset({"starting", "ready", "degraded", "failed"})
"""Reported dev env states that still hold a namespace."""
ENV_DOWN_STATES: Final = frozenset({"down", "parked"})
IC_GATES: Final = ("draft", "checks", "review", "verify", "demo", "mergeable")
"""Gates whose failure sends a PR back to its IC."""
WORK_TASK_STATES: Final = frozenset({"ready", "running"})
"""Task states an IC works in (``planned`` and ``blocked`` wait on something else)."""

NEED_IDLE: Final = "idle"
NEED_WORK: Final = "work"
NEED_WAIT: Final = "wait"


@dataclass(frozen=True)
class DevEnvNeed:
    """
    What a project needs of its dev env right now.

    :param need: ``idle`` (only waits for a human merge: the dev env can go),
        ``work`` (an IC has work: the dev env should be up) or ``wait``
        (neither, e.g. a new head's checks are still running: leave it as it is).
    :param reason: Plain words, e.g. ``"luka asked for changes on PR #9826"``.
    :param since: ``idle`` only: when the last open PR became ready (epoch s).
    :param prs: ``idle`` only: the open PRs, all ready for a human merge.
    """

    need: str
    reason: str
    since: int | None = None
    prs: tuple[int, ...] = ()


def idle_env_s() -> float | None:
    """
    How long a project must only wait for a merge before its dev env goes.

    :returns: ``OMNIGENT_FACTORY_IDLE_ENV_S`` (default 900), or ``None`` when
        set to ``0`` (off).
    """
    raw = os.environ.get(IDLE_ENV_ENV, "").strip()
    if not raw:
        return DEFAULT_IDLE_ENV_S
    try:
        value = float(raw)
    except ValueError:
        _logger.warning("Ignoring invalid %s=%r", IDLE_ENV_ENV, raw)
        return DEFAULT_IDLE_ENV_S
    return value if value > 0 else None


def env_state(project: FactoryProject) -> str | None:
    """:returns: The dev env state env_keeper last reported, or ``None`` before any report."""
    env = project.env if isinstance(project.env, Mapping) else {}
    reported = env.get("reported")
    state = reported.get("state") if isinstance(reported, Mapping) else None
    return state if isinstance(state, str) and state else None


def env_subject(slug: str) -> str:
    """:returns: The event subject of a project's dev env, e.g. ``"env:api-version"``."""
    return f"env:{slug}"


def _key(row_id: str | None) -> str | None:
    if not row_id:
        return None
    try:
        return norm_id(row_id)
    except Exception:  # noqa: BLE001 - an unparsable id matches nothing
        return None


def _failed_gates(pr: FactoryPr) -> list[str]:
    gates = pr.gates or {}
    return [name for name in IC_GATES if gates.get(name) == GATE_FAIL]


def assess(
    project: FactoryProject,
    prs: Iterable[FactoryPr],
    tasks: Iterable[FactoryTask],
    *,
    busy: Sequence[str] = (),
) -> DevEnvNeed:
    """
    Decide what *project* needs of its dev env. Pure.

    Work, in this order: a human change request on an open PR's head, an open
    PR whose draft, checks, review, verify, demo or mergeable gate failed, a
    task that is ready or running (but for one whose PR is ready: a reworked
    task stays ``running`` on its PR). Idle: at least one open PR, every open
    PR ready for a human merge, no open task but those PRs' own, and no
    sub-agent but env_keeper running. Anything else waits.

    :param prs: PRs of the factory (other projects' and closed ones are ignored).
    :param tasks: Tasks of the factory (other projects' and closed ones are ignored).
    :param busy: Titles of the PL's running sub-agents, env_keeper left out.
    """
    slug = project.slug
    open_prs = sorted(
        (p for p in prs if p.state == "open" and p.project_slug == slug), key=lambda p: p.number
    )
    mine = sorted(
        (t for t in tasks if t.project_slug == slug and t.state not in TASK_CLOSED_STATES),
        key=lambda t: t.number,
    )
    for pr in open_prs:
        if changes_requested_on_head(pr.human_review, pr.head_sha):
            by = (pr.human_review or {}).get("by") or "a human"
            return DevEnvNeed(NEED_WORK, f"{by} asked for changes on PR #{pr.number}")
    for pr in open_prs:
        failed = _failed_gates(pr)
        if "draft" in failed:
            return DevEnvNeed(NEED_WORK, f"PR #{pr.number} is a draft again")
        if failed:
            return DevEnvNeed(NEED_WORK, f"PR #{pr.number} failed {', '.join(failed)}")
    ready_ids = {_key(p.id) for p in open_prs if ready_for_human(p)}

    def waits_on_merge(task: FactoryTask) -> bool:
        return task.state in ("review", "running") and _key(task.pr_id) in ready_ids

    for task in mine:
        if task.state in WORK_TASK_STATES and not waits_on_merge(task):
            return DevEnvNeed(NEED_WORK, f"task {task.ref} {task.key} is {task.state}")
    if not open_prs:
        return DevEnvNeed(NEED_WAIT, "no open PR")
    pending = [p for p in open_prs if not ready_for_human(p)]
    if pending:
        return DevEnvNeed(NEED_WAIT, f"PR #{pending[0].number} is not ready yet")
    left = [t for t in mine if not waits_on_merge(t)]
    if left:
        return DevEnvNeed(NEED_WAIT, f"task {left[0].ref} {left[0].key} is {left[0].state}")
    if busy:
        return DevEnvNeed(NEED_WAIT, f"{busy[0]} is running")
    since = max(p.ready_at or p.first_ready_at or 0 for p in open_prs)
    verb = "waits" if len(open_prs) == 1 else "wait"
    return DevEnvNeed(
        NEED_IDLE,
        f"{_pr_list([p.number for p in open_prs])} only {verb} for a human merge",
        since=since or None,
        prs=tuple(p.number for p in open_prs),
    )


def _pr_list(numbers: Sequence[int]) -> str:
    """``PR #1``, ``PRs #1 and #2``, ``PRs #1, #2 and #3``."""
    refs = [f"#{n}" for n in numbers]
    if len(refs) == 1:
        return f"PR {refs[0]}"
    return f"PRs {', '.join(refs[:-1])} and {refs[-1]}"


def down_notice_text(project: FactoryProject, need: DevEnvNeed) -> str:
    """:returns: The ``[Factory notice · dev env idle · take it down]`` notice for the PL."""
    prs = _pr_list(need.prs)
    verb = "is" if len(need.prs) == 1 else "are"
    return (
        f"[Factory notice · dev env idle · take it down] Project {project.slug} only waits for "
        f"a human merge: {prs} {verb} ready and no task is open. Send env_keeper DOWN "
        "reason=idle. It takes down the dev env only: keep every preview up for review. "
        "The server tells you when work comes back."
    )


def up_notice_text(project: FactoryProject, need: DevEnvNeed) -> str:
    """:returns: The ``[Factory notice · dev env needed · bring it up]`` notice for the PL."""
    return (
        f"[Factory notice · dev env needed · bring it up] Project {project.slug} has work "
        f"again: {need.reason}. Send env_keeper UP base={project_base_branch(project)} "
        "so the IC has its dev env."
    )


def _last_ask(store: FactoryStore, factory_id: str, slug: str) -> FactoryEvent | None:
    found = store.find_events(
        factory_id,
        [DOWN_KIND, UP_KIND],
        0,
        env_subject(slug),
        limit=1,
        newest_first=True,
    )
    return found[0] if found else None


async def on_tick(
    app_state: Any,
    members: Sequence[Conversation],
    busy: Mapping[str, Sequence[str]],
    now: float,
    *,
    after_s: float | None,
) -> None:
    """
    Ask PLs to take idle dev envs down and to bring them back for new work. Never raises.

    :param members: Open top-level members of every factory.
    :param busy: PL session id → titles of its running sub-agents (env_keeper left out).
    :param now: Epoch seconds.
    :param after_s: Seconds every PR must have been ready; ``None`` turns this off.
    """
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if after_s is None or not isinstance(deps, FactoryDeps):
        return
    by_factory: dict[str, list[Conversation]] = {}
    for member in members:
        factory_id = member.labels.get(LABEL_ID)
        if factory_id:
            by_factory.setdefault(factory_id, []).append(member)
    for factory_id, factory_members in by_factory.items():
        try:
            await _check_factory(app_state, deps, factory_id, factory_members, busy, now, after_s)
        except Exception:  # noqa: BLE001 - one factory's store error must not stop the rest
            _logger.warning("factory: idle dev env check of %s failed", factory_id, exc_info=True)


async def _check_factory(
    app_state: Any,
    deps: FactoryDeps,
    factory_id: str,
    members: Sequence[Conversation],
    busy: Mapping[str, Sequence[str]],
    now: float,
    after_s: float,
) -> None:
    from omnigent.server.factory.snapshot import load_open_tasks, pick_pl

    store = deps.factory_store
    projects = await asyncio.to_thread(store.list_projects, factory_id)
    candidates = [
        p
        for p in projects
        if p.status == "active"
        and env_state(p) is not None
        and project_kind(p) != PROJECT_KIND_INTEGRATION
    ]
    if not candidates:
        return
    prs = await asyncio.to_thread(store.list_prs, factory_id, ["open"])
    tasks = await asyncio.to_thread(load_open_tasks, deps, factory_id)
    for project in candidates:
        pl = pick_pl(project, project.slug, members)
        if pl is None or pl.labels.get(LABEL_STATE) != STATE_ACTIVE:
            # A paused PL took its dev env down itself; RESUME brings it back.
            continue
        state = env_state(project) or ""
        need = assess(project, prs, tasks, busy=busy.get(pl.id, ()))
        if need.need == NEED_IDLE and state in ENV_UP_STATES:
            await _ask_down(app_state, store, factory_id, project, pl, need, state, now, after_s)
        elif need.need == NEED_WORK and state in ENV_DOWN_STATES:
            await _ask_up(app_state, store, factory_id, project, pl, need, state, now)


async def _ask_down(
    app_state: Any,
    store: FactoryStore,
    factory_id: str,
    project: FactoryProject,
    pl: Conversation,
    need: DevEnvNeed,
    state: str,
    now: float,
    after_s: float,
) -> None:
    """Once per idle stretch, after ``after_s`` (counted from a later ask to bring it back)."""
    idle_from = float(need.since or 0)
    if now - idle_from < after_s:
        return
    last = await asyncio.to_thread(_last_ask, store, factory_id, project.slug)
    if last is not None:
        at = last.ts / 1000
        if last.kind == DOWN_KIND and at >= idle_from:
            # Asked already; a PL that kept or brought the env back up had its reason.
            return
        if last.kind == UP_KIND and now - at < after_s:
            return
    get_mailer(app_state).notify(
        pl.id, down_notice_text(project, need), factory_id=factory_id, project=project.slug
    )
    await events.emit(
        app_state,
        factory_id,
        DOWN_KIND,
        severity="notice",
        project=project.slug,
        to_id=pl.id,
        subject=env_subject(project.slug),
        summary=(
            f"{project.slug} only waits for a human merge ({_pr_list(need.prs)}) · "
            "asked the PL to take the dev env down, previews stay up"
        ),
        data={"prs": list(need.prs), "ready_since": need.since, "env_state": state},
        ts=int(now * 1000),
    )


async def _ask_up(
    app_state: Any,
    store: FactoryStore,
    factory_id: str,
    project: FactoryProject,
    pl: Conversation,
    need: DevEnvNeed,
    state: str,
    now: float,
) -> None:
    """Only after this module took the dev env down, and once per such stretch."""
    last = await asyncio.to_thread(_last_ask, store, factory_id, project.slug)
    if last is None or last.kind != DOWN_KIND:
        return
    get_mailer(app_state).notify(
        pl.id, up_notice_text(project, need), factory_id=factory_id, project=project.slug
    )
    await events.emit(
        app_state,
        factory_id,
        UP_KIND,
        severity="notice",
        project=project.slug,
        to_id=pl.id,
        subject=env_subject(project.slug),
        summary=(
            f"{project.slug} has work again ({need.reason}) · asked the PL to bring the dev env up"
        ),
        data={"reason": need.reason, "env_state": state},
        ts=int(now * 1000),
    )


__all__ = [
    "DEFAULT_IDLE_ENV_S",
    "DOWN_KIND",
    "ENV_DOWN_STATES",
    "ENV_UP_STATES",
    "IDLE_ENV_ENV",
    "NEED_IDLE",
    "NEED_WAIT",
    "NEED_WORK",
    "UP_KIND",
    "DevEnvNeed",
    "assess",
    "down_notice_text",
    "env_state",
    "env_subject",
    "idle_env_s",
    "on_tick",
    "up_notice_text",
]
