import { CodeIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useHosts } from "@/hooks/useHosts";
import { useSession } from "@/hooks/useSession";
import { useSessionHostOnline } from "@/hooks/RunnerHealthProvider";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { openVsCodeRemote } from "@/lib/vscodeRemote";

type Variant = "button" | "menu-item";

/**
 * "Open in VS Code" control for a managed sandbox session.
 *
 * Self-contained (same shape as HostBadge): reads session + hosts +
 * `/v1/info` SSHPiper settings and renders nothing unless the session
 * is an online managed sandbox with a resolvable SSH target and the
 * server has SSHPiper configured.
 */
export function OpenInVsCodeRemote({
  sessionId,
  variant = "button",
}: {
  sessionId: string;
  variant?: Variant;
}) {
  const info = useServerInfo();
  const { session } = useSession(sessionId);
  const hostId = session?.hostId ?? null;
  const { data: hosts } = useHosts({ includeSandbox: true, enabled: Boolean(hostId) });
  const liveOnline = useSessionHostOnline(sessionId);

  const host = hostId ? hosts?.find((h) => h.host_id === hostId) : undefined;
  const online =
    liveOnline === undefined ? (host ? host.status === "online" : undefined) : liveOnline;

  const sshpiperHost = info !== "loading" ? info.sshpiper_host : null;
  const sshpiperPort = info !== "loading" ? info.sshpiper_port : null;
  const sshpiperUser = info !== "loading" ? info.sshpiper_user : null;
  const workspace = session?.workspace ?? null;
  const sshTarget = host?.ssh_target ?? null;
  const sshpiperUsername = host?.sshpiper_username ?? null;

  const ready =
    Boolean(sshpiperHost) &&
    Boolean(workspace) &&
    Boolean(sshTarget || sshpiperUsername) &&
    online === true &&
    Boolean(host?.sandbox_provider);

  if (!ready || !sshpiperHost || !workspace) return null;

  const open = () => {
    openVsCodeRemote({
      sshpiperHost,
      sshpiperPort,
      sshpiperUsername: sshpiperUsername ?? undefined,
      sshTarget: sshTarget ?? undefined,
      sshUser: sshpiperUser ?? undefined,
      workspacePath: workspace,
    });
  };

  if (variant === "menu-item") {
    return (
      <DropdownMenuItem
        onSelect={open}
        data-testid="mobile-open-vscode-remote"
        className="gap-2.5 px-2.5 py-2 text-base"
      >
        <CodeIcon className="size-4" />
        Open in VS Code
      </DropdownMenuItem>
    );
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          aria-label="Open in VS Code Remote"
          data-testid="open-vscode-remote"
          onClick={open}
          className="hidden h-8 gap-1.5 rounded-full px-3 text-13 font-normal text-muted-foreground hover:text-foreground md:inline-flex"
        >
          <CodeIcon className="size-4" />
          VS Code
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">Open this sandbox in VS Code Remote via SSH</TooltipContent>
    </Tooltip>
  );
}

/**
 * True when `/v1/info` advertises an SSHPiper gateway — used to keep the
 * mobile session-actions menu visible even before host metadata loads.
 */
export function sshpiperConfigured(info: ReturnType<typeof useServerInfo>): boolean {
  return info !== "loading" && Boolean(info.sshpiper_host);
}
