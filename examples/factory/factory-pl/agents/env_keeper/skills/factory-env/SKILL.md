---
name: factory-env
description: env_keeper reference for the project's Tilt envs on the sandbox cluster - the dev env (pnpm factory:up|status|urls|down, env.json), per-PR factory previews (scripts/factory.ts preview up|status|down|list, preview.json, the preview tooling overlay from the APP-1 branch), reporting with sys_factory_env, the verify marker, reading Tilt, the failure table, and recovery after a sandbox wake.
---

# factory-env

Two kinds of env, both yours:

| | Dev env | Factory preview |
|---|---|---|
| For | ICs, while they code | humans and the demo agent, while a PR waits for review |
| Commit | origin/main (moves on MERGED) | exactly one PR head (moves on a new PREVIEW) |
| Process | one long-running `tilt up` (port 10350) | one-shot `tilt ci --port 0`; objects keep running after it exits |
| Worktree | `/home/omnigent/workspace/wt/env` | `/home/omnigent/workspace/wt/preview-p<n>` |
| Namespace | `env-<id>` | `env-<id>-p<n>` |
| State file | `~/factory/env.json` | `~/factory/previews/p<n>/preview.json` (+ `tilt-ci.log`, `tilt-snapshot.json`, `synthetic-secret`) |
| Report | `sys_factory_env(...)` without `pr` | `sys_factory_env(pr=<n>, ...)` |

Previews never touch the dev env. VERIFY is PREVIEW: there is no "check out
the PR in the dev env, then go back to main" any more.

## Dev env (run in `/home/omnigent/workspace/wt/env`)

| Command | What it does |
|---|---|
| `pnpm factory:up` | Idempotent. Logs in to ghcr, ensures namespace `env-<id>`, starts `tilt up` detached (log `/tmp/tilt.log`; reuses a running tilt), waits for `caffeine-ux` and `caffeine-api` (up to 20 min cold), writes `~/factory/env.json`. |
| `pnpm -s factory:status` | env.json, then one line per Tilt resource, then the pods. |
| `pnpm -s factory:urls` | The URLs as JSON. |
| `pnpm factory:down` | `tilt down`, deletes the namespace, removes env.json. |

UP:
1. If the worktree does not exist:
   `git -C <repo> fetch origin && git -C <repo> worktree add --detach /home/omnigent/workspace/wt/env origin/main`.
2. So humans can watch: `sys_terminal_launch(terminal="tilt", session="log")`, then
   `sys_terminal_send(terminal="tilt", session="log", text="tail -n +1 -F /tmp/tilt.log")`.
3. `pnpm factory:up` with the longest Bash timeout; run it again on a timeout.
4. Report (values from env.json; resources from Tilt):

```
sys_factory_env(state="ready", env_id=<env_id>, urls={app, api, minio, tilt_ui},
  deployed={sha: <git -C wt/env rev-parse HEAD>, ref: "origin/main"}, resources=[{name, status, detail}])
```

Resources: `tilt get uiresources -o json | jq -c '[.items[] | {name: .metadata.name, status: .status.runtimeStatus, detail: ((.status.buildHistory // [])[0].error // "" | split("\n")[0])}]'`.
Use `state="starting"` while it comes up, `"degraded"` when some resources
fail, `"failed"` when it is unusable (put the diagnosis in `note`), and
`state="down"` after DOWN (`urls={}`, `deployed={}`).

## Factory previews (run in the preview worktree)

The preview CLI lives in caffeinelabs/app `scripts/factory.ts`. Always run it
with node directly; it works on old and new heads, and the root `package.json`
is never overlaid:

```bash
node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --experimental-strip-types scripts/factory.ts preview up --name p<n> --pr <n> --sha <40-char sha>
node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --experimental-strip-types scripts/factory.ts preview status --name p<n>   # exit 1 unless it serves preview.json's sha
node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --experimental-strip-types scripts/factory.ts preview list                 # this pod's preview namespaces
node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --experimental-strip-types scripts/factory.ts preview down --name p<n>
```

Below, `FP` stands for `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --experimental-strip-types scripts/factory.ts preview`;
type it out in full. Never set `FACTORY_DIR` for previews (they live under
`~/factory/previews/`). `up` refuses unless HEAD is `--sha` and the worktree is
clean apart from the preview tooling, creates the namespace and the
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

### PREVIEW pr=<n> head=<sha> [checks=[...]] [tooling=<ref>]

1. **Head.** `gh pr view <n> --repo caffeinelabs/app --json headRefOid,state -q '.state + " " + .headRefOid'`.
   Use the sha as `<sha>` (full 40 chars); if it differs from `head`, say
   "head moved from <head7>" in the result. A PR that is not `OPEN` gets
   `PREVIEW SKIPPED not-open`.
2. **Claim the slot.**
   `sys_factory_env(pr=<n>, state="deploying", env_id=<env_id from an earlier preview.json, else "p<n>">, urls={}, deployed={sha: <sha>, ref: "pull/<n>/head"})`.
   A `preview_cap` error means the factory runs its maximum of live previews:
   reply `PREVIEW CAPPED p<n> · park one of: <park_candidates>` and stop.
3. **Worktree at the head.**
   ```bash
   W=/home/omnigent/workspace/wt/preview-p<n>
   git -C <repo> fetch origin pull/<n>/head main
   test -d "$W" || git -C <repo> worktree add --detach "$W" <sha>
   git -C "$W" reset -q --hard && git -C "$W" clean -fdq && git -C "$W" checkout -q --detach <sha>
   test "$(git -C "$W" rev-parse HEAD)" = "<sha>"
   ```
   The reset and clean drop an earlier tooling overlay; ignored files such as
   `node_modules` stay.
4. **Tooling and deploy, in ONE Bash call** (shell variables do not survive
   between calls), with the longest Bash timeout. The tooling ref is `tooling=`
   from the command, else the charter's "preview tooling ref", else
   `factory/preview-tooling` (the open APP-1 PR, caffeinelabs/app#9640); drop a
   leading `origin/`.
   ```bash
   cd /home/omnigent/workspace/wt/preview-p<n>
   TOOLING=""
   if ! grep -q '"factory:preview"' package.json; then
     P=(Tiltfile scripts/factory.ts scripts/lib deploy/tilt deploy/services/src/builders/tilt.ts \
        ':(glob)deploy/services/*/src/tilt.ts' ':(glob)deploy/services/*/zz_generated/tilt/**')
     CONFLICT=$(git diff --name-only origin/main...HEAD -- "${P[@]}")
     if [ -n "$CONFLICT" ]; then echo "tooling-conflict: $CONFLICT"; exit 3; fi
     git fetch -q origin <tooling ref> && TOOLING=$(git rev-parse FETCH_HEAD)
     git checkout "$TOOLING" -- "${P[@]}"
   fi
   FACTORY_PREVIEW_TOOLING_REF="$TOOLING" node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON \
     --experimental-strip-types scripts/factory.ts preview up --name p<n> --pr <n> --sha <sha>
   ```
   - A head without the `factory:preview` script gets the preview tooling
     overlaid from the tooling ref. Product code stays exactly the PR head; on
     an old head the Tiltfile falls back to the overlaid committed
     `zz_generated/tilt` manifests. Never overlay the root `package.json`,
     `deploy/package.json` or `deploy/pnpm-lock.yaml`.
   - Exit 3 with `tooling-conflict`: the PR itself changes the tooling, so it
     cannot be overlaid. Report
     `sys_factory_env(pr=<n>, state="failed", env_id=…, urls={}, deployed={sha: <sha>}, note="tooling-conflict: <files>")`
     and reply `PREVIEW SKIPPED tooling-conflict <files>`.
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

1. In `/home/omnigent/workspace/wt/preview-p<n>` (or `wt/env` if it has the
   preview tooling): `FP down --name p<n>` (typed in full). Without a usable
   checkout:
   `kubectl delete namespace <namespace from preview.json> --wait=false` and
   `rm -rf ~/factory/previews/p<n>`.
2. merged or closed: `git -C <repo> worktree remove --force /home/omnigent/workspace/wt/preview-p<n>`.
   ttl or human (parked): keep the worktree for a quick redeploy.
3. `sys_factory_env(pr=<n>, state="down"|"parked", env_id=<env_id>, urls={}, deployed={})`.
4. Result: `PREVIEW DOWN p<n> (<reason>)`.

The demo and its files stay in the cockpit; a parked preview is redeployed
by the next PREVIEW.

### PREVIEW_STATUS [pr=<n>]

`FP list` (typed in full, in any checkout with the tooling), then
`FP status --name p<n>` for each listed or requested preview. Report each:
`ready` when `ready` is true, else `failed` with the reason in `note`
(`observed.sha` differs, `api_ready` false, or a note about an earlier pod).
Previews of this pod that are missing from `list` are gone: say so; the PL
redeploys them with PREVIEW.

### MERGED pr=<n> / CLOSED pr=<n>

- MERGED: PREVIEW_DOWN reason=merged, then
  `git -C /home/omnigent/workspace/wt/env fetch origin && git -C /home/omnigent/workspace/wt/env checkout --detach origin/main`
  (Tilt live-updates the dev env), then report the dev env with the new
  `deployed.sha`. Result: `ENV OK at <sha7> · PREVIEW DOWN p<n>`.
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
| `uncommitted changes outside the factory tooling: …` | Something besides the overlay changed the worktree. Only `scripts/lib/*` listed after an overlay: `git checkout HEAD -- scripts/lib` and run `up` again. Anything else: `git reset --hard` and redo step 4. |
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
env is gone. Run UP (and PREVIEW for each open PR the PL lists) again and
report the NEW URLs. A `pnpm factory:down` first is harmless.
