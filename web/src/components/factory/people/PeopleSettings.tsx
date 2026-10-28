/**
 * Settings ▸ People: the roster (invite, roles, remove), whose identity the
 * agents use, and the audit log of people and control events: who was
 * invited, changed, removed or left, who asked for access, and every
 * pause, resume, restart, relaunch and stop with its author.
 */

import { useMemo } from "react";
import { ScrollTextIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useFactoryEvents, useFactoryPeople } from "@/hooks/useFactory";
import type { EventFilters, EventView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { ageText } from "../board/boardModel";
import { FactoryLoadError } from "../FactoryLoadError";
import { EmptyState, SectionCard } from "../project/projectParts";
import { credentialLine } from "../shell/FactoryPresence";
import { InviteRow, PeopleRoster, RosterSkeleton } from "./PeopleRoster";
import { RolesLegend } from "./ShareDialog";

/** The audit log's events: people, access requests and controls. */
export const AUDIT_FILTERS: EventFilters = {
  kinds: ["people.*", "access.*", "control.*"],
  limit: 50,
};

function clock(ms: number): string {
  return new Date(ms).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function by(event: EventView): string | null {
  const value = event.data?.by;
  return typeof value === "string" && value ? value : null;
}

function AuditRow({ event, nowS }: { event: EventView; nowS: number }) {
  const author = by(event);
  return (
    <li
      data-testid={`audit-${event.id}`}
      className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 py-2 text-sm first:pt-0 last:pb-0 md:grid-cols-[9rem_8rem_minmax(0,1fr)_auto]"
    >
      <span className="text-muted-foreground tabular-nums max-md:hidden" title={clock(event.ts)}>
        {clock(event.ts)}
      </span>
      <span
        className={cn(
          "self-start justify-self-start rounded-sm px-1.5 font-mono text-xs leading-5 max-md:col-span-2",
          event.kind.startsWith("people.") || event.kind.startsWith("access.")
            ? "bg-session-active/10 text-session-active"
            : "bg-muted text-muted-foreground",
        )}
      >
        {event.kind}
      </span>
      <span className="min-w-0 break-words">
        {event.summary}
        {author && <span className="text-muted-foreground"> · by {author}</span>}
      </span>
      <span className="text-muted-foreground tabular-nums">
        {ageText(nowS - Math.floor(event.ts / 1000))}
      </span>
    </li>
  );
}

export function AuditLog({ factoryId, nowS }: { factoryId: string; nowS: number }) {
  const query = useFactoryEvents(factoryId, AUDIT_FILTERS);
  const events = useMemo(() => query.data?.pages.flatMap((p) => p.data) ?? [], [query.data]);
  return (
    <SectionCard
      title="Audit log"
      meta="people, access and controls"
      testId="people-audit"
      action={
        query.hasNextPage ? (
          <Button
            variant="ghost"
            size="xs"
            onClick={() => void query.fetchNextPage()}
            loading={query.isFetchingNextPage}
            componentId="factory.people.audit_more"
          >
            Older
          </Button>
        ) : undefined
      }
    >
      {query.error instanceof Error && (
        <FactoryLoadError
          error={query.error}
          updatedAt={query.data ? query.dataUpdatedAt : 0}
          onRetry={() => void query.refetch()}
          what="the audit log"
          testId="people-audit-error"
        />
      )}
      {query.isLoading && (
        <div aria-busy="true" aria-label="Loading the audit log" className="flex flex-col gap-2">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-5 animate-pulse rounded bg-muted" />
          ))}
        </div>
      )}
      {query.data && events.length === 0 && (
        <EmptyState
          testId="people-audit-empty"
          icon={<ScrollTextIcon aria-hidden className="size-5 text-muted-foreground/50" />}
          title="Nothing yet"
          body="Invites, role changes, access requests and controls show up here with who did them."
        />
      )}
      {events.length > 0 && (
        <ul className="flex flex-col divide-y divide-border">
          {events.map((event) => (
            <AuditRow key={event.id} event={event} nowS={nowS} />
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

export function PeopleSettings({ factoryId, nowS }: { factoryId: string; nowS: number }) {
  const people = useFactoryPeople(factoryId);
  const data = people.data;
  return (
    <div data-testid="people-settings" className="flex min-w-0 flex-col gap-4">
      <SectionCard
        title="People"
        meta={data ? `${data.count} with access` : undefined}
        testId="people-card"
      >
        {people.error && !data && (
          <FactoryLoadError
            error={people.error}
            updatedAt={0}
            onRetry={() => void people.refetch()}
            what="the people"
            testId="people-error"
          />
        )}
        {people.isLoading && <RosterSkeleton />}
        {data && (
          <div className="flex flex-col gap-4">
            <InviteRow
              factoryId={factoryId}
              roster={data.data}
              canInvite={data.viewer.can.invite}
            />
            <PeopleRoster factoryId={factoryId} people={data} nowS={nowS} />
            <p
              data-testid="people-credential-line"
              className="rounded-lg bg-muted/50 px-3 py-2 text-sm text-muted-foreground"
            >
              {credentialLine(data.runs_as?.user_id)}.
            </p>
            <RolesLegend />
          </div>
        )}
      </SectionCard>
      <AuditLog factoryId={factoryId} nowS={nowS} />
    </div>
  );
}
