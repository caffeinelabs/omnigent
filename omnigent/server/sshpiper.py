"""SSHPiper gateway configuration for VS Code Remote into sandboxes.

SSHPiper sits in front of per-sandbox SSH and routes by username:

    {target}--{real_user}

e.g. ``omnigent-managed-….omnigent-sandboxes.svc.cluster.local--sandbox``
connects to host ``omnigent-managed-….omnigent-sandboxes.svc.cluster.local``
as user ``sandbox``.

The web UI builds a ``vscode://vscode-remote/ssh-remote+…`` deep link
from this config plus the host's ``sandbox_id``. When
``OMNIGENT_SSHPIPER_HOST`` is unset the feature stays dormant (no
button, no ``ssh_target`` on hosts).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

_logger = logging.getLogger(__name__)

# Default matches the OpenShell / k8s host-image non-root user
# (``deploy/docker/Dockerfile`` creates ``sandbox``). Root-based
# providers can override via ``OMNIGENT_SSHPIPER_USER``.
_DEFAULT_USER = "sandbox"
_DEFAULT_PORT = 22
_DEFAULT_NAMESPACE = "omnigent-sandboxes"
_DEFAULT_TARGET_TEMPLATE = "{sandbox_id}.{namespace}.svc.cluster.local"


@dataclass(frozen=True)
class SshPiperConfig:
    """Validated SSHPiper gateway settings.

    :param host: Public (or cluster-reachable) SSHPiper gateway hostname.
    :param port: Gateway SSH port (``22`` when standard).
    :param user: Linux username on the sandbox after SSHPiper splits
        ``target--user``.
    :param target_template: ``str.format`` template for the SSHPiper
        *target* (left of ``--``). Placeholders: ``sandbox_id``,
        ``namespace``, ``host_id``, ``name``, ``provider``.
    :param namespace: Value substituted for ``{namespace}`` in the
        template (k8s sandbox namespace, or an operator override).
    """

    host: str
    port: int
    user: str
    target_template: str
    namespace: str

    @classmethod
    def from_env(cls) -> SshPiperConfig | None:
        """Load config from ``OMNIGENT_SSHPIPER_*`` env vars.

        :returns: Config when ``OMNIGENT_SSHPIPER_HOST`` is set, else
            ``None`` (feature disabled).
        """
        host = os.environ.get("OMNIGENT_SSHPIPER_HOST", "").strip()
        if not host:
            return None

        port_raw = os.environ.get("OMNIGENT_SSHPIPER_PORT", "").strip()
        if port_raw:
            try:
                port = int(port_raw)
            except ValueError:
                _logger.warning(
                    "OMNIGENT_SSHPIPER_PORT=%r is not an int; SSHPiper disabled.",
                    port_raw,
                )
                return None
            if not (1 <= port <= 65535):
                _logger.warning(
                    "OMNIGENT_SSHPIPER_PORT=%s out of range; SSHPiper disabled.",
                    port,
                )
                return None
        else:
            port = _DEFAULT_PORT

        user = os.environ.get("OMNIGENT_SSHPIPER_USER", "").strip() or _DEFAULT_USER
        if "--" in user:
            _logger.warning(
                "OMNIGENT_SSHPIPER_USER=%r contains '--' which is the "
                "SSHPiper username splitter; SSHPiper disabled.",
                user,
            )
            return None

        template = (
            os.environ.get("OMNIGENT_SSHPIPER_TARGET_TEMPLATE", "").strip()
            or _DEFAULT_TARGET_TEMPLATE
        )
        namespace = os.environ.get("OMNIGENT_SSHPIPER_NAMESPACE", "").strip() or _DEFAULT_NAMESPACE
        return cls(
            host=host,
            port=port,
            user=user,
            target_template=template,
            namespace=namespace,
        )

    def ssh_target(
        self,
        *,
        sandbox_id: str,
        host_id: str = "",
        name: str = "",
        provider: str = "",
    ) -> str:
        """Render the SSHPiper target (left of ``--``) for a sandbox.

        :param sandbox_id: Provider sandbox id (k8s Pod / Service name).
        :param host_id: Omnigent host id, for templates that need it.
        :param name: Friendly host name.
        :param provider: Sandbox provider id (``kubernetes``, …).
        :returns: The rendered target hostname.
        :raises ValueError: If the template references an unknown key
            or renders empty / still contains ``--``.
        """
        try:
            target = self.target_template.format(
                sandbox_id=sandbox_id,
                namespace=self.namespace,
                host_id=host_id,
                name=name,
                provider=provider,
            ).strip()
        except KeyError as exc:
            raise ValueError(
                f"OMNIGENT_SSHPIPER_TARGET_TEMPLATE references unknown "
                f"placeholder {exc}; allowed: sandbox_id, namespace, "
                f"host_id, name, provider"
            ) from exc
        if not target:
            raise ValueError("SSHPiper target template rendered empty")
        if "--" in target:
            raise ValueError(
                f"SSHPiper target {target!r} contains '--', which would break username splitting"
            )
        return target

    def sshpiper_username(self, target: str) -> str:
        """Build the SSH username SSHPiper expects: ``{target}--{user}``.

        :param target: Already-rendered SSHPiper target.
        :returns: The composite username.
        """
        return f"{target}--{self.user}"
