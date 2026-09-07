# DIFF.md — staging-branch fork delta ledger

This file tracks what the fork's `staging` branch carries that is **not**
on `omnigent-ai/omnigent` `main`. Fork `main` is kept as an exact mirror
of upstream `main` (synced to `7b9b54784`; upstream has since advanced to
`358c0df67`, which this sync merges into `staging`), so the fork delta is
exactly:

    git diff upstream/main...origin/staging

`develop` keeps its own ledger. This one is for `staging`.

That diff still shows two layers, not one:

1. **Caffeinelabs fork work on staging** (fork PRs #1–#79 + direct
   commits) — the entries below.
2. **An inherited Databricks-internal fork base** (PRs #2xxx–#3xxx)
   that never landed in `omnigent-ai/main` (see the entry at the end).

Do not grow layer 2. New work lands on `develop` first and is ported to
`staging` on purpose.

## Entries

### pi-native: scrub provider credential env from the pi terminal (this PR / #76)

- **What:** Two cooperating mechanisms. (1) The pi-native terminal spec
  strips an operator-declared denylist of credential env vars, named as a
  comma-separated list in `OMNIGENT_PI_ENV_UNSET` and popped via
  `TerminalEnvSpec.env_unset` — pi activates a built-in provider's entire
  catalog on the mere presence of its credential (any value), and pi's own
  auth rides the managed `models.json` `apiKey`, so it needs no credential
  env. (2) The managed `settings.json` now carries `enabledModels` —
  provider-qualified refs for every model in the rendered `models.json` —
  so pi's picker (`/model` dialog, Ctrl+P cycling) is scoped to the
  managed shortlist even if a sniffed var slips through (allowlist
  belt-and-suspenders; older pi ignores the unknown key harmlessly).
- **Why:** Pi's built-in catalog activation flooded the session picker
  with entries that bypass the managed provider and its gateway routing —
  and hang on use. Two enabling conditions met in caffeine sandboxes: the
  shortlist `models.json` carries a `claude-*` id (enables the built-in
  anthropic provider) and pods set a dummy `ANTHROPIC_AUTH_TOKEN` for
  claude-code, which pi >= 0.84 newly reads as anthropic auth (0.79 did
  not). Verified against pi 0.84.4 (13 built-in Claude rows) vs 0.79.0
  (clean). The denylist is operator config rather than a code constant
  because the deployment knows which credential vars it projects into
  runner pods — only the intersection of "projected" and "sniffed by pi"
  can flood — so the list lives next to the projection (e.g.
  `ANTHROPIC_AUTH_TOKEN`, `DEEPSEEK_API_KEY`) and can absorb future vars
  without an Omnigent release.
- **Overlap with upstream:** none — upstream has no managed-gateway
  deployment with dummy credential env; the env-var-driven `env_unset`
  config is a candidate for an upstream discussion (generic per-harness
  denylist config), as is a pi-side opt-out for built-in catalog
  activation.

### Web UX: new-session repo list cache + last selection (PR #67)

- **What:** `57e3a5158` caches the GitHub repo list in the new-session
  dialog and remembers the last repo selection across sessions.
- **Why:** The repo query refetched (and re-rendered a loading state)
  on every dialog open.
- **Upstreamable:** yes, generic web polish; not proposed.
- **Lifetime:** open-ended.

### Web UX: per-chat sandbox running indicator (PR #68)

- **What:** `1cffb6c72` shows a per-chat indicator in the sidebar while
  a session's managed sandbox is running.
- **Why:** Sandbox liveness was only visible inside the open chat.
- **Upstreamable:** yes, generic web polish; not proposed.
- **Lifetime:** open-ended.

### Second upstream sync + staging hardening (sync/upstream-main-2)

- **What:** `ed3b8545a` merges `omnigent-ai/main` (+90 commits:
  upstream #4766–#5036 incl. the upstream pi launch model picker
  #4961) into `staging`; `2a6d95185` (#2) keeps staging's own
  `.github/workflows` during the sync; `0bf8b1313` merges the
  `github_connections` + `task_summary` alembic heads instead of
  reparenting; `e7b346ded` fixes the sandbox-provider entrypoint lookup
  after the upstream `ManagedSandboxDeployment` rename.
- **Why:** Routine upstream reconciliation; the two fixes are
  staging-only collision points (fork migrations, fork provider
  resolution).
- **Upstreamable:** no (sync mechanics).
- **Lifetime:** permanent pattern for future syncs.

### Third upstream sync (this PR)

- **What:** Two-step reconciliation. `75325fe1b` (direct on `staging`)
  migrated the fork's GitHub App identity onto upstream's native
  `connections` architecture (bridge via `_resolve_owner_github_identity`;
  SSHPiper / Open-in-VS-Code wiring kept) and removed ci-watch, sandbox
  credential refresh, and the session PR listing — their entries are
  removed below. This PR completes the sync by merging the ten upstream
  commits landed after the mirror point (`358c0df67`: `#6660` session
  navigation, `#6678` GitHub panel split, `#6307` shell-before-probes,
  `#6571` sidebar click-through, `#6573` mobile tap targets, `#6591`
  composer pill modal, plus `#2445`, `#6415`, `#6557`, `#6683`).
- **Sync decisions:**
  - Adopted upstream for: pi model-entry rendering (`#71` superseded,
    entry removed), opencode-native runner support (now upstream, entry
    trimmed to the image bake), and the `#77` islo/lint repair deltas
    (reverted, entry removed).
  - Kept fork behavior for: the per-user GitHub identity bridge +
    SSHPiper, k8s `config_map_mounts`, jcode ACP row + `/opt/jcode`
    wiring + pinned `EXTRA_HARNESS_CLIS`, the opencode image pin, fork
    web UX (NewChatDialog multi-repo picker + cache prefs, sidebar
    state), and fork CI workflows.
  - Alembic: upstream's `ga1b2c3d4e5f` `connections` migration replaces
    the fork's github-connections file with the same id; single head
    `4e8542fa67c4` verified.
- **Why:** Routine upstream reconciliation; the staging-side half went
  direct, this PR finishes it.
- **Upstreamable:** the reconcile mechanics are sync-only.
- **Lifetime:** permanent pattern for future syncs.

### Host image + server: all-harness runner bundle (#62)

- **What:** Bare-minimum port of caffeinelabs/omnigent#57 onto
  `staging` @ `7b8cd26`. The CLI bake itself is now **upstream**
  (upstream's `EXTRA_HARNESS_CLIS` + `install-harness-cli.sh`, merged
  as #4148, with the `agy` row and sandbox-user smoke check). What
  remains fork-only on staging:
  - `deploy/docker/Dockerfile` (+ `.ubi`) default
    `EXTRA_HARNESS_CLIS="goose@1.46.0 jcode@0.77.1"` (upstream's
    default is empty), plus the `/opt/jcode` wiring, `mcp-remote`, and
    seeded `deploy/docker/preview-jcode/jcode-agent.yaml`
    (`OMNIGENT_BUILTIN_AGENT_DIRS`) — all deployment-specific.
  - `jcode` ACP catalog row (`omnigent_mcp=False` →
    `HARNESS_ACP_OMNIGENT_MCP`) + `omni setup` drill-in.
  - `sandbox.kubernetes.config_map_mounts` +
    `OMNIGENT_KUBERNETES_CONFIG_MAP_MOUNTS` env fallback.
- **Why:** Staging sandboxes launch claude, codex, pi, goose, and jcode.
- **Upstreamable:** the bake is done. `config_map_mounts` / the ACP
  field / jcode catalog row are separate upstream candidates.
- **Lifetime:** the pinned default and wiring last until the deployment
  moves; `config_map_mounts` and the catalog row until upstream adopts
  them.

### GitHub App sandbox auth + per-user identity (PR #1, follow-ups)

- **What:** `fe3cb8786` (GitHub App sandbox auth + Open in VS Code via
  SSHPiper; server routes, k8s SSH policy, web UI), `5944eabb6`
  (attribute managed-runner commits to the session owner), `c7196a64a`
  (reparent `add_github_connections` onto the upstream alembic head).
- **Why:** Sandbox sessions act as the signed-in user (git + gh).
  Engineers attach over SSH via sshpiper.
- **Upstreamable:** GitHub App auth is generic; SSHPiper / VS Code is
  deployment-shaped. Not proposed.
- **Lifetime:** open-ended; required by the staging deployment.

### Session PRs + multi-repo sessions (#17–#22, `7b8cd2606`)

- **What:** Fork `NewChatDialog` repo picker with multi-repo sessions,
  per-repo branch selection, and repo-list cache + last-selection UX
  (`sandboxRepoPreferences`) — all still on staging. The session PR
  listing, closed/merged badges, per-session commit-trailer scoping, and
  the Open-in-Omnigent body link were removed by the third sync's
  native-connections migration (upstream's read-only GitHub panel covers
  the repo view now).
- **Why:** Staging runs real multi-repo sessions.
- **Upstreamable:** yes in principle; not proposed.
- **Lifetime:** open-ended.

### opencode-native stack (#31–#48 + direct commits)

- **What:** Runner-side support (gateway wiring, `GIT_CONFIG_*`
  passthrough, MCP `oauth`, `opencode_permission`, workspace-root
  `AGENTS.md`, `OMNIGENT_RUNNER_ENV_PASSTHROUGH`) is now upstream (#5041
  and later). The fork keeps the deployment-shaped image bake: pinned
  `OPENCODE_VERSION` (1.18.13) + `OPENROUTER_*` env and the mise/
  OpenSSH/VS Code tooling blocks in `deploy/docker/Dockerfile`.
- **Why:** Staging runs opencode against Bifrost with deployment MCP
  (Datadog, Linear) and workspace rules.
- **Upstreamable:** the remaining bake is deployment-specific.
- **Lifetime:** open-ended while staging runs opencode.

### Fork CI/CD on staging (PRs #5, #7, #10, #11 + direct commits)

- **What:** `992f8633a` + `b623eb8c1` (caffeine server + host images
  from `staging`, kubernetes extra), #5 preview-image builds, #11
  sortable `<branch>-<timestamp>` tags, #10 staging web-build fix, #7
  drop inherited Maintainer Approval, `68f3b36b9` (keep staging
  `.github/workflows` on upstream sync).
- **Why:** Staging and preview envs build from fork branches, not
  upstream tags.
- **Upstreamable:** no (GHCR + Flux).
- **Lifetime:** permanent while the staging deployment exists.

### Host-image app-dev tooling (PR #12, reverted by #13)

- **What:** #12 folded sandbox app-dev tooling into the host image; #13
  reverted it. Net delta zero. Tooling lives in infra
  `omnigent-sandbox-host`.
- **Lifetime:** closed (history only).

### Inherited Databricks-internal fork base (PRs #2xxx–#3xxx)

- **What:** Staging's oldest layer is a Databricks-internal omnigent
  fork never merged to `omnigent-ai/main` (`git cherry upstream/main
  origin/staging` has no patch equivalents). Roughly a hundred commits:
  Slack (#2569), embedded browser (#2248), ScheduledTasks (#2247,
  #3186), harness bench (#2307–#2476), pinned sessions (#3189+),
  worktree UX (#2088, #2094), turn-rail minimap (#2285), subagent graph
  (#1201), smart-routing (#3215), Docker RuntimeCaps (#3222),
  sessions.py split (#3194), plus web polish.
  Caffeinelabs later adopted `omnigent-ai/main` via merge PRs (#9,
  `35e796104`, `2b64caa92`).
- **Why:** Staging predates the fork's upstream-tracking workflow. The
  deployment still runs some of this (Slack, scheduled tasks).
- **Upstreamable:** only piecemeal. staging↔develop is a real merge,
  not a fast-forward.
- **Lifetime:** until staging is replaced by / rebased onto `develop`.
  Do not grow this layer.

## How to verify

Fork `main` is a mirror of upstream `main`, so the fork delta is exactly

    git diff upstream/main...origin/staging

minus the inherited Databricks layer above. Use `git cherry
upstream/main origin/staging` (or the fork PR numbers in these entries)
before calling something a delta.

