// The body of a view in the Lineage, Timeline or Money lens: each reads its
// own route when it is shown, says it is loading, and says so plainly when it
// could not be read. A lens with nothing to show renders nothing (the bar
// never offers it).

import type { ReactNode } from "react";
import { ChevronRightIcon } from "lucide-react";
import type { NodePage, TimelineView } from "../nodeTypes";
import { useLineage, useMoney } from "./lensQueries";
import { LineageLens } from "./LineageLens";
import { MoneyLens } from "./MoneyLens";
import { TimelineLens } from "./TimelineLens";

function Loading({ label }: { label: string }) {
  return (
    <div
      aria-busy="true"
      aria-label={`Loading ${label}`}
      className="h-40 animate-pulse rounded-xl bg-muted"
    />
  );
}

function Failed({ what, error }: { what: string; error: unknown }) {
  return (
    <p role="alert" className="text-sm text-status-red-fg">
      The {what} could not be read: {error instanceof Error ? error.message : String(error)}
    </p>
  );
}

export function LineageBody({ nodeRef }: { nodeRef: string }): ReactNode {
  const query = useLineage(nodeRef);
  if (query.isLoading) return <Loading label="the lineage" />;
  if (query.isError) return <Failed what="lineage" error={query.error} />;
  return query.data ? <LineageLens view={query.data} /> : null;
}

export function MoneyBody({ nodeRef }: { nodeRef: string }): ReactNode {
  const query = useMoney(nodeRef);
  if (query.isLoading) return <Loading label="the money" />;
  if (query.isError) return <Failed what="money" error={query.error} />;
  return query.data ? <MoneyLens view={query.data} /> : null;
}

export function TimelineBody({
  query,
}: {
  query: { data?: TimelineView | null; isLoading: boolean; isError: boolean; error: unknown };
}): ReactNode {
  if (query.isLoading) return <Loading label="the timeline" />;
  if (query.isError) return <Failed what="timeline" error={query.error} />;
  return query.data ? <TimelineLens view={query.data} /> : null;
}

/** Where a node came from, one quiet line on a page with nothing below it (a click shows it). */
export function LineageFold({ page, onOpen }: { page: NodePage; onOpen: () => void }) {
  return (
    <button
      type="button"
      data-testid="v3-lineage-fold"
      onClick={onOpen}
      title={`Where ${page.node.title} came from, and where it landed`}
      className="mt-1 flex items-center gap-1 px-0.5 text-left text-[12.5px] text-muted-foreground hover:text-foreground"
    >
      Lineage
      <ChevronRightIcon aria-hidden className="size-3" />
    </button>
  );
}
