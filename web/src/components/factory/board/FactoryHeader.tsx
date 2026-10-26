/**
 * The cockpit header: the factory's name, whether the page is live, its state,
 * cadence, run time, cost and when the next heartbeat checks happen, plus the
 * factory controls.
 */

import { type ReactNode, useState } from "react";
import { FactoryIcon, HeartPulseIcon, PlusIcon, TimerIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { FACTORY_FALLBACK_POLL_MS } from "@/hooks/useFactory";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { AddProjectDialog } from "../controls/AddProjectDialog";
import { FactoryControlsMenu } from "../controls/FactoryControlsMenu";
import { FactoryStateChip } from "../FactoryIndexCard";
import { formatElapsed, formatUsd } from "../factoryFormat";
import { factoryElapsed, nextChecks, projectLeadIds, untilText } from "./boardModel";
import { useFactoryLive } from "./factoryLive";

/** `● Live` while pushes arrive, `◌ Reconnecting · polling 15 s` while they don't. */
export function LiveDot() {
  const live = useFactoryLive();
  return (
    <span
      data-testid="factory-live-dot"
      data-state={live ? "live" : "reconnecting"}
      title={
        live
          ? "Updates arrive as they happen"
          : `Live updates are reconnecting; refreshing every ${FACTORY_FALLBACK_POLL_MS / 1000} s`
      }
      className="inline-flex items-center gap-1.5 text-sm text-muted-foreground"
    >
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full",
          live ? "bg-success" : "border border-muted-foreground bg-transparent",
        )}
      />
      {live ? "Live" : `Reconnecting · polling ${FACTORY_FALLBACK_POLL_MS / 1000} s`}
    </span>
  );
}

function Meta({
  children,
  title,
  className,
}: {
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 text-sm whitespace-nowrap text-muted-foreground",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function FactoryHeader({ snapshot, nowS }: { snapshot: FactorySnapshot; nowS: number }) {
  const { factory } = snapshot;
  const [adding, setAdding] = useState(false);
  const elapsed = formatElapsed(factoryElapsed(factory, nowS));
  const cost = formatUsd(factory.cost_usd);
  const checks =
    factory.state === "active" ? nextChecks(factory, nowS, projectLeadIds(snapshot)) : null;
  const checkParts = [
    checks?.lead ? `${checks.lead.title} ${untilText(checks.lead.in)}` : null,
    checks?.pls != null ? `PLs ${untilText(checks.pls)}` : null,
    ...(checks?.others ?? []).map((other) => `${other.title} ${untilText(other.in)}`),
  ].filter(Boolean);
  return (
    <header data-testid="factory-header" className="flex flex-col gap-1.5">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex min-w-0 items-center gap-2 text-2xl font-semibold">
          <FactoryIcon aria-hidden className="size-5 shrink-0 text-muted-foreground" />
          <span className="truncate">{factory.name}</span>
        </h1>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setAdding(true)}
            data-testid="factory-add-project"
            componentId="factory.header.add_project"
          >
            <PlusIcon className="size-3.5" />
            Add project
          </Button>
          <FactoryControlsMenu
            factoryId={factory.id}
            factory={factory}
            members={snapshot.members}
          />
        </div>
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <LiveDot />
        <FactoryStateChip state={factory.state} />
        <Meta>{factory.cadence === "demo" ? "Demo cadence" : "Normal cadence"}</Meta>
        {elapsed && (
          <Meta title="Running for">
            <TimerIcon aria-hidden className="size-3.5" />
            <span className="font-mono tabular-nums">{elapsed}</span>
          </Meta>
        )}
        {cost && (
          <Meta title="Cost so far (all agents)" className="text-foreground">
            <span className="font-mono tabular-nums">{cost}</span>
          </Meta>
        )}
        {checkParts.length > 0 && (
          <Meta title="Heartbeats check in no later than this; quiet beats are skipped when nothing changed">
            <HeartPulseIcon aria-hidden className="size-3.5" />
            next check ≤ <span className="tabular-nums">{checkParts.join(" · ")}</span>
          </Meta>
        )}
      </div>
      <AddProjectDialog factoryId={factory.id} open={adding} onOpenChange={setAdding} />
    </header>
  );
}
