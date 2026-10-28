---
name: factory-planning
description: How Atlas turns work asked for in the Room into factory projects - when to plan alone and when to dispatch the read-only planner sub-agent, the splitting heuristics, the planner's {changes, analysis} JSON contract with the scope-graph.mjs import-graph report, sys_factory_project(reply_to=…, analysis_file=…), autonomy, and the one-line answer in the thread.
---

# Planning work into projects

Load this when a `[Factory chat` (or a brief) asks for work. The ask is the
human's message id `chat_<id>`; everything you propose answers it.

## 1. Restate and check

Restate the goal in one line in the ledger. Call `sys_factory_project()` (no
arguments) to list the projects and pending proposals: extend or update an
existing project instead of creating a twin.

## 2. Plan alone or dispatch the planner

- One reviewable outcome in one area (about one PR): propose it yourself (step 4).
- Anything bigger: answer the thread first (`sys_factory_chat(thread="factory", reply_to="chat_<id>", message="Planning: <goal>…")`),
  then dispatch the read-only planner and end the turn; its completion wakes you:
  `sys_session_send(agent="planner", title="plan-<id>", args={input: "PLAN id=<id> repo=<owner/name> base=<default base> script=~/factory/bin/scope-graph.mjs goal=<one line>\n<the ask, quoted>\n<existing projects and their scope_paths>"})`.
  Use the first 8 characters of the chat id as `<id>`. Copy the script first:

```bash
SRC=$(dirname "$(find "${TMPDIR:-/tmp}" /tmp -path '*/skills/factory-planning/scope-graph.mjs' \
  -exec ls -t {} + 2>/dev/null | head -n1)")
mkdir -p ~/factory/bin ~/factory/plans && cp "$SRC/scope-graph.mjs" ~/factory/bin/scope-graph.mjs
node ~/factory/bin/scope-graph.mjs --selftest
```

(If Claude Code showed this skill's base directory, use `SRC=<base>`.)

## 3. Splitting heuristics (yours and the planner's)

- One project per independently reviewable and previewable outcome; about 3
  PRs or fewer per project. Split by repo.
- No two active projects on the same `repo#base` with overlapping `scope_paths`.
- Shared groundwork (hub files several scopes import) is its own project; the
  others `depends_on` it (`depends_mode: merged` unless stacking is asked for).
- Tightly coupled code stays in ONE project: low cohesion or many cross-scope
  edges in the analysis mean merge, not split. Parallel PLs pay only when
  coupling is low; every extra PL costs about 15x the chat tokens.
- Every DoD item has an observable check, and every project a `demo_focus`.

## 4. The contract and the proposal

The planner writes `~/factory/plans/<id>.json` and ends with
`PLAN READY ~/factory/plans/<id>.json · <n> projects` (or `PLAN FAILED <why>`):

```json
{"changes": [{"op": "create", "project": {"slug": "db-version", "title": "…", "goal": "…",
  "dod": ["…"], "scope_paths": ["services/api/src/db/**"], "demo_focus": "…",
  "depends_on": [], "budget_ics": 2, "priority": 1}}],
 "analysis": {"head": "<base sha>", "scope_graph": {"…": "scope-graph.mjs output"},
  "hub_files": ["…"], "overlaps": [], "sizes": {"db-version": "S · 1 PR · 4 files"},
  "notes": ["…"]}}
```

Read it, fix anything that breaks the heuristics, then file it as ONE proposal:
`sys_factory_project(changes=<its changes>, rationale=<why this split, 2-4 lines>, thread="factory", reply_to="chat_<id>", analysis_file="~/factory/plans/<id>.json")`.
A field error names its path (`changes[1].project.slug`): fix and call again.
`PLAN FAILED`: propose what you can yourself, or ask in the thread.

**Autonomy.** With `reply_to` a human's recent message, creates inside the
caps auto-apply with Undo and the server spawns each PL (dependencies wait).
Splits, merges, cancels and anything you start on your own wait for a human
on the proposal's card: never ask twice.

## 5. Answer in one line

`sys_factory_chat(thread="factory", reply_to="chat_<id>", message="Proposed 3 projects → C-4: db-version, api-version, settings-row waits on both")`.
The C-n card in the thread shows the plan, its depends_on and the analysis.
