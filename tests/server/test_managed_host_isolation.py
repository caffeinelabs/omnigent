"""Unit tests for :mod:`omnigent.server.managed_host_isolation`.

A managed sandbox host runs only the session tree it was provisioned for; a
shared host (laptop, SSH host) keeps admitting every session of its owner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.managed_host_isolation import (
    MANAGED_HOST_CLAIM_LABEL_KEY,
    is_managed_host,
    managed_host_admits,
    require_host_admits_new_session,
    session_fired_by_schedule,
    session_may_use_host,
    spawn_tree_root,
)

_PL_POD = "21ff0b1851d246639ebf87735bce7167"
_OTHER_POD = "9c0ffee0000000000000000000000001"


@dataclass
class _Host:
    host_id: str
    user_id: str = "alice@example.com"
    sandbox_provider: str | None = "kubernetes"


@dataclass
class _Conv:
    id: str
    host_id: str | None = None
    root_conversation_id: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    kind: str = "default"


class _ConvStore:
    def __init__(self, *convs: _Conv) -> None:
        self.rows = {c.id: c for c in convs}

    def get_conversation(self, conversation_id: str) -> _Conv | None:
        return self.rows.get(conversation_id)


class _RunStore:
    def __init__(self, fired: set[str]) -> None:
        self.fired = fired

    def has_run_for_conversation(self, conversation_id: str) -> bool:
        return conversation_id in self.fired


def _claimed(conv_id: str, host_id: str, **kwargs: object) -> _Conv:
    return _Conv(
        id=conv_id,
        host_id=host_id,
        labels={MANAGED_HOST_CLAIM_LABEL_KEY: host_id},
        **kwargs,  # type: ignore[arg-type]
    )


def test_is_managed_host_requires_a_provider_string() -> None:
    assert is_managed_host(_Host(_PL_POD))
    assert not is_managed_host(_Host(_PL_POD, sandbox_provider=None))
    assert not is_managed_host(None)
    # Duck-typed fakes without the attribute, and MagicMock stand-ins, are shared.
    assert not is_managed_host(SimpleNamespace(host_id="h"))
    assert not is_managed_host(MagicMock())


def test_shared_host_admits_any_session() -> None:
    laptop = _Host("laptop", sandbox_provider=None)
    assert managed_host_admits(_Conv("s1"), laptop)
    assert managed_host_admits(_Conv("s1", host_id="elsewhere"), laptop, fired_by_schedule=True)


def test_managed_host_admits_only_its_claimed_owner() -> None:
    pod = _Host(_PL_POD)
    assert managed_host_admits(_claimed("pl", _PL_POD), pod)
    # Claim on another sandbox: this session's own sandbox is elsewhere.
    assert not managed_host_admits(_claimed("other", _OTHER_POD), pod)
    # A claim is authoritative even against borrowed-binding evidence.
    assert managed_host_admits(_claimed("pl", _PL_POD), pod, fired_by_schedule=True)


def test_claimless_root_must_already_be_bound_and_not_fired() -> None:
    pod = _Host(_PL_POD)
    # A pre-claim managed session bound to its sandbox keeps working.
    assert managed_host_admits(_Conv("legacy", host_id=_PL_POD), pod)
    # Unbound, or bound elsewhere: never admitted to someone else's sandbox.
    assert not managed_host_admits(_Conv("fresh"), pod)
    assert not managed_host_admits(_Conv("laptop-session", host_id="laptop"), pod)
    # A scheduled-fire session bound to the sandbox borrowed it.
    assert not managed_host_admits(_Conv("fired", host_id=_PL_POD), pod, fired_by_schedule=True)


def test_sub_agent_inherits_its_roots_ownership() -> None:
    pod = _Host(_PL_POD)
    root = _claimed("pl", _PL_POD)
    child = _Conv("child", root_conversation_id="pl", kind="sub_agent")
    stranger_child = _Conv("child2", root_conversation_id="hello", kind="sub_agent")
    store = _ConvStore(root, _Conv("hello"), child, stranger_child)

    assert spawn_tree_root(child, store) is root
    assert session_may_use_host(child, pod, store)
    assert not session_may_use_host(stranger_child, pod, store)


def test_spawn_tree_root_falls_back_to_self_when_root_is_gone() -> None:
    orphan = _Conv("child", root_conversation_id="deleted", kind="sub_agent")
    assert spawn_tree_root(orphan, _ConvStore(orphan)) is orphan


def test_session_may_use_host_uses_scheduled_run_evidence() -> None:
    pod = _Host(_PL_POD)
    pl = _Conv("pl", host_id=_PL_POD)
    hello = _Conv("hello", host_id=_PL_POD)
    store = _ConvStore(pl, hello)
    runs = _RunStore(fired={"hello"})

    assert session_may_use_host(pl, pod, store, scheduled_task_store=runs)
    assert not session_may_use_host(hello, pod, store, scheduled_task_store=runs)
    # Without the run store there is no evidence; the legacy binding stands.
    assert session_may_use_host(hello, pod, store)
    # Shared hosts never consult the evidence.
    assert session_may_use_host(hello, _Host("laptop", sandbox_provider=None), store)


def test_scheduled_evidence_lookup_failure_is_no_evidence() -> None:
    class _Broken:
        def has_run_for_conversation(self, conversation_id: str) -> bool:
            raise RuntimeError(f"db down for {conversation_id}")

    assert session_fired_by_schedule("hello", _Broken()) is False
    assert session_fired_by_schedule("hello", None) is False


@pytest.mark.asyncio
async def test_new_top_level_session_cannot_name_a_managed_sandbox() -> None:
    hosts = SimpleNamespace(get_host=lambda host_id: _Host(host_id))
    with pytest.raises(OmnigentError) as exc:
        await require_host_admits_new_session(
            user_id="alice@example.com",
            host_id=_PL_POD,
            parent_session_id=None,
            host_store=hosts,
            conversation_store=_ConvStore(),
        )
    assert exc.value.code == ErrorCode.INVALID_INPUT
    assert "managed sandbox" in exc.value.message


@pytest.mark.asyncio
async def test_new_sub_agent_of_the_owner_may_name_its_sandbox() -> None:
    hosts = SimpleNamespace(get_host=lambda host_id: _Host(host_id))
    store = _ConvStore(_claimed("pl", _PL_POD), _Conv("hello"))
    await require_host_admits_new_session(
        user_id="alice@example.com",
        host_id=_PL_POD,
        parent_session_id="pl",
        host_store=hosts,
        conversation_store=store,
    )
    with pytest.raises(OmnigentError):
        await require_host_admits_new_session(
            user_id="alice@example.com",
            host_id=_PL_POD,
            parent_session_id="hello",
            host_store=hosts,
            conversation_store=store,
        )


@pytest.mark.asyncio
async def test_new_session_check_leaves_shared_and_foreign_hosts_to_existing_checks() -> None:
    laptop = SimpleNamespace(get_host=lambda host_id: _Host(host_id, sandbox_provider=None))
    await require_host_admits_new_session(
        user_id="alice@example.com",
        host_id="laptop",
        parent_session_id=None,
        host_store=laptop,
        conversation_store=_ConvStore(),
    )
    # Another user's sandbox: the ownership check (403/404) answers, not this one.
    bobs = SimpleNamespace(get_host=lambda host_id: _Host(host_id, user_id="bob@example.com"))
    await require_host_admits_new_session(
        user_id="alice@example.com",
        host_id=_PL_POD,
        parent_session_id=None,
        host_store=bobs,
        conversation_store=_ConvStore(),
    )


# ── Enforcement points that only hold a connection / a transfer ─────────────


class _LaunchConvStore(_ConvStore):
    def __init__(self, *convs: _Conv) -> None:
        super().__init__(*convs)
        self.rotations: list[str] = []

    def replace_runner_id(self, conversation_id: str, *_args: object, **_kwargs: object) -> None:
        self.rotations.append(conversation_id)


@pytest.mark.asyncio
async def test_runner_launch_refuses_a_foreign_tree_on_a_managed_connection() -> None:
    """The last line: whatever path reaches the launch primitive, a managed
    sandbox's connection never spawns a runner for a tree it does not own —
    and the session's runner binding is left untouched."""
    from omnigent.server.routes import sessions as sessions_module

    stranger = _Conv("hello", host_id=None, root_conversation_id="hello")
    store = _LaunchConvStore(stranger)
    conn = SimpleNamespace(host_id=_PL_POD, host_record=_Host(_PL_POD))
    with pytest.raises(OmnigentError) as exc:
        await sessions_module._launch_runner_on_host(
            stranger,  # type: ignore[arg-type]
            store,  # type: ignore[arg-type]
            SimpleNamespace(),  # type: ignore[arg-type]
            conn,  # type: ignore[arg-type]
        )
    assert exc.value.code == ErrorCode.CONFLICT
    assert store.rotations == []


@pytest.mark.asyncio
async def test_terminal_transfer_carries_the_claim_only_from_an_owner() -> None:
    """The native /clear rotation moves the live terminal to a fresh session;
    that session joins the sandbox's tree only when the source owned it."""
    from omnigent.server.routes.sessions.routes_resources import _carry_managed_host_claim

    class _LabelStore(_ConvStore):
        def __init__(self, *convs: _Conv) -> None:
            super().__init__(*convs)
            self.writes: dict[str, dict[str, str]] = {}

        def set_labels(self, conversation_id: str, labels: dict[str, str]) -> None:
            self.writes[conversation_id] = labels

    hosts = {
        _PL_POD: _Host(_PL_POD),
        "laptop": _Host("laptop", sandbox_provider=None),
    }
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                host_store=SimpleNamespace(get_host=hosts.get),
                scheduled_task_store=_RunStore(fired={"hello"}),
            )
        )
    )
    owner = _claimed("pl", _PL_POD)
    fired = _Conv("hello", host_id=_PL_POD)
    on_laptop = _Conv("local", host_id="laptop")
    store = _LabelStore(owner, fired, on_laptop)

    await _carry_managed_host_claim(request, owner, "pl_after_clear", store)  # type: ignore[arg-type]
    await _carry_managed_host_claim(request, fired, "hello_after_clear", store)  # type: ignore[arg-type]
    await _carry_managed_host_claim(request, on_laptop, "local_after_clear", store)  # type: ignore[arg-type]

    assert store.writes == {"pl_after_clear": {MANAGED_HOST_CLAIM_LABEL_KEY: _PL_POD}}
