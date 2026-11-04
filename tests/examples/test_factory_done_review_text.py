"""The done review in the lead bundle: the dispatch, Sentinel's skill, the retro's count.

Text only: no new policy path in a bundle (an older runner that cannot import
one denies every tool call), and the reviewer can answer on an older host
through ``sys_factory_retro``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

LEAD = Path(__file__).resolve().parents[2] / "examples" / "factory" / "factory-lead"
SKILLS = LEAD / "skills"


def _text(*parts: str) -> str:
    return (SKILLS.joinpath(*parts)).read_text(encoding="utf-8")


def test_the_lead_dispatches_the_brief_and_the_verdict() -> None:
    prompt = yaml.safe_load((LEAD / "config.yaml").read_text(encoding="utf-8"))["prompt"]

    assert "`[Factory review · I-<n> review <k> · …] … is it truly done?` (Sentinel)" in prompt
    assert 'load_skill("factory-done-review")' in prompt and "`sys_factory_review`" in prompt
    assert "A feature is done only when a review passed or a person said so." in prompt


def test_the_lead_bundle_adds_no_policy_path() -> None:
    config = yaml.safe_load((LEAD / "config.yaml").read_text(encoding="utf-8"))
    paths = sorted(p["function"]["path"] for p in config["guardrails"]["policies"].values())

    assert paths == [
        "omnigent.policies.builtins.factory.factory_role_guard",
        "omnigent.policies.builtins.orchestration.blast_radius",
        "omnigent.policies.builtins.orchestration.spawn_bounds",
    ]


def test_sentinels_skill_returns_one_verdict_and_only_proposes() -> None:
    text = _text("factory-done-review", "SKILL.md")

    assert text.startswith("---\nname: factory-done-review\n")
    for needle in (
        'sys_factory_review(initiative="I-<n>", review=<k>, verdict="not_done"',
        'sys_factory_retro(kind="finding", target="review:I-<n>"',
        "exactly one `recommended`",
        "mock-first",
        "You only propose",
        "`next_wave`",
    ):
        assert needle in text, needle


def test_the_playbook_and_planning_know_the_review() -> None:
    playbook = _text("factory-lead-playbook", "SKILL.md")
    planning = _text("factory-planning", "SKILL.md")

    assert "## Sentinel: the done reviewer" in playbook
    assert 'load_skill("factory-done-review")' in playbook
    assert "A feature reads\ndone only once a review passes." in planning


def test_the_retro_counts_reopened_done() -> None:
    text = _text("factory-retro", "SKILL.md")

    assert "`done_reopened`" in text and "done_review.rubric" in text
