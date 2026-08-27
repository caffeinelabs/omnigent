# Agent-sandbox provider (prototype)

A managed-sandbox provider (`sandbox.provider: agentsandbox`) that runs each
sandbox as a **`Sandbox` custom resource** from
[kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox)
(`agents.x-k8s.io/v1beta1`) instead of a raw `batch/v1` Job. It exists to get the
lifecycle features other managed runtimes (Modal, Islo, …) offer and plain Pods
can't: **persist a session**, and **shut it down when idle**.

Status: prototype. Code + unit tests land; it is **not yet deployed** (needs the
agent-sandbox controller installed on the cluster and an RBAC change). Kept as a
branch on top of the GitHub-integration stack, not a stack PR.

## Why a CRD instead of a Job

Same entrypoint-as-host model as the `kubernetes` provider — the container runs
`omnigent host` and dials back to the server over a WebSocket, so the server's
liveness/connection machinery is reused unchanged (no Service, no `pods/exec`,
no pod-IP resolution). Only the *lifecycle* moves to the controller:

| Need | Job provider | agent-sandbox provider |
| --- | --- | --- |
| Persist workspace across restart | operator-supplied PVC mounts | `spec.volumeClaimTemplates` — controller-managed PVC backing HOME (workspace clone + `~/.omnigent`) |
| Shut down when idle | ✗ (create-then-delete only) | `spec.operatingMode: Suspended` frees the pod, keeps the PVC (`suspend()`); `Running` reattaches (`resume()`) |
| Hard reclaim deadline | Job `activeDeadlineSeconds` | `spec.shutdownTime` |
| Stable network identity | ✗ | `spec.service` → `status.serviceFQDN` |

`resume` is a true in-place flip of `operatingMode` (the persisted PVC reattaches
to a fresh pod), unlike the Job provider's delete-and-recreate.

## Code

- `omnigent/onboarding/sandboxes/agentsandbox.py` — `AgentSandboxLauncher`
  (subclasses `KubernetesSandboxLauncher`, reusing all config resolution / token
  Secret / pod template / diagnostics) + `build_sandbox_manifest` (pure; reuses
  `build_job_manifest` for the hardened pod template, reshapes into the CR).
  Drives the CR via `CustomObjectsApi` (`sandboxes` plural). Adds `suspend()`.
- Registered in `registry.py` (`_builtin_contribution`) and
  `server/managed_hosts.py` (`SUPPORTED_SANDBOX_PROVIDERS`,
  `PROVIDERS_WITH_MANAGED_LAUNCH`, a `provider: agentsandbox` parse branch, and
  `_agentsandbox_launcher_factory`).
- Tests: `tests/onboarding/sandboxes/test_agentsandbox.py`.

## Config

```yaml
sandbox:
  provider: agentsandbox
  agentsandbox:
    namespace: omnigent-sandboxes
    persist: true              # PVC-backed HOME (default true)
    workspace_storage: 20Gi
    storage_class: <optional>
    idle_shutdown_seconds: 7200  # optional shutdownTime reclaim deadline
    service: false
    # plus the kubernetes pod-template keys: image, env, secret_name,
    # service_account, node_selector, kubeconfig, in_cluster, pod_ready_timeout_s
```

## Deploy prerequisites (not yet done)

1. Install the controller:
   `kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/<VERSION>/sandbox-with-extensions.yaml`
2. Grant the server ServiceAccount `sandboxes` + `sandboxes/status` verbs in
   `agents.x-k8s.io` (create/get/list/watch/patch/delete) in place of the Job
   RBAC; keep the existing `secrets` create/delete rights (token Secret).
3. Point the github-mvp overlay `sandbox-config.yaml` at `provider: agentsandbox`.

## Open items / assumptions to confirm against the CRD

- `volumeClaimTemplates` is assumed to be injected into the pod by matching name
  (StatefulSet-style) — the provider drops the pod template's `home` emptyDir and
  declares a `home` claim template. Verify the controller's binding rule.
- `operatingMode` values assumed `Running` / `Suspended`.
- Server-side **idle detection** (when to call `suspend`) is not wired yet —
  there is no idle reaper today (only Islo self-idles). `suspend()` is the
  primitive; a reaper (or a `shutdownTime` refresh on activity) is the follow-up.
- `resume_managed_host` already exists server-side and will drive `resume()`.
