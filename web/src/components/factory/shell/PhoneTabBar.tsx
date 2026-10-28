/**
 * The factory's bottom tabs on a phone: Room · Needs you · Projects · More.
 * More opens a sheet with the other pages and the shell's actions (Share,
 * Jump or act). It sits above the OS inset; pages leave room for it.
 */

import { useState } from "react";
import {
  ActivityIcon,
  BellIcon,
  BotIcon,
  CommandIcon,
  EllipsisIcon,
  FolderKanbanIcon,
  GanttChartIcon,
  GitForkIcon,
  LayoutDashboardIcon,
  ListChecksIcon,
  MessagesSquareIcon,
  SettingsIcon,
  Share2Icon,
  SirenIcon,
} from "lucide-react";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Link, useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";
import {
  activityPageHref,
  agentsPageHref,
  alertsPageHref,
  decisionsPageHref,
  factoryPageHref,
  graphPageHref,
  projectsPageHref,
  roomPageHref,
  settingsPageHref,
  timelinePageHref,
} from "../factoryRoutes";
import type { FactoryLocation } from "./shellRoutes";

/** Height the bar takes above the inset (pages pad their bottom by it). */
export const PHONE_TAB_BAR_REM = 3.5;

function Tab({
  to,
  label,
  icon: Icon,
  active,
  badge,
  tone = "warning",
  testId,
}: {
  to: string;
  label: string;
  icon: typeof BellIcon;
  active: boolean;
  badge?: number;
  tone?: "warning" | "primary";
  testId: string;
}) {
  return (
    <Link
      to={to}
      componentId={`factory.phone.${testId}`}
      data-testid={testId}
      aria-current={active ? "page" : undefined}
      className={cn(
        "relative flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 text-xs",
        active ? "text-foreground" : "text-muted-foreground",
      )}
    >
      <span className="relative">
        <Icon aria-hidden className="size-5" />
        {badge != null && badge > 0 && (
          <span
            data-testid={`${testId}-badge`}
            className={cn(
              "absolute -top-1 -right-2.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-medium tabular-nums",
              tone === "primary" ? "bg-primary text-primary-foreground" : "bg-warning text-white",
            )}
          >
            {badge}
          </span>
        )}
      </span>
      {label}
    </Link>
  );
}

function MoreLink({
  to,
  icon: Icon,
  children,
  onDone,
}: {
  to: string;
  icon: typeof BellIcon;
  children: string;
  onDone: () => void;
}) {
  return (
    <Link
      to={to}
      onClick={onDone}
      componentId="factory.phone.more_link"
      className="flex items-center gap-3 rounded-lg px-2 py-2.5 text-ui hover:bg-muted"
    >
      <Icon aria-hidden className="size-4 text-muted-foreground" />
      {children}
    </Link>
  );
}

export function PhoneTabBar({
  factoryId,
  location,
  needsYou,
  roomUnread = 0,
  onShare,
  onPalette,
}: {
  factoryId: string;
  location: FactoryLocation;
  /** What waits on the viewer (the Needs you badge). */
  needsYou: number;
  /** Unread Room messages (the Room tab's badge). */
  roomUnread?: number;
  onShare: () => void;
  onPalette: () => void;
}) {
  const [more, setMore] = useState(false);
  const navigate = useNavigate();
  const close = () => setMore(false);
  return (
    <>
      <nav
        aria-label="Factory"
        data-testid="factory-phone-tabs"
        className="fixed inset-x-0 z-30 flex h-14 items-stretch border-t border-border bg-background/95 backdrop-blur-md md:hidden"
        style={{ bottom: "var(--omnigent-inset-bottom, 0px)" }}
      >
        <Tab
          to={roomPageHref(factoryId)}
          label="Room"
          icon={MessagesSquareIcon}
          active={location.page === "room"}
          badge={roomUnread}
          tone="primary"
          testId="factory-phone-room"
        />
        <Tab
          to={`${factoryPageHref(factoryId)}#needs-you`}
          label="Needs you"
          icon={BellIcon}
          active={location.page === "overview"}
          badge={needsYou}
          testId="factory-phone-needs-you"
        />
        <Tab
          to={projectsPageHref(factoryId)}
          label="Projects"
          icon={FolderKanbanIcon}
          active={["projects", "project", "pr"].includes(location.page)}
          testId="factory-phone-projects"
        />
        <button
          type="button"
          onClick={() => setMore(true)}
          data-testid="factory-phone-more"
          aria-expanded={more}
          className={cn(
            "flex min-w-0 flex-1 cursor-pointer flex-col items-center justify-center gap-0.5 text-xs",
            [
              "activity",
              "settings",
              "decisions",
              "graph",
              "agents",
              "agent",
              "timeline",
              "alerts",
              "incident",
              "other",
            ].includes(location.page)
              ? "text-foreground"
              : "text-muted-foreground",
          )}
        >
          <EllipsisIcon aria-hidden className="size-5" />
          More
        </button>
      </nav>
      <Sheet open={more} onOpenChange={setMore}>
        {/* A handful of rows: a sheet sized to them, not a full-screen page. */}
        <SheetContent
          side="bottom"
          size="md"
          data-testid="factory-phone-more-sheet"
          className="max-md:top-auto max-md:h-auto max-md:max-h-[85dvh] max-md:rounded-t-xl"
        >
          <SheetHeader>
            <SheetTitle>More</SheetTitle>
            <SheetDescription className="sr-only">Other factory pages and actions</SheetDescription>
          </SheetHeader>
          <SheetBody className="flex flex-col gap-0.5 pb-[calc(1rem+var(--omnigent-safe-bottom))]">
            <MoreLink to={factoryPageHref(factoryId)} icon={LayoutDashboardIcon} onDone={close}>
              Overview
            </MoreLink>
            <MoreLink to={agentsPageHref(factoryId)} icon={BotIcon} onDone={close}>
              Agents
            </MoreLink>
            <MoreLink to={decisionsPageHref(factoryId)} icon={ListChecksIcon} onDone={close}>
              Decisions
            </MoreLink>
            <MoreLink to={graphPageHref(factoryId)} icon={GitForkIcon} onDone={close}>
              Graph
            </MoreLink>
            <MoreLink to={timelinePageHref(factoryId)} icon={GanttChartIcon} onDone={close}>
              Timeline
            </MoreLink>
            <MoreLink to={alertsPageHref(factoryId)} icon={SirenIcon} onDone={close}>
              Alerts
            </MoreLink>
            <MoreLink to={activityPageHref(factoryId)} icon={ActivityIcon} onDone={close}>
              Activity
            </MoreLink>
            <MoreLink to={settingsPageHref(factoryId)} icon={SettingsIcon} onDone={close}>
              People & settings
            </MoreLink>
            <button
              type="button"
              onClick={() => {
                close();
                onShare();
              }}
              className="flex cursor-pointer items-center gap-3 rounded-lg px-2 py-2.5 text-left text-ui hover:bg-muted"
            >
              <Share2Icon aria-hidden className="size-4 text-muted-foreground" />
              Share
            </button>
            <button
              type="button"
              onClick={() => {
                close();
                onPalette();
              }}
              className="flex cursor-pointer items-center gap-3 rounded-lg px-2 py-2.5 text-left text-ui hover:bg-muted"
            >
              <CommandIcon aria-hidden className="size-4 text-muted-foreground" />
              Jump or act…
            </button>
            <button
              type="button"
              onClick={() => {
                close();
                navigate("/factory");
              }}
              className="flex cursor-pointer items-center gap-3 rounded-lg px-2 py-2.5 text-left text-ui text-muted-foreground hover:bg-muted"
            >
              All factories
            </button>
          </SheetBody>
        </SheetContent>
      </Sheet>
    </>
  );
}
