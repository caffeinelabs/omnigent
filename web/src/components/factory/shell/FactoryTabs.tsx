/**
 * The factory's page tabs under the header (Overview, Room, Projects, Agents,
 * Graph, Timeline, Activity) and, on the right, what waits on the viewer
 * (decisions, alerts), Settings and the ⌘K "Jump or act" button. Tabs scroll
 * sideways on a narrow screen rather than wrap; on a phone the bottom bar
 * takes over.
 *
 * The new cockpit has five places instead (`CockpitTabs`): Home (with the
 * amber Needs you count), Discovery, Projects, Teams and Steer (with a grey
 * count of proposals waiting).
 */

import type { ReactNode } from "react";
import {
  CommandIcon,
  CompassIcon,
  FolderIcon,
  HomeIcon,
  type LucideIcon,
  SearchIcon,
  SettingsIcon,
  SlidersHorizontalIcon,
  TriangleAlertIcon,
  UsersIcon,
} from "lucide-react";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { isMacPlatform } from "@/lib/hotkeys";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import {
  type CockpitPlace,
  activityPageHref,
  agentsPageHref,
  alertsPageHref,
  decisionsPageHref,
  discoveryPageHref,
  factoryPageHref,
  graphPageHref,
  improvementsPageHref,
  insightsPageHref,
  projectsPageHref,
  roomPageHref,
  settingsPageHref,
  steerPageHref,
  teamsPageHref,
  timelinePageHref,
} from "../factoryRoutes";
import { TONE_CHIP } from "../factoryFormat";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import type { FactoryLocation, FactoryPageKey } from "./shellRoutes";

export interface FactoryTab {
  key: string;
  label: string;
  href: (factoryId: string) => string;
  /** Pages that light this tab. */
  pages: FactoryPageKey[];
}

/** The tabs in order; later pages (Insights, Improvements, …) add theirs here. */
export const FACTORY_TABS: FactoryTab[] = [
  { key: "overview", label: "Overview", href: factoryPageHref, pages: ["overview"] },
  { key: "room", label: "Room", href: (fid) => roomPageHref(fid), pages: ["room"] },
  {
    key: "projects",
    label: "Projects",
    href: projectsPageHref,
    pages: ["projects", "project", "pr"],
  },
  {
    key: "agents",
    label: "Agents",
    href: (fid) => agentsPageHref(fid),
    pages: ["agents", "agent"],
  },
  { key: "graph", label: "Graph", href: (fid) => graphPageHref(fid), pages: ["graph"] },
  { key: "timeline", label: "Timeline", href: (fid) => timelinePageHref(fid), pages: ["timeline"] },
  { key: "insights", label: "Insights", href: (fid) => insightsPageHref(fid), pages: ["insights"] },
  {
    key: "improvements",
    label: "Improvements",
    href: (fid) => improvementsPageHref(fid),
    pages: ["improvements", "proposal"],
  },
  { key: "activity", label: "Activity", href: activityPageHref, pages: ["activity"] },
];

function TabLink({
  to,
  active,
  testId,
  children,
}: {
  to: string;
  active: boolean;
  testId: string;
  children: ReactNode;
}) {
  return (
    <Link
      to={to}
      componentId={`factory.tab.${testId}`}
      data-testid={testId}
      data-active={active ? "true" : "false"}
      aria-current={active ? "page" : undefined}
      className={cn(
        "relative inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md px-1 text-ui font-medium whitespace-nowrap transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
        "after:absolute after:inset-x-1 after:-bottom-px after:h-0.5 after:rounded-full after:bg-foreground after:opacity-0",
        active
          ? "text-foreground after:opacity-100"
          : "text-foreground/60 hover:text-foreground dark:text-muted-foreground dark:hover:text-foreground",
      )}
    >
      {children}
    </Link>
  );
}

function Count({ n, tone = "muted" }: { n: number; tone?: "muted" | "warning" }) {
  return (
    <span
      className={cn(
        "inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-xs font-medium tabular-nums",
        tone === "warning" ? TONE_CHIP.warning : "bg-muted text-muted-foreground",
      )}
    >
      {n}
    </span>
  );
}

export function FactoryTabs({
  snapshot,
  location,
  mineCount,
  roomUnread = 0,
  onPalette,
  onTryCockpit,
}: {
  snapshot: FactorySnapshot;
  location: FactoryLocation;
  /** Open decisions assigned to the viewer or their role. */
  mineCount: number;
  /** Unread Room messages across threads. */
  roomUnread?: number;
  onPalette: () => void;
  /** Offers "Try the new cockpit" (when the server lets people switch). */
  onTryCockpit?: () => void;
}) {
  const fid = snapshot.factory.id;
  const active = FACTORY_TABS.find((tab) => tab.pages.includes(location.page))?.key;
  const projects = snapshot.projects.filter((p) => p.stage !== "done").length;
  const alerts = snapshot.counts?.issues_open ?? 0;
  const openDecisions = snapshot.decisions.filter((d) => d.status === "open").length;
  const onTrial = snapshot.counts?.on_trial ?? 0;
  const mac = isMacPlatform();
  return (
    <nav
      aria-label="Factory pages"
      data-testid="factory-tabs"
      className="flex min-w-0 items-center gap-3 border-b border-border"
    >
      <div className="-mb-px flex min-w-0 flex-1 items-center gap-3 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {FACTORY_TABS.map((tab) => (
          <TabLink
            key={tab.key}
            to={tab.href(fid)}
            active={tab.key === active}
            testId={`factory-tab-${tab.key}`}
          >
            {tab.label}
            {tab.key === "projects" && projects > 0 && <Count n={projects} />}
            {tab.key === "improvements" && onTrial > 0 && (
              <span
                data-testid="factory-tab-on-trial"
                title={`${onTrial} definition change${onTrial === 1 ? "" : "s"} on trial`}
                className="text-xs font-normal text-muted-foreground tabular-nums"
              >
                · {onTrial} trial
              </span>
            )}
            {tab.key === "room" && roomUnread > 0 && (
              <span data-testid="factory-tab-room-unread">
                <Count n={roomUnread} />
              </span>
            )}
          </TabLink>
        ))}
      </div>
      <div className="flex shrink-0 items-center gap-2 pb-1 text-sm max-md:hidden">
        {onTryCockpit && (
          <button
            type="button"
            onClick={onTryCockpit}
            data-testid="factory-try-cockpit"
            className="cursor-pointer rounded-md px-1 text-muted-foreground underline underline-offset-2 hover:text-foreground"
          >
            Try the new cockpit
          </button>
        )}
        <Link
          to={decisionsPageHref(fid)}
          componentId="factory.tab.decisions"
          data-testid="factory-tab-decisions"
          title={`${openDecisions} open · ${mineCount} for you or your role`}
          aria-current={location.page === "decisions" ? "page" : undefined}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md px-1 hover:text-foreground",
            location.page === "decisions" ? "text-foreground" : "text-muted-foreground",
          )}
        >
          Decisions
          {openDecisions > 0 && (
            <Count n={openDecisions} tone={mineCount > 0 ? "warning" : "muted"} />
          )}
        </Link>
        <Link
          to={alertsPageHref(fid)}
          componentId="factory.tab.alerts"
          data-testid="factory-tab-alerts"
          aria-current={["alerts", "incident"].includes(location.page) ? "page" : undefined}
          className={cn(
            "inline-flex items-center gap-1 rounded-md px-1 hover:text-foreground",
            ["alerts", "incident"].includes(location.page)
              ? "text-foreground"
              : "text-muted-foreground",
          )}
        >
          {alerts > 0 && <TriangleAlertIcon aria-hidden className="size-3.5 text-warning" />}
          Alerts
          {alerts > 0 && <Count n={alerts} tone="warning" />}
        </Link>
        <Link
          to={settingsPageHref(fid)}
          componentId="factory.tab.settings"
          data-testid="factory-tab-settings"
          aria-label="Settings"
          title="Settings"
          aria-current={location.page === "settings" ? "page" : undefined}
          className={cn(
            "inline-flex size-7 items-center justify-center rounded-md hover:bg-muted hover:text-foreground",
            location.page === "settings" ? "text-foreground" : "text-muted-foreground",
          )}
        >
          <SettingsIcon className="size-4" />
        </Link>
        <button
          type="button"
          onClick={onPalette}
          data-testid="factory-palette-open"
          aria-label="Jump or act"
          title="Jump or act…"
          className="inline-flex h-7 w-52 cursor-pointer items-center gap-2 rounded-md border border-border bg-muted/40 px-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none max-2xl:w-auto"
        >
          {/* Compact below 1536 px, so the page tabs keep their room beside the Room dock. */}
          <SearchIcon aria-hidden className="size-3.5 2xl:hidden" />
          <span className="max-2xl:hidden">Jump or act…</span>
          <kbd className="ml-auto inline-flex items-center gap-0.5 rounded-sm border border-border bg-background px-1 font-sans text-xs">
            {mac ? <CommandIcon aria-hidden className="size-3" /> : "Ctrl "}K
          </kbd>
        </button>
      </div>
    </nav>
  );
}

// ── The new cockpit's five places ──────────────────────────────

export interface CockpitTab {
  key: CockpitPlace;
  label: string;
  icon: LucideIcon;
  href: (factoryId: string) => string;
  /** Pages that light this tab. */
  pages: FactoryPageKey[];
}

/** Home, Discovery, Projects, Teams, Steer (Debug and Settings sit behind the avatar). */
export const COCKPIT_TABS: CockpitTab[] = [
  { key: "home", label: "Home", icon: HomeIcon, href: factoryPageHref, pages: ["overview"] },
  {
    key: "discovery",
    label: "Discovery",
    icon: CompassIcon,
    href: (fid) => discoveryPageHref(fid),
    pages: ["discovery"],
  },
  {
    key: "projects",
    label: "Projects",
    icon: FolderIcon,
    href: projectsPageHref,
    pages: ["projects", "project", "pr"],
  },
  {
    key: "teams",
    label: "Teams",
    icon: UsersIcon,
    href: (fid) => teamsPageHref(fid),
    pages: ["teams", "agents", "agent"],
  },
  {
    key: "steer",
    label: "Steer",
    icon: SlidersHorizontalIcon,
    href: (fid) => steerPageHref(fid),
    pages: ["steer", "improvements", "proposal", "insights"],
  },
];

/** The place a page belongs to, if any (Debug and Settings belong to none). */
export function cockpitPlaceOf(location: FactoryLocation): CockpitPlace | null {
  return COCKPIT_TABS.find((tab) => tab.pages.includes(location.page))?.key ?? null;
}

/** A tab's count: amber for what needs you, grey for what merely waits. */
export function PlaceCount({
  n,
  tone,
  label,
  testId,
}: {
  n: number;
  tone: "needsYou" | "muted";
  label: string;
  testId?: string;
}) {
  if (n <= 0) return null;
  return (
    <span
      data-testid={testId}
      aria-label={label}
      className={cn(
        "inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full px-1 text-xs font-semibold tabular-nums",
        tone === "needsYou"
          ? cn(MEANING.needsYou.surface, MEANING.needsYou.text)
          : "bg-muted text-muted-foreground",
      )}
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}

export function CockpitTabs({
  factoryId,
  location,
  needsYou,
  proposals,
  className,
}: {
  factoryId: string;
  location: FactoryLocation;
  /** What waits on the viewer (Home's amber count). */
  needsYou: number;
  /** Proposals to change how the factory works, waiting for a person (Steer's grey count). */
  proposals: number;
  className?: string;
}) {
  const active = cockpitPlaceOf(location);
  return (
    <nav
      aria-label="Cockpit"
      data-testid="cockpit-tabs"
      className={cn(
        "flex min-w-0 items-center gap-0.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
        className,
      )}
    >
      {COCKPIT_TABS.map(({ key, label, icon: Icon, href }) => {
        const on = key === active;
        return (
          <Link
            key={key}
            to={href(factoryId)}
            componentId={`factory.cockpit.tab.${key}`}
            data-testid={`cockpit-tab-${key}`}
            aria-current={on ? "page" : undefined}
            className={cn(
              "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2 text-ui font-medium whitespace-nowrap transition-colors",
              FOCUS_RING,
              on
                ? "bg-muted text-foreground"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
          >
            <Icon aria-hidden className="size-4" />
            {/* A narrow bar keeps the icons; the name stays for screen readers. */}
            <span className="@max-[900px]:sr-only">{label}</span>
            {key === "home" && (
              <PlaceCount
                n={needsYou}
                tone="needsYou"
                label={`${needsYou} need${needsYou === 1 ? "s" : ""} you`}
                testId="cockpit-tab-home-count"
              />
            )}
            {key === "steer" && (
              <PlaceCount
                n={proposals}
                tone="muted"
                label={`${proposals} proposal${proposals === 1 ? "" : "s"} waiting`}
                testId="cockpit-tab-steer-count"
              />
            )}
          </Link>
        );
      })}
    </nav>
  );
}

/** Cockpit v3's two trees: the product and the factory itself (with what waits on the viewer there). */
export interface TreeSwitchProps {
  current: "product" | "factory";
  productHref: string;
  factoryHref: string;
  /** Asks in the factory tree (rules, team, environments): the grey count on Factory. */
  factoryNeeds: number;
  className?: string;
}

/** "Product | Factory 3": one tree at a time, never both on one page. */
export function TreeSwitch({
  current,
  productHref,
  factoryHref,
  factoryNeeds,
  className,
}: TreeSwitchProps) {
  const item = (key: "product" | "factory", label: string, href: string) => {
    const on = key === current;
    return (
      <Link
        to={href}
        componentId={`factory.v3.tree.${key}`}
        data-testid={`cockpit-tree-${key}`}
        aria-current={on ? "page" : undefined}
        className={cn(
          "inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-ui font-medium whitespace-nowrap",
          FOCUS_RING,
          on
            ? "bg-card-solid text-foreground shadow-sm ring-1 ring-border"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        {label}
        {key === "factory" && (
          <PlaceCount
            n={factoryNeeds}
            tone="muted"
            label={`${factoryNeeds} need${factoryNeeds === 1 ? "s" : ""} you in the factory`}
            testId="cockpit-tree-factory-count"
          />
        )}
      </Link>
    );
  };
  return (
    <nav
      aria-label="Tree"
      data-testid="cockpit-trees"
      className={cn(
        "inline-flex shrink-0 items-center gap-0.5 rounded-lg border border-border bg-muted/40 p-0.5",
        className,
      )}
    >
      {item("product", "Product", productHref)}
      {item("factory", "Factory", factoryHref)}
    </nav>
  );
}
