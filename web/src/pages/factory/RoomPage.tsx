/**
 * The Room page (`/factory/:id/room/:thread?`): every thread on the left
 * with unread counts, the thread in the middle (live, paged, cards, bus
 * view, composer), and what it is about on the right. Below 1280 px the
 * context opens from the header; on a phone the thread list is a picker.
 */

import { useRef, useState } from "react";
import { MessagesSquareIcon, PanelRightIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { WIDE_BOARD_QUERY, useMediaQuery } from "@/components/factory/board/boardHooks";
import { roomPageHref } from "@/components/factory/factoryRoutes";
import { RoomContextPanel } from "@/components/factory/room/RoomContextPanel";
import { threadFromParam, threadTitle } from "@/components/factory/room/roomModel";
import { useRoomThreads } from "@/components/factory/room/roomQueries";
import { RoomThreadList, ThreadSwitcher } from "@/components/factory/room/RoomThreadList";
import { RoomThreadView } from "@/components/factory/room/RoomThreadView";
import { useViewportFill } from "@/components/factory/room/useViewportFill";
import { PHONE_TAB_BAR_REM } from "@/components/factory/shell/PhoneTabBar";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import { useNavigate } from "@/lib/routing";

const LIST_QUERY = "(min-width: 1024px)";

export function RoomPage() {
  const { snapshot, location, clockOffsetS, nowS } = useFactoryShell();
  const navigate = useNavigate();
  const phone = useIsMobileViewport();
  const wide = useMediaQuery(WIDE_BOARD_QUERY);
  const withList = useMediaQuery(LIST_QUERY);
  const fid = snapshot.factory.id;
  const thread = threadFromParam(snapshot, location.id);
  const threads = useRoomThreads(fid);
  const threadView = threads.data?.data.find((t) => t.key === thread) ?? null;
  const [context, setContext] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const height = useViewportFill(ref, {
    bottomPx: phone ? PHONE_TAB_BAR_REM * 16 + 24 : 24,
    minPx: phone ? 420 : 480,
  });
  const pick = (key: string) => navigate(roomPageHref(fid, key));
  const contextPanel = (
    <RoomContextPanel snapshot={snapshot} thread={thread} threadView={threadView} nowS={nowS} />
  );
  return (
    <div
      ref={ref}
      data-testid="factory-room-page"
      data-thread={thread}
      className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-[14rem_minmax(0,1fr)] xl:grid-cols-[15rem_minmax(0,1fr)_18rem]"
      style={height ? { height } : undefined}
    >
      {withList && (
        <div className="min-h-0 overflow-y-auto">
          <RoomThreadList snapshot={snapshot} threads={threads.data?.data ?? []} active={thread} />
          {threads.isLoading && (
            <div aria-busy="true" className="flex flex-col gap-2 p-2">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="h-5 animate-pulse rounded bg-muted" />
              ))}
            </div>
          )}
        </div>
      )}
      <RoomThreadView
        key={thread}
        snapshot={snapshot}
        clockOffsetS={clockOffsetS}
        thread={thread}
        variant="page"
        className="min-h-0"
        headerStart={
          <span className="flex min-w-0 items-center gap-1">
            {withList ? (
              <h2 className="flex min-w-0 items-center gap-1.5 text-ui font-semibold">
                <MessagesSquareIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                <span className="truncate font-mono" data-testid="room-thread-title">
                  {threadTitle(snapshot, thread)}
                </span>
              </h2>
            ) : (
              <ThreadSwitcher
                snapshot={snapshot}
                threads={threads.data?.data ?? []}
                active={thread}
                onPick={pick}
              />
            )}
            {!wide && (
              <Button
                size="icon-xs"
                variant="ghost"
                onClick={() => setContext(true)}
                aria-label="Thread context"
                title="Thread context"
                data-testid="room-context-open"
                className="text-muted-foreground"
              >
                <PanelRightIcon />
              </Button>
            )}
          </span>
        }
      />
      {wide && <div className="min-h-0 overflow-y-auto">{contextPanel}</div>}
      <Sheet open={context} onOpenChange={setContext}>
        <SheetContent side="right" size="md" data-testid="room-context-sheet">
          <SheetHeader>
            <SheetTitle>Context</SheetTitle>
            <SheetDescription>What this thread is about, and who is here</SheetDescription>
          </SheetHeader>
          <SheetBody>{contextPanel}</SheetBody>
        </SheetContent>
      </Sheet>
    </div>
  );
}
