---
name: factory-plan-analysis
description: The planner's procedure - a fresh read-only worktree of origin/<base>, finding the areas a goal touches, drafting projects with scope_paths, running scope-graph.mjs (cohesion, cross-scope edges, hub files, overlaps), applying the split heuristics, sizing each project, a walking skeleton and acceptance steps for an ask bigger than one project, and writing the {changes, analysis, initiative} JSON for the lead's sys_factory_project call.
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
     A dependency is only for a real code or API one (the dependent imports,
     calls or builds on what the other adds or changes); write each item with
     its one-line reason: `"<slug>: <what it needs from it>"`.
   - `overlaps`: tighten the globs until there are none.
4. Size each project: files likely touched, PRs (about 3 or fewer), and
   `S`/`M`/`L`. Set `budget_ics` (1-3) and `priority` (0 highest).
5. More than one project (the request says `INITIATIVE`): it is one outcome.
   - Wave 1 is a walking skeleton: one project with a thin vertical slice
     from the user surface (the entry point a person sees) to the back end,
     behind the flag, that stubs what the others will build. It has no
     `depends_on` and priority 0.
   - The others run in parallel and integrate through the flag and a stable
     contract (write the contract into each goal); `depends_on` only for a
     real code or API dependency, with its reason (`"<slug>: <why>"`).
   - Write 2-8 acceptance steps a demo agent can run on the base env with a
     browser and the API: what a person does and what they see, each with
     the project whose code it exercises (`owner`) and `needs` (`login`,
     `build`, `ai`, `external`) when an env may lack it.

## 3. Write the plan

Write `~/factory/plans/<id>.json` (one file, valid JSON):

```json
{"changes": [{"op": "create", "project": {"slug": "…", "title": "…", "goal": "…",
  "dod": ["…"], "scope_paths": ["…"], "demo_focus": "…", "depends_on": [],
  "budget_ics": 2, "priority": 1}}],
 "analysis": {"head": "<sha>", "scope_graph": <the report>, "hub_files": ["…"],
  "overlaps": [], "sizes": {"<slug>": "S · 1 PR · 4 files"}, "notes": ["…"]},
 "initiative": {"title": "…", "outcome": "…", "flag": "<flag key>",
  "acceptance": [{"step": "…", "expect": "…", "owner": "<slug>", "needs": []}],
  "skeleton": "<slug>", "user_facing": ["<slug>"],
  "waves": [{"n": 2, "goal": "…", "projects": ["<planned slug>"]}]}}
```

Leave `initiative` out for a single project.

Check it with `node -e 'JSON.parse(require("fs").readFileSync(process.argv[1]))' ~/factory/plans/$ID.json`.
Leave the worktree for the lead to inspect. End with exactly one line:
`PLAN READY ~/factory/plans/<id>.json · <n> projects · <one-line rationale>`,
or `PLAN FAILED <reason>` (repo unreadable, goal unclear: say what you need).
