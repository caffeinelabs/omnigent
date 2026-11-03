"""What every source kind shares: the resolved source, the backends, a run's context and result.

A kind (:class:`Kind`) knows its read-only tools, how to query them, how to
sync, and how to test the source. It never stores anything: it hands raw
items back to the gateway, which alone passes them to the ledger.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, ValidationError

from omnigent.server.factory.control import CockpitError

if TYPE_CHECKING:
    from omnigent.entities.factory_discovery import FactorySignal, FactorySourceState
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.discovery.kinds.repo import GitHubReader
    from omnigent.server.factory.discovery.ledger import Item, SourceRef
    from omnigent.server.factory.discovery.web_io import SearchBackend, WebFetcher

TEXT_MAX: Final = 4000
"""The most raw text of one item handed to the ledger (it clips to ``quote_max`` itself)."""


class SourceFailed(Exception):
    """
    The source itself did not answer or broke (counts toward holding it).

    Its message is for people (the access log, ``last_error``, the incident);
    an agent only learns that the source failed.
    """


class ItemFailed(Exception):
    """
    One thing the caller chose could not be read (a dead link): the caller's
    miss, not the source's, so it never counts toward holding the source.

    :param message: A fixed sentence, safe to show an agent.
    :param detail: What happened, for people's log.
    """

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = (detail or message)[:300]


class Refused(CockpitError):
    """
    A refusal whose agent-facing message is fixed, with what happened kept for people's log.

    :param detail: Never in the response body; the gateway logs it in place of the message.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        detail: str | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(status_code, code, message, field=field)
        self.detail = (detail or message)[:300]


class Args(BaseModel):
    """A tool's arguments: strict, so nothing unexpected reaches a source."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)


class Params(BaseModel):
    """A kind's ``params``: owners' data, read leniently (unknown keys are ignored)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    tools: list[str] | None = None
    """When set, only these of the kind's tools (it never adds one)."""


@dataclass(frozen=True)
class Source:
    """
    One ``discovery.sources[]`` entry as the gateway uses it.

    :param ref: What the ledger needs (privacy, retention, state).
    :param roles: The seats that may query it (``scout``, ``lead``).
    :param calls_per_day: Its daily cap; every query, sync request and test counts.
    :param interval_min: Minutes between syncs (``0``: query only).
    """

    id: str
    label: str
    kind: str
    params: Mapping[str, Any]
    roles: tuple[str, ...]
    state: str
    calls_per_day: int
    interval_min: int
    ref: SourceRef


@dataclass(frozen=True)
class ToolSpec:
    """A read-only tool of a kind: its name, its arguments and what it reads."""

    name: str
    args: type[Args]
    does: str

    def describe(self) -> dict[str, Any]:
        """:returns: ``{name, does, args}`` for an agent (argument names, types, bounds)."""
        schema = self.args.model_json_schema()
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        args = {}
        for name, prop in props.items():
            entry = {k: v for k, v in prop.items() if k in ("type", "enum", "default")}
            for bound in ("minimum", "maximum", "minLength", "maxLength"):
                if bound in prop:
                    entry[bound] = prop[bound]
            if "anyOf" in prop:
                entry["type"] = [p.get("type") for p in prop["anyOf"] if p.get("type")]
            entry["required"] = name in required
            args[name] = entry
        return {"name": self.name, "does": self.does, "args": args}


@dataclass(frozen=True)
class Check:
    """One line of a source test: what was checked, whether it worked, and the detail."""

    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class Backends:
    """Where kinds reach the outside (injectable: tests pass fakes on ``app.state``)."""

    github: GitHubReader | None = None
    search: SearchBackend | None = None
    fetcher: WebFetcher | None = None


@dataclass
class Fetched:
    """
    What one query or sync brought back, before the ledger stores it.

    :param items: Raw items for the ledger.
    :param existing: Signals already stored (a kind that reads the ledger).
    :param returned: Items the source answered with (before anything was skipped).
    :param urls: URLs read (logged).
    :param q: The query sent (logged, redacted).
    :param cursor: The sync's new cursor (stored when it succeeds).
    :param errors: Parts that failed while others worked.
    """

    items: list[Item] = field(default_factory=list)
    existing: list[FactorySignal] = field(default_factory=list)
    returned: int = 0
    urls: list[str] = field(default_factory=list)
    q: str | None = None
    cursor: str | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class Context:
    """
    One run of a kind.

    :param charge: Count one more request against the source's daily cap
        (the first is counted before the run); refuses with 429 ``over_budget``.
    """

    app_state: Any
    deps: FactoryDeps
    factory_id: str
    source: Source
    backends: Backends
    now: int
    charge: Callable[[], Awaitable[None]]
    state: FactorySourceState | None = None


class Kind(ABC):
    """A kind of source the gateway can read without a secret."""

    name: ClassVar[str]
    tools: ClassVar[Mapping[str, ToolSpec]]
    params_model: ClassVar[type[Params]] = Params
    syncs: ClassVar[bool] = True

    def params(self, source: Source) -> Params:
        """
        :returns: The source's params as this kind reads them.
        :raises CockpitError: 422 ``source_invalid`` with the first problem.
        """
        try:
            return self.params_model.model_validate(dict(source.params))
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            where = ".".join(str(p) for p in first.get("loc", ()))
            raise CockpitError(
                422,
                "source_invalid",
                f"source {source.id}: params.{where}: {first.get('msg', 'invalid')}",
                source=source.id,
            ) from None

    def allowed(self, source: Source, backends: Backends) -> list[str]:  # noqa: ARG002
        """:returns: The tools this source offers: the kind's, narrowed by ``params.tools``."""
        names = list(self.tools)
        listed = self.params(source).tools
        return [n for n in names if listed is None or n in listed]

    def parse_args(self, tool: str, raw: Mapping[str, Any]) -> Args:
        """:raises CockpitError: 400 ``invalid_args`` with the first problem."""
        spec = self.tools[tool]
        try:
            return spec.args.model_validate(dict(raw))
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            where = ".".join(str(p) for p in first.get("loc", ()))
            raise CockpitError(
                400,
                "invalid_args",
                f"{tool}: {where or 'args'}: {first.get('msg', 'invalid')}",
                field=f"args.{where}" if where else "args",
            ) from None

    def check(  # noqa: B027
        self,
        deps: FactoryDeps,
        factory_id: str,
        source: Source,
        tool: str,
        args: Args,
    ) -> None:
        """
        Refuse arguments *source* does not allow, before any call is counted (synchronous).

        :raises CockpitError: 400 or 403 with why.
        """

    @abstractmethod
    async def query(self, ctx: Context, tool: str, args: Args) -> Fetched:
        """Run one read-only tool."""

    async def sync(self, ctx: Context) -> Fetched:  # noqa: ARG002
        """Read what is new since the cursor (kinds with :attr:`syncs`)."""
        return Fetched()

    @abstractmethod
    async def test(self, ctx: Context) -> list[Check]:
        """Check the source answers, storing nothing."""


def clip_text(*parts: str | None) -> str:
    """:returns: The non-empty *parts* on their own lines, cut to :data:`TEXT_MAX`."""
    return "\n".join(p.strip() for p in parts if p and p.strip())[:TEXT_MAX]


def unique(values: Sequence[str]) -> list[str]:
    """:returns: *values* without repeats, in order."""
    return list(dict.fromkeys(values))


__all__ = [
    "TEXT_MAX",
    "Args",
    "Backends",
    "Check",
    "Context",
    "Fetched",
    "ItemFailed",
    "Kind",
    "Params",
    "Refused",
    "Source",
    "SourceFailed",
    "ToolSpec",
    "clip_text",
    "unique",
]
