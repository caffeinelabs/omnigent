"""Agent-sandbox launcher: managed sandboxes as ``Sandbox`` custom resources.

Targets the `kubernetes-sigs/agent-sandbox <https://github.com/kubernetes-sigs/agent-sandbox>`_
controller (API group ``agents.x-k8s.io/v1beta1``, kind ``Sandbox``) instead of
building raw ``batch/v1`` Jobs. It keeps the kubernetes provider's
entrypoint-as-host model unchanged — the sandbox container still runs
``omnigent host`` and dials back to the server over a WebSocket, so the server's
liveness/connection machinery (``host_store.is_online``) is reused verbatim, with
no Service, pod-IP resolution, or ``pods/exec`` — but hands the *lifecycle* to the
agent-sandbox controller so we gain the features other managed runtimes offer and
raw Jobs can't:

- **Persist a session** — ``spec.volumeClaimTemplates`` gives the sandbox a
  controller-managed PVC that survives the pod being torn down, so HOME (the
  cloned workspace + ``~/.omnigent``) is durable across suspend/resume.
- **Shut down when idle** — ``spec.operatingMode`` toggles ``Running`` ↔
  ``Suspended``: :meth:`suspend` frees the pod (compute) while keeping the PVC,
  and :meth:`resume` flips it back in place (no delete-and-recreate, unlike the
  Job provider). ``spec.shutdownTime`` sets a hard reclaim deadline.

This is a prototype provider (not yet wired into a deployed cluster): it needs
the agent-sandbox controller installed and the server ServiceAccount granted
``sandboxes``/``sandboxes/status`` verbs in ``agents.x-k8s.io`` (in place of
``jobs``). See ``docs/`` note added alongside this module.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, ClassVar

import click

from omnigent.onboarding.sandboxes.base import SandboxHostLauncher
from omnigent.onboarding.sandboxes.kubernetes import (
    KubernetesSandboxLauncher,
    _api_reason,
    _ensure_sdk,
    _format_api_error,
    _HOME_DIR,
    _POD_READY_REQUEST_TIMEOUT_S,
    _POD_READY_TIMEOUT_S,
    _token_secret_name,
    build_job_manifest,
    build_token_secret_manifest,
)
from omnigent.onboarding.sandboxes.types import SandboxCapabilities

if TYPE_CHECKING:
    from kubernetes import client as k8s_client

# agent-sandbox CRD coordinates (kubernetes-sigs/agent-sandbox).
SANDBOX_API_GROUP = "agents.x-k8s.io"
SANDBOX_API_VERSION = "v1beta1"
SANDBOX_PLURAL = "sandboxes"

# operatingMode values the controller understands.
OPERATING_MODE_RUNNING = "Running"
OPERATING_MODE_SUSPENDED = "Suspended"

# Default persistent-workspace size when persistence is enabled without an
# explicit size. HOME (workspace clone + ~/.omnigent) lives here.
_DEFAULT_WORKSPACE_STORAGE = "10Gi"
# The volumeClaimTemplate name; the controller injects a PVC by this name in
# place of the pod template's "home" volume (StatefulSet-style by-name binding).
_HOME_VOLUME_NAME = "home"


def build_sandbox_manifest(
    *,
    sandbox_name: str,
    namespace: str,
    persist: bool = False,
    workspace_storage: str = _DEFAULT_WORKSPACE_STORAGE,
    storage_class: str | None = None,
    operating_mode: str = OPERATING_MODE_RUNNING,
    idle_shutdown_seconds: int | None = None,
    service: bool = False,
    **job_kwargs: object,
) -> dict[str, object]:
    """
    Build a ``Sandbox`` custom-resource manifest as a plain dict.

    Reuses :func:`build_job_manifest` to construct the exact same hardened pod
    template (security context, init-container clone, ``secretKeyRef`` token,
    ``envFrom`` harness creds, labels, PVC/secret mounts) the Job provider uses,
    then reshapes it into a ``Sandbox`` CR and layers on the controller-managed
    lifecycle fields. Pure: no SDK import, no I/O — the manifest is a literal
    dict, so it is the unit-test surface for the CR shape.

    :param sandbox_name: DNS-label-safe ``Sandbox`` name.
    :param namespace: Namespace the ``Sandbox`` is created in.
    :param persist: When ``True``, HOME is backed by a controller-managed PVC
        (``spec.volumeClaimTemplates``) that survives suspend/resume instead of
        an ``emptyDir``.
    :param workspace_storage: PVC size request when *persist* is set.
    :param storage_class: Optional storage class for the persistent HOME PVC.
    :param operating_mode: Initial ``spec.operatingMode`` (``Running``).
    :param idle_shutdown_seconds: When set, a ``spec.shutdownTime`` reclaim
        deadline this many seconds out (hard lifetime cap; the controller
        reclaims the sandbox at that time).
    :param service: When ``True``, request a stable ``spec.service`` (gives the
        sandbox a stable ``status.serviceFQDN``). Off by default — the host
        dials out, so no inbound Service is required.
    :param job_kwargs: Forwarded verbatim to :func:`build_job_manifest`
        (``image``, ``host_id``, ``server_url``, ``token_secret_name``, …).
    :returns: The ``Sandbox`` manifest dict.
    """
    # Borrow the Job builder purely for its pod template + label set. The
    # Job-level spec (backoffLimit / activeDeadlineSeconds) is discarded — the
    # agent-sandbox controller owns restart + lifetime.
    job = build_job_manifest(
        job_name=sandbox_name,
        namespace=namespace,
        **job_kwargs,  # type: ignore[arg-type]
    )
    labels = job["metadata"]["labels"]  # type: ignore[index]
    pod_template = job["spec"]["template"]  # type: ignore[index]

    spec: dict[str, object] = {
        "operatingMode": operating_mode,
        "podTemplate": pod_template,
    }
    if service:
        spec["service"] = True

    if persist:
        # Swap the ephemeral HOME emptyDir for a controller-managed PVC so the
        # workspace + ~/.omnigent survive a suspend (pod torn down, PVC kept)
        # and reattach on resume. The pod template already mounts a volume named
        # "home"; dropping the emptyDir lets the controller bind the PVC created
        # from the same-named volumeClaimTemplate (StatefulSet-style).
        pod_spec = pod_template["spec"]  # type: ignore[index]
        pod_spec["volumes"] = [  # type: ignore[index]
            v
            for v in pod_spec["volumes"]  # type: ignore[index]
            if v.get("name") != _HOME_VOLUME_NAME
        ]
        claim_spec: dict[str, object] = {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": workspace_storage}},
        }
        if storage_class is not None:
            claim_spec["storageClassName"] = storage_class
        spec["volumeClaimTemplates"] = [
            {"metadata": {"name": _HOME_VOLUME_NAME}, "spec": claim_spec}
        ]

    if idle_shutdown_seconds is not None:
        # metav1.Time (RFC3339). A hard reclaim deadline; the controller deletes
        # the sandbox at this time. gmtime keeps it UTC/"Z" without a tz dep.
        deadline = time.gmtime(time.time() + idle_shutdown_seconds)
        spec["shutdownTime"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", deadline)

    return {
        "apiVersion": f"{SANDBOX_API_GROUP}/{SANDBOX_API_VERSION}",
        "kind": "Sandbox",
        "metadata": {"name": sandbox_name, "namespace": namespace, "labels": labels},
        "spec": spec,
    }


class AgentSandboxLauncher(KubernetesSandboxLauncher):
    """
    :class:`SandboxHostLauncher` backed by agent-sandbox ``Sandbox`` CRs.

    Subclasses :class:`KubernetesSandboxLauncher` to reuse its config
    resolution (namespace/image/env/service-account), token-Secret arming, pod
    template, and API-error formatting — overriding only the create / readiness
    / delete / resume steps to drive the ``Sandbox`` custom resource via the
    ``CustomObjectsApi`` instead of a ``Job`` via ``BatchV1Api``.
    """

    provider: ClassVar[str] = "agentsandbox"
    can_resume: ClassVar[bool] = True

    def __init__(
        self,
        *,
        persist: bool = True,
        workspace_storage: str = _DEFAULT_WORKSPACE_STORAGE,
        storage_class: str | None = None,
        idle_shutdown_seconds: int | None = None,
        service: bool = False,
        **kubernetes_kwargs: object,
    ) -> None:
        """
        :param persist: Back HOME with a persistent PVC (default on — the whole
            point of this provider). See :func:`build_sandbox_manifest`.
        :param workspace_storage: Persistent HOME PVC size.
        :param storage_class: Storage class for the HOME PVC, or ``None``.
        :param idle_shutdown_seconds: Hard reclaim deadline (``shutdownTime``).
        :param service: Request a stable ``spec.service`` FQDN.
        :param kubernetes_kwargs: Forwarded to
            :class:`KubernetesSandboxLauncher` (image, namespace, env, …).
        """
        super().__init__(**kubernetes_kwargs)  # type: ignore[arg-type]
        self._persist = persist
        self._workspace_storage = workspace_storage
        self._storage_class = storage_class
        self._idle_shutdown_seconds = idle_shutdown_seconds
        self._service = service
        self._custom: k8s_client.CustomObjectsApi | None = None

    @property
    def capabilities(self) -> SandboxCapabilities:
        # resume_stopped=True here means a REAL in-place resume (operatingMode
        # flip), not the delete-and-recreate the Job provider does.
        return SandboxCapabilities(
            cli_bootstrap=False,
            managed_launch=True,
            local_port_forward=False,
            resume_stopped=True,
            programmatic_terminate=True,
            classifies_runner_by_agent=True,
        )

    def _load_custom(self) -> k8s_client.CustomObjectsApi:
        """Return the (lazily built) ``CustomObjectsApi`` on the isolated config."""
        if self._custom is not None:
            return self._custom
        from kubernetes import client

        # _load_clients() populates self._api_client on the isolated Configuration.
        self._load_clients()
        self._custom = client.CustomObjectsApi(self._api_client)
        return self._custom

    def _close_clients(self) -> None:
        self._custom = None
        super()._close_clients()

    def start_host(
        self,
        sandbox_id: str,
        *,
        token: str,
        host_id: str,
        host_name: str,
        server_url: str,
        repo_url: str | None = None,
        repo_branch: str | None = None,
        repo_name: str | None = None,
        host_config: dict[str, object] | None = None,
        agent_name: str | None = None,
        on_stage: Callable[[str], None] | None = None,
    ) -> str:
        """
        Create the token Secret + ``Sandbox`` CR and wait for it to be Ready.

        Mirrors :meth:`KubernetesSandboxLauncher.start_host` but creates a
        ``Sandbox`` custom object instead of a Job and waits on the CR's
        ``Ready`` status condition. Returns the in-sandbox workspace path.
        """
        _ensure_sdk()
        from kubernetes.client.rest import ApiException
        from urllib3.exceptions import HTTPError

        namespace = self._resolve_namespace()
        image = self._resolve_image()
        env_literals = self._resolve_sandbox_env()
        secret_name = _token_secret_name(sandbox_id)
        workspace = f"{_HOME_DIR}/workspace"
        clone_dir = f"{workspace}/{repo_name}" if repo_name else None
        if on_stage is not None:
            on_stage("starting")
        core = self._load_core()
        custom = self._load_custom()

        # Resume-in-place: if the CR survived a suspend (its PVC with it), do NOT
        # recreate it (that would cascade the PVC). Arm a fresh token Secret and
        # flip operatingMode back to Running so the controller brings up a fresh
        # pod on the persisted PVC. Never delete on failure — the volume is the
        # user's workspace.
        if self._get_sandbox(namespace, sandbox_id) is not None:
            if on_stage is not None:
                on_stage("starting")
            click.echo(f"▸ Resuming Sandbox '{sandbox_id}' in place (operatingMode=Running)")
            try:
                self._arm_token_secret(namespace, secret_name, token)
                self._patch_operating_mode(sandbox_id, OPERATING_MODE_RUNNING)
                self._wait_for_sandbox_ready(namespace, sandbox_id)
            finally:
                self._close_clients()
            click.echo(f"  → Sandbox '{sandbox_id}' is starting the host")
            return clone_dir or workspace

        click.echo(
            f"▸ Creating Sandbox '{sandbox_id}' in namespace '{namespace}' from {image} "
            f"(persist={self._persist})"
        )
        try:
            try:
                manifest = build_sandbox_manifest(
                    sandbox_name=sandbox_id,
                    namespace=namespace,
                    persist=self._persist,
                    workspace_storage=self._workspace_storage,
                    storage_class=self._storage_class,
                    idle_shutdown_seconds=self._idle_shutdown_seconds,
                    service=self._service,
                    image=image,
                    service_account=self._resolve_service_account(),
                    host_id=host_id,
                    host_name=host_name,
                    server_url=server_url,
                    token_secret_name=secret_name,
                    harness_secret=self._resolve_secret(),
                    env_literals=env_literals,
                    node_selector=self._node_selector,
                    workspace=workspace,
                    clone_dir=clone_dir,
                    repo_url=repo_url,
                    repo_branch=repo_branch,
                    host_config=host_config,
                    resources=self._resources,
                    pvc_mounts=self._pvc_mounts,
                    secret_mounts=self._secret_mounts,
                    agent_name=agent_name,
                )
                # Secret before the Sandbox so the pod's secretKeyRef resolves
                # as soon as the controller schedules it.
                core.create_namespaced_secret(
                    namespace,
                    build_token_secret_manifest(
                        secret_name=secret_name, namespace=namespace, token=token
                    ),
                    _request_timeout=_POD_READY_REQUEST_TIMEOUT_S,
                )
                custom.create_namespaced_custom_object(
                    group=SANDBOX_API_GROUP,
                    version=SANDBOX_API_VERSION,
                    namespace=namespace,
                    plural=SANDBOX_PLURAL,
                    body=manifest,
                    _request_timeout=_POD_READY_REQUEST_TIMEOUT_S,
                )
            except (ApiException, HTTPError) as exc:
                self._best_effort_delete_sandbox(namespace, sandbox_id, secret_name)
                if isinstance(exc, ApiException):
                    raise click.ClickException(
                        _format_api_error("create Sandbox", sandbox_id, exc)
                    ) from exc
                raise click.ClickException(
                    f"timed out creating Sandbox '{sandbox_id}' ({_api_reason(exc)})"
                ) from exc

            try:
                self._wait_for_sandbox_ready(namespace, sandbox_id)
            except BaseException:
                self._best_effort_delete_sandbox(namespace, sandbox_id, secret_name)
                raise
        finally:
            self._close_clients()
        click.echo(f"  → Sandbox '{sandbox_id}' is starting the host")
        return clone_dir or workspace

    def _wait_for_sandbox_ready(self, namespace: str, sandbox_id: str) -> None:
        """
        Poll the ``Sandbox`` until its ``Ready`` condition is ``True``.

        :raises click.ClickException: On timeout or a terminal ``Finished``
            condition before Ready.
        """
        from kubernetes.client.rest import ApiException
        from urllib3.exceptions import HTTPError

        custom = self._load_custom()
        deadline = time.monotonic() + (self._pod_ready_timeout_s or _POD_READY_TIMEOUT_S)
        last_reason = ""
        while time.monotonic() < deadline:
            try:
                obj = custom.get_namespaced_custom_object_status(
                    group=SANDBOX_API_GROUP,
                    version=SANDBOX_API_VERSION,
                    namespace=namespace,
                    plural=SANDBOX_PLURAL,
                    name=sandbox_id,
                    _request_timeout=_POD_READY_REQUEST_TIMEOUT_S,
                )
            except ApiException as exc:
                if getattr(exc, "status", None) in (401, 403):
                    raise click.ClickException(
                        _format_api_error("read Sandbox status", sandbox_id, exc)
                    ) from exc
                last_reason = _api_reason(exc)
                time.sleep(1.0)
                continue
            except HTTPError as exc:
                last_reason = _api_reason(exc)
                time.sleep(1.0)
                continue
            conditions = (obj.get("status") or {}).get("conditions") or []
            by_type = {c.get("type"): c for c in conditions}
            ready = by_type.get("Ready")
            if ready and ready.get("status") == "True":
                return
            finished = by_type.get("Finished")
            if finished and finished.get("status") == "True":
                raise click.ClickException(
                    f"Sandbox '{sandbox_id}' finished before becoming Ready "
                    f"({finished.get('reason') or 'unknown'})"
                )
            last_reason = (ready or {}).get("reason") or last_reason
            time.sleep(1.0)
        raise click.ClickException(
            f"Sandbox '{sandbox_id}' did not become Ready in time ({last_reason or 'timeout'})"
        )

    def suspend(self, sandbox_id: str) -> None:
        """
        Suspend an idle sandbox in place: flip ``operatingMode`` to ``Suspended``.

        The controller tears the pod down (freeing compute) but keeps the PVC,
        so the workspace is preserved for a later :meth:`resume`. This is the
        "shut them down when not used" primitive the Job provider lacks — the
        token Secret is intentionally left armed so resume needs no re-arm.

        :param sandbox_id: The ``Sandbox`` to suspend.
        """
        click.echo(f"▸ Suspending Sandbox '{sandbox_id}' (operatingMode=Suspended)")
        self._patch_operating_mode(sandbox_id, OPERATING_MODE_SUSPENDED)

    def resume(self, sandbox_id: str) -> None:
        """
        Prepare a suspended sandbox for in-place resume.

        The shared wake path (:func:`resume_managed_host`) calls this and then
        :meth:`start_host` with a freshly minted token. Unlike the Job provider
        — whose ``resume`` deletes the old Job + Secret so ``start_host`` can
        recreate them against an *external* PVC — the agent-sandbox PVC is owned
        by the ``Sandbox`` CR (``volumeClaimTemplates``), so deleting the CR
        would cascade the PVC and lose the workspace. So keep the CR (still
        ``Suspended``) and its PVC, and delete only the stale launch-token
        Secret; :meth:`start_host` sees the surviving CR, arms a fresh Secret,
        and flips ``operatingMode`` back to ``Running`` in place.

        :param sandbox_id: The dormant ``Sandbox`` to prepare.
        """
        click.echo(f"▸ Preparing suspended Sandbox '{sandbox_id}' for resume")
        self._delete_token_secret(sandbox_id)

    def _delete_token_secret(self, sandbox_id: str) -> None:
        """Delete the launch-token Secret (404 = already gone); leave the CR/PVC."""
        _ensure_sdk()
        from kubernetes.client.rest import ApiException

        namespace = self._resolve_namespace()
        secret_name = _token_secret_name(sandbox_id)
        try:
            self._load_core().delete_namespaced_secret(
                secret_name, namespace, _request_timeout=_POD_READY_REQUEST_TIMEOUT_S
            )
        except ApiException as exc:
            if getattr(exc, "status", None) != 404:
                raise click.ClickException(
                    _format_api_error("delete token Secret", secret_name, exc)
                ) from exc
        finally:
            self._close_clients()

    def _arm_token_secret(self, namespace: str, secret_name: str, token: str) -> None:
        """Create the launch-token Secret, replacing it in place if it exists."""
        from kubernetes.client.rest import ApiException

        core = self._load_core()
        manifest = build_token_secret_manifest(
            secret_name=secret_name, namespace=namespace, token=token
        )
        try:
            core.create_namespaced_secret(
                namespace, manifest, _request_timeout=_POD_READY_REQUEST_TIMEOUT_S
            )
        except ApiException as exc:
            if getattr(exc, "status", None) == 409:
                core.replace_namespaced_secret(
                    secret_name, namespace, manifest, _request_timeout=_POD_READY_REQUEST_TIMEOUT_S
                )
            else:
                raise click.ClickException(
                    _format_api_error("arm token Secret", secret_name, exc)
                ) from exc

    def _get_sandbox(self, namespace: str, sandbox_id: str) -> dict | None:
        """Return the ``Sandbox`` CR, or ``None`` if it does not exist (404)."""
        from kubernetes.client.rest import ApiException

        try:
            return self._load_custom().get_namespaced_custom_object(
                group=SANDBOX_API_GROUP,
                version=SANDBOX_API_VERSION,
                namespace=namespace,
                plural=SANDBOX_PLURAL,
                name=sandbox_id,
                _request_timeout=_POD_READY_REQUEST_TIMEOUT_S,
            )
        except ApiException as exc:
            if getattr(exc, "status", None) == 404:
                return None
            raise click.ClickException(
                _format_api_error("read Sandbox", sandbox_id, exc)
            ) from exc

    def _patch_operating_mode(self, sandbox_id: str, mode: str) -> None:
        """JSON-merge-patch ``spec.operatingMode`` on the ``Sandbox``."""
        _ensure_sdk()
        from kubernetes.client.rest import ApiException

        namespace = self._resolve_namespace()
        custom = self._load_custom()
        try:
            custom.patch_namespaced_custom_object(
                group=SANDBOX_API_GROUP,
                version=SANDBOX_API_VERSION,
                namespace=namespace,
                plural=SANDBOX_PLURAL,
                name=sandbox_id,
                body={"spec": {"operatingMode": mode}},
                _request_timeout=_POD_READY_REQUEST_TIMEOUT_S,
            )
        except ApiException as exc:
            if getattr(exc, "status", None) == 404:
                raise click.ClickException(
                    f"Sandbox '{sandbox_id}' not found (cannot set operatingMode={mode})"
                ) from exc
            raise click.ClickException(
                _format_api_error(f"set operatingMode={mode}", sandbox_id, exc)
            ) from exc

    def terminate(self, sandbox_id: str) -> None:
        """
        Delete the ``Sandbox`` CR (the controller cascades pod + PVC) and the
        token Secret. Idempotent (404 = success).

        :param sandbox_id: The ``Sandbox`` to delete.
        """
        _ensure_sdk()
        namespace = self._resolve_namespace()
        secret_name = _token_secret_name(sandbox_id)
        first_error: click.ClickException | None = None
        try:
            for kind, name, delete in (
                (
                    "sandbox",
                    sandbox_id,
                    lambda: self._load_custom().delete_namespaced_custom_object(
                        group=SANDBOX_API_GROUP,
                        version=SANDBOX_API_VERSION,
                        namespace=namespace,
                        plural=SANDBOX_PLURAL,
                        name=sandbox_id,
                        _request_timeout=_POD_READY_REQUEST_TIMEOUT_S,
                    ),
                ),
                (
                    "secret",
                    secret_name,
                    lambda: self._load_core().delete_namespaced_secret(
                        secret_name,
                        namespace,
                        _request_timeout=_POD_READY_REQUEST_TIMEOUT_S,
                    ),
                ),
            ):
                try:
                    self._delete_with_retry(kind, name, delete)
                except click.ClickException as exc:
                    if first_error is None:
                        first_error = exc
        finally:
            self._close_clients()
        if first_error is not None:
            raise first_error

    def _best_effort_delete_sandbox(
        self, namespace: str, sandbox_id: str, secret_name: str
    ) -> None:
        """Delete the Sandbox + token Secret, swallowing errors (cleanup path)."""
        with contextlib.suppress(Exception):
            self._delete_with_retry(
                "sandbox",
                sandbox_id,
                lambda: self._load_custom().delete_namespaced_custom_object(
                    group=SANDBOX_API_GROUP,
                    version=SANDBOX_API_VERSION,
                    namespace=namespace,
                    plural=SANDBOX_PLURAL,
                    name=sandbox_id,
                    _request_timeout=_POD_READY_REQUEST_TIMEOUT_S,
                ),
            )
        with contextlib.suppress(Exception):
            self._delete_with_retry(
                "secret",
                secret_name,
                lambda: self._load_core().delete_namespaced_secret(
                    secret_name, namespace, _request_timeout=_POD_READY_REQUEST_TIMEOUT_S
                ),
            )


# Re-exported for parity with kubernetes.py's public surface.
__all__ = ["AgentSandboxLauncher", "build_sandbox_manifest"]

assert issubclass(AgentSandboxLauncher, SandboxHostLauncher)
