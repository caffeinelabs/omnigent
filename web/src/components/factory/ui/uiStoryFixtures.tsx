// Story-only frame and sample data for the factory UI primitives: a router
// (links render as links), the toast provider, a theme forced on the whole
// document so portalled panels follow the story, and sample data. Every
// product-like string is marked "(sample)"; none of it ships in the cockpit.

import { type ReactNode, useContext, useLayoutEffect } from "react";
import { MemoryRouter } from "react-router-dom";
import { ForceThemeContext } from "@/components/theme/ThemeProvider";
import { cn } from "@/lib/utils";
import type { ChainCount } from "./ChainCounts";
import type { LoopGoal, LoopStage } from "./LoopStrip";
import type { MemberIdentity } from "./MemberAvatar";
import type { SourceCount } from "./SourceMix";
import type { StripSegment } from "./StateStrip";
import { FactoryToastProvider } from "./Toast";
import type { WhyLink } from "./WhyChain";

export type StoryTheme = "light" | "dark";

/** Forces the document theme while mounted (the provider would reset a bare class). */
function DocumentTheme({ theme }: { theme: StoryTheme }) {
  const force = useContext(ForceThemeContext);
  useLayoutEffect(() => {
    force?.(theme);
    const root = document.documentElement;
    const had = root.classList.contains("dark");
    const apply = () => {
      const dark = root.classList.contains("dark");
      if (theme === "dark" && !dark) root.classList.add("dark");
      if (theme === "light" && dark) root.classList.remove("dark");
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => {
      observer.disconnect();
      force?.(undefined);
      root.classList.toggle("dark", had);
    };
  }, [theme, force]);
  return null;
}

export interface UiFrameProps {
  theme?: StoryTheme;
  /** Fill the story viewport (for fixed bars and panels). */
  viewport?: boolean;
  className?: string;
  children: ReactNode;
}

/** One primitive story: themed surface, router and toast region. */
export function UiFrame({ theme = "light", viewport = false, className, children }: UiFrameProps) {
  return (
    <MemoryRouter>
      <DocumentTheme theme={theme} />
      <FactoryToastProvider>
        <div
          className={cn(theme === "dark" && "dark", viewport && "fixed inset-0")}
          data-theme-frame={theme}
        >
          <div
            data-testid="ui-story-frame"
            className={cn(
              "bg-background p-4 text-ui text-foreground",
              viewport ? "h-full w-full overflow-auto" : "rounded-lg",
              className,
            )}
          >
            {children}
          </div>
        </div>
      </FactoryToastProvider>
    </MemoryRouter>
  );
}

/** A labelled row in a gallery story. */
export function Specimen({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-sm text-muted-foreground">{label}</span>
      <div className="flex flex-wrap items-center gap-3">{children}</div>
    </div>
  );
}

// ── Sample data ────────────────────────────────────────────────

const H = 3600;

/** The three lead seats (sample handles). */
export const SAMPLE_LEADS = {
  discovery: {
    name: "Scout",
    role: "lead",
    seat: "discovery",
    vendor: "claude",
    model: "sonnet",
    state: "working",
  },
  portfolio: {
    name: "Atlas",
    role: "lead",
    seat: "portfolio",
    vendor: "claude",
    model: "opus",
    state: "idle",
  },
  reliability: {
    name: "Sentinel",
    role: "lead",
    seat: "reliability",
    vendor: "claude",
    model: "opus",
    state: "working",
  },
} satisfies Record<string, MemberIdentity>;

/** A project team in every common state (sample handles). */
export const SAMPLE_TEAM: MemberIdentity[] = [
  { name: "Nova", role: "project_lead", vendor: "claude", model: "opus", state: "idle" },
  { name: "Rex", role: "ic", vendor: "claude", model: "sonnet", state: "working" },
  { name: "Kai", role: "ic", vendor: "codex", model: "gpt-5-codex", state: "working" },
  { name: "Iris", role: "reviewer", vendor: "codex", model: "gpt-5-codex", state: "waiting" },
  { name: "Dot", role: "demo", vendor: "claude", model: "sonnet", state: "stuck" },
  { name: "Bo", role: "env_keeper", vendor: "claude", model: "sonnet", state: "dead" },
];

export const SAMPLE_SOURCES: SourceCount[] = [
  { tag: "forum", count: 11 },
  { tag: "web", count: 9 },
  { tag: "trk", count: 3 },
  { tag: "team", count: 1, hunch: true },
];

/** A stuck agent's last day: idle, two working stretches, then stuck. */
export const SAMPLE_STRIP_STUCK: StripSegment[] = [
  { state: "idle", seconds: 6 * H },
  { state: "working", seconds: 3 * H },
  { state: "idle", seconds: 5 * H },
  { state: "working", seconds: 3 * H },
  { state: "waiting_ci", seconds: 1 * H },
  { state: "stuck", seconds: 2 * H },
  { state: "working", seconds: 1 * H },
  { state: "stuck", seconds: 3 * H },
];

/** A builder's busy day, with a wait on a person. */
export const SAMPLE_STRIP_BUSY: StripSegment[] = [
  { state: "working", seconds: 9 * H },
  { state: "waiting_ci", seconds: 2 * H },
  { state: "waiting", seconds: 1 * H },
  { state: "working", seconds: 7 * H },
  { state: "idle", seconds: 4 * H },
  { state: "working", seconds: 1 * H },
];

/** Work that came from evidence, all the way to a readout. */
export const SAMPLE_CHAIN_COMPLETE: WhyLink[] = [
  { kind: "evidence", value: "41 accounts", detail: "forum 22 · trk 12 · web 7" },
  { kind: "opportunity", value: "O-3 · moderate", detail: "score .31", onSelect: () => undefined },
  { kind: "bet", value: "C-24", detail: "expects 2 to 6 pp", onSelect: () => undefined },
  { kind: "project", value: "retry-build (sample)", detail: "Shipped", to: "/factory" },
  { kind: "readout", value: "day 7 · won", detail: "actual 4.1 pp", onSelect: () => undefined },
];

/** A bet that waits for a person; nothing built or measured yet. */
export const SAMPLE_CHAIN_WAITING: WhyLink[] = [
  { kind: "evidence", value: "23 accounts", detail: "forum 11 · web 9 · trk 3 · 1 hunch" },
  { kind: "opportunity", value: "O-15 · moderate", detail: "score .23", onSelect: () => undefined },
  {
    kind: "bet",
    value: "C-31 · waits for you",
    detail: "expects 1 to 4 pp",
    waiting: true,
    onSelect: () => undefined,
  },
  { kind: "project", value: "none yet", missing: true },
  { kind: "readout", value: "not yet", detail: "day 7 and 28 after release", missing: true },
];

/** Requested work: no evidence, no metric, never measured. */
export const SAMPLE_CHAIN_REQUESTED: WhyLink[] = [
  { kind: "evidence", value: "none", detail: "no user evidence", missing: true },
  { kind: "opportunity", value: "none", detail: "not linked", missing: true },
  { kind: "bet", value: "none", detail: "no metric or range", missing: true },
  { kind: "project", value: "copy-env-name (sample)", detail: "Ready for merge", to: "/factory" },
  { kind: "readout", value: "not measured", detail: "requested work has no metric", missing: true },
];

export const SAMPLE_GOAL: LoopGoal = {
  id: "G-1",
  statement: "More new users reach a working first result in week one (sample)",
  measured: false,
  note: "No source can measure it yet. An analytics source waits for approval.",
};

/** The loop of a factory with discovery on (sample counts). */
export const SAMPLE_LOOP: LoopStage[] = [
  {
    key: "sense",
    count: 214,
    unit: "signals this week",
    detail: "4 sources on",
    waiting: "1 waits for you",
    owner: SAMPLE_LEADS.discovery,
    to: "/factory",
  },
  {
    key: "make_sense",
    count: 8,
    unit: "opportunities",
    detail: "2 in Now · 37 signals unsorted",
    owner: SAMPLE_LEADS.discovery,
    to: "/factory",
  },
  {
    key: "decide",
    count: 3,
    unit: "bets",
    waiting: "1 waits for you",
    detail: "1 co-signing",
    owner: SAMPLE_LEADS.portfolio,
    to: "/factory",
  },
  {
    key: "build",
    count: 6,
    unit: "projects",
    detail: "1 from evidence · 4 requested",
    owner: SAMPLE_LEADS.portfolio,
    to: "/factory",
  },
  {
    key: "measure",
    count: 1,
    unit: "readout",
    detail: "1 won · requested work is not measured",
    owner: SAMPLE_LEADS.reliability,
    to: "/factory",
  },
];

/** A factory that just turned discovery on: honest zeros, no goal. */
export const SAMPLE_LOOP_COLD: LoopStage[] = [
  {
    key: "sense",
    count: 64,
    unit: "signals this week",
    detail: "3 sources on · trial day 2 of 14",
    owner: SAMPLE_LEADS.discovery,
    to: "/factory",
  },
  {
    key: "make_sense",
    count: 0,
    unit: "opportunities",
    detail: "first synthesis Thursday",
    owner: SAMPLE_LEADS.discovery,
    to: "/factory",
  },
  {
    key: "decide",
    count: 0,
    unit: "bets",
    detail: "Atlas plans from the top opportunities",
    owner: SAMPLE_LEADS.portfolio,
    to: "/factory",
  },
  {
    key: "build",
    count: 2,
    unit: "projects",
    detail: "0 from evidence · 2 requested",
    owner: SAMPLE_LEADS.portfolio,
    to: "/factory",
  },
  {
    key: "measure",
    count: 0,
    unit: "readouts",
    detail: "nothing shipped from a bet yet",
    owner: SAMPLE_LEADS.reliability,
    to: "/factory",
  },
];

export const SAMPLE_CHAIN_COUNTS: ChainCount[] = [
  { key: "signals", count: 214, label: "signals this week" },
  { key: "opportunities", count: 8, label: "opportunities" },
  { key: "bets", count: 3, label: "bets", waiting: true },
  { key: "building", count: 1, label: "building" },
  { key: "shipped", count: 1, label: "shipped" },
  { key: "measured", count: 1, label: "measured" },
];

export const SAMPLE_CHAIN_COUNTS_COLD: ChainCount[] = [
  { key: "signals", count: 64, label: "signals this week" },
  { key: "opportunities", count: 0, label: "opportunities" },
  { key: "bets", count: 0, label: "bets" },
  { key: "building", count: 0, label: "building" },
  { key: "shipped", count: 0, label: "shipped" },
  { key: "measured", count: 0, label: "measured" },
];

/** Shows a story at 390 × 844 in Storybook's viewport toolbar (captures use a 390 px window). */
export const PHONE = {
  parameters: {
    viewport: {
      options: {
        phone390: {
          name: "Phone 390",
          styles: { width: "390px", height: "844px" },
          type: "mobile",
        },
      },
    },
  },
  globals: { viewport: { value: "phone390", isRotated: false } },
};
