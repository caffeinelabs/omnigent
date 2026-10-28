/**
 * Factory index (`/factory`): one card per factory the viewer can see, with
 * its pipeline counts and what needs the human (a dense table once there are
 * more than three), who shared it and as what, plus the way to start one.
 *
 * Never shows the empty state before the list settles; a failed refresh keeps
 * the last cards under an alert with Retry.
 */

import { FactoryIcon, PlusIcon } from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import { RunningDot } from "@/components/RunningDot";
import {
  FactoryIndexCard,
  FactoryIndexCardSkeleton,
  FactoryStateChip,
  sharedLine,
} from "@/components/factory/FactoryIndexCard";
import { factoryPageHref } from "@/components/factory/factoryRoutes";
import { formatUsd } from "@/components/factory/factoryFormat";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { useFactoryNow } from "@/components/factory/factoryClock";
import {
  FactoryPreflight,
  FactoryPreflightSkeleton,
  preflightPasses,
} from "@/components/factory/FactoryPreflight";
import { Button } from "@/components/ui/button";
import { useFactories, usePreflight } from "@/hooks/useFactory";
import type { FactorySummary } from "@/lib/factoryApi";
import { Link, useNavigate } from "@/lib/routing";

function NewFactoryButton({
  testId,
  disabled = false,
  size = "sm",
}: {
  testId: string;
  disabled?: boolean;
  size?: "sm" | "default";
}) {
  if (disabled) {
    return (
      <Button size={size} disabled data-testid={testId}>
        <PlusIcon />
        New factory
      </Button>
    );
  }
  return (
    <Button asChild size={size} data-testid={testId}>
      <Link to="/factory/new" componentId="factory.index.new">
        <PlusIcon />
        New factory
      </Link>
    </Button>
  );
}

/** Cards up to this many factories; a dense table past it. */
export const INDEX_TABLE_AFTER = 3;

const TH = "px-3 py-2 text-left text-xs font-medium whitespace-nowrap text-muted-foreground";

/** Every factory as one row: state, $/h, what needs you, agents, people, cost. */
function FactoryIndexTable({ factories }: { factories: FactorySummary[] }) {
  const navigate = useNavigate();
  return (
    <div className="min-w-0 overflow-x-auto rounded-xl border border-border bg-card">
      <table data-testid="factory-list-table" className="w-full min-w-0 text-sm">
        <thead>
          <tr className="border-b border-border">
            <th className={`${TH} w-[40%]`}>Factory</th>
            <th className={TH}>State</th>
            <th className={`${TH} text-right max-md:hidden`}>$/h</th>
            <th className={`${TH} text-right`}>Needs you</th>
            <th className={`${TH} text-right max-sm:hidden`}>Agents</th>
            <th className={`${TH} text-right max-md:hidden`}>People</th>
            <th className={`${TH} text-right max-sm:hidden`}>$</th>
          </tr>
        </thead>
        <tbody>
          {factories.map((factory) => {
            const shared = sharedLine(factory);
            const mine = factory.mine ?? factory.decisions_open;
            return (
              <tr
                key={factory.id}
                data-testid={`factory-row-${factory.id}`}
                onClick={() => navigate(factoryPageHref(factory.id))}
                className="cursor-pointer border-b border-border/60 last:border-0 hover:bg-muted/50"
              >
                <td className="max-w-0 px-3 py-2">
                  <Link
                    to={factoryPageHref(factory.id)}
                    componentId="factory.index.row"
                    onClick={(e) => e.stopPropagation()}
                    className="block truncate font-medium hover:underline"
                  >
                    {factory.name}
                  </Link>
                  {shared && (
                    <span className="block truncate text-xs text-muted-foreground">{shared}</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <FactoryStateChip state={factory.state} />
                </td>
                <td className="px-3 py-2 text-right font-mono text-muted-foreground tabular-nums max-md:hidden">
                  {factory.usd_per_h != null ? formatUsd(factory.usd_per_h) : "—"}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {mine > 0 ? (
                    <span className="rounded-full bg-warning/15 px-1.5 text-xs font-medium text-warning">
                      {mine}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                  {factory.prs_ready > 0 && (
                    <span className="ml-1.5 text-xs text-success">★{factory.prs_ready}</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right whitespace-nowrap tabular-nums max-sm:hidden">
                  {factory.members_total}
                  {factory.members_running > 0 && (
                    <span className="ml-1 inline-flex items-center gap-1 text-session-active">
                      <RunningDot className="size-2" />
                      {factory.members_running}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-right text-muted-foreground tabular-nums max-md:hidden">
                  {factory.people_count ?? "—"}
                </td>
                <td className="px-3 py-2 text-right font-mono tabular-nums max-sm:hidden">
                  {formatUsd(factory.cost_usd) ?? "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** No factory yet: what a factory is, the start checklist, and the CTA. */
function FactoryEmptyState() {
  const preflight = usePreflight();
  const blocked = preflight.data !== undefined && !preflightPasses(preflight.data);
  return (
    <section
      data-testid="factory-empty"
      className="flex flex-col items-center gap-5 rounded-xl border border-border bg-card px-5 py-10 text-center"
    >
      <div className="flex flex-col items-center gap-2">
        <FactoryIcon aria-hidden className="size-8 text-muted-foreground/50" />
        <h2 className="text-ui font-medium">No factory yet</h2>
        <p className="max-w-prose text-sm text-muted-foreground">
          A factory runs several projects at once: two leads that supervise each other, one project
          lead per project with its own team, live previews and recorded demos. Humans review and
          merge.
        </p>
      </div>
      <div className="flex w-full max-w-xl flex-col gap-2 border-t border-border pt-5">
        <h3 className="text-left text-sm font-medium text-muted-foreground">Preflight</h3>
        {preflight.data ? (
          <FactoryPreflight preflight={preflight.data} />
        ) : preflight.isLoading ? (
          <FactoryPreflightSkeleton />
        ) : (
          <p className="text-left text-sm text-muted-foreground">
            Couldn’t check this server’s factory setup.
          </p>
        )}
      </div>
      <div className="flex flex-col items-center gap-1.5">
        <NewFactoryButton testId="factory-empty-new-button" size="default" disabled={blocked} />
        {blocked && (
          <p className="text-sm text-muted-foreground">Fix the failing checks to start one.</p>
        )}
      </div>
    </section>
  );
}

export function FactoryIndexPage() {
  const factories = useFactories();
  const now = useFactoryNow();
  const list = factories.data?.data;
  const error = (factories.error as Error | null) ?? null;
  // The empty state carries its own (preflight-gated) CTA, so the header has none then.
  const headerButton = list ? list.length > 0 : error !== null;
  return (
    <PageScroll data-testid="factory-index" maxWidthClassName="max-w-4xl" contentClassName="px-6">
      <div className="mb-6 flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Factory</h1>
        {headerButton && <NewFactoryButton testId="factory-new-button" />}
      </div>
      {error && (
        <FactoryLoadError
          error={error}
          updatedAt={list ? factories.dataUpdatedAt : 0}
          onRetry={() => void factories.refetch()}
        />
      )}
      {factories.isLoading && (
        <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading factories">
          <FactoryIndexCardSkeleton />
          <FactoryIndexCardSkeleton />
        </div>
      )}
      {list && list.length > INDEX_TABLE_AFTER && <FactoryIndexTable factories={list} />}
      {list && list.length > 0 && list.length <= INDEX_TABLE_AFTER && (
        <div className="flex flex-col gap-3" data-testid="factory-list">
          {list.map((factory) => (
            <FactoryIndexCard key={factory.id} factory={factory} now={now} />
          ))}
        </div>
      )}
      {list && list.length === 0 && <FactoryEmptyState />}
    </PageScroll>
  );
}
