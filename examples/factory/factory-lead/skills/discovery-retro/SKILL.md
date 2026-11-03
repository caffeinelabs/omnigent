---
name: discovery-retro
description: Sentinel's monthly discovery retro - read kills and their reasons, declined, rejected and sent-back bets, readouts, quote refusals and coverage gaps, code what went wrong onto the discovery taxonomy, and propose at most 2 method changes (the rubric, thresholds, or Scout's brief) with evidence through sys_factory_propose; post the digest in #discovery. Runs on "[Factory discovery · retro".
---

# Discovery retro (Sentinel)

`[Factory discovery · retro · window <w> · budget $<n>]` starts it. Stay within
the budget. You judge the method (how the factory finds and ranks what users
need), never the product. Tier 0 is never on trial: provenance and quote
checks, redaction, the untrusted-text envelope, no outbound channel for agents
that read user text, frozen measurement plans.

## 1. Read

- `sys_discovery_read(kind="summary")`: the chain counts, coverage and spend.
- `sys_discovery_read(kind="calibration")`: kills with their seeded reasons
  (`off_strategy`, `weak_evidence`, `wrong_segment`, `too_costly`,
  `solved_elsewhere`, `harmful`, `duplicate`), and people's merges, splits,
  renames and detaches: each one is a place the method was wrong.
- `sys_discovery_read(kind="opportunities")` and the bets: declined ones (and
  why), rejected ones, ones you sent back, readouts (won, flat, lost,
  inconclusive) against the ranges they predicted.
- `sys_factory_insights(kind="findings")` for refused quotes and privacy events:
  both must stay at 0.

## 2. Code

Label each miss in a few words with its evidence id (`O-n`, `C-n`, `S-n`),
then map it onto: `weak_evidence_ranked_high`, `strong_evidence_ranked_low`,
`wrong_segment`, `loud_minority`, `stale_evidence`, `over_split`,
`over_merged`, `solution_not_need`, `quote_or_privacy`, `cost`.

## 3. Propose: at most 2

The levers are data in the definition, owners approve them:
- the rubric (`discovery.rubric.*`: ladder, minimum accounts and source types,
  window, decay half-life, counter-evidence share, reach ratio, intensity
  weights, off-focus factor, bands),
- the thresholds (`discovery.thresholds.*`),
- Scout's brief (`briefs.scout`).
`sys_factory_propose(kind="tuning", changes=[{op: "replace", path: "discovery.rubric.moderate_min_accounts", value: 6}], rationale="…", evidence=["O-…", "C-…"])`.
With fewer than 5 readouts per rubric version, say "not enough data" for
outcome claims and rest the case on people's corrections instead.

## 4. Digest

One post in `#discovery`, at most 10 lines: the misses by code with counts and
ids, the proposals you filed, and "not enough data" where n is small:
`sys_factory_chat(thread="discovery", message="…")`. Record the retro in your ledger.
