---
name: factory-plan-analysis
description: The planner's procedure - a fresh read-only worktree of origin/<base>, finding the areas a goal touches, drafting projects with scope_paths, running scope-graph.mjs (cohesion, cross-scope edges, hub files, overlaps), applying the split heuristics, sizing each project, and writing the {changes, analysis} JSON for the lead's sys_factory_project call.
---

# Plan analysis

Input: `PLAN id=<id> repo=<owner/name> base=<branch> script=<path> goal=<one line>`,
then the human's ask and the existing projects with their `scope_paths`.

## 1. A fresh worktree of the base

```bash
ID=<id>; REPO=<owner/name>; BASE=<branch>
SRC=~/factory/plans/src/${REPO##*/}
test -d "$SRC/.git" || gh repo clone "$REPO" "$SRC" -- --filter=blob:none --no-checkout
git -C "$SRC" fetch -q origin "$BASE"
git -C "$SRC" worktree add --detach ~/factory/plans/wt/$ID "origin/$BASE"
git -C ~/factory/plans/wt/$ID rev-parse HEAD
```

Read `AGENTS.md` (and `agents/factory.md` if present), the top-level layout,
and find what the goal touches: `rg -l '<keyword>' ~/factory/plans/wt/$ID`
for the domain words, routes, tables and UI strings in the ask.

## 2. Draft, measure, split

1. Draft candidate projects: slug (kebab-case), goal, observable DoD items,
   `scope_paths` globs (directories or `**` globs, no overlaps with each
   other or with the existing projects), `demo_focus`.
2. Measure them, one `--scope` per candidate:

```bash
node <script> --root ~/factory/plans/wt/$ID \
  --scope db-version='services/api/src/db/**' --scope settings-row='web/src/settings/**' \
  --out ~/factory/plans/$ID.graph.json
```

3. Read the report and split by it:
   - `cohesion` near 1 and few `cross_scope` edges: the split holds.
   - Low cohesion, or many edges both ways between two scopes: merge them.
     Tightly coupled code stays in one project.
   - `hubs` and `shared_outside_scopes` (files several scopes import): if the
     goal changes them, make a groundwork project the others `depends_on`.
   - `overlaps`: tighten the globs until there are none.
4. Size each project: files likely touched, PRs (about 3 or fewer), and
   `S`/`M`/`L`. Set `budget_ics` (1-3) and `priority` (0 highest).

## 3. Write the plan

Write `~/factory/plans/<id>.json` (one file, valid JSON):

```json
{"changes": [{"op": "create", "project": {"slug": "…", "title": "…", "goal": "…",
  "dod": ["…"], "scope_paths": ["…"], "demo_focus": "…", "depends_on": [],
  "budget_ics": 2, "priority": 1}}],
 "analysis": {"head": "<sha>", "scope_graph": <the report>, "hub_files": ["…"],
  "overlaps": [], "sizes": {"<slug>": "S · 1 PR · 4 files"}, "notes": ["…"]}}
```

Check it with `node -e 'JSON.parse(require("fs").readFileSync(process.argv[1]))' ~/factory/plans/$ID.json`.
Leave the worktree for the lead to inspect. End with exactly one line:
`PLAN READY ~/factory/plans/<id>.json · <n> projects · <one-line rationale>`,
or `PLAN FAILED <reason>` (repo unreadable, goal unclear: say what you need).
