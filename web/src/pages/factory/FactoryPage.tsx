/**
 * Factory cockpit (`/factory/:factoryId`): the header for now; the board,
 * review drawer and decisions mount here as they land.
 */

import { ArrowLeftIcon, FactoryIcon, TimerIcon } from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import { FactoryStateChip } from "@/components/factory/FactoryIndexCard";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { formatElapsed, formatUsd } from "@/components/factory/factoryFormat";
import { useFactory } from "@/hooks/useFactory";
import { Link, useParams } from "@/lib/routing";
import { ApiError } from "@/lib/sessionsApi";

export function FactoryPage() {
  const { factoryId } = useParams<{ factoryId: string }>();
  const { data, error, isLoading, updatedAt, refetch } = useFactory(factoryId);
  const missing = !data && error instanceof ApiError && error.status === 404;
  const factory = data?.factory;
  const elapsed = formatElapsed(factory?.elapsed_s);
  const cost = formatUsd(factory?.cost_usd);
  return (
    <PageScroll data-testid="factory-page" maxWidthClassName="max-w-7xl" contentClassName="px-6">
      <Link
        to="/factory"
        componentId="factory.page.back"
        className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeftIcon aria-hidden className="size-3.5" />
        Factory
      </Link>
      {error && !missing && (
        <FactoryLoadError
          error={error}
          updatedAt={data ? updatedAt : 0}
          onRetry={refetch}
          what="this factory"
        />
      )}
      {isLoading && (
        <div aria-busy="true" aria-label="Loading factory" className="flex items-center gap-3">
          <div className="h-7 w-56 animate-pulse rounded bg-muted" />
          <div className="h-5 w-20 animate-pulse rounded bg-muted" />
        </div>
      )}
      {missing && (
        <p data-testid="factory-missing" className="text-ui text-muted-foreground">
          This factory doesn’t exist, or you can’t see it.
        </p>
      )}
      {factory && (
        <header
          data-testid="factory-header"
          className="flex flex-wrap items-center gap-x-4 gap-y-1"
        >
          <h1 className="flex min-w-0 items-center gap-2 text-2xl font-semibold">
            <FactoryIcon aria-hidden className="size-5 shrink-0 text-muted-foreground" />
            <span className="truncate">{factory.name}</span>
          </h1>
          <FactoryStateChip state={factory.state} />
          <span className="text-sm text-muted-foreground">
            {factory.cadence === "demo" ? "Demo cadence" : "Normal cadence"}
          </span>
          {elapsed && (
            <span className="inline-flex items-center gap-1 text-sm text-muted-foreground">
              <TimerIcon aria-hidden className="size-3.5" />
              {elapsed}
            </span>
          )}
          {cost && <span className="font-mono text-sm tabular-nums">{cost}</span>}
        </header>
      )}
    </PageScroll>
  );
}
