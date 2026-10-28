---
name: factory-ic
description: IC reference for the Omnigent software factory - worktree setup and the load-bearing factory/<slug>/<task> branch, the NOTES.md checkpoint and WIP push cadence, the factory PR template (Verification plan, Demo plan, provenance) and sys_factory_pr registration, the RESULT block, the cross-vendor review contract and marker, and the rule that ICs never run the envs themselves.
---

# factory-ic

## Worktree

The repo is the checkout you start in (`git rev-parse --show-toplevel`, e.g.
`/home/omnigent/workspace/app`). `<base>` is the card's `Base:` branch, the
project's base branch (`main` when the card names none, e.g. `temp-main`):
you branch from it, sync with it and open the PR against it.

```
git -C <repo> fetch origin
git -C <repo> worktree add --no-track -b factory/<slug>/<task> /home/omnigent/workspace/wt/<task> origin/<base>
cd /home/omnigent/workspace/wt/<task>
echo NOTES.md >> "$(git rev-parse --git-common-dir)/info/exclude"   # NOTES.md stays out of the PR
```

The branch name `factory/<slug>/<task>` is load-bearing: the server finds the
PR by it and shows it in the cockpit. Never rename it.

On a re-dispatch (`<task>-r<k>`), the worktree exists. `cd` into it, read
NOTES.md, and `git status`. Continue on the same branch and PR.

After a sibling PR merges, the PL tells you to sync. Run
`git fetch origin && git merge origin/<base>`, never rebase.

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

Commit and push to your branch at least hourly, always with an explicit
refspec: `git push -u origin factory/<slug>/<task>`. A bare `git push` or
`git push origin HEAD` is denied (the guard cannot see where it goes), and so
is any branch outside `factory/`, `git -c …` on a push, and writes to push,
remote, url or branch config. Never force-push, and never push `<base>` or
main.

## PR

- Open it early, after the first meaningful commit:
  `gh pr create --draft --repo caffeinelabs/<repo> --base <base> --head factory/<slug>/<task> --title "<plain title>" --body-file <file>`.
- Right after, register it: `sys_factory_pr(url=<pr url>, task=<task>, claim="opened")`.
- The body uses the repo's PR template. Fill in:
  - **Verification plan**: the env checks from the card, verbatim, with
    expected results. env_keeper runs them against the PR's factory preview.
  - **Demo plan** (when the card says `Preview: yes`): the steps a reviewer
    should see, one tagged line each, from the card's Demo focus. The demo
    agent turns each line into a recorded step:
    ```
    ## Demo plan
    - [api] GET /version → 200, body.gitSha is the deployed sha
    - [ui] Open / logged out → landing renders
    - [ui:login] Open /settings → the "API version" row shows the short sha
    - [skip] Draft build flow — needs the build tier (not in factory envs)
    ```
  - **Factory provenance**: project `<slug>`, PL `<PL session id>`, IC `<your
    session id>` (from `sys_session_get_info({})`), and author vendor `claude`
    or `codex`.
  Leave the `<!-- factory:review-links -->` block to the PL.
- If the card says `Preview: no` (no env checks), add the label:
  `gh api repos/<owner>/<repo>/issues/<n>/labels -f 'labels[]=factory:no-env'`.
- When acceptance is green, run `gh pr ready <url>`.
- Never merge, and never enable auto-merge. Humans merge: once review, verify,
  the demo and CI are green, your PL hands the PR over as ready for human merge.

## The envs are read-only for you

Never run `tilt up`/`down`/`ci`, `pnpm factory:up`/`down`/`preview` (or
`scripts/factory.ts` up/down/preview), `pnpm dev` or `pnpm session:bootstrap`.
Only env_keeper does. You may:
- read the dev env's URLs in `~/factory/env.json`, and a PR's factory preview
  in `~/factory/previews/p<n>/preview.json`;
- run `tilt get uiresources` and `tilt logs <res>`;
- run `kubectl -n <namespace from env.json> get|describe|logs`;
- curl the URLs.
If the env looks broken, say so in RESULT (or post to `parent`). Do not fix it.

## RESULT block (end of every turn)

```
RESULT
status: READY_FOR_REVIEW | BLOCKED: <reason> | FAILED | IN_PROGRESS
task: <id> · attempt <k>/<N> (the card's first line)
pr: <url or none>
head: <sha>
branch: factory/<slug>/<task> · worktree: /home/omnigent/workspace/wt/<task>
commands:
- `<command>` → PASS|FAIL (<counts>)
changes:
- <file:line> · <what>
env checks for VERIFY: [<check>, ...] | none
open questions: <list | none>
blocked by: <the one thing that blocked or slowed you | none>
```

`BLOCKED: <reason>`: the card cannot pass as written; also `sys_factory_task(op="report", result="BLOCKED", note=<reason>)`.
`READY_FOR_REVIEW` means acceptance is green, the PR is ready (not a draft) and
pushed, and `head` is its current head.

## Review contract (when dispatched with purpose "review")

1. Get the head with `gh pr view <url> --json headRefOid,baseRefName,title,body,files`.
   If it differs from the SHA you were given, review the current head and say so.
2. Read `gh pr diff <url>` and the surrounding code. You may run the acceptance
   commands in a scratch worktree, e.g. `git worktree add --detach /home/omnigent/workspace/wt/review-<task> <head>`.
   Never commit or push.
3. Judge the change against the task card: correctness, tests that cover the
   acceptance, scope, no protected paths, no secrets, and a `baseRefName` that
   is the card's `Base:` branch (a PR against another branch is blocking).
4. Post:
   `gh pr review <url> --comment --body "<!-- factory:review sha=<head> verdict=APPROVE|CHANGES reviewer=<your session id> blocking=<n> findings=<fp1,fp2> -->` followed by the summary
   (`fp` = `printf '%s' "<path>:<line/10>:<category>" | sha1sum | cut -c1-8`).
   List the blocking issues, the non-blocking issues and the suggestions, each
   with `file:line`. Use APPROVE only when there are no blocking issues.
5. RESULT: the verdict, the head, the number of blocking issues, and the
   review URL.
