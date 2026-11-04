"""
A ``depends_on`` item may name one change (``PR-9876``) instead of a project.

It is met once that pull request merged, whatever the rest of its project still
does; the charter keeps it, its reason rides along, the waits and the gate read
it, and it is never part of a project cycle. Every name is sample data.
"""

from __future__ import annotations

from typing import Any

import pytest

from omnigent.entities.factory import FactoryPr, FactoryProject, FactoryTask
from omnigent.server.factory import gating, waits
from omnigent.server.factory.charter import change_dependency, normalize_charter
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.projects import check_dependencies
from omnigent.server.factory.start import CharterPatchIn

FID = "f" * 32
REPO = "sample-org/sample-app"


def _row(slug: str, **charter: Any) -> FactoryProject:
    return FactoryProject(
        id=slug.ljust(32, "0")[:32],
        factory_id=FID,
        slug=slug,
        title=slug,
        repo=REPO,
        charter={"goal": f"Build {slug}.", **charter},
    )


def _pr(number: int, state: str, slug: str = "core") -> FactoryPr:
    return FactoryPr(
        id=f"{number:032x}",
        factory_id=FID,
        repo=REPO,
        number=number,
        url=f"https://github.com/{REPO}/pull/{number}",
        project_slug=slug,
        state=state,
    )


def _open_task(slug: str) -> FactoryTask:
    return FactoryTask(
        id="9" * 32,
        factory_id=FID,
        number=1,
        project_slug=slug,
        key="cache",
        title="Cache",
        state="ready",
    )


def test_a_change_item_is_kept_with_its_reason() -> None:
    charter = normalize_charter(
        {
            "goal": "g",
            "depends_on": ["core", "PR-12", "PR-0", "PR 12", "PR-12x"],
            "depends_why": {"PR-12": "its route"},
        }
    )
    patch = CharterPatchIn.model_validate({"depends_on": ["PR-12: the route it calls"]})

    assert charter["depends_on"] == ["core", "PR-12"]
    assert charter["depends_why"] == {"PR-12": "its route"}
    assert patch.depends_on == ["PR-12"] and patch.depends_why == {"PR-12": "the route it calls"}
    assert (change_dependency("PR-12"), change_dependency("core")) == (12, None)
    with pytest.raises(ValueError):
        CharterPatchIn.model_validate({"depends_on": ["PR-12x"]})


def test_the_gate_waits_until_the_change_merged_not_its_project() -> None:
    core, panel = _row("core"), _row("panel", depends_on=["PR-12"])
    projects = {"core": core, "panel": panel}
    work = [_open_task("core")]

    waiting = gating.unmet_dependencies(panel, projects, [_pr(12, "open")], work)
    met = gating.unmet_dependencies(panel, projects, [_pr(12, "merged")], work)
    on_core = gating.unmet_dependencies(
        _row("panel", depends_on=["core"]), projects, [_pr(12, "merged")], work
    )

    assert waiting == ["PR-12"]
    # Its project still works (an open task), yet the change it needs is in.
    assert met == []
    assert on_core == ["core"]


def test_the_waits_name_the_change() -> None:
    panel = _row("panel", depends_on=["PR-12"], depends_why={"PR-12": "the route it calls"})
    facts = waits.Facts(projects={"panel": panel}, prs=[_pr(12, "open")])

    found = waits.dependency_waits(panel, facts)

    assert [(w.kind, w.ref, w.label) for w in found] == [
        ("dependency", "PR-12", "waiting on PR-12 to merge (the route it calls)")
    ]
    assert waits.dependency_waits(panel, waits.Facts(prs=[_pr(12, "merged")])) == []


def test_a_change_item_must_name_a_tracked_pull_request() -> None:
    rows = [_row("core"), _row("panel")]

    check_dependencies(rows, {"panel": (["PR-12"], "depends_on")}, prs=[_pr(12, "open")])
    with pytest.raises(CockpitError) as missing:
        check_dependencies(rows, {"panel": (["PR-13"], "depends_on")}, prs=[_pr(12, "open")])

    assert missing.value.code == "unknown_dependency"
