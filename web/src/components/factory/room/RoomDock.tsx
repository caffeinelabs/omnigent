/**
 * The Room docked beside every factory page from 1280 px: `Room | Decisions`.
 * Room is the live thread the page binds to (`#<project>` on a project page,
 * `!<pr>` on a PR page, else the last one used, `#factory` at first);
 * Decisions is the decisions rail. Its width is dragged on the left edge and
 * kept in this browser. Below 1280 px the same panel opens as a bottom sheet
 * from a "Room" button that carries the unread count.
 */

import {
  type KeyboardEvent,
  type PointerEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { MessagesSquareIcon } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { DecisionsRail } from "../decisions/DecisionsRail";
import { DOCK_SCROLLER_ATTRIBUTE } from "../shell/scroll";
import type { FactoryLocation } from "../shell/shellRoutes";
import {
  DOCK_TAB_KEY,
  clampDockWidth,
  loadDockWidth,
  loadLastThread,
  pageProjectSlug,
  pageThread,
  saveDockWidth,
  saveLastThread,
  unreadTotal,
} from "./roomModel";
import { useRoomThreads } from "./roomQueries";
import { ThreadSwitcher } from "./RoomThreadList";
import { RoomThreadView } from "./RoomThreadView";

export type DockTab = "room" | "decisions";

function loadTab(): DockTab {
  try {
    return window.localStorage.getItem(DOCK_TAB_KEY) === "decisions" ? "decisions" : "room";
  } catch {
    return "room";
  }
}

function saveTab(tab: DockTab): void {
  try {
    window.localStorage.setItem(DOCK_TAB_KEY, tab);
  } catch {
    // The tab lasts this page only.
  }
}

/** The dock's width, kept in this browser; `setWidth` clamps and stores it. */
export function useDockWidth(): [number, (px: number) => void] {
  const [width, setState] = useState(loadDockWidth);
  const setWidth = useCallback((px: number) => {
    const next = clampDockWidth(px);
    setState(next);
    saveDockWidth(next);
  }, []);
  return [width, setWidth];
}

/** The thread the dock shows: the page's, else the last one picked in this factory. */
export function useDockThread(
  snapshot: FactorySnapshot,
  location: FactoryLocation,
): [string, (thread: string) => void] {
  const fid = snapshot.factory.id;
  const [last, setLast] = useState<string | null>(() => loadLastThread(fid));
  const bound = location.page === "project" || location.page === "pr";
  const thread = pageThread(snapshot, location, last);
  const pick = useCallback(
    (next: string) => {
      setLast(next);
      saveLastThread(fid, next);
    },
    [fid],
  );
  // A page-bound thread is remembered too, so leaving the page keeps it.
  useEffect(() => {
    if (!bound) return;
    setLast(thread);
    saveLastThread(fid, thread);
  }, [bound, fid, thread]);
  return [thread, pick];
}

/** The dock's left edge: drag (or ←/→) to resize. */
export function DockResizeHandle({
  width,
  onWidth,
}: {
  width: number;
  onWidth: (px: number) => void;
}) {
  const start = useRef<{ x: number; width: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    start.current = { x: event.clientX, width };
    setDragging(true);
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!start.current) return;
    onWidth(start.current.width + (start.current.x - event.clientX));
  };
  const end = () => {
    start.current = null;
    setDragging(false);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowLeft") onWidth(width + 16);
    else if (event.key === "ArrowRight") onWidth(width - 16);
    else return;
    event.preventDefault();
  };
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the Room"
      aria-valuenow={width}
      tabIndex={0}
      data-testid="room-dock-resize"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={end}
      onPointerCancel={end}
      onKeyDown={onKeyDown}
      className="group/resize absolute inset-y-0 -left-3 z-10 flex w-3 cursor-col-resize justify-center focus-visible:outline-none"
    >
      <span
        aria-hidden
        className={cn(
          "h-full w-px bg-transparent transition-colors group-hover/resize:bg-border group-focus-visible/resize:bg-ring",
          dragging && "bg-ring",
        )}
      />
    </div>
  );
}

export interface RoomDockProps {
  snapshot: FactorySnapshot;
  location: FactoryLocation;
  highlightDecision: string | null;
  clockOffsetS: number;
  /** The dock's height is its container's (a sheet passes a fixed one). */
  className?: string;
  /** The panel is on screen (the sheet is open): its thread marks read. */
  visible?: boolean;
}

export function RoomDock({
  snapshot,
  location,
  highlightDecision,
  clockOffsetS,
  className,
  visible = true,
}: RoomDockProps) {
  const [tab, setTab] = useState<DockTab>(() => (highlightDecision ? "decisions" : loadTab()));
  const [thread, pickThread] = useDockThread(snapshot, location);
  const threads = useRoomThreads(snapshot.factory.id);
  const open = snapshot.decisions.filter((d) => d.status === "open").length;
  // A `?decision=` link opens the Decisions tab to show it.
  useEffect(() => {
    if (highlightDecision) setTab("decisions");
  }, [highlightDecision]);
  const unreadElsewhere = unreadTotal(threads.data?.data.filter((t) => t.key !== thread));
  return (
    <Tabs
      value={tab}
      onValueChange={(value) => {
        setTab(value as DockTab);
        saveTab(value as DockTab);
      }}
      componentId="factory.dock.tab"
      data-testid="room-dock"
      data-thread={thread}
      className={cn("flex h-full min-h-0 min-w-0 flex-col gap-2", className)}
    >
      <TabsList
        variant="line"
        className="h-8 w-full shrink-0 justify-start gap-3 border-b border-border bg-background"
      >
        <TabsTrigger value="room" data-testid="room-dock-tab-room" className="flex-none px-0.5">
          Room
          {unreadElsewhere > 0 && (
            <span className="rounded-full bg-primary/15 px-1.5 text-xs leading-4 text-primary tabular-nums">
              {unreadElsewhere}
            </span>
          )}
        </TabsTrigger>
        <TabsTrigger
          value="decisions"
          data-testid="room-dock-tab-decisions"
          className="flex-none px-0.5"
        >
          Decisions
          {open > 0 && (
            <span className="rounded-full bg-warning/15 px-1.5 text-xs leading-4 text-warning tabular-nums">
              {open}
            </span>
          )}
        </TabsTrigger>
      </TabsList>
      <TabsContent value="room" className="flex min-h-0 flex-1 flex-col">
        <RoomThreadView
          snapshot={snapshot}
          clockOffsetS={clockOffsetS}
          thread={thread}
          variant="dock"
          visible={visible && tab === "room"}
          className="min-h-0 flex-1"
          headerStart={
            <ThreadSwitcher
              snapshot={snapshot}
              threads={threads.data?.data ?? []}
              active={thread}
              onPick={pickThread}
            />
          }
        />
      </TabsContent>
      <TabsContent
        value="decisions"
        data-testid="factory-rail"
        {...{ [DOCK_SCROLLER_ATTRIBUTE]: "" }}
        // The padding keeps a highlighted card's outline inside the scroller.
        className="-m-1 min-h-0 flex-1 overflow-y-auto overscroll-contain p-1"
      >
        <DecisionsRail
          factoryId={snapshot.factory.id}
          decisions={snapshot.decisions}
          highlightId={highlightDecision}
          pinProject={pageProjectSlug(snapshot, location)}
          clockOffsetS={clockOffsetS}
          hideHeading
        />
      </TabsContent>
    </Tabs>
  );
}

/**
 * Below 1280 px: a "Room" button (unread badge) bottom-right that opens the
 * dock as a bottom sheet. On a phone the bottom tabs carry the Room instead.
 */
export function RoomSheetButton(props: Omit<RoomDockProps, "className" | "visible">) {
  const [open, setOpen] = useState(false);
  const threads = useRoomThreads(props.snapshot.factory.id);
  const unread = unreadTotal(threads.data?.data);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        data-testid="room-sheet-open"
        aria-label={`Room${unread ? ` · ${unread} unread` : ""}`}
        className="fixed right-4 z-30 inline-flex h-10 cursor-pointer items-center gap-2 rounded-full border border-border bg-background/95 px-4 text-ui font-medium shadow-lg backdrop-blur-md hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
        style={{ bottom: "calc(1rem + var(--omnigent-inset-bottom, 0px))" }}
      >
        <MessagesSquareIcon aria-hidden className="size-4" />
        Room
        {unread > 0 && (
          <span
            data-testid="room-sheet-unread"
            className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-xs text-primary-foreground tabular-nums"
          >
            {unread}
          </span>
        )}
      </button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          side="bottom"
          size="lg"
          data-testid="room-sheet"
          className="h-[85dvh] max-h-[85dvh] rounded-t-xl"
        >
          <SheetHeader className="sr-only">
            <SheetTitle>Room</SheetTitle>
            <SheetDescription>The factory's shared chat and its decisions</SheetDescription>
          </SheetHeader>
          <div className="flex min-h-0 flex-1 flex-col px-4 pt-3 pb-[calc(0.75rem+var(--omnigent-safe-bottom,0px))]">
            <RoomDock {...props} visible={open} className="flex-1" />
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
