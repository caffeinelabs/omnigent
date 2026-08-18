# DIFF.md — staging-branch fork delta ledger

This file tracks what the fork's `staging` branch carries on top of upstream
(`omnigent-ai/omnigent` main). `develop` keeps its own ledger for the new
default-branch line; this one covers `staging`.

Reference point: `upstream/main`. Note the fork's `main` merely mirrors an
older upstream state, so `staging` vs `main` additionally shows upstream
drift that is not a fork delta.

`staging` is a long-lived deployment branch with three layers of history,
ledgered below newest-first:

1. caffeinelabs fork work (PRs #1–#48 + direct commits),
2. this harness-bundle port,
3. an inherited Databricks-internal fork base (PRs #2xxx–#3xxx) that never
   landed in `omnigent-ai/main`.

## Entries

### Host image + server: all-harness runner bundle (ported from feat-harness-bundle)

- **What:** A port of caffeinelabs/omnigent#57 (`feat-harness-bundle` →
  `develop`) onto staging's older base. `deploy/docker/Dockerfile` (+ `.ubi`)
  bake goose + jcode via upstream omnigent-ai/omnigent#4148's
  `EXTRA_HARNESS_CLIS` mechanism (`deploy/docker/install-harness-cli.sh`
  carried verbatim; fork delta: non-empty ARG default
  `goose@1.46.0 jcode@0.77.1`); `/opt/jcode` baked profile with symlinks into
  `/mnt/config/jcode` + `mcp-remote`; `deploy/docker/preview-jcode/jcode-agent.yaml`
  seeded via `OMNIGENT_BUILTIN_AGENT_DIRS`. `omnigent/acp_cli_harnesses.py`
  adds the `jcode` ACP catalog row with an `omnigent_mcp` opt-out threaded
  through `omnigent/runtime/workflow.py` as `HARNESS_ACP_OMNIGENT_MCP`, plus
  develop's `omni setup` drill-in for builtin ACP CLI rows
  (`omnigent/cli_config.py`). `omnigent/server/managed_hosts.py` +
  `omnigent/onboarding/sandboxes/kubernetes.py` add
  `sandbox.kubernetes.config_map_mounts` with the
  `OMNIGENT_KUBERNETES_CONFIG_MAP_MOUNTS` JSON env fallback. After #60
  brought `ManagedSandboxDeployment`, the tests unwrap `.default` like
  the rest of the file.
- **Why:** The staging deployment (clusters/caffeine-sandbox/omnigent in
  caffeinelabs/infra) runs managed k8s sandboxes; one runner image should
  launch claude, codex, pi, goose, and jcode with zero per-pod setup.
- **Upstreamable:** already upstreamed/upstream-bound — #4148 (CLI baking),
  `config_map_mounts` + the ACP row field proposed via the `develop` line.
  This port exists only because staging has not yet synced that develop
  state; it collapses away when staging next merges develop.
- **Lifetime:** until staging syncs a develop that contains #57. Resolve
  conflicts in favor of the develop versions — they are the same changes
  integrated with the newer base.

### CI restore: merge fork `main` into `staging` (PR #60)

- **What:** `f55ae0034` merged fork `main` into `staging` so lint / e2e-ui /
  web-tests use `./.github/actions/setup-pnpm` again (the old
  `setup-node` local action was deleted upstream). Also brings Devin as a
  third builtin ACP CLI row. This harness-bundle branch rebases the
  `omni setup` overview tests onto three ACP rows (Devin, Grok Build,
  Jcode) instead of two.
- **Why:** Staging CI was failing at "Set up Node 20" before any test ran.
- **Upstreamable:** n/a (fork workflow restore).
- **Lifetime:** until staging's workflows stay in sync with fork `main`.

### GitHub App sandbox auth + per-user identity (PR #1, follow-ups)

- **What:** `fe3cb8786` (GitHub App sandbox auth + "Open in VS Code" via
  SSHPiper; ~57 files: server routes, k8s SSH policy, web UI),
  `5944eabb6` (attribute managed-runner commits to the session owner),
  `c7196a64a` (reparent the `add_github_connections` migration onto the
  upstream head after a sync).
- **Why:** Sandbox sessions act as the signed-in user (git + gh) against
  GitHub, and engineers attach to pods over SSH via sshpiper.
- **Upstreamable:** partially (GitHub App auth is generic; the SSHPiper /
  VS Code parts are deployment-shaped). Not proposed to date.
- **Lifetime:** open-ended; required by the staging deployment.

### Session PRs + multi-repo sessions (PRs #17–#22, `7b8cd2606`)

- **What:** New-chat GitHub repo picker with multi-repo selection and
  per-repo branches (#17, #18); sub-repo file changes + session PR surfacing
  (#20); session PRs list with closed/merged + badges (#21), scoped by
  per-session commit trailer (#22) and by Open-in-Omnigent body link with
  cross-repo search (`7b8cd2606`).
- **Why:** Staging runs real engineering sessions across several repos;
  the UI must attribute PRs to the session that made them.
- **Upstreamable:** yes in principle; not proposed (UI is staging-driven).
- **Lifetime:** open-ended.

### ci-watch (PRs #23, #24)

- **What:** Wake a session when its PRs' CI concludes, plus a GET dry-run
  diagnostic endpoint.
- **Why:** Lets agent sessions react to CI without polling.
- **Upstreamable:** yes; not proposed.
- **Lifetime:** open-ended.

### opencode-native stack (PRs #31–#48 + direct commits)

- **What:** `3428eab9c` (generic env-configured gateway — route opencode
  through Bifrost), `2233b62e3` (GIT_CONFIG_* passthrough to opencode's
  git), `bffe36c0b` (bake opencode into the host image + OPENROUTER_*
  passthrough), then #31–#48: MCP `oauth` passthrough (config + MCPTool
  runtime), opencode 1.18.13 (streamable-HTTP MCP), DD_API_KEY/DD_APP_KEY
  passthrough later generalized to `OMNIGENT_RUNNER_ENV_PASSTHROUGH`
  (#46, #47), `opencode_permission` agent-spec passthrough (#41, #45),
  workspace-root AGENTS.md via `instructions` (#42, #44, #45), drop
  hardcoded DD keys from the serve allowlist (#48). Related: `ee3f30fea`
  (#16, sandbox stops generating the workspace AGENTS.md — the deployment
  mounts its own).
- **Why:** The staging deployment runs opencode against its own gateway
  with deployment-owned MCP servers (Datadog, Linear) and workspace rules.
- **Upstreamable:** mixed — `OMNIGENT_RUNNER_ENV_PASSTHROUGH` and the MCP
  `oauth` carry are generic; the rest is deployment wiring. Note: develop's
  ledger records that opencode is not in the supported five-harness set,
  so this stack stays staging-only unless that policy changes.
- **Lifetime:** open-ended while staging runs opencode workloads.

### Fork CI/CD on staging (PRs #5, #7, #10, #11 + direct commits)

- **What:** `992f8633a` + `b623eb8c1` (build the caffeine server + host
  images from the staging branch, kubernetes extra), `d74561e10` (#5,
  per-branch preview-image builds), `ff5520004` (#11, sortable
  `<branch>-<timestamp>` tags for Flux ImagePolicy), `0be16c42c` (#10,
  staging web build fix), `1c7cb9c50` (#7, drop the inherited Maintainer
  Approval workflow), `68f3b36b9` (keep staging's `.github/workflows`
  during upstream syncs).
- **Why:** The staging deployment and the preview envs build from fork
  branches, not upstream tags.
- **Upstreamable:** no — tied to our GHCR + Flux flow.
- **Lifetime:** permanent while the staging deployment exists.

### Host-image app-dev tooling (PR #12, reverted by #13)

- **What:** `90e9b5986` folded sandbox app-dev tooling into the host image;
  `289b49457` reverted it. Net code delta is zero; the tooling now lives in
  the infra-built `omnigent-sandbox-host` wrapper image
  (caffeinelabs/infra `bases/system-components/omnigent-sandbox`).
- **Why:** Deployment-specific image flavor belongs in infra, not in the
  fork's base image.
- **Lifetime:** closed (ledgered for history).

### Inherited Databricks-internal fork base (PRs #2xxx–#3xxx)

- **What:** Staging's oldest layer descends from a Databricks-internal
  omnigent fork whose PRs were never merged to `omnigent-ai/main` —
  verified via `git cherry upstream/main origin/staging` (no patch
  equivalents). Roughly a hundred commits: Slack integration (#2569),
  embedded browser (#2248), ScheduledTasks (#2247, #3186), harness bench
  probes (#2307, #2313, #2350–#2351, #2467–#2476), pinned sessions
  server-side (#3189 + follow-ups), worktree UX (#2088, #2094), turn-rail
  minimap (#2285), subagent graph view (#1201), smart-routing config
  enablement (#3215), Docker entrypoint RuntimeCaps wiring (#3222),
  sessions.py domain split (#3194), plus assorted web/UX polish and
  dependency bumps. The caffeinelabs fork adopted `omnigent-ai/main` as
  upstream via merge PRs (#9, `35e796104`, `2b64caa92`).
- **Why:** Historical: staging predates the caffeinelabs fork's
  upstream-tracking workflow; the deployment still runs features from this
  base (e.g. Slack integration, scheduled tasks).
- **Upstreamable:** only piecemeal, PR by PR; not planned as a whole.
  `git cherry` shows none of it exists upstream even in patch-equivalent
  form, so a staging↔develop reconciliation is a real merge effort, not a
  fast-forward.
- **Lifetime:** until staging is rebased onto, or replaced by, the develop
  line. Do not grow this layer: new work goes to `develop` first and is
  ported to staging deliberately (like the harness bundle above).
