---
name: factory-ic
description: IC reference for the Omnigent software factory - worktree setup, the NOTES.md checkpoint and WIP push cadence, the factory PR template, the RESULT block, the cross-vendor review contract and marker, and the rule that ICs never run the env themselves.
---

# factory-ic

## Worktree

The repo is the checkout you start in (`git rev-parse --show-toplevel`, e.g.
`/home/omnigent/workspace/app`).

```
git -C <repo> fetch origin
git -C <repo> worktree add -b factory/<slug>/<task> /home/omnigent/workspace/wt/<task> origin/main
cd /home/omnigent/workspace/wt/<task>
echo NOTES.md >> "$(git rev-parse --git-common-dir)/info/exclude"   # NOTES.md stays out of the PR
```

On a re-dispatch (`<task>-r<k>`), the worktree exists. `cd` into it, read
NOTES.md, and `git status`. Continue on the same branch and PR.

After a sibling PR merges, the PL tells you to sync. Run
`git fetch origin && git merge origin/main`, never rebase.

## NOTES.md (worktree root, untracked)

```
# <task> · <one-line goal>
PR: <url> · branch: factory/<slug>/<task> · head: <sha>
## Plan
## Done
## Next
## Decisions / open questions
```

Update it at every milestone and before every turn ends. It is the handoff if
you are restarted or replaced.

## WIP cadence

Commit and push to your branch at least hourly:
`git push -u origin factory/<slug>/<task>`. Never force-push, and never push
main.

## PR

- Open it early, after the first meaningful commit:
  `gh pr create --draft --repo caffeinelabs/<repo> --base main --head factory/<slug>/<task> --title "<plain title>" --body-file <file>`.
- The body uses the repo's PR template. Fill in:
  - **Verification plan**: the env checks from the card, verbatim, with
    expected results. The env verifier runs them as written.
  - **Factory provenance**: project `<slug>`, PL `<PL session id>`, IC `<your
    session id>` (from `sys_session_get_info({})`), and author vendor `claude`
    or `codex`.
- If the card has no env checks, add the label:
  `gh api repos/<owner>/<repo>/issues/<n>/labels -f 'labels[]=factory:no-env'`.
- When acceptance is green, run `gh pr ready <url>`.
- Never merge, and never enable auto-merge. Humans merge: once review, verify
  and CI are green, your PL hands the PR over as ready for human merge.

## The env is read-only for you

Never run `tilt up`/`down`, `pnpm factory:up`/`down`, `pnpm dev` or
`pnpm session:bootstrap`. Only env_keeper does. You may:
- read the URLs in `~/factory/env.json`;
- run `tilt get uiresources` and `tilt logs <res>`;
- run `kubectl -n <namespace from env.json> get|describe|logs`;
- curl the URLs.
If the env looks broken, say so in RESULT (or post to `parent`). Do not fix it.

## RESULT block (end of every turn)

```
RESULT
status: READY_FOR_REVIEW | BLOCKED | FAILED | IN_PROGRESS
task: <id>
pr: <url or none>
head: <sha>
branch: factory/<slug>/<task> · worktree: /home/omnigent/workspace/wt/<task>
commands:
- `<command>` → PASS|FAIL (<counts>)
changes:
- <file:line> · <what>
env checks for VERIFY: [<check>, ...] | none
open questions: <list | none>
```

`READY_FOR_REVIEW` means acceptance is green, the PR is ready (not a draft) and
pushed, and `head` is its current head.

## Review contract (when dispatched with purpose "review")

1. Get the head with `gh pr view <url> --json headRefOid,title,body,files`. If
   it differs from the SHA you were given, review the current head and say so.
2. Read `gh pr diff <url>` and the surrounding code. You may run the acceptance
   commands in a scratch worktree, e.g. `git worktree add --detach /home/omnigent/workspace/wt/review-<task> <head>`.
   Never commit or push.
3. Judge the change against the task card: correctness, tests that cover the
   acceptance, scope, no protected paths, and no secrets.
4. Post:
   `gh pr review <url> --comment --body "<!-- factory:review sha=<head> verdict=APPROVE|CHANGES reviewer=<your session id> -->` followed by the summary.
   List the blocking issues, the non-blocking issues and the suggestions, each
   with `file:line`. Use APPROVE only when there are no blocking issues.
5. RESULT: the verdict, the head, the number of blocking issues, and the
   review URL.
