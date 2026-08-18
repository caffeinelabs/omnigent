# DIFF.md — staging-branch fork delta ledger

This file tracks what the fork's `staging` branch carries on top of the
upstream it last synced (omnigent-ai/omnigent main @ the 2026-08-11 sync).
`develop` keeps its own ledger; this one covers deltas introduced directly
onto `staging`. Staging's older feature deltas (opencode-native stack,
session-PRs, multi-repo picker, ci-watch — PRs #10–#48) predate this ledger
and are not itemized here.

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
  `OMNIGENT_KUBERNETES_CONFIG_MAP_MOUNTS` JSON env fallback — adapted to
  staging's pre-`ManagedSandboxDeployment` parse shape (no `.default`
  unwrap in the tests).
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
