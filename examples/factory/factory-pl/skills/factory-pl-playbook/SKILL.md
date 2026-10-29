---
name: factory-pl-playbook
description: Project-lead playbook for the Omnigent software factory - plan.md and task-card templates (Preview and Demo focus), worktree and branch naming, parallel dispatch rules, the /goal convention, the review → factory preview → demo → ready-for-human-merge pipeline, the env_keeper and demo results table (preview failures, tooling conflicts, the preview cap), the server message table (reviews, decisions, PR and preview notices), the PR review-links block, questions through sys_factory_ask, the definition of done, milestone reports, and the pause and done procedures.
---

# PL playbook

## Base branch

`<base>` is the project's base branch: the charter's `Base branch:` line (or
the `#<branch>` of `Repo#branch:`), `main` when the charter names none, e.g.
`temp-main` for a test base. Every PR of the project targets it, and the dev
env tracks origin/<base>. Pass it on every dispatch, never assume `main`:
- env_keeper: `UP base=<base>`, `PREVIEW pr=<n> head=<sha> base=<base> …` and
  `MERGED pr=<n> base=<base>`;
- ICs and reviewers: the task card's `Base: <base>` line.

## Work graph (server truth)

The server owns the tasks (`T-<n>`), their dependencies and states
(planned → ready → running → review → done; blocked, failed, cancelled).
- KICKOFF: one task per reviewable change, from the DoD:
  `sys_factory_task(op="upsert", tasks=[...])`. `key` is the branch suffix;
  `deps` are task keys, `project:<slug>` or `decision:D-<n>`; parallel tasks'
  `scope` globs must not overlap (`scope_overlap` serializes them). A cycle
  or unknown dependency is refused: fix and resend.
- Dispatch only the wave of `sys_factory_task(op="ready")`: `start` the task,
  then send `title_to_use`, `card_text` and `cost_budget` verbatim. An unknown
  title becomes an ad-hoc task and a warning. Re-dispatches `start` again.
- A draft PR keeps its task running, out of draft it is in review, merged it
  is done. Close the rest (ad-hoc too) with `op="done"`, `fail` or `cancel`.
- A person's `[Factory control · TASK ADD|TASK EDIT …]` already changed the graph.

## plan.md (`~/factory/<slug>/plan.md`)

```
# <slug> · plan · updated <iso>
Charter: <goal> · DoD: <items> · Repo: caffeinelabs/<repo> · Base: <base> · Env needed: yes|no · Previews: on|off (slots <n>, tooling <ref>) · Budget: <n> ICs
Lead: <parent id> · Supervisor: <id> · Me: <id> · Cockpit: /factory/<factory id>
Dev env: <env_keeper session id> · <starting|READY|FAILED|DOWN> · <urls from ENV READY>

## Milestones
1. [ ] <milestone> (tasks: t1, t2)

## Task notes (states live on the server: sys_factory_task(op="list"))
| id | session | PR | head | review | preview | demo | handover | notes |
|----|---------|----|------|--------|---------|------|----------|-------|

`preview` is `-`, `p<n> <sha7>` or `parked`; `demo` is `-`, `PASS|PARTIAL|FAIL <sha7> <demo url>`;
`handover` is `-`, `ready <iso>` or `merged <sha7>`.

## Open questions (D-n, asked with sys_factory_ask)
## Handled mail ids (last 20)
## Log (newest first; prune to 30 lines)
```

## Task card (the IC's input; `card_text` renders it from the task)

```
TASK <id> · <title>
Project: <slug> · PL: <your session id>
Repo: <repo checkout, e.g. /home/omnigent/workspace/<repo>> · Base: <base>
Worktree: /home/omnigent/workspace/wt/<id> · Branch: factory/<slug>/<id>
Scope: <files/globs>; do not touch anything else
Do: <precise change>
Acceptance (run verbatim, all must pass): <commands>
Env checks (env_keeper runs these against the PR's factory preview): <curl {api}/... → expected> | none
Preview: yes|no
Demo focus: <the user-visible flow or API behaviour reviewers must see> | none
PR: draft early, repo template; Verification plan = the env checks; Demo plan = tagged steps for the demo focus; Factory provenance = project <slug>, PL <your id>, IC <its own id>, author vendor claude|codex; label factory:no-env if Preview: no
Report: the RESULT block
```

- `Base:` is the charter's base branch. The IC branches from origin/<base>,
  syncs with it and opens the PR with `--base <base>`.
- `Preview: yes` when the change is runnable: it touches `services/**`,
  `libs/**`, `generated/**` or `content/**`, and the charter has previews on.
  `no` for docs-only and tooling-only changes, and for every change on a repo
  other than caffeinelabs/app (the factory preview tooling exists only
  there); the IC labels the PR `factory:no-env` and it gets no preview and no
  demo.
- UX changes need a demo with video and screenshots; backend changes need API
  calls in the demo. Write the demo focus so a reviewer knows what to look at.
- Task ids are short kebab-case names, e.g. `version-route`. A re-dispatch is
  `<id>-r<k>` and reuses the same worktree, branch and PR. The branch
  `factory/<slug>/<id>` is how the server finds the PR; never rename it.
- Parallel-safe means no shared files and no ordering dependency. Run
  dependent tasks one after the other.
- Pick `ic` for multi-file work, refactors and test writing. Pick `ic_codex`
  for narrow, crisp cards and for vendor diversity.

## Dispatch rules

- KICKOFF sends everything in ONE response: env_keeper `UP base=<base>` (when the env is
  needed) and every parallel-safe IC. Several `sys_session_send` calls in one
  response run concurrently.
- `spawn_bounds` allows at most 8 dispatches per turn, and an answer to an IC
  counts as one. Send the rest in waves.
- Never run more concurrent ICs than the charter's budget (6 at most).
- Work that needs more ICs than `budget_ics` across milestones that do not
  depend on each other, or a milestone outside the charter's scope, is a
  split, not a longer queue. See Splitting the project.
- At most the charter's preview slots (default 2, max 3) of your PRs have a
  live preview at once. When all are taken, park the preview of a PR that is
  not ready (`PREVIEW_DOWN pr=<n> reason=human`) before deploying another.
- `/goal` mode is for long IMPLEMENT tasks: `args.input` is one standalone
  `"/goal <condition>"`, and `/goal` is the first token. Put the whole task
  card, the acceptance commands and the PR requirement inside the condition.
  Never use it for reviews or demos.
- Always dispatch with the task's `title_to_use` (`version-route`,
  `version-route-r1`); reviews and demos use `review-<key>` and `demo-<key>`.

## Pipeline: review → preview → demo → handover

1. **Register.** The moment an IC reports a PR: `sys_factory_pr(url=<url>, task=<id>)`.
   The cockpit shows it from then on (the server also finds `factory/<slug>/*`
   branches by itself).
2. **READY_FOR_REVIEW** (PR, head): re-run the acceptance commands in its
   worktree, then in ONE response:
   - cross-vendor review: `reviewer` for `ic` authors, `ic` with purpose
     "review" for `ic_codex` authors, with the PR URL, the head and the card.
     It posts `<!-- factory:review sha=<head> verdict=APPROVE|CHANGES reviewer=<id> -->`;
   - `Preview: yes`: env_keeper `PREVIEW pr=<n> head=<sha> base=<base> checks=[<env checks>]`
     (add `tooling=<ref>` only when the charter names a preview tooling ref
     other than `main`).
3. **PREVIEW READY** `pr=<n> sha=<sha7> app=<url> api=<url> secret_file=<path> · VERIFY PASS|FAIL`:
   env_keeper deployed the head, verified it and posted
   `<!-- factory:verify sha=<head> result=PASS|FAIL env=<api> preview=<app> -->`.
   Dispatch the demo for that head:
   `sys_session_send(agent="demo", title="demo-<task>", args={input: "DEMO pr=<url> sha=<40-char sha> app=<app> api=<api> secret_file=<path> focus=<demo focus>"})`,
   or `sys_session_send(session_id=<its id>, ...)` for a new head of the same PR.
   VERIFY FAIL is a product failure: send it to the author like review CHANGES.
4. **DEMO** results:
   - `DEMO PASS|PARTIAL … demo=<demo url>`: the demo comment is on the PR.
     Record it, then check readiness (step 5).
   - `DEMO FAIL`: send the author the failing step, what the demo saw and the
     demo link; a fix is a new head (step 6).
   - `DEMO BLOCKED stale-preview` or `head-moved`: env_keeper PREVIEW again at
     the current head, then DEMO.
   - `DEMO ERROR`: send DEMO once more; if it fails again, ask the human
     (`sys_factory_ask`) and say so in plan.md.
5. **Readiness.** Claim only when the required checks are green on the head
   (and its review, verify and demo passed there; CI can run 15+ min):
   `sys_factory_pr(url=<url>, claim="ready_for_human_merge", head_sha=<sha>)`.
   The server re-checks GitHub and answers `{ready, gates, reasons, claim_mismatch, claim_status}`
   (gates: draft, CI, review, verify, demo, mergeable, protected paths).
   - `ready: true`: hand over (step 7).
   - `claim_status: "early"`: gates are only pending (CI running, mergeable
     computing, a marker not posted yet). It uses no claim attempt and you get
     `[Factory loop · WAIT · …]`; do not claim again. Start any step `reasons`
     says is missing (review, PREVIEW, demo), then wait for the notice below.
     A gate that then fails at that head counts as a refuted claim; a wait past
     2 h stalls the claim loop (`[Factory loop · STALLED · …]`: re-run a hung check).
   - `claim_status: "refuted"` (a failed gate, a draft, a moved head): it counts
     toward the claim budget. Fix what `reasons` names: a draft (`gh pr ready` by
     the IC), failing CI (dispatch a fix), a failed marker (re-run that step).
   The server also sends `[Factory notice · PR … READY FOR HUMAN MERGE …]`
   when the gates turn green; treat it the same way.
6. **A new head** invalidates the review, verify and demo for the old one:
   repeat steps 2-5 for it. The preview is redeployed in place.
7. **Handover** (always to a human, on every repo):
   1. Write the review-links block into the PR body (step 8).
   2. Post ONE line to `parent` (kind=info):
      `READY FOR HUMAN MERGE <url> · head <sha7>`.
   3. `sys_factory_set(stage="ready")` and record `ready <iso>` in plan.md.
   Never merge (any repo) and never enable auto-merge or a merge queue. The
   IC stays idle on its worktree and the preview stays up until the PR merges
   or a human asks for changes. The merge gate only reports for you:
   `python -m omnigent.factory_merge --dry-run <pr-url>` (exit 2 when a gate
   fails); the guard denies it without `--dry-run`. Never add
   `factory:allow-protected` yourself.
8. **Review-links block**: see below. A `factory:no-env` PR gets the review
   link only.

### Engine hints (NEXT, READY, loops)

`[Factory engine · NEXT · …]` names a head's next step (never twice per head):
| NEXT step | Do |
|---|---|
| `REVIEW head=<sha>` | step 2: the cross-vendor review of that head |
| `PREVIEW pr=<n> head=<sha> base=<base>` | step 2: env_keeper PREVIEW with the card's checks |
| `DEMO pr=<url> sha=<sha> app=<url> api=<url>` | step 3: dispatch the demo (add `secret_file` and the focus) |

CI failures and readiness (steps 5-7) come as their own `[Factory notice · PR …]`.

In enforce mode `start` answers 409 `budget_exhausted` past the budget; a new
dispatch without a task title, or to `ic`/`demo` without `args.cost_budget`, is denied
(the card's; `demo-<key>`: `{max_cost_usd: <budget_left.usd>}` of `op="get"`).

### Review-links block

`sys_factory_pr` returns both links: `review_url` (the PR's cockpit page,
`<origin>/factory/<factory id>?review=<n>`) and `preview_url` (the stable
preview link, always the current deploy). Run this once the PR is ready (after
the first demo when it has a preview); running it again replaces the block.
For a `factory:no-env` PR drop the preview line:

```bash
gh pr view <url> --json body -q .body > /tmp/pr-<n>.md
python3 - /tmp/pr-<n>.md <<'EOF'
import re, sys
path = sys.argv[1]
body = open(path).read()
block = """<!-- factory:review-links -->
Review in Omnigent: <review_url>
Live preview (always the current deploy): <preview_url>
<!-- /factory:review-links -->"""
pattern = r"<!-- factory:review-links -->.*?<!-- /factory:review-links -->"
body = re.sub(pattern, block, body, flags=re.S) if re.search(pattern, body, re.S) else body.rstrip() + "\n\n" + block + "\n"
open(path, "w").write(body)
EOF
gh pr edit <url> --body-file /tmp/pr-<n>.md
```

The links need Tailscale and an Omnigent login; that is expected.

## Results from env_keeper and demo

Every result line has an action. A PR left without a preview keeps its
verify and demo gates pending, so it never becomes ready.

| Result | Action |
|---|---|
| `ENV READY app=… api=… tilt_ui=… env_id=…` | Record the dev env as READY with its URLs in plan.md. |
| `ENV OK <urls>`, `ENV OK at <sha7> · PREVIEW DOWN p<n>` | Record it. |
| `ENV DEGRADED <res: error>` | Record it. If an IC needs a failing resource, send env_keeper `UP base=<base>` once and keep that IC on work that does not need it. |
| `ENV FAILED <diagnosis>` | Send env_keeper `UP base=<base>` once more. If it fails again, `sys_factory_ask` the human with the diagnosis and keep the ICs on work that does not need the env. |
| `ENV DOWN` | Record it. |
| `PREVIEW READY … · VERIFY PASS` | Pipeline step 3: dispatch the demo. |
| `PREVIEW READY … · VERIFY FAIL` | A product failure: send the failing checks to the author like review CHANGES. No demo for that head. |
| `PREVIEW FAILED p<n> <diagnosis>` | Read the diagnosis. A failure of the PR's own code (its build, its migrations) goes to the author like review CHANGES. Otherwise send `PREVIEW pr=<n> head=<sha> base=<base>` once more (after `tooling ref <ref> not found`, without `tooling=`: the tooling is on main). If that fails too, `sys_factory_ask` the human (options: "Review without a preview" = label `factory:no-env`, or "Wait for an env fix") and record D-n in plan.md. |
| `PREVIEW SKIPPED tooling-conflict <files>` | The PR predates the preview tooling (caffeinelabs/app#9640, on main) and changes those files itself, so they cannot be overlaid. Send the author: merge origin/<base> into the branch and push. The new head has the tooling natively once <base> has it; preview it like any new head (step 6). If the author cannot merge <base> cleanly, or <base> lacks the tooling, `sys_factory_ask` the human with options "Review without a preview" (you label it `factory:no-env`; the verify and demo gates then skip) and "Resolve the merge first", record D-n in plan.md and act on the answer. |
| `PREVIEW SKIPPED not-open` | The PR merged or closed; its server notice drives the rest. Drop the PREVIEW. |
| `PREVIEW CAPPED p<n> · park one of: p<m> (<project>, …), …` | The factory-wide preview cap is reached. If a candidate is your project's and not ready, send env_keeper `PREVIEW_DOWN pr=<m> reason=human`, note "p<n> waits for a slot" in plan.md, and send the PREVIEW again when `PREVIEW DOWN p<m>` arrives. Otherwise post one line to `parent` (`kind="info"`, "PR #<n> waits for a preview slot; candidates: …") and send the PREVIEW again at your next heartbeat. |
| `PREVIEW DOWN p<n> (<reason>)` | Set the task's preview to `parked` or `-` in plan.md. If a preview waits for a slot, send its PREVIEW now. |
| `PREVIEW STATUS p<n> <state> …` | `ready`: record it. `stale`, `failed` or `gone` for a PR awaiting review: send `PREVIEW pr=<n> head=<sha> base=<base>`. |
| `DEMO PASS`, `DEMO PARTIAL`, `DEMO FAIL`, `DEMO ERROR`, `DEMO BLOCKED stale-preview`, `DEMO BLOCKED head-moved <sha>` | Pipeline step 4. |

To label a PR the human chose to review without a preview:
`gh pr edit <url> --add-label factory:no-env`, then check readiness (step 5).

## Server messages

| First line | Action |
|---|---|
| `[Factory review · CHANGES REQUESTED on PR #<n> @<sha7> by <human> …]` + text | A human reviewed the demo or preview. Re-dispatch the author (`<task>-r<k>`) with the text verbatim, the demo link and the failing step; then the new head goes through the pipeline again. Note it in plan.md. |
| `[Factory review · APPROVED PR #<n> …]` | The human will merge on GitHub. Keep the preview up; nothing else. |
| `[Factory decision · D-<n> answered by <human> · "<option>"]` | Route the answer to whoever needed it (yourself, or the IC with `sys_session_send(session_id=…)`), act on it, and move D-<n> to answered in plan.md. |
| `[Factory decision · D-<n> withdrawn …]` | Drop the question. |
| `[Factory notice · PR <repo>#<n> READY FOR HUMAN MERGE · …]` | Hand over (pipeline step 7) unless you already did for that head. |
| `[Factory notice · PR <repo>#<n> checks failed: <names> …]` | Dispatch a fix to the author with the failing check names. |
| `[Factory notice · PR <repo>#<n> merged by @<login> as <sha7> …]` | After-merge steps (below). |
| `[Factory notice · PR <repo>#<n> closed without merge …]` | env_keeper `CLOSED pr=<n>`. If it names a decision D-<n>, wait for its answer before reworking or abandoning the task. |
| `[Factory notice · preview p<n> unreachable · redeploy]` | env_keeper `PREVIEW pr=<n> head=<sha> base=<base>` (the PR's current head). |
| `[Factory notice · preview p<n> idle 72h · park it]` | env_keeper `PREVIEW_DOWN pr=<n> reason=ttl`. The demo stays reviewable; the cockpit can ask for a redeploy. |
| `[Factory control · …]` | The CONTROL section of your prompt. Reply once. |
| `[Factory proposal · C-<n> …]` | The outcome of your `sys_factory_project` or `sys_factory_propose` proposal. Record it in plan.md; after a rejection, adapt the plan to the note. |

## Splitting the project

Propose it with `sys_factory_project(changes=[{op: "split", slug: "<slug>", into: [<new projects: slug, title, goal, dod, scope_paths, depends_on>], keep: "umbrella", pr_map: {"<PR number>": "<new slug>"}}], reason=<one line>, rationale=<why>)`,
or `op: "update"` with a narrower `patch` (scope, DoD). Open PRs move only
through `pr_map`. A human approves it on its decision; the server spawns the
new PLs and you get `[Factory proposal · C-<n> applied|rejected …]`. Keep the
rest moving meanwhile, and never split work that is tightly coupled.

## Questions for the human

Ask with `sys_factory_ask(question=…, options=[{label, description, recommended}], context=…, blocking=…)`.
It returns `{decision: "D-7", …}` at once: record D-7 under Open questions,
keep working on everything that does not depend on it, and act when
`[Factory decision · D-7 answered …]` arrives. Asking the same open question
again returns the same D-n. Never post DECISIONS NEEDED lists to your lead.

## After a merge

On `[Factory notice · PR … merged …]` (or `merged: true` from the gate):
1. env_keeper `MERGED pr=<n> base=<base>`: it takes the preview down and moves
   the dev env to the new origin/<base>.
2. Tell the sibling ICs to run `git merge origin/<base>`. App repos sync by
   merge, never by rebase.
3. Close the IC and the demo agent of that task with
   `sys_session_close(conversation_id=<id>)`, or give the IC the next card.
4. Post the milestone to `parent`; `sys_factory_set(stage="merged")` when it
   was the project's last open PR.

## Definition of done

Every DoD item in the charter is observably true:
- the PR was handed over as ready for human merge, and a human merged it into
  <base> (link the merge commit);
- the env check passed in its factory preview and its demo is published (when
  the card had `Preview: yes`);
- the tests are green.
A PR that is merely opened or approved is not done. Until a human merges,
the project waits; keep its preview up and the ICs on other cards.

## Milestone report (to `parent`)

```
MILESTONE · <slug> · <task>
PR: <url> merged as <sha7> by a human
Review: APPROVE @<sha7> · Verify: PASS in preview p<n> | n/a (factory:no-env) · Demo: PASS|PARTIAL <demo url> | n/a
Next: <next milestone or DONE>
```

## Pause, resume and done

- **PAUSE** (`[Factory control · PAUSE …]`):
  1. Send env_keeper `DOWN` (the dev env only). Previews of PRs awaiting
     review stay up unless the control says to tear them down; then also
     `PREVIEW_DOWN pr=<n> reason=human` for each.
  2. Call `sys_cancel_task(task_id=<ic id>)` for every running IC. Their
     worktrees, branches and NOTES.md stay on disk.
  3. Update plan.md and reply once.
- **RESUME:**
  1. Re-read plan.md.
  2. Send env_keeper `UP base=<base>` (a new env id after a wake) and `PREVIEW`
     for each PR awaiting review whose preview is gone.
  3. Re-dispatch each unfinished task with "continue from NOTES.md in <worktree>".
- **DONE:**
  1. Post the final report to `parent`.
  2. `sys_session_close(conversation_id=<id>)` every IC, the reviewer and the
     demo agents.
  3. Send env_keeper `DOWN`, then close it.
  4. `sys_factory_set(state="done", summary="done: <PR urls>")`.
