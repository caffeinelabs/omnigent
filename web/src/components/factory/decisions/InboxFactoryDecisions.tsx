/**
 * The Inbox's factory sections: first "Assigned to you" (decisions,
 * proposals and incidents routed to the viewer or a role they hold, across factories),
 * then every other open decision grouped by factory, answerable in place
 * with the same card as the cockpit's rail. A card answered here stays and
 * follows its factory's snapshot until the delivery receipt arrives.
 */

import { useState } from "react";
import { ArrowRightIcon, FactoryIcon, TriangleAlertIcon, UserRoundCheckIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useFactory } from "@/hooks/useFactory";
import type { DecisionView, OpenDecisionItem } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { ProposalCard } from "../cards/ProposalCard";
import { plural } from "../factoryFormat";
import { useProposals } from "../room/roomQueries";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { DecisionCard } from "./DecisionCard";
import { InboxAssignedIncidents, useAssignedIncidents } from "./InboxAssignedIncidents";
import { orderOpenDecisions } from "./DecisionsRail";
import { proposalOf } from "./decisionsPageModel";

function InboxDecision({
  item,
  factoryName,
  answered,
  onAnswered,
}: {
  item: OpenDecisionItem;
  /** Named on the card when cards of several factories mix ("Assigned to you"). */
  factoryName?: string;
  /** Answered here: follow the factory snapshot for the delivery receipt. */
  answered: boolean;
  onAnswered: (decision: DecisionView) => void;
}) {
  const isProposal = item.decision.source === "proposal";
  const { data } = useFactory(answered || isProposal ? item.factory.id : null);
  const proposals = useProposals(item.factory.id, isProposal);
  const fresh = data?.decisions.find((d) => d.id === item.decision.id) ?? item.decision;
  // A proposal decides on its own card: Edit & apply opens its form there.
  const proposal = proposalOf(fresh, proposals.data?.data);
  if (proposal) {
    return (
      <FactoryViewerProvider viewer={data?.viewer}>
        <ProposalCard
          factoryId={item.factory.id}
          proposal={proposal}
          snapshot={data ?? null}
          factoryName={factoryName}
          onDecided={() => onAnswered(fresh)}
        />
      </FactoryViewerProvider>
    );
  }
  return <DecisionCard decision={fresh} factoryName={factoryName} onAnswered={onAnswered} />;
}

interface Group {
  factory: OpenDecisionItem["factory"];
  items: OpenDecisionItem[];
}

function groupByFactory(items: OpenDecisionItem[]): Group[] {
  const groups = new Map<string, Group>();
  for (const item of items) {
    const group = groups.get(item.factory.id) ?? { factory: item.factory, items: [] };
    group.items.push(item);
    groups.set(item.factory.id, group);
  }
  for (const group of groups.values()) {
    const open = orderOpenDecisions(group.items.map((i) => i.decision));
    const rank = (i: OpenDecisionItem) => {
      const at = open.findIndex((d) => d.id === i.decision.id);
      return at === -1 ? Number.MAX_SAFE_INTEGER : at;
    };
    group.items.sort((a, b) => rank(a) - rank(b));
  }
  return [...groups.values()];
}

export function InboxFactoryDecisions({
  items,
  error,
  onRetry,
}: {
  /** Open decisions as `GET /v1/factory/decisions?status=open` lists them. */
  items: OpenDecisionItem[];
  error?: Error | null;
  onRetry?: () => void;
}) {
  const [mine, setMine] = useState<Record<string, OpenDecisionItem>>({});
  const incidents = useAssignedIncidents();
  const kept = Object.values(mine).filter(
    (m) => !items.some((i) => i.decision.id === m.decision.id),
  );
  const all = [...items, ...kept];
  if (all.length === 0 && incidents.length === 0 && !error) return null;
  const remember = (item: OpenDecisionItem) => (decision: DecisionView) =>
    setMine((prev) => ({ ...prev, [decision.id]: { ...item, decision } }));
  const assigned = all.filter((i) => i.mine === true);
  const rest = all.filter((i) => i.mine !== true);
  return (
    <section
      data-testid="inbox-factory-decisions"
      aria-label="Factory decisions"
      className="mb-6 flex flex-col gap-3"
    >
      {(assigned.length > 0 || incidents.length > 0) && (
        <div data-testid="inbox-assigned-to-you" className="flex flex-col gap-2">
          <h2 className="flex items-center gap-1.5 text-sm font-medium text-foreground">
            <UserRoundCheckIcon aria-hidden className="size-4 text-warning" />
            Assigned to you
            <span className="font-normal text-muted-foreground tabular-nums">
              {` · ${assigned.filter((i) => items.includes(i)).length + incidents.length} open`}
            </span>
          </h2>
          <InboxAssignedIncidents incidents={incidents} nowS={Math.floor(Date.now() / 1000)} />
          {assigned.map((item) => (
            <InboxDecision
              key={item.decision.id}
              item={item}
              factoryName={item.factory.name}
              answered={item.decision.id in mine}
              onAnswered={remember(item)}
            />
          ))}
        </div>
      )}
      {(rest.length > 0 || error) && (
        <h2 className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground">
          Factory decisions
          <span className="tabular-nums">{` · ${rest.filter((i) => items.includes(i)).length} open`}</span>
        </h2>
      )}
      {error && (
        <p
          data-testid="inbox-factory-decisions-error"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <TriangleAlertIcon aria-hidden className="size-3.5 text-warning" />
          Couldn’t load factory decisions ({error.message}).
          {onRetry && (
            <Button variant="ghost" size="xs" onClick={onRetry} componentId="inbox.factory.retry">
              Retry
            </Button>
          )}
        </p>
      )}
      {groupByFactory(rest).map((group) => (
        <div
          key={group.factory.id}
          data-testid={`inbox-factory-${group.factory.id}`}
          className="flex flex-col gap-2"
        >
          <div className="flex min-w-0 items-center gap-2">
            <FactoryIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 truncate text-ui font-medium">{group.factory.name}</span>
            <span className="shrink-0 text-sm text-muted-foreground">
              {plural(group.items.filter((i) => items.includes(i)).length, "decision")}
            </span>
            <Button asChild variant="ghost" size="sm" className="ml-auto text-sm">
              <Link
                to={`/factory/${group.factory.id}`}
                componentId="inbox.factory.open_cockpit"
                data-testid={`inbox-open-cockpit-${group.factory.id}`}
              >
                Open cockpit
                <ArrowRightIcon className="ml-1 size-3.5" />
              </Link>
            </Button>
          </div>
          {group.items.map((item) => (
            <InboxDecision
              key={item.decision.id}
              item={item}
              answered={item.decision.id in mine}
              onAnswered={remember(item)}
            />
          ))}
        </div>
      ))}
    </section>
  );
}
