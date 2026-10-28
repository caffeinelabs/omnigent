/**
 * Improvements (`/factory/:factoryId/improvements?tab=`): the factory
 * changing itself under supervision. On trial (scorecards, Keep now,
 * Revert), Proposed (Approve trial, Edit, Reject; code changes recorded with
 * "Copy as issue" while Tier 2 is off), History (every C-n), Rules,
 * Definition (versions, diff, revert, bundle drift) and Findings ("Is this
 * right?", Propose change). The Tier-2 status line and a link to the
 * `#improvements` discussion sit under the tabs.
 */

import { type ReactElement, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { FlaskConicalIcon, InfoIcon, LightbulbIcon, MessagesSquareIcon } from "lucide-react";
import {
  STATE_LABEL,
  STATE_TONE,
  proposalState,
  proposerLine,
} from "@/components/factory/cards/proposalModel";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { TONE_CHIP, formatElapsed } from "@/components/factory/factoryFormat";
import {
  IMPROVEMENT_TABS,
  type ImprovementTab,
  improvementPageHref,
  improvementsPageHref,
  roomPageHref,
  settingsPageHref,
} from "@/components/factory/factoryRoutes";
import { useFactoryIssues } from "@/components/factory/agents/opsQueries";
import { DefinitionPanel } from "@/components/factory/improvements/DefinitionPanel";
import { FINDINGS_FILTERS, FindingsTab } from "@/components/factory/improvements/FindingsTab";
import {
  improvementCounts,
  onTrial,
  proposed,
  tier2Plain,
} from "@/components/factory/improvements/improvementsModel";
import { LearnProposalCard } from "@/components/factory/improvements/LearnProposalCard";
import {
  PROPOSE_REASON,
  ProposeChangeDialog,
  type ProposeSource,
} from "@/components/factory/improvements/ProposeChangeDialog";
import {
  useFactoryDefinition,
  useLearnProposals,
} from "@/components/factory/insights/learnQueries";
import { EmptyState, SectionCard } from "@/components/factory/project/projectParts";
import { RulesTable } from "@/components/factory/settings/RulesEditor";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { ReasonGated, useViewerCan } from "@/components/factory/shell/viewerCan";
import { Button } from "@/components/ui/button";
import type { IssueView, ProposalView, Tier2StatusView } from "@/lib/factoryApi";
import { Link, useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

const TAB_LABEL: Record<ImprovementTab, string> = {
  trial: "On trial",
  proposed: "Proposed",
  history: "History",
  rules: "Rules",
  definition: "Definition",
  findings: "Findings",
};

/** "Tier 2: off ⓘ" with the missing prerequisite in plain words (the raw one on hover). */
export function Tier2Line({ tier2 }: { tier2: Tier2StatusView | null | undefined }) {
  if (!tier2) return null;
  const plain = tier2Plain(tier2.reason);
  return (
    <span
      data-testid="improvements-tier2"
      data-enabled={tier2.enabled ? "true" : "false"}
      title={
        tier2.enabled
          ? "The Factory Smith lane may open code PRs to omnigent; humans review and merge"
          : `Tier 2 disabled: ${tier2.reason ?? "prerequisites missing"}. Code proposals are recorded with their evidence and never run.`
      }
      className="inline-flex items-center gap-1 text-sm text-muted-foreground"
    >
      Tier 2:{" "}
      <span className={tier2.enabled ? "text-success" : undefined}>
        {tier2.enabled ? "on" : "off"}
      </span>
      <InfoIcon aria-hidden className="size-3.5" />
      {!tier2.enabled && plain && (
        <span className="max-w-[22rem] truncate max-md:hidden">· {plain}</span>
      )}
    </span>
  );
}

function HistoryTable({
  factoryId,
  proposals,
  nowS,
  snapshot,
}: {
  factoryId: string;
  proposals: readonly ProposalView[];
  nowS: number;
  snapshot: Parameters<typeof proposerLine>[1];
}) {
  if (proposals.length === 0) {
    return (
      <EmptyState
        testId="history-empty"
        title="No changes yet"
        body="Every definition change lands here as C-n: an agent's proposal, a person's edit in Settings, a revert."
      />
    );
  }
  return (
    <>
      <ul
        data-testid="history-cards"
        className="flex min-w-0 flex-col divide-y divide-border rounded-xl border border-border bg-card sm:hidden"
      >
        {proposals.map((p) => {
          const state = proposalState(p);
          return (
            <li key={p.id} className="flex min-w-0 flex-col gap-0.5 px-3 py-2 text-sm">
              <span className="flex min-w-0 items-center gap-2">
                <Link
                  to={improvementPageHref(factoryId, p.ref)}
                  componentId="factory.history.ref"
                  className="font-mono font-medium hover:underline"
                >
                  {p.ref}
                </Link>
                <span className="min-w-0 flex-1 truncate">{p.title}</span>
                <span
                  className={cn(
                    "shrink-0 rounded-md px-1.5 text-xs leading-5",
                    TONE_CHIP[STATE_TONE[state]],
                  )}
                >
                  {STATE_LABEL[state]}
                </span>
              </span>
              <span className="text-xs text-muted-foreground">
                {p.kind} · {proposerLine(p, snapshot)} · {formatElapsed(nowS - p.created_at)} ago
              </span>
            </li>
          );
        })}
      </ul>
      <div className="min-w-0 overflow-x-auto rounded-xl border border-border bg-card max-sm:hidden">
        <table data-testid="history-table" className="w-full min-w-[44rem] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted-foreground">
              <th className="px-3 py-2 font-medium">Change</th>
              <th className="px-3 py-2 font-medium">Kind</th>
              <th className="px-3 py-2 font-medium">What</th>
              <th className="px-3 py-2 font-medium">By</th>
              <th className="px-3 py-2 font-medium">Version</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 text-right font-medium">When</th>
            </tr>
          </thead>
          <tbody>
            {proposals.map((p) => {
              const state = proposalState(p);
              return (
                <tr
                  key={p.id}
                  data-testid={`history-${p.ref}`}
                  className="border-b border-border/60 last:border-0"
                >
                  <td className="px-3 py-1.5">
                    <Link
                      to={improvementPageHref(factoryId, p.ref)}
                      componentId="factory.history.ref"
                      className="font-mono font-medium hover:underline"
                    >
                      {p.ref}
                    </Link>
                  </td>
                  <td className="px-3 py-1.5 text-muted-foreground">
                    {p.kind}
                    {p.class ? ` · ${p.class === "human" ? "human" : p.class}` : ""}
                  </td>
                  <td className="max-w-[22rem] truncate px-3 py-1.5" title={p.title}>
                    {p.title}
                  </td>
                  <td className="max-w-[12rem] truncate px-3 py-1.5 text-muted-foreground">
                    {proposerLine(p, snapshot).replace(/^by /, "")}
                  </td>
                  <td className="px-3 py-1.5 font-mono text-xs text-muted-foreground">
                    {p.def_version_from != null && p.def_version_to != null
                      ? `v${p.def_version_from}→v${p.def_version_to}`
                      : "—"}
                  </td>
                  <td className="px-3 py-1.5">
                    <span
                      className={cn(
                        "rounded-md px-1.5 py-0.5 text-xs",
                        TONE_CHIP[STATE_TONE[state]],
                      )}
                    >
                      {STATE_LABEL[state]}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-right text-muted-foreground tabular-nums">
                    {formatElapsed(nowS - p.created_at)} ago
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

function CardList({
  testId,
  proposals,
  empty,
  render,
}: {
  testId: string;
  proposals: readonly ProposalView[];
  empty: ReactElement;
  render: (p: ProposalView) => ReactNode;
}) {
  if (proposals.length === 0) return empty;
  return (
    <div data-testid={testId} className="flex min-w-0 flex-col gap-2">
      {proposals.map(render)}
    </div>
  );
}

export function ImprovementsPage() {
  const { snapshot, nowS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const { can } = useViewerCan();
  const [params, setParams] = useSearchParams();
  const tab = (IMPROVEMENT_TABS as readonly string[]).includes(params.get("tab") ?? "")
    ? (params.get("tab") as ImprovementTab)
    : "trial";
  const proposals = useLearnProposals(fid);
  const definition = useFactoryDefinition(fid);
  const findings = useFactoryIssues(fid, FINDINGS_FILTERS);
  const [proposing, setProposing] = useState<ProposeSource | null>(null);
  const all = useMemo(() => proposals.data ?? [], [proposals.data]);
  const counts = improvementCounts(all);
  const rules = definition.data?.effective.rules ?? [];
  const tier2 = definition.data?.tier2 ?? null;
  const name = snapshot.factory.name;
  const improvementsThread = "improvements";

  const card = (p: ProposalView) => (
    <LearnProposalCard
      key={p.id}
      factoryId={fid}
      proposal={p}
      snapshot={snapshot}
      nowS={nowS}
      tier2={tier2}
      factoryName={name}
    />
  );
  const proposeReason = can.settings || can.apply_projects ? null : PROPOSE_REASON;
  const proposeFor = (issue: IssueView) => (
    <ReasonGated reason={proposeReason} testId={`finding-propose-${issue.ref ?? issue.id}`}>
      <Button
        size="xs"
        variant="outline"
        onClick={() => setProposing({ evidence: [issue.ref ?? issue.id], why: issue.summary })}
        data-testid={`finding-propose-${issue.ref ?? issue.id}`}
        componentId="factory.improvements.propose"
      >
        <LightbulbIcon />
        Propose change
      </Button>
    </ReasonGated>
  );

  // Counts show only once their data has loaded: a 0 while loading would be wrong.
  const loaded = proposals.data != null;
  const count = (key: ImprovementTab): number | null => {
    switch (key) {
      case "trial":
        return loaded ? counts.trial : null;
      case "proposed":
        return loaded ? counts.proposed : null;
      case "history":
        return loaded ? counts.history : null;
      case "rules":
        return definition.data ? rules.length : null;
      case "findings":
        return findings.data ? findings.data.data.length : null;
      default:
        return null;
    }
  };

  const loading = proposals.isLoading && !proposals.data;
  const failed = proposals.error && !proposals.data;
  const findingsCount = count("findings");
  const rulesCount = count("rules");
  const strip = useRef<HTMLDivElement | null>(null);
  // Keep the selected tab in view on a narrow strip (only the strip scrolls).
  useEffect(() => {
    const box = strip.current;
    const active = box?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!box || !active) return;
    const outer = box.getBoundingClientRect();
    const inner = active.getBoundingClientRect();
    if (inner.left < outer.left) box.scrollLeft -= outer.left - inner.left + 16;
    else if (inner.right > outer.right) box.scrollLeft += inner.right - outer.right + 16;
    // Counts arriving later widen the tabs: keep the selected one in view then too.
  }, [tab, loaded, findingsCount, rulesCount]);

  return (
    <div data-testid="factory-improvements-page" className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-base font-semibold">Improvements</h1>
        {loaded && (
          <span
            data-testid="improvements-counts"
            className="text-sm text-muted-foreground tabular-nums"
          >
            On trial {counts.trial} · Proposed {counts.proposed} · Kept {counts.kept} · Reverted{" "}
            {counts.reverted}
          </span>
        )}
        <span className="ml-auto">
          <Tier2Line tier2={tier2} />
        </span>
      </div>
      <div className="flex min-w-0 items-center gap-3 border-b border-border">
        <div
          ref={strip}
          role="tablist"
          aria-label="Improvements"
          className="-mb-px flex min-w-0 flex-1 items-center gap-3 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {IMPROVEMENT_TABS.map((key) => {
            const n = count(key);
            return (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                data-testid={`improvements-tab-${key}`}
                onClick={() =>
                  setParams(
                    (prev) => {
                      const next = new URLSearchParams(prev);
                      if (key === "trial") next.delete("tab");
                      else next.set("tab", key);
                      return next;
                    },
                    { replace: true },
                  )
                }
                className={cn(
                  "inline-flex h-8 shrink-0 cursor-pointer items-center gap-1 border-b-2 px-0.5 text-sm font-medium whitespace-nowrap focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
                  tab === key
                    ? "border-foreground text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                {key === "trial" && <FlaskConicalIcon aria-hidden className="size-3.5" />}
                {TAB_LABEL[key]}
                {n != null && (
                  <span className="text-xs font-normal text-muted-foreground tabular-nums">
                    {n}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <Link
          to={roomPageHref(fid, improvementsThread)}
          componentId="factory.improvements.thread"
          data-testid="improvements-thread"
          aria-label="Discuss in #improvements"
          title="Discuss in #improvements"
          className="inline-flex h-8 shrink-0 items-center gap-1 text-sm whitespace-nowrap text-muted-foreground hover:text-foreground"
        >
          <MessagesSquareIcon aria-hidden className="size-3.5" />
          <span className="max-sm:hidden">discuss in #improvements</span>
        </Link>
      </div>
      {tab === "definition" ? (
        <DefinitionPanel factoryId={fid} nowS={nowS} />
      ) : tab === "findings" ? (
        <FindingsTab factoryId={fid} nowS={nowS} proposeFor={proposeFor} />
      ) : tab === "rules" ? (
        <SectionCard
          title="Rules"
          meta="advisory text each role reads with its cards and briefs"
          testId="improvements-rules"
          action={
            <Link
              to={settingsPageHref(fid, "rules")}
              componentId="factory.improvements.edit_rules"
              className="text-sm text-muted-foreground hover:text-foreground hover:underline"
            >
              Edit in Settings →
            </Link>
          }
        >
          <RulesTable factoryId={fid} rules={rules} />
        </SectionCard>
      ) : loading ? (
        <div aria-busy="true" data-testid="improvements-loading" className="flex flex-col gap-2">
          {[0, 1].map((i) => (
            <div key={i} className="h-32 animate-pulse rounded-xl bg-muted/70" />
          ))}
        </div>
      ) : failed ? (
        <FactoryLoadError
          error={proposals.error!}
          updatedAt={0}
          onRetry={() => void proposals.refetch()}
          what="the proposals"
          testId="improvements-error"
        />
      ) : tab === "trial" ? (
        <CardList
          testId="improvements-trial"
          proposals={onTrial(all)}
          render={card}
          empty={
            <EmptyState
              testId="improvements-trial-empty"
              icon={<FlaskConicalIcon aria-hidden className="size-6 text-muted-foreground/50" />}
              title="Nothing on trial"
              body="An approved change runs as a trial (48 h by default): guard metrics must hold, and n must reach 3 merged PRs before it is kept."
            />
          }
        />
      ) : tab === "proposed" ? (
        <CardList
          testId="improvements-proposed"
          proposals={proposed(all)}
          render={card}
          empty={
            <EmptyState
              testId="improvements-proposed-empty"
              title="No proposals waiting"
              body="Sentinel files at most two a day from its retro; cheaper or safer changes go on trial once Atlas co-signs, the rest wait for an owner."
            />
          }
        />
      ) : (
        <HistoryTable factoryId={fid} proposals={all} nowS={nowS} snapshot={snapshot} />
      )}
      {proposing && (
        <ProposeChangeDialog
          factoryId={fid}
          open={proposing != null}
          onOpenChange={(open) => !open && setProposing(null)}
          source={proposing}
        />
      )}
      <p className="text-xs text-muted-foreground">
        Every change is versioned and reverts in one click.{" "}
        <Link
          to={improvementsPageHref(fid, "definition")}
          componentId="factory.improvements.definition_link"
          className="underline-offset-2 hover:underline"
        >
          Definition history
        </Link>
      </p>
    </div>
  );
}
