/**
 * The right-hand dock of every factory page from 1280 px: `Room | Decisions`.
 * Decisions is the decisions rail (open ones answer in one click). The Room
 * tab holds the slot the shared chat docks into, bound to the page's thread
 * (`#<project>` on a project page, `!<pr>` on a PR page, else `#factory`).
 */

import { useState } from "react";
import { MessagesSquareIcon } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { DecisionsRail } from "../decisions/DecisionsRail";
import type { FactoryLocation } from "./shellRoutes";
import { findPr } from "./shellRoutes";

type DockTab = "room" | "decisions";

/** The Room thread a page binds the dock to: `#db-version`, `!9631` or `#factory`. */
export function dockThread(
  snapshot: FactorySnapshot,
  location: FactoryLocation,
): { key: string; title: string } {
  if (location.page === "project" && location.id) {
    return { key: `project:${location.id}`, title: `#${location.id}` };
  }
  if (location.page === "pr" && location.id) {
    const pr = findPr(snapshot.prs, location.id);
    return { key: `pr:${pr?.id ?? location.id}`, title: `!${pr?.number ?? location.id}` };
  }
  return { key: "factory", title: "#factory" };
}

/** Where the Room goes while it isn't docked yet: the thread this page binds to. */
export function RoomSlot({ thread }: { thread: { key: string; title: string } }) {
  return (
    <div
      data-testid="room-dock-slot"
      data-thread={thread.key}
      className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border px-4 py-8 text-center"
    >
      <MessagesSquareIcon aria-hidden className="size-5 text-muted-foreground/60" />
      <p className="text-ui font-medium">
        Room <span className="font-mono">{thread.title}</span>
      </p>
      <p className="max-w-64 text-sm text-muted-foreground">
        The factory’s shared chat docks here, on this page’s thread. Until then, decisions wait
        under Decisions.
      </p>
    </div>
  );
}

export function RoomDock({
  snapshot,
  location,
  highlightDecision,
  clockOffsetS,
}: {
  snapshot: FactorySnapshot;
  location: FactoryLocation;
  highlightDecision: string | null;
  clockOffsetS: number;
}) {
  const [tab, setTab] = useState<DockTab>("decisions");
  const open = snapshot.decisions.filter((d) => d.status === "open").length;
  const thread = dockThread(snapshot, location);
  return (
    <Tabs
      value={tab}
      onValueChange={(value) => setTab(value as DockTab)}
      componentId="factory.dock.tab"
      data-testid="room-dock"
      className="min-w-0 gap-3"
    >
      <TabsList variant="line" className="h-8 w-full justify-start gap-3 border-b border-border">
        <TabsTrigger value="room" data-testid="room-dock-tab-room" className="flex-none px-0.5">
          Room
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
      <TabsContent value="room">
        <RoomSlot thread={thread} />
      </TabsContent>
      <TabsContent value="decisions" data-testid="factory-rail">
        <DecisionsRail
          factoryId={snapshot.factory.id}
          decisions={snapshot.decisions}
          highlightId={highlightDecision}
          clockOffsetS={clockOffsetS}
          hideHeading
        />
      </TabsContent>
    </Tabs>
  );
}
