"""The product-agnostic definition blocks: product, environment, strategy, discovery and more.

Everything a factory knows about its product is data in these blocks of the
versioned definition (:mod:`.definition`): what the product is and how it is
delivered (``product``), how its dev env and previews run (``environment``),
what the factory is trying to achieve (``strategy``), how it finds out what
users need (``discovery``), what each role is told (``briefs``), which
vendor and model a role runs on (``role_defaults``) and what the cockpit's
levels are called, who answers on each and who changes them (``structure``).
No product name, metric, source or prompt text lives in code.

The blocks are inert until a reader uses them; discovery stays dark behind
``discovery.enabled`` (off by default) and the server env :data:`DISCOVERY_ENV`.

Lists whose items carry an ``id`` (:data:`LISTS`) are changed item by item:
``add`` at the list, ``replace`` or ``remove`` at ``<list>.<id>``. Goal ids
(``G-n``) are numbered by the server and never reused.
"""

from __future__ import annotations

import itertools
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
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

from omnigent.server.factory.charter import BRANCH_RE

DISCOVERY_ENV: Final = "OMNIGENT_FACTORY_DISCOVERY"
"""Server switch for discovery routes, tools and jobs (off unless ``1``/``true``)."""
BRIEF_MAX: Final = 4000
SOURCE_PARAMS_MAX: Final = 4000
"""The largest a source's ``params`` may be, as compact JSON."""

_SLUG_RE: Final = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
GOAL_ID_RE: Final = re.compile(r"^G-([1-9][0-9]{0,5})$")
_ALIAS_RE: Final = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_REPO_RE: Final = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_HOST_RE: Final = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_RELEASE_RE: Final = re.compile(
    r"^(merged|github_release|manual|github_deployment:[A-Za-z0-9_.-]{1,64})$"
)
_SECRET_WORDS: Final = frozenset(
    {
        "apikey",
        "auth",
        "authorization",
        "bearer",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "jwt",
        "oauth",
        "passwd",
        "password",
        "pat",
        "secret",
        "secrets",
        "session",
        "token",
        "tokens",
    }
)
"""Words that make a key secret-looking (``auth``, ``pat`` and ``session`` only as whole words)."""
_SECRET_PARTS: Final = (
    "accesskey",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "passw",
    "privatekey",
    "secret",
    "token",
)
"""Long enough to refuse wherever they appear in a key (``mytoken``, ``clientsecret``)."""
_QUERY_SECRET_WORDS: Final = frozenset({"sig", "signature"})
"""Words of a URL query parameter's name that carry a secret (besides the secret-looking keys)."""
_CAMEL_RE: Final = re.compile(r"([a-z0-9])([A-Z])")
_SECRET_VALUE_RE: Final = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\bBearer\s+[A-Za-z0-9._~+/=-]{16,})"
)
_BASIC_RE: Final = re.compile(r"\bBasic\s+([A-Za-z0-9+/]{8,}={0,2})(?![A-Za-z0-9+/=])")
"""An HTTP basic credential (checked for base64's mixed case, so prose passes)."""
_URL_USERINFO_RE: Final = re.compile(r"://[^/@\s:?#]+:[^/@\s?#]+@")
"""A URL with a password in it (``https://user:pass@host``)."""
_QUERY_NAME_RE: Final = re.compile(r"[?&;#]([^=&;#?\s]+)=")
SECRET_REFUSAL: Final = (
    "source secrets are never part of the definition (Tier 0): an owner sets them "
    "write-only on the source, and the server keeps them"
)


def _slug(value: str) -> str:
    if not _SLUG_RE.match(value):
        raise ValueError("an id is a short slug: a-z first, then a-z, 0-9, _ or - (≤ 32)")
    return value


def _alias(value: str | None) -> str | None:
    if value is not None and not _ALIAS_RE.match(value):
        raise ValueError("a model alias such as 'sonnet' or 'opus', never a model id")
    return value


def _repo(value: str) -> str:
    if not _REPO_RE.match(value):
        raise ValueError("a repository is owner/name")
    return value


def _branch(value: str) -> str:
    if not BRANCH_RE.match(value):
        raise ValueError("a branch name such as main or release/2 (no wildcards)")
    return value


def _host(value: str | None) -> str | None:
    if value is not None and not _HOST_RE.match(value):
        raise ValueError("a domain such as previews.example.org (no scheme or path)")
    return value


def _link(value: str) -> str:
    if secret_text(value):
        raise ValueError("a link never carries a secret (Tier 0): no password or token in it")
    return value


Slug = Annotated[str, AfterValidator(_slug)]
Alias = Annotated[str | None, AfterValidator(_alias)]
Repo = Annotated[str, AfterValidator(_repo)]
Branch = Annotated[str, AfterValidator(_branch)]
Host = Annotated[str | None, AfterValidator(_host)]


def _strip(max_length: int, min_length: int = 0) -> StringConstraints:
    return StringConstraints(strip_whitespace=True, min_length=min_length, max_length=max_length)


Label = Annotated[str, _strip(80, 1)]
Line = Annotated[str, _strip(200, 1)]
Glob = Annotated[str, _strip(200, 1)]
Event = Annotated[str, _strip(120, 1)]
Statement = Annotated[str, _strip(280, 1)]
Url = Annotated[str, _strip(500, 1), AfterValidator(_link)]
Short = Annotated[str, _strip(80)]
Words = Annotated[str, _strip(500)]
Brief = Annotated[str, _strip(BRIEF_MAX)]


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _unique_ids(items: list[Any], what: str) -> list[Any]:
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise ValueError(f"duplicate {what} id {item.id}")
        seen.add(item.id)
    return items


# ── product ────────────────────────────────────────────────────


class SegmentDef(_Block):
    """A kind of user and how a source can recognise one."""

    id: Slug
    label: Label
    rule: Words = ""


class AreaDef(_Block):
    """A product area: its code paths (globs) and the events that measure it."""

    id: Slug
    label: Label
    paths: list[Glob] = Field(default_factory=list, max_length=20)
    events: list[Event] = Field(default_factory=list, max_length=20)


class CompetitorDef(_Block):
    name: Label
    changelog: Url | None = None


class FlagsDef(_Block):
    """How the product ships behind a flag, in words (``provider`` ``none`` when it has none)."""

    provider: Annotated[str, _strip(64)] | None = None
    convention: Words | None = None


class AutoMergeTargetDef(_Block):
    """One repo and base branch whose ready factory PRs the server merges (names only)."""

    repo: Repo
    base: Branch


class DeliveryDef(_Block):
    """
    How work reaches users.

    :param merge_policy: Who merges pull requests, in words. Agents never
        merge (Tier 0), whatever this says.
    :param auto_merge: Repo and base pairs whose factory PRs the server
        merges once ready and green (:mod:`.auto_merge`). Owners alone set
        it; an agent never proposes it (:data:`AGENT_NEVER`).
    :param release: What counts as released: ``merged``, ``github_release``,
        ``github_deployment:<env>`` or ``manual``.
    """

    merge_policy: Annotated[str, _strip(300)] | None = None
    auto_merge: list[AutoMergeTargetDef] = Field(default_factory=list, max_length=10)
    protected_repos: list[Repo] = Field(default_factory=list, max_length=20)
    demo_paths: list[Glob] = Field(default_factory=list, max_length=30)
    preview_domain: Host = None
    release: str = "merged"
    flags: FlagsDef = Field(default_factory=FlagsDef)

    @field_validator("release")
    @classmethod
    def _release(cls, value: str) -> str:
        if not _RELEASE_RE.match(value):
            raise ValueError(
                "release is merged, github_release, github_deployment:<env> or manual"
            )
        return value

    @field_validator("auto_merge")
    @classmethod
    def _targets(cls, items: list[AutoMergeTargetDef]) -> list[AutoMergeTargetDef]:
        seen: set[tuple[str, str]] = set()
        for item in items:
            key = (item.repo.lower(), item.base)
            if key in seen:
                raise ValueError(f"auto_merge lists {item.repo}#{item.base} twice")
            seen.add(key)
        return items


class ProductDef(_Block):
    """What the product is (drafted by an agent as a proposal, corrected by owners)."""

    name: Short | None = None
    kind: Short | None = None
    summary: Annotated[str, _strip(2000)] | None = None
    links: dict[Slug, Url] = Field(default_factory=dict, max_length=12)
    segments: list[SegmentDef] = Field(default_factory=list, max_length=20)
    areas: list[AreaDef] = Field(default_factory=list, max_length=50)
    competitors: list[CompetitorDef] = Field(default_factory=list, max_length=20)
    delivery: DeliveryDef = Field(default_factory=DeliveryDef)

    @field_validator("segments", "areas")
    @classmethod
    def _ids(cls, items: list[Any]) -> list[Any]:
        return _unique_ids(items, "item")


# ── environment ────────────────────────────────────────────────

_K8S_NAME_RE: Final = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_HEALTH_RE: Final = re.compile(r"^/[A-Za-z0-9._~/%+-]{0,199}$")
_IMAGE_RE: Final = re.compile(r"^[a-z0-9.-]+(:[0-9]{1,5})?(/[a-z0-9._-]+){1,6}$")
# A literal name: the guards compare branches exactly, so a glob would protect nothing.
_PROTECTED_BRANCH_RE: Final = re.compile(r"^(?![-./])(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]{1,200}$")
_STORAGE_KEY_RE: Final = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
# The demo agent passes the storage as JSON in single quotes on a shell line.
_STORAGE_VALUE_RE: Final = re.compile(r"^[A-Za-z0-9_.:@{}/=+-]{0,200}$")
USER_ID: Final = "{user_id}"
"""Stands for the signed-in user's id in a demo storage value."""


def _k8s_name(value: str | None) -> str | None:
    if value is not None and not _K8S_NAME_RE.match(value):
        raise ValueError("a Kubernetes name: a-z, 0-9 and -, e.g. sample-api")
    return value


def _health(value: str | None) -> str | None:
    if value is not None and not _HEALTH_RE.match(value):
        raise ValueError("a path such as /ready (no host, query or spaces)")
    return value


def _image(value: str | None) -> str | None:
    if value is not None and not _IMAGE_RE.match(value):
        raise ValueError("an image without its tag, e.g. registry.example.com/sample-org/db")
    return value


def _branch(value: str) -> str:
    if not _PROTECTED_BRANCH_RE.match(value):
        raise ValueError("a branch name such as develop or release/2026 (no globs)")
    return value


def _storage_key(value: str) -> str:
    if not _STORAGE_KEY_RE.match(value):
        raise ValueError("a storage key: letters, digits and _ . : - (≤ 64)")
    return value


def _storage_value(value: str) -> str:
    if not _STORAGE_VALUE_RE.match(value):
        raise ValueError(
            "a storage value: letters, digits and _ . : @ { } / = + - (no spaces or quotes)"
        )
    return value


def _no_secret(value: str) -> str:
    if secret_text(value):
        raise ValueError("environment facts never carry a secret (Tier 0)")
    return value


K8sName = Annotated[str, AfterValidator(_k8s_name)]
HealthPath = Annotated[str | None, AfterValidator(_health)]
Fact = Annotated[str, _strip(200, 1), AfterValidator(_no_secret)]
Facts = Annotated[str, _strip(500), AfterValidator(_no_secret)]
StorageKey = Annotated[str, AfterValidator(_storage_key)]
StorageValue = Annotated[
    str, _strip(200), AfterValidator(_storage_value), AfterValidator(_no_secret)
]


class ServiceDef(_Block):
    """
    One service of the env.

    :param id: Its key in the env's ``urls`` (``env.json``, ``preview.json``), e.g. ``api``.
    :param name: Its Deployment and Service in the env namespace, e.g. ``sample-api``.
    :param health: The path that answers 200 once it is up, e.g. ``/ready``.
    """

    id: Slug
    name: K8sName
    port: int = Field(ge=1, le=65535)
    health: HealthPath = None


class DemoAuthDef(_Block):
    """
    How the app keeps a demo's synthetic session in the browser.

    :param storage: ``localStorage`` entries set after the synthetic login;
        ``{user_id}`` in a value stands for the signed-in user's id. Values
        are shell-safe: the demo agent passes them in single quotes.
    :param clear: ``localStorage`` keys removed before them.
    """

    storage: dict[StorageKey, StorageValue] = Field(default_factory=dict, max_length=12)
    clear: list[StorageKey] = Field(default_factory=list, max_length=12)


class EnvironmentDef(_Block):
    """
    How the product's dev env and previews run: the facts env_keeper, the
    demo agent, ICs and PLs read instead of bundle text.

    :param repo: The repo whose projects run this env, e.g. ``sample-org/sample-app``
        (``None``: every project's repo).
    :param summary: How the dev env starts, in words.
    :param tooling: Where the env tooling lives (since when the base has it, known fixes).
    :param notes: Facts env_keeper reads before it fixes a failed env.
    :param conventions: Repo conventions every builder follows (on every task card).
    :param protected_branches: Branches no agent pushes, besides the guards'
        defaults: literal names, since the guards compare them exactly.
    """

    repo: Repo | None = None
    summary: Facts | None = None
    services: list[ServiceDef] = Field(default_factory=list, max_length=12)
    tooling: Facts | None = None
    registry: Host = None
    base_image: Annotated[str | None, AfterValidator(_image)] = None
    pull_secret: Annotated[str | None, AfterValidator(_k8s_name)] = None
    notes: list[Fact] = Field(default_factory=list, max_length=20)
    conventions: list[Fact] = Field(default_factory=list, max_length=10)
    protected_branches: list[Annotated[str, AfterValidator(_branch)]] = Field(
        default_factory=list, max_length=20
    )
    demo: DemoAuthDef = Field(default_factory=DemoAuthDef)

    @field_validator("services")
    @classmethod
    def _ids(cls, items: list[ServiceDef]) -> list[ServiceDef]:
        return _unique_ids(items, "service")


# ── strategy ───────────────────────────────────────────────────


class GoalDef(_Block):
    """
    One goal, ``G-n``.

    :param metric: A metric binding's name; ``None`` until a source can measure it.
    :param weight: Its weight against the other goals (the heaviest counts as 1).
    """

    id: str
    statement: Statement
    metric: Annotated[str, _strip(120)] | None = None
    baseline: float | None = None
    target: float | None = None
    by: date | None = None
    weight: float = Field(1.0, ge=0, le=10)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not GOAL_ID_RE.match(value):
            raise ValueError("a goal id is G-<n>")
        return value


class MixDef(_Block):
    """Where capacity goes, in percent of active projects (adds up to 100)."""

    outcome: int = Field(60, ge=0, le=100)
    tests: int = Field(20, ge=0, le=100)
    enablers: int = Field(15, ge=0, le=100)
    hunches: int = Field(5, ge=0, le=100)

    @model_validator(mode="after")
    def _sum(self) -> MixDef:
        total = self.outcome + self.tests + self.enablers + self.hunches
        if total != 100:
            raise ValueError(f"the mix adds up to 100, not {total}")
        return self


class StrategyDef(_Block):
    """What the factory is trying to achieve. Never on trial: judged on outcomes."""

    goals: list[GoalDef] = Field(default_factory=list, max_length=10)
    guardrails: list[Line] = Field(default_factory=list, max_length=10)
    non_goals: list[Line] = Field(default_factory=list, max_length=20)
    focus_segments: list[Slug] = Field(default_factory=list, max_length=10)
    mix: MixDef = Field(default_factory=MixDef)

    @field_validator("goals")
    @classmethod
    def _ids(cls, items: list[GoalDef]) -> list[GoalDef]:
        return _unique_ids(items, "goal")

    @field_validator("focus_segments")
    @classmethod
    def _focus(cls, items: list[str]) -> list[str]:
        if len(set(items)) != len(items):
            raise ValueError("a focus segment is listed twice")
        return items


# ── discovery ──────────────────────────────────────────────────

SourceKind = Literal[
    "repo",
    "team",
    "web",
    "analytics",
    "observability",
    "chat",
    "tracker",
    "reviews",
    "feedback",
    "flags",
    "mcp",
]
PrivacyLevel = Literal["public", "internal", "personal", "sensitive"]
RedactKind = Literal["email", "phone", "token", "ip", "url_secret", "card", "name", "address"]
REDACT_FLOOR: Final[tuple[RedactKind, ...]] = (
    "email",
    "phone",
    "token",
    "ip",
    "url_secret",
    "card",
)
"""Redactions every source applies (Tier 0); a source may add more."""
PRIVACY_RANK: Final = {"public": 0, "internal": 1, "personal": 2, "sensitive": 3}
SOURCE_PRIVACY: Final[dict[str, PrivacyLevel]] = {
    "repo": "internal",
    "team": "internal",
    "web": "public",
    "analytics": "internal",
    "observability": "internal",
    "chat": "personal",
    "tracker": "internal",
    "reviews": "public",
    "feedback": "personal",
    "flags": "internal",
    "mcp": "internal",
}
"""A source kind's privacy class when the source does not set one (mechanism §4.1)."""
KEEP_DAYS: Final[dict[str, int]] = {
    "public": 365,
    "internal": 180,
    "personal": 90,
    "sensitive": 90,
}
"""Retention by privacy class when a source does not set ``keep_days`` (mechanism §4.4)."""
CONSENT: Final[dict[str, str]] = {
    "public": "public",
    "internal": "internal",
    "personal": "opt_in",
    "sensitive": "opt_in",
}
"""The consent basis by privacy class when a source does not state one."""
EVIDENCE_LEVELS: Final = ("hunch", "weak", "moderate", "strong", "proven")


def secret_key(key: str) -> bool:
    """
    Whether a params key looks like it holds a secret: a secret word in it
    (camelCase and separators split, so ``accessToken``, ``client-secret``)
    or a long secret word anywhere in it (``mytoken``).
    """
    lowered = _CAMEL_RE.sub(r"\1_\2", key).lower()
    words = set(re.split(r"[^a-z0-9]+", lowered))
    flat = re.sub(r"[^a-z0-9]+", "", lowered)
    return bool(words & _SECRET_WORDS) or any(part in flat for part in _SECRET_PARTS)


def _basic(text: str) -> bool:
    for match in _BASIC_RE.finditer(text):
        token = match.group(1)
        if token.endswith("=") or (
            any(c.islower() for c in token)
            and any(c.isupper() or c.isdigit() or c in "+/" for c in token[1:])
        ):
            return True
    return False


def secret_text(text: str) -> bool:
    """
    Whether *text* carries a secret: a token-shaped value, a basic or bearer
    credential, a URL with a password, or a URL query parameter named like one.
    """
    if _SECRET_VALUE_RE.search(text) or _URL_USERINFO_RE.search(text) or _basic(text):
        return True
    for match in _QUERY_NAME_RE.finditer(text):
        name = _CAMEL_RE.sub(r"\1_\2", match.group(1)).lower()
        words = set(re.split(r"[^a-z0-9]+", name))
        if secret_key(name) or name == "key" or words & _QUERY_SECRET_WORDS:
            return True
    return False


def _no_secrets(value: Any, where: str = "params") -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if secret_key(str(key)):
                raise ValueError(f"{where}.{key}: {SECRET_REFUSAL}")
            _no_secrets(inner, f"{where}.{key}")
    elif isinstance(value, list):
        for i, inner in enumerate(value):
            _no_secrets(inner, f"{where}[{i}]")
    elif isinstance(value, str) and secret_text(value):
        raise ValueError(f"{where}: {SECRET_REFUSAL}")


def has_secret(value: Any) -> bool:
    """Whether *value* (a source or its params) carries a secret-looking key or value."""
    try:
        _no_secrets(value)
    except ValueError:
        return True
    return False


class SeatDef(_Block):
    """Scout's seat: ``model`` overrides the bundle's default alias."""

    model: Alias = None


class PrivacyDef(_Block):
    """
    A source's privacy (``level`` is the privacy class).

    :param redact: Kinds redacted before storage; the Tier-0 floor is always included.
    """

    level: PrivacyLevel = "public"
    redact: list[RedactKind] = Field(default_factory=lambda: list(REDACT_FLOOR), max_length=16)
    quote_max: int = Field(280, ge=0, le=1000)
    keep_days: int = Field(365, ge=1, le=3650)

    @field_validator("redact")
    @classmethod
    def _floor(cls, kinds: list[RedactKind]) -> list[RedactKind]:
        extra = [k for k in kinds if k not in REDACT_FLOOR]
        return [*REDACT_FLOOR, *dict.fromkeys(extra)]


SourceRole = Literal["scout", "lead"]


def _scout_only() -> list[SourceRole]:
    return ["scout"]


class SourceBudgetDef(_Block):
    calls_per_day: int = Field(100, ge=0, le=10000)


class SourceDef(_Block):
    """
    One evidence source. Never holds a secret (Tier 0).

    What it leaves out comes from its kind (see :meth:`_kind_defaults`): a
    ``chat`` source is ``personal``, kept 90 days, on ``opt_in`` consent and
    paused; an ``analytics`` one ``internal`` for 180 days; a ``web`` one
    ``public`` for 365.

    :param roles: Who may query it through the gateway.
    :param interval_min: Minutes between syncs; ``0`` is query-only.
    :param state: ``trial`` (its first 14 days), ``active``, ``paused``, or
        ``pending``: listed but not approved by an owner yet (the discovery
        starter leaves a source the owner did not tick there). Nothing reads a
        paused or pending source; approving one (pending to trial) is an owner's.
    """

    id: Slug
    label: Label
    kind: SourceKind
    params: dict[str, Any] = Field(default_factory=dict)
    roles: list[SourceRole] = Field(default_factory=_scout_only, max_length=2)
    privacy: PrivacyDef = Field(default_factory=PrivacyDef)
    consent: Literal["public", "terms", "opt_in", "internal"] = "public"
    budget: SourceBudgetDef = Field(default_factory=SourceBudgetDef)
    interval_min: int = Field(60, ge=0, le=10080)
    state: Literal["trial", "active", "paused", "pending"] = "trial"

    @model_validator(mode="before")
    @classmethod
    def _kind_defaults(cls, data: Any) -> Any:
        """
        Fill what a source leaves out from its kind: the privacy class
        (:data:`SOURCE_PRIVACY`), then retention (:data:`KEEP_DAYS`) and the
        consent basis (:data:`CONSENT`) from that class. A ``personal`` or
        ``sensitive`` source starts paused (it may not run yet).
        """
        if not isinstance(data, Mapping) or data.get("kind") not in SOURCE_PRIVACY:
            return data
        out = dict(data)
        privacy: Any = out.get("privacy")
        if isinstance(privacy, BaseModel):
            privacy = privacy.model_dump(exclude_unset=True)
        if privacy is None:
            privacy = {}
        if not isinstance(privacy, Mapping):
            return out  # Validation says what is wrong with it.
        privacy = dict(privacy)
        if privacy.get("level") is None:
            privacy["level"] = SOURCE_PRIVACY[str(out["kind"])]
        level = privacy["level"]
        if privacy.get("keep_days") is None and level in KEEP_DAYS:
            privacy["keep_days"] = KEEP_DAYS[level]
        out["privacy"] = privacy
        if out.get("consent") is None and level in CONSENT:
            out["consent"] = CONSENT[level]
        if out.get("state") is None and PRIVACY_RANK.get(level, 0) >= PRIVACY_RANK["personal"]:
            out["state"] = "paused"
        return out

    @field_validator("params")
    @classmethod
    def _params(cls, params: dict[str, Any]) -> dict[str, Any]:
        _no_secrets(params)
        if len(json.dumps(params, separators=(",", ":"), default=str)) > SOURCE_PARAMS_MAX:
            raise ValueError(f"params is at most {SOURCE_PARAMS_MAX} characters as JSON")
        return params

    @model_validator(mode="after")
    def _personal_stays_off(self) -> SourceDef:
        if PRIVACY_RANK[self.privacy.level] >= PRIVACY_RANK["personal"] and self.state not in (
            "paused",
            "pending",
        ):
            raise ValueError(
                f"source {self.id}: a {self.privacy.level} source stays paused until "
                "the server can detect names in it"
            )
        if self.interval_min and self.interval_min < 5:
            raise ValueError("interval_min is 0 (query only) or at least 5")
        return self


class LadderDef(_Block):
    """The evidence meter's levels."""

    hunch: float = 0.1
    weak: float = 0.25
    moderate: float = 0.5
    strong: float = 0.8
    proven: float = 1.0

    @model_validator(mode="after")
    def _ascending(self) -> LadderDef:
        steps = [self.hunch, self.weak, self.moderate, self.strong, self.proven]
        if any(b < a for a, b in itertools.pairwise(steps)):
            raise ValueError("the ladder rises from hunch to proven")
        return self


class ReachRatioDef(_Block):
    low: float = 10
    high: float = 40

    @model_validator(mode="after")
    def _order(self) -> ReachRatioDef:
        if self.high < self.low:
            raise ValueError("reach_ratio.high is at least reach_ratio.low")
        return self


class IntensityDef(_Block):
    blocker: float = 1.0
    workaround: float = 0.6
    annoyance: float = 0.3
    nice: float = 0.1


class BandsDef(_Block):
    now: float = 0.20
    next: float = 0.10

    @model_validator(mode="after")
    def _order(self) -> BandsDef:
        if self.next > self.now:
            raise ValueError("bands.next is at most bands.now")
        return self


class MapDef(_Block):
    value_high: float = 0.4
    confidence_high: float = 0.5


class RubricDef(_Block):
    """The numbers of the opportunity rank; its formula is code (Tier 0)."""

    ladder: LadderDef = Field(default_factory=LadderDef)
    moderate_min_accounts: int = 5
    moderate_min_source_types: int = 2
    window_days: int = 90
    per_account_cap: int = 1
    decay_half_life_days: int = 60
    counter_share_down: float = 0.33
    reach_ratio: ReachRatioDef = Field(default_factory=ReachRatioDef)
    reach_ref: int = 1000
    intensity: IntensityDef = Field(default_factory=IntensityDef)
    off_focus_factor: float = 0.5
    bands: BandsDef = Field(default_factory=BandsDef)
    map: MapDef = Field(default_factory=MapDef)
    human_minute_usd: float = 2.0


class ThresholdsDef(_Block):
    validated_min_level: Literal["hunch", "weak", "moderate", "strong", "proven"] = "moderate"
    min_accounts: int = 2
    bets_per_week: int = 3
    max_open_bets: int = 3


class DiscoveryBudgetsDef(_Block):
    usd_per_day: float = 10.0
    per_ask: float = 2.0


class DiscoveryCadenceDef(_Block):
    """Hours between Scout's sweeps and syntheses (``0`` turns one off)."""

    sweep_h: int = 24
    synthesis_h: int = 168


class DiscoveryAutonomyDef(_Block):
    """Who approves a bet Atlas files from an opportunity (the projects autonomy values)."""

    bets: Literal["confirm", "requested", "grace"] = "confirm"


class DiscoveryDef(_Block):
    """How the factory finds out what users need. Off until an owner turns it on."""

    enabled: bool = False
    seat: SeatDef = Field(default_factory=SeatDef)
    sources: list[SourceDef] = Field(default_factory=list, max_length=30)
    rubric: RubricDef = Field(default_factory=RubricDef)
    thresholds: ThresholdsDef = Field(default_factory=ThresholdsDef)
    budgets: DiscoveryBudgetsDef = Field(default_factory=DiscoveryBudgetsDef)
    cadence: DiscoveryCadenceDef = Field(default_factory=DiscoveryCadenceDef)
    autonomy: DiscoveryAutonomyDef = Field(default_factory=DiscoveryAutonomyDef)

    @field_validator("sources")
    @classmethod
    def _ids(cls, items: list[SourceDef]) -> list[SourceDef]:
        return _unique_ids(items, "source")


# ── briefs and role defaults ───────────────────────────────────


class BriefsDef(_Block):
    """This factory's brief per role: added to the built-in prompt, never replacing it."""

    atlas: Brief = ""
    sentinel: Brief = ""
    scout: Brief = ""
    project_lead: Brief = ""
    ic: Brief = ""
    reviewer: Brief = ""
    env_keeper: Brief = ""
    demo: Brief = ""
    planner: Brief = ""


Vendor = Literal["claude", "codex"]
VENDORS_BY_ROLE: Final[dict[str, tuple[str, ...]]] = {
    "project_lead": ("claude",),
    "ic": ("claude", "codex"),
    "reviewer": ("claude", "codex"),
    "env_keeper": ("claude",),
    "demo": ("claude",),
    "planner": ("claude",),
}
"""The vendors each role's bundle can run on today."""


class RoleDefaultDef(_Block):
    """A role's default vendor and model alias (``None``: the bundle's own)."""

    vendor: Vendor | None = None
    model: Alias = None


class RoleDefaultsDef(_Block):
    project_lead: RoleDefaultDef = Field(default_factory=RoleDefaultDef)
    ic: RoleDefaultDef = Field(default_factory=RoleDefaultDef)
    reviewer: RoleDefaultDef = Field(default_factory=RoleDefaultDef)
    env_keeper: RoleDefaultDef = Field(default_factory=RoleDefaultDef)
    demo: RoleDefaultDef = Field(default_factory=RoleDefaultDef)
    planner: RoleDefaultDef = Field(default_factory=RoleDefaultDef)

    @model_validator(mode="after")
    def _vendors(self) -> RoleDefaultsDef:
        for role, allowed in VENDORS_BY_ROLE.items():
            vendor = getattr(self, role).vendor
            if vendor is not None and vendor not in allowed:
                raise ValueError(f"role_defaults.{role}: runs on {', '.join(allowed)} today")
        return self


# ── structure: the cockpit's levels ────────────────────────────

PRODUCT_TREE: Final = "product"
FACTORY_TREE: Final = "factory"
PRODUCT_LEVELS: Final = ("product", "goal", "need", "feature", "project", "task", "change", "run")
"""The product tree, root first. Keys and order are code; names and visibility are data."""
FACTORY_LEVELS: Final = ("fx", "area", "item")
"""The factory tree (rules, team, environments, health, the definition), root first."""
TREES: Final[dict[str, tuple[str, ...]]] = {
    PRODUCT_TREE: PRODUCT_LEVELS,
    FACTORY_TREE: FACTORY_LEVELS,
}
LEVEL_KINDS: Final = ("study",)
"""Kinds that answer, steer and are changed apart from their level (a study sits on the
feature level): their responders, verbs and authority are their own."""
TILES_MAX_BOUNDS: Final = (1, 60)

Show = Literal["always", "when_any", "when_many", "inline"]
"""``when_any``: hidden while no node is on it; ``when_many``: while at most one is;
``inline``: drawn inside its parent's row, never as a column of its own."""
Seat = Literal["owner", "operator", "atlas", "scout", "sentinel", "lead", "server"]
Approval = Literal["none", "autonomy", "owner", "budget", "ledger"]
"""How a structural change on a level is approved: at once (Undo), by the subtree's
autonomy, by an owner, within a research budget, or by the discovery ledger's rules."""
Responder = Literal["atlas", "scout", "sentinel", "lead"]
Verb = Literal[
    "talk", "add", "start", "research", "team", "auto", "pause", "move", "name", "rename"
]
LevelName = Annotated[str, _strip(40, 1)]
ColumnName = Annotated[str, _strip(40, 1)]


def _unique_seats(items: list[str]) -> list[str]:
    if len(set(items)) != len(items):
        raise ValueError("a seat is listed twice")
    return items


Seats = Annotated[list[Seat], AfterValidator(_unique_seats), Field(max_length=7)]


class AuthorityDef(_Block):
    """
    Who changes the nodes of one level (Tier 0 and autonomy still apply on top).

    :param create: Seats that add, split, order and close nodes here.
    :param move: Seats that move a node to another parent (one level up); empty:
        nodes here never move.
    :param approval: How such a change is approved (:data:`Approval`).
    """

    create: Seats = Field(default_factory=list)
    move: Seats = Field(default_factory=list)
    approval: Approval = "owner"


class LevelDef(_Block):
    """
    One level: what people call it and when it shows.

    :param debug: Shown only when someone descends into it (a run).
    """

    name: LevelName
    plural: LevelName
    show: Show = "always"
    debug: bool = False
    authority: AuthorityDef = Field(default_factory=AuthorityDef)


def _level(
    name: str,
    plural: str,
    *,
    show: Show = "always",
    debug: bool = False,
    create: tuple[Seat, ...] = (),
    move: tuple[Seat, ...] = (),
    approval: Approval = "owner",
) -> Any:
    def make() -> LevelDef:
        return LevelDef(
            name=name,
            plural=plural,
            show=show,
            debug=debug,
            authority=AuthorityDef(create=list(create), move=list(move), approval=approval),
        )

    return Field(default_factory=make)


class LevelsDef(_Block):
    """Every level of both trees (:data:`TREES`), each renamed or hidden on its own."""

    product: LevelDef = _level("Product", "Products")
    goal: LevelDef = _level("Goal", "Goals", show="when_any", create=("owner",))
    need: LevelDef = _level(
        "Need", "Needs", create=("scout", "operator", "owner"), approval="ledger"
    )
    feature: LevelDef = _level("Feature", "Features", create=("atlas", "owner"), move=("atlas",))
    project: LevelDef = _level(
        "Project",
        "Projects",
        create=("atlas", "lead", "operator", "owner"),
        move=("atlas",),
        approval="autonomy",
    )
    task: LevelDef = _level("Task", "Tasks", create=("lead",), move=("atlas",), approval="none")
    change: LevelDef = _level(
        "Change", "Changes", show="inline", create=("lead",), approval="none"
    )
    run: LevelDef = _level("Run", "Runs", debug=True, create=("server",), approval="none")
    fx: LevelDef = _level("Factory", "Factories")
    area: LevelDef = _level("Area", "Areas")
    item: LevelDef = _level("Item", "Items", create=("atlas", "sentinel", "owner"))


class HomeDef(_Block):
    """The level each tree's home page shows (grouped under the level above it)."""

    product: Literal["goal", "need", "feature", "project", "task"] = "feature"
    factory: Literal["area", "item"] = "area"


class RespondersDef(_Block):
    """Who answers a node's thread, per level (and per :data:`LEVEL_KINDS`); ``None``: nobody."""

    product: Responder | None = "atlas"
    goal: Responder | None = "atlas"
    need: Responder | None = "scout"
    feature: Responder | None = "atlas"
    study: Responder | None = "scout"
    project: Responder | None = "lead"
    task: Responder | None = "lead"
    change: Responder | None = "lead"
    run: Responder | None = None
    fx: Responder | None = "sentinel"
    area: Responder | None = "sentinel"
    item: Responder | None = "sentinel"


def _unique_verbs(items: list[str]) -> list[str]:
    if len(set(items)) != len(items):
        raise ValueError("a verb is listed twice")
    return items


Verbs = Annotated[list[Verb], AfterValidator(_unique_verbs), Field(max_length=10)]


def _verbs(*names: Verb) -> Any:
    return Field(default_factory=lambda: list(names))


class VerbsDef(_Block):
    """The verbs a node may offer, per level (whether one is enabled is the server's call)."""

    product: Verbs = _verbs("talk", "add")
    goal: Verbs = _verbs("talk", "add")
    need: Verbs = _verbs("talk", "add", "research", "name")
    feature: Verbs = _verbs("talk", "add", "auto", "pause", "move", "name")
    study: Verbs = _verbs("talk", "research")
    project: Verbs = _verbs("talk", "add", "start", "team", "auto", "pause", "move")
    task: Verbs = _verbs("talk", "pause", "move")
    change: Verbs = _verbs("talk")
    run: Verbs = _verbs()
    fx: Verbs = _verbs("talk")
    area: Verbs = _verbs("talk")
    item: Verbs = _verbs("talk", "rename")


KIND_IS_LEVEL: Final[dict[str, str]] = {
    "product": "product",
    "goal": "goal",
    "feature": "feature",
    "project": "project",
    "task": "task",
    "run": "run",
    "factory": "fx",
    "area": "area",
    "item": "item",
}
"""Kinds of node called what their level is called (kind -> level key): renaming the level
renames them, so :class:`KindsDef` names only the other kinds."""


class KindsDef(_Block):
    """
    What each kind of node is called, for the kinds not named after their level
    (:data:`KIND_IS_LEVEL`; the kinds themselves come from the adapters).
    """

    opportunity: LevelName = "Opportunity"
    ask: LevelName = "Ask"
    question: LevelName = "Question"
    proposal: LevelName = "From a proposal"
    study: LevelName = "Study"
    adhoc: LevelName = "Task (no ticket)"
    pr: LevelName = "Pull request"
    commit: LevelName = "Local commit"
    rule: LevelName = "Rule"
    agent: LevelName = "Agent"
    env: LevelName = "Environment"
    alarm: LevelName = "Alarm"
    decision: LevelName = "Decision"
    definition: LevelName = "Definition"


class KindAuthorityDef(_Block):
    """
    Who changes the nodes of a kind that is changed apart from its level
    (:data:`LEVEL_KINDS`): a study sits on the feature level, but Scout starts one
    within the need's research budget.
    """

    study: AuthorityDef = Field(
        default_factory=lambda: AuthorityDef(create=["scout"], approval="budget")
    )


def _unique_columns(items: list[str]) -> list[str]:
    if len({c.casefold() for c in items}) != len(items):
        raise ValueError("a column name is listed twice")
    return items


class OrderDef(_Block):
    """
    Column titles for siblings ordered by their dependencies, and for held work.

    :param cols: First, second, … column; deeper orderings reuse the last title.
    """

    cols: Annotated[
        list[ColumnName], AfterValidator(_unique_columns), Field(min_length=1, max_length=6)
    ] = Field(default_factory=lambda: ["First", "Then", "After that", "Last"])
    later: ColumnName = "Later (held)"


class StructureDef(_Block):
    """
    The cockpit's levels: names, visibility, who answers, verbs and authority per level.

    The level keys, their order and the adapters that fill them are code
    (:data:`TREES`); everything people read is data. Only owners change it,
    and no agent proposes it (:data:`AGENT_NEVER`).

    :param kinds: Names of the kinds not named after their level (the others
        read their level's name: :data:`KIND_IS_LEVEL`).
    :param kind_authority: Who changes the kinds changed apart from their
        level (:data:`LEVEL_KINDS`).
    :param tiles_max: Children shown as tiles up to this many; rows past it.
    :param fold_single_child: ``implicit_only``: an implicit parent with one
        child shows as that child; ``never``: every parent shows.
    :param node_header: Which Room deliveries carry the header of the node a
        thread is about (path, why, status, roll-up, open asks):
        ``node_threads``, only cockpit v3's per-node threads (``need:``,
        ``feature:``, ``study:``, ``task:``), so the threads v2 shares
        (``#factory``, a project's, a PR's) keep their envelopes; ``all``, those
        too; ``off``, none.
    """

    levels: LevelsDef = Field(default_factory=LevelsDef)
    home: HomeDef = Field(default_factory=HomeDef)
    responders: RespondersDef = Field(default_factory=RespondersDef)
    verbs: VerbsDef = Field(default_factory=VerbsDef)
    kinds: KindsDef = Field(default_factory=KindsDef)
    kind_authority: KindAuthorityDef = Field(default_factory=KindAuthorityDef)
    order: OrderDef = Field(default_factory=OrderDef)
    tiles_max: int = Field(12, ge=TILES_MAX_BOUNDS[0], le=TILES_MAX_BOUNDS[1])
    fold_single_child: Literal["implicit_only", "never"] = "implicit_only"
    node_header: Literal["node_threads", "all", "off"] = "node_threads"

    @model_validator(mode="after")
    def _distinct_names(self) -> StructureDef:
        for tree, keys in TREES.items():
            for attr in ("name", "plural"):
                seen: dict[str, str] = {}
                for key in keys:
                    said = getattr(getattr(self.levels, key), attr).casefold()
                    if said in seen:
                        raise ValueError(
                            f"levels.{key}.{attr}: the {tree} tree already calls {seen[said]} that"
                        )
                    seen[said] = key
        return self


LEGACY_MODELS: Final[dict[str, str]] = {
    "models.ic": "role_defaults.ic.model",
    "models.reviewer": "role_defaults.reviewer.model",
    "models.planner": "role_defaults.planner.model",
}
"""The unread ``models.*`` settings and the ``role_defaults`` paths they moved to."""


AUTO_MERGE_KEY: Final = "auto_merge_targets"
"""Where a version stores ``product.delivery.auto_merge``: its own top-level key, so a
server from before the targets drops only them (it still reads the product block)."""


LEVEL_KEY: Final = "autonomy_level"
"""Where a version stores a set ``autonomy.level``: its own top-level key, so a server
from before the level drops only it on a rollback (it reads ``careful``, the safe
direction) and still reads ``autonomy.projects`` as the owners set it."""


def upgrade(stored: dict[str, Any]) -> dict[str, Any]:
    """
    :returns: *stored* with legacy blocks moved: an owner-set ``models.<kind>``
        becomes ``role_defaults.<kind>.model`` unless that is already set, the
        stored :data:`AUTO_MERGE_KEY` goes back into ``product.delivery`` and
        :data:`LEVEL_KEY` back into ``autonomy.level``.
    """
    return _upgrade_models(_lower_level(_lower_targets(stored)))


def _lower_level(stored: dict[str, Any]) -> dict[str, Any]:
    if LEVEL_KEY not in stored:
        return stored
    out = {k: v for k, v in stored.items() if k != LEVEL_KEY}
    found = out.get("autonomy")
    out["autonomy"] = {**(found if isinstance(found, Mapping) else {}), "level": stored[LEVEL_KEY]}
    return out


def _lift_level(stored: dict[str, Any]) -> dict[str, Any]:
    found = stored.get("autonomy")
    if not isinstance(found, Mapping) or "level" not in found:
        return stored
    out = dict(stored)
    out["autonomy"] = {k: v for k, v in found.items() if k != "level"}
    out[LEVEL_KEY] = found["level"]
    return out


def _lower_targets(stored: dict[str, Any]) -> dict[str, Any]:
    if AUTO_MERGE_KEY not in stored:
        return stored
    out = {k: v for k, v in stored.items() if k != AUTO_MERGE_KEY}
    targets = stored[AUTO_MERGE_KEY]
    product = dict(out.get("product") or {})
    delivery = dict(product.get("delivery") or {})
    delivery["auto_merge"] = targets
    product["delivery"] = delivery
    out["product"] = product
    return out


def _lift_targets(stored: dict[str, Any]) -> dict[str, Any]:
    product = stored.get("product")
    delivery = product.get("delivery") if isinstance(product, Mapping) else None
    if not isinstance(delivery, Mapping) or "auto_merge" not in delivery:
        return stored
    assert isinstance(product, Mapping)
    out = dict(stored)
    out["product"] = {
        **product,
        "delivery": {k: v for k, v in delivery.items() if k != "auto_merge"},
    }
    out[AUTO_MERGE_KEY] = delivery["auto_merge"]
    return out


def _upgrade_models(stored: dict[str, Any]) -> dict[str, Any]:
    models = stored.get("models")
    if "models" not in stored:
        return stored
    out = {k: v for k, v in stored.items() if k != "models"}
    if not isinstance(models, dict):
        return out
    defaults = dict(out.get("role_defaults") or {})
    for kind in ("ic", "reviewer", "planner"):
        alias = models.get(kind)
        if not isinstance(alias, str) or not alias:
            continue
        role = dict(defaults.get(kind) or {})
        if not role.get("model"):
            role["model"] = alias
        defaults[kind] = role
    if defaults:
        out["role_defaults"] = defaults
    return out


LEGACY_KINDS: Final = ("ic", "reviewer", "planner")


def downgrade(stored: Mapping[str, Any]) -> dict[str, Any]:
    """
    :returns: *stored* with ``role_defaults`` written as the legacy ``models``
        block when all it sets is the model alias of ``ic``, ``reviewer`` or
        ``planner``, ``product.delivery.auto_merge`` under
        :data:`AUTO_MERGE_KEY` and ``autonomy.level`` under :data:`LEVEL_KEY`
        (:func:`upgrade` reads them back), so a server from before them still
        reads the version. Anything else stays as it is.
    """
    return _lift_level(_lift_targets(_downgrade_models(stored)))


def _downgrade_models(stored: Mapping[str, Any]) -> dict[str, Any]:
    defaults = stored.get("role_defaults")
    if not isinstance(defaults, Mapping):
        return dict(stored)
    empty = RoleDefaultDef().model_dump(mode="json")
    models: dict[str, Any] = {}
    for role, found in defaults.items():
        if found == empty:
            continue
        if (
            role in LEGACY_KINDS
            and isinstance(found, Mapping)
            and set(found) <= {"vendor", "model"}
            and found.get("vendor") is None
        ):
            models[role] = found.get("model")
            continue
        return dict(stored)
    out = {k: v for k, v in stored.items() if k != "role_defaults"}
    if any(v is not None for v in models.values()):
        out["models"] = {kind: models.get(kind) for kind in LEGACY_KINDS}
    return out


# ── Lists changed item by item ─────────────────────────────────


@dataclass(frozen=True)
class ListSpec:
    """
    A list of the doc whose items carry an ``id``.

    :param prefix: Set when the server numbers its items (``"G"`` gives ``G-1``…).
    :param seq: The doc's counter of numbers handed out (never reused).
    """

    path: str
    prefix: str | None = None
    seq: str | None = None

    @property
    def numbered(self) -> bool:
        return self.prefix is not None


LISTS: Final[dict[str, ListSpec]] = {
    spec.path: spec
    for spec in (
        ListSpec("product.segments"),
        ListSpec("product.areas"),
        ListSpec("strategy.goals", prefix="G", seq="goal_seq"),
        ListSpec("discovery.sources"),
        ListSpec("team.templates"),
    )
}
"""Lists changed by ``id``: ``add`` at the list, ``replace``/``remove`` at ``<list>.<id>``."""


def list_of(path: str) -> tuple[ListSpec, str | None, str | None] | None:
    """
    :returns: ``(spec, item id, field)`` when *path* is a list of :data:`LISTS`,
        one of its items (``strategy.goals.G-2``) or an item's field
        (``discovery.sources.forum.state``); else ``None``.
    """
    for list_path, spec in LISTS.items():
        if path == list_path:
            return spec, None, None
        if path.startswith(f"{list_path}."):
            rest = path[len(list_path) + 1 :].split(".")
            if len(rest) > 2 or not rest[0]:
                return None
            return spec, rest[0], rest[1] if len(rest) == 2 else None
    return None


def item_id_ok(spec: ListSpec, item_id: str) -> bool:
    """Whether *item_id* is a well-formed id for *spec*'s items."""
    if spec.numbered:
        return bool(re.fullmatch(rf"{spec.prefix}-[1-9][0-9]{{0,5}}", item_id))
    return bool(_SLUG_RE.match(item_id))


def seq_of(doc: dict[str, Any], spec: ListSpec, items: list[Any]) -> int:
    """:returns: The highest number *spec*'s list has used (its counter or an item id)."""
    numbers = [
        int(m.group(1))
        for item in items
        if isinstance(item, dict)
        and (m := re.fullmatch(rf"{spec.prefix}-([1-9][0-9]{{0,5}})", str(item.get("id"))))
    ]
    return max([int(doc.get(spec.seq or "") or 0), *numbers], default=0)


# ── Who may change what ────────────────────────────────────────

BLOCKS: Final = (
    "product",
    "environment",
    "strategy",
    "discovery",
    "briefs",
    "role_defaults",
    "structure",
)
"""The blocks of this module (agents may propose only some of their paths; none of
``environment``, whose facts reach shell commands, nor ``structure``)."""
NOT_TRIALABLE_REASON: Final = "judged on outcomes"
ENV_FACTS: Final = "facts, not a way of working"
HOW_WORK_SHOWS: Final = "how work is shown, not how it is done"
NEEDS_DISCOVERY_GUARDS: Final = "needs discovery guards"
TOO_FEW_PRS: Final = "too few merged PRs per team"
NO_PROJECT_FOLLOWS: Final = "no project follows a template"
NOT_TRIALABLE: Final[dict[str, str]] = {
    "product": NOT_TRIALABLE_REASON,
    "environment": ENV_FACTS,
    "strategy": NOT_TRIALABLE_REASON,
    "discovery": NEEDS_DISCOVERY_GUARDS,
    "briefs.scout": NEEDS_DISCOVERY_GUARDS,
    "team.projects": TOO_FEW_PRS,
    "team.templates": NO_PROJECT_FOLLOWS,
    "structure": HOW_WORK_SHOWS,
}
"""
Paths a trial would judge on the wrong metrics, and why: they apply as a version.

Strategy and the profile are judged on outcomes. Discovery and Scout's brief
are judged on discovery guards (quote failures, privacy events, accepted
bets, cost per accepted bet), which a server measures only while it runs
discovery; there, discovery's method (:data:`DISCOVERY_TRIALS`) trials on
them, and its other settings apply as a version. One project's team merges
about one PR a day, too few for a trial's n. A template reaches a project
only when it switches to it, so a template trial would measure nothing until
projects follow one.
"""
DISCOVERY_TRIALS: Final = ("discovery.rubric", "discovery.thresholds", "briefs.scout")
"""Discovery's method: on a server that runs discovery it trials on the discovery guards."""
DISCOVERY_SETTING: Final = "a setting, not discovery's method"
MIXED_GUARDS: Final = "discovery guards: split from other changes"
AGENT_PATHS: Final[dict[str, tuple[str, ...]]] = {
    "scout": ("product", "strategy", "discovery.sources"),
    "sentinel": ("discovery.rubric", "discovery.thresholds", "briefs.scout", "team"),
    "atlas": ("team",),
}
"""What each agent seat may propose in :data:`BLOCKS` and ``team`` (others: nothing)."""
AGENT_GATED: Final = (*BLOCKS, "team")
"""Blocks an agent proposes only by :data:`AGENT_PATHS` (teams: Atlas and Sentinel)."""
ALLOWLIST_SEATS: Final = frozenset({"scout"})
"""Seats that may propose only their :data:`AGENT_PATHS`, in any block (Scout reads users'
text, so it never proposes caps, budgets, autonomy or any other setting)."""
AGENT_NEVER: Final[Mapping[str, str]] = {
    "product.delivery.auto_merge": (
        "auto-merge targets are set by owners in the cockpit; agents never propose them "
        "and never merge"
    ),
    "autonomy": "owners set the factory's autonomy in Steer; agents never propose it",
    "structure": (
        "the levels (their names, who answers, verbs and authority) are set by owners in "
        "the cockpit; agents never propose them"
    ),
}
"""Paths no agent proposes, whatever its seat, and why: which PRs the server merges, how
much the agents may do alone and how work is shown are the owners' call."""


def under(path: str, prefix: str) -> bool:
    """Whether dotted *path* is *prefix* or lies below it (segment-aware)."""
    return path == prefix or path.startswith(f"{prefix}.")


def in_blocks(path: str, blocks: tuple[str, ...] = BLOCKS) -> bool:
    """Whether *path* lies in one of *blocks*."""
    return any(under(path, block) for block in blocks)


def agent_never_reason(path: str) -> str | None:
    """
    :returns: Why no agent may propose *path* when it is, holds or lies in one of
        :data:`AGENT_NEVER` (the first that matches), else ``None``.
    """
    for never, reason in AGENT_NEVER.items():
        if under(path, never) or under(never, path):
            return reason
    return None


def agent_never(path: str) -> bool:
    """Whether *path* is, holds or lies in one no agent may propose (:data:`AGENT_NEVER`)."""
    return agent_never_reason(path) is not None


def agent_may_propose(seat: str, path: str) -> bool:
    """Whether an agent in *seat* (``scout``, ``sentinel``, ``atlas``, …) may propose *path*."""
    if agent_never(path):
        return False
    allowed = AGENT_PATHS.get(seat, ())
    if seat not in ALLOWLIST_SEATS and not in_blocks(path, AGENT_GATED):
        return True
    return any(under(path, prefix) for prefix in allowed)


def discovery_server_on() -> bool:
    """Whether the server runs discovery at all (:data:`DISCOVERY_ENV`)."""
    return os.environ.get(DISCOVERY_ENV, "").strip().lower() in ("1", "true", "yes", "on")


# ── Approval classes of the new paths ──────────────────────────


def _narrower_source(before: Any, after: Any) -> bool:
    """Whether source *after* only narrows *before* (pause, less budget, fewer roles, stricter)."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    same = ("id", "label", "kind", "params", "consent")
    if any(before.get(k) != after.get(k) for k in same):
        return False
    if not set(after.get("roles") or []) <= set(before.get("roles") or []):
        return False
    b_budget = (before.get("budget") or {}).get("calls_per_day", 0)
    a_budget = (after.get("budget") or {}).get("calls_per_day", 0)
    if a_budget > b_budget:
        return False
    b_priv, a_priv = before.get("privacy") or {}, after.get("privacy") or {}
    if a_priv.get("level") != b_priv.get("level"):
        return False
    if not set(b_priv.get("redact") or []) <= set(a_priv.get("redact") or []):
        return False
    if a_priv.get("quote_max", 0) > b_priv.get("quote_max", 0):
        return False
    if a_priv.get("keep_days", 0) > b_priv.get("keep_days", 0):
        return False
    b_int, a_int = before.get("interval_min", 0), after.get("interval_min", 0)
    if b_int and (not a_int or a_int < b_int):
        return False
    if not b_int and a_int:
        return False
    return after.get("state") in (before.get("state"), "paused")


def source_class(before: Any, after: Any) -> Literal["A", "B"]:
    """Class of a ``discovery.sources.<id>`` change: A when it only narrows, else B."""
    return "A" if _narrower_source(before, after) else "B"


def sources_narrow_only(entries: Any) -> bool:
    """
    Whether diff *entries* (``{path, before, after}``) only narrow sources:
    each is one ``discovery.sources.<id>`` that :func:`source_class` calls A
    (a pause, a lower budget, fewer roles, stricter privacy). Editors apply
    those at once, with Undo (mechanism §4.5).
    """
    found = list(entries)
    return bool(found) and all(
        str(e.get("path") or "").startswith("discovery.sources.")
        and source_class(e.get("before"), e.get("after")) == "A"
        for e in found
    )


def off_is_a(before: Any, after: Any) -> Literal["A", "B"]:
    """Turning something off is A (cheaper and safer); turning it on is B."""
    return "A" if after is False and before is True else "B"


def slower_or_off_is_a(before: Any, after: Any) -> Literal["A", "B"]:
    """An hours interval: off (0) or longer is A, shorter or back on is B."""
    if isinstance(after, bool) or isinstance(before, bool):
        return "B"
    if not isinstance(after, int | float) or not isinstance(before, int | float):
        return "B"
    if after == 0:
        return "A"
    return "A" if before and after >= before else "B"


__all__ = [
    "AGENT_GATED",
    "AGENT_NEVER",
    "AGENT_PATHS",
    "ALLOWLIST_SEATS",
    "AUTO_MERGE_KEY",
    "BLOCKS",
    "BRIEF_MAX",
    "CONSENT",
    "DISCOVERY_ENV",
    "DISCOVERY_SETTING",
    "DISCOVERY_TRIALS",
    "ENV_FACTS",
    "EVIDENCE_LEVELS",
    "FACTORY_LEVELS",
    "FACTORY_TREE",
    "GOAL_ID_RE",
    "HOW_WORK_SHOWS",
    "KEEP_DAYS",
    "KIND_IS_LEVEL",
    "LEGACY_MODELS",
    "LEVEL_KEY",
    "LEVEL_KINDS",
    "LISTS",
    "MIXED_GUARDS",
    "NEEDS_DISCOVERY_GUARDS",
    "NOT_TRIALABLE",
    "NOT_TRIALABLE_REASON",
    "NO_PROJECT_FOLLOWS",
    "PRIVACY_RANK",
    "PRODUCT_LEVELS",
    "PRODUCT_TREE",
    "REDACT_FLOOR",
    "SECRET_REFUSAL",
    "SOURCE_PRIVACY",
    "TILES_MAX_BOUNDS",
    "TOO_FEW_PRS",
    "TREES",
    "USER_ID",
    "VENDORS_BY_ROLE",
    "AuthorityDef",
    "AutoMergeTargetDef",
    "BriefsDef",
    "DeliveryDef",
    "DemoAuthDef",
    "DiscoveryDef",
    "EnvironmentDef",
    "GoalDef",
    "HomeDef",
    "KindAuthorityDef",
    "KindsDef",
    "LevelDef",
    "LevelsDef",
    "ListSpec",
    "OrderDef",
    "ProductDef",
    "RespondersDef",
    "RoleDefaultsDef",
    "RubricDef",
    "ServiceDef",
    "SourceDef",
    "StrategyDef",
    "StructureDef",
    "VerbsDef",
    "agent_may_propose",
    "agent_never",
    "agent_never_reason",
    "discovery_server_on",
    "downgrade",
    "has_secret",
    "in_blocks",
    "item_id_ok",
    "list_of",
    "off_is_a",
    "secret_key",
    "secret_text",
    "seq_of",
    "slower_or_off_is_a",
    "source_class",
    "sources_narrow_only",
    "under",
    "upgrade",
]
