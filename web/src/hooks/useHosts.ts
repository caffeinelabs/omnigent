import { useQuery } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";

export interface Host {
  host_id: string;
  name: string;
  owner: string;
  status: "online" | "offline";
  /**
   * Sandbox provider backing a server-managed host (e.g. "modal");
   * null for user-connected hosts. Optional because older servers
   * omit the field entirely.
   */
  sandbox_provider?: string | null;
  /**
   * Per-harness readiness reported by the host's last connect, e.g.
   * `{"claude-sdk": true, "codex": "needs-auth"}`. `null`/absent means the
   * host has never reported it (older host build) — unknown, never
   * "nothing configured".
   */
  configured_harnesses?: Record<string, boolean | string> | null;
  /**
   * Provider sandbox id (k8s Pod name, etc.). Present for managed
   * sandbox hosts; used with SSHPiper target templating.
   */
  sandbox_id?: string | null;
  /**
   * SSHPiper *target* (left of ``--``), e.g. a cluster-local Service
   * DNS name. Only set when the server has SSHPiper configured.
   */
  ssh_target?: string | null;
  /**
   * Full SSHPiper username ``{ssh_target}--{user}`` ready to use in a
   * Remote-SSH URI. Only set when SSHPiper is configured.
   */
  sshpiper_username?: string | null;
}

interface HostsResponse {
  hosts: Host[];
}

async function fetchHosts(includeSandbox: boolean): Promise<Host[]> {
  const res = await authenticatedFetch("/v1/hosts");
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const body = (await res.json()) as HostsResponse;
  // Hide server-managed sandbox hosts from every host picker: they
  // are launch targets the server creates on demand (and relaunches
  // at will), not user-connectable machines, so offering them as
  // manual targets is misleading. Hosts from older servers lack the
  // field and are kept. `includeSandbox` opts a caller (the chat-header
  // HostBadge) back into seeing them so it can label sandbox sessions.
  if (includeSandbox) return body.hosts;
  return body.hosts.filter((h) => !h.sandbox_provider);
}

interface UseHostsOptions {
  enabled?: boolean;
  includeSandbox?: boolean;
}

export function useHosts(options: UseHostsOptions = {}) {
  const enabled = options.enabled ?? true;
  const includeSandbox = options.includeSandbox ?? false;
  return useQuery({
    // Distinct cache key per filtering mode so the picker's filtered
    // list and the header's unfiltered list don't overwrite each other.
    // A bare ["hosts"] invalidation still prefix-matches both.
    queryKey: ["hosts", { includeSandbox }],
    queryFn: () => fetchHosts(includeSandbox),
    enabled,
    staleTime: 10_000,
    refetchInterval: enabled ? 10_000 : false,
  });
}
