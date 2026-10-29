"""A factory member's guards write the repos of its factory projects, as the server reads them.

Round-3 E2E (decision D-4): a docs project on ``caffeinelabs/omnigent#staging``
pushed its branch but ``gh pr create`` was denied, because every factory
bundle passed ``write_repos: [caffeinelabs/app]`` to ``factory_role_guard``.
The server now resolves the repos when it builds a member's policy engine
(:mod:`omnigent.server.factory.guard_repos`), so these tests drive a real
engine over the seeded factory, with the same guards the bundles declare.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from omnigent import factory as F
from omnigent.policies.builtins.factory_invariants import seed_invariant_policy
from omnigent.policies.types import EvaluationContext
from omnigent.runtime.policies.builder import SERVER_POLICY_ARGUMENTS, build_policy_engine
from omnigent.server.factory import guard_repos
from omnigent.server.factory.background.reconcile import CREATED_BY_RECONCILE
from omnigent.spec.types import (
    AgentSpec,
    FunctionPolicySpec,
    FunctionRef,
    GuardrailsSpec,
    Phase,
    PolicyAction,
    PolicySpec,
)
from tests.server.factory.conftest import FactoryWorld

_GUARD = "omnigent.policies.builtins.factory.factory_role_guard"
_APP = "caffeinelabs/app"
_OMNI = "caffeinelabs/omnigent"
_HELLO = "octo/hello"


def _guard(role: str) -> FunctionPolicySpec:
    """The ``factory_guard`` a factory bundle declares for *role* (no write_repos)."""
    return FunctionPolicySpec(
        name=f"factory_guard_{role}",
        on=None,
        function=FunctionRef(
            path=_GUARD, arguments={"role": role, "push_ref_prefixes": ["factory/"]}
        ),
    )


def _spec(*roles: str) -> AgentSpec:
    """A spec carrying one guard per role (a sub-agent passes its PL's and its own)."""
    policies: list[PolicySpec] = [_guard(role) for role in roles]
    return AgentSpec(
        spec_version=1, name="factory-test", guardrails=GuardrailsSpec(policies=policies)
    )


_PL = _spec(F.ROLE_PROJECT_LEAD)
_PL_AND_IC = _spec(F.ROLE_PROJECT_LEAD, F.ROLE_IC)
_LEAD = _spec(F.ROLE_LEAD)


async def _decide(
    world: FactoryWorld,
    key: str,
    command: str,
    spec: AgentSpec = _PL_AND_IC,
    defaults: list[Any] | None = None,
) -> tuple[str, str]:
    """:returns: ``(ALLOW|DENY, reason)`` for *command* run by session *key*."""
    engine = build_policy_engine(
        spec=spec,
        conversation_id=world.id(key),
        conversation_store=world.store,
        default_policies=defaults,
    )
    result = await engine.evaluate(
        EvaluationContext(
            phase=Phase.TOOL_CALL,
            tool_name="Bash",
            content={"name": "Bash", "arguments": {"command": command}},
        )
    )
    verdict = "DENY" if result.action == PolicyAction.DENY else "ALLOW"
    assert result.action in (PolicyAction.ALLOW, PolicyAction.DENY), result
    return verdict, result.reason or ""


def _add_project(world: FactoryWorld, slug: str, repo: str, created_by: str = "alice") -> None:
    world.deps.factory_store.upsert_project(
        world.id("lead_a"), slug, title=slug, repo=repo, base_branch="main", created_by=created_by
    )


def _add_pl(world: FactoryWorld, key: str, slug: str) -> None:
    world.make_session(
        key,
        agent="pl",
        title=f"PL · {slug}",
        labels={
            F.LABEL_ID: world.id("lead_a"),
            F.LABEL_ROLE: F.ROLE_PROJECT_LEAD,
            F.LABEL_PROJECT: slug,
            F.LABEL_PARENT: world.id("lead_a"),
            F.LABEL_STATE: "active",
        },
    )


@pytest.fixture()
def factory(world: FactoryWorld) -> Iterator[FactoryWorld]:
    """
    The seeded factory with two projects and the server resolver installed.

    ``api-version`` (PL ``pl`` and its sub-agents) is on caffeinelabs/app;
    ``docs-guide`` (PL ``pl_docs`` and its IC ``ic_docs``) is on
    caffeinelabs/omnigent, like the round-3 docs project.
    """
    _add_project(world, "api-version", _APP)
    _add_project(world, "docs-guide", _OMNI)
    _add_pl(world, "pl_docs", "docs-guide")
    world.make_session(
        "ic_docs", agent="pl", title="ic:guide", parent="pl_docs", sub_agent_name="ic"
    )
    uninstall = guard_repos.install(world.app_state)
    try:
        yield world
    finally:
        uninstall()
    assert not set(guard_repos.GUARDED_HANDLERS) & set(SERVER_POLICY_ARGUMENTS)


# ── A project writes its own repo ─────────────────────────────────────────────


@pytest.mark.parametrize("key", ["pl_docs", "ic_docs"])
async def test_an_omnigent_project_opens_its_pr_there_and_nowhere_else(
    factory: FactoryWorld, key: str
) -> None:
    allowed = [
        "gh pr create --draft --repo caffeinelabs/omnigent --base staging "
        "--head factory/docs-guide/guide --title x --body-file b.md",
        "gh pr comment https://github.com/caffeinelabs/omnigent/pull/160 --body hi",
        "git push -u origin factory/docs-guide/guide",
        "git push https://github.com/caffeinelabs/omnigent.git factory/docs-guide/guide",
    ]
    for command in allowed:
        verdict, reason = await _decide(factory, key, command)
        assert verdict == "ALLOW", (command, reason)
    # Another project's repo is not this project's, and octo/hello is no project's.
    for repo in (_APP, _HELLO, "caffeinelabs/infra"):
        verdict, reason = await _decide(factory, key, f"gh pr create -R {repo} --fill")
        assert verdict == "DENY", repo
        assert repo in reason
    verdict, _ = await _decide(
        factory, key, "git push https://github.com/caffeinelabs/app.git factory/docs-guide/x"
    )
    assert verdict == "DENY"


async def test_an_app_project_keeps_writing_app_only(factory: FactoryWorld) -> None:
    for key in ("pl", "ic"):
        assert (await _decide(factory, key, f"gh pr create -R {_APP} --fill"))[0] == "ALLOW"
        verdict, reason = await _decide(factory, key, f"gh pr create -R {_OMNI} --fill")
        assert verdict == "DENY"
        assert "writable: caffeinelabs/app" in reason


@pytest.mark.parametrize("key", ["lead_a", "lead_b"])
async def test_a_lead_writes_every_factory_repo(factory: FactoryWorld, key: str) -> None:
    for repo in (_APP, _OMNI):
        verdict, reason = await _decide(factory, key, f"gh pr comment -R {repo} 7 -b ok", _LEAD)
        assert verdict == "ALLOW", reason
        # Leads still never open PRs or push.
        assert (await _decide(factory, key, f"gh pr create -R {repo} --fill", _LEAD))[0] == "DENY"
    assert (await _decide(factory, key, f"gh pr comment -R {_HELLO} 7 -b ok", _LEAD))[0] == "DENY"
    assert (await _decide(factory, key, "git push origin factory/a/b", _LEAD))[0] == "DENY"


# ── What the repos never change ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "spec"),
    [("pl_docs", _PL), ("ic_docs", _PL_AND_IC), ("pl", _PL), ("lead_a", _LEAD)],
)
async def test_merges_and_protected_branches_stay_denied_on_every_repo(
    factory: FactoryWorld, key: str, spec: AgentSpec
) -> None:
    defaults: list[Any] = []
    seed_invariant_policy(defaults)
    for repo in (_APP, _OMNI):
        url = f"https://github.com/{repo}/pull/1"
        for command in (
            f"gh pr merge -R {repo} 1 --squash",
            f"gh api -X PUT repos/{repo}/pulls/1/merge",
            f"python -m omnigent.factory_merge {url}",
            f"OMNIGENT_FACTORY_MERGE_REPOS={repo} python -m omnigent.factory_merge {url}",
            f"git push https://github.com/{repo}.git HEAD:staging",
            "git push origin temp-main",
        ):
            verdict, _ = await _decide(factory, key, command, spec, defaults)
            assert verdict == "DENY", (key, command)


async def test_the_invariant_policy_lifts_the_same_repos(factory: FactoryWorld) -> None:
    """The admin default sees the member's repos too, so it never vetoes the guard's ALLOW."""
    defaults: list[Any] = []
    seed_invariant_policy(defaults)
    bare = AgentSpec(spec_version=1, name="no-guardrails")
    create = f"gh pr create -R {_OMNI} --fill"
    for spec in (bare, _PL):
        assert (await _decide(factory, "pl_docs", create, spec, defaults))[0] == "ALLOW"
    # The app project's PL does not write omnigent, so the invariant still denies it.
    verdict, reason = await _decide(
        factory, "pl", f"gh pr create -R {_OMNI} --fill", bare, defaults
    )
    assert verdict == "DENY"
    assert reason.startswith("Factory invariant: ")
    infra = await _decide(
        factory, "pl_docs", "gh pr create -R caffeinelabs/infra --fill", bare, defaults
    )
    assert infra[0] == "DENY"


# ── The set is the server's ───────────────────────────────────────────────────


async def test_a_project_added_later_extends_the_set(factory: FactoryWorld) -> None:
    comment = f"gh pr comment -R {_HELLO} 7 -b ok"
    assert (await _decide(factory, "lead_a", comment, _LEAD))[0] == "DENY"

    _add_project(factory, "hello-world", _HELLO)
    _add_pl(factory, "pl_hello", "hello-world")

    assert (await _decide(factory, "lead_a", comment, _LEAD))[0] == "ALLOW"
    assert (await _decide(factory, "pl_hello", f"gh pr create -R {_HELLO} --fill"))[0] == "ALLOW"
    # Existing projects keep their own repo only.
    assert (await _decide(factory, "pl_docs", f"gh pr create -R {_HELLO} --fill"))[0] == "DENY"


async def test_an_agent_cannot_widen_the_set(factory: FactoryWorld) -> None:
    """Labels, session_state, charters and a spawn-backfilled project add no repo."""
    store = factory.store
    pl_docs = factory.id("pl_docs")
    store.set_labels(
        pl_docs,
        {
            "omnigent.factory.write_repos": _HELLO,
            "omnigent.factory.factory_repos": _HELLO,
            "omnigent.sandbox.repo.0": f"https://github.com/{_HELLO}#main",
            "factory_repos": _HELLO,
        },
    )
    store.set_session_state(pl_docs, {"factory_repos": [_HELLO], "write_repos": [_HELLO]})
    store.set_session_state(factory.id("ic_docs"), {"factory_repos": [_HELLO]})
    project = factory.deps.factory_store.get_project(factory.id("lead_a"), "docs-guide")
    assert project is not None
    factory.deps.factory_store.update_project(
        project.id, charter={"goal": "docs", "repo": _HELLO, "write_repos": [_HELLO]}
    )
    # A PL a lead spawned for a project no human made: reconcile backfills its row.
    _add_project(factory, "rogue", _HELLO, created_by=CREATED_BY_RECONCILE)
    _add_pl(factory, "pl_rogue", "rogue")

    create = f"gh pr create -R {_HELLO} --fill"
    for key in ("pl_docs", "ic_docs", "pl_rogue"):
        assert (await _decide(factory, key, create))[0] == "DENY", key
    lead_comment = await _decide(factory, "lead_a", f"gh pr comment -R {_HELLO} 7 -b x", _LEAD)
    assert lead_comment[0] == "DENY"
    # The docs project still writes its own repo.
    assert (await _decide(factory, "pl_docs", f"gh pr create -R {_OMNI} --fill"))[0] == "ALLOW"


async def test_a_store_error_leaves_no_writable_repo(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _broken(_factory_id: str) -> list[Any]:
        raise RuntimeError("db down")

    monkeypatch.setattr(factory.deps.factory_store, "list_projects", _broken)
    for repo in (_OMNI, _APP):
        verdict, reason = await _decide(factory, "pl_docs", f"gh pr create -R {repo} --fill")
        assert verdict == "DENY"
        assert "writable: none" in reason


# ── Unchanged outside a factory ───────────────────────────────────────────────


async def test_non_factory_sessions_keep_the_bundle_defaults(factory: FactoryWorld) -> None:
    assert (await _decide(factory, "plain", f"gh pr create -R {_APP} --fill"))[0] == "ALLOW"
    verdict, reason = await _decide(factory, "plain", f"gh pr create -R {_OMNI} --fill")
    assert verdict == "DENY"
    assert "never write to" in reason


async def test_with_the_factory_off_members_keep_the_bundle_defaults(
    world: FactoryWorld,
) -> None:
    """No resolver (factory off): the guard keeps its static arguments, as before."""
    _add_project(world, "docs-guide", _OMNI)
    _add_pl(world, "pl_docs", "docs-guide")
    assert not set(guard_repos.GUARDED_HANDLERS) & set(SERVER_POLICY_ARGUMENTS)
    assert (await _decide(world, "pl_docs", f"gh pr create -R {_APP} --fill"))[0] == "ALLOW"
    assert (await _decide(world, "pl_docs", f"gh pr create -R {_OMNI} --fill"))[0] == "DENY"


def test_member_repos_by_membership(factory: FactoryWorld) -> None:
    store = factory.deps.factory_store

    def repos(key: str) -> frozenset[str] | None:
        conv = factory.get(key)
        root = factory.store.get_conversation(conv.root_conversation_id) or conv
        return guard_repos.member_repos(conv, root, store)

    assert repos("pl") == {_APP}
    assert repos("ic") == {_APP}
    assert repos("reviewer") == {_APP}
    assert repos("pl_docs") == {_OMNI}
    assert repos("ic_docs") == {_OMNI}
    assert repos("lead_a") == {_APP, _OMNI}
    assert repos("lead_b") == {_APP, _OMNI}
    assert repos("plain") is None
    # Another factory's lead sees only its own (none) projects.
    assert repos("other_lead") == frozenset()


async def test_factory_services_install_the_resolver_only_when_enabled(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.factory import start_factory_services

    monkeypatch.delenv(F.FACTORY_ENABLED_ENV, raising=False)
    stop = await start_factory_services(world.app_state)
    assert not set(guard_repos.GUARDED_HANDLERS) & set(SERVER_POLICY_ARGUMENTS)
    await stop()

    monkeypatch.setenv(F.FACTORY_ENABLED_ENV, "1")
    stop = await start_factory_services(world.app_state)
    try:
        assert set(guard_repos.GUARDED_HANDLERS) <= set(SERVER_POLICY_ARGUMENTS)
    finally:
        await stop()
    assert not set(guard_repos.GUARDED_HANDLERS) & set(SERVER_POLICY_ARGUMENTS)
