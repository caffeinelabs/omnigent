---
name: factory-retro
description: Sentinel's retro (rule S12, daily and at each project done) - error analysis over sys_factory_insights, open and axial coding onto the seeded taxonomy, ranking by count x cost, dedupe against open proposals, at most 2 sys_factory_propose per day with evidence and one expected metric, the metric to lever map, the #improvements digest with sys_factory_chat, and the co-sign duty.
---

# Factory retro (Sentinel)

`[Factory heartbeat · retro · window <w> · budget $<n>]` starts it. Stay within the
budget: read, code, decide, post. Never touch Tier 0 (guards, gates, policies,
metric definitions, secrets): the server refuses it with `tier0`.

## 1. Read
- `sys_factory_insights(kind="kpis", window="24h")`: every number carries n; small n is noise.
- `sys_factory_insights(kind="findings")`: open findings, new retros, and the humans'
  calibration of your past labels. Treat confirmed and corrected labels as examples.
- `sys_factory_insights(kind="loops")` for stuck loops; `kind="metrics", names="<a,b>"` to drill in.

## 2. Open coding
For each failure in the window (a PR closed without merge, an escalated or unnecessary
decision, an exhausted loop, an incident, a slow review), write one short label of what
went wrong, in your own words, with its evidence id (D-n, F-n, INC-n, !pr, T-n).

## 3. Axial coding
Map each label onto the taxonomy: charter_unclear, task_too_big, env_flaky, ci_flaky,
tool_error, prompt_gap, reviewer_miss, waiting_on_human, coordination, platform_bug.
File each pattern seen twice or more as a finding:
`sys_factory_retro(kind="finding", target="factory", root_causes=[{category, text, evidence}], proposals=[{area, change, metric}])`.

## 4. Rank and dedupe
Rank the categories by count × cost (`kind="metrics", names="cost_usd,wasted_usd"`).
Drop every one an open proposal already covers (your ledger's `C-n` lines).

## 5. Propose: at most 2 per day
| Signal | Lever |
|---|---|
| decision_latency high, unnecessary_questions | PL rule: decide reversible choices yourself; ask only for scope, data model or merge |
| review_rounds high | smaller cards (planning rule) plus a reviewer checklist rule |
| first_pass_yield low on verify or demo | env_keeper rule, or a finding for the humans (tooling) |
| heartbeat_share high | slower `cadence.project_lead` (class A) |
| claim_mismatch_rate high | PL rule: claim ready only when `sys_factory_pr` says ready |
| merge_acceptance low with wrong_scope closures | planning rule for Atlas |

`sys_factory_propose(kind="tuning"|"rules", changes=[…] or rule={role, project, text}, rationale="…", evidence=[ids], expected_effect={metric, direction})`.
Cheaper or safer numbers are class A: Atlas co-signs and it runs as a trial. Rules and
money go to the owners. Never aim at interventions = 0 alone: the guard metrics must hold.

## 6. Digest
One post: `sys_factory_chat(thread="improvements", message="…")`, at most 12 lines: the top
findings with counts and ids, the proposals you filed, and the trials kept or reverted.
Cite ids; never paste tables. Then record the retro in your ledger.

## Co-sign duty (both leads)
`[Factory proposal · C-<n> · co-sign requested by <lead>]`: check its evidence with
`sys_factory_insights`; co-sign with `sys_factory_propose(op="cosign", kind=<kind>, id="C-<n>")`
when it holds and is cheaper or safer, else tell that lead why not. Never your own.
