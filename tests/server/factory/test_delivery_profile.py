"""The product profile's ``product.delivery`` drives what used to be product code.

Where the server once had one product's repos, env domain, merge wording and
demo paths built in, it now reads the factory's definition: the env and
preview URLs it probes and redirects to, the repos the guards deny, the PL's
charter and the cockpit's snapshot. A deployment seeds the profile of every
factory once (``OMNIGENT_FACTORY_DELIVERY_SEED``), so a factory from before
the profile keeps behaving as it did. Values here are samples.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from omnigent import factory as F
from omnigent.entities.factory import FactoryProject
from omnigent.policies.builtins import factory as guard
from omnigent.policies.types import EvaluationContext
from omnigent.runtime.policies.builder import build_policy_engine
from omnigent.server.factory import definition, env_urls, guard_repos, product_profile
from omnigent.server.factory.agent_scope import env_url_error
from omnigent.server.factory.api.pr_preview import preview_redirect_target
from omnigent.server.factory.background import delivery_seed
from omnigent.server.factory.charter import render_pl_charter
from omnigent.spec.types import (
    AgentSpec,
    FunctionPolicySpec,
    FunctionRef,
    GuardrailsSpec,
    Phase,
    PolicyAction,
)
from tests.server.factory.conftest import FactoryWorld

# (sample) What one deployment seeds into every factory.
SEED = {
    "merge_policy": "Humans merge every pull request on GitHub",
    "protected_repos": ["acme/infra", "acme/platform"],
    "demo_paths": ["web/**"],
}
APP = "acme/app"
DOMAIN = "previews.example.test"


@pytest.fixture()
def factory(world: FactoryWorld, monkeypatch: pytest.MonkeyPatch) -> Iterator[FactoryWorld]:
    """The seeded world with the definition resolver installed and no deployment seed."""
    monkeypatch.delenv(product_profile.DELIVERY_SEED_ENV, raising=False)
    restore = definition.install(world.app_state)
    try:
        yield world
    finally:
        restore()


def _seed_env(monkeypatch: pytest.MonkeyPatch, value: Any = SEED) -> None:
    raw = value if isinstance(value, str) else json.dumps(value)
    monkeypatch.setenv(product_profile.DELIVERY_SEED_ENV, raw)
    definition.invalidate()


def _fid(world: FactoryWorld) -> str:
    return world.id("lead_a")


def _root(world: FactoryWorld) -> Any:
    root = world.store.get_conversation(_fid(world))
    assert root is not None
    return root


def _set(world: FactoryWorld, values: dict[str, Any], by: str = "alice") -> int:
    return definition.set_values(world.deps, _fid(world), values, created_by=by).version


# ── The seed ─────────────────────────────────────────────────────────────────


def test_without_a_deployment_seed_v1_holds_no_delivery(factory: FactoryWorld) -> None:
    resolved = definition.definition_for(factory.deps, _fid(factory), fresh=True)
    assert resolved is not None and resolved.version == 1
    # Stored sparsely: a server from before the product block reads it unchanged.
    assert "product" not in resolved.stored
    assert product_profile.delivery(factory.deps, _fid(factory)) == {
        "merge_policy": None,
        "protected_repos": [],
        "demo_paths": [],
        "preview_domain": None,
        "release": "merged",
        "flags": {"provider": None, "convention": None},
    }


def test_a_new_factory_starts_from_the_deployment_seed(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_env(monkeypatch)
    resolved = definition.definition_for(factory.deps, _fid(factory), fresh=True)
    assert resolved is not None and resolved.version == 1
    assert resolved.created_via == "seed"
    assert resolved.stored["product"]["delivery"] == {
        **resolved.doc["product"]["delivery"],
        **SEED,
    }
    assert product_profile.protected_repos(factory.deps, _fid(factory)) == {
        "acme/infra",
        "acme/platform",
    }


@pytest.mark.parametrize(
    "raw", ["not json", "[1, 2]", json.dumps({"protected_repos": ["no slash"]}), "{}"]
)
def test_an_invalid_seed_seeds_nothing(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    _seed_env(monkeypatch, raw)
    assert product_profile.seed_values() == {}
    resolved = definition.definition_for(factory.deps, _fid(factory), fresh=True)
    assert resolved is not None and "product" not in resolved.stored


def test_an_existing_factory_gets_the_seed_once_as_a_version(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Started before the profile: v1 and an owner's v2, neither with delivery.
    assert definition.definition_for(factory.deps, _fid(factory), fresh=True) is not None
    assert _set(factory, {"loops.review.rounds": 2}) == 2

    _seed_env(monkeypatch)
    assert product_profile.seed_existing(factory.deps, _root(factory)) == 3
    row = factory.deps.factory_store.get_definition(_fid(factory), 3)
    assert row is not None
    assert (row.created_via, row.created_by, row.parent_version) == ("seed", "server", 2)
    resolved = definition.definition_for(factory.deps, _fid(factory), fresh=True)
    assert resolved is not None and resolved.version == 3
    # The owner's change stays; the seed only adds the delivery fields.
    assert resolved.doc["loops"]["review"]["rounds"] == 2
    assert {k: resolved.doc["product"]["delivery"][k] for k in SEED} == SEED
    assert _root(factory).labels[product_profile.LABEL_DELIVERY_SEED] == "v3"

    # Never again, even after an owner empties the profile.
    _set(factory, {"product.delivery.protected_repos": [], "product.delivery.demo_paths": []})
    assert product_profile.seed_existing(factory.deps, _root(factory)) is None
    assert definition.definition_for(factory.deps, _fid(factory), fresh=True).version == 4


def test_a_profile_an_owner_already_set_is_not_seeded(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert definition.definition_for(factory.deps, _fid(factory), fresh=True) is not None
    _set(factory, {"product.delivery.merge_policy": "Maintainers merge"})
    _set(factory, {"product.delivery.merge_policy": None})
    _seed_env(monkeypatch)
    assert product_profile.seed_existing(factory.deps, _root(factory)) is None
    assert _root(factory).labels[product_profile.LABEL_DELIVERY_SEED] == "set"


def test_nothing_to_seed_leaves_the_factory_for_a_later_seed(factory: FactoryWorld) -> None:
    assert product_profile.seed_existing(factory.deps, _root(factory)) is None
    assert product_profile.LABEL_DELIVERY_SEED not in _root(factory).labels


async def test_the_boot_service_seeds_every_factory_once(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = factory.id("other_lead")
    for fid in (_fid(factory), other):
        assert definition.definition_for(factory.deps, fid, fresh=True) is not None
    _seed_env(monkeypatch)
    assert sorted(await delivery_seed.seed_all(factory.app_state)) == sorted(
        [_fid(factory), other]
    )
    assert await delivery_seed.seed_all(factory.app_state) == []
    stop = await delivery_seed.start(factory.app_state)
    await stop()


# ── What today's behaviour reads from it ─────────────────────────────────────


def _project(**fields: Any) -> FactoryProject:
    base = FactoryProject(
        id="proj_1",
        factory_id="fac_1",
        slug="status",
        title="Status endpoint",
        repo=APP,
        base_branch="main",
        priority=1,
        status="active",
        charter={"goal": "Add GET /status.", "dod": ["curl -fsS {api}/status → 200"]},
    )
    return replace(base, **fields)


def test_the_charter_prints_the_merge_policy_protected_repos_and_demo_paths() -> None:
    delivery = {**SEED, "protected_repos": ["acme/infra", APP]}
    lines = render_pl_charter(_project(), delivery).splitlines()
    assert (
        "Protected repos: acme/infra (never write them; ask your lead before anything "
        "touching them)" in lines
    )
    assert (
        "Paths that need a demo: web/** (a PR touching them shows that change running "
        "in its demo)" in lines
    )
    assert lines[-1].startswith(
        "Merge policy: Humans merge every pull request on GitHub. The factory never merges. "
        "Report READY FOR HUMAN MERGE <url>"
    )


def test_an_unset_profile_renders_todays_charter() -> None:
    project = _project()
    assert render_pl_charter(project, None) == render_pl_charter(project, {})
    text = render_pl_charter(project, product_profile.delivery(None, None))
    assert "Protected repos" not in text and "need a demo" not in text
    assert "Merge policy: human. Humans merge acme/app; the factory never merges." in text


def test_demo_paths_stay_out_of_a_charter_without_previews() -> None:
    project = _project(charter={"goal": "Docs", "dod": ["renders"], "env_needed": False})
    assert "need a demo" not in render_pl_charter(project, SEED)


# ── The env domain ───────────────────────────────────────────────────────────


def test_the_profile_names_the_preview_domain_over_the_deployment(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(env_urls.ENV_DOMAIN_ENV, "envs.deploy.test")
    fid = _fid(factory)
    assert env_urls.factory_env_domain(fid) == "envs.deploy.test"
    _set(factory, {"product.delivery.preview_domain": DOMAIN})
    definition.invalidate()
    assert env_urls.factory_env_domain(fid) == DOMAIN
    domain = env_urls.factory_env_domain(fid)
    assert (
        env_urls.safe_env_origin(f"https://app-1.{DOMAIN}/x", domain) == f"https://app-1.{DOMAIN}"
    )
    assert env_urls.safe_env_origin("https://app-1.envs.deploy.test/", domain) is None
    assert env_url_error("app", "https://app-1.envs.deploy.test/", domain) == (
        f"must be https://<host>.{DOMAIN}/…"
    )


def test_no_domain_anywhere_accepts_no_env_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(env_urls.ENV_DOMAIN_ENV, raising=False)
    monkeypatch.delenv(env_urls.LEGACY_ENV_DOMAIN_ENV, raising=False)
    assert env_urls.env_domain() == ""
    assert env_urls.safe_env_origin("https://app-1.anything.test/") is None
    assert env_urls.safe_env_origin("https://app./") is None
    assert "no preview domain is set" in (env_url_error("app", "https://app-1.x.test/") or "")


def test_the_stable_preview_link_follows_the_profile(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.entities.factory import FactoryPr

    monkeypatch.setenv(env_urls.ENV_DOMAIN_ENV, "envs.deploy.test")
    _set(factory, {"product.delivery.preview_domain": DOMAIN})
    definition.invalidate()
    pr = FactoryPr(
        id="pr_1",
        factory_id=_fid(factory),
        repo=APP,
        number=7,
        url=f"https://github.com/{APP}/pull/7",
        preview={"state": "ready", "urls": {"app": f"https://app-p7.{DOMAIN}"}},
    )
    assert preview_redirect_target(pr) == f"https://app-p7.{DOMAIN}/"
    other = {"state": "ready", "urls": {"app": "https://app-p7.envs.deploy.test"}}
    assert "preview=unavailable" in preview_redirect_target(replace(pr, preview=other))


# ── The guards ───────────────────────────────────────────────────────────────


def _decide(**params: Any) -> dict[str, Any]:
    command = params.pop("command")
    event = {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": command}}}
    return guard.factory_role_guard(role=F.ROLE_IC, **params)(event, {})


def test_the_guard_builds_in_no_product_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    assert guard.DEFAULT_WRITE_REPOS == () and guard.DEFAULT_DENY_REPOS == ()
    # Outside a factory a spec that names no repo writes none.
    assert _decide(command=f"gh pr create -R {APP} --fill")["result"] == "DENY"
    assert _decide(command=f"gh pr create -R {APP} --fill", write_repos=[APP])["result"] == (
        "ALLOW"
    )


def test_the_always_deny_list_is_server_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(guard.ALWAYS_DENY_ENV, "Acme/Infra, https://github.com/acme/vault ")
    assert guard._always_deny_from_env() == ("acme/infra", "acme/vault")
    monkeypatch.setattr(guard, "_ALWAYS_DENY_REPOS", guard._always_deny_from_env())
    # Denied even when a project or a spec names it.
    denied = _decide(command="gh pr create -R acme/infra --fill", factory_repos=["acme/infra"])
    assert denied["result"] == "DENY" and "never write to" in denied["reason"]
    monkeypatch.delenv(guard.ALWAYS_DENY_ENV)
    assert guard._always_deny_from_env() == ()


def test_protected_repos_are_denied_unless_they_are_the_projects() -> None:
    params = {"factory_repos": [APP], "factory_protected_repos": ["acme/infra", APP]}
    assert _decide(command=f"gh pr create -R {APP} --fill", **params)["result"] == "ALLOW"
    denied = _decide(command="gh pr comment https://github.com/acme/infra/pull/1 -b x", **params)
    assert denied["result"] == "DENY"
    assert "acme/infra, which factory agents never write to" in denied["reason"]


def _guard_spec(role: str) -> AgentSpec:
    return AgentSpec(
        spec_version=1,
        name="factory-test",
        guardrails=GuardrailsSpec(
            policies=[
                FunctionPolicySpec(
                    name="factory_guard",
                    on=None,
                    function=FunctionRef(
                        path="omnigent.policies.builtins.factory.factory_role_guard",
                        arguments={"role": role, "push_ref_prefixes": ["factory/"]},
                    ),
                )
            ]
        ),
    )


async def _engine_verdict(world: FactoryWorld, key: str, role: str, command: str) -> str:
    engine = build_policy_engine(
        spec=_guard_spec(role), conversation_id=world.id(key), conversation_store=world.store
    )
    result = await engine.evaluate(
        EvaluationContext(
            phase=Phase.TOOL_CALL,
            tool_name="Bash",
            content={"name": "Bash", "arguments": {"command": command}},
        )
    )
    return "DENY" if result.action == PolicyAction.DENY else "ALLOW"


async def test_the_server_passes_the_profiles_protected_repos_to_the_guards(
    factory: FactoryWorld,
) -> None:
    store = factory.deps.factory_store
    store.upsert_project(_fid(factory), "api-version", title="api", repo=APP, created_by="alice")
    store.upsert_project(
        _fid(factory), "infra-docs", title="docs", repo="acme/platform", created_by="alice"
    )
    _set(factory, {"product.delivery.protected_repos": ["acme/platform", "acme/infra"]})
    definition.invalidate()
    resolve = guard_repos.guard_arguments(factory.deps)
    lead = factory.get("lead_a")
    assert resolve(lead, lead) == {
        "factory_repos": ["acme/app", "acme/platform"],
        "factory_protected_repos": ["acme/infra", "acme/platform"],
    }
    uninstall = guard_repos.install(factory.app_state)
    try:
        # A lead comments on any project's repo, the protected one a human made a project's too.
        for repo in (APP, "acme/platform"):
            command = f"gh pr comment -R {repo} 7 -b ok"
            assert await _engine_verdict(factory, "lead_a", F.ROLE_LEAD, command) == "ALLOW"
        command = "gh pr comment -R acme/infra 7 -b ok"
        assert await _engine_verdict(factory, "lead_a", F.ROLE_LEAD, command) == "DENY"
        # The api PL writes its own repo only.
        for repo, verdict in ((APP, "ALLOW"), ("acme/platform", "DENY")):
            command = f"gh pr create -R {repo} --fill"
            assert await _engine_verdict(factory, "pl", F.ROLE_PROJECT_LEAD, command) == verdict
    finally:
        uninstall()


async def test_a_project_on_a_protected_repo_needs_an_owner(factory: FactoryWorld) -> None:
    from omnigent.server.factory import proposals

    store = factory.deps.factory_store
    store.upsert_project(_fid(factory), "api-version", title="api", repo=APP, created_by="alice")
    assert proposals._protected_repos(factory.deps, _fid(factory)) == frozenset()
    _set(factory, {"product.delivery.protected_repos": [APP]})
    definition.invalidate()
    assert proposals._protected_repos(factory.deps, _fid(factory)) == {APP}


# ── The snapshot ─────────────────────────────────────────────────────────────


async def test_the_snapshot_carries_the_product_profile(factory: FactoryWorld) -> None:
    from omnigent.server.factory.snapshot import build_snapshot

    _set(
        factory, {"product.name": "Sample app (sample)", "product.delivery.demo_paths": ["web/**"]}
    )
    definition.invalidate()
    raw = await build_snapshot(factory.app_state, _root(factory))
    product = raw.body["factory"]["product"]
    assert product["name"] == "Sample app (sample)"
    assert product["delivery"]["demo_paths"] == ["web/**"]
    assert product["delivery"]["merge_policy"] is None
