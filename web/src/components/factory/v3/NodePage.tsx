// One node's page in cockpit v3, calm: the level strip (or the phone's level
// row), the short title with Details and the ⋯ verbs, Your move, then its
// children one line each (or one level under it, grouped under parents).
// Nothing is drawn between nodes. The node's chat is the page's right column.

import {
  type MutableRefObject,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Navigate } from "react-router-dom";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import { useLocation, useNavigate, useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { NodeChat } from "./chat/NodeChat";
import { ChildrenView } from "./Children";
import { LevelStrip, PhoneLevelRow } from "./LevelStrip";
import { LevelView } from "./LevelView";
import {
  AboveProvider,
  type NodeActions,
  NodeContextProvider,
  useNodeActions,
} from "./NodeContext";
import { NodeContent } from "./NodeContent";
import { NodeHeader } from "./NodeHeader";
import { NodeLink } from "./nodeParts";
import { useLevel, useMoreChildren, useNode, usePrefetchNode } from "./nodeQueries";
import { nodeHref } from "./nodeRoutes";
import { useNodeSource } from "./nodeSource";
import { useAskReview, useMarkSeen, useVisitSince } from "./nodeSteer";
import { DEFAULT_OVERLAYS, ParentNameContext } from "./NodeTile";
import { movedAsks, nodeName } from "./calm/calmModel";
import { YourMove, useYourMove } from "./calm/YourMove";
import { LensMenu } from "./calm/NodeMenu";
import type { ChildPage, NodePage as Page, TimelineView, VerbArgs, VerbAvail } from "./nodeTypes";
import { timelineSub } from "./lenses/TimelineLens";
import { drawsTiles, levelName } from "./nodeWire";
import { useSheetActions } from "./sheets/V3Sheets";
import { hasWhy } from "./statusModel";
import { useNodeKeys, useRovingStops } from "./useNodeKeys";
import { useFactoryToast } from "../ui/Toast";
import { LineageBody, MoneyBody, TimelineBody } from "./lenses/LensBodies";
import { type Lens, useLensChoice } from "./lenses/lensModel";
import { type OpenVerb, VerbPreview } from "./verbs/VerbPreviews";

/** A section's heading: the children's level name, and the lens menu ("List ▾"). */
function SectionHead({
  title,
  sub,
  lenses,
  lens,
  onLens,
}: {
  title: string;
  /** What a lens the viewer picked shows (never on the default view). */
  sub?: string;
  lenses: Lens[];
  lens: Lens | null;
  onLens: (l: Lens) => void;
}) {
  return (
    <div className="mb-2 flex items-center gap-2.5">
      <h2 className="px-0.5 text-[11px] tracking-[0.07em] text-foreground-tertiary uppercase">
        {title}
      </h2>
      {sub && <span className="text-xs text-foreground-tertiary max-md:hidden">{sub}</span>}
      <span className="flex-1" />
      <LensMenu lenses={lenses} lens={lens} onLens={onLens} />
    </div>
  );
}

/** What the section's heading says for a lens other than Map and List. */
function lensHead(lens: Lens, page: Page, timeline?: TimelineView | null) {
  const plural = levelName(page.children.level, page.children.level_name);
  switch (lens) {
    case "lineage":
      return { title: "Lineage", sub: "where it came from, what is below, where it landed" };
    case "timeline":
      return { title: `${plural} over time`, sub: timeline ? timelineSub(timeline) : "" };
    default:
      return { title: "Money", sub: "cost so far against budget" };
  }
}

function Below({
  page,
  level,
  explicit,
  lensKey,
}: {
  page: Page;
  level: string | null;
  explicit: boolean;
  /** Where `v` finds the next lens. */
  lensKey: MutableRefObject<(() => void) | null>;
}) {
  const { factoryId } = useNodeActions();
  const grouped = Boolean(level && level !== page.children.level);
  const view = useLevel(page.node.ref, grouped ? level : null, factoryId);
  const more = useMoreChildren(page.node.ref, page.children, factoryId);
  const c: ChildPage = more.items.length
    ? { ...page.children, items: [...page.children.items, ...more.items] }
    : page.children;
  const items = grouped
    ? (view.data?.groups.flatMap((g) => g.items) ?? [])
    : [...c.items, ...c.ghosts];
  const tiles = grouped ? Boolean(view.data && drawsTiles(view.data)) : drawsTiles(c);
  const choice = useLensChoice(page, { grouped, items: items.length, tiles });
  const { lens, lenses, choose } = choice;
  useEffect(() => {
    lensKey.current = choice.next;
    return () => {
      lensKey.current = null;
    };
  }, [choice.next, lensKey]);
  const other = lens === "lineage" || lens === "timeline" || lens === "money";
  if (other || choice.pending) {
    const shown: Lens = choice.pending ? "timeline" : lens!;
    const head = lensHead(shown, page, choice.timeline.data);
    const folds = !items.length && shown === "lineage";
    return (
      <section className="mt-1" aria-label={head.title} data-testid="v3-below">
        <SectionHead
          title={head.title}
          sub={head.sub}
          lenses={lenses}
          lens={shown}
          onLens={choose}
        />
        {folds && (
          <button
            type="button"
            onClick={() => choose(null)}
            className="-mt-1 mb-2 text-xs text-status-blue-fg hover:underline"
          >
            Fold
          </button>
        )}
        {shown === "lineage" ? (
          <LineageBody nodeRef={page.node.ref} />
        ) : shown === "money" ? (
          <MoneyBody nodeRef={page.node.ref} />
        ) : (
          <TimelineBody query={choice.timeline} />
        )}
      </section>
    );
  }
  const shownLens: "map" | "list" = lens === "map" ? "map" : "list";
  if (grouped) {
    if (view.isPending)
      return <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-muted" />;
    if (!view.data) return null;
    const name = levelName(view.data.level, view.data.level_name);
    return (
      <section className="mt-1" aria-label={name} data-testid="v3-below">
        <SectionHead title={name} lenses={lenses} lens={lens} onLens={choose} />
        {explicit && (
          <NodeLink
            nodeRef={page.node.ref}
            className="-mt-1 mb-2 inline-block px-0.5 text-xs text-status-blue-fg hover:underline"
          >
            ‹ Back to children
          </NodeLink>
        )}
        <LevelView view={view.data} lens={shownLens} overlays={DEFAULT_OVERLAYS} />
      </section>
    );
  }
  // Nothing below: the page ends at Your move (its lineage is in Details and the strip).
  if (!c.items.length && !c.later.length && !c.ghosts.length) return null;
  const name = levelName(c.level, c.level_name);
  const parentName = nodeName(page.node);
  return (
    <section className="mt-1" aria-label={name} data-testid="v3-below">
      <SectionHead title={name} lenses={lenses} lens={lens} onLens={choose} />
      <ParentNameContext.Provider value={parentName}>
        <ChildrenView page={c} lens={shownLens} more={more} />
      </ParentNameContext.Provider>
    </section>
  );
}

export interface NodePageProps {
  factoryId: string;
  nodeRef: string;
  /** A level under the node to list, grouped under parents (the URL's `/<level>`). */
  level?: string | null;
  /**
   * The page's actions (answer, open an ask, talk); links, prefetch, sheets and
   * verb previews are wired here. `onVerb` is asked only for Talk.
   */
  actions?: Omit<NodeActions, "factoryId" | "prefetch" | "aboveId">;
  /** Says the data is not this factory's, with a way back. */
  notice?: ReactNode;
  /** The node's chat (a column beside the page, or a box pinned at the bottom of a narrow one); off when absent. */
  chat?: { sample?: boolean };
}

function Missing({ nodeRef }: { nodeRef: string }) {
  return (
    <div
      data-testid="v3-node-missing"
      className="rounded-xl border border-dashed border-border-strong p-6 text-sm text-muted-foreground"
    >
      No node has the ref <span className="font-mono">{nodeRef}</span> here.
    </div>
  );
}

export function NodePage(props: NodePageProps) {
  const { factoryId, nodeRef, level = null } = props;
  const query = useNode(nodeRef, factoryId);
  const { search } = useLocation();
  const page = query.data;
  if (query.isPending) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading"
        data-testid="v3-node-loading"
        className="h-96 animate-pulse rounded-xl bg-muted"
      />
    );
  }
  if (query.isError) {
    return (
      <div
        role="alert"
        className="rounded-xl border border-status-red/55 p-4 text-sm text-status-red-fg"
      >
        This node could not be read: {query.error instanceof Error ? query.error.message : "error"}
      </div>
    );
  }
  if (!page) return <Missing nodeRef={nodeRef} />;
  // An implicit parent with one child answers as that child: show its own address
  // (with the verb or sheet the address named).
  if (page.node.ref !== nodeRef) {
    return <Navigate replace to={`${nodeHref(factoryId, page.node.ref, level)}${search}`} />;
  }
  return <LoadedNodePage {...props} page={page} />;
}

/** What a verb's preview starts from, as the address names it (`?verb=add&title=…`). */
export const VERB_ARGS: ("title" | "member" | "to")[] = ["title", "member", "to"];

/**
 * The verb opened on this page: its preview shows under the header. Talk goes
 * to the chat; a verb the source cannot preview says so and changes nothing.
 * `?verb=start` (words in another node's chat) opens one once the page is in.
 */
function useOpenVerb(page: Page, talk?: NodeActions["onVerb"]) {
  const source = useNodeSource();
  const toast = useFactoryToast();
  const { mutate: askReview, supported: canReview } = useAskReview(page);
  const [open, setOpen] = useState<OpenVerb | null>(null);
  const onVerb = useCallback(
    (verb: VerbAvail, args?: VerbArgs) => {
      if (verb.verb === "talk") {
        talk?.(verb, args);
        return;
      }
      if (!verb.enabled) return;
      // "Is this done?" only reads: no preview, the review is asked at once.
      if (verb.verb === "review" && canReview) {
        askReview();
        return;
      }
      // Team and Rename levels are definition versions: they preview through the definition.
      if (verb.verb !== "team" && verb.verb !== "rename" && !source?.preview) {
        toast.show({
          message: `${verb.label}: this server cannot preview it yet, so nothing changed.`,
        });
        return;
      }
      setOpen((prev) => (prev?.verb.verb === verb.verb && !args ? null : { verb, args }));
    },
    [askReview, canReview, source?.preview, talk, toast],
  );
  const [params, setParams] = useSearchParams();
  const pending = params.get("verb");
  useEffect(() => {
    if (!pending) return;
    const args: VerbArgs = {};
    for (const key of VERB_ARGS) {
      const value = params.get(key);
      if (value) args[key] = value;
    }
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const key of ["verb", ...VERB_ARGS]) next.delete(key);
        return next;
      },
      { replace: true },
    );
    const verb = page.verbs.find((v) => v.verb === pending && v.enabled);
    if (verb) onVerb(verb, args);
    else toast.show({ message: `${page.node.title} offers no “${pending}” here.` });
  }, [onVerb, page, params, pending, setParams, toast]);
  const close = useCallback(() => setOpen(null), []);
  /** A verb's key (`a`, `s`, `t` …) opens its preview. */
  const onKey = useCallback(
    (key: string) => {
      const verb = page.verbs.find((v) => v.key === key && v.enabled && v.verb !== "talk");
      if (!verb) return false;
      onVerb(verb);
      return true;
    },
    [onVerb, page.verbs],
  );
  return { open, onVerb, close, onKey };
}

function LoadedNodePage({
  factoryId,
  level = null,
  actions,
  notice,
  chat,
  page: read,
}: NodePageProps & { page: Page }) {
  // This visit's "since" stays while the page reads again (the mark moved, a verb applied).
  const page = useVisitSince(read);
  const prefetch = usePrefetchNode(factoryId);
  const phone = useIsMobileViewport();
  const navigate = useNavigate();
  const body = useRef<HTMLDivElement>(null);
  const [whyOpen, setWhyOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const parent = [...page.breadcrumb].reverse().find((c) => !c.folded);
  // `w`: the why chain lives in Details, so it opens Details with it.
  const toggleWhy = useCallback(() => {
    setWhyOpen((open) => {
      if (!open) setDetailsOpen(true);
      return !open;
    });
  }, []);
  const why = hasWhy(page.status);
  useRovingStops(body);
  const verbs = useOpenVerb(page, actions?.onVerb);
  const sheets = useSheetActions();
  useMarkSeen(page.node.ref, factoryId);
  const lensKey = useRef<(() => void) | null>(null);
  const onLens = useCallback(() => lensKey.current?.(), []);
  useNodeKeys(body, {
    onStepOut: parent ? () => navigate(nodeHref(factoryId, parent.ref)) : undefined,
    onWhy: why ? toggleWhy : undefined,
    onVerbKey: verbs.onKey,
    onLens,
  });
  const openVerb = verbs.open?.verb.verb ?? null;
  const value = useMemo<NodeActions>(
    () => ({
      ...sheets,
      ...actions,
      factoryId,
      prefetch,
      onVerb: verbs.onVerb,
      openVerb,
    }),
    [actions, factoryId, prefetch, sheets, verbs.onVerb, openVerb],
  );
  const shown = level ?? page.home ?? (page.children.level || null);
  const { view: move, you } = useYourMove(page, shown, factoryId);
  // Every ask Your move holds: tiles, rows, Details and the chat never repeat it.
  const above = useMemo(() => movedAsks(move), [move]);
  return (
    <NodeContextProvider value={value}>
      <AboveProvider aboveId={above}>
        <div data-testid="v3-node-page" data-ref={page.node.ref} className="flex min-w-0 flex-col">
          {phone ? (
            <div className="mb-3">
              <PhoneLevelRow page={page} shown={shown} you={you} />
            </div>
          ) : (
            <div className="-mx-4 -mt-1 mb-[18px] md:-mx-6">
              <LevelStrip page={page} shown={shown} you={you} />
            </div>
          )}
          <div className={cn("grid min-w-0 gap-6", chat && "lg:grid-cols-[minmax(0,1fr)_auto]")}>
            <div ref={body} className="min-w-0" data-testid="v3-main">
              {notice}
              <NodeHeader
                page={page}
                whyOpen={whyOpen}
                onWhyChange={setWhyOpen}
                detailsOpen={detailsOpen}
                onDetailsChange={setDetailsOpen}
              />
              {verbs.open && (
                <div className="mb-3">
                  <VerbPreview
                    key={`${verbs.open.verb.verb}:${JSON.stringify(verbs.open.args ?? {})}`}
                    page={page}
                    open={verbs.open}
                    onClose={verbs.close}
                  />
                </div>
              )}
              <YourMove view={move} />
              {!level && <NodeContent page={page} part="body" />}
              <Below page={page} level={shown} explicit={Boolean(level)} lensKey={lensKey} />
            </div>
            {chat && <NodeChat page={page} sample={chat.sample} />}
          </div>
        </div>
      </AboveProvider>
    </NodeContextProvider>
  );
}
