/**
 * Factory index (`/factory`): one card per factory the viewer can see, with
 * its pipeline counts and what needs the human, plus the way to start one.
 *
 * Never shows the empty state before the list settles; a failed refresh keeps
 * the last cards under an alert with Retry.
 */

import { FactoryIcon, PlusIcon } from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import { FactoryIndexCard, FactoryIndexCardSkeleton } from "@/components/factory/FactoryIndexCard";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { useFactoryNow } from "@/components/factory/factoryClock";
import {
  FactoryPreflight,
  FactoryPreflightSkeleton,
  preflightPasses,
} from "@/components/factory/FactoryPreflight";
import { Button } from "@/components/ui/button";
import { useFactories, usePreflight } from "@/hooks/useFactory";
import { Link } from "@/lib/routing";

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
      {list && list.length > 0 && (
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
