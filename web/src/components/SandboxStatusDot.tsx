import { cn } from "@/lib/utils";

/**
 * Per-chat sandbox running indicator for the sidebar row.
 *
 * A session's sandbox is "running" when its host tunnel is live
 * (`host_online === true`); "stopped" when the session is host-bound but the
 * tunnel is down (`host_online === false`) — the sandbox has been torn down or
 * its host expired, and the next message relaunches it. A session with no host
 * binding (`hostId` null — never used a managed sandbox) shows nothing, so the
 * dot only appears where "is the sandbox up?" is a meaningful question.
 *
 * Colors mirror {@link HostBadge}'s convention (green `bg-success` online), but
 * a stopped sandbox uses a muted dot rather than a red one: across a long chat
 * list most sandboxes are idle-stopped by design, so red would read as a fleet
 * of errors. Green calls out the few that are live; muted marks the rest as
 * simply "not running". While liveness hasn't been observed yet (`undefined`)
 * the dot stays neutral instead of flashing a state, matching HostBadge.
 */
export function SandboxStatusDot({
  hostId,
  online,
}: {
  hostId: string | null | undefined;
  online: boolean | null | undefined;
}) {
  // Not host-bound → not a sandbox session; nothing to report.
  // `online === null` is the liveness stream's "no host" signal and means the
  // same thing, so treat it identically.
  if (!hostId || online === null) return null;

  const running = online === true;
  const stopped = online === false;
  const label = running
    ? "Sandbox running"
    : stopped
      ? "Sandbox stopped"
      : "Sandbox status unknown";

  return (
    <span
      className="inline-flex shrink-0 items-center"
      title={label}
      data-testid="sandbox-status-dot"
      data-state={running ? "running" : stopped ? "stopped" : "unknown"}
    >
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full",
          running ? "bg-success" : stopped ? "bg-muted-foreground/40" : "bg-muted-foreground/25",
        )}
      />
      <span className="sr-only">{label}</span>
    </span>
  );
}
