"""Release detection: readout days count from the release the factory's profile names.

``product.delivery.release`` is ``merged`` (all the bet's projects are out),
``github_release`` / ``github_deployment:<env>`` (then a release or a
successful deployment of their repos, read on the server with the owner's
connection) or ``manual`` (a person says so). Every product word is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.factory import definition
from omnigent.server.factory.background import discovery_readouts as job
from omnigent.server.factory.discovery import readouts
from omnigent.server.github_app_client import graphql_operations
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import DAY, discovery_cockpit
from tests.server.factory.readout_helpers import (
    REPO,
    base,
    file_bet,
    now,
    read_sources,
    seeded,
)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    readouts._last_poll.clear()
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value
    readouts.set_release_reader(None)


def mode(cockpit: Cockpit, value: str) -> None:
    definition.set_values(
        cockpit.deps, cockpit.fid, {"product.delivery.release": value}, created_by="alice"
    )
    definition.invalidate(cockpit.fid)


def merged_pr(cockpit: Cockpit, number: int, slug: str, at: int) -> None:
    cockpit.store.upsert_pr(
        cockpit.fid, REPO, number, state="merged", project_slug=slug, merged_at=at
    )


async def detect(cockpit: Cockpit, at: int | None = None) -> list[Any]:
    return await readouts.detect_releases(cockpit.app.state, cockpit.fid, now=at or now())


class FakeReader:
    """Answers release lookups from a list; records each call."""

    def __init__(self, answer: int | None = None, fail: bool = False) -> None:
        self.answer = answer
        self.fail = fail
        self.calls: list[tuple[list[str], str, int]] = []

    async def released_after(
        self, factory_id: str, repos: Sequence[str], mode: str, after: int
    ) -> int | None:
        self.calls.append((list(repos), mode, after))
        if self.fail:
            raise RuntimeError("GitHub is down (sample)")
        return self.answer


# ── merged ─────────────────────────────────────────────────────


async def test_merged_waits_for_every_project_then_counts_from_the_last_merge(
    cockpit: Cockpit,
) -> None:
    seeded(cockpit)
    file_bet(cockpit, slugs=("draft-share", "draft-share-api"), stage="building")
    cockpit.store.upsert_project(cockpit.fid, "draft-share", stage="merged")
    t = now()
    merged_pr(cockpit, 101, "draft-share", t - 3 * DAY)
    assert await detect(cockpit) == []

    cockpit.store.upsert_project(cockpit.fid, "draft-share-api", stage="merged")
    merged_pr(cockpit, 102, "draft-share-api", t - 2 * DAY)
    rows = await detect(cockpit)

    assert [(r.day, r.released_at, r.due_at) for r in rows] == [
        (7, t - 2 * DAY, t + 5 * DAY),
        (28, t - 2 * DAY, t + 26 * DAY),
    ]
    assert rows[0].result["release"]["how"] == "merged"
    assert rows[0].stamp["origin"] == "discovered"
    [event] = cockpit.events("readout.scheduled")
    assert event.data["readouts"] == ["C-1-d7", "C-1-d28"]
    assert await detect(cockpit) == []


async def test_cancelled_projects_are_left_out_and_done_counts_as_out(cockpit: Cockpit) -> None:
    seeded(cockpit)
    file_bet(cockpit, slugs=("draft-share", "draft-share-old"), stage="building")
    cockpit.store.upsert_project(cockpit.fid, "draft-share-old", status="cancelled")
    cockpit.store.upsert_project(cockpit.fid, "draft-share", stage="done")

    rows = await detect(cockpit)

    assert [r.day for r in rows] == [7, 28]


async def test_nothing_out_reads_no_pull_requests(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded(cockpit)
    file_bet(cockpit, stage="building")
    file_bet(cockpit, slugs=("draft-old",))
    cockpit.store.upsert_project(cockpit.fid, "draft-old", status="cancelled")

    def no_prs(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("nothing is out: no pull request is read")

    monkeypatch.setattr(cockpit.store, "list_prs", no_prs)

    assert await detect(cockpit) == []
    pending = {p["proposal"]: p["state"] for p in readouts.pending_bets(cockpit.deps, cockpit.fid)}
    # A bet whose every project was cancelled will never ship.
    assert pending == {"C-1": "building", "C-2": "cancelled"}


async def test_only_applied_bets_with_a_frozen_plan_are_measured(cockpit: Cockpit) -> None:
    seeded(cockpit)
    row, _ = file_bet(cockpit)
    cockpit.store.update_proposal(row.id, status="undone")
    cockpit.store.upsert_project(
        cockpit.fid,
        "requested-work",
        title="Requested (sample)",
        charter={"goal": "x"},
        stage="merged",
    )

    assert await detect(cockpit) == []
    assert readouts.bets(cockpit.deps, cockpit.fid) == []


# ── manual ─────────────────────────────────────────────────────


async def test_manual_waits_for_a_person(cockpit: Cockpit) -> None:
    seeded(cockpit)
    mode(cockpit, "manual")
    file_bet(cockpit)
    assert await detect(cockpit) == []

    early = await cockpit.post(
        base(cockpit, "/readouts/release"), {"proposal": "C-1", "at": now() + DAY}
    )
    nameless = await cockpit.post(base(cockpit, "/readouts/release"), {})
    viewer = await cockpit.post(
        base(cockpit, "/readouts/release"), {"proposal": "C-1"}, user="bob"
    )
    unknown = await cockpit.post(base(cockpit, "/readouts/release"), {"proposal": "C-9"})
    resp = await cockpit.post(
        base(cockpit, "/readouts/release"),
        {"project": "#draft-share", "note": "shipped to everyone today (sample)"},
    )
    again = await cockpit.post(base(cockpit, "/readouts/release"), {"proposal": "C-1"})

    assert early.status_code == 422 and error_code(early) == "release_before_apply"
    assert nameless.status_code == 400
    assert viewer.status_code in (403, 404)
    assert unknown.status_code == 404 and error_code(unknown) == "unknown_bet"
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["proposal"] == "C-1" and [r["day"] for r in body["readouts"]] == [7, 28]
    assert body["readouts"][0]["release"]["how"] == "manual"
    assert body["readouts"][0]["release"]["by"].startswith("alice")
    assert body["readouts"][0]["release"]["note"] == "shipped to everyone today (sample)"
    [event] = cockpit.events("readout.scheduled")
    assert event.data["note"] == "shipped to everyone today (sample)"
    assert again.status_code == 409 and error_code(again) == "already_released"


# ── GitHub ─────────────────────────────────────────────────────


async def test_a_github_release_after_the_merge_is_the_release(cockpit: Cockpit) -> None:
    seeded(cockpit)
    mode(cockpit, "github_release")
    file_bet(cockpit)
    t = now()
    merged_pr(cockpit, 101, "draft-share", t - 3 * DAY)
    reader = FakeReader(answer=None)
    readouts.set_release_reader(lambda _state: reader)

    assert await detect(cockpit, t) == []
    assert reader.calls == [([REPO], "github_release", t - 3 * DAY)]
    # Looked up again only after RELEASE_POLL_S.
    assert await detect(cockpit, t + 60) == []
    assert len(reader.calls) == 1

    reader.answer = t - DAY
    rows = await detect(cockpit, t + readouts.RELEASE_POLL_S)

    assert [(r.day, r.released_at) for r in rows] == [(7, t - DAY), (28, t - DAY)]
    assert rows[0].result["release"]["how"] == "github_release"


async def test_a_deployment_mode_names_its_environment_and_a_failure_waits(
    cockpit: Cockpit,
) -> None:
    seeded(cockpit)
    mode(cockpit, "github_deployment:production")
    file_bet(cockpit)
    reader = FakeReader(fail=True)
    readouts.set_release_reader(lambda _state: reader)

    assert await detect(cockpit) == []
    assert reader.calls[0][1] == "github_deployment:production"


async def test_the_github_reader_reads_releases_and_deployments_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, Mapping[str, Any]]] = []
    answers = {
        "releases": {
            "repository": {
                "releases": {
                    "nodes": [
                        {"publishedAt": "2026-10-20T10:00:00Z", "isDraft": False},
                        {"publishedAt": "2026-10-12T10:00:00Z", "isDraft": False},
                        {"publishedAt": "2026-10-11T10:00:00Z", "isDraft": True},
                        {"publishedAt": "2026-10-11T12:00:00Z", "isPrerelease": True},
                        {"publishedAt": "2026-10-01T10:00:00Z", "isDraft": False},
                    ]
                }
            }
        },
        "deployments": {
            "repository": {
                "deployments": {
                    "nodes": [
                        {
                            "latestStatus": {
                                "state": "FAILURE",
                                "createdAt": "2026-10-11T10:00:00Z",
                            }
                        },
                        {
                            "latestStatus": {
                                "state": "SUCCESS",
                                "createdAt": "2026-10-13T10:00:00Z",
                            }
                        },
                    ]
                }
            }
        },
    }

    class FakeGitHub:
        def __init__(self, _state: Any) -> None:
            pass

        async def graphql(
            self, factory_id: str, query: str, variables: Mapping[str, Any]
        ) -> dict[str, Any]:
            seen.append((query, dict(variables)))
            return answers["deployments" if "deployments" in query else "releases"]

    from omnigent.server.factory.discovery.kinds import repo

    monkeypatch.setattr(repo, "AppGitHubReader", FakeGitHub)
    reader = readouts.GitHubReleaseReader(object())
    merged = 1_791_000_000  # 2026-10-03

    released = await reader.released_after("f" * 32, [REPO], "github_release", merged)
    deployed = await reader.released_after(
        "f" * 32, [REPO], "github_deployment:production", merged
    )

    assert released == 1_791_799_200  # 2026-10-12T10:00Z: drafts and prereleases skipped
    assert deployed == 1_791_885_600  # 2026-10-13T10:00Z: only a success counts
    assert seen[1][1] == {"owner": "sample-org", "name": "sample-app", "env": ["production"]}
    for query, _variables in seen:
        assert graphql_operations(query) == ["query"]


# ── The background job ─────────────────────────────────────────


async def test_the_job_schedules_and_writes_for_open_factories_only(cockpit: Cockpit) -> None:
    seeded(cockpit, before_days=10)
    file_bet(cockpit)
    merged_pr(cockpit, 101, "draft-share", now() - 8 * DAY)
    read_sources(cockpit, now())

    first = await job.run_once(cockpit.app.state)

    assert first == {"scheduled": 2, "written": 1}
    cockpit.world.store.set_labels(cockpit.fid, {F.LABEL_STATE: F.STATE_DONE})
    assert await job.run_once(cockpit.app.state, now=now() + 30 * DAY) == {
        "scheduled": 0,
        "written": 0,
    }
