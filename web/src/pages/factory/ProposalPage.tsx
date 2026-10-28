/**
 * One change's page (`/factory/:factoryId/improvements/:ref`). A tuning,
 * rules or code change shows its card with the scorecard open, what it
 * changed in the definition, its decision and where it is discussed; a
 * projects proposal shows the Room's proposal card (Apply, Edit & apply,
 * Reject, Undo). A finding ref (`F-7`) opens the finding's page.
 */

import type { ReactNode } from "react";
import { ChevronLeftIcon, FileQuestionIcon } from "lucide-react";
import { Navigate } from "react-router-dom";
import { ProposalCard } from "@/components/factory/cards/ProposalCard";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { formatElapsed } from "@/components/factory/factoryFormat";
import {
  alertPageHref,
  decisionsPageHref,
  improvementsPageHref,
} from "@/components/factory/factoryRoutes";
import { LearnProposalCard } from "@/components/factory/improvements/LearnProposalCard";
import {
  useFactoryDefinition,
  useFactoryProposal,
} from "@/components/factory/insights/learnQueries";
import { EmptyState, SectionCard } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { ApiError } from "@/lib/sessionsApi";
import { Link, useParams } from "@/lib/routing";

function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ProposalPage() {
  const { ref = "" } = useParams<{ ref: string }>();
  const { snapshot, nowS, clockOffsetS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const finding = /^F-\d+$/i.test(ref);
  const proposal = useFactoryProposal(finding ? "" : fid, ref);
  const definition = useFactoryDefinition(fid);
  if (finding) return <Navigate to={alertPageHref(fid, ref.toUpperCase())} replace />;
  const back = (
    <Link
      to={improvementsPageHref(fid)}
      componentId="factory.proposal.back"
      className="inline-flex items-center gap-1 self-start text-sm text-muted-foreground hover:text-foreground"
    >
      <ChevronLeftIcon aria-hidden className="size-4" />
      Improvements
    </Link>
  );
  if (proposal.isLoading) {
    return (
      <div data-testid="proposal-page-loading" aria-busy="true" className="flex flex-col gap-3">
        {back}
        <div className="h-48 animate-pulse rounded-xl bg-muted/70" />
      </div>
    );
  }
  if (proposal.error || !proposal.data) {
    const missing = proposal.error instanceof ApiError && proposal.error.status === 404;
    return (
      <div className="flex flex-col gap-3">
        {back}
        {missing ? (
          <EmptyState
            testId="proposal-page-missing"
            icon={<FileQuestionIcon aria-hidden className="size-6 text-muted-foreground/50" />}
            title={`No change ${ref} in this factory`}
            body="It may belong to another factory, or the link is mistyped."
          />
        ) : (
          <FactoryLoadError
            error={proposal.error ?? new Error("not found")}
            updatedAt={0}
            onRetry={() => void proposal.refetch()}
            what={ref}
            testId="proposal-page-error"
          />
        )}
      </div>
    );
  }
  const p = proposal.data.proposal;
  const result = (p.result ?? {}) as Record<string, unknown>;
  return (
    <div data-testid="factory-proposal-page" className="flex min-w-0 flex-col gap-3">
      {back}
      <div className="flex min-w-0 flex-wrap items-baseline gap-2">
        <h1 className="font-mono text-base font-semibold">{p.ref}</h1>
        <span className="min-w-0 truncate text-base">{p.title}</span>
      </div>
      {p.kind === "projects" ? (
        <ProposalCard
          factoryId={fid}
          proposal={p}
          snapshot={snapshot}
          clockOffsetS={clockOffsetS}
        />
      ) : (
        <LearnProposalCard
          factoryId={fid}
          proposal={p}
          snapshot={snapshot}
          nowS={nowS}
          tier2={definition.data?.tier2}
          factoryName={snapshot.factory.name}
          expanded
        />
      )}
      <SectionCard title="Record" testId="proposal-record">
        <Facts
          items={[
            [
              "Filed",
              `${formatElapsed(nowS - p.created_at)} ago by ${p.author_title ?? p.author} (${p.author_kind})`,
            ],
            ["Approval", `${p.approval}${p.class ? ` · class ${p.class}` : ""}`],
            ...(p.decision_ref
              ? ([
                  [
                    "Decision",
                    <Link
                      key="d"
                      to={decisionsPageHref(fid, { decision: p.decision_ref })}
                      componentId="factory.proposal.decision"
                      className="font-mono hover:underline"
                    >
                      {p.decision_ref}
                    </Link>,
                  ],
                ] as [string, ReactNode][])
              : []),
            ...(p.decided_by
              ? ([
                  [
                    "Decided",
                    `${p.decided_by}${p.decided_at ? ` · ${formatElapsed(nowS - p.decided_at)} ago` : ""}`,
                  ],
                ] as [string, ReactNode][])
              : []),
            ...(p.def_version_to != null
              ? ([["Definition", `v${p.def_version_from ?? "?"} → v${p.def_version_to}`]] as [
                  string,
                  ReactNode,
                ][])
              : []),
            ...(p.trial_until != null
              ? ([["Trial until", new Date(p.trial_until * 1000).toLocaleString()]] as [
                  string,
                  ReactNode,
                ][])
              : []),
            ...(p.reverted_at != null
              ? ([
                  [
                    "Reverted",
                    `${p.reverted_by ?? "server"} · ${formatElapsed(nowS - p.reverted_at)} ago${typeof result.revert_reason === "string" ? ` · ${result.revert_reason}` : ""}`,
                  ],
                ] as [string, ReactNode][])
              : []),
            ...(typeof result.note === "string" && result.note
              ? ([["Note", result.note]] as [string, ReactNode][])
              : []),
          ]}
        />
      </SectionCard>
    </div>
  );
}
