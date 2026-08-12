"""Public types for the sandbox launcher surface.

These dataclasses and exceptions are the vocabulary used by both the
existing :class:`~omnigent.onboarding.sandboxes.base.SandboxLauncher`
interface and the newer pluggable surface in
:mod:`omnigent.onboarding.sandboxes.registry`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class SandboxError(Exception):
    """Base for all sandbox-provider errors."""


class SandboxConfigError(SandboxError):
    """Sandbox provider configuration is malformed or unavailable."""


class SandboxAuthError(SandboxError):
    """Provider credentials or local tooling are missing/invalid."""


class SandboxCommandError(SandboxError):
    """A command executed inside a sandbox failed.

    :param message: Human-readable reason.
    :param command: The remote command that failed.
    :param returncode: Remote exit code.
    :param stdout: Captured standard output.
    :param stderr: Captured standard error.
    """

    def __init__(
        self,
        message: str,
        *,
        command: str | None = None,
        returncode: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True)
class SandboxCapabilities:
    """Feature flags declared by a sandbox provider.

    Providers advertise which primitives they support so callers can fail
    fast and surface actionable messages.

    :param cli_bootstrap: Provider supports ``omnigent sandbox create`` /
        ``connect`` (``put`` / ``stream_exec`` / ``exec_foreground`` /
        ``wheel_install_command``).
    :param managed_launch: Provider supports server-managed
        ``host_type="managed"`` sessions (``prepare`` / ``provision`` /
        ``start_host``).
    :param local_port_forward: Provider can bridge a local port into the
        sandbox for the App OAuth callback flow.
    :param resume_stopped: Provider can resume a stopped sandbox in place
        with its persistent volume.
    :param programmatic_terminate: Provider can terminate a sandbox
        programmatically.
    :param file_copy: Provider supports copying files into the sandbox.
    :param streaming_exec: Provider supports streaming process execution
        inside the sandbox.
    :param foreground_exec: Provider supports a foreground exec that
        inherits local stdio.
    :param classifies_runner_by_agent: Provider stamps the session's
        resolved built-in agent onto the managed runner as platform
        metadata a policy can select on (the Kubernetes runner Pod's
        ``omnigent.ai/agent`` label). When set, the managed launch path
        threads ``agent_name`` into ``start_host``; providers that leave
        it ``False`` never receive the keyword.
    """

    cli_bootstrap: bool = False
    managed_launch: bool = False
    local_port_forward: bool = False
    resume_stopped: bool = False
    programmatic_terminate: bool = False
    file_copy: bool = False
    streaming_exec: bool = False
    foreground_exec: bool = False
    classifies_runner_by_agent: bool = False


@dataclass(frozen=True)
class RepoCheckout:
    """One repository to clone into a sandbox workspace.

    A managed launch may seed several repositories side by side under the
    workspace root (``<workspace>/<repo_name>`` each), so the agent starts
    with every repo it needs already checked out. The primary repo is
    passed through ``start_host``'s ``repo_url`` / ``repo_branch`` /
    ``repo_name`` primitives; any additional repos ride the ``extra_repos``
    list as :class:`RepoCheckout` values (kept free of the server's
    ``RepoWorkspace`` so the onboarding layer carries no server dependency).

    :param url: Clone URL, e.g. ``"https://github.com/org/repo.git"``.
    :param branch: Branch to clone (``--branch … --single-branch``), or
        ``None`` for the repo's default branch.
    :param repo_name: Directory the clone lands in under the workspace
        root, e.g. ``"repo"``.
    """

    url: str
    branch: str | None
    repo_name: str


@dataclass(frozen=True)
class SandboxSpec:
    """Provider-agnostic description of a sandbox to provision."""

    name: str
    image: str | None = None
    cpu: float | None = None
    memory_mib: int | None = None
    disk_gb: int | None = None
    lifetime_s: int | None = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxInfo:
    """Result of a successful provision or attach."""

    sandbox_id: str
    workspace_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HostContext:
    """Context handed to ``start_host`` when launching a managed host."""

    token: str
    host_id: str
    host_name: str
    server_url: str
    repo_url: str | None = None
    repo_branch: str | None = None
    repo_name: str | None = None
    host_config: dict[str, object] = field(default_factory=dict)
    on_stage: Callable[[str], None] | None = None
