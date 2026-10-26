---
name: factory-pl-playbook
description: Project-lead playbook for the Omnigent software factory - plan.md and task-card templates, worktree and branch naming, parallel dispatch rules (env_keeper first, in the same response), the /goal convention, the review → verify → ready-for-human-merge pipeline (and the factory_merge gate where a repo is opted in), the definition of done, milestone reports, and the pause and done procedures.
---

# PL playbook

## plan.md (`~/factory/<slug>/plan.md`)

```
# <slug> · plan · updated <iso>
Charter: <goal> · DoD: <items> · Repo: caffeinelabs/<repo>#<branch> · Env needed: yes|no · Merge policy: human|gate · Budget: <n> ICs
Lead: <parent id> · Supervisor: <id> · Me: <id>
Env: <env_keeper session id> · <state: starting|READY|FAILED|DOWN> · <urls from ENV READY> · last STATUS <iso>

## Milestones
1. [ ] <milestone> (tasks: t1, t2)

## Tasks
| id | harness | session id | state | PR | head | review | verify | handover | notes |
|----|---------|------------|-------|----|------|--------|--------|----------|-------|

`handover` is `-`, `ready <iso>` (READY FOR HUMAN MERGE posted) or `merged <sha>`.

## Open requests (sent to parent/peers, no reply yet)
## Log (newest first; prune to 30 lines)
```

## Task card (the IC's input)

```
TASK <id> · <title>
Project: <slug> · PL: <your session id>
Repo: <repo checkout, e.g. /home/omnigent/workspace/<repo>> · Base: origin/main
Worktree: /home/omnigent/workspace/wt/<id> · Branch: factory/<slug>/<id>
Scope: <files/globs>; do not touch anything else
Do: <precise change>
Acceptance (run verbatim, all must pass): <commands>
Env checks (env_keeper runs these against the PR head): <curl ... → expected> | none
PR: draft early, repo template; Verification plan = the env checks; Factory provenance = project <slug>, PL <your id>, IC <its own id>, author vendor claude|codex; label factory:no-env if Env checks = none
Report: the RESULT block
```

- Task ids are short kebab-case names, e.g. `version-route`. A re-dispatch is
  `<id>-r<k>` and reuses the same worktree and branch.
- Parallel-safe means no shared files and no ordering dependency. Run
  dependent tasks one after the other.
- Pick `ic` for multi-file work, refactors and test writing. Pick `ic_codex`
  for narrow, crisp cards and for vendor diversity.

## Dispatch rules

- KICKOFF sends everything in ONE response: env_keeper `UP` (when the env is
  needed) and every parallel-safe IC. Several `sys_session_send` calls in one
  response run concurrently.
- `spawn_bounds` allows at most 8 dispatches per turn, and an answer to an IC
  counts as one. Send the rest in waves.
- Never run more concurrent ICs than the charter's budget (6 at most).
- `/goal` mode is for long IMPLEMENT tasks: `args.input` is one standalone
  `"/goal <condition>"`, and `/goal` is the first token. Put the whole task
  card, the acceptance commands and the PR requirement inside the condition.
  Never use it for reviews.
- Always set a task-based `title` (`version-route`, `review-version-route`),
  never a vendor name.

## Review → verify → handover

1. The IC reports `READY_FOR_REVIEW` with the PR and head SHA. Re-run its
   acceptance commands in its worktree, and send failures back to it.
2. Get a cross-vendor review:
   - an `ic` author is reviewed by `reviewer`;
   - an `ic_codex` author is reviewed by `ic` with purpose "review".
   Pass the PR URL, the head SHA and the task card. The reviewer posts
   `<!-- factory:review sha=<head> verdict=APPROVE|CHANGES reviewer=<id> -->`.
3. Verify in the env, if the env is needed: send env_keeper
   `VERIFY pr=<url> head=<sha> checks=[<env checks>]`. It posts
   `<!-- factory:verify sha=<head> result=PASS|FAIL env=<url> -->`.
   Verification runs one at a time, because env_keeper takes one message at a
   time.
4. A new commit invalidates both markers. Repeat steps 2 and 3 for the new head.
5. Check readiness yourself:
   - `gh pr view <url> --json isDraft,headRefOid,mergeable,files,labels`: not a
     draft, mergeable, and no protected paths (`.github/workflows/**`,
     `deploy/**/prod/**`, `**/zz_generated/prod/**`) unless the PR says so;
   - `gh pr checks <url>`: nothing failing or pending;
   - `gh pr view <url> --comments`: the newest review marker for `headRefOid`
     says APPROVE and was posted after the newest commit, and the newest
     verify marker for it says PASS (unless labelled `factory:no-env` or
     `factory:revert`).
6. Hand over, by the charter's merge policy:
   - `human` (the default, and always for caffeinelabs/app): post
     `READY FOR HUMAN MERGE <url> · head <sha> · review APPROVE · verify PASS|n/a · checks green`
     to `parent` (kind=info). Never merge, never run the merge gate on
     caffeinelabs/app, and never enable auto-merge or a merge queue. The IC
     stays idle on its worktree until the PR merges or a human asks for
     changes.
   - `gate` (only a repo an operator opted in with `OMNIGENT_FACTORY_MERGE_REPOS`):
     `python -m omnigent.factory_merge <pr-url>` (add `--admin-fallback` only
     if the charter allows it). Exit 0 means merged, 2 means a gate failed
     (the JSON `reasons` say which), 1 is an error. A `repo` failure means the
     repo is not opted in: hand it over as `human` instead.
   - Never add `factory:allow-protected` yourself. That is a human decision.
7. After the PR merges (your lead posts `MERGED <url>`, the gate says
   `merged: true`, or `gh pr view <url> --json state` reads MERGED):
   1. Send env_keeper `MERGED`.
   2. Tell the sibling ICs to run `git merge origin/main`. App repos sync by
      merge, never by rebase.
   3. Close the IC with `sys_session_close(conversation_id=<ic id>)`, or give
      it the next card.
   4. Post the milestone to `parent`.

## Definition of done

Every DoD item in the charter is observably true:
- the PR was handed over as ready for human merge, and a human merged it to
  main (link the merge commit);
- the env check passes against the env (when required);
- the tests are green.
A PR that is merely opened or approved is not done. Until a human merges,
the project waits; keep the env up and the ICs on other cards.

## Milestone report (to `parent`)

```
MILESTONE · <slug> · <task>
PR: <url> merged as <sha> by a human (or factory_merge: merged)
Review: APPROVE by <reviewer id> for <head> · Verify: PASS <env url> | n/a (factory:no-env)
Next: <next milestone or DONE>
```

## Pause and done

- **PAUSE:**
  1. Send env_keeper `DOWN`.
  2. Call `sys_cancel_task(task_id=<ic id>)` for every running IC. Their
     worktrees, branches and NOTES.md stay on disk.
  3. Update plan.md and post "paused" to `parent`.
- **RESUME:**
  1. Re-read plan.md.
  2. Send env_keeper `UP` (a new env id).
  3. Re-dispatch each unfinished task with "continue from NOTES.md in <worktree>".
- **DONE:**
  1. Post the final report to `parent`.
  2. `sys_session_close(conversation_id=<id>)` every IC and the reviewer.
  3. Send env_keeper `DOWN`, then close it.
  4. `sys_factory_set(state="done", summary="done: <PR urls>")`.
