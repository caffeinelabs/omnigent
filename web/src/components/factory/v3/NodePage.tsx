// One node's page in cockpit v3: the level strip (or the phone's level row),
// the header, then its children in one lens, or one level under it grouped
// under parents. One parent per node and one level per view: nothing here is
// drawn between nodes. The node's chat is the page's right column (`chat`).

import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { Kbd, NodeLink, sinceWhen } from "./nodeParts";
import { useLevel, useMoreChildren, useNode, usePrefetchNode } from "./nodeQueries";
import { nodeHref } from "./nodeRoutes";
import { useNodeSource } from "./nodeSource";
import { useMarkSeen } from "./nodeSteer";
import { DEFAULT_OVERLAYS, type Overlays } from "./NodeTile";
import type {
  ChildPage,
  LevelView as View,
  NodePage as Page,
  NodeSummary,
  VerbArgs,
  VerbAvail,
} from "./nodeTypes";
import { drawsTiles, levelName } from "./nodeWire";
import { useSheetActions } from "./sheets/V3Sheets";
import { hasWhy } from "./statusModel";
import { useNodeKeys, useRovingStops } from "./useNodeKeys";
import { useFactoryToast } from "../ui/Toast";
import { type OpenVerb, VerbPreview } from "./verbs/VerbPreviews";

type Lens = "map" | "list";

const OVERLAY_NAMES: [keyof Overlays, string][] = [
  ["needs", "Needs you"],
  ["changed", "Since"],
  ["env", "Try it"],
  ["cost", "Cost"],
  ["people", "People"],
];

/** Overlays that would show something for these items (an overlay with nothing to add is not offered). */
function usefulOverlays(items: NodeSummary[]): (keyof Overlays)[] {
  const has: Record<keyof Overlays, boolean> = {
    needs: items.some((k) => k.needs.open > 0),
    changed: items.some((k) => k.changed?.length),
    env: items.some((k) => k.try_ready),
    cost: items.some((k) => k.cost),
    people: items.some((k) => k.working?.length || k.idle),
  };
  return OVERLAY_NAMES.map(([k]) => k).filter((k) => has[k]);
}

function Seg({
  options,
  value,
  onChange,
  label,
}: {
  options: [Lens, string][];
  value: Lens;
  onChange: (v: Lens) => void;
  label: string;
}) {
  return (
    <span
      role="tablist"
      aria-label={label}
      className="inline-flex shrink-0 rounded-lg border border-border bg-muted/40 p-0.5"
    >
      {options.map(([v, name]) => (
        <button
          key={v}
          role="tab"
          type="button"
          aria-selected={v === value}
          onClick={() => onChange(v)}
          className={cn(
            "rounded-md px-2.5 py-0.5 text-[12.5px] whitespace-nowrap text-muted-foreground",
            v === value && "bg-card-solid text-foreground shadow-sm ring-1 ring-border",
          )}
        >
          {name}
        </button>
      ))}
    </span>
  );
}

function SectionHead({
  title,
  sub,
  lenses,
  lens,
  onLens,
  overlays,
  offered,
  onOverlay,
  since,
}: {
  title: string;
  sub: string;
  lenses: Lens[];
  lens: Lens;
  onLens: (l: Lens) => void;
  overlays: Overlays;
  offered: (keyof Overlays)[];
  onOverlay: (k: keyof Overlays) => void;
  since?: string;
}) {
  return (
    <div className="mb-2.5 flex flex-wrap items-center gap-2.5">
      <h2 className="text-[13px] font-semibold tracking-[0.01em]">{title}</h2>
      {sub && <span className="text-[12.5px] text-foreground-tertiary max-md:hidden">{sub}</span>}
      <span className="flex-1 max-md:hidden" />
      {lenses.length > 1 && (
        <Seg
          label="Lens"
          value={lens}
          onChange={onLens}
          options={lenses.map((l) => [l, l === "map" ? "Map" : "List"])}
        />
      )}
      {offered.length > 0 && (
        <span
          aria-label="Overlays"
          className="flex max-w-full items-center gap-1 overflow-x-auto [scrollbar-width:none] md:basis-full md:flex-wrap [&::-webkit-scrollbar]:hidden"
        >
          {offered.map((k) => {
            const name = OVERLAY_NAMES.find(([key]) => key === k)![1];
            return (
              <button
                key={k}
                type="button"
                aria-pressed={overlays[k]}
                onClick={() => onOverlay(k)}
                className={cn(
                  "shrink-0 rounded-full border px-2.5 py-px text-xs whitespace-nowrap",
                  overlays[k]
                    ? "border-border-strong bg-card-solid text-foreground before:mr-1 before:text-[9px] before:text-status-blue before:content-['●']"
                    : "border-border text-foreground-tertiary",
                )}
              >
                {k === "changed" && since ? `Since ${sinceWhen(since)}` : name}
              </button>
            );
          })}
        </span>
      )}
    </div>
  );
}

function childSub(c: ChildPage): string {
  // `total` counts every child, also those past the first 60 not read yet.
  const active = Math.max(c.items.length, c.total - c.later.length - c.ghosts.length);
  const later = c.later.length ? ` · ${c.later.length} later` : "";
  const fold = c.via_fold ? ` · ${c.via_fold} shown in place of an implicit parent` : "";
  return `${active}${later}${fold}`;
}

function viewSub(v: View): string {
  const grouped = v.parent_level_name ? ` · grouped under ${v.parent_level_name}` : "";
  const fold = v.via_fold ? ` · ${v.via_fold} shown in place of an implicit parent` : "";
  return `${v.total}${grouped}${fold}`;
}

function Below({ page, level, explicit }: { page: Page; level: string | null; explicit: boolean }) {
  const { factoryId } = useNodeActions();
  const grouped = Boolean(level && level !== page.children.level);
  const view = useLevel(page.node.ref, grouped ? level : null, factoryId);
  const more = useMoreChildren(page.node.ref, page.children, factoryId);
  const [lens, setLens] = useState<Lens>("map");
  const [overlays, setOverlays] = useState<Overlays>(DEFAULT_OVERLAYS);
  const c: ChildPage = more.items.length
    ? { ...page.children, items: [...page.children.items, ...more.items] }
    : page.children;
  const items = grouped
    ? (view.data?.groups.flatMap((g) => g.items) ?? [])
    : [...c.items, ...c.ghosts];
  const tiles = grouped ? Boolean(view.data && drawsTiles(view.data)) : drawsTiles(c);
  const lenses: Lens[] = items.length ? (tiles ? ["map", "list"] : ["list"]) : [];
  const shownLens: Lens = lenses.includes(lens) ? lens : "list";
  const offered = usefulOverlays(items);
  const toggle = (k: keyof Overlays) => setOverlays((o) => ({ ...o, [k]: !o[k] }));
  if (grouped) {
    if (view.isPending)
      return <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-muted" />;
    if (!view.data) return null;
    const name = levelName(view.data.level, view.data.level_name);
    return (
      <section className="mt-[18px]" aria-label={name} data-testid="v3-below">
        <SectionHead
          title={name}
          sub={viewSub(view.data)}
          lenses={lenses}
          lens={shownLens}
          onLens={setLens}
          overlays={overlays}
          offered={offered}
          onOverlay={toggle}
          since={page.rollup.since?.at}
        />
        {explicit && (
          <p className="-mt-0.5 mb-2.5 text-xs text-foreground-tertiary">
            One level at a time: {name.toLowerCase()} under {page.node.title}, each under its
            parent&rsquo;s name.{" "}
            <NodeLink nodeRef={page.node.ref} className="text-status-blue-fg hover:underline">
              Back to children
            </NodeLink>
          </p>
        )}
        <LevelView view={view.data} lens={shownLens} overlays={overlays} />
      </section>
    );
  }
  if (!c.items.length && !c.later.length && !c.ghosts.length) return null;
  const name = levelName(c.level, c.level_name);
  return (
    <section className="mt-[18px]" aria-label={name} data-testid="v3-below">
      <SectionHead
        title={name}
        sub={childSub(c)}
        lenses={lenses}
        lens={shownLens}
        onLens={setLens}
        overlays={overlays}
        offered={offered}
        onOverlay={toggle}
        since={page.rollup.since?.at}
      />
      <ChildrenView page={c} lens={shownLens} overlays={overlays} more={more} />
    </section>
  );
}

/** The keys this page answers: moving and stepping in only with nodes to move to, `w` only with a why. */
function KeysLine({ stops, out, why }: { stops: boolean; out: boolean; why: boolean }) {
  return (
    <div
      className="mt-6 flex flex-wrap gap-3 text-[11.5px] text-foreground-tertiary max-md:hidden"
      data-testid="v3-keys"
    >
      {stops && (
        <>
          <span>
            <Kbd>j</Kbd>
            <Kbd>k</Kbd> move
          </span>
          <span>
            <Kbd>↵</Kbd> or <Kbd>l</Kbd> step in
          </span>
        </>
      )}
      {out && (
        <span>
          <Kbd>h</Kbd> step out
        </span>
      )}
      {why && (
        <span>
          <Kbd>w</Kbd> why
        </span>
      )}
      <span>
        <Kbd>?</Kbd> all keys
      </span>
    </div>
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
export const VERB_ARGS: (keyof VerbArgs)[] = ["title", "member", "to"];

/**
 * The verb opened on this page: its preview shows under the header. Talk goes
 * to the chat; a verb the source cannot preview says so and changes nothing.
 * `?verb=start` (words in another node's chat) opens one once the page is in.
 */
function useOpenVerb(page: Page, talk?: NodeActions["onVerb"]) {
  const source = useNodeSource();
  const toast = useFactoryToast();
  const [open, setOpen] = useState<OpenVerb | null>(null);
  const onVerb = useCallback(
    (verb: VerbAvail, args?: VerbArgs) => {
      if (verb.verb === "talk") {
        talk?.(verb, args);
        return;
      }
      if (!verb.enabled) return;
      if (verb.verb !== "team" && !source?.preview) {
        toast.show({
          message: `${verb.label}: this server cannot preview it yet, so nothing changed.`,
        });
        return;
      }
      setOpen((prev) => (prev?.verb.verb === verb.verb && !args ? null : { verb, args }));
    },
    [source?.preview, talk, toast],
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
  page,
}: NodePageProps & { page: Page }) {
  const prefetch = usePrefetchNode(factoryId);
  const phone = useIsMobileViewport();
  const navigate = useNavigate();
  const body = useRef<HTMLDivElement>(null);
  const [whyOpen, setWhyOpen] = useState(false);
  const parent = [...page.breadcrumb].reverse().find((c) => !c.folded);
  const toggleWhy = useCallback(() => setWhyOpen((open) => !open), []);
  const why = hasWhy(page.status);
  const stops = useRovingStops(body);
  const verbs = useOpenVerb(page, actions?.onVerb);
  const sheets = useSheetActions();
  useMarkSeen(page.node.ref, factoryId);
  useNodeKeys(body, {
    onStepOut: parent ? () => navigate(nodeHref(factoryId, parent.ref)) : undefined,
    onWhy: why ? toggleWhy : undefined,
    onVerbKey: verbs.onKey,
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
  const aboveId = page.status.ends === "person" ? (page.status.decision?.id ?? null) : null;
  return (
    <NodeContextProvider value={value}>
      <AboveProvider aboveId={aboveId}>
        <div data-testid="v3-node-page" data-ref={page.node.ref} className="flex min-w-0 flex-col">
          {phone ? (
            <div className="mb-3">
              <PhoneLevelRow page={page} shown={shown} />
            </div>
          ) : (
            <div className="-mx-4 -mt-1 mb-[18px] md:-mx-6">
              <LevelStrip page={page} shown={shown} />
            </div>
          )}
          <div className={cn("grid min-w-0 gap-6", chat && "lg:grid-cols-[minmax(0,1fr)_auto]")}>
            <div ref={body} className="min-w-0">
              {notice}
              <NodeHeader page={page} whyOpen={whyOpen} onWhyChange={setWhyOpen} />
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
              {!level && <NodeContent node={page.node} />}
              <Below page={page} level={shown} explicit={Boolean(level)} />
              <KeysLine stops={stops} out={Boolean(parent)} why={why} />
            </div>
            {chat && <NodeChat page={page} sample={chat.sample} />}
          </div>
        </div>
      </AboveProvider>
    </NodeContextProvider>
  );
}
