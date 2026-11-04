"""The lead and PL bundles teach decide first: decide what you may, record it, ask the rest.

Text only: no new policy path, no new skill. The protocol (one text in both
bundles) names the tool, the fallback for a host without it, Tier 0 and the
change-of-direction text; the lead's dispatch and both playbooks say what to
do with ``[Factory decision · K-<n> changed by …]``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2] / "examples" / "factory"
PROTOCOLS = [
    ROOT / "factory-lead" / "skills" / "factory-protocol" / "SKILL.md",
    ROOT / "factory-pl" / "skills" / "factory-protocol" / "SKILL.md",
]


def _decide_first(text: str) -> str:
    start = text.index("## Decide first")
    return text[start : text.index("\n## ", start + 1)]


def test_the_protocol_teaches_decide_first() -> None:
    lead, pl = (p.read_text(encoding="utf-8") for p in PROTOCOLS)
    section = _decide_first(lead)

    assert lead == pl
    assert "sys_factory_decide(question=" in section
    assert "Where your tools have no `sys_factory_decide`" in section
    assert "recommended" in section and "Tier 0" in section
    assert '`reply_to="K-<n>"`' in section
    assert "[Factory decision · K-<n> changed by <human> …]" in section
    assert "Re-plan from that checkpoint" in section
    assert "`[Factory decision · D-<n> answered by the factory" in lead


def test_the_leads_and_pls_act_on_a_change_of_direction() -> None:
    lead = yaml.safe_load((ROOT / "factory-lead" / "config.yaml").read_text(encoding="utf-8"))
    pl = yaml.safe_load((ROOT / "factory-pl" / "config.yaml").read_text(encoding="utf-8"))
    lead_book = (
        ROOT / "factory-lead" / "skills" / "factory-lead-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")
    pl_book = (ROOT / "factory-pl" / "skills" / "factory-pl-playbook" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "`[Factory decision · K-<n> changed by <human> …]`" in lead["prompt"]
    assert "Decide first" in lead["prompt"] and "sys_factory_decide" in lead["prompt"]
    assert "sys_factory_decide" in pl["prompt"]
    for book in (lead_book, pl_book):
        assert '| `[Factory decision · K-<n> changed by <human> · "<option>"]` |' in book


def test_no_bundle_gains_a_policy_path() -> None:
    for bundle in ("factory-lead", "factory-pl"):
        text = (ROOT / bundle / "config.yaml").read_text(encoding="utf-8")
        assert "decide_policy" not in text and "decide_first" not in text
