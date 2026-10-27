---
name: factory-env
description: env_keeper reference for the project's Tilt envs on the sandbox cluster - the dev env at origin/<base>, the project's base branch (pnpm factory:up|status|urls|down, env.json), per-PR factory previews (pnpm -s factory:preview up|status|down|list, preview.json, the tooling overlay from the tooling ref for heads that predate it), reporting with sys_factory_env, the verify marker, reading Tilt, the failure table, and recovery after a sandbox wake.
---

# factory-env

Two kinds of env, both yours. `<base>` is the project's base branch: the
charter's `Base branch:` (default `main`), which the PL sends as `base=<base>`
on UP, PREVIEW and MERGED. Every factory PR targets it.

| | Dev env | Factory preview |
|---|---|---|
| For | ICs, while they code | humans and the demo agent, while a PR waits for review |
| Commit | origin/<base> (moves on MERGED) | exactly one PR head (moves on a new PREVIEW) |
| Process | one long-running `tilt up` (port 10350) | one-shot `tilt ci --port 0`; objects keep running after it exits |
| Worktree | `/home/omnigent/workspace/wt/env` | `/home/omnigent/workspace/wt/preview-p<n>` |
| Namespace | `env-<id>` | `env-<id>-p<n>` |
| State file | `~/factory/env.json` | `~/factory/previews/p<n>/preview.json` (+ `tilt-ci.log`, `tilt-snapshot.json`, `synthetic-secret`) |
| Report | `sys_factory_env(...)` without `pr` | `sys_factory_env(pr=<n>, ...)` |

Previews never touch the dev env. VERIFY is PREVIEW: there is no "check out
the PR in the dev env, then go back to the base" any more.

## Dev env (run in `/home/omnigent/workspace/wt/env`)

| Command | What it does |
|---|---|
| `pnpm factory:up` | Idempotent. Logs in to ghcr, ensures namespace `env-<id>`, starts `tilt up` detached (log `/tmp/tilt.log`; reuses a running tilt), waits for `caffeine-ux` and `caffeine-api` (up to 20 min cold), writes `~/factory/env.json`. |
| `pnpm -s factory:status` | env.json, then one line per Tilt resource, then the pods. |
| `pnpm -s factory:urls` | The URLs as JSON. |
| `pnpm factory:down` | `tilt down`, deletes the namespace, removes env.json. |

UP base=<base>:
1. `git -C <repo> fetch origin`. If the worktree does not exist:
   `git -C <repo> worktree add --detach /home/omnigent/workspace/wt/env origin/<base>`.
   If it exists but is not on that branch's history
   (`git -C /home/omnigent/workspace/wt/env merge-base --is-ancestor HEAD origin/<base>`
   fails, e.g. it was made for another base), move it:
   `git -C /home/omnigent/workspace/wt/env checkout --detach origin/<base>`.
2. So humans can watch: `sys_terminal_launch(terminal="tilt", session="log")`, then
   `sys_terminal_send(terminal="tilt", session="log", text="tail -n +1 -F /tmp/tilt.log")`.
3. `pnpm factory:up` with the longest Bash timeout; run it again on a timeout.
4. Report (values from env.json; resources from Tilt):

```
sys_factory_env(state="ready", env_id=<env_id>, urls={app, api, minio, tilt_ui},
  deployed={sha: <git -C wt/env rev-parse HEAD>, ref: "origin/<base>"}, resources=[{name, status, detail}])
```

Resources: `tilt get uiresources -o json | jq -c '[.items[] | {name: .metadata.name, status: .status.runtimeStatus, detail: ((.status.buildHistory // [])[0].error // "" | split("\n")[0])}]'`.
Use `state="starting"` while it comes up, `"degraded"` when some resources
fail, `"failed"` when it is unusable (put the diagnosis in `note`), and
`state="down"` after DOWN (`urls={}`, `deployed={}`).

## Factory previews (run in the preview worktree)

The preview CLI is `scripts/factory.ts preview` in caffeinelabs/app, on main
since caffeinelabs/app#9640. A PR head whose history includes it (current
main, or a base branch cut from it) has it, and the `factory:preview` script
with it (`-s` keeps stdout pure JSON):

```bash
pnpm -s factory:preview up --name p<n> --pr <n> --sha <40-char sha>
pnpm -s factory:preview status --name p<n>   # exit 1 unless it serves preview.json's sha
pnpm -s factory:preview list                 # this pod's preview namespaces
pnpm -s factory:preview down --name p<n>
```

A head that predates it has no `preview` subcommand: PREVIEW overlays the
tooling from the tooling ref, `main` (step 4), and runs the same commands with node,
because the root `package.json` is never overlaid:
`node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --experimental-strip-types scripts/factory.ts preview up|status|list|down …`.

Below, `FP` stands for `pnpm -s factory:preview` in a checkout whose
`package.json` has the `factory:preview` script, else for
`node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --experimental-strip-types scripts/factory.ts preview`;
type it out in full. `FP` needs a checkout with the preview tooling, that is
one where `grep -q "case 'preview'" scripts/factory.ts` succeeds: a preview
worktree after step 4, or `wt/env` once it is at an origin/<base> that includes
caffeinelabs/app#9640. A `wt/env` created before that stays on its old commit
until the next MERGED, and there `FP` prints usage and exits 1; move it first
with `git -C /home/omnigent/workspace/wt/env fetch origin && git -C /home/omnigent/workspace/wt/env checkout --detach origin/<base>`
(Tilt live-updates the dev env; report it with the new `deployed.sha`).
Never set `FACTORY_DIR` for previews (they live under `~/factory/previews/`).
`up` refuses unless HEAD is `--sha` and the worktree is clean apart from the
preview tooling, creates the namespace and the
`factory-env-secrets` Secret (synthetic session secret, also written to
`~/factory/previews/p<n>/synthetic-secret`, mode 0600, never printed), runs
`tilt ci`, waits until `<app>/__factory/preview` serves the sha and
`<api>/ready` answers, then writes and prints `preview.json` (progress goes to
stderr; stdout is only JSON):

```json
{"name": "p9630", "pr": 9630, "repo": "caffeinelabs/app", "sha": "<40>",
 "env_id": "<id>-p9630", "namespace": "env-<id>-p9630",
 "urls": {"app": "https://app-…", "api": "https://api-…", "minio": "https://minio-…"},
 "tiers": ["core"], "resources": [{"name": "caffeine-api", "ready": true, "restarts": 0}],
 "deployed_at": "…", "timings_s": {"total": 101}, "tooling_overlay": null | "<sha>",
 "synthetic_secret_file": "/home/omnigent/factory/previews/p9630/synthetic-secret"}
```

### PREVIEW pr=<n> head=<sha> base=<base> [checks=[...]] [tooling=<ref>]

1. **Head.** `gh pr view <n> --repo caffeinelabs/app --json headRefOid,state,baseRefName -q '.state + " " + .headRefOid + " " + .baseRefName'`.
   Use the sha as `<sha>` (full 40 chars); if it differs from `head`, say
   "head moved from <head7>" in the result. Use `baseRefName` as `<base>`
   below (for a factory PR it is the command's `base=`). A PR that is not
   `OPEN` gets `PREVIEW SKIPPED not-open`.
2. **Claim the slot.**
   `sys_factory_env(pr=<n>, state="deploying", env_id=<env_id from an earlier preview.json, else "p<n>">, urls={}, deployed={sha: <sha>, ref: "pull/<n>/head"})`.
   A `preview_cap` error means the factory runs its maximum of live previews:
   reply `PREVIEW CAPPED p<n> · park one of: p<m> (<project>, ready|not ready), …`
   from its `park_candidates` (`preview`, `project`, `pr_ready`) and stop.
3. **Worktree at the head.**
   ```bash
   W=/home/omnigent/workspace/wt/preview-p<n>
   git -C <repo> fetch origin pull/<n>/head <base>
   test -d "$W" || git -C <repo> worktree add --detach "$W" <sha>
   git -C "$W" reset -q --hard && git -C "$W" clean -fdq && git -C "$W" checkout -q --detach <sha>
   test "$(git -C "$W" rev-parse HEAD)" = "<sha>"
   ```
   The reset and clean drop an earlier tooling overlay; ignored files such as
   `node_modules` stay.
4. **Tooling and deploy, in ONE Bash call** (shell variables do not survive
   between calls), with the longest Bash timeout. The tooling ref is `main`
   (caffeinelabs/app#9640 merged the preview tooling there); only a
   `tooling=<ref>` in the command replaces it (drop a leading `origin/`). A
   `tooling=factory/preview-tooling` names the deleted pre-#9640 branch and
   means `main`.
   ```bash
   cd /home/omnigent/workspace/wt/preview-p<n>
   BASE=<base>   # the PR's baseRefName (step 1)
   REF=main   # or the command's tooling=<ref>
   TOOLING=""
   if ! grep -q "case 'preview'" scripts/factory.ts 2>/dev/null; then
     P=(Tiltfile scripts/factory.ts deploy/tilt deploy/services/src/builders/tilt.ts \
        ':(glob)deploy/services/*/src/tilt.ts' ':(glob)deploy/services/*/zz_generated/tilt/**')
     CONFLICT=$(git diff --name-only "origin/$BASE...HEAD" -- "${P[@]}")
     if [ -n "$CONFLICT" ]; then echo "tooling-conflict: $CONFLICT"; exit 3; fi
     git fetch -q origin "$REF" && TOOLING=$(git rev-parse FETCH_HEAD) \
       && git checkout "$TOOLING" -- "${P[@]}" || { echo "tooling-fetch-failed"; exit 4; }
   fi
   if grep -q '"factory:preview"' package.json; then
     FACTORY_PREVIEW_TOOLING_REF="$TOOLING" pnpm -s factory:preview up --name p<n> --pr <n> --sha <sha>
   else
     FACTORY_PREVIEW_TOOLING_REF="$TOOLING" node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON \
       --experimental-strip-types scripts/factory.ts preview up --name p<n> --pr <n> --sha <sha>
   fi
   ```
   - A head that has `scripts/factory.ts preview` and the `factory:preview`
     script: nothing is overlaid, `TOOLING` stays empty and `preview.json`
     says `"tooling_overlay": null`.
   - A head that predates the tooling has no `preview` subcommand: the
     tooling files are overlaid from the tooling ref (`git fetch origin "$REF"`),
     and the CLI runs with node. Product code stays exactly the PR head; on an old head
     the Tiltfile falls back to the overlaid committed `zz_generated/tilt`
     manifests. Never overlay the root `package.json`,
     `deploy/package.json` or `deploy/pnpm-lock.yaml`.
   - Exit 3 with `tooling-conflict`: the PR predates the tooling and its diff
     against origin/<base> changes the tooling itself, so it cannot be
     overlaid. Report
     `sys_factory_env(pr=<n>, state="failed", env_id=…, urls={}, deployed={sha: <sha>}, note="tooling-conflict: <files>")`
     and reply `PREVIEW SKIPPED tooling-conflict <files>`. Merging origin/<base>
     into the PR gives it the tooling natively once <base> has it.
   - Exit 4 with `tooling-fetch-failed`: the tooling ref could not be fetched
     or checked out, so nothing was deployed (see the failure table).
   - On a timeout run the same block again: it waits for the previous run's
     lock and redeploys idempotently. A new head of the same PR reuses the
     namespace (about 1-3 min warm).
5. **Overlay sha.** When `TOOLING` was set, `jq -r .tooling_overlay ~/factory/previews/p<n>/preview.json`
   is that commit; report it as `deployed.tooling_overlay`.
6. **Confirm and report.** Read `~/factory/previews/p<n>/preview.json`, then
   `curl -fsS <urls.app>/__factory/preview | jq -e --arg s <sha> '.sha == $s'`, then report:
   ```
   sys_factory_env(pr=<n>, state="ready", env_id=<env_id>, namespace=<namespace>,
     urls={app, api, minio}, deployed={sha: <sha>, ref: "pull/<n>/head", tooling_overlay: <sha, only when overlaid>},
     resources=<preview.json resources>)
   ```
7. **Checks.** Always check that `/__factory/preview` serves the sha and that
   `<urls.api>/ready` answers 200. Then run the PL's checks verbatim against
   the preview URLs: `{app}`, `{api}` and `{minio}` (or dev env URLs) stand
   for the preview's. Use `curl -fsS -o /tmp/v.out -w '%{http_code}' <url>`
   and keep the status plus a trimmed body.
8. **One verify comment.** Write the body to a file and post it once:
   ```
   <!-- factory:verify sha=<sha> result=PASS env=<urls.api> preview=<urls.app> -->
   Verified in factory preview p<n> (`<env_id>`) at `<sha7>`:
   - `/__factory/preview` → sha <sha7> ✓ · `<api>/ready` → 200
   - `<check>` → 200 <trimmed body>
   Preview: <urls.app> · API <urls.api> (Tailscale)
   ```
   `gh pr comment <pr url> --body-file /tmp/verify-p<n>.md`. Use
   `result=FAIL` when any check fails, and list the failing ones first. The
   marker's sha is the full 40-character head.
9. **Result:** `PREVIEW READY pr=<n> sha=<sha7> app=<urls.app> api=<urls.api> secret_file=~/factory/previews/p<n>/synthetic-secret · VERIFY PASS|FAIL <one-line evidence>`.

When `up` fails: read the error (it lists the resources that did not settle),
`tail -n 80 ~/factory/previews/p<n>/tilt-ci.log` and the snapshot, fix what
is yours to fix (table below) and run `up` once more. Then report
`sys_factory_env(pr=<n>, state="failed", env_id=…, urls={}, deployed={sha: <sha>}, note=<diagnosis>)`
and reply `PREVIEW FAILED p<n> <diagnosis>`.

### PREVIEW_DOWN pr=<n> reason=merged|closed|ttl|human

1. First read `env_id` and `namespace` from `~/factory/previews/p<n>/preview.json`
   (`jq -r '.env_id, .namespace'`): `down` deletes that directory. Without the
   file, the namespace is `env-<env_id>`, and `FP list` names it.
2. In `/home/omnigent/workspace/wt/preview-p<n>` (or `wt/env` if it has the
   preview tooling, see `FP`): `FP down --name p<n>` (typed in full). Without a usable
   checkout:
   `kubectl delete namespace <namespace> --wait=false` and
   `rm -rf ~/factory/previews/p<n>`.
3. merged or closed: `git -C <repo> worktree remove --force /home/omnigent/workspace/wt/preview-p<n>`.
   ttl or human (parked): keep the worktree for a quick redeploy.
4. `sys_factory_env(pr=<n>, state="down"|"parked", env_id=<env_id from step 1>, urls={}, deployed={})`.
5. Result: `PREVIEW DOWN p<n> (<reason>)`.

The demo and its files stay in the cockpit; a parked preview is redeployed
by the next PREVIEW.

### PREVIEW_STATUS [pr=<n>]

`FP list` (typed in full, in `wt/env` or any other checkout with the preview tooling, see `FP`), then
`FP status --name p<n>` for each listed or requested preview. Report each:
`ready` when `ready` is true, else `failed` with the reason in `note`
(`observed.sha` differs, `api_ready` false, or a note about an earlier pod).
Previews of this pod that are missing from `list` are gone. Result: one line
per preview, `PREVIEW STATUS p<n> ready|stale|failed|gone sha=<sha7> · <note>`;
the PL redeploys the ones that are not ready with PREVIEW.

### MERGED pr=<n> base=<base> / CLOSED pr=<n>

- MERGED: PREVIEW_DOWN reason=merged, then
  `git -C /home/omnigent/workspace/wt/env fetch origin && git -C /home/omnigent/workspace/wt/env checkout --detach origin/<base>`
  (Tilt live-updates the dev env), then report the dev env with the new
  `deployed.sha` (`deployed.ref` stays `origin/<base>`). Result: `ENV OK at <sha7> · PREVIEW DOWN p<n>`.
- CLOSED: PREVIEW_DOWN reason=closed.

## Reading Tilt

- Dev env: `tilt get uiresources`, `tilt logs <res> | tail -n 80`, `tilt trigger <res>`.
- Preview: there is no Tilt server; read `~/factory/previews/p<n>/tilt-ci.log`,
  `jq '.uiResources // .' ~/factory/previews/p<n>/tilt-snapshot.json`, and the
  cluster: `kubectl -n <namespace> get pods,httproute,httproutefilter,configmap/factory-preview`.
- `kubectl -n <namespace> describe pod <p>` and `logs <p> [-c <container>] --tail=80`.

## Failure table

| Symptom | Fix |
|---|---|
| `tilt` or `kubectl` not found | `mkdir -p ~/.local/bin && curl -fsSL https://github.com/tilt-dev/tilt/releases/download/v0.37.8/tilt.0.37.8.linux.x86_64.tar.gz \| tar -xz -C ~/.local/bin tilt`; kubectl: `curl -fsSLo ~/.local/bin/kubectl https://dl.k8s.io/release/v1.35.9/bin/linux/amd64/kubectl && chmod +x ~/.local/bin/kubectl`. Put `~/.local/bin` on PATH. |
| Base image pull `denied` or `unauthorized` (ghcr.io) | Re-run `pnpm factory:up` (it logs in to ghcr). Never print tokens. |
| Image push to `registry.envs.sandbox.caffeinelabs.io` fails | `docker info` (DinD sidecar) and `curl -fsS https://registry.envs.sandbox.caffeinelabs.io/v2/`, then retry. |
| `ImagePullBackOff` | `kubectl -n <ns> describe pod <p>`; check `kubectl -n <ns> get secret github-registry-caffeine`; image pulls on a new node take about a minute. |
| caffeine-db migrations fail | `kubectl -n <ns> logs <db pod> -c <init container>`; postgres Ready? Then redeploy. |
| `OOMKilled` | Note container and limit; resource changes go through a PR: report it to the PL. |
| `exceeded quota: env-budget` | `kubectl -n <ns> describe resourcequota`; each env and preview has its own 12 CPU / 32 Gi quota, so one preview never hits it: look for stray Jobs and Pods. |
| `forbidden` right after the namespace was created | The RoleBinding arrives within about 5 s; the CLI waits for it. |
| Namespace owned by another pod UID | Never adopt it; the reaper deletes it. Report it. |
| `HEAD is …, not …` | Step 3 was skipped or the head moved: check out `<sha>` again. |
| `uncommitted changes outside the factory tooling: …` | Something besides the overlay changed the worktree: `git reset -q --hard && git clean -fdq`, then redo step 4. |
| Exit 4, `tooling-fetch-failed` | A network error: retry once. If `git ls-remote origin <tooling ref>` prints nothing, the ref does not exist (only a `tooling=` override can be missing; `main` always has the tooling): report `PREVIEW FAILED p<n> tooling ref <ref> not found`; a PREVIEW without `tooling=` overlays from `main`. |
| `/__factory/preview` 404 or the app's HTML | The routes were rendered without preview mode: the tooling overlay is missing or `tilt up` ran instead of `preview up`. Check `kubectl -n <ns> get httproute,httproutefilter,configmap/factory-preview`. |
| `HTTPRouteFilter` or route denied | Report the Kyverno message; never hand-edit routes. |
| `pid … holds …/up.lock` | Another `up` of that preview runs; wait for it. |
| `FACTORY_PREVIEW=p<N> needs the preview env id` | `FACTORY_PREVIEW*` leaked into a dev env shell: unset it. |
| `tilt ci exited with 1` | The error names the resources that did not settle; read `tilt-ci.log` and the snapshot, fix, run `up` once more. |
| `App services rendered from the committed zz_generated/tilt` | Expected on old heads (the load-time synth needs the deploy workspace); not an error. |
| URL returns 404 or 503 | Wait for Ready; check the pod logs; redeploy. |

If a fix needs product or infra changes, report `FAILED` or `DEGRADED` with
the evidence. Do not edit the repo.

## After a sandbox wake

A wake gives the pod a new UID, so every env id changes and the old
namespaces are reaped. If env.json's `pod_uid` is not `$OMNIGENT_POD_UID`, or
`FP status` says preview.json "describes … from an earlier sandbox pod", that
env is gone. Run UP base=<base> (and PREVIEW for each open PR the PL lists) again and
report the NEW URLs. A `pnpm factory:down` first is harmless.
