/**
 * Decisions (`/factory/:id/decisions`): everything the factory asked a
 * human. Open ones come assigned-to-me first, then my role's, then the rest,
 * each answerable in place (a proposal as its card, a loop with its
 * remedies, the rest as decision cards with Claim and Assign). Answered and
 * expired ones are the history. Filters: whom, source, project.
 */

import { useMemo, useState } from "react";
import { CircleCheckIcon, ListChecksIcon } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DecisionEmbed } from "@/components/factory/cards/DecisionEmbed";
import { ProposalCard } from "@/components/factory/cards/ProposalCard";
import { ageText } from "@/components/factory/board/boardModel";
import { DecisionReceipt, answerLabel } from "@/components/factory/decisions/DecisionCard";
import {
  type AssignedFilter,
  type DecisionFilters,
  NO_FILTERS,
  SOURCE_LABEL,
  groupOpen,
  historyOf,
  matchesFilters,
  proposalOf,
} from "@/components/factory/decisions/decisionsPageModel";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useDecisionsByStatus, useProposals } from "@/components/factory/room/roomQueries";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import type { DecisionSource, DecisionView } from "@/lib/factoryApi";
import { useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

type View = "open" | "answered" | "closed";
const ALL = "all";
const CLOSED_STATUSES = ["expired", "withdrawn"];

function Group({
  title,
  testId,
  decisions,
  children,
}: {
  title: string;
  testId: string;
  decisions: readonly DecisionView[];
  children: (decision: DecisionView) => React.ReactNode;
}) {
  if (decisions.length === 0) return null;
  return (
    <section data-testid={testId} aria-label={title} className="flex min-w-0 flex-col gap-2">
      <h3 className="text-sm font-medium text-muted-foreground">
        {title} <span className="tabular-nums">· {decisions.length}</span>
      </h3>
      {/* One column: cards of very different heights side by side leave holes. */}
      <ul className="flex max-w-3xl min-w-0 flex-col gap-3">
        {decisions.map((d) => (
          <li key={d.id} className="min-w-0">
            {children(d)}
          </li>
        ))}
      </ul>
    </section>
  );
}

function HistoryRow({ decision, nowS }: { decision: DecisionView; nowS: number }) {
  const answered = decision.status === "answered";
  return (
    <li
      data-testid={`decision-history-${decision.ref}`}
      data-status={decision.status}
      className="grid min-w-0 grid-cols-[3.5rem_minmax(0,1fr)] gap-x-3 gap-y-0.5 border-b border-border py-2 text-sm last:border-b-0 md:grid-cols-[3.5rem_minmax(0,1fr)_minmax(0,16rem)_6rem]"
    >
      <span className="font-mono text-muted-foreground">{decision.ref}</span>
      <span className="min-w-0">
        <span className="line-clamp-2 break-words" title={decision.question}>
          {decision.question}
        </span>
        <span className="text-xs text-muted-foreground">
          {SOURCE_LABEL[decision.source]}
          {decision.project_slug ? ` · ${decision.project_slug}` : ""}
        </span>
      </span>
      <span className="col-start-2 min-w-0 md:col-start-auto">
        {answered ? (
          <>
            <span className="break-words">“{answerLabel(decision)}”</span>
            {decision.answered_by && (
              <span className="text-muted-foreground"> · {decision.answered_by.split("@")[0]}</span>
            )}
            <DecisionReceipt decision={decision} />
          </>
        ) : (
          <span className="text-muted-foreground">{decision.status}</span>
        )}
      </span>
      <span className="col-start-2 text-xs text-muted-foreground tabular-nums md:col-start-auto md:text-right">
        {ageText(nowS - (decision.answered_at ?? decision.updated_at))}
      </span>
    </li>
  );
}

export function DecisionsPage() {
  const { snapshot, nowS, clockOffsetS } = useFactoryShell();
  const [params] = useSearchParams();
  const highlight = params.get("decision");
  const fid = snapshot.factory.id;
  const viewer = snapshot.viewer ?? null;
  const [view, setView] = useState<View>("open");
  const [filters, setFilters] = useState<DecisionFilters>(NO_FILTERS);
  const proposals = useProposals(fid);
  const answered = useDecisionsByStatus(["answered"], view === "answered");
  const closed = useDecisionsByStatus(["expired", "withdrawn"], view === "closed");
  const open = snapshot.decisions.filter((d) => d.status === "open");
  const groups = groupOpen(
    open.filter((d) => matchesFilters(d, filters, viewer)),
    viewer,
  );
  const history = useMemo(() => {
    if (view === "open") return [];
    const statuses = view === "answered" ? ["answered"] : CLOSED_STATUSES;
    const listed = (view === "answered" ? answered : closed).data?.data;
    return historyOf(fid, statuses, listed, snapshot.decisions).filter((d) =>
      matchesFilters(d, filters, viewer),
    );
  }, [view, answered, closed, fid, snapshot.decisions, filters, viewer]);
  const projects = [
    ...new Set(snapshot.decisions.map((d) => d.project_slug).filter(Boolean)),
  ] as string[];
  const sources = [...new Set(snapshot.decisions.map((d) => d.source))];
  const historyQuery = view === "answered" ? answered : closed;
  const set = (patch: Partial<DecisionFilters>) => setFilters((prev) => ({ ...prev, ...patch }));

  const card = (decision: DecisionView) => {
    const proposal = proposalOf(decision, proposals.data?.data);
    if (proposal) {
      return (
        <ProposalCard
          factoryId={fid}
          proposal={proposal}
          snapshot={snapshot}
          clockOffsetS={clockOffsetS}
        />
      );
    }
    return (
      <DecisionEmbed
        factoryId={fid}
        decision={decision}
        clockOffsetS={clockOffsetS}
        highlighted={highlight === decision.ref || highlight === decision.id}
      />
    );
  };

  const total = groups.mine.length + groups.role.length + groups.others.length;
  return (
    <div
      data-testid="factory-decisions-page"
      data-view={view}
      className="flex min-w-0 flex-col gap-4"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
        <h2 className="inline-flex items-center gap-2 text-base font-semibold">
          <ListChecksIcon aria-hidden className="size-4 text-muted-foreground" />
          Decisions
        </h2>
        <Tabs
          value={view}
          onValueChange={(v) => setView(v as View)}
          componentId="factory.decisions.view"
        >
          <TabsList variant="line" className="h-8 gap-3">
            <TabsTrigger
              value="open"
              data-testid="decisions-view-open"
              className="flex-none px-0.5"
            >
              Open <span className="tabular-nums text-muted-foreground">{open.length}</span>
            </TabsTrigger>
            <TabsTrigger
              value="answered"
              data-testid="decisions-view-answered"
              className="flex-none px-0.5"
            >
              Answered
            </TabsTrigger>
            <TabsTrigger
              value="closed"
              data-testid="decisions-view-closed"
              className="flex-none px-0.5"
            >
              Expired
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="flex min-w-0 flex-wrap items-center gap-2 md:ml-auto">
          <Select
            value={filters.assigned}
            onValueChange={(v) => set({ assigned: v as AssignedFilter })}
          >
            <SelectTrigger
              size="sm"
              aria-label="Assigned"
              data-testid="decisions-filter-assigned"
              className="min-w-32"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Anyone</SelectItem>
              <SelectItem value="me">Assigned to me</SelectItem>
              <SelectItem value="role">My role</SelectItem>
              <SelectItem value="operators">Operators</SelectItem>
              <SelectItem value="owners">Owners</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={filters.source}
            onValueChange={(v) => set({ source: v as DecisionSource | "all" })}
          >
            <SelectTrigger
              size="sm"
              aria-label="Source"
              data-testid="decisions-filter-source"
              className="min-w-32"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Every source</SelectItem>
              {sources.map((s) => (
                <SelectItem key={s} value={s}>
                  {SOURCE_LABEL[s]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={filters.project} onValueChange={(v) => set({ project: v })}>
            <SelectTrigger
              size="sm"
              aria-label="Project"
              data-testid="decisions-filter-project"
              className="min-w-32"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All projects</SelectItem>
              {projects.map((p) => (
                <SelectItem key={p} value={p}>
                  {p}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {view === "open" && (
        <>
          {total === 0 && (
            <EmptyState
              testId="decisions-page-empty"
              icon={<CircleCheckIcon aria-hidden className="size-6 text-success" />}
              title={open.length === 0 ? "Nothing needs a human" : "Nothing matches these filters"}
              body={
                open.length === 0
                  ? "Agents ask here instead of in chat; answered ones are under Answered."
                  : `${open.length} open decision${open.length === 1 ? "" : "s"} hidden by the filters.`
              }
            />
          )}
          <Group title="Assigned to me" testId="decisions-mine" decisions={groups.mine}>
            {card}
          </Group>
          <Group title="For my role" testId="decisions-role" decisions={groups.role}>
            {card}
          </Group>
          <Group title="Others" testId="decisions-others" decisions={groups.others}>
            {card}
          </Group>
        </>
      )}

      {view !== "open" && (
        <section
          aria-label={view === "answered" ? "Answered" : "Expired and withdrawn"}
          className={cn(
            "flex min-w-0 flex-col rounded-xl border border-border bg-card px-3",
            history.length === 0 && "border-dashed",
          )}
        >
          {historyQuery.error instanceof Error && !historyQuery.data && (
            <div className="py-3">
              <FactoryLoadError
                error={historyQuery.error}
                updatedAt={0}
                onRetry={() => void historyQuery.refetch()}
                what="the history"
                className="mb-0"
              />
            </div>
          )}
          {historyQuery.isLoading && (
            <div aria-busy="true" className="flex flex-col gap-2 py-3">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-8 animate-pulse rounded bg-muted" />
              ))}
            </div>
          )}
          {!historyQuery.isLoading && history.length === 0 && (
            <p
              data-testid="decisions-history-empty"
              className="py-6 text-center text-sm text-muted-foreground"
            >
              {view === "answered" ? "No answered decisions yet." : "Nothing expired or withdrawn."}
            </p>
          )}
          <ul data-testid="decisions-history" className="flex min-w-0 flex-col">
            {history.map((d) => (
              <HistoryRow key={d.id} decision={d} nowS={nowS} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
