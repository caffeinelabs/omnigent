import type { ReactNode } from "react";
import { CircleCheckIcon, CircleXIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { beginGithubConnect } from "@/lib/githubIntegration";
import type { Preflight } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";

type PreflightKey = "github" | "agents" | "sandbox" | "enabled";

interface PreflightRow {
  key: PreflightKey;
  ok: boolean;
  label: string;
  fix?: ReactNode;
}

/** Whether every check passes (a factory can start). */
export function preflightPasses(preflight: Preflight): boolean {
  return (
    preflight.github.connected &&
    preflight.agents.factory_lead &&
    preflight.agents.factory_pl &&
    preflight.sandbox_host &&
    preflight.factory_enabled
  );
}

function rows(preflight: Preflight, returnTo: string): PreflightRow[] {
  const missingAgents = [
    !preflight.agents.factory_lead && "factory-lead",
    !preflight.agents.factory_pl && "factory-pl",
  ].filter(Boolean);
  return [
    {
      key: "github",
      ok: preflight.github.connected,
      label: preflight.github.connected
        ? `GitHub connected${preflight.github.login ? ` (${preflight.github.login})` : ""}`
        : "GitHub not connected",
      fix: (
        <Button
          variant="outline"
          size="xs"
          componentId="factory.preflight.connect_github"
          onClick={() => beginGithubConnect(returnTo)}
        >
          Connect GitHub
        </Button>
      ),
    },
    {
      key: "agents",
      ok: missingAgents.length === 0,
      label:
        missingAgents.length === 0
          ? "Factory agents installed"
          : `Missing agent${missingAgents.length > 1 ? "s" : ""}: ${missingAgents.join(", ")}`,
      fix: <span>Install the factory bundles (see docs/FACTORY.md)</span>,
    },
    {
      key: "sandbox",
      ok: preflight.sandbox_host,
      label: preflight.sandbox_host ? "Sandbox host available" : "No sandbox host",
      fix: <span>Managed sandboxes are not configured on this server</span>,
    },
    {
      key: "enabled",
      ok: preflight.factory_enabled,
      label: preflight.factory_enabled
        ? "Factory enabled on this server"
        : "Factory is off on this server",
      fix: <span>Ask an admin to set OMNIGENT_FACTORY_ENABLED=1</span>,
    },
  ];
}

/**
 * The start checklist: GitHub, the factory agents, a sandbox host and the
 * server switch, each with a fix when it fails.
 */
export function FactoryPreflight({
  preflight,
  returnTo = "/factory",
}: {
  preflight: Preflight;
  returnTo?: string;
}) {
  return (
    <ul
      aria-label="Preflight"
      data-testid="factory-preflight"
      className="grid w-full gap-x-6 gap-y-2 text-left sm:grid-cols-2"
    >
      {rows(preflight, returnTo).map((row) => (
        <li
          key={row.key}
          data-testid={`factory-preflight-${row.key}`}
          data-state={row.ok ? "ok" : "fail"}
          className="flex min-w-0 items-start gap-2 text-ui"
        >
          {row.ok ? (
            <CircleCheckIcon aria-hidden className="mt-1 size-4 shrink-0 text-success" />
          ) : (
            <CircleXIcon aria-hidden className="mt-1 size-4 shrink-0 text-destructive" />
          )}
          <span className="flex min-w-0 flex-col gap-1">
            <span className={cn(!row.ok && "font-medium")}>{row.label}</span>
            {!row.ok && row.fix && <span className="text-sm text-muted-foreground">{row.fix}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Placeholder rows while the checks load. */
export function FactoryPreflightSkeleton() {
  return (
    <ul aria-hidden className="grid w-full gap-x-6 gap-y-2 sm:grid-cols-2">
      {["github", "agents", "sandbox", "enabled"].map((key) => (
        <li key={key} className="flex items-center gap-2">
          <span className="size-4 animate-pulse rounded-full bg-muted" />
          <span className="h-4 w-44 animate-pulse rounded bg-muted" />
        </li>
      ))}
    </ul>
  );
}
