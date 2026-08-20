# DIFF.md — staging-branch fork delta ledger

This file tracks what the fork's `staging` branch carries that is **not**
on the fork's `main` (caffeinelabs/omnigent `main`, currently
`8aaf72c91`, a mirror of an older `omnigent-ai/omnigent` main).

`develop` keeps its own ledger. This one is for `staging`.

`git diff origin/main...origin/staging` is **not** only caffeine fork
work. It also contains:

1. **Caffeinelabs fork work on staging** (PRs #1–#48 + direct commits).
2. **This PR's harness-bundle port** (#62).
3. **An inherited Databricks-internal fork base** (PRs #2xxx–#3xxx) that
   never landed in `omnigent-ai/main`.
4. **Upstream drift the other way:** fork `main` is behind current
   `omnigent-ai/main`. Some files look like staging-only because `main`
   never picked up later upstream commits. Those are **not** fork deltas.

Do not grow layer 3. New work lands on `develop` first and is ported to
`staging` on purpose (this PR).

## Entries

### Host image + server: all-harness runner bundle (this PR / #62)

- **What:** Bare-minimum port of caffeinelabs/omnigent#57 onto
  `staging` @ `7b8cd26`. **No merge from `main`.** Cherry-picks only
  `9f5c266a7` + `88574e7c5`.
  - `deploy/docker/Dockerfile` (+ `.ubi`) bake goose `1.46.0` + jcode
    `0.77.1` via upstream `EXTRA_HARNESS_CLIS`
    (`deploy/docker/install-harness-cli.sh` verbatim).
  - `/opt/jcode` wiring + `mcp-remote` + seeded
    `deploy/docker/preview-jcode/jcode-agent.yaml`.
  - `jcode` ACP catalog row (`omnigent_mcp=False` →
    `HARNESS_ACP_OMNIGENT_MCP`) + `omni setup` drill-in.
  - `sandbox.kubernetes.config_map_mounts` +
    `OMNIGENT_KUBERNETES_CONFIG_MAP_MOUNTS` env fallback, on staging's
    pre-`ManagedSandboxDeployment` parse shape.
- **Why:** Staging sandboxes should launch claude, codex, pi, goose, and
  jcode. Claude / codex / pi CLIs and Bifrost config are already on
  `staging-7b8cd26` + host `aeb12ae`. This only adds goose + jcode.
- **Not in this port:** `main` merge, workflow restore,
  `ManagedSandboxDeployment`, `OMNIGENT_HARNESS_INSTALL_ENABLED`, Cursor.
- **Upstreamable:** #4148 (CLI bake) + develop line for
  `config_map_mounts` / ACP field. This copy dies when staging next
  syncs a develop that contains #57.
- **Lifetime:** until that sync. Prefer the develop versions on conflict.

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

### Session PRs + multi-repo sessions (PRs #17–#22, `7b8cd2606`)

- **What:** New-chat GitHub repo picker, multi-repo + per-repo branches
  (#17, #18); sub-repo file changes + session PR list (#20); closed /
  merged + badges (#21); scoped by per-session commit trailer (#22) and
  by Open-in-Omnigent body link / cross-repo search (`7b8cd2606`).
- **Why:** Staging runs real multi-repo sessions; PRs must attach to the
  session that opened them.
- **Upstreamable:** yes in principle; not proposed.
- **Lifetime:** open-ended.

### ci-watch (PRs #23, #24)

- **What:** Wake a session when its PRs' CI concludes, plus a GET
  dry-run diagnostic.
- **Why:** Agent sessions can react to CI without polling.
- **Upstreamable:** yes; not proposed.
- **Lifetime:** open-ended.

### opencode-native stack (PRs #31–#48 + direct commits)

- **What:** `3428eab9c` (env-configured gateway → Bifrost), `2233b62e3`
  (`GIT_CONFIG_*` passthrough), `bffe36c0b` (bake opencode +
  `OPENROUTER_*`), then #31–#48: MCP `oauth` passthrough, opencode
  1.18.13 (streamable-HTTP MCP), `OMNIGENT_RUNNER_ENV_PASSTHROUGH`
  (#46, #47), `opencode_permission` (#41, #45), workspace-root
  `AGENTS.md` via `instructions` (#42, #44, #45), drop hardcoded DD
  keys (#48). Related: #16 (`ee3f30fea`) — sandbox no longer generates
  workspace `AGENTS.md` (deployment mounts its own).
- **Why:** Staging runs opencode against Bifrost with deployment MCP
  (Datadog, Linear) and workspace rules.
- **Upstreamable:** mixed. `OMNIGENT_RUNNER_ENV_PASSTHROUGH` and MCP
  `oauth` are generic. Develop's supported set is still the five
  harnesses, so this stack stays staging-only unless that changes.
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

### Claude-native: auto-approve project `.mcp.json` (this PR)

- **What:** `build_hook_settings` sets `enableAllProjectMcpServers: true`
  on the invocation `--settings` file.
- **Why:** Caffeine clones ship `.mcp.json` with `vexp`. Claude Code
  blocks the TUI on "New MCP server found in this project". Omnigent
  injects via tmux and only waits for the input prompt, so the first
  message times out. `--settings` is honored in an untrusted workspace;
  a checked-in project settings file is not.
- **Upstreamable:** yes (same hang on any repo with project MCP).
- **Lifetime:** until upstreamed.

### Not a fork delta (do not treat as staging-only)

Fork `main` lags current `omnigent-ai/main`. A `staging` vs `main` file
list will also show later upstream commits that `main` never took. Those
are upstream drift, not caffeine work. Use `git cherry upstream/main
origin/staging` (or the PR numbers above) before calling something a
delta.
