"""A project's team at run time: catalog, rates, preview effects, the PL's view, dispatch.

The team spec itself is definition data (:mod:`.team_spec`); this module is
what reads it while the factory runs:

- :func:`catalog` (``GET …/catalog``): the roles of the PL bundle, the vendor ·
  models each runs, the gate each is needed by, $/h estimates, templates, and
  the roles no bundle covers yet.
- :func:`preview_effects`: what a team change does before it applies (rate
  while working, cost to finish against the budget, who restarts and when,
  review-route changes, the TEAM UPDATE it sends).
- :func:`charter_team` and :func:`deliver_update`: the PL sees its team in its
  charter, and one ``[Factory control · TEAM UPDATE · vN]`` per change.
- :func:`dispatch_choice`: the member, agent and model ``sys_factory_task(op="start")``
  returns per task.
- :func:`guard_arguments`: the server arguments of
  :func:`omnigent.policies.builtins.factory_team.factory_team_guard`, armed only
  with ``OMNIGENT_FACTORY_TEAM_SPEC=enforce``; :func:`observe_dispatch` records
  ``team.dispatch_outside_spec`` in either mode.

A project without its own team keeps today's behaviour: no Team block in its
charter, no dispatch check, its charter's builders and budget.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    LABEL_ID,
    LABEL_PROJECT,
    LABEL_ROLE,
    MESSAGE_MAX_CHARS,
    ROLE_PROJECT_LEAD,
)
from omnigent.server.factory import definition, team_spec
from omnigent.server.factory.charter import TeamBlock, normalize_charter
from omnigent.server.factory.envelope import display_title, quote_spoofed_lines
from omnigent.server.factory.graph import OCCUPYING
from omnigent.server.factory.team_spec import ProjectFacts

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryProject, FactoryTask
    from omnigent.server.factory.actor import Actor
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

TEAM_SPEC_ENV: Final = "OMNIGENT_FACTORY_TEAM_SPEC"
"""``warn`` (default: record dispatches outside the team) or ``enforce`` (deny them)."""
MODES: Final = ("warn", "enforce")
TEAM_GUARD_HANDLER: Final = "omnigent.policies.builtins.factory_team.factory_team_guard"
UPDATED_KIND: Final = "team.updated"
OUTSIDE_KIND: Final = "team.dispatch_outside_spec"
INTERRUPTED_KIND: Final = "team.interrupted"
WHEN: Final = ("after_current", "now")
RATE_WINDOW_DAYS: Final = 30
MIN_RATE_HOURS: Final = 0.25
MODEL_USD_PER_H: Final[dict[str, float]] = {"opus": 12.0, "sonnet": 4.0, "haiku": 1.0}
"""Rough $/h of a working agent by model family, until the cost ledger has its own."""
VENDOR_USD_PER_H: Final[dict[str, float]] = {"claude": 4.0, "codex": 3.0}
CLAUDE_MODELS: Final = ("opus", "sonnet", "haiku")
ROLE_DOES: Final[dict[str, str]] = {
    "project_lead": "Plans the project, dispatches its team, reviews and hands PRs over.",
    "ic": "Implements one task card per dispatch in its own worktree and PR.",
    "reviewer": "Reviews a PR against its acceptance contract; never edits.",
    "env_keeper": "Keeps the dev env and one preview per PR head.",
    "demo": "Records a demo of a PR head in its preview.",
}
MISSING_ROLES: Final = (
    ("designer", "Designer"),
    ("qa", "QA tester"),
    ("docs", "Docs writer"),
)
"""Kinds of role people ask for that no built-in bundle covers yet."""
MISSING_REASON: Final = (
    "Not in the catalog. A new kind of role needs a built-in bundle, which is a code change."
)


def mode() -> str:
    """:returns: ``OMNIGENT_FACTORY_TEAM_SPEC`` (``warn`` unless it says ``enforce``)."""
    raw = os.environ.get(TEAM_SPEC_ENV, "").strip().lower()
    return raw if raw in MODES else "warn"


def vendor_of(harness: str | None) -> str | None:
    """:returns: ``claude`` or ``codex`` for a harness such as ``codex-native``."""
    text = (harness or "").lower()
    if text.startswith("codex"):
        return "codex"
    if text.startswith("claude"):
        return "claude"
    return None


# ── Facts and the effective team ───────────────────────────────


def project_facts(project: FactoryProject | None) -> ProjectFacts:
    """:returns: Whether *project*'s charter needs an env and has previews on."""
    from omnigent.server.factory.prs import previews_on

    charter = normalize_charter(project.charter if project is not None else None)
    return ProjectFacts(env_needed=bool(charter["env_needed"]), previews_on=previews_on(project))


def facts_lookup(deps: FactoryDeps, factory_id: str) -> team_spec.FactsLookup:
    """:returns: ``slug -> facts`` over the factory's projects (read once, on first use)."""
    rows: dict[str, FactoryProject] = {}
    loaded: list[bool] = []

    def lookup(slug: str) -> ProjectFacts | None:
        if not loaded:
            rows.update({p.slug: p for p in deps.factory_store.list_projects(factory_id)})
            loaded.append(True)
        found = rows.get(slug)
        return project_facts(found) if found is not None else None

    return lookup


def _doc(deps: FactoryDeps, factory_id: str) -> tuple[dict[str, Any], int | None]:
    resolved = definition.definition_for(deps, factory_id)
    if resolved is None:
        return {}, None
    return resolved.doc, resolved.version


@dataclass(frozen=True)
class TeamView:
    """
    A project's team with its charter's values filled in.

    :param own: The project has its own team (else the default template's).
    :param builders_from: ``team`` or ``charter``.
    :param budget_from: ``team``, ``charter`` or ``definition``.
    """

    slug: str
    own: bool
    template: str | None
    builders_at_once: int
    builders_from: str
    budget_usd: float
    budget_from: str
    members: tuple[dict[str, Any], ...]
    briefs: dict[str, str]
    def_version: int | None = None

    def agents(self) -> dict[str, list[str | None]]:
        """:returns: Agent -> the models its members run (``None``: the bundle's default)."""
        out: dict[str, list[str | None]] = {}
        for member in self.members:
            if member["role"] == ROLE_PROJECT_LEAD:
                continue
            models = out.setdefault(str(member["agent"]), [])
            if member.get("model") not in models:
                models.append(member.get("model"))
        return out

    def builders(self) -> list[dict[str, Any]]:
        """:returns: The builder members."""
        return [m for m in self.members if m["role"] == team_spec.BUILDER]


def _budget(project: FactoryProject | None, factory_id: str) -> tuple[float, str]:
    from omnigent.server.factory.loops import budget

    charter = (project.charter or {}) if project is not None else {}
    found = charter.get("budgets") if isinstance(charter.get("budgets"), Mapping) else {}
    value = found.get("project_usd")
    if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        return float(value), "charter"
    return float(budget(factory_id, "budgets", "project_usd")), "definition"


def view_of(
    doc: Mapping[str, Any],
    project: FactoryProject,
    *,
    def_version: int | None = None,
) -> TeamView:
    """:returns: *project*'s team in *doc* with the charter's values where it sets none."""
    team = team_spec.team_of(doc, project.slug)
    charter = normalize_charter(project.charter)
    builders = team.get("builders_at_once")
    budget, budget_from = (
        (float(team["budget_usd"]), "team")
        if team.get("budget_usd") is not None
        else _budget(project, project.factory_id)
    )
    return TeamView(
        slug=project.slug,
        own=team_spec.has_own(doc, project.slug),
        template=team.get("template"),
        builders_at_once=int(builders) if builders is not None else int(charter["budget_ics"]),
        builders_from="team" if builders is not None else "charter",
        budget_usd=budget,
        budget_from=budget_from,
        members=tuple(team["members"]),
        briefs=dict(team.get("briefs") or {}),
        def_version=def_version,
    )


def own_view(deps: FactoryDeps, factory_id: str, slug: str) -> TeamView | None:
    """:returns: Project *slug*'s own team (``None`` without one, or no project row)."""
    doc, version = _doc(deps, factory_id)
    if not team_spec.has_own(doc, slug):
        return None
    project = deps.factory_store.get_project(factory_id, slug)
    return view_of(doc, project, def_version=version) if project is not None else None


def builders_at_once(deps: FactoryDeps, factory_id: str, slug: str) -> int | None:
    """:returns: The builders at once a project's own team sets (``None``: the charter's)."""
    doc, _ = _doc(deps, factory_id)
    if not team_spec.has_own(doc, slug):
        return None
    found = team_spec.team_of(doc, slug).get("builders_at_once")
    return int(found) if found is not None else None


def budget_usd(deps: FactoryDeps, factory_id: str, slug: str) -> float | None:
    """:returns: The budget a project's own team sets (``None``: the charter's or definition's)."""
    doc, _ = _doc(deps, factory_id)
    if not team_spec.has_own(doc, slug):
        return None
    found = team_spec.team_of(doc, slug).get("budget_usd")
    return float(found) if found is not None else None


# ── The PL bundle ──────────────────────────────────────────────


def _bundles_dir() -> Path:
    from omnigent._platform import resolve_repo_symlink
    from omnigent.resources import examples

    return resolve_repo_symlink(Path(examples.__file__).parent / "factory")


@functools.cache
def bundle_agents(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """
    The PL bundle's sub-agents as the server ships them (``tools.agents``).

    :returns: Agent -> ``{role, vendor, model, harness}``, e.g. ``{"ic": {"role":
        "ic", "vendor": "claude", "model": "sonnet", "harness": "claude-native"}}``;
        the PL itself under ``factory-pl``. Empty when the bundle is not shipped.
    """
    import yaml

    base = (root or _bundles_dir()) / "factory-pl"
    out: dict[str, dict[str, Any]] = {}
    try:
        pl = yaml.safe_load((base / "config.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return out

    def entry(raw: Mapping[str, Any]) -> dict[str, Any]:
        found = raw.get("executor")
        executor: Mapping[str, Any] = found if isinstance(found, Mapping) else {}
        found = executor.get("config")
        config: Mapping[str, Any] = found if isinstance(found, Mapping) else {}
        harness = config.get("harness")
        model = executor.get("model")
        return {
            "role": config.get("factory_role"),
            "vendor": vendor_of(harness if isinstance(harness, str) else None),
            "model": model if isinstance(model, str) else None,
            "harness": harness if isinstance(harness, str) else None,
        }

    out[team_spec.PL_AGENT] = entry(pl)
    tools = pl.get("tools") if isinstance(pl.get("tools"), Mapping) else {}
    for name in tools.get("agents") or []:
        try:
            raw = yaml.safe_load((base / "agents" / str(name) / "config.yaml").read_text("utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(raw, Mapping):
            out[str(name)] = entry(raw)
    return out


def default_model(agent: str | None) -> str | None:
    """:returns: The model alias *agent*'s bundle runs on by default (``None``: its harness's)."""
    found = bundle_agents().get(agent or "")
    return found.get("model") if found else None


# ── Rates from the cost ledger ─────────────────────────────────


@dataclass(frozen=True)
class Rate:
    """
    $/h of one agent and model while it works.

    :param n: Finished task runs behind it (``0`` for a default estimate).
    :param source: ``ledger`` (task spans and the cost ledger) or ``default``.
    """

    usd_per_h: float
    n: int
    source: str


def _family(model: str | None) -> str | None:
    text = (model or "").lower()
    return next((f for f in MODEL_USD_PER_H if f in text), None)


@dataclass
class RateBook:
    """Hours and spend per agent (task spans) and spend per agent and model (cost ledger)."""

    hours: dict[str, float]
    cost: dict[str, float]
    runs: dict[str, int]
    ledger: dict[tuple[str, str], tuple[float, int]]

    def rate(self, agent: str, vendor: str | None, model: str | None) -> Rate:
        """:returns: The $/h estimate of *agent* on *model* (``None``: its bundle's default)."""
        model = model or default_model(agent)
        hours = self.hours.get(agent, 0.0)
        if hours >= MIN_RATE_HOURS and self.runs.get(agent):
            base = self.cost.get(agent, 0.0) / hours
            return Rate(
                round(base * self._model_factor(agent, model), 2), self.runs[agent], "ledger"
            )
        family = _family(model)
        default = MODEL_USD_PER_H[family] if family else VENDOR_USD_PER_H.get(vendor or "", 4.0)
        return Rate(default, 0, "default")

    def _model_factor(self, agent: str, model: str | None) -> float:
        cells = [(m, v) for (a, m), v in self.ledger.items() if a == agent]
        total_cost = sum(c for _, (c, _t) in cells)
        total_turns = sum(t for _, (_c, t) in cells)
        mine = [(c, t) for m, (c, t) in cells if model and model in m]
        if not model or not mine or not total_turns or not total_cost:
            return 1.0
        cost, turns = sum(c for c, _ in mine), sum(t for _, t in mine)
        if not turns:
            return 1.0
        factor = (cost / turns) / (total_cost / total_turns)
        return min(5.0, max(0.2, factor))


def rate_book(deps: FactoryDeps, factory_id: str, *, now: float | None = None) -> RateBook:
    """Read the last :data:`RATE_WINDOW_DAYS` of task spans and the cost ledger (synchronous)."""
    now = time.time() if now is None else now
    since = now - RATE_WINDOW_DAYS * 86400
    store = deps.factory_store
    hours: dict[str, float] = {}
    cost: dict[str, float] = {}
    runs: dict[str, int] = {}
    for span in store.list_spans(
        factory_id, kinds=["task"], since_ms=int(since * 1000), limit=5000
    ):
        if span.ended_at is None or span.cost_usd is None or not span.agent_name:
            continue
        took = max(0.0, (span.ended_at - span.started_at) / 3_600_000)
        hours[span.agent_name] = hours.get(span.agent_name, 0.0) + took
        cost[span.agent_name] = cost.get(span.agent_name, 0.0) + float(span.cost_usd)
        runs[span.agent_name] = runs.get(span.agent_name, 0) + 1
    ledger: dict[tuple[str, str], tuple[float, int]] = {}
    day = datetime.fromtimestamp(since, tz=UTC).date().isoformat()
    for cell in store.list_cost_daily(factory_id, since_day=day):
        key = (cell.agent_name, cell.model or "")
        old = ledger.get(key, (0.0, 0))
        ledger[key] = (old[0] + float(cell.cost_usd or 0.0), old[1] + int(cell.turns or 0))
    return RateBook(hours, cost, runs, ledger)


def team_rate(book: RateBook, members: Iterable[Mapping[str, Any]], builders: int) -> float:
    """
    $/h of a team while it works: every member but the builders, plus
    *builders* builders at their average rate.
    """
    others, builder_rates = 0.0, []
    for member in members:
        rate = book.rate(str(member["agent"]), member.get("vendor"), member.get("model")).usd_per_h
        if member["role"] == team_spec.BUILDER:
            builder_rates.append(rate)
        else:
            others += rate
    average = sum(builder_rates) / len(builder_rates) if builder_rates else 0.0
    return round(others + average * max(0, builders), 2)


# ── The catalog ────────────────────────────────────────────────


def catalog(deps: FactoryDeps, factory_id: str, slug: str | None = None) -> dict[str, Any]:
    """
    ``GET …/catalog``: what a team can be made of.

    :param slug: A project: its template locks and which members it cannot remove.
    :returns: ``{roles[], missing[], templates[], gates[], vendors, mode, project?}``.
    """
    doc, version = _doc(deps, factory_id)
    book = rate_book(deps, factory_id)
    shipped = bundle_agents()
    seen = {model for (_agent, model) in book.ledger if model}
    roles = []
    for role in team_spec.ROLE_ORDER:
        vendors: list[dict[str, Any]] = []
        for vendor in ("claude", "codex"):
            agent = team_spec.AGENTS.get((role, vendor))
            if vendor not in team_spec.VENDORS[role] or agent is None:
                vendors.append(
                    {
                        "vendor": vendor,
                        "available": False,
                        "reason": team_spec.vendor_refusal(role, vendor),
                    }
                )
                continue
            in_bundle = agent in shipped
            default = default_model(agent)
            models = (
                list(CLAUDE_MODELS)
                if vendor == "claude"
                else sorted(m for m in seen if vendor_of(m) is None and not _family(m))
            )
            if default and default not in models:
                models.insert(0, default)
            rate = book.rate(agent, vendor, None)
            vendors.append(
                {
                    "vendor": vendor,
                    "available": in_bundle,
                    "reason": None if in_bundle else f"{agent} is not in the PL bundle",
                    "agent": agent,
                    "default_model": default,
                    "models": models,
                    "usd_per_h": rate.usd_per_h,
                    "rate_source": rate.source,
                    "rate_n": rate.n,
                    "model_rates": {m: book.rate(agent, vendor, m).usd_per_h for m in models},
                    "bench": None,
                }
            )
        need = next((n for n in team_spec.GATE_NEEDS if n.role == role), None)
        roles.append(
            {
                "role": role,
                "label": team_spec.ROLE_LABELS[role],
                "does": ROLE_DOES[role],
                "needed_by": (
                    {"gate": need.gate, "text": need.text, "when": need.when} if need else None
                ),
                "addable": role != ROLE_PROJECT_LEAD,
                "vendors": vendors,
            }
        )
    project = deps.factory_store.get_project(factory_id, slug) if slug else None
    facts = project_facts(project) if project is not None else None
    templates = []
    for shape in team_spec.templates(doc):
        templates.append(
            {
                **shape,
                "members": sum(int(r.get("count") or 1) for r in shape.get("roles") or []),
                "locked": team_spec.template_lock(shape, facts) if facts is not None else None,
            }
        )
    out: dict[str, Any] = {
        "def_version": version,
        "roles": roles,
        "missing": [
            {"role": role, "label": label, "reason": MISSING_REASON}
            for role, label in MISSING_ROLES
        ],
        "templates": templates,
        "gates": [
            {"gate": n.gate, "role": n.role, "text": n.text, "when": n.when}
            for n in team_spec.GATE_NEEDS
        ],
        "vendors": dict(team_spec.VENDOR_RUNTIMES),
        "mode": mode(),
        "rates_window_days": RATE_WINDOW_DAYS,
    }
    if project is not None and facts is not None:
        view = view_of(doc, project, def_version=version)
        out["project"] = {
            "slug": project.slug,
            "own": view.own,
            "env_needed": facts.env_needed,
            "previews_on": facts.previews_on,
            "remove_blocked": team_spec.remove_reasons({"members": list(view.members)}, facts),
        }
    return out


# ── Preview effects ────────────────────────────────────────────


def _by_project(entries: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = {}
    for entry in entries:
        path = str(entry["path"])
        if team_spec.is_project_path(path):
            out.setdefault(team_spec.parse_path(path).slug, []).append(entry)
    return out


def _changed_members(
    entries: Sequence[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any] | None]]:
    """:returns: ``(before, after)`` of members removed or moved to another vendor or model."""
    out = []
    for entry in entries:
        before, after = entry.get("before"), entry.get("after")
        path = str(entry["path"])
        where = team_spec.parse_path(path) if team_spec.is_project_path(path) else None
        if where is None or where.part != "members" or not isinstance(before, Mapping):
            continue
        if after is None or any(
            before.get(k) != after.get(k) for k in ("vendor", "model", "agent")
        ):
            out.append((before, after if isinstance(after, Mapping) else None))
    return out


def _name(member: Mapping[str, Any]) -> str:
    return str(member.get("handle") or member.get("id"))


def _running(deps: FactoryDeps, pl: Conversation | None, agents: set[str]) -> list[Conversation]:
    from omnigent.server.routes._sessions.helpers import _session_status_from_cache

    if pl is None or not agents:
        return []
    page = deps.conversation_store.list_conversations(
        limit=200, kind="sub_agent", parent_conversation_id=pl.id, order="desc"
    )
    return [
        c
        for c in page.data
        if c.sub_agent_name in agents
        and _session_status_from_cache(c.id, c.live_status) == "running"
    ]


def _review_route(before: Mapping[str, Any], after: Mapping[str, Any] | None) -> str | None:
    if after is None or before.get("role") != team_spec.BUILDER:
        return None
    if before.get("vendor") == after.get("vendor"):
        return None
    reviewer = "a Codex review" if after.get("vendor") == "claude" else "a Claude review"
    return f"{_name(after)}'s PRs then get {reviewer} (cross-vendor rule)."


def _finish(
    deps: FactoryDeps, factory_id: str, slug: str, before_rate: float, after_rate: float
) -> tuple[float, float, float]:
    """:returns: ``(spent, to finish now, to finish after)`` in dollars."""
    from omnigent.server.factory.loops import budget

    store = deps.factory_store
    spent = sum(c.cost_usd for c in store.list_cost_daily(factory_id, project_slug=slug))
    tasks = store.list_tasks(factory_id, project_slug=slug)
    done = [t.cost_usd for t in tasks if t.state == "done" and t.cost_usd]
    left = sum(1 for t in tasks if t.state not in ("done", "cancelled", "failed"))
    per_task = (
        sum(done) / len(done) if done else float(budget(factory_id, "implement", "usd")) * 0.5
    )
    now = left * per_task
    ratio = after_rate / before_rate if before_rate > 0 else 1.0
    return round(spent, 2), round(spent + now, 2), round(spent + now * ratio, 2)


def preview_effects(
    deps: FactoryDeps,
    factory_id: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    *,
    version_next: int,
    when: str = "after_current",
) -> list[dict[str, Any]]:
    """
    What the team changes in *entries* do, per project, in plain words (synchronous).

    :returns: Effects of kinds ``rate`` {before_usd_h, after_usd_h}, ``finish``
        {spent_usd, before_usd, after_usd, budget_usd}, ``restart`` {members[],
        when, sessions}, ``review_route`` {text} and ``team_update`` {to}.
    """
    from omnigent.server.factory.control import find_project_pl

    grouped = _by_project(entries)
    if not grouped:
        return []
    book = rate_book(deps, factory_id)
    full_before, full_after = definition.full_doc(before), definition.full_doc(after)
    out: list[dict[str, Any]] = []
    for slug, changes in grouped.items():
        project = deps.factory_store.get_project(factory_id, slug)
        if project is None:
            continue
        old = view_of(full_before, project)
        new = view_of(full_after, project)
        before_rate = team_rate(book, old.members, old.builders_at_once)
        after_rate = team_rate(book, new.members, new.builders_at_once)
        out.append(
            {
                "kind": "rate",
                "project": slug,
                "before_usd_h": before_rate,
                "after_usd_h": after_rate,
                "text": f"While working: ${before_rate:.2f}/h → ${after_rate:.2f}/h "
                "(estimate from the cost ledger).",
            }
        )
        spent, finish_before, finish_after = _finish(
            deps, factory_id, slug, before_rate, after_rate
        )
        out.append(
            {
                "kind": "finish",
                "project": slug,
                "spent_usd": spent,
                "before_usd": finish_before,
                "after_usd": finish_after,
                "budget_usd": new.budget_usd,
                "text": f"To finish: ${finish_before:.0f} → ${finish_after:.0f} of "
                f"${new.budget_usd:.0f}.",
            }
        )
        moved = _changed_members(changes)
        pl = find_project_pl(deps, factory_id, slug)
        running = _running(deps, pl, {str(b["agent"]) for b, _a in moved})
        if moved:
            parts = []
            for was, now in moved:
                target = (
                    f"restarts on {now['vendor']} · {now.get('model') or 'default'}"
                    if now is not None
                    else "leaves the team"
                )
                lead = (
                    "finishes its current task, then "
                    if when == "after_current"
                    else "is interrupted now and "
                )
                parts.append(f"{_name(was)} {lead}{target}.")
            out.append(
                {
                    "kind": "restart",
                    "project": slug,
                    "when": when,
                    "members": [str(b["id"]) for b, _a in moved],
                    "sessions": [c.id for c in running],
                    "text": " ".join(parts)
                    + (
                        f" {len(running)} running session(s) are interrupted; about 20 minutes "
                        "of work restarts."
                        if when == "now" and running
                        else ""
                    ),
                }
            )
        for was, now in moved:
            route = _review_route(was, now)
            if route:
                out.append({"kind": "review_route", "project": slug, "text": route})
        name = display_title(pl.title, pl.id) if pl is not None else None
        out.append(
            {
                "kind": "team_update",
                "project": slug,
                "to": pl.id if pl is not None else None,
                "text": (
                    f"{name} gets one TEAM UPDATE message with v{version_next}."
                    if name
                    else f"{slug} has no running project lead: its next one reads the team "
                    "from its charter."
                ),
            }
        )
    return out


# ── The PL's view: charter, TEAM UPDATE ────────────────────────


def charter_lines(view: TeamView) -> list[str]:
    """:returns: The Team block of a PL's charter (briefs spoof-quoted)."""
    version = f"definition v{view.def_version} · " if view.def_version else ""
    lines = [
        f"Team ({version}template {view.template or 'none'} · {len(view.members)} members · "
        f"builders at once: {view.builders_at_once} · budget ${view.budget_usd:g}):"
    ]
    for member in view.members:
        if member["role"] == ROLE_PROJECT_LEAD:
            lines.append(f"- {member['id']} project lead · you")
            continue
        handle = f" {member['handle']}" if member.get("handle") else ""
        role = team_spec.ROLE_LABELS.get(str(member["role"]), str(member["role"]))
        model = member.get("model") or "default model"
        lines.append(
            f"- {member['id']}{handle} · {role} · agent {member['agent']} · "
            f"{member['vendor']} · {model}"
        )
    if view.briefs:
        lines.append("Briefs from people (add each to that member's dispatches, verbatim):")
        lines += [f"- {mid}: {quote_spoofed_lines(text)}" for mid, text in view.briefs.items()]
    lines.append(
        "Dispatch only to these agents; pass a member's model as args.model when it names one "
        '("default model": leave it out).'
    )
    return lines


def charter_team(deps: FactoryDeps, factory_id: str, project: FactoryProject) -> TeamBlock | None:
    """:returns: The Team block for *project*'s charter, ``None`` without its own team."""
    try:
        doc, version = _doc(deps, factory_id)
        if not team_spec.has_own(doc, project.slug):
            return None
        view = view_of(doc, project, def_version=version)
    except Exception:  # noqa: BLE001 - a charter goes out without a Team block rather than not at all
        _logger.warning("factory team: no Team block for %s", project.slug, exc_info=True)
        return None
    return TeamBlock(builders_at_once=view.builders_at_once, lines=tuple(charter_lines(view)))


def team_update_text(
    *,
    slug: str,
    version: int,
    by: str,
    lines: Sequence[str],
    block: Sequence[str],
    when: str,
    interrupted: int = 0,
) -> str:
    """:returns: ``[Factory control · TEAM UPDATE · v13 · by alice]``, the diff and the team."""
    when_line = (
        f"When: now. The server interrupted {interrupted} session(s) of changed or removed "
        "members: close each (sys_session_close) and re-dispatch its task on the new team."
        if when == "now"
        else "When: after current tasks. Changed or removed members finish their current task; "
        "then close their sessions (sys_session_close) so new dispatches use the new team."
    )
    body = [
        f"Project {slug}: your team changed.",
        "Changes:",
        *(f"- {line}" for line in lines),
        when_line,
        "",
        *block,
        'From now on sys_factory_task(op="start") returns each task\'s agent and model. Note '
        "the version in plan.md. No reply.",
    ]
    text = f"[Factory control · TEAM UPDATE · v{version} · by {by}]\n" + quote_spoofed_lines(
        "\n".join(body)
    )
    return text[:MESSAGE_MAX_CHARS]


def _affected_sessions(
    deps: FactoryDeps,
    factory_id: str,
    slug: str,
    pl: Conversation,
    moved: Sequence[tuple[Mapping[str, Any], Mapping[str, Any] | None]],
    before_members: Sequence[Mapping[str, Any]],
) -> list[Conversation]:
    """The running sessions of members a change removed or moved (synchronous)."""
    ids = {str(b["id"]) for b, _a in moved}
    agents = {str(b["agent"]) for b, _a in moved}
    running = _running(deps, pl, agents)
    if not running:
        return []
    by_session: dict[str, str | None] = {}
    for task in deps.factory_store.list_tasks(factory_id, project_slug=slug):
        for sid in task.session_ids or []:
            by_session[str(sid)] = (task.card or {}).get("member")
    out = []
    for conv in running:
        member = by_session.get(conv.id)
        if member is not None:
            if member in ids:
                out.append(conv)
            continue
        # Not bound to a task (env, reviews, demos): stop it once every member of its agent moved.
        same = [m for m in before_members if m.get("agent") == conv.sub_agent_name]
        if same and all(str(m["id"]) in ids for m in same):
            out.append(conv)
    return out


async def deliver_update(
    app_state: Any,
    root: Conversation,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    version: int,
    ref: str,
    by: str,
    actor: Actor,
    when: str = "after_current",
) -> list[str]:
    """
    Tell each PL whose project's own team changed: one TEAM UPDATE (and, with
    ``when="now"``, interrupt the sessions of changed or removed members).

    :returns: The PLs told.
    """
    from omnigent.server.factory import control, events
    from omnigent.server.factory.context import factory_deps
    from omnigent.server.factory.interventions import interrupt_session

    deps = factory_deps(app_state)
    entries = [d for d in definition.diff(before, after) if team_spec.is_project_path(d["path"])]
    told: list[str] = []
    for slug, changes in _by_project(entries).items():
        project = await asyncio.to_thread(deps.factory_store.get_project, root.id, slug)
        pl = await asyncio.to_thread(control.find_project_pl, deps, root.id, slug)
        lines = [definition.diff_lines([c], 1)[0] for c in changes]
        moved = _changed_members(changes)
        interrupted: list[str] = []
        if when == "now" and pl is not None and moved:
            old = team_spec.team_of(definition.full_doc(before), slug)["members"]
            sessions = await asyncio.to_thread(
                _affected_sessions, deps, root.id, slug, pl, moved, old
            )
            for conv in sessions:
                if await interrupt_session(app_state, conv.id):
                    interrupted.append(conv.id)
        if pl is not None and project is not None:
            view = view_of(definition.full_doc(after), project, def_version=version)
            text = team_update_text(
                slug=slug,
                version=version,
                by=by,
                lines=lines,
                block=charter_lines(view),
                when=when,
                interrupted=len(interrupted),
            )
            control.send_text(app_state, pl.id, text, kind="info")
            told.append(pl.id)
        await events.emit(
            app_state,
            root.id,
            UPDATED_KIND,
            severity="notice",
            project=slug,
            to_id=pl.id if pl is not None else None,
            subject=f"project:{slug}",
            summary=f"Team of {slug} changed in v{version} ({ref} by {by})",
            data={
                **actor.event_data(),
                "version": version,
                "ref": ref,
                "when": when,
                "changes": [dict(c) for c in changes[:30]],
                "interrupted": interrupted,
            },
        )
        if interrupted:
            await events.emit(
                app_state,
                root.id,
                INTERRUPTED_KIND,
                severity="notice",
                project=slug,
                subject=f"project:{slug}",
                summary=f"{len(interrupted)} session(s) of {slug} interrupted for v{version}",
                data={**actor.event_data(), "sessions": interrupted, "version": version},
            )
    return told


# ── Dispatch ───────────────────────────────────────────────────


def role_default_model(doc: Mapping[str, Any], role: str, vendor: str | None) -> str | None:
    """
    :returns: The ``role_defaults.<role>`` model when it is for *vendor* (a
        default without a vendor is for the role's default vendor), else ``None``.
    """
    defaults = (doc.get("role_defaults") or {}).get(role) or {}
    model = defaults.get("model")
    if not model or role not in team_spec.VENDORS:
        return None
    for_vendor = defaults.get("vendor") or team_spec.VENDORS[role][0]
    return str(model) if for_vendor == (vendor or team_spec.VENDORS[role][0]) else None


def dispatch_choice(
    deps: FactoryDeps,
    factory_id: str,
    slug: str,
    harness: str,
    tasks: Sequence[FactoryTask] = (),
    *,
    pinned: bool = False,
) -> dict[str, Any]:
    """
    The member a task's next attempt runs on.

    :param harness: The agent the card (or the caller) asks for, ``ic`` or ``ic_codex``.
    :param tasks: The project's tasks (the least busy builder wins).
    :param pinned: The caller named *harness*: keep it even when the team has none.
    :returns: ``{agent, model, member, handle, team}``; ``team`` is ``own``,
        ``default`` (no team of its own: today's behaviour) or ``outside``.
    """
    doc, _ = _doc(deps, factory_id)
    if not team_spec.has_own(doc, slug):
        vendor = "codex" if harness == "ic_codex" else "claude"
        return {
            "agent": harness,
            "model": role_default_model(doc, team_spec.BUILDER, vendor),
            "member": None,
            "handle": None,
            "team": "default",
        }
    team = team_spec.team_of(doc, slug)
    builders = [m for m in team["members"] if m["role"] == team_spec.BUILDER]
    fits = [m for m in builders if m["agent"] == harness] or ([] if pinned else builders)
    if not fits:
        return {"agent": harness, "model": None, "member": None, "handle": None, "team": "outside"}
    busy: dict[str, int] = {}
    for task in tasks:
        member = (task.card or {}).get("member")
        if task.state in OCCUPYING and isinstance(member, str):
            busy[member] = busy.get(member, 0) + 1
    chosen = min(fits, key=lambda m: (busy.get(m["id"], 0), team_spec.member_number(m["id"])))
    return {
        "agent": chosen["agent"],
        "model": chosen.get("model")
        or role_default_model(doc, team_spec.BUILDER, chosen.get("vendor")),
        "member": chosen["id"],
        "handle": chosen.get("handle"),
        "team": "own",
    }


def _team_arguments(deps: FactoryDeps, conversation: Conversation) -> dict[str, Any] | None:
    labels = conversation.labels
    factory_id, slug = labels.get(LABEL_ID), labels.get(LABEL_PROJECT)
    if labels.get(LABEL_ROLE) != ROLE_PROJECT_LEAD or not factory_id or not slug:
        return None
    view = own_view(deps, factory_id, slug)
    if view is None:
        return None
    occupying = sum(
        1
        for t in deps.factory_store.list_tasks(factory_id, project_slug=slug)
        if t.state in OCCUPYING
    )
    return {
        "team": {
            "project": slug,
            "agents": view.agents(),
            "builder_agents": sorted({str(m["agent"]) for m in view.builders()}),
            "builders_at_once": view.builders_at_once,
            "occupying": occupying,
            "def_version": view.def_version,
        },
        "mode": "enforce",
    }


def guard_arguments(
    deps: FactoryDeps,
) -> Callable[[Conversation, Conversation], Mapping[str, object] | None]:
    """
    Build the resolver of the team check's server arguments.

    :returns: ``resolve(conversation, root)``: the team of a top-level PL whose
        project has its own team, while ``OMNIGENT_FACTORY_TEAM_SPEC=enforce``;
        ``None`` (the check allows) for everyone else, and when the store
        cannot be read (dispatches go on; the binder still records them).
    """
    from omnigent.server.factory.membership import is_top_level

    def _resolve(conversation: Conversation, root: Conversation) -> Mapping[str, object] | None:
        del root
        if mode() != "enforce" or not is_top_level(conversation):
            return None
        try:
            return _team_arguments(deps, conversation)
        except Exception:  # noqa: BLE001 - a broken read must not stop every dispatch
            _logger.warning(
                "factory team: could not resolve the team of %s", conversation.id, exc_info=True
            )
            return None

    return _resolve


def install(app_state: Any) -> Callable[[], None]:
    """Register the team check's resolver for *app_state*'s factory. :returns: The uninstaller."""
    from omnigent.runtime.policies.builder import SERVER_POLICY_ARGUMENTS
    from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return lambda: None
    resolver = guard_arguments(deps)
    SERVER_POLICY_ARGUMENTS[TEAM_GUARD_HANDLER] = resolver

    def _uninstall() -> None:
        if SERVER_POLICY_ARGUMENTS.get(TEAM_GUARD_HANDLER) is resolver:
            del SERVER_POLICY_ARGUMENTS[TEAM_GUARD_HANDLER]

    return _uninstall


def outside_spec(view: TeamView, child: Conversation) -> list[str]:
    """:returns: What sub-agent *child* breaks of *view* (its agent or model), in words."""
    agents = view.agents()
    agent = child.sub_agent_name
    if not agent:
        return []
    if agent not in agents:
        return [f"{agent} is not on the team"]
    model = child.model_override
    allowed = agents[agent]
    named = sorted({str(m) for m in allowed if m})
    if model and named and model not in named and None not in allowed:
        return [f"{agent} runs {model}; the team says {', '.join(named)}"]
    return []


async def observe_dispatch(app_state: Any, pl: Conversation, child: Conversation) -> None:
    """
    Record a sub-agent created outside its project's own team
    (``team.dispatch_outside_spec``, once per session). Never raises.
    """
    from omnigent.server.factory import events
    from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    factory_id, slug = pl.labels.get(LABEL_ID), pl.labels.get(LABEL_PROJECT)
    if not isinstance(deps, FactoryDeps) or not factory_id or not slug:
        return
    try:
        view = await asyncio.to_thread(own_view, deps, factory_id, slug)
        problems = outside_spec(view, child) if view is not None else []
        if not problems:
            return
        subject = f"session:{child.id}"
        seen = await asyncio.to_thread(
            deps.factory_store.find_events, factory_id, OUTSIDE_KIND, 0, subject, limit=1
        )
        if seen:
            return
        title = display_title(child.title, child.id)
        await events.emit(
            app_state,
            factory_id,
            OUTSIDE_KIND,
            severity="warning",
            project=slug,
            from_id=pl.id,
            to_id=child.id,
            subject=subject,
            summary=f"{title} runs outside the team of {slug}: {'; '.join(problems)}",
            data={
                "session_id": child.id,
                "agent": child.sub_agent_name,
                "model": child.model_override,
                "problems": problems,
                "mode": mode(),
                "def_version": view.def_version if view is not None else None,
            },
        )
    except Exception:  # noqa: BLE001 - the record is best effort
        _logger.warning("factory team: could not check %s", child.id, exc_info=True)


# ── Snapshot ───────────────────────────────────────────────────


def snapshot_view(
    doc: Mapping[str, Any],
    project: FactoryProject,
    children: Sequence[Conversation],
    *,
    def_version: int | None,
) -> dict[str, Any]:
    """:returns: ``projects[].team_spec``: the team, who can't go, drift from the live team."""
    from omnigent.server.routes._sessions.helpers import _session_status_from_cache
    from omnigent.util.session_lifecycle import is_session_closed

    view = view_of(doc, project, def_version=def_version)
    facts = project_facts(project)
    blocked = team_spec.remove_reasons({"members": list(view.members)}, facts)
    members = [
        {
            **member,
            "model_label": member.get("model") or default_model(str(member["agent"])),
            "brief": bool(view.briefs.get(str(member["id"]))),
            "remove_blocked": blocked.get(str(member["id"])),
        }
        for member in view.members
    ]
    drift: list[dict[str, Any]] = []
    live = [c for c in children if not is_session_closed(c.labels, c.title)]
    if view.own:
        for child in live:
            for problem in outside_spec(view, child):
                drift.append(
                    {
                        "kind": "outside_team",
                        "session_id": child.id,
                        "text": f"{display_title(child.title, child.id)}: {problem}",
                    }
                )
        builder_agents = {str(m["agent"]) for m in view.builders()}
        working = [
            c
            for c in live
            if c.sub_agent_name in builder_agents
            and not (c.title or "").startswith(("review-", "demo-"))
            and _session_status_from_cache(c.id, c.live_status) == "running"
        ]
        if len(working) > view.builders_at_once:
            drift.append(
                {
                    "kind": "over_builders",
                    "session_id": None,
                    "text": f"{len(working)} builders work; the team runs "
                    f"{view.builders_at_once} at once",
                }
            )
    return {
        "own": view.own,
        "template": view.template,
        "builders_at_once": view.builders_at_once,
        "builders_from": view.builders_from,
        "budget_usd": view.budget_usd,
        "budget_from": view.budget_from,
        "members": members,
        "drift": drift,
        "drifted": bool(drift),
        "mode": mode(),
        "def_version": def_version,
    }


__all__ = [
    "INTERRUPTED_KIND",
    "OUTSIDE_KIND",
    "TEAM_GUARD_HANDLER",
    "TEAM_SPEC_ENV",
    "UPDATED_KIND",
    "WHEN",
    "Rate",
    "RateBook",
    "TeamView",
    "budget_usd",
    "builders_at_once",
    "bundle_agents",
    "catalog",
    "charter_lines",
    "charter_team",
    "default_model",
    "deliver_update",
    "dispatch_choice",
    "facts_lookup",
    "guard_arguments",
    "install",
    "mode",
    "observe_dispatch",
    "outside_spec",
    "own_view",
    "preview_effects",
    "project_facts",
    "rate_book",
    "role_default_model",
    "snapshot_view",
    "team_rate",
    "team_update_text",
    "vendor_of",
    "view_of",
]
