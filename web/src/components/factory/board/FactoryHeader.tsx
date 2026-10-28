/**
 * The factory header on every factory page: the name, whether the page is
 * live, the factory's state, its engine mode and definition version (when
 * the server sends them), the base and the env at its head, spend, who is
 * here, Share, Add project, Pause all and the ⋯ menu.
 */

import { type ReactNode, useState } from "react";
import { FactoryIcon, PlusIcon, Share2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { FACTORY_FALLBACK_POLL_MS } from "@/hooks/useFactory";
import type { EngineMode, FactorySnapshot } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { AddProjectDialog } from "../controls/AddProjectDialog";
import { FactoryControlsMenu } from "../controls/FactoryControlsMenu";
import { FactoryStateChip } from "../FactoryIndexCard";
import { formatElapsed, formatUsd } from "../factoryFormat";
import { BaseEnvLine } from "../shell/BaseEnvLine";
import { FactoryPresence } from "../shell/FactoryPresence";
import { LeverGated } from "../shell/viewerCan";
import { factoryElapsed } from "./boardModel";
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
      className="inline-flex items-center gap-1.5 text-sm whitespace-nowrap text-muted-foreground"
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
  testId,
}: {
  children: ReactNode;
  title?: string;
  className?: string;
  testId?: string;
}) {
  return (
    <span
      title={title}
      data-testid={testId}
      className={cn(
        "inline-flex items-center gap-1 text-sm whitespace-nowrap text-muted-foreground",
        className,
      )}
    >
      {children}
    </span>
  );
}

const ENGINE_HINT: Record<EngineMode, string> = {
  observe: "Engine observes: loops and budgets are measured, nothing is enforced",
  assist: "Engine assists: NEXT hints and loop escalations, budgets warn",
  enforce: "Engine enforces: attempt budgets refuse, spend caps pause",
};

/** A small outlined chip for the engine mode and definition version. */
function HeaderChip({
  children,
  title,
  testId,
}: {
  children: ReactNode;
  title: string;
  testId: string;
}) {
  return (
    <span
      title={title}
      data-testid={testId}
      className="inline-flex h-5 items-center rounded-sm border border-border px-1.5 font-mono text-xs whitespace-nowrap text-muted-foreground"
    >
      {children}
    </span>
  );
}

export function FactoryHeader({
  snapshot,
  nowS,
  onShare,
  onAddProject,
}: {
  snapshot: FactorySnapshot;
  nowS: number;
  /** Open the Share dialog; the button is hidden without it. */
  onShare?: () => void;
  /** Open the shell's Add project dialog; without it the header has its own. */
  onAddProject?: () => void;
}) {
  const { factory } = snapshot;
  const [adding, setAdding] = useState(false);
  const addProject = onAddProject ?? (() => setAdding(true));
  const elapsed = formatElapsed(factoryElapsed(factory, nowS));
  const cost = formatUsd(factory.cost_usd);
  const perHour = snapshot.totals.usd_per_h;
  const engine = snapshot.engine_mode ?? null;
  const defVersion = snapshot.def_version ?? null;
  return (
    <header data-testid="factory-header" className="flex min-w-0 flex-col gap-1.5">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex min-w-0 items-center gap-2 text-xl font-semibold">
          <FactoryIcon aria-hidden className="size-5 shrink-0 text-muted-foreground" />
          <span className="truncate">{factory.name}</span>
        </h1>
        <div className="flex min-w-0 shrink-0 items-center gap-2">
          <FactoryPresence
            factoryId={factory.id}
            presence={snapshot.presence ?? []}
            selfId={snapshot.viewer?.user_id}
          />
          {onShare && (
            <Button
              variant="outline"
              size="sm"
              onClick={onShare}
              data-testid="factory-share"
              componentId="factory.header.share"
            >
              <Share2Icon className="size-3.5" />
              Share
            </Button>
          )}
          <LeverGated lever="apply_projects">
            <Button
              variant="outline"
              size="sm"
              onClick={addProject}
              data-testid="factory-add-project"
              componentId="factory.header.add_project"
              className="max-sm:px-2"
            >
              <PlusIcon className="size-3.5" />
              <span className="max-sm:sr-only">Add project</span>
            </Button>
          </LeverGated>
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
        {engine && (
          <HeaderChip testId="factory-engine-mode" title={ENGINE_HINT[engine]}>
            {engine}
          </HeaderChip>
        )}
        {defVersion != null && (
          <HeaderChip testId="factory-def-version" title="The factory definition version in force">
            def v{defVersion}
          </HeaderChip>
        )}
        {factory.base && <BaseEnvLine base={factory.base} nowS={nowS} />}
        {cost && (
          <Meta title="Cost so far (all agents)" className="text-foreground" testId="factory-cost">
            <span className="font-mono tabular-nums">{cost}</span>
            {perHour != null && (
              <span className="font-mono text-muted-foreground tabular-nums">
                ({formatUsd(perHour)}/h)
              </span>
            )}
          </Meta>
        )}
        {elapsed && (
          <Meta title="Running for">
            <span className="font-mono tabular-nums">{elapsed}</span>
          </Meta>
        )}
      </div>
      {!onAddProject && (
        <AddProjectDialog factoryId={factory.id} open={adding} onOpenChange={setAdding} />
      )}
    </header>
  );
}
