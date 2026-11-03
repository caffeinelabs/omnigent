---
name: discovery-synthesis
description: Scout's weekly synthesis - read the opportunities, the unsorted signals and people's calibration, then in one batch merge, split, rephrase and create opportunities (job form, no solution, two alternatives, cited signals), draft bet notes (three solutions, riskiest assumption, cheapest test, metric) for the validated ones and a cheapest test for the top hunches, note coverage gaps, and post the digest in #discovery. Runs on "[Factory discovery · synthesis".
---

# The weekly synthesis

This is where the opportunity list changes. Do it in one pass, then post one
digest. The server then offers Atlas what is ready to plan.

## 1. Read

- `sys_discovery_read(kind="brief")`: the owners' brief, the rubric numbers,
  the thresholds and today's spend. Follow the brief.
- `sys_discovery_read(kind="strategy")`: goals, non-goals, focus segments.
- `sys_discovery_read(kind="calibration")`: how people corrected you since the
  last pass (merges, splits, renames, moves, detaches, kills with reasons).
  Do the same kind of thing they did; never undo it.
- `sys_discovery_read(kind="opportunities")` and
  `sys_discovery_read(kind="unsorted", window="7d")`.
- Read an opportunity's signals only when you change it:
  `sys_discovery_read(kind="opportunity", ids=["O-3"])`.

## 2. Shape the list

- **Create** an opportunity for each cluster of unsorted signals that shows a
  need, even a single strong one:
  `sys_discovery_opportunity(op="create", title="…", statement="When I …, I want …, so I can …", problem="…", goal="G-1", segments=["…"], area="…", alternatives=["…", "…", "…"], signals=[{signal: "S-41"}, {signal: "S-57", counter: true}])`.
  The statement is the user's need, never a feature ("When I hand work to a
  colleague…", not "Add a handover button"); `alternatives` are at least two different ways
  to meet it. Place it under a goal when it serves one; leave `goal` out when
  none fits (people see it as unassigned).
- **Merge** two that are one need: `sys_discovery_opportunity(op="merge", id="O-7", into="O-3", reason="…")`.
- **Split** one that hides two needs: `sys_discovery_opportunity(op="split", id="O-3", parts=[{title: "…", statement: "When I …", signals: ["S-41", "S-44"]}, …], reason="…")`.
- **Rephrase** when the evidence says the need differently: `op="rephrase"`.
- Attach the rest of the unsorted signals (`sys_discovery_code`) or leave them.
- Never touch a parked, killed or merged opportunity, and never re-create one
  people killed: its evidence must rise first, and the server checks that.

## 3. Bet notes

For every validated opportunity without a current note, and for the top
`test first` ones (high value, low evidence: hunches included):
`sys_discovery_opportunity(op="bet_note", id="O-3", bet_note={solutions: ["…", "…", "…"], riskiest_assumption: "…", cheapest_test: "…", metric: "…"})`.
- At least three different solutions, in a sentence each.
- The riskiest assumption: what must be true for the best solution to work.
- The cheapest test, cheapest first: your own desk research (a query, no
  project), a prototype in a preview environment, a fake door.
- The metric: a goal's metric when it has one, else `signal_trend:O-3`.
A hunch with value gets a cheapest test, never an outcome bet.

## 4. Coverage

For each focus segment, how many signals arrived in 30 days
(`sys_discovery_read(kind="summary")`). A segment you have not heard from is a
gap: name it in the digest, and if a source could reach it, propose that source
(`sys_factory_propose(kind="tuning", changes=[{op: "add", path: "discovery.sources", value: {…}}], …)`)
with what it would read and why. Owners decide; never assume they will.

## 5. Digest

One post, at most 12 lines, ids and titles, no quotes and no account details:
`sys_factory_chat(thread="discovery", message="Weekly synthesis: 3 new (O-21, O-22, O-23), O-7 merged into O-3, bet notes on O-3 and O-12. Ready to plan: O-3 (moderate), O-12 (moderate). Not hearing from: <segment>. Spent $<x> of $<y> today.")`.
Then update `~/factory/scout.md` and your beacon.
