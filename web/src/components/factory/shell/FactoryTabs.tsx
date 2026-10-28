/**
 * The factory's page tabs under the header (Overview, Room, Projects,
 * Activity) and, on the right, what waits on the viewer (decisions, alerts),
 * Settings and the ⌘K "Jump or act" button. Tabs scroll sideways on a narrow
 * screen rather than wrap; on a phone the bottom bar takes over.
 */

import type { ReactNode } from "react";
import { CommandIcon, SettingsIcon, TriangleAlertIcon } from "lucide-react";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { isMacPlatform } from "@/lib/hotkeys";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import {
  activityPageHref,
  factoryPageHref,
  projectsPageHref,
  roomPageHref,
  settingsPageHref,
} from "../factoryRoutes";
import { TONE_CHIP } from "../factoryFormat";
import type { FactoryLocation, FactoryPageKey } from "./shellRoutes";

export interface FactoryTab {
  key: string;
  label: string;
  href: (factoryId: string) => string;
  /** Pages that light this tab. */
  pages: FactoryPageKey[];
}

/** The tabs in order; later pages (Agents, Graph, Timeline, …) add theirs here. */
export const FACTORY_TABS: FactoryTab[] = [
  { key: "overview", label: "Overview", href: factoryPageHref, pages: ["overview"] },
  { key: "room", label: "Room", href: (fid) => roomPageHref(fid), pages: ["room"] },
  {
    key: "projects",
    label: "Projects",
    href: projectsPageHref,
    pages: ["projects", "project", "pr"],
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
  onPalette,
}: {
  snapshot: FactorySnapshot;
  location: FactoryLocation;
  /** Open decisions assigned to the viewer or their role. */
  mineCount: number;
  onPalette: () => void;
}) {
  const fid = snapshot.factory.id;
  const active = FACTORY_TABS.find((tab) => tab.pages.includes(location.page))?.key;
  const projects = snapshot.projects.filter((p) => p.stage !== "done").length;
  const alerts = snapshot.counts?.issues_open ?? 0;
  const openDecisions = snapshot.decisions.filter((d) => d.status === "open").length;
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
          </TabLink>
        ))}
      </div>
      <div className="flex shrink-0 items-center gap-2 pb-1 text-sm max-md:hidden">
        <Link
          to={`${factoryPageHref(fid)}#needs-you`}
          componentId="factory.tab.decisions"
          data-testid="factory-tab-decisions"
          title={`${openDecisions} open · ${mineCount} for you or your role`}
          className="inline-flex items-center gap-1.5 rounded-md px-1 text-muted-foreground hover:text-foreground"
        >
          Decisions
          {openDecisions > 0 && (
            <Count n={openDecisions} tone={mineCount > 0 ? "warning" : "muted"} />
          )}
        </Link>
        {alerts > 0 && (
          <span
            data-testid="factory-tab-alerts"
            className="inline-flex items-center gap-1 text-muted-foreground"
          >
            <TriangleAlertIcon aria-hidden className="size-3.5 text-warning" />
            Alerts <Count n={alerts} tone="warning" />
          </span>
        )}
        <Link
          to={settingsPageHref(fid)}
          componentId="factory.tab.settings"
          data-testid="factory-tab-settings"
          aria-label="Settings"
          title="Settings · People"
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
          className="inline-flex h-7 w-52 cursor-pointer items-center gap-2 rounded-md border border-border bg-muted/40 px-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none max-lg:w-auto"
        >
          <span className="max-lg:hidden">Jump or act…</span>
          <kbd className="ml-auto inline-flex items-center gap-0.5 rounded-sm border border-border bg-background px-1 font-sans text-xs">
            {mac ? <CommandIcon aria-hidden className="size-3" /> : "Ctrl "}K
          </kbd>
        </button>
      </div>
    </nav>
  );
}
