/**
 * Build a VS Code Remote-SSH deep link for an SSHPiper-routed sandbox.
 *
 * SSHPiper splits the SSH username on `--`:
 *   `{target}--{user}` → connect to host `{target}` as `{user}`.
 *
 * The VS Code URI encodes the full `user@gateway` authority so dots and
 * `--` in the username cannot be misparsed as the host separator.
 */

export interface VsCodeRemoteLinkArgs {
  /** SSHPiper gateway hostname from `/v1/info`. */
  sshpiperHost: string;
  /** Gateway SSH port; omit or `22` skips the `:port` suffix. */
  sshpiperPort?: number | null;
  /**
   * SSHPiper composite username (`{target}--{user}`), or the pieces to
   * build it from.
   */
  sshpiperUsername?: string;
  /** Left side of `--` (sandbox Service/Pod DNS). */
  sshTarget?: string;
  /** Right side of `--` (linux user inside the sandbox). */
  sshUser?: string;
  /** Absolute workspace path inside the sandbox (must start with `/`). */
  workspacePath: string;
}

/**
 * Compose the SSHPiper username `{target}--{user}`.
 */
export function buildSshPiperUsername(sshTarget: string, sshUser: string): string {
  return `${sshTarget}--${sshUser}`;
}

/**
 * Build a `vscode://vscode-remote/ssh-remote+…` URI.
 *
 * Returns `null` when required pieces are missing or the workspace path
 * is not absolute.
 */
export function buildVsCodeRemoteUri(args: VsCodeRemoteLinkArgs): string | null {
  const host = args.sshpiperHost?.trim();
  const workspace = args.workspacePath?.trim();
  if (!host || !workspace || !workspace.startsWith("/")) return null;

  const username =
    args.sshpiperUsername?.trim() ||
    (args.sshTarget && args.sshUser
      ? buildSshPiperUsername(args.sshTarget.trim(), args.sshUser.trim())
      : "");
  if (!username || username.includes("@")) return null;

  const port = args.sshpiperPort && args.sshpiperPort !== 22 ? args.sshpiperPort : null;
  const authority = port ? `${username}@${host}:${port}` : `${username}@${host}`;
  // encodeURIComponent so `@` / `--` / `.` in the username stay in the
  // userinfo portion and cannot be read as the host separator.
  return `vscode://vscode-remote/ssh-remote+${encodeURIComponent(authority)}${workspace}`;
}

/**
 * Open the URI via the OS protocol handler. Returns false when the URI
 * could not be built.
 */
export function openVsCodeRemote(args: VsCodeRemoteLinkArgs): boolean {
  const uri = buildVsCodeRemoteUri(args);
  if (!uri) return false;
  window.open(uri);
  return true;
}
