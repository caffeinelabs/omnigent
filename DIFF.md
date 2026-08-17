# DIFF.md — fork deltas vs upstream

Every change `caffeinelabs/omnigent` carries beyond upstream
`omnigent-ai/omnigent`, with rationale. `main` mirrors upstream `main`;
`develop` is the caffeinelabs working branch.

Read this before any upstream sync or rebase. Update it **in the same commit**
as any new delta (see the *Caffeinelabs fork* section of `AGENTS.md`).

Entry format: **What** (files), **Why** (rationale), **Upstreamable** (would
upstream take it), **Lifetime** (permanent fork delta / until upstream lands X).

---

## Deltas on `develop`

### CI: per-branch preview image builds

- **What:** `.github/workflows/preview-env.yml`. Builds server + host images
  (server with `OMNIGENT_EXTRAS=kubernetes`), tagged `<slug>-<UTC-timestamp>`
  and pushed to `ghcr.io/caffeinelabs/omnigent-{server,host}`. Auto-builds when
  a PR into `develop` carries the `preview` label; manual for any ref:
  `gh workflow run preview-env.yml --ref develop -f ref=<branch>`. Emits the
  ready-to-commit Flux Kustomization snippet for `caffeinelabs/infra`.
- **Why:** Sandbox preview environments (`caffeinelabs/infra`:
  `clusters/caffeine-sandbox/omnigent-previews/`) resolve the newest image per
  branch via a Flux `ImagePolicy` on the sortable tag. Upstream publishes only
  release images without the `kubernetes` extra (the sandbox launcher imports
  the k8s client), and its host image tops out at v0.1.x while host↔server
  protocol must come from the same commit.
- **Upstreamable:** no — tied to our GHCR and the infra GitOps flow.
- **Lifetime:** permanent while preview environments exist.

### Host image: all-harness runner image (goose + jcode baked, jcode profile, config_map_mounts)

- **What:** `deploy/docker/Dockerfile` (+ `Dockerfile.ubi`) bake the goose CLI
  (pinned, per-arch sha256-verified) and jcode (pinned via its checksummed
  installer) into the host image next to upstream's npm-installed
  claude/codex/pi; `/opt/jcode` baked profile with symlinks into
  `/mnt/config/jcode` + `mcp-remote` for jcode's stdio-only MCP client;
  `deploy/docker/preview-jcode/jcode-agent.yaml` seeded via
  `OMNIGENT_BUILTIN_AGENT_DIRS`. `omnigent/acp_cli_harnesses.py` adds the
  `jcode` ACP catalog row with an `omnigent_mcp` opt-out (jcode rejects
  `mcpServers` in `session/new`; MCP is configured via `~/.jcode/mcp.json`),
  threaded through `omnigent/runtime/workflow.py` as
  `HARNESS_ACP_OMNIGENT_MCP`. `omnigent/onboarding/sandboxes/kubernetes.py` +
  `omnigent/server/managed_hosts.py` add `sandbox.kubernetes.config_map_mounts`
  (with an `OMNIGENT_KUBERNETES_CONFIG_MAP_MOUNTS` JSON env fallback for
  GitOps-rendered shared configs).
- **Why:** One runner image runs all five supported harnesses (claude, codex,
  pi, goose, jcode) in managed k8s sandboxes. Runner pods get an emptyDir
  HOME, so jcode's provider/MCP config must come from a baked profile plus a
  cluster-mounted ConfigMap.
- **Upstreamable:** `config_map_mounts` and the `omnigent_mcp` row field are
  generic and upstreamable; the image bakes and seeded jcode agent are
  deployment-specific (jcode is our own harness, not upstream's set).
- **Lifetime:** until upstream takes `config_map_mounts` + the ACP row field;
  the image/jcode-agent parts are permanent fork deltas.

### Docs: VM harness setup guide

- **What:** `docs/harness-setup-vm.md` — install + configure Omnigent (fork)
  and the five supported harnesses (Claude Code, Codex, Pi, Goose, jcode) on a
  VM/EC2 (Ubuntu 24.04+), routed through our Bifrost gateway; verified
  working-example config, per-harness upgrade one-liners + float/pin policy,
  and a per-harness verification matrix.
- **Why:** SRE-681 exit criterion — colleagues must be able to reproduce the
  environment from a doc; upstream docs cover neither our gateway nor the fork's
  harness set.
- **Upstreamable:** no — references our internal gateway, key handling, and
  Linear tickets.
- **Lifetime:** living doc; keep current as harness setup changes.

### Process: this ledger + agent instructions

- **What:** `DIFF.md`; the *Caffeinelabs fork* section appended to `AGENTS.md`.
- **Why:** staging-era deltas were discoverable only by archaeology over a
  diverged branch. A single ledger makes sync/rebase decisions explicit and
  keeps fork deltas intentional.
- **Upstreamable:** section content is fork-specific; upstream keeps its own
  `AGENTS.md` (ours appends one marked section at the end to minimize
  sync conflicts).
- **Lifetime:** permanent.

## Parked on `staging` (deliberately not on `develop`)

These exist on `origin/staging` (+ `staging-backup-2026-08-11`) and were left
out when `develop` was cut. Revive by porting from there if needed.

| Item | Why parked |
|---|---|
| `caffeine-server-image.yml` (staging-tagged server+host builds) | Feeds the main sandbox env, kept on `staging` until the default-branch flip; tags are hardcoded `staging` and must be parameterized before porting |
| opencode-native saga (#31–#48: MCP oauth, permissions, workspace AGENTS.md, DD keys, 1.18.13) | opencode not in the supported harness set (claude, codex, pi, goose, jcode) |
| opencode-in-host-image + `OPENROUTER_` passthrough | harnesses route through Bifrost, which holds provider keys |
| ci-watch (wake session when PR CI concludes, #23/#24) | usefulness unconfirmed; cheap to port later |
| sandbox git attribution → session owner (5944eabb6) | generic and good; awaiting a porting decision |
| `OMNIGENT_RUNNER_ENV_PASSTHROUGH` forwarding (a3cbf0a32/#47) | likely needed for harness config in k8s; awaiting confirmation of requirement |
| `add_github_connections` DB migration reparent (c7196a64a) | tied to the `omnigent-github-mvp` sandbox deployment; needs an owner decision |
