"""
agent-sandbox launcher.

Runs the same sandbox Pod as the :mod:`kubernetes` provider, but as an
`agent-sandbox <https://github.com/kubernetes-sigs/agent-sandbox>`_
``Sandbox`` custom resource (``agents.x-k8s.io/v1beta1``) instead of a
``batch/v1`` Job. Everything about the Pod is inherited unchanged from
:class:`KubernetesSandboxLauncher` (the entrypoint-as-host command, the PID-1
reaper, the launch-token ``secretKeyRef``, the restricted security context, the
workspace init container, operator PVC / Secret mounts); only the enclosing
workload kind and its lifecycle differ.

The reason to prefer it is reclamation. A Job caps its Pod with
``activeDeadlineSeconds`` (7 days), which is a *fixed* lifetime: an abandoned
sandbox holds a node slot for a week whether or not anything ever ran in it.
A ``Sandbox`` carries ``spec.shutdownTime``, an *absolute deadline the owner is
expected to keep pushing forward*, which turns the same field into an
inactivity timeout:

- :meth:`~KubernetesSandboxLauncher.start_host` stamps
  ``shutdownTime = now + window`` (:data:`DEFAULT_SHUTDOWN_WINDOW_S`).
- :meth:`AgentSandboxLauncher.keep_alive` pushes it forward, and the server
  calls that for as long as the sandbox has a live runner tunnel
  (:mod:`omnigent.server.managed_keepalive`).
- Once nothing is running, nothing refreshes the deadline, and the
  agent-sandbox controller tears the Pod down and (``shutdownPolicy: Delete``)
  removes the ``Sandbox`` object with it.

So an idle sandbox reclaims itself within one window, a busy one lives as long
as work keeps arriving, and the controller (not the Omnigent server) does the
reaping. Nothing here has to enumerate or babysit live Pods.

Requires the agent-sandbox controller installed in the cluster and the server's
ServiceAccount granted ``sandboxes`` create/get/patch/delete (see
``deploy/kubernetes/overlays/sandbox-runners/role.yaml``). Configuration is read
from the same ``sandbox.kubernetes`` block as the Job provider, so switching
``sandbox.provider`` between the two needs no other config change.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, ClassVar

import click

from omnigent.onboarding.sandboxes.kubernetes import (
    _POD_READY_REQUEST_TIMEOUT_S,
    KubernetesSandboxLauncher,
    _api_reason,
    _ensure_sdk,
    _format_api_error,
    _token_secret_name,
)

if TYPE_CHECKING:
    from kubernetes import client as k8s_client


_logger = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────

API_GROUP: str = "agents.x-k8s.io"
"""API group of the agent-sandbox CRDs."""

API_VERSION: str = "v1beta1"
"""Served version of the ``Sandbox`` CRD."""

SANDBOX_PLURAL: str = "sandboxes"
"""Plural resource name, as required by ``CustomObjectsApi``."""

SHUTDOWN_WINDOW_ENV_VAR: str = "OMNIGENT_AGENT_SANDBOX_SHUTDOWN_WINDOW_S"
"""Environment variable overriding :data:`DEFAULT_SHUTDOWN_WINDOW_S`."""

DEFAULT_SHUTDOWN_WINDOW_S: int = 3600
"""How far ahead of now ``spec.shutdownTime`` is set, in seconds.

This is effectively the sandbox's inactivity timeout: a sandbox with no live
runner is reclaimed within one window of its last refresh. It MUST stay
comfortably above :data:`omnigent.server.managed_keepalive._MIN_INTERVAL_S`
(the server's per-runner refresh rate) so a couple of missed or slow refreshes
cannot reclaim a busy sandbox. One hour against a 10-minute refresh leaves five
misses of headroom, and also covers the gap between a host starting and its
first session spawning a runner (no runner yet means no refresh yet).
"""


def resolve_shutdown_window_s() -> int:
    """
    Resolve the shutdown window from the environment, else the default.

    A non-positive or unparseable value falls through to the default rather
    than raising: a malformed knob must not make sandboxes unlaunchable, and a
    zero window would expire every sandbox at birth.

    :returns: The window in seconds, always positive.
    """
    raw = os.environ.get(SHUTDOWN_WINDOW_ENV_VAR, "").strip()
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            _logger.warning(
                "ignoring %s=%r (not an integer); using %ss",
                SHUTDOWN_WINDOW_ENV_VAR,
                raw,
                DEFAULT_SHUTDOWN_WINDOW_S,
            )
        else:
            if parsed > 0:
                return parsed
            _logger.warning(
                "ignoring %s=%r (must be positive); using %ss",
                SHUTDOWN_WINDOW_ENV_VAR,
                raw,
                DEFAULT_SHUTDOWN_WINDOW_S,
            )
    return DEFAULT_SHUTDOWN_WINDOW_S


def _shutdown_time(window_s: int, *, now: datetime | None = None) -> str:
    """
    Render an absolute ``shutdownTime`` *window_s* ahead of now.

    :param window_s: Seconds ahead of *now*.
    :param now: Reference time, or ``None`` for the current UTC time.
    :returns: An RFC 3339 UTC timestamp, the format ``metav1.Time`` expects.
    """
    base = now or datetime.now(UTC)
    return (base + timedelta(seconds=window_s)).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_sandbox_manifest(
    job_manifest: dict[str, object],
    *,
    shutdown_time: str,
) -> dict[str, object]:
    """
    Convert a sandbox Job manifest into an agent-sandbox ``Sandbox`` manifest.

    Pure: a dict in, a dict out, which makes it the unit-test surface for the
    conversion. The Job's ``spec.template`` is already a full
    ``PodTemplateSpec`` (``{"metadata": {"labels": …}, "spec": …}``), which is
    exactly the shape of the CRD's ``spec.podTemplate``, so the Pod is carried
    over verbatim: there is no second copy of the Pod's security or
    credential decisions to keep in sync.

    The Job's own ``backoffLimit`` and ``activeDeadlineSeconds`` are dropped:
    the Pod's ``restartPolicy: OnFailure`` still restarts a crashed host in
    place, and the fixed deadline is replaced by the refreshable
    *shutdown_time*. ``volumeClaimTemplates`` is deliberately not set: the
    operator PVC lane (``sandbox.kubernetes.pvc_mounts``) already rides in the
    Pod template, and controller-created claims would be cascade-deleted with
    the ``Sandbox``, which is the opposite of what a persistent volume is for.

    :param job_manifest: The manifest from
        :func:`~omnigent.onboarding.sandboxes.kubernetes.build_job_manifest`.
    :param shutdown_time: Absolute RFC 3339 expiry for ``spec.shutdownTime``.
    :returns: The ``Sandbox`` manifest to hand to ``CustomObjectsApi``.
    """
    metadata = dict(job_manifest["metadata"])  # type: ignore[arg-type]
    template = job_manifest["spec"]["template"]  # type: ignore[index]
    return {
        "apiVersion": f"{API_GROUP}/{API_VERSION}",
        "kind": "Sandbox",
        "metadata": metadata,
        "spec": {
            "podTemplate": template,
            "operatingMode": "Running",
            "shutdownTime": shutdown_time,
            # Delete, not the CRD's Retain default: an expired-but-retained
            # Sandbox is a leftover object with cleared status that something
            # would still have to sweep, and self-cleaning expiry is the whole
            # reason to be on this provider.
            "shutdownPolicy": "Delete",
        },
    }


class AgentSandboxLauncher(KubernetesSandboxLauncher):
    """
    :class:`SandboxLauncher` backing managed hosts with agent-sandbox
    ``Sandbox`` custom resources.

    A thin lifecycle specialization of :class:`KubernetesSandboxLauncher`: the
    Pod, its credentials and its start-readiness polling are all inherited, and
    only the four lifecycle verbs that differ are overridden:
    :meth:`_create_workload` (create a ``Sandbox`` rather than a Job),
    :meth:`_find_job_pod` (the backing Pod is named after the ``Sandbox``, so
    no label lookup is needed), :meth:`keep_alive` (push ``shutdownTime``
    forward) and :meth:`terminate` (delete the ``Sandbox``).
    """

    provider: ClassVar[str] = "agent_sandbox"

    # ── clients ─────────────────────────────────────────────

    def _load_custom(self) -> k8s_client.CustomObjectsApi:
        """
        Return a ``CustomObjectsApi`` on the launcher's isolated config.

        Built fresh per call rather than cached: it is a stateless wrapper over
        the shared ``ApiClient`` (which owns the connection pool), so there is
        nothing extra for :meth:`_close_clients` to release.

        :returns: A ``CustomObjectsApi`` bound to the shared ``ApiClient``.
        :raises click.ClickException: When cluster config cannot be loaded.
        """
        from kubernetes import client

        self._load_clients()
        return client.CustomObjectsApi(self._api_client)

    # ── lifecycle ───────────────────────────────────────────

    def _create_workload(self, namespace: str, manifest: dict[str, object]) -> None:
        """
        Create a ``Sandbox`` custom resource wrapping the Job's Pod template.

        :param namespace: Namespace to create the ``Sandbox`` in.
        :param manifest: The manifest from ``build_job_manifest``.
        """
        window_s = resolve_shutdown_window_s()
        self._load_custom().create_namespaced_custom_object(
            API_GROUP,
            API_VERSION,
            namespace,
            SANDBOX_PLURAL,
            build_sandbox_manifest(manifest, shutdown_time=_shutdown_time(window_s)),
            _request_timeout=_POD_READY_REQUEST_TIMEOUT_S,
        )

    def _find_job_pod(self, namespace: str, job_name: str) -> str | None:
        """
        Return the ``Sandbox``'s backing Pod name once it exists.

        The controller names the backing Pod after the ``Sandbox`` itself
        (``resolvePodName`` in its sandbox controller), so this is a direct read
        rather than the base class's ``job-name`` label lookup. Re-raises 401/403
        so an RBAC gap surfaces as itself instead of a readiness timeout.

        :param namespace: Namespace the ``Sandbox`` lives in.
        :param job_name: The ``Sandbox`` name (also the Pod name).
        :returns: The Pod name, or ``None`` while the Pod does not exist yet.
        :raises click.ClickException: On a 401/403 from the apiserver.
        """
        from kubernetes.client.rest import ApiException
        from urllib3.exceptions import HTTPError

        try:
            self._load_core().read_namespaced_pod(
                job_name, namespace, _request_timeout=_POD_READY_REQUEST_TIMEOUT_S
            )
        except ApiException as exc:
            if getattr(exc, "status", None) in (401, 403):
                raise click.ClickException(
                    _format_api_error("read sandbox pod", job_name, exc)
                ) from exc
            return None
        except HTTPError:
            return None
        return job_name

    def keep_alive(self, sandbox_id: str) -> None:
        """
        Push ``spec.shutdownTime`` one window into the future.

        Idempotent and cheap by design, a single-field JSON merge patch, so
        the server can call it on every runner-tunnel refresh. Soft-fails: a
        patch that does not land only shortens the sandbox's remaining life, so
        it is logged rather than raised, and a genuinely gone sandbox (404) is
        not an error at all.

        :param sandbox_id: The ``Sandbox`` to extend.
        """
        _ensure_sdk()
        from kubernetes.client.rest import ApiException
        from urllib3.exceptions import HTTPError

        namespace = self._resolve_namespace()
        window_s = resolve_shutdown_window_s()
        shutdown_time = _shutdown_time(window_s)
        try:
            self._load_custom().patch_namespaced_custom_object(
                API_GROUP,
                API_VERSION,
                namespace,
                SANDBOX_PLURAL,
                sandbox_id,
                {"spec": {"shutdownTime": shutdown_time}},
                _request_timeout=_POD_READY_REQUEST_TIMEOUT_S,
            )
        except ApiException as exc:
            if getattr(exc, "status", None) != 404:
                _logger.warning(
                    "could not extend agent-sandbox '%s' to %s: %s",
                    sandbox_id,
                    shutdown_time,
                    _api_reason(exc),
                )
        except HTTPError as exc:
            _logger.warning(
                "could not extend agent-sandbox '%s' to %s: %s",
                sandbox_id,
                shutdown_time,
                _api_reason(exc),
            )
        else:
            _logger.debug("extended agent-sandbox '%s' to %s", sandbox_id, shutdown_time)
        finally:
            self._close_clients()

    def terminate(self, sandbox_id: str) -> None:
        """
        Delete the ``Sandbox`` (cascading to its Pod) and its token Secret.

        Idempotent: a 404 on either object is success. Both deletes are always
        attempted, so a failure on one cannot leak the other: in particular a
        leaked token Secret would keep a valid launch token alive.

        :param sandbox_id: The ``Sandbox`` to delete.
        :raises click.ClickException: On an API delete failure other than
            not-found.
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
                        API_GROUP,
                        API_VERSION,
                        namespace,
                        SANDBOX_PLURAL,
                        sandbox_id,
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

    def resume(self, sandbox_id: str) -> None:
        """
        Prepare an expired or dormant sandbox for recreation in place.

        Delete the ``Sandbox`` and its stale launch-token Secret so the shared
        managed-host wake path can call
        :meth:`~KubernetesSandboxLauncher.start_host` again under the same
        sandbox id with a freshly armed token. Operator-managed PVCs
        (``sandbox.kubernetes.pvc_mounts``) are external and untouched, so
        anything the previous run persisted there is still mounted.

        Suspend-and-resume in place (``operatingMode: Suspended``, which keeps
        the object and its volumes) is the natural fit for this CRD, but it
        needs the wake path to accept "the provider already resumed it, do not
        start a new host": a separate change.

        :param sandbox_id: The sandbox id to recreate.
        :raises click.ClickException: On an API delete failure other than
            not-found.
        """
        click.echo(f"▸ Resuming agent-sandbox '{sandbox_id}'")
        self.terminate(sandbox_id)
