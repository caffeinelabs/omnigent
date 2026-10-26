---
name: factory-env
description: env_keeper reference for the project's shared Tilt env on the sandbox cluster - the pnpm factory:up|status|urls|down commands, env.json, reading tilt uiresources, common failures (tools, ghcr login, registry push, image pull, migrations, OOM, quota, routes) and fixes, recovery after a sandbox wake, and the VERIFY steps and marker format.
---

# factory-env

## Commands (run in `/home/omnigent/workspace/wt/env`)

| Command | What it does |
|---|---|
| `pnpm factory:up` | Idempotent. It logs in to ghcr, ensures namespace `env-<id>`, and starts `tilt up` detached (log `/tmp/tilt.log`, pidfile; it reuses a running tilt). It waits for `caffeine-ux` and `caffeine-api` to be Ready (up to 20 min cold), writes `~/factory/env.json`, and prints JSON. |
| `pnpm -s factory:status` | JSON with ready resources, failing resources plus their last error, and the URLs. |
| `pnpm -s factory:urls` | The URLs as JSON. |
| `pnpm factory:down` | `tilt down`, deletes the namespace, and removes env.json. |

`~/factory/env.json` holds `env_id`, `namespace`, `pod`, `pod_uid`, `urls`,
`started_at` and `tilt_pid`. `urls` has `app` (the UX), `api`, `minio` and
`tilt_ui`; read one with `jq -r .urls.api ~/factory/env.json`. Report the URLs
verbatim. Never build `<svc>-<id>.envs...` hostnames yourself.

There is only one tilt per pod, and it belongs to you. The Tilt UI is also
reachable at the `tilt_ui` URL.

## Reading Tilt

- `tilt get uiresources` lists resources. Look at the `RUNTIME` and `UPDATE`
  columns.
- `tilt get uiresources -o json | jq -r '.items[] | [.metadata.name, .status.runtimeStatus, .status.updateStatus] | @tsv'`
- `tilt logs <res> | tail -n 80` shows a resource's build and runtime log.
- `tilt trigger <res>` rebuilds or restarts one resource.
- `kubectl -n env-<id> get pods`, `describe pod <p>`, `logs <p> [-c <container>] --tail=80`.

## Common failures

| Symptom | Fix |
|---|---|
| `tilt` or `kubectl` not found | `mkdir -p ~/.local/bin && curl -fsSL https://github.com/tilt-dev/tilt/releases/download/v0.37.8/tilt.0.37.8.linux.x86_64.tar.gz \| tar -xz -C ~/.local/bin tilt`. For kubectl: `curl -fsSLo ~/.local/bin/kubectl https://dl.k8s.io/release/v1.35.9/bin/linux/amd64/kubectl && chmod +x ~/.local/bin/kubectl`. Make sure `~/.local/bin` is on PATH. |
| Base image pull `denied` or `unauthorized` (ghcr.io) | Re-run `pnpm factory:up`, which performs the ghcr login. Check with `docker pull ghcr.io/caffeinelabs/app/caffeine-db:latest`. Never print tokens. |
| Image push to `registry.envs.sandbox.caffeinelabs.io` fails | Check `docker info` (the DinD sidecar) and `curl -fsS https://registry.envs.sandbox.caffeinelabs.io/v2/`, then `tilt trigger <res>`. |
| `ImagePullBackOff` in the env | Run `kubectl -n env-<id> describe pod <p>`. For ghcr images, check that `kubectl -n env-<id> get secret github-registry-caffeine` exists. For registry images, re-trigger the build. |
| caffeine-db migrations fail (init container) | Read `kubectl -n env-<id> logs <db pod> -c <init container>`. Check that postgres is Ready, then `tilt trigger caffeine-db`. |
| `OOMKilled` | Note the container and its limit. Resource changes go into `deploy/tilt` through a PR, so report it to the PL as a task. |
| `exceeded quota: env-budget` | Run `kubectl -n env-<id> describe resourcequota`, and delete stray Jobs and Pods you created. The budget is 12 CPU, 32Gi requests and 40 pods. |
| `forbidden` right after the namespace was created | The RoleBinding arrives within about 5 s. `factory:up` waits for it; otherwise poll `kubectl auth can-i create deployments -n env-<id>`. |
| The namespace exists but is owned by another pod UID | Never adopt it. It belongs to a previous pod and the reaper deletes it. Report it if `factory:up` cannot proceed. |
| HTTPRoute denied | Routes come from the Tiltfile. Do not hand-edit them. Report the Kyverno message. |
| URL returns 404 or 503 | Wait for Ready, check `tilt logs caffeine-ux` and `caffeine-api`, then `tilt trigger`. |

If a fix needs product or infra changes, report `ENV FAILED` or
`ENV DEGRADED` with the evidence. Do not edit the repo.

## After a sandbox wake

A wake gives the pod a new UID, so the env id changes and the old namespace is
reaped. If env.json's `pod_uid` is not `$OMNIGENT_POD_UID`, or the namespace is
gone, the env is gone. Run UP again and report the NEW URLs. A `pnpm factory:down`
first is harmless.

## VERIFY

1. In the env worktree, run `git fetch origin pull/<n>/head && git checkout --detach FETCH_HEAD`.
   Check that `git rev-parse HEAD` equals the requested `<sha>`.
2. Tilt live-updates the change. Run
   `tilt wait --for=condition=Ready uiresource/caffeine-ux uiresource/caffeine-api --timeout=10m`.
3. Run the PL's checks verbatim against URLs from env.json. Use `curl -fsS -o
   /tmp/v.out -w '%{http_code}' <url>` and show the status plus a trimmed body.
4. Post the marker and evidence:
   ```
   gh pr comment <pr-url> --body "<!-- factory:verify sha=<sha> result=PASS env=<urls.app or urls.api> -->
   Verified in env <env_id> at <sha>:
   - <check> → 200 <trimmed body>"
   ```
   Use `result=FAIL` if any check fails, and list the failing ones.
5. Return to main with `git fetch origin && git checkout --detach origin/main`.
