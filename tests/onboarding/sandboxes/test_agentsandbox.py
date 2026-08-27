"""
Tests for the agent-sandbox (Sandbox CRD) launcher.

The pure ``build_sandbox_manifest`` builder is the primary unit surface (it
decides persistence + lifecycle shape). The launcher's create/suspend/resume/
terminate are exercised by injecting fake ``CustomObjectsApi`` / ``CoreV1Api``
recorders via the ``_load_custom`` / ``_load_core`` seams — no real cluster.
"""

from __future__ import annotations

import sys
import types

import pytest

from omnigent.onboarding.sandboxes.agentsandbox import (
    OPERATING_MODE_RUNNING,
    OPERATING_MODE_SUSPENDED,
    SANDBOX_API_GROUP,
    SANDBOX_API_VERSION,
    SANDBOX_PLURAL,
    AgentSandboxLauncher,
    build_sandbox_manifest,
)
from omnigent.onboarding.sandboxes.base import SandboxHostLauncher

_MANIFEST_KW = {
    "image": "ghcr.io/omnigent-ai/omnigent-host:latest",
    "service_account": "omnigent-runner",
    "host_id": "host_abcdef",
    "host_name": "managed-abcdef",
    "server_url": "http://srv.example.com",
    "token_secret_name": "omnigent-managed-abc-token",
    "harness_secret": "omnigent-creds",
    "env_literals": {},
    "node_selector": None,
    "workspace": "/home/omnigent/workspace",
}


def _manifest(**overrides: object) -> dict:
    kw: dict[str, object] = {
        "sandbox_name": "omnigent-managed-abc-1a2b3c",
        "namespace": "omnigent-sandboxes",
        **_MANIFEST_KW,
    }
    kw.update(overrides)
    return build_sandbox_manifest(**kw)  # type: ignore[arg-type]


# ── pure manifest tests ─────────────────────────────────────


def test_build_sandbox_manifest_is_a_sandbox_cr() -> None:
    m = _manifest()
    assert m["kind"] == "Sandbox"
    assert m["apiVersion"] == f"{SANDBOX_API_GROUP}/{SANDBOX_API_VERSION}"
    assert m["metadata"]["name"] == "omnigent-managed-abc-1a2b3c"
    assert m["metadata"]["namespace"] == "omnigent-sandboxes"


def test_pod_template_wraps_the_same_hardened_pod_spec() -> None:
    m = _manifest()
    pod_spec = m["spec"]["podTemplate"]["spec"]
    # The host container runs omnigent host under the reaper; security context
    # + service account carry over from the shared Job builder.
    assert pod_spec["serviceAccountName"] == "omnigent-runner"
    assert pod_spec["automountServiceAccountToken"] is False
    assert any(c["name"] == "host" for c in pod_spec["containers"])
    assert pod_spec["securityContext"]["runAsNonRoot"] is True


def test_operating_mode_defaults_to_running() -> None:
    assert _manifest()["spec"]["operatingMode"] == OPERATING_MODE_RUNNING


def test_persist_swaps_home_emptydir_for_a_volume_claim_template() -> None:
    m = _manifest(persist=True, workspace_storage="20Gi", storage_class="fast")
    spec = m["spec"]
    vcts = spec["volumeClaimTemplates"]
    assert [v["metadata"]["name"] for v in vcts] == ["home"]
    claim = vcts[0]["spec"]
    assert claim["resources"]["requests"]["storage"] == "20Gi"
    assert claim["storageClassName"] == "fast"
    # The ephemeral HOME emptyDir is gone (the controller binds the PVC instead).
    vol_names = [v.get("name") for v in spec["podTemplate"]["spec"]["volumes"]]
    assert "home" not in vol_names


def test_no_persist_keeps_emptydir_home_and_no_pvc() -> None:
    m = _manifest(persist=False)
    assert "volumeClaimTemplates" not in m["spec"]
    vols = m["spec"]["podTemplate"]["spec"]["volumes"]
    assert any(v.get("name") == "home" and "emptyDir" in v for v in vols)


def test_idle_shutdown_sets_shutdown_time_rfc3339() -> None:
    m = _manifest(idle_shutdown_seconds=3600)
    st = m["spec"]["shutdownTime"]
    assert isinstance(st, str) and st.endswith("Z") and "T" in st


def test_no_idle_shutdown_omits_shutdown_time() -> None:
    assert "shutdownTime" not in _manifest()["spec"]


def test_service_flag_requests_stable_service() -> None:
    assert _manifest(service=True)["spec"]["service"] is True
    assert "service" not in _manifest(service=False)["spec"]


# ── launcher config / capabilities ──────────────────────────


def test_launcher_is_a_sandbox_host_launcher_with_provider_name() -> None:
    assert issubclass(AgentSandboxLauncher, SandboxHostLauncher)
    assert AgentSandboxLauncher.provider == "agentsandbox"


def test_capabilities_advertise_managed_launch_and_in_place_resume() -> None:
    caps = AgentSandboxLauncher().capabilities
    assert caps.managed_launch is True
    assert caps.resume_stopped is True
    assert caps.programmatic_terminate is True


# ── suspend / resume / terminate via fake CustomObjectsApi ───


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> type[Exception]:
    """Inject a minimal fake ``kubernetes`` package (the SDK is an optional dep).

    Only what the launcher's delete/patch paths import is faked:
    ``kubernetes`` (for ``_ensure_sdk``) and ``kubernetes.client.rest.ApiException``.
    """

    class _ApiException(Exception):
        def __init__(self, status: int | None = None) -> None:
            self.status = status

    kmod = types.ModuleType("kubernetes")
    client_mod = types.ModuleType("kubernetes.client")
    rest_mod = types.ModuleType("kubernetes.client.rest")
    rest_mod.ApiException = _ApiException  # type: ignore[attr-defined]
    client_mod.rest = rest_mod  # type: ignore[attr-defined]
    kmod.client = client_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kubernetes", kmod)
    monkeypatch.setitem(sys.modules, "kubernetes.client", client_mod)
    monkeypatch.setitem(sys.modules, "kubernetes.client.rest", rest_mod)
    return _ApiException


class _FakeCustom:
    def __init__(self, existing: dict | None = None) -> None:
        self.patches: list[dict] = []
        self.deletes: list[dict] = []
        self.creates: list[dict] = []
        # When set, _get_sandbox sees a surviving CR (the resume-in-place path).
        self._existing = existing

    def patch_namespaced_custom_object(self, **kw: object) -> None:
        self.patches.append(kw)

    def delete_namespaced_custom_object(self, **kw: object) -> None:
        self.deletes.append(kw)

    def create_namespaced_custom_object(self, **kw: object) -> None:
        self.creates.append(kw)

    def get_namespaced_custom_object(self, **kw: object) -> dict:
        if self._existing is None:
            from kubernetes.client.rest import ApiException

            raise ApiException(status=404)
        return self._existing

    def get_namespaced_custom_object_status(self, **kw: object) -> dict:
        return {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}


class _FakeCore:
    def __init__(self) -> None:
        self.deleted_secrets: list[str] = []
        self.created_secrets: list[str] = []
        self.replaced_secrets: list[str] = []

    def delete_namespaced_secret(self, name: str, namespace: str, **kw: object) -> None:
        self.deleted_secrets.append(name)

    def create_namespaced_secret(self, namespace: str, body: object, **kw: object) -> None:
        self.created_secrets.append(namespace)

    def replace_namespaced_secret(self, name: str, namespace: str, body: object, **kw: object) -> None:
        self.replaced_secrets.append(name)


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    launcher: AgentSandboxLauncher,
    *,
    existing: dict | None = None,
) -> tuple:
    fake_custom, fake_core = _FakeCustom(existing=existing), _FakeCore()
    monkeypatch.setattr(launcher, "_load_custom", lambda: fake_custom)
    monkeypatch.setattr(launcher, "_load_core", lambda: fake_core)
    monkeypatch.setattr(launcher, "_close_clients", lambda: None)
    return fake_custom, fake_core


def test_suspend_patches_operating_mode_suspended(monkeypatch: pytest.MonkeyPatch, fake_sdk: type[Exception]) -> None:
    lc = AgentSandboxLauncher(namespace="omnigent-sandboxes")
    fake_custom, _ = _wire(monkeypatch, lc)
    lc.suspend("omnigent-managed-abc")
    assert len(fake_custom.patches) == 1
    p = fake_custom.patches[0]
    assert p["name"] == "omnigent-managed-abc"
    assert p["plural"] == SANDBOX_PLURAL
    assert p["body"] == {"spec": {"operatingMode": OPERATING_MODE_SUSPENDED}}


def test_resume_prepares_by_deleting_only_the_token_secret(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: type[Exception]
) -> None:
    # resume() must NOT touch the CR/PVC (that would cascade the persistent
    # volume). It only clears the stale token Secret so start_host can re-arm.
    lc = AgentSandboxLauncher(namespace="omnigent-sandboxes")
    fake_custom, fake_core = _wire(monkeypatch, lc)
    lc.resume("omnigent-managed-abc")
    assert fake_core.deleted_secrets == ["omnigent-managed-abc-token"]
    assert fake_custom.patches == [] and fake_custom.deletes == []


def test_start_host_resumes_in_place_when_cr_survived(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: type[Exception]
) -> None:
    # A surviving CR (post-suspend) must be resumed in place: arm a fresh token
    # Secret + flip operatingMode=Running, and NEVER recreate the CR (its
    # volumeClaimTemplate PVC would be lost).
    lc = AgentSandboxLauncher(namespace="omnigent-sandboxes")
    fake_custom, fake_core = _wire(monkeypatch, lc, existing={"metadata": {"name": "omnigent-managed-abc"}})
    lc.start_host(
        "omnigent-managed-abc",
        token="tok",
        host_id="host_abcdef",
        host_name="managed-abcdef",
        server_url="http://srv.example.com",
    )
    # Fresh token armed, mode flipped to Running, CR NOT recreated.
    assert fake_core.created_secrets == ["omnigent-sandboxes"]
    assert fake_custom.patches[0]["body"] == {"spec": {"operatingMode": OPERATING_MODE_RUNNING}}
    assert fake_custom.creates == []


def test_terminate_deletes_sandbox_cr_and_token_secret(monkeypatch: pytest.MonkeyPatch, fake_sdk: type[Exception]) -> None:
    lc = AgentSandboxLauncher(namespace="omnigent-sandboxes")
    fake_custom, fake_core = _wire(monkeypatch, lc)
    lc.terminate("omnigent-managed-abc")
    assert [d["name"] for d in fake_custom.deletes] == ["omnigent-managed-abc"]
    assert fake_custom.deletes[0]["group"] == SANDBOX_API_GROUP
    assert fake_core.deleted_secrets == ["omnigent-managed-abc-token"]
