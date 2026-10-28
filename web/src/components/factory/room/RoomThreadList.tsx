/**
 * The Room's threads: `#factory` and `#improvements`, then one per project
 * (a stage dot each), then PR threads, each with the viewer's unread count.
 * The page lists them in its left column; the dock picks one from a menu.
 */

import { ChevronDownIcon, GitPullRequestIcon, HashIcon, LightbulbIcon } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { FactorySnapshot, ThreadView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { TONE_DOT, stageTone } from "../factoryFormat";
import { roomPageHref } from "../factoryRoutes";
import { groupThreads, threadTitle } from "./roomModel";

function Unread({ n }: { n: number }) {
  if (n <= 0) return null;
  return (
    <span
      data-testid="room-thread-unread"
      className="ml-auto inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-medium text-primary-foreground tabular-nums"
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}

function ThreadIcon({ thread, snapshot }: { thread: ThreadView; snapshot: FactorySnapshot }) {
  if (thread.kind === "project") {
    const project = snapshot.projects.find((p) => p.slug === thread.project_slug);
    const tone = project ? stageTone(project.stage, project.overlay) : "muted";
    return (
      <span aria-hidden className="flex size-3.5 shrink-0 items-center justify-center">
        <span className={cn("size-2 rounded-full", TONE_DOT[tone])} />
      </span>
    );
  }
  if (thread.kind === "pr") {
    return <GitPullRequestIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />;
  }
  if (thread.kind === "improvements") {
    return <LightbulbIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />;
  }
  return <HashIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />;
}

/** `#db-version`, `!9631 db-version`: a thread's row label. */
function rowLabel(
  thread: ThreadView,
  snapshot: FactorySnapshot,
): { name: string; detail: string | null } {
  if (thread.kind === "pr") {
    return { name: thread.title || threadTitle(snapshot, thread.key), detail: thread.project_slug };
  }
  return { name: thread.title.replace(/^#/, ""), detail: null };
}

function Group({
  label,
  threads,
  snapshot,
  active,
}: {
  label: string | null;
  threads: ThreadView[];
  snapshot: FactorySnapshot;
  active: string;
}) {
  if (threads.length === 0) return null;
  return (
    <div className="flex flex-col gap-0.5">
      {label && (
        <p className="px-2 pt-2 pb-0.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
          {label}
        </p>
      )}
      {threads.map((thread) => {
        const { name, detail } = rowLabel(thread, snapshot);
        const current = thread.key === active;
        return (
          <Link
            key={thread.key}
            to={roomPageHref(snapshot.factory.id, thread.key)}
            componentId="factory.room.thread"
            data-testid={`room-thread-${thread.key}`}
            data-active={current ? "true" : "false"}
            aria-current={current ? "page" : undefined}
            className={cn(
              "flex min-w-0 items-center gap-2 rounded-md px-2 py-1 text-ui",
              current
                ? "bg-muted font-medium text-foreground"
                : "text-foreground/80 hover:bg-muted/60",
              thread.unread > 0 && !current && "font-medium text-foreground",
            )}
          >
            <ThreadIcon thread={thread} snapshot={snapshot} />
            <span className="min-w-0 truncate">{name}</span>
            {detail && (
              <span className="min-w-0 truncate text-xs text-muted-foreground">{detail}</span>
            )}
            <Unread n={thread.unread} />
          </Link>
        );
      })}
    </div>
  );
}

export function RoomThreadList({
  snapshot,
  threads,
  active,
  className,
}: {
  snapshot: FactorySnapshot;
  threads: readonly ThreadView[];
  active: string;
  className?: string;
}) {
  const groups = groupThreads(threads, snapshot);
  return (
    <nav
      aria-label="Room threads"
      data-testid="room-thread-list"
      className={cn("flex min-w-0 flex-col gap-1", className)}
    >
      <Group label={null} threads={groups.fixed} snapshot={snapshot} active={active} />
      <Group label="Projects" threads={groups.projects} snapshot={snapshot} active={active} />
      <Group label="PRs" threads={groups.prs} snapshot={snapshot} active={active} />
    </nav>
  );
}

/** The dock's thread picker: the current thread, a menu of the others with unread counts. */
export function ThreadSwitcher({
  snapshot,
  threads,
  active,
  onPick,
}: {
  snapshot: FactorySnapshot;
  threads: readonly ThreadView[];
  active: string;
  onPick: (thread: string) => void;
}) {
  const groups = groupThreads(threads, snapshot);
  const otherUnread = threads.filter((t) => t.key !== active).reduce((n, t) => n + t.unread, 0);
  const sections: [string | null, ThreadView[]][] = [
    [null, groups.fixed],
    ["Projects", groups.projects],
    ["PRs", groups.prs],
  ];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        data-testid="room-thread-switcher"
        className="inline-flex min-w-0 cursor-pointer items-center gap-1 rounded-md px-1 py-0.5 text-ui font-semibold hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <span className="truncate font-mono" data-testid="room-thread-title">
          {threadTitle(snapshot, active)}
        </span>
        {otherUnread > 0 && (
          <span
            className="size-1.5 shrink-0 rounded-full bg-primary"
            aria-label={`${otherUnread} unread elsewhere`}
          />
        )}
        <ChevronDownIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 w-64 overflow-y-auto">
        {sections.map(([label, list], i) =>
          list.length === 0 ? null : (
            <div key={label ?? "fixed"}>
              {i > 0 && <DropdownMenuSeparator />}
              {label && <DropdownMenuLabel>{label}</DropdownMenuLabel>}
              {list.map((thread) => (
                <DropdownMenuItem
                  key={thread.key}
                  data-testid={`room-switch-${thread.key}`}
                  onSelect={() => onPick(thread.key)}
                  className={cn(thread.key === active && "bg-muted")}
                >
                  <ThreadIcon thread={thread} snapshot={snapshot} />
                  <span className="min-w-0 truncate">{rowLabel(thread, snapshot).name}</span>
                  <Unread n={thread.unread} />
                </DropdownMenuItem>
              ))}
            </div>
          ),
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
