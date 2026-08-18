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

### Host image: all-harness runner image (goose + jcode + dsh-acp)

- **What:** `deploy/docker/Dockerfile` (+ `Dockerfile.ubi`) bake goose, jcode,
  and DeepSeek Harness (`dsh-acp`) via upstream's `EXTRA_HARNESS_CLIS`
  mechanism (omnigent-ai/omnigent#4148). Fork ARG default is
  `goose@1.46.0 jcode@0.77.1 deepseek@0.4.14`. Debian host `NODE_VERSION`
  is `22.19` (`dsh-acp` needs Node >= 22.15 / `createZstdDecompress`).
  Fork-only on top: `/opt/jcode` profile + `mcp-remote`; baked
  `jcode-agent.yaml` and `deepseek-agent.yaml` via
  `OMNIGENT_BUILTIN_AGENT_DIRS`. `omnigent/acp_cli_harnesses.py` adds
  `jcode` (`omnigent_mcp` opt-out) and `deepseek` (OpenMA `dsh-acp`,
  `env_passthrough` for `DEEPSEEK_*` / `DSH_*`).
  `HARNESS_ACP_OMNIGENT_MCP` + row `env_passthrough` in
  `omnigent/runtime/workflow.py`. `config_map_mounts` +
  `OMNIGENT_KUBERNETES_CONFIG_MAP_MOUNTS` env fallback.
- **Why:** One runner image for claude, codex, pi, goose, jcode, and
  DeepSeek Harness in managed k8s sandboxes. Official `@deepseek-ai/dsh-acp`
  is too thin (no MCP/tools/usage); OpenMA adapter is the path in
  omnigent-ai/omnigent#4863 / #4864.
- **Upstreamable:** `deepseek` catalog row + `env_passthrough` match #4863
  (CONFLICTING). CLI bake row is a candidate for #4148 follow-up.
  `/opt/jcode` wiring stays a fork delta.
- **Lifetime:** catalog row until #4863 lands; ARG pin + Node 22.19 until
  the host image default covers dsh-acp.

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
