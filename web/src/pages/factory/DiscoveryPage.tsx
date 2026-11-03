/**
 * Discovery (`/discovery[/<view>]`): what the factory has learned about its
 * users, and why it proposes what it proposes. Scout's line and spend, the
 * sources, the chain from signals to readouts, the goal and who the factory
 * hears least from, then the views: Opportunities (ranked by band, triaged
 * inline and by key), Flow, Map, Impact, Signals and Hunches. Side panels are
 * in the address (`?peek=O-15`, `C-31`, `source:<id>`, `readout:<id>`) and a
 * Flow selection too (`?sel=`). While discovery is off it says what it does,
 * what Scout would read, what it costs and what an owner approves; nothing
 * names a product, which is the factory's own data.
 */

import { CompassIcon, LightbulbIcon, SlidersHorizontalIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { CHORD_MS, keyOwnedByTarget } from "@/components/factory/cmdk/useFactoryPaletteHotkey";
import { AskScoutButton } from "@/components/factory/discovery/AskScout";
import { BetPeek } from "@/components/factory/discovery/BetPeek";
import { ChainStrip } from "@/components/factory/discovery/ChainStrip";
import {
  type ChainKey,
  type FlowNode,
  type OpportunityRow,
  type TableFilter,
  bandSections,
  chainCells,
  filterRows,
  findReadout,
  flowModel,
  hunchWeight,
  latestReadouts,
  ledgerFacts,
  mapDividers,
  mapPoints,
  mayWithdrawHunch,
  peekTarget,
  pendingBets,
  pendingSources,
  quietSegments,
  sourceStateWord,
  triageAction,
  weightText,
} from "@/components/factory/discovery/discoveryModel";
import {
  isDiscoveryOff,
  isNotFound,
  useDiscoveryProposals,
  useDiscoverySummary,
  useOpportunityList,
  useReadouts,
  useSignals,
} from "@/components/factory/discovery/discoveryQueries";
import { FlowView } from "@/components/factory/discovery/FlowView";
import { HunchComposer, Hunches } from "@/components/factory/discovery/Hunches";
import { ImpactView } from "@/components/factory/discovery/ImpactView";
import { KillDialog } from "@/components/factory/discovery/KillDialog";
import { OpportunityPeek } from "@/components/factory/discovery/OpportunityPeek";
import { OpportunityTable } from "@/components/factory/discovery/OpportunityTable";
import { ReadoutPeek } from "@/components/factory/discovery/ReadoutPeek";
import { SignalQueue } from "@/components/factory/discovery/SignalQueue";
import { SourcePeek } from "@/components/factory/discovery/SourcePeek";
import { TurnOnDiscovery } from "@/components/factory/discovery/TurnOnDiscovery";
import { useDiscoveryLevers } from "@/components/factory/discovery/useDiscoveryLevers";
import { ValueConfidenceMap } from "@/components/factory/discovery/ValueConfidenceMap";
import {
  DISCOVERY_VIEWS,
  type DiscoveryView,
  agentPageHref,
  discoveryPageHref,
  factoryPageHref,
  projectPageHref,
} from "@/components/factory/factoryRoutes";
import { DiscoveryChecklist } from "@/components/factory/home/DiscoveryChecklist";
import { discoveryChecklist, discoveryRuns } from "@/components/factory/home/homeModel";
import { useFactoryAsks } from "@/components/factory/home/homeQueries";
import { useFactoryDefinition } from "@/components/factory/insights/learnQueries";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { useViewer, useViewerCan } from "@/components/factory/shell/viewerCan";
import { MemberAvatar } from "@/components/factory/ui/MemberAvatar";
import { FAINT_TEXT, FOCUS_RING, MEANING } from "@/components/factory/ui/statusTokens";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import { Link, useNavigate, useParams, useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

const VIEW_LABEL: Record<DiscoveryView, string> = {
  opportunities: "Opportunities",
  flow: "Flow",
  map: "Map",
  impact: "Impact",
  signals: "Signals",
  hunches: "Hunches",
};

function viewOf(value: string | undefined): DiscoveryView {
  return value && (DISCOVERY_VIEWS as readonly string[]).includes(value)
    ? (value as DiscoveryView)
    : "opportunities";
}

function Header({
  factoryId,
  on,
  onHunch,
}: {
  factoryId: string;
  on: boolean;
  onHunch?: () => void;
}) {
  return (
    <header className="flex min-w-0 flex-wrap items-start gap-x-4 gap-y-2">
      <div className="flex min-w-0 grow flex-col gap-0.5">
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <CompassIcon aria-hidden className="size-5 text-muted-foreground" />
          Discovery
        </h1>
        <p className="text-sm text-muted-foreground">
          What the factory has learned about its users, and why it proposes what it proposes.
        </p>
      </div>
      {on && (
        <div className="flex flex-wrap items-center gap-1.5">
          <AskScoutButton factoryId={factoryId} />
          {onHunch && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={onHunch}
              data-testid="discovery-i-think"
            >
              <LightbulbIcon aria-hidden />I think…
            </Button>
          )}
          <Button asChild size="sm" variant="ghost">
            <Link
              to={`${factoryPageHref(factoryId)}/steer/rubric`}
              componentId="factory.discovery.rubric"
            >
              <SlidersHorizontalIcon aria-hidden />
              Rubric
            </Link>
          </Button>
        </div>
      )}
    </header>
  );
}

function DiscoveryBody() {
  const { snapshot, nowS, clockOffsetS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const { view: rawView } = useParams<{ view?: string }>();
  const view = viewOf(rawView);
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const phone = useIsMobileViewport();
  const { can } = useViewerCan();
  const viewer = useViewer();
  const canAct = can.post;
  const isOwner = viewer == null || viewer.role === "owner" || viewer.role === "creator";

  const definition = useFactoryDefinition(fid);
  const serverOn = definition.data?.discovery_server === true;
  const configuredOn = discoveryRuns(definition.data);
  const summary = useDiscoverySummary(fid, configuredOn);
  // The ledger has the last word: `404 discovery_off` means it is off after all.
  const on = configuredOn && !(summary.error && isDiscoveryOff(summary.error));
  const facts = summary.data ?? null;
  const list = useOpportunityList(fid, on);
  const proposals = useDiscoveryProposals(fid, on);
  const readouts = useReadouts(fid, on);
  const asks = useFactoryAsks(fid);
  const wantsSignals = on && view === "signals";
  const unsorted = useSignals(fid, "unsorted", wantsSignals);
  const flagged = useSignals(fid, "flagged", wantsSignals);
  const hunches = useSignals(fid, "hunches", on && view === "hunches");
  const levers = useDiscoveryLevers(fid);

  const rows = useMemo(() => list.data?.rows ?? [], [list.data]);
  const allProposals = proposals.all;
  const pending = useMemo(() => pendingSources(proposals.open), [proposals.open]);
  const weight = hunchWeight(definition.data?.effective);
  const readoutList = useMemo(() => readouts.data ?? [], [readouts.data]);

  const [filter, setFilter] = useState<TableFilter>({});
  const [showClosed, setShowClosed] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [killing, setKilling] = useState<{ id: string; title: string } | null>(null);
  const [composing, setComposing] = useState(false);
  const hunchInput = useRef<HTMLInputElement>(null);

  const setParam = useCallback(
    (key: string, value: string | null) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set(key, value);
          else next.delete(key);
          return next;
        },
        { replace: key === "sel" },
      ),
    [setParams],
  );
  const peek = params.get("peek");
  const target = peekTarget(peek);
  const openPeek = useCallback((value: string) => setParam("peek", value), [setParam]);
  const closePeek = useCallback(() => setParam("peek", null), [setParam]);

  const filtered = useMemo(() => filterRows(rows, filter), [rows, filter]);
  const sections = useMemo(() => {
    const all = bandSections(
      filtered,
      list.data?.cutoffs ?? facts?.cutoffs ?? { now: null, next: null },
    );
    return showClosed || filter.chain === "measured" ? all : all.filter((s) => s.band !== "closed");
  }, [filtered, list.data, facts, showClosed, filter.chain]);
  const visible = useMemo(() => sections.flatMap((s) => s.rows), [sections]);
  const closedCount = rows.filter((r) => r.band == null).length;

  const handlers = useMemo(
    () => ({
      open: (row: OpportunityRow) => openPeek(row.id),
      vote: (row: OpportunityRow) => void levers.vote(row),
      plan: (row: OpportunityRow) => void levers.plan(row),
      park: (row: OpportunityRow) => void levers.park(row),
      kill: (row: OpportunityRow) => setKilling({ id: row.id, title: row.title }),
    }),
    [levers, openPeek],
  );

  // j/k move, Enter opens, v vote, n plan, p park, x kill on the selected row.
  // The key after a `g` belongs to the cockpit's chord (`g p` goes to Projects).
  const roomOpen = params.has("room");
  const chordUntil = useRef(0);
  useEffect(() => {
    if (!on || view !== "opportunities" || peek || killing || roomOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.repeat) return;
      const mods = event.metaKey || event.ctrlKey || event.altKey;
      if (!mods && !keyOwnedByTarget(event.target)) {
        const inChord = Date.now() < chordUntil.current;
        chordUntil.current = 0;
        if (inChord) return;
        if (event.key === "g" && !event.shiftKey) {
          chordUntil.current = Date.now() + CHORD_MS;
          return;
        }
      }
      const action = triageAction(event);
      if (!action) return;
      const at = visible.findIndex((r) => r.id === selectedId);
      if (action === "down" || action === "up") {
        if (visible.length === 0) return;
        event.preventDefault();
        const next =
          at < 0 ? 0 : Math.max(0, Math.min(visible.length - 1, at + (action === "down" ? 1 : -1)));
        const id = visible[next]!.id;
        setSelectedId(id);
        document
          .querySelector<HTMLElement>(`[data-testid="opportunity-open-${CSS.escape(id)}"]`)
          ?.focus();
        return;
      }
      const row = at >= 0 ? visible[at] : null;
      if (!row) return;
      // Enter on a button already presses it.
      if (action === "open" && event.target instanceof HTMLButtonElement) return;
      if (action !== "open" && !canAct) return;
      event.preventDefault();
      handlers[action === "open" ? "open" : action](row);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [on, view, peek, killing, roomOpen, visible, selectedId, canAct, handlers]);

  const betsWaiting = (asks.data?.asks ?? []).some((a) => a.kind === "bet");
  const readoutsWaiting = (asks.data?.asks ?? []).some((a) => a.kind === "readout");
  const goal = facts?.goals[0] ?? null;
  const quiet = quietSegments(facts?.coverage ?? []);
  const segmentLabel = useCallback(
    (id: string) => facts?.coverage.find((c) => c.segment === id)?.label ?? id,
    [facts],
  );
  const goalLabel = useCallback(
    (id: string) => facts?.goals.find((g) => g.id === id)?.statement ?? null,
    [facts],
  );
  const checklist = discoveryChecklist(
    definition.data?.effective,
    asks.data?.asks,
    facts
      ? {
          signalsWeek: facts.chain.signalsWeek,
          signalsUnsorted: facts.signals.unsorted,
          opportunities: facts.chain.opportunities,
          opportunitiesNow: null,
          bets: facts.chain.bets,
          readouts: facts.chain.measured,
          readoutsWon: null,
          nextSynthesisAt: facts.nextSynthesisAt,
          learned: [],
        }
      : null,
    on,
  );

  const openNode = (node: FlowNode) => {
    if (node.project) navigate(projectPageHref(fid, node.project));
    else if (node.peek) openPeek(node.peek);
  };

  const startHunch = () => {
    setComposing(true);
    requestAnimationFrame(() => hunchInput.current?.focus());
  };

  if (definition.isLoading && !definition.data) {
    return (
      <div data-testid="factory-discovery-page" className="flex min-w-0 flex-col gap-3">
        <Header factoryId={fid} on={false} />
        <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-muted/70" />
      </div>
    );
  }

  // A server without the definition route (404) runs no discovery; any other failure is an error.
  if (definition.error && !definition.data && !isNotFound(definition.error)) {
    return (
      <div data-testid="factory-discovery-page" className="flex min-w-0 flex-col gap-3">
        <Header factoryId={fid} on={false} />
        <p
          role="alert"
          data-testid="discovery-definition-error"
          className="rounded-lg border border-border px-3 py-2 text-sm"
        >
          Could not load the factory's definition
          {definition.error instanceof Error ? `: ${definition.error.message}` : ""}, so this page
          cannot tell whether discovery is on.{" "}
          <button type="button" className="underline" onClick={() => void definition.refetch()}>
            Retry
          </button>
        </p>
      </div>
    );
  }

  if (!on) {
    return (
      <div data-testid="factory-discovery-page" className="flex min-w-0 flex-col gap-3">
        <Header factoryId={fid} on={false} />
        <TurnOnDiscovery
          factoryId={fid}
          definition={definition.data}
          serverOn={serverOn}
          isOwner={isOwner}
        />
      </div>
    );
  }

  const opportunitiesEmpty = list.data != null && rows.length === 0;
  const unsortedCount = facts?.signals.unsorted ?? null;
  const tableFooter = (
    <>
      <span>
        {unsortedCount != null ? `${unsortedCount} signals not sorted yet · ` : ""}
        <Link
          to={discoveryPageHref(fid, "signals")}
          componentId="factory.discovery.sort"
          className="underline"
        >
          Sort them
        </Link>
      </span>
      {closedCount > 0 && filter.chain !== "measured" && (
        <button
          type="button"
          onClick={() => setShowClosed((v) => !v)}
          className={cn("rounded underline", FOCUS_RING)}
        >
          {showClosed ? "Hide" : "Show"} {closedCount} closed
        </button>
      )}
      <span className="grow" />
      <span className="font-mono">
        {list.data?.formula ?? "rank = reach × intensity × fit × confidence"}
        {list.data?.defVersion != null ? ` · definition v${list.data.defVersion}` : ""}
      </span>
    </>
  );

  return (
    <div data-testid="factory-discovery-page" className="flex min-w-0 flex-col gap-3">
      <Header factoryId={fid} on onHunch={canAct ? startHunch : undefined} />
      {composing && canAct && (
        <HunchComposer
          ref={hunchInput}
          id="header"
          onAdd={async (text) => {
            const ok = await levers.hunch(text);
            if (ok) setComposing(false);
            return ok;
          }}
        />
      )}

      {summary.error && !isDiscoveryOff(summary.error) ? (
        <p
          role="alert"
          data-testid="discovery-summary-error"
          className="rounded-lg border border-border px-3 py-2 text-sm"
        >
          Could not load the discovery summary
          {summary.error instanceof Error ? `: ${summary.error.message}` : ""}. Everything else on
          this page still works.{" "}
          <button type="button" className="underline" onClick={() => void summary.refetch()}>
            Retry
          </button>
        </p>
      ) : !facts ? (
        <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-muted/70" />
      ) : (
        <>
          <div
            className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm"
            data-testid="discovery-scout"
          >
            <MemberAvatar
              name="Scout"
              role="lead"
              seat="discovery"
              state="idle"
              size={22}
              label={`Scout, discovery lead, ${facts.scout.state === "off" ? "not running" : facts.scout.state}`}
            />
            {facts.scout.sessionId ? (
              <Link
                to={agentPageHref(fid, facts.scout.sessionId)}
                componentId="factory.discovery.scout"
                className={cn("font-semibold hover:underline", FOCUS_RING)}
              >
                Scout
              </Link>
            ) : (
              <span className="font-semibold">Scout</span>
            )}
            <span className="text-muted-foreground">
              {facts.scout.state === "off" ? "not running" : facts.scout.state}
              {facts.budget.usdPerDay != null
                ? ` · $${(facts.budget.spentToday ?? 0).toFixed(2)} of $${facts.budget.usdPerDay} today`
                : ""}
            </span>
          </div>
          <div
            className="flex flex-wrap items-center gap-1.5 text-sm max-md:flex-nowrap max-md:overflow-x-auto max-md:[scrollbar-width:none] max-md:[&>*]:shrink-0"
            data-testid="discovery-sources"
          >
            <span className="text-muted-foreground">Sources</span>
            {facts.sources.length === 0 && pending.length === 0 && (
              <span className={FAINT_TEXT}>none yet</span>
            )}
            {facts.sources.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => openPeek(`source:${s.id}`)}
                data-testid={`source-chip-${s.id}`}
                className={cn(
                  "flex items-center gap-1.5 rounded-md border px-2 py-0.5 hover:bg-muted",
                  s.state === "paused" || s.health === "failing"
                    ? "border-dashed border-border"
                    : "border-border",
                  s.health === "failing" && MEANING.broken.text,
                  FOCUS_RING,
                )}
              >
                <span className="font-mono text-xs text-muted-foreground">{s.id}</span>
                {s.label} · {sourceStateWord(s)}
              </button>
            ))}
            {pending.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => openPeek(`source:${s.id}`)}
                data-testid={`source-chip-${s.id}`}
                className={cn(
                  "flex items-center gap-1.5 rounded-md border px-2 py-0.5",
                  MEANING.needsYou.border,
                  MEANING.needsYou.surface,
                  MEANING.needsYou.text,
                  FOCUS_RING,
                )}
              >
                <span className="font-mono text-xs">{s.id}</span>
                {s.label} · waits for you
              </button>
            ))}
          </div>
          <ChainStrip
            cells={chainCells(facts.chain, { betsWaiting, readoutsWaiting })}
            selected={filter.chain ?? null}
            onSelect={(key: ChainKey | null) => {
              if (key === "signals") {
                navigate(discoveryPageHref(fid, "signals"));
                return;
              }
              setFilter((f) => ({ ...f, chain: key }));
              if (view !== "opportunities") navigate(discoveryPageHref(fid));
            }}
          />
          <div
            className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm"
            data-testid="discovery-goal"
          >
            {goal ? (
              <>
                <span className="rounded border border-border px-1.5 font-mono text-xs">
                  {goal.id}
                </span>
                <span className="font-semibold">{goal.statement}</span>
                <span className="text-muted-foreground">
                  {goal.measured ? "measured" : "not measured yet"}
                </span>
              </>
            ) : (
              <span className="text-muted-foreground">
                No goal yet. Opportunities stay unassigned until one exists.
              </span>
            )}
            <span className="grow" />
            {quiet.length > 0 && (
              <>
                <span className="text-muted-foreground">Hearing least from:</span>
                {quiet.map((c) => (
                  <span key={c.segment} className="rounded-md bg-muted px-1.5 py-0.5">
                    {c.label} · {c.signals30d} {c.signals30d === 1 ? "signal" : "signals"} in 30
                    days
                  </span>
                ))}
              </>
            )}
          </div>
        </>
      )}

      <nav
        aria-label="Discovery views"
        className="flex min-w-0 gap-1 overflow-x-auto overflow-y-hidden border-b border-border [scrollbar-width:none]"
      >
        {DISCOVERY_VIEWS.map((v) => {
          const count =
            v === "signals"
              ? unsortedCount
              : v === "hunches"
                ? (facts?.signals.hunches ?? null)
                : null;
          return (
            <Link
              key={v}
              to={`${discoveryPageHref(fid, v === "opportunities" ? undefined : v)}`}
              componentId={`factory.discovery.view_${v}`}
              aria-current={view === v ? "page" : undefined}
              data-testid={`discovery-view-${v}`}
              className={cn(
                "-mb-px shrink-0 border-b-2 px-3 py-2 text-sm font-medium",
                view === v
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
                FOCUS_RING,
              )}
            >
              {VIEW_LABEL[v]}
              {count ? (
                <span className="ml-1.5 rounded-full bg-muted px-1.5 text-xs text-foreground">
                  {count}
                </span>
              ) : null}
            </Link>
          );
        })}
      </nav>

      {view === "opportunities" && (
        <div
          className={cn("grid min-w-0 gap-4", checklist && "lg:grid-cols-[minmax(0,1fr)_20rem]")}
        >
          <div className="flex min-w-0 flex-col gap-3">
            {list.error ? (
              <p role="alert" className="rounded-lg border border-border px-3 py-2 text-sm">
                Could not load the opportunities. Everything else on this page still works.{" "}
                <button type="button" className="underline" onClick={() => void list.refetch()}>
                  Retry
                </button>
              </p>
            ) : !list.data ? (
              <div aria-busy="true" className="h-64 animate-pulse rounded-xl bg-muted/70" />
            ) : opportunitiesEmpty ? (
              <section
                data-testid="discovery-cold-start"
                className="rounded-xl border border-border bg-card p-4 text-sm"
              >
                <h2 className="text-base font-semibold">No opportunities yet</h2>
                <p className="mt-1 text-muted-foreground">
                  Scout groups signals into opportunities in its weekly synthesis.
                  {unsortedCount ? ` ${unsortedCount} signals are waiting.` : ""} Until then you can
                  add hunches, ask Scout a question, or look at the unsorted signals.
                </p>
                <Button asChild size="sm" variant="outline" className="mt-2">
                  <Link
                    to={discoveryPageHref(fid, "signals")}
                    componentId="factory.discovery.see_signals"
                  >
                    See signals
                  </Link>
                </Button>
              </section>
            ) : (
              <>
                <div
                  className="flex flex-wrap items-center gap-1.5 text-sm"
                  data-testid="discovery-filters"
                >
                  <span className="text-muted-foreground">Filter:</span>
                  {(facts?.coverage ?? []).map((c) => (
                    <button
                      key={c.segment}
                      type="button"
                      aria-pressed={filter.segment === c.segment}
                      onClick={() =>
                        setFilter((f) => ({
                          ...f,
                          segment: f.segment === c.segment ? null : c.segment,
                        }))
                      }
                      className={cn(
                        "rounded-md border border-border px-2 py-0.5 aria-pressed:bg-status-blue/15",
                        FOCUS_RING,
                      )}
                    >
                      {c.label}
                      {c.focus ? " · focus" : ""}
                    </button>
                  ))}
                  {(facts?.sources ?? []).map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      aria-pressed={filter.source === s.id}
                      onClick={() =>
                        setFilter((f) => ({ ...f, source: f.source === s.id ? null : s.id }))
                      }
                      className={cn(
                        "rounded-md border border-border px-2 py-0.5 font-mono aria-pressed:bg-status-blue/15",
                        FOCUS_RING,
                      )}
                    >
                      {s.id}
                    </button>
                  ))}
                  {(filter.chain || filter.segment || filter.source) && (
                    <Button type="button" size="sm" variant="ghost" onClick={() => setFilter({})}>
                      Clear
                    </Button>
                  )}
                  <span className="grow" />
                  <span className="text-xs text-muted-foreground max-md:hidden">
                    j k move · Enter open · v vote · n plan · p park · x kill
                  </span>
                </div>
                {visible.length === 0 ? (
                  <p className="rounded-xl border border-border bg-card px-4 py-6 text-sm text-muted-foreground">
                    Nothing matches this filter.{" "}
                    <button type="button" className="underline" onClick={() => setFilter({})}>
                      Clear the filter
                    </button>
                  </p>
                ) : (
                  <OpportunityTable
                    sections={sections}
                    selectedId={selectedId}
                    nowS={nowS}
                    canAct={canAct}
                    handlers={handlers}
                    onSelect={setSelectedId}
                    footer={tableFooter}
                  />
                )}
              </>
            )}
            <section
              aria-label="Add a hunch"
              className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4"
            >
              <div className="flex flex-wrap items-baseline gap-x-3">
                <h2 className="grow text-base font-semibold">Your input</h2>
                <span className="text-xs text-muted-foreground">
                  A hunch counts with weight {weightText(weight)} and gets a cheapest test. It is
                  never mixed into user evidence.
                </span>
              </div>
              {canAct ? (
                <HunchComposer id="opportunities" onAdd={(text) => levers.hunch(text)} />
              ) : (
                <p className="text-sm text-muted-foreground">Ask an operator to change this.</p>
              )}
            </section>
          </div>
          {checklist && (
            <div>
              <DiscoveryChecklist steps={checklist} />
            </div>
          )}
        </div>
      )}

      {view === "flow" && (
        <FlowView
          model={flowModel({
            sources: facts?.sources ?? [],
            rows,
            proposals: allProposals,
            snapshot,
            readouts: readoutList,
            pendingSources: pending,
          })}
          selected={params.get("sel")}
          phone={phone}
          onSelect={(id) => setParam("sel", id)}
          onOpen={openNode}
        />
      )}

      {view === "map" && (
        <ValueConfidenceMap
          points={mapPoints(rows)}
          dividers={mapDividers(definition.data?.effective)}
          defVersion={list.data?.defVersion ?? null}
          onOpen={openPeek}
        />
      )}

      {view === "impact" && (
        <ImpactView
          readouts={latestReadouts(readoutList)}
          readoutsRoute={readouts.data != null}
          bets={pendingBets(allProposals, snapshot.projects, readoutList)}
          ledger={ledgerFacts(facts?.ledger ?? null, readoutList, snapshot.projects)}
          onOpenReadout={(id) => openPeek(`readout:${id}`)}
          onOpenBet={openPeek}
        />
      )}

      {view === "signals" && (
        <SignalQueue
          unsorted={unsorted.data ?? null}
          flagged={flagged.data ?? null}
          waiting={unsortedCount}
          targets={rows.filter((r) => r.band != null && r.band !== "parked")}
          canAct={canAct}
          loading={unsorted.isLoading || flagged.isLoading}
          onAttach={(signal, opportunity, counter) =>
            void levers.attach(signal, opportunity, counter)
          }
        />
      )}

      {view === "hunches" && (
        <Hunches
          hunches={hunches.data ?? null}
          rows={rows}
          loading={hunches.isLoading}
          canAct={canAct}
          weight={weight}
          mayWithdraw={(h) =>
            mayWithdrawHunch(h, { userId: viewer?.user_id ?? null, owner: isOwner })
          }
          onOpen={openPeek}
          onWithdraw={(ref) => void levers.withdraw(ref)}
          onAdd={(text) => levers.hunch(text)}
        />
      )}

      <OpportunityPeek
        factoryId={fid}
        opportunity={target?.kind === "opportunity" ? target.id : null}
        nowS={nowS}
        canAct={canAct}
        perAsk={facts?.budget.perAsk ?? null}
        hunchWeight={weight}
        readouts={readoutList}
        levers={levers}
        goalLabel={goalLabel}
        segmentLabel={segmentLabel}
        onPeek={openPeek}
        onClose={closePeek}
        onKill={(t) => setKilling({ id: t.id, title: t.title })}
      />
      <BetPeek
        factoryId={fid}
        proposal={target?.kind === "bet" ? target.id : null}
        snapshot={snapshot}
        definition={definition.data?.effective}
        clockOffsetS={clockOffsetS}
        onClose={closePeek}
      />
      <SourcePeek
        factoryId={fid}
        sourceId={target?.kind === "source" ? target.id : null}
        sources={facts?.sources ?? []}
        pending={pending}
        snapshot={snapshot}
        clockOffsetS={clockOffsetS}
        canAct={canAct}
        isOwner={isOwner}
        levers={levers}
        hunchWeight={weight}
        onClose={closePeek}
      />
      <ReadoutPeek
        factoryId={fid}
        readoutId={target?.kind === "readout" ? target.id : null}
        readout={target?.kind === "readout" ? findReadout(readoutList, target.id) : null}
        canAct={canAct}
        onOpenOpportunity={openPeek}
        onClose={closePeek}
      />
      <KillDialog
        target={killing}
        onClose={() => setKilling(null)}
        onKill={(reason, note) => {
          const row = killing;
          if (row) void levers.kill(row, reason, note);
        }}
      />
    </div>
  );
}

export function DiscoveryPage() {
  return <DiscoveryBody />;
}
