// The cockpit's one way around, always in view: a sidebar with the four
// places (Home, Demos, Chats, Decisions) and Search, then an outline of the
// work (features, each opening to its projects) with the page's node marked.
// On a phone the places sit in a bottom tab bar and the outline is Home's
// feature list. Counts are what needs this person: unread chats, open asks.

import { type ReactNode, useEffect, useMemo, useState } from "react";
import {
  ChevronRightIcon,
  ClapperboardIcon,
  CommandIcon,
  FactoryIcon,
  HomeIcon,
  MessagesSquareIcon,
  MoonIcon,
  SearchIcon,
  SettingsIcon,
  SunIcon,
  VoteIcon,
  WrenchIcon,
} from "lucide-react";
import { useTheme } from "next-themes";
import { useResolvedThemeMode } from "@/components/theme/useResolvedThemeMode";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { isMacPlatform } from "@/lib/hotkeys";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { debugPageHref, factoryPageHref, settingsPageHref } from "../factoryRoutes";
import { FactorySwitcher } from "../shell/FactoryTopBar";
import { FOCUS_RING } from "../ui/statusTokens";
import { nodeName } from "../v3/calm/calmModel";
import { StateDot } from "../v3/nodeParts";
import { useLevel, useNode, usePrefetchNode } from "../v3/nodeQueries";
import { FX_REF, PRODUCT_REF, nodeHref } from "../v3/nodeRoutes";
import type { NodeSummary } from "../v3/nodeTypes";
import { type CockpitPlace, chatsHref, decisionsHref, demosHref } from "./cockpitRoutes";

export interface NavCounts {
  /** Asks waiting on this person (Decisions). */
  decisions: number;
  /** Unread messages across the threads (Chats). */
  unread: number;
}

const PLACES: {
  place: CockpitPlace;
  label: string;
  icon: typeof HomeIcon;
  href: (fid: string) => string;
}[] = [
  { place: "home", label: "Home", icon: HomeIcon, href: factoryPageHref },
  { place: "demos", label: "Demos", icon: ClapperboardIcon, href: (fid) => demosHref(fid) },
  { place: "chats", label: "Chats", icon: MessagesSquareIcon, href: (fid) => chatsHref(fid) },
  { place: "decisions", label: "Decisions", icon: VoteIcon, href: decisionsHref },
];

function Badge({ n, tone, testId }: { n: number; tone: "amber" | "blue"; testId?: string }) {
  if (n <= 0) return null;
  return (
    <span
      data-testid={testId}
      className={cn(
        "ml-auto inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full px-1.5 text-[11px] font-semibold tabular-nums",
        tone === "amber"
          ? "bg-status-yellow/20 text-status-yellow-fg"
          : "bg-status-blue text-white",
      )}
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}

function countOf(place: CockpitPlace, counts: NavCounts): { n: number; tone: "amber" | "blue" } {
  if (place === "decisions") return { n: counts.decisions, tone: "amber" };
  if (place === "chats") return { n: counts.unread, tone: "blue" };
  return { n: 0, tone: "blue" };
}

const ROW =
  "flex h-8 min-w-0 items-center gap-2.5 rounded-md px-2 text-[13.5px] transition-colors hover:bg-muted";

function SearchButton({ onSearch }: { onSearch: () => void }) {
  const mac = isMacPlatform();
  return (
    <button
      type="button"
      onClick={onSearch}
      data-testid="cockpit-nav-search"
      className={cn(ROW, "w-full cursor-pointer text-muted-foreground", FOCUS_RING)}
    >
      <SearchIcon aria-hidden className="size-4 shrink-0" />
      Search
      <kbd className="ml-auto inline-flex items-center gap-0.5 rounded border border-border bg-background px-1 font-sans text-[11px] text-foreground-tertiary">
        {mac ? <CommandIcon aria-hidden className="size-3" /> : "Ctrl "}K
      </kbd>
    </button>
  );
}

function ThemeRow() {
  const { setTheme } = useTheme();
  const mode = useResolvedThemeMode();
  const next = mode === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      data-testid="cockpit-nav-theme"
      className={cn(ROW, "w-full cursor-pointer text-muted-foreground", FOCUS_RING)}
    >
      {mode === "dark" ? (
        <SunIcon aria-hidden className="size-4 shrink-0" />
      ) : (
        <MoonIcon aria-hidden className="size-4 shrink-0" />
      )}
      {mode === "dark" ? "Light theme" : "Dark theme"}
    </button>
  );
}

// ── The outline of the work ─────────────────────────────────────

/** Where the page's node sits: its feature and project refs (from its breadcrumb). */
function useHere(nodeRef: string | null): { feature?: string; project?: string; ref?: string } {
  const page = useNode(nodeRef ?? "").data;
  return useMemo(() => {
    if (!nodeRef || !page) return nodeRef ? { ref: nodeRef } : {};
    const chain = [...page.breadcrumb, { ref: page.node.ref, level: page.node.level }];
    return {
      ref: page.node.ref,
      feature: chain.find((c) => c.level === "feature")?.ref,
      project: chain.find((c) => c.level === "project")?.ref,
    };
  }, [nodeRef, page]);
}

function OutlineLink({
  fid,
  node,
  depth,
  current,
  children,
}: {
  fid: string;
  node: NodeSummary;
  depth: 0 | 1;
  current: boolean;
  children?: ReactNode;
}) {
  const prefetch = usePrefetchNode(fid);
  const needs = node.needs?.open ?? 0;
  return (
    <Link
      to={nodeHref(fid, node.ref)}
      componentId="factory.cockpit.outline"
      data-testid="cockpit-outline-node"
      data-ref={node.ref}
      aria-current={current ? "page" : undefined}
      onMouseEnter={() => prefetch(node.ref)}
      onFocus={() => prefetch(node.ref)}
      title={node.full_title ?? node.title}
      className={cn(
        "flex h-7 min-w-0 flex-1 items-center gap-2 rounded-md pr-2 text-[13px] hover:bg-muted",
        depth === 0 ? "pl-1" : "pl-7",
        current ? "bg-muted font-semibold text-foreground" : "text-muted-foreground",
        node.state === "done" && !current && "text-foreground-tertiary",
        FOCUS_RING,
      )}
    >
      {children}
      <StateDot state={node.state} />
      <span className="min-w-0 flex-1 truncate">{nodeName(node)}</span>
      {needs > 0 && (
        <span
          title={`${needs} need${needs === 1 ? "s" : ""} you`}
          className="text-[11px] font-semibold text-status-yellow-fg tabular-nums"
        >
          {needs}
        </span>
      )}
    </Link>
  );
}

function FeatureProjects({
  fid,
  feature,
  here,
}: {
  fid: string;
  feature: string;
  here: string | undefined;
}) {
  const page = useNode(feature, fid);
  const items = page.data?.children.items ?? [];
  if (page.isPending) {
    return <div aria-busy="true" className="ml-7 h-6 w-32 animate-pulse rounded bg-muted" />;
  }
  if (!items.length) {
    return <p className="py-0.5 pl-7 text-xs text-foreground-tertiary">Nothing planned yet</p>;
  }
  return (
    <ul>
      {items.map((p) => (
        <li key={p.ref} className="flex">
          <OutlineLink fid={fid} node={p} depth={1} current={p.ref === here} />
        </li>
      ))}
    </ul>
  );
}

function FeatureRow({
  fid,
  node,
  here,
}: {
  fid: string;
  node: NodeSummary;
  here: { feature?: string; project?: string; ref?: string };
}) {
  const inside = here.feature === node.ref || here.project === node.ref;
  const [open, setOpen] = useState(inside);
  useEffect(() => {
    if (inside) setOpen(true);
  }, [inside]);
  // A project shown in place of its implicit feature has no projects to list.
  const opens = node.level === "feature";
  return (
    <li>
      <div className="flex min-w-0 items-center">
        {opens ? (
          <button
            type="button"
            aria-label={open ? `Close ${node.title}` : `Open ${node.title}`}
            aria-expanded={open}
            data-testid="cockpit-outline-toggle"
            onClick={() => setOpen((o) => !o)}
            className={cn(
              "grid size-6 shrink-0 cursor-pointer place-items-center rounded text-foreground-tertiary hover:bg-muted hover:text-foreground",
              FOCUS_RING,
            )}
          >
            <ChevronRightIcon
              aria-hidden
              className={cn("size-3.5 transition-transform", open && "rotate-90")}
            />
          </button>
        ) : (
          <span aria-hidden className="size-6 shrink-0" />
        )}
        <OutlineLink fid={fid} node={node} depth={0} current={here.ref === node.ref} />
      </div>
      {opens && open && (
        <FeatureProjects fid={fid} feature={node.ref} here={here.project ?? here.ref} />
      )}
    </li>
  );
}

/** Features (the product's home level), the open ones first, each opening to its projects. */
export function WorkOutline({ fid, nodeRef }: { fid: string; nodeRef: string | null }) {
  const level = useLevel(PRODUCT_REF, "feature", fid);
  const here = useHere(nodeRef);
  const [showDone, setShowDone] = useState(false);
  const all = useMemo(() => (level.data?.groups ?? []).flatMap((g) => g.items), [level.data]);
  const open = all.filter((n) => n.state !== "done");
  const done = all.filter((n) => n.state === "done");
  const doneHere = done.some((n) => n.ref === here.feature || n.ref === here.project);
  return (
    <nav aria-label="Work" data-testid="cockpit-outline" className="flex min-w-0 flex-col">
      <h2 className="px-2 pb-1 text-[11px] tracking-[0.07em] text-foreground-tertiary uppercase">
        Work
      </h2>
      {level.isPending ? (
        <div aria-busy="true" className="flex flex-col gap-1.5 px-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-5 animate-pulse rounded bg-muted" />
          ))}
        </div>
      ) : (
        <ul className="flex flex-col">
          {open.map((n) => (
            <FeatureRow key={n.ref} fid={fid} node={n} here={here} />
          ))}
          {done.length > 0 && (
            <li>
              <button
                type="button"
                onClick={() => setShowDone((s) => !s)}
                aria-expanded={showDone || doneHere}
                data-testid="cockpit-outline-done"
                className={cn(
                  "flex h-7 w-full cursor-pointer items-center gap-1 rounded-md px-1 text-[12.5px] text-foreground-tertiary hover:bg-muted",
                  FOCUS_RING,
                )}
              >
                <ChevronRightIcon
                  aria-hidden
                  className={cn(
                    "size-3.5 transition-transform",
                    (showDone || doneHere) && "rotate-90",
                  )}
                />
                Done · {done.length}
              </button>
            </li>
          )}
          {(showDone || doneHere) &&
            done.map((n) => <FeatureRow key={n.ref} fid={fid} node={n} here={here} />)}
        </ul>
      )}
    </nav>
  );
}

// ── The sidebar and the phone's tab bar ─────────────────────────

export function CockpitSidebar({
  snapshot,
  place,
  nodeRef,
  counts,
  factoryAsks,
  productName,
  onSearch,
  onShare,
  onAddProject,
  style,
}: {
  snapshot: FactorySnapshot;
  place: CockpitPlace | null;
  /** The node page's ref (the outline marks it), else null. */
  nodeRef: string | null;
  counts: NavCounts;
  /** Asks in the factory tree (how the factory runs). */
  factoryAsks: number;
  productName: string | null;
  onSearch: () => void;
  onShare: () => void;
  onAddProject: () => void;
  style?: React.CSSProperties;
}) {
  const fid = snapshot.factory.id;
  const inFx = nodeRef === FX_REF || nodeRef?.startsWith("fx:");
  return (
    <aside
      aria-label="Cockpit"
      data-testid="cockpit-sidebar"
      className="sticky flex min-h-0 flex-col gap-3 overflow-y-auto overscroll-contain pr-1 pb-2"
      style={style}
    >
      <div className="-ml-1.5">
        <FactorySwitcher
          snapshot={snapshot}
          productName={productName}
          compact
          onShare={onShare}
          onAddProject={onAddProject}
        />
      </div>
      <nav aria-label="Places" className="flex flex-col gap-0.5">
        {PLACES.map(({ place: p, label, icon: Icon, href }) => {
          const { n, tone } = countOf(p, counts);
          const active = place === p;
          return (
            <Link
              key={p}
              to={href(fid)}
              componentId={`factory.cockpit.nav.${p}`}
              data-testid={`cockpit-nav-${p}`}
              aria-current={active ? "page" : undefined}
              className={cn(
                ROW,
                active ? "bg-muted font-semibold text-foreground" : "text-muted-foreground",
                FOCUS_RING,
              )}
            >
              <Icon aria-hidden className="size-4 shrink-0" />
              {label}
              <Badge n={n} tone={tone} testId={`cockpit-nav-${p}-count`} />
            </Link>
          );
        })}
        <SearchButton onSearch={onSearch} />
      </nav>
      <div className="border-t border-border pt-3">
        <WorkOutline fid={fid} nodeRef={nodeRef} />
      </div>
      <div className="mt-auto flex flex-col gap-0.5 border-t border-border pt-2">
        <Link
          to={nodeHref(fid, FX_REF)}
          componentId="factory.cockpit.nav.factory"
          data-testid="cockpit-nav-factory"
          aria-current={inFx ? "page" : undefined}
          title="How the factory runs: its agents, rules and health"
          className={cn(
            ROW,
            inFx ? "bg-muted text-foreground" : "text-muted-foreground",
            FOCUS_RING,
          )}
        >
          <FactoryIcon aria-hidden className="size-4 shrink-0" />
          The factory
          <Badge n={factoryAsks} tone="amber" />
        </Link>
        <Link
          to={settingsPageHref(fid, "people")}
          componentId="factory.cockpit.nav.settings"
          data-testid="cockpit-nav-settings"
          className={cn(ROW, "text-muted-foreground", FOCUS_RING)}
        >
          <SettingsIcon aria-hidden className="size-4 shrink-0" />
          Settings
        </Link>
        <Link
          to={debugPageHref(fid, "fleet")}
          componentId="factory.cockpit.nav.debug"
          data-testid="cockpit-nav-debug"
          className={cn(ROW, "text-muted-foreground", FOCUS_RING)}
        >
          <WrenchIcon aria-hidden className="size-4 shrink-0" />
          Debug
        </Link>
        <ThemeRow />
      </div>
    </aside>
  );
}

/** The four places and Search on a phone, at the bottom of the screen. */
export function CockpitTabBar({
  fid,
  place,
  counts,
  onSearch,
}: {
  fid: string;
  place: CockpitPlace | null;
  counts: NavCounts;
  onSearch: () => void;
}) {
  return (
    <nav
      aria-label="Cockpit"
      data-testid="cockpit-phone-tabs"
      className="fixed inset-x-0 z-30 flex h-14 items-stretch border-t border-border bg-background/95 backdrop-blur-md"
      style={{ bottom: "var(--omnigent-inset-bottom, 0px)" }}
    >
      {PLACES.map(({ place: p, label, icon: Icon, href }) => {
        const { n, tone } = countOf(p, counts);
        const active = place === p;
        return (
          <Link
            key={p}
            to={href(fid)}
            componentId={`factory.cockpit.tab.${p}`}
            data-testid={`cockpit-tab-${p}`}
            aria-current={active ? "page" : undefined}
            className={cn(
              "relative flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 text-[11px]",
              active ? "text-foreground" : "text-muted-foreground",
            )}
          >
            <span className="relative">
              <Icon aria-hidden className="size-5" />
              {n > 0 && (
                <span
                  className={cn(
                    "absolute -top-1 -right-2.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold tabular-nums",
                    tone === "amber" ? "bg-status-yellow text-black" : "bg-status-blue text-white",
                  )}
                >
                  {n > 99 ? "99+" : n}
                </span>
              )}
            </span>
            {label}
          </Link>
        );
      })}
      <button
        type="button"
        onClick={onSearch}
        data-testid="cockpit-tab-search"
        className="flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] text-muted-foreground"
      >
        <SearchIcon aria-hidden className="size-5" />
        Search
      </button>
    </nav>
  );
}
