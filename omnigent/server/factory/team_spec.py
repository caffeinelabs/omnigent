"""The team spec (definition block ``team``): who works on a project, on which vendor and model.

- ``team.templates[]``: named team shapes ``{id, label, summary, roles: [{role,
  vendor, model, count}], builders_at_once?, default}``. The default template is
  the shape of every project that has no team of its own (its *implicit* team).
- ``team.projects.<slug>``: one project's own team ``{template, builders_at_once,
  budget_usd, members: [{id M-n, handle, role, agent, vendor, model}], briefs:
  {M-n: text}}``. ``None`` keeps the charter's value for ``builders_at_once`` and
  ``budget_usd`` and the bundle's default for a member's ``model``.

Members change like any list with ids: add one at ``team.projects.<slug>.members``,
replace or remove one at ``….members.M-3``, one field at ``….members.M-3.model``,
a member's brief at ``….briefs.M-3``. The first change to a project stores its
implicit team (members ``M-1``… in the default template's order) as its own, so
every change and every diff line names a real member. Member numbers are never
reused within a project.

Classes (Tier 0, :func:`classify`): adding a member, a vendor or model change,
more builders at once, a higher budget, a brief and any template are B
(owners); removing a member, fewer builders at once, a lower budget, a new
handle or a template label are A. Removing the only member a ready gate needs
is refused (422 ``gate_requires_role``, :func:`gate_refusals`).

Pure: no store and no server. :mod:`omnigent.server.factory.team` reads it at run time.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from omnigent.factory import (
    PROJECT_SLUG_RE,
    ROLE_DEMO,
    ROLE_ENV_KEEPER,
    ROLE_IC,
    ROLE_PROJECT_LEAD,
    ROLE_REVIEWER,
)
from omnigent.factory_gates import DEMO_GATE, READY_GATES
from omnigent.server.factory.definition_blocks import Slug

TEAM: Final = "team"
PROJECTS: Final = "team.projects"
TEMPLATES: Final = "team.templates"
TeamRole = Literal["project_lead", "ic", "reviewer", "env_keeper", "demo"]
Vendor = Literal["claude", "codex"]
ROLE_ORDER: Final = (ROLE_PROJECT_LEAD, ROLE_IC, ROLE_REVIEWER, ROLE_ENV_KEEPER, ROLE_DEMO)
BUILDER: Final = ROLE_IC
VENDORS: Final[dict[str, tuple[str, ...]]] = {
    ROLE_PROJECT_LEAD: ("claude",),
    ROLE_IC: ("claude", "codex"),
    ROLE_REVIEWER: ("codex", "claude"),
    ROLE_ENV_KEEPER: ("claude",),
    ROLE_DEMO: ("claude",),
}
"""The vendors each role's bundle runs on today, its default first."""
PL_AGENT: Final = "factory-pl"
AGENTS: Final[dict[tuple[str, str], str]] = {
    (ROLE_PROJECT_LEAD, "claude"): PL_AGENT,
    (ROLE_IC, "claude"): "ic",
    (ROLE_IC, "codex"): "ic_codex",
    (ROLE_REVIEWER, "codex"): "reviewer",
    (ROLE_REVIEWER, "claude"): "ic",
    (ROLE_ENV_KEEPER, "claude"): "env_keeper",
    (ROLE_DEMO, "claude"): "demo",
}
"""The PL bundle's agent that runs a role on a vendor (a Claude review runs as an ``ic``)."""
ROLE_LABELS: Final[dict[str, str]] = {
    ROLE_PROJECT_LEAD: "project lead",
    ROLE_IC: "builder",
    ROLE_REVIEWER: "reviewer",
    ROLE_ENV_KEEPER: "env keeper",
    ROLE_DEMO: "demo agent",
}
VENDOR_LABELS: Final[dict[str, str]] = {"claude": "Claude", "codex": "Codex"}
VENDOR_RUNTIMES: Final[dict[str, str]] = {"claude": "Claude Code", "codex": "Codex"}
MEMBER_FIELDS: Final = ("handle", "vendor", "model", "agent")
PROJECT_FIELDS: Final = ("template", "builders_at_once", "budget_usd")
PROJECT_KEYS: Final = (*PROJECT_FIELDS, "members", "briefs")
MEMBERS_MAX: Final = 16
PROJECTS_MAX: Final = 50
TEMPLATES_MAX: Final = 12
BRIEF_MAX: Final = 1000
BUILDERS_MAX: Final = 10
BUDGET_MIN: Final = 1.0
BUDGET_MAX: Final = 1000.0
MEMBER_ID_RE: Final = re.compile(r"^M-([1-9][0-9]{0,4})$")
_MODEL_RE: Final = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
_HANDLE_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9 ._-]{0,23}$")
NOT_TRIALABLE_REASON: Final = (
    "a trial needs at least 3 merged PRs in its window and one team merges about one a "
    "day: trial team changes on a template instead"
)


def vendor_refusal(role: str, vendor: str) -> str:
    """:returns: Why *role* cannot run on *vendor*, in words for a tile or a refusal."""
    allowed = VENDORS.get(role, ())
    name = ROLE_LABELS.get(role, role)
    runs = " and ".join(VENDOR_RUNTIMES.get(v, v) for v in allowed) or "nothing"
    return (
        f"{VENDOR_LABELS.get(vendor, vendor)} is not available for {name}s yet: "
        f"they run on {runs} today"
    )


# ── Gates (Tier 0) ─────────────────────────────────────────────


@dataclass(frozen=True)
class GateNeed:
    """
    A role a ready gate needs on every team.

    :param when: ``always``, ``env`` (the charter needs an env) or ``previews`` (previews on).
    """

    gate: str
    role: str
    text: str
    when: Literal["always", "env", "previews"]


GATE_NEEDS: Final = (
    GateNeed("review", ROLE_REVIEWER, "the review gate needs a reviewer", "always"),
    GateNeed("verify", ROLE_ENV_KEEPER, "the verify gate needs an env keeper", "env"),
    GateNeed(DEMO_GATE, ROLE_DEMO, "the demo gate needs one while previews are on", "previews"),
    GateNeed("builder", ROLE_IC, "a project needs at least one builder", "always"),
)
"""What each project needs to reach ready (``factory_gates.READY_GATES`` plus the demo gate)."""
assert {"review", "verify"} <= set(READY_GATES)


@dataclass(frozen=True)
class ProjectFacts:
    """What decides a project's gate needs: its charter's env and previews."""

    env_needed: bool = True
    previews_on: bool = True


FactsLookup = Callable[[str], ProjectFacts | None]
"""``slug -> facts``, ``None`` for a project the factory does not have."""


def needed(need: GateNeed, facts: ProjectFacts) -> bool:
    """Whether *need* applies to a project with *facts*."""
    if need.when == "env":
        return facts.env_needed
    if need.when == "previews":
        return facts.previews_on
    return True


# ── The document ───────────────────────────────────────────────


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _model_id(value: str | None) -> str | None:
    if value is not None and not _MODEL_RE.match(value):
        raise ValueError(
            "a model is an alias such as 'sonnet' or the provider's model id (lowercase, ≤ 64)"
        )
    return value


def _handle(value: str | None) -> str | None:
    if value is not None and not _HANDLE_RE.match(value):
        raise ValueError("a handle is a short name such as 'Rex' (a letter first, ≤ 24)")
    return value


def _project_slug(value: str) -> str:
    if not PROJECT_SLUG_RE.match(value):
        raise ValueError(f"{value!r} is not a project slug")
    return value


ModelId = Annotated[str | None, AfterValidator(_model_id)]
Handle = Annotated[str | None, AfterValidator(_handle)]
ProjectSlug = Annotated[str, AfterValidator(_project_slug)]
BriefText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=BRIEF_MAX)]


def _short(max_length: int) -> StringConstraints:
    return StringConstraints(strip_whitespace=True, max_length=max_length)


class TemplateRoleDef(_Block):
    """``count`` members of *role* on *vendor* (``None``: the role's default) and *model*."""

    role: TeamRole
    vendor: Vendor | None = None
    model: ModelId = None
    count: int = Field(1, ge=1, le=6)

    @model_validator(mode="after")
    def _vendor(self) -> TemplateRoleDef:
        if self.vendor is not None and self.vendor not in VENDORS[self.role]:
            raise ValueError(vendor_refusal(self.role, self.vendor))
        return self


class TemplateDef(_Block):
    """A named team shape. ``builders_at_once`` applies when a project switches to it."""

    id: Slug
    label: Annotated[str, _short(40)] = ""
    summary: Annotated[str, _short(200)] = ""
    roles: list[TemplateRoleDef] = Field(min_length=1, max_length=12)
    builders_at_once: int | None = Field(None, ge=1, le=BUILDERS_MAX)
    default: bool = False

    @model_validator(mode="after")
    def _shape(self) -> TemplateDef:
        leads = sum(r.count for r in self.roles if r.role == ROLE_PROJECT_LEAD)
        if leads != 1:
            raise ValueError(f"template {self.id}: a team has exactly one project lead")
        if sum(r.count for r in self.roles) > MEMBERS_MAX:
            raise ValueError(f"template {self.id}: at most {MEMBERS_MAX} members")
        return self


class MemberDef(_Block):
    """One seat of a project's team; ``agent`` and ``vendor`` default from the role."""

    id: str
    role: TeamRole
    handle: Handle = None
    vendor: Vendor | None = None
    agent: str | None = None
    model: ModelId = None

    @model_validator(mode="before")
    @classmethod
    def _defaults(cls, data: Any) -> Any:
        if not isinstance(data, Mapping) or data.get("role") not in VENDORS:
            return data
        out = dict(data)
        role = str(out["role"])
        if out.get("vendor") is None:
            out["vendor"] = VENDORS[role][0]
        if out.get("agent") is None:
            out["agent"] = AGENTS.get((role, str(out["vendor"])))
        return out

    @model_validator(mode="after")
    def _consistent(self) -> MemberDef:
        if not MEMBER_ID_RE.match(self.id):
            raise ValueError("a member id is M-<n>")
        vendor = self.vendor or VENDORS[self.role][0]
        if vendor not in VENDORS[self.role]:
            raise ValueError(f"{self.id}: {vendor_refusal(self.role, vendor)}")
        if self.agent != AGENTS[(self.role, vendor)]:
            raise ValueError(
                f"{self.id}: a {ROLE_LABELS[self.role]} on {vendor} runs as "
                f"{AGENTS[(self.role, vendor)]}, not {self.agent}"
            )
        return self


def member_number(member_id: str) -> int:
    """:returns: ``3`` for ``M-3`` (``0`` for anything else)."""
    match = MEMBER_ID_RE.match(member_id)
    return int(match.group(1)) if match else 0


class ProjectTeamDef(_Block):
    """
    One project's own team.

    :param seq: The highest member number the project used (server-kept, never reused).
    """

    template: Slug | None = None
    builders_at_once: int | None = Field(None, ge=1, le=BUILDERS_MAX)
    budget_usd: float | None = Field(None, ge=BUDGET_MIN, le=BUDGET_MAX)
    members: list[MemberDef] = Field(default_factory=list, max_length=MEMBERS_MAX)
    briefs: dict[str, BriefText] = Field(default_factory=dict)
    seq: int = Field(0, ge=0)

    @model_validator(mode="after")
    def _shape(self) -> ProjectTeamDef:
        ids = [m.id for m in self.members]
        if len(set(ids)) != len(ids):
            raise ValueError("a member id is listed twice")
        if sum(1 for m in self.members if m.role == ROLE_PROJECT_LEAD) != 1:
            raise ValueError("a team has exactly one project lead")
        for key, text in self.briefs.items():
            if key not in ids:
                raise ValueError(f"briefs.{key}: no member {key}")
            if not text:
                raise ValueError(f"briefs.{key}: a brief has text (remove it instead)")
        self.seq = max([self.seq, *(member_number(i) for i in ids)])
        return self


def _standard() -> dict[str, Any]:
    return {
        "id": "standard",
        "label": "Standard",
        "summary": "A Claude and a Codex builder, a cross-vendor reviewer, an env keeper and "
        "a demo agent.",
        "roles": [
            {"role": ROLE_PROJECT_LEAD},
            {"role": ROLE_IC, "vendor": "claude"},
            {"role": ROLE_IC, "vendor": "codex"},
            {"role": ROLE_REVIEWER, "vendor": "codex"},
            {"role": ROLE_ENV_KEEPER},
            {"role": ROLE_DEMO},
        ],
        "default": True,
    }


def default_templates() -> list[dict[str, Any]]:
    """:returns: The built-in templates (``standard`` is every bundle agent: today's team)."""
    return [
        _standard(),
        {
            "id": "pair",
            "label": "Pair",
            "summary": "Two Claude builders, a Codex reviewer, an env keeper and a demo agent.",
            "roles": [
                {"role": ROLE_PROJECT_LEAD},
                {"role": ROLE_IC, "vendor": "claude", "count": 2},
                {"role": ROLE_REVIEWER, "vendor": "codex"},
                {"role": ROLE_ENV_KEEPER},
                {"role": ROLE_DEMO},
            ],
        },
        {
            "id": "lean",
            "label": "Lean",
            "summary": "One builder, a reviewer and an env keeper; no demo (previews off only).",
            "roles": [
                {"role": ROLE_PROJECT_LEAD},
                {"role": ROLE_IC, "vendor": "claude"},
                {"role": ROLE_REVIEWER, "vendor": "codex"},
                {"role": ROLE_ENV_KEEPER},
            ],
            "builders_at_once": 1,
        },
        {
            "id": "investigation",
            "label": "Investigation",
            "summary": "Two Claude builders and a reviewer, no env (for work that needs none).",
            "roles": [
                {"role": ROLE_PROJECT_LEAD},
                {"role": ROLE_IC, "vendor": "claude", "count": 2},
                {"role": ROLE_REVIEWER, "vendor": "codex"},
            ],
        },
    ]


class TeamDef(_Block):
    """Team templates and the projects that have their own team."""

    templates: list[TemplateDef] = Field(
        default_factory=lambda: [TemplateDef.model_validate(t) for t in default_templates()],
        max_length=TEMPLATES_MAX,
    )
    projects: dict[ProjectSlug, ProjectTeamDef] = Field(default_factory=dict)

    @field_validator("templates")
    @classmethod
    def _templates(cls, items: list[TemplateDef]) -> list[TemplateDef]:
        ids = [t.id for t in items]
        if len(set(ids)) != len(ids):
            raise ValueError("a template id is listed twice")
        if sum(1 for t in items if t.default) > 1:
            raise ValueError("at most one template is the default")
        return items

    @field_validator("projects")
    @classmethod
    def _projects(cls, items: dict[str, ProjectTeamDef]) -> dict[str, ProjectTeamDef]:
        if len(items) > PROJECTS_MAX:
            raise ValueError(f"at most {PROJECTS_MAX} projects have their own team")
        return items


# ── Reading a doc (plain dicts) ────────────────────────────────


def _team(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    found = doc.get(TEAM)
    return found if isinstance(found, Mapping) else {}


def templates(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """:returns: The doc's templates."""
    found = _team(doc).get("templates")
    return [dict(t) for t in found if isinstance(t, Mapping)] if isinstance(found, list) else []


def own_teams(doc: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """:returns: Slug -> the project's own team, for the projects that have one."""
    found = _team(doc).get("projects")
    if not isinstance(found, Mapping):
        return {}
    return {str(k): dict(v) for k, v in found.items() if isinstance(v, Mapping)}


def template(doc: Mapping[str, Any], template_id: str | None) -> dict[str, Any] | None:
    """:returns: Template *template_id* of *doc*, or ``None``."""
    return next((t for t in templates(doc) if t.get("id") == template_id), None)


def default_template(doc: Mapping[str, Any]) -> dict[str, Any]:
    """:returns: The default template (else the first, else the built-in standard)."""
    found = templates(doc)
    return next((t for t in found if t.get("default")), found[0] if found else _standard())


def new_member(
    role: str,
    number: int,
    *,
    vendor: str | None = None,
    model: str | None = None,
    handle: str | None = None,
) -> dict[str, Any]:
    """:returns: Member ``M-<number>`` with its vendor and agent filled in."""
    vendor = vendor or VENDORS[role][0]
    return {
        "id": f"M-{number}",
        "role": role,
        "handle": handle,
        "vendor": vendor,
        "agent": AGENTS.get((role, vendor)),
        "model": model,
    }


def filled(member: Mapping[str, Any]) -> dict[str, Any]:
    """:returns: *member* with every key, vendor and agent defaulted from its role."""
    role = str(member.get("role"))
    vendor = member.get("vendor") or (VENDORS[role][0] if role in VENDORS else None)
    return {
        "id": member.get("id"),
        "role": role,
        "handle": member.get("handle"),
        "vendor": vendor,
        "agent": member.get("agent") or AGENTS.get((role, str(vendor))),
        "model": member.get("model"),
    }


def expand(shape: Mapping[str, Any], start: int = 0) -> list[dict[str, Any]]:
    """:returns: The members of template *shape*, numbered from ``M-<start + 1>``."""
    out: list[dict[str, Any]] = []
    for entry in shape.get("roles") or []:
        for _ in range(int(entry.get("count") or 1)):
            out.append(
                new_member(
                    str(entry["role"]),
                    start + len(out) + 1,
                    vendor=entry.get("vendor"),
                    model=entry.get("model"),
                )
            )
    return out


def implicit_team(doc: Mapping[str, Any]) -> dict[str, Any]:
    """
    :returns: The team of a project without its own: the default template's
        members, the charter's builders at once and budget.
    """
    shape = default_template(doc)
    members = expand(shape)
    return {
        "template": shape.get("id"),
        "builders_at_once": None,
        "budget_usd": None,
        "members": members,
        "briefs": {},
        "seq": len(members),
    }


def team_of(doc: Mapping[str, Any], slug: str) -> dict[str, Any]:
    """:returns: Project *slug*'s team: its own, else its implicit one (a copy)."""
    own = own_teams(doc).get(slug)
    team = copy.deepcopy(own) if own is not None else implicit_team(doc)
    team["members"] = [filled(m) for m in team.get("members") or []]
    team.setdefault("briefs", {})
    return team


def has_own(doc: Mapping[str, Any], slug: str) -> bool:
    """Whether project *slug* has its own team in *doc*."""
    return slug in own_teams(doc)


def count(team: Mapping[str, Any], role: str) -> int:
    """:returns: How many members of *role* *team* has."""
    return sum(1 for m in team.get("members") or [] if m.get("role") == role)


# ── Paths ──────────────────────────────────────────────────────


def is_team_path(path: str) -> bool:
    """Whether *path* lies in the ``team`` block."""
    return path == TEAM or path.startswith(f"{TEAM}.")


def is_project_path(path: str) -> bool:
    """Whether *path* is a project's team or lies in one (``team.projects.<slug>…``)."""
    return path.startswith(f"{PROJECTS}.")


@dataclass(frozen=True)
class TeamPath:
    """
    ``team.projects.<slug>[.<part>[.<item>[.<field>]]]``.

    :param part: ``None`` (the whole team), a field of :data:`PROJECT_FIELDS`,
        ``members`` or ``briefs``.
    :param item: A member id.
    :param field: A member's field (:data:`MEMBER_FIELDS`).
    """

    slug: str
    part: str | None = None
    item: str | None = None
    field: str | None = None

    @property
    def base(self) -> str:
        """:returns: ``team.projects.<slug>``."""
        return f"{PROJECTS}.{self.slug}"


def parse_path(path: str) -> TeamPath:
    """
    :returns: The parts of a project team path.
    :raises ValueError: With what is wrong in words.
    """
    if not is_project_path(path):
        raise ValueError(
            "change a project's team at team.projects.<slug> (or a template at team.templates)"
        )
    rest = path[len(PROJECTS) + 1 :].split(".")
    slug = rest[0]
    if not PROJECT_SLUG_RE.match(slug):
        raise ValueError(f"{slug!r} is not a project slug")
    if len(rest) == 1:
        return TeamPath(slug)
    part = rest[1]
    if part in PROJECT_FIELDS and len(rest) == 2:
        return TeamPath(slug, part)
    if part == "members" and len(rest) <= 4:
        item = rest[2] if len(rest) >= 3 else None
        field = rest[3] if len(rest) == 4 else None
        if item is not None and not MEMBER_ID_RE.match(item):
            raise ValueError(f"{item!r} is not a member id (M-<n>)")
        if field is not None and field not in MEMBER_FIELDS:
            raise ValueError(f"a member's {field!r} is not one of {', '.join(MEMBER_FIELDS)}")
        return TeamPath(slug, part, item, field)
    if part == "briefs" and len(rest) == 3 and MEMBER_ID_RE.match(rest[2]):
        return TeamPath(slug, part, rest[2])
    raise ValueError(
        f"{path} is not part of a team: use team.projects.<slug>, its "
        f"{', '.join(PROJECT_FIELDS)}, members[.M-<n>[.<field>]] or briefs.M-<n>"
    )


def effective_get(doc: Mapping[str, Any], path: str) -> Any:
    """:returns: The value at a project team *path*, own or implicit (``None`` when absent)."""
    try:
        where = parse_path(path)
    except ValueError:
        return None
    team = team_of(doc, where.slug)
    if where.part is None:
        return team
    if where.part in PROJECT_FIELDS:
        return team.get(where.part)
    if where.part == "briefs":
        return (team.get("briefs") or {}).get(where.item or "")
    if where.item is None:
        return team["members"]
    member = next((m for m in team["members"] if m.get("id") == where.item), None)
    if member is None or where.field is None:
        return member
    return member.get(where.field)


# ── Changing a doc ─────────────────────────────────────────────


class TeamError(Exception):
    """A refused team change: rendered by the caller as a ``CockpitError``."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _check_value(where: TeamPath, value: Any) -> None:
    """Bounds of a project field (422 ``out_of_bounds``)."""
    if value is None:
        return
    if where.part == "builders_at_once":
        number = _number(value)
        if number is None or number != int(number) or not 1 <= number <= BUILDERS_MAX:
            raise TeamError(
                422,
                "out_of_bounds",
                f"{where.base}.builders_at_once is 1..{BUILDERS_MAX}, or null for the charter's",
            )
    elif where.part == "budget_usd":
        number = _number(value)
        if number is None or not BUDGET_MIN <= number <= BUDGET_MAX:
            raise TeamError(
                422,
                "out_of_bounds",
                f"{where.base}.budget_usd is {BUDGET_MIN:g}..{BUDGET_MAX:g}, or null for the "
                "charter's",
            )
    elif where.part == "template" and not isinstance(value, str):
        raise TeamError(400, "invalid_body", f"{where.base}.template is a template id")


def parse(op: str, path: str, value: Any) -> tuple[str, str, Any, str | None]:
    """
    Validate one change of a project's team.

    :returns: ``(op, path, value, item id)`` of the change: a member's field
        becomes a replace of the member with ``{field: value}``.
    :raises TeamError: 400 ``invalid_body`` for a bad path or op, 422
        ``out_of_bounds`` for a value out of its bounds, 422 ``tier0`` for the
        server's member counter.
    """
    if path.startswith(f"{PROJECTS}.") and path.endswith(".seq"):
        raise TeamError(422, "tier0", "member numbers are handed out by the server (Tier 0)")
    try:
        where = parse_path(path)
    except ValueError as exc:
        raise TeamError(400, "invalid_body", str(exc)) from None
    if where.part is None:
        if op != "remove" and value is not None and not isinstance(value, Mapping):
            raise TeamError(400, "invalid_body", f"{path}: a team is an object")
        unknown = sorted(set(value or {}) - set(PROJECT_KEYS)) if op != "remove" else []
        if unknown:
            raise TeamError(
                400, "invalid_body", f"{path}: unknown {', '.join(unknown)} (seq is the server's)"
            )
        for key in PROJECT_FIELDS:
            if op != "remove" and isinstance(value, Mapping) and key in value:
                _check_value(TeamPath(where.slug, key), value[key])
        return op, where.base, value, None
    if where.part in PROJECT_FIELDS:
        if op != "replace":
            raise TeamError(400, "invalid_body", f"{path} is replaced, never {op}")
        _check_value(where, value)
        return op, path, value, None
    if where.part == "briefs":
        if op not in ("replace", "remove"):
            raise TeamError(400, "invalid_body", f"{path}: replace or remove a brief")
        if op == "replace" and value is not None and not isinstance(value, str):
            raise TeamError(400, "invalid_body", f"{path}: a brief is text")
        return op, f"{where.base}.briefs", value, where.item
    members = f"{where.base}.members"
    if where.item is None:
        if op != "add":
            raise TeamError(
                400,
                "invalid_body",
                f"add a member at {members}, replace or remove one at {members}.M-<n>",
            )
        if not isinstance(value, Mapping) or "id" in value:
            raise TeamError(
                400,
                "invalid_body",
                f"{members}: a new member is {{role, …}}; the server numbers it",
            )
        return op, members, value, None
    if where.field is not None:
        if op != "replace":
            raise TeamError(400, "invalid_body", f"{path}: a member's field is replaced")
        return "replace", members, {where.field: value}, where.item
    if op == "add":
        raise TeamError(400, "invalid_body", f"add a member at {members}; the server numbers it")
    if op == "replace" and not isinstance(value, Mapping):
        raise TeamError(400, "invalid_body", f"{path}: a member is an object")
    return op, members, value, where.item


def _projects(doc: dict[str, Any]) -> dict[str, Any]:
    found = doc.get(TEAM)
    team: dict[str, Any] = found if isinstance(found, dict) else {"templates": default_templates()}
    doc[TEAM] = team
    projects = team.get("projects")
    if not isinstance(projects, dict):
        projects = {}
        team["projects"] = projects
    return projects


def _own(doc: dict[str, Any], slug: str) -> dict[str, Any]:
    """Project *slug*'s own team in *doc*, stored from its implicit team when it has none."""
    projects = _projects(doc)
    if slug not in projects or not isinstance(projects[slug], dict):
        projects[slug] = implicit_team(doc)
    team = projects[slug]
    team["members"] = [filled(m) for m in team.get("members") or []]
    team["briefs"] = dict(team.get("briefs") or {})
    team["seq"] = max(
        [int(team.get("seq") or 0), *(member_number(str(m["id"])) for m in team["members"])]
    )
    return team


def _template_or_raise(doc: Mapping[str, Any], template_id: Any) -> dict[str, Any]:
    found = template(doc, str(template_id)) if template_id is not None else None
    if found is None:
        raise TeamError(404, "unknown_template", f"no template {template_id!r}", item=template_id)
    return found


def switch_template(team: dict[str, Any], shape: Mapping[str, Any]) -> None:
    """
    Reshape *team* to template *shape*: members that fit a seat stay (by role
    and vendor, oldest first), missing seats are added, the rest are removed.
    The template's ``builders_at_once`` applies when it sets one.
    """
    members = [filled(m) for m in team.get("members") or []]
    kept: set[str] = set()
    missing: list[tuple[str, str, str | None]] = []
    for entry in shape.get("roles") or []:
        role = str(entry["role"])
        vendor = entry.get("vendor") or VENDORS[role][0]
        want = int(entry.get("count") or 1)
        fits = [
            m
            for m in members
            if m["id"] not in kept and m["role"] == role and m["vendor"] == vendor
        ][:want]
        kept.update(m["id"] for m in fits)
        missing += [(role, vendor, entry.get("model"))] * (want - len(fits))
    seq = int(team.get("seq") or 0)
    out = [m for m in members if m["id"] in kept]
    for role, vendor, model in missing:
        seq += 1
        out.append(new_member(role, seq, vendor=vendor, model=model))
    team["members"] = out
    team["seq"] = seq
    team["briefs"] = {k: v for k, v in (team.get("briefs") or {}).items() if k in kept}
    team["template"] = shape.get("id")
    if shape.get("builders_at_once") is not None:
        team["builders_at_once"] = shape["builders_at_once"]


def _check_vendor(role: str, vendor: Any) -> None:
    if role in VENDORS and vendor is not None and vendor not in VENDORS[role]:
        raise TeamError(422, "invalid_definition", vendor_refusal(role, str(vendor)))


def _numbered(members: Iterable[Any], seq: int) -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    taken = {str(m.get("id")) for m in members if isinstance(m, Mapping) and m.get("id")}
    seq = max([seq, *(member_number(i) for i in taken)])
    for member in members:
        if not isinstance(member, Mapping):
            raise TeamError(422, "invalid_definition", "a member is an object")
        body = dict(member)
        if not body.get("id"):
            seq += 1
            body["id"] = f"M-{seq}"
        out.append(body)
    return out, seq


def _new_team(doc: dict[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    team: dict[str, Any]
    if body.get("template") is not None:
        shape = _template_or_raise(doc, body["template"])
        team = {
            "template": shape.get("id"),
            "builders_at_once": shape.get("builders_at_once"),
            "budget_usd": None,
            "members": expand(shape),
            "briefs": {},
        }
    else:
        team = implicit_team(doc)
    if "members" in body:
        team["members"], team["seq"] = _numbered(body.get("members") or [], 0)
    team["seq"] = max(
        [int(team.get("seq") or 0), *(member_number(str(m["id"])) for m in team["members"])]
    )
    for key in ("builders_at_once", "budget_usd", "briefs"):
        if key in body:
            team[key] = body[key] if key != "briefs" else dict(body[key] or {})
    return team


def apply(doc: dict[str, Any], op: str, path: str, value: Any, item: str | None) -> None:
    """
    Apply one parsed change (see :func:`parse`) to *doc* in place.

    :raises TeamError: 404 ``unknown_item`` / ``unknown_template``, 409
        ``duplicate_item`` (the project already has its own team), 422
        ``invalid_definition``.
    """
    where = parse_path(f"{path}.{item}" if item else path)
    slug = where.slug
    projects = _projects(doc)
    if where.part is None:
        body = dict(value or {})
        if op == "remove":
            projects.pop(slug, None)
            return
        if op == "add" and slug in projects:
            raise TeamError(409, "duplicate_item", f"{slug} already has its own team", item=slug)
        if op == "add" or slug not in projects:
            projects[slug] = _new_team(doc, body)
            return
        team = _own(doc, slug)
        if "template" in body and body["template"] != team.get("template"):
            if "members" not in body and body["template"] is not None:
                switch_template(team, _template_or_raise(doc, body["template"]))
            team["template"] = body["template"]
        if "members" in body:
            team["members"], team["seq"] = _numbered(body["members"] or [], team["seq"])
        for key in ("builders_at_once", "budget_usd"):
            if key in body:
                team[key] = body[key]
        if "briefs" in body:
            team["briefs"] = dict(body["briefs"] or {})
        return
    team = _own(doc, slug)
    if where.part in PROJECT_FIELDS:
        if where.part == "template" and value is not None and value != team.get("template"):
            switch_template(team, _template_or_raise(doc, value))
        else:
            team[where.part] = value
        return
    if where.part == "briefs":
        if not any(m["id"] == item for m in team["members"]):
            raise TeamError(404, "unknown_item", f"no member {item} in {slug}", item=item)
        text = str(value or "").strip() if op == "replace" else ""
        if text:
            team["briefs"][str(item)] = text
        else:
            team["briefs"].pop(str(item), None)
        return
    if item is None:
        body = dict(value)
        _check_vendor(str(body.get("role")), body.get("vendor"))
        team["seq"] += 1
        team["members"].append({**body, "id": f"M-{team['seq']}"})
        return
    index = next((i for i, m in enumerate(team["members"]) if m["id"] == item), None)
    if index is None:
        raise TeamError(404, "unknown_item", f"no member {item} in {slug}", item=item)
    current = team["members"][index]
    if op == "remove":
        if current["role"] == ROLE_PROJECT_LEAD:
            raise TeamError(
                422, "invalid_definition", "the project lead stays: every project has one"
            )
        team["members"].pop(index)
        team["briefs"].pop(str(item), None)
        return
    body = {k: v for k, v in dict(value).items() if k != "id"}
    if "role" in body and body["role"] != current["role"]:
        raise TeamError(
            422,
            "invalid_definition",
            f"{item} stays a {ROLE_LABELS.get(current['role'], current['role'])}: remove it "
            "and add a member in the other role",
        )
    merged = {**current, **body}
    _check_vendor(str(merged["role"]), merged.get("vendor"))
    if "vendor" in body and body["vendor"] != current.get("vendor"):
        if "agent" not in body:
            merged["agent"] = None
        if "model" not in body:
            merged["model"] = None
    team["members"][index] = filled(merged) if merged.get("agent") is None else merged


# ── Diff, class, lines ─────────────────────────────────────────


def _member_key(member: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return filled(member) if isinstance(member, Mapping) else None


def diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """
    :returns: ``[{path, before, after}]`` between the teams of two docs (own or
        implicit) for every project with its own team in either: its fields,
        each member (``team.projects.<slug>.members.M-3``) and each brief.
    """
    out: list[dict[str, Any]] = []
    for slug in sorted(set(own_teams(before)) | set(own_teams(after))):
        a, b = team_of(before, slug), team_of(after, slug)
        base = f"{PROJECTS}.{slug}"
        for key in PROJECT_FIELDS:
            if a.get(key) != b.get(key):
                out.append({"path": f"{base}.{key}", "before": a.get(key), "after": b.get(key)})
        old = {m["id"]: _member_key(m) for m in a["members"]}
        new = {m["id"]: _member_key(m) for m in b["members"]}
        for member_id in [*old, *(i for i in new if i not in old)]:
            if old.get(member_id) != new.get(member_id):
                out.append(
                    {
                        "path": f"{base}.members.{member_id}",
                        "before": old.get(member_id),
                        "after": new.get(member_id),
                    }
                )
        old_briefs, new_briefs = a.get("briefs") or {}, b.get("briefs") or {}
        for member_id in [*old_briefs, *(i for i in new_briefs if i not in old_briefs)]:
            if old_briefs.get(member_id) != new_briefs.get(member_id):
                out.append(
                    {
                        "path": f"{base}.briefs.{member_id}",
                        "before": old_briefs.get(member_id),
                        "after": new_briefs.get(member_id),
                    }
                )
    return out


def classify(path: str, before: Any, after: Any) -> Literal["A", "B"]:
    """
    :returns: The approval class of one team diff entry: A when it is cheaper
        or narrower (a removal, fewer builders, a lower budget, a handle), else B.
    """
    if not is_project_path(path):
        return "B"
    try:
        where = parse_path(path)
    except ValueError:
        return "B"
    if where.part in ("builders_at_once", "budget_usd"):
        a, b = _number(after), _number(before)
        return "A" if a is not None and b is not None and a <= b else "B"
    if where.part == "template":
        return "A"
    if where.part == "members" and where.item is not None:
        if after is None and isinstance(before, Mapping):
            return "A"
        if not isinstance(before, Mapping) or not isinstance(after, Mapping):
            return "B"
        changed = {k for k in MEMBER_FIELDS if before.get(k) != after.get(k)}
        return "A" if changed <= {"handle"} else "B"
    return "B"


def editor_may_apply(entries: Iterable[Mapping[str, Any]]) -> bool:
    """Whether *entries* only narrow project teams (class A), which editors may apply."""
    found = list(entries)
    return bool(found) and all(
        is_project_path(str(e["path"]))
        and classify(str(e["path"]), e.get("before"), e.get("after")) == "A"
        for e in found
    )


def member_text(member: Mapping[str, Any], *, with_id: bool = False) -> str:
    """
    :returns: e.g. ``"Rex · builder · claude · sonnet"`` (``default`` for the
        bundle's model); with *with_id*, ``"M-2 Rex · builder · …"``.
    """
    filled_in = filled(member)
    role = ROLE_LABELS.get(str(filled_in["role"]), str(filled_in["role"]))
    head = [str(filled_in["id"])] if with_id else []
    if filled_in.get("handle"):
        head.append(str(filled_in["handle"]))
    prefix = f"{' '.join(head)} · " if head else ""
    return f"{prefix}{role} · {filled_in['vendor']} · {filled_in.get('model') or 'default'}"


def _value(value: Any) -> str:
    return "charter's" if value is None else str(value)


def diff_line(entry: Mapping[str, Any]) -> str:
    """:returns: One line for a team diff entry (``"~ team.projects.x.members.M-3 · …"``)."""
    path, before, after = str(entry["path"]), entry.get("before"), entry.get("after")
    try:
        where = parse_path(path)
    except ValueError:
        return f"{path}: {before} → {after}"
    if where.part == "members" and where.item is not None:
        if before is None and isinstance(after, Mapping):
            return f"+ {path} · {member_text(after)}"
        if after is None and isinstance(before, Mapping):
            return f"- {path} · {member_text(before)}"
        was = before if isinstance(before, Mapping) else {}
        now = after if isinstance(after, Mapping) else {}
        empty = {"model": "default"}
        parts = [
            f"{k} {was.get(k) or empty.get(k, 'none')} → {now.get(k) or empty.get(k, 'none')}"
            for k in MEMBER_FIELDS
            if was.get(k) != now.get(k)
        ]
        return f"~ {path} · {'; '.join(parts)}"
    if where.part == "briefs":
        what = "brief set" if after and not before else "brief cleared" if not after else "brief"
        return f"~ {path} · {what}"
    return f"{path}: {_value(before)} → {_value(after)}"


# ── Tier 0: gates, unknown projects ────────────────────────────


def gate_refusals(
    before: Mapping[str, Any], after: Mapping[str, Any], facts: FactsLookup | None = None
) -> list[dict[str, Any]]:
    """
    The changes from *before* to *after* that remove the only member a ready gate needs.

    A team a project gets now (its first edit, or a new team from a template)
    must have every member its gates need, even when the shape it came from had none.

    :param facts: ``slug -> facts`` (unknown: env and previews on, the strict case).
    :returns: ``[{path, project, gate, role, message}]``, empty when every gate stays reachable.
    """
    out: list[dict[str, Any]] = []
    had = set(own_teams(before))
    for slug in sorted(had | set(own_teams(after))):
        a, b = team_of(before, slug), team_of(after, slug)
        known = facts(slug) if facts is not None else None
        project = known or ProjectFacts()
        for need in GATE_NEEDS:
            if not needed(need, project):
                continue
            if count(b, need.role) == 0 and (count(a, need.role) > 0 or slug not in had):
                out.append(
                    {
                        "path": f"{PROJECTS}.{slug}",
                        "project": slug,
                        "gate": need.gate,
                        "role": need.role,
                        "message": f"{slug}: can't remove the last {ROLE_LABELS[need.role]}: "
                        f"{need.text} (Tier 0)",
                    }
                )
    return out


def unknown_projects(
    before: Mapping[str, Any], after: Mapping[str, Any], facts: FactsLookup | None
) -> list[str]:
    """:returns: Slugs that got their own team in *after* but are no project of the factory."""
    if facts is None:
        return []
    new = set(own_teams(after)) - set(own_teams(before))
    return sorted(slug for slug in new if facts(slug) is None)


def remove_reasons(team: Mapping[str, Any], facts: ProjectFacts) -> dict[str, str]:
    """
    :returns: Member id -> why it cannot be removed now ("Can't remove: the review
        gate needs a reviewer (Tier 0)"); members missing from the map can go.
    """
    out: dict[str, str] = {}
    for member in team.get("members") or []:
        role = member.get("role")
        if role == ROLE_PROJECT_LEAD:
            out[str(member["id"])] = "The project lead stays: every project has one."
            continue
        need = next((n for n in GATE_NEEDS if n.role == role and needed(n, facts)), None)
        if need is not None and count(team, str(role)) <= 1:
            out[str(member["id"])] = f"Can't remove: {need.text} (Tier 0)"
    return out


def template_lock(shape: Mapping[str, Any], facts: ProjectFacts) -> str | None:
    """:returns: Why a project with *facts* cannot use template *shape* (``None`` when it can)."""
    roles = {str(r.get("role")) for r in shape.get("roles") or []}
    for need in GATE_NEEDS:
        if needed(need, facts) and need.role not in roles:
            return f"Locked: {need.text}."
    return None


# ── Revert ─────────────────────────────────────────────────────


def revert_entry(doc: dict[str, Any], old: Mapping[str, Any], path: str, value: Any) -> None:
    """
    Put team *path* of *doc* back to its value in *old*, unless *doc* changed
    it again since (its value there is no longer *value*).
    """
    if effective_get(doc, path) != value:
        return
    where = parse_path(path)
    previous = effective_get(old, path)
    team = _own(doc, where.slug)
    if where.part in PROJECT_FIELDS:
        team[where.part] = previous
    elif where.part == "briefs" and where.item is not None:
        if previous:
            team["briefs"][where.item] = previous
        else:
            team["briefs"].pop(where.item, None)
    elif where.part == "members" and where.item is not None:
        index = next((i for i, m in enumerate(team["members"]) if m["id"] == where.item), None)
        if previous is None and index is not None:
            team["members"].pop(index)
        elif previous is not None and index is None:
            team["members"].append(dict(previous))
        elif previous is not None and index is not None:
            team["members"][index] = dict(previous)


__all__ = [
    "AGENTS",
    "BUILDER",
    "GATE_NEEDS",
    "MEMBER_FIELDS",
    "NOT_TRIALABLE_REASON",
    "PROJECTS",
    "PROJECT_FIELDS",
    "ROLE_LABELS",
    "ROLE_ORDER",
    "TEMPLATES",
    "VENDORS",
    "FactsLookup",
    "GateNeed",
    "MemberDef",
    "ProjectFacts",
    "ProjectTeamDef",
    "TeamDef",
    "TeamError",
    "TeamPath",
    "TemplateDef",
    "apply",
    "classify",
    "count",
    "default_template",
    "default_templates",
    "diff",
    "diff_line",
    "editor_may_apply",
    "effective_get",
    "expand",
    "filled",
    "gate_refusals",
    "has_own",
    "implicit_team",
    "is_project_path",
    "is_team_path",
    "member_text",
    "own_teams",
    "parse",
    "parse_path",
    "remove_reasons",
    "revert_entry",
    "switch_template",
    "team_of",
    "template",
    "template_lock",
    "templates",
    "unknown_projects",
    "vendor_refusal",
]
