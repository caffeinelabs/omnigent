/**
 * Steer (`/steer/<section>`): the one place to change how the factory works.
 * Three columns on desktop: the outline (each section with its current value
 * in one line, what waits for an owner and what is staged), the section, and
 * the always-on change column (the staged change for the next definition
 * version with its effect, the re-rank of opportunities, who can apply it and
 * whether it may run as a trial; then agents' proposals and running trials).
 * Every change, in any section, is one definition version with Undo and
 * restore. Sources and the rubric hide while discovery is off; Strategy and
 * the product profile stay. On a phone the outline is a chip row, the change
 * column follows the section, and a staged bar sits above the tab bar.
 * One change (`?peek=C-n`) opens in a side panel.
 */

import { SlidersHorizontalIcon } from "lucide-react";
import { useMemo, useRef } from "react";
import { Navigate } from "react-router-dom";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import {
  STEER_SECTIONS,
  type SteerSection,
  steerPageHref,
} from "@/components/factory/factoryRoutes";
import { discoveryRuns } from "@/components/factory/home/homeModel";
import { useFactoryAsks } from "@/components/factory/home/homeQueries";
import {
  useDefinitionVersions,
  useFactoryDefinition,
  useLearnProposals,
} from "@/components/factory/insights/learnQueries";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { useViewerCan } from "@/components/factory/shell/viewerCan";
import { AlertsAndRules } from "@/components/factory/steer/AlertsAndRules";
import { Budgets } from "@/components/factory/steer/Budgets";
import { Cadence } from "@/components/factory/steer/Cadence";
import { ChangeColumn } from "@/components/factory/steer/ChangeColumn";
import { Gates } from "@/components/factory/steer/Gates";
import { Overview } from "@/components/factory/steer/Overview";
import { ProductProfile } from "@/components/factory/steer/ProductProfile";
import { RolesAndBriefs } from "@/components/factory/steer/RolesAndBriefs";
import { Rubric } from "@/components/factory/steer/Rubric";
import { Sources } from "@/components/factory/steer/Sources";
import { SteerProvider, useSteer } from "@/components/factory/steer/SteerContext";
import {
  type OutlineFacts,
  outlineValue,
  stagedCount,
  stagedSections,
  visibleGroups,
  visibleSections,
} from "@/components/factory/steer/steerModel";
import { SteerOutline } from "@/components/factory/steer/SteerOutline";
import { useSteerPreview } from "@/components/factory/steer/steerQueries";
import { Strategy } from "@/components/factory/steer/Strategy";
import { Templates } from "@/components/factory/steer/Templates";
import { Versions } from "@/components/factory/steer/Versions";
import { Peek } from "@/components/factory/ui/Peek";
import { StagedBar } from "@/components/factory/ui/StagedBar";
import type { DefinitionView, ProposalView } from "@/lib/factoryApi";
import type { Ask } from "@/lib/factoryAsksApi";
import { useParams, useSearchParams } from "@/lib/routing";
import { ProposalPage } from "./ProposalPage";

function Section({
  section,
  asks,
  proposals,
  versions,
  versionsLoading,
}: {
  section: SteerSection;
  asks: readonly Ask[] | null | undefined;
  proposals: readonly ProposalView[] | undefined;
  versions: readonly DefinitionView[] | undefined;
  versionsLoading: boolean;
}) {
  switch (section) {
    case "overview":
      return <Overview asks={asks} versions={versions} />;
    case "cadence":
      return <Cadence />;
    case "strategy":
      return <Strategy proposals={proposals} />;
    case "product":
      return <ProductProfile proposals={proposals} />;
    case "sources":
      return <Sources proposals={proposals} />;
    case "rubric":
      return <Rubric />;
    case "roles":
      return <RolesAndBriefs />;
    case "templates":
      return <Templates />;
    case "gates":
      return <Gates />;
    case "budgets":
      return <Budgets />;
    case "alerts":
      return <AlertsAndRules />;
    case "versions":
      return <Versions versions={versions} proposals={proposals} loading={versionsLoading} />;
  }
}

function SteerBody({ active }: { active: SteerSection }) {
  const steer = useSteer();
  const { factoryId, definition, discoveryOn, staging } = steer;
  const proposals = useLearnProposals(factoryId);
  const versions = useDefinitionVersions(factoryId);
  const asks = useFactoryAsks(factoryId);
  const preview = useSteerPreview(factoryId, staging, definition.def_version);
  const column = useRef<HTMLElement>(null);
  const askRows = asks.data?.factory_id === factoryId ? asks.data.asks : null;
  const facts: OutlineFacts = {
    definition,
    discoveryOn,
    sourcesWaiting: (askRows ?? []).filter((a) => a.kind === "source").length,
    templates:
      (definition.effective.team as { templates?: unknown[] } | undefined)?.templates?.length ??
      null,
    trialsRunning: definition.on_trial.length,
  };
  const groups = visibleGroups(discoveryOn);
  const values = Object.fromEntries(
    visibleSections(discoveryOn).map((s) => [s, outlineValue(s, facts)]),
  ) as Partial<Record<SteerSection, string>>;
  const waiting: Partial<Record<SteerSection, number>> = {
    sources: facts.sourcesWaiting,
    product: (askRows ?? []).filter((a) => a.kind === "profile").length,
    strategy: (askRows ?? []).filter((a) => a.kind === "goal").length,
    versions: 0,
  };
  const count = stagedCount(staging);
  const next = preview.data?.version_next ?? definition.def_version + 1;
  return (
    <>
      <div className="grid min-w-0 grid-cols-1 gap-4 md:grid-cols-[15rem_minmax(0,1fr)] xl:grid-cols-[15rem_minmax(0,1fr)_22rem]">
        <div className="min-w-0 md:row-span-2">
          <SteerOutline
            factoryId={factoryId}
            active={active}
            groups={groups}
            values={values}
            staged={stagedSections(staging)}
            waiting={waiting}
          />
        </div>
        <div
          data-testid="steer-section"
          data-section={active}
          className="flex min-w-0 flex-col gap-4"
        >
          {steer.frozen && (
            <p className="rounded-lg border border-dashed border-status-violet px-3 py-2 text-sm">
              {steer.frozen}
            </p>
          )}
          <Section
            section={active}
            asks={askRows}
            proposals={proposals.data}
            versions={versions.data?.data}
            versionsLoading={versions.isLoading}
          />
        </div>
        <ChangeColumn
          ref={column}
          preview={preview}
          proposals={proposals.data}
          proposalsLoading={proposals.isLoading}
          className="md:col-start-2 xl:sticky xl:top-2 xl:col-start-3 xl:row-start-1 xl:max-h-[calc(100vh-6rem)] xl:self-start xl:overflow-y-auto"
        />
      </div>
      <StagedBar
        count={count}
        context={`v${next}`}
        onReview={() => column.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
        className="xl:hidden"
      />
    </>
  );
}

export function SteerPage() {
  const { section = "overview" } = useParams<{ section: string }>();
  const { snapshot, nowS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const { can } = useViewerCan();
  const definition = useFactoryDefinition(fid);
  const [params, setParams] = useSearchParams();
  const discoveryOn = discoveryRuns(definition.data);
  const known = (STEER_SECTIONS as readonly string[]).includes(section);
  const shown = useMemo(() => visibleSections(discoveryOn), [discoveryOn]);
  if (!known) return <Navigate to={steerPageHref(fid)} replace />;
  const active = section as SteerSection;
  if (definition.data && !shown.includes(active)) {
    return <Navigate to={steerPageHref(fid)} replace />;
  }
  const peek = params.get("peek");
  const change = peek && /^[CF]-\d+$/i.test(peek) ? peek.toUpperCase() : null;
  const closePeek = () =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("peek");
        return next;
      },
      { replace: true },
    );
  return (
    <div
      data-testid="factory-steer-page"
      data-section={active}
      className="flex min-w-0 flex-col gap-3 pb-16 xl:pb-0"
    >
      <div className="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-1">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <SlidersHorizontalIcon aria-hidden className="size-5 text-muted-foreground" />
            Steer
          </h1>
          <p className="text-sm text-muted-foreground">
            How this factory works. Every section is versioned data: change it with a preview, apply
            it as a new version, run it as a trial where that makes sense, and restore any version.
          </p>
        </div>
        {definition.data && (
          <span
            data-testid="steer-version"
            className="shrink-0 rounded-md border border-border px-2 py-0.5 font-mono text-sm"
          >
            definition v{definition.data.def_version}
          </span>
        )}
      </div>
      {definition.data ? (
        <SteerProvider
          snapshot={snapshot}
          nowS={nowS}
          definition={definition.data}
          discoveryOn={discoveryOn}
          canEdit={can.settings || can.apply_projects}
          owner={can.settings}
        >
          <SteerBody active={active} />
        </SteerProvider>
      ) : definition.isLoading ? (
        <div
          aria-busy="true"
          data-testid="steer-loading"
          className="grid gap-4 md:grid-cols-[15rem_minmax(0,1fr)]"
        >
          <div className="h-80 animate-pulse rounded-xl bg-muted/70" />
          <div className="h-80 animate-pulse rounded-xl bg-muted/70" />
        </div>
      ) : (
        <FactoryLoadError
          error={definition.error ?? new Error("unavailable")}
          updatedAt={0}
          onRetry={() => void definition.refetch()}
          what="the factory definition"
          testId="steer-error"
        />
      )}
      <Peek
        open={change != null}
        onOpenChange={(open) => !open && closePeek()}
        title={change ?? ""}
        wide
      >
        {change && <ProposalPage proposalRef={change} />}
      </Peek>
    </div>
  );
}
