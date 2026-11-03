/**
 * The shell around every factory page (`/factory/:factoryId/*`): it reads the
 * live snapshot once, sends old cockpit links to their pages, beats presence,
 * and frames the page with the header, the page tabs, the viewer banner, the
 * Room dock (from 1280 px) and, on a phone, the bottom tabs. ⌘K opens the
 * factory's palette; Share and Add project open from anywhere in it.
 *
 * Someone who switched to the new cockpit (the server offers it with
 * `OMNIGENT_FACTORY_COCKPIT_V2`; each person chooses) gets `CockpitShell`
 * instead, and each cockpit sends the other's addresses to its own pages.
 */

import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeftIcon, LockIcon } from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import { WIDE_BOARD_QUERY, useMediaQuery } from "@/components/factory/board/boardHooks";
import { FactoryHeader } from "@/components/factory/board/FactoryHeader";
import { FactoryCommandPalette } from "@/components/factory/cmdk/FactoryCommandPalette";
import { useFactoryPaletteHotkey } from "@/components/factory/cmdk/useFactoryPaletteHotkey";
import { AddProjectDialog } from "@/components/factory/controls/AddProjectDialog";
import { FactoryControlsProvider } from "@/components/factory/controls/FactoryControlsContext";
import { useFactoryServerNowS } from "@/components/factory/factoryClock";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { myDecisionCount, splitNeedsYou } from "@/components/factory/overview/needsYouModel";
import { ShareDialog } from "@/components/factory/people/ShareDialog";
import {
  FactoryShellContext,
  type FactoryShellValue,
} from "@/components/factory/shell/FactoryShellContext";
import { FactoryTabs } from "@/components/factory/shell/FactoryTabs";
import {
  DockResizeHandle,
  RoomDock,
  RoomSheetButton,
  useDockWidth,
} from "@/components/factory/room/RoomDock";
import { unreadTotal } from "@/components/factory/room/roomModel";
import { useRoomThreads } from "@/components/factory/room/roomQueries";
import { useStickyTop } from "@/components/factory/room/useViewportFill";
import { PhoneTabBar } from "@/components/factory/shell/PhoneTabBar";
import { PAGE_EXTRA_TOP, pageExtraBottom } from "@/components/factory/shell/scroll";
import { useCockpitV2 } from "@/components/factory/shell/cockpitV2";
import {
  cockpitRedirect,
  factoryLocation,
  legacyFactoryRedirect,
  presenceLocation,
} from "@/components/factory/shell/shellRoutes";
import { ViewerBanner } from "@/components/factory/shell/ViewerBanner";
import { PAGE_PALETTE_ATTRIBUTE } from "@/hooks/useCommandPaletteHotkey";
import { COCKPIT_MIN_REFRESH_MS, useFactory } from "@/hooks/useFactory";
import {
  mentionNotices,
  snapshotNotices,
  useFactoryNotifications,
} from "@/hooks/useFactoryNotifications";
import { useFactoryPresence } from "@/hooks/useFactoryPresence";
import { EmptyState } from "@/components/factory/project/projectParts";
import { Button } from "@/components/ui/button";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "@/lib/routing";
import { ApiError } from "@/lib/sessionsApi";
import { CockpitShell } from "./CockpitShell";

/** The dock's height once stuck 1rem under the header, ending 1rem above the bottom. */
const DOCK_STUCK_HEIGHT =
  "calc(100dvh - var(--omnigent-header-height) - var(--omnigent-inset-top) - var(--omnigent-inset-bottom) - 2rem)";

function ShellSkeleton() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading factory"
      data-testid="factory-loading"
      className="flex flex-col gap-4"
    >
      <div className="flex items-center gap-3">
        <div className="h-7 w-56 animate-pulse rounded bg-muted" />
        <div className="ml-auto h-7 w-40 animate-pulse rounded bg-muted" />
      </div>
      <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
      <div className="flex gap-4 border-b border-border pb-2">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-5 w-20 animate-pulse rounded bg-muted" />
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex flex-col gap-3">
          <div className="h-40 animate-pulse rounded-xl bg-muted" />
          <div className="h-64 animate-pulse rounded-xl bg-muted" />
        </div>
        <div className="h-80 animate-pulse rounded-xl bg-muted max-xl:hidden" />
      </div>
    </div>
  );
}

function Shell({
  snapshot,
  clockOffsetS,
  updatedAt,
  rest,
  onTryCockpit,
  children,
}: {
  snapshot: FactorySnapshot;
  clockOffsetS: number;
  updatedAt: number;
  rest: string;
  /** Offers "Try the new cockpit" (the server lets people switch). */
  onTryCockpit?: () => void;
  children: ReactNode;
}) {
  const [params] = useSearchParams();
  const nowS = useFactoryServerNowS(clockOffsetS);
  const wide = useMediaQuery(WIDE_BOARD_QUERY);
  const phone = useIsMobileViewport();
  const location = useMemo(() => factoryLocation(rest), [rest]);
  const [sharing, setSharing] = useState(false);
  const [palette, setPalette] = useState(false);
  const [adding, setAdding] = useState<{ title: string | null } | null>(null);
  const [dockWidth, setDockWidth] = useDockWidth();
  const dockRef = useRef<HTMLElement>(null);
  const threads = useRoomThreads(snapshot.factory.id).data?.data;
  const roomUnread = unreadTotal(threads);
  const togglePalette = useCallback(() => setPalette((open) => !open), []);
  useFactoryPaletteHotkey(togglePalette);
  useFactoryNotifications(
    useMemo(
      () => [
        ...snapshotNotices(snapshot),
        ...mentionNotices(snapshot.factory.id, snapshot.factory.name, threads),
      ],
      [snapshot, threads],
    ),
  );
  const split = useMemo(() => splitNeedsYou(snapshot), [snapshot]);
  const viewer = snapshot.viewer ?? null;
  const shell: FactoryShellValue = useMemo(
    () => ({
      factoryId: snapshot.factory.id,
      snapshot,
      clockOffsetS,
      nowS,
      updatedAt,
      location,
      wide,
      cockpit: "classic",
      openShare: () => setSharing(true),
      openPalette: () => setPalette(true),
      openAddProject: () => setAdding({ title: null }),
      openAddProjectTitled: (title: string | null) => setAdding({ title }),
    }),
    [snapshot, clockOffsetS, nowS, updatedAt, location, wide],
  );
  const dock = wide && location.page !== "room" && location.page !== "settings";
  const dockTop = useStickyTop(dockRef, dock);
  const sheet = !wide && !phone && location.page !== "room";
  return (
    <FactoryShellContext.Provider value={shell}>
      <div
        {...{ [PAGE_PALETTE_ATTRIBUTE]: "" }}
        data-testid="factory-shell"
        data-page={location.page}
        className="flex min-w-0 flex-col gap-3"
      >
        <FactoryHeader
          snapshot={snapshot}
          nowS={nowS}
          onShare={() => setSharing(true)}
          onAddProject={() => setAdding({ title: null })}
        />
        {!phone && (
          <FactoryTabs
            snapshot={snapshot}
            location={location}
            mineCount={myDecisionCount(split)}
            roomUnread={roomUnread}
            onPalette={() => setPalette(true)}
            onTryCockpit={onTryCockpit}
          />
        )}
        {viewer?.role === "viewer" && <ViewerBanner factoryId={snapshot.factory.id} />}
        <div
          data-testid="factory-layout"
          data-wide={dock ? "true" : "false"}
          className={dock ? "grid items-start gap-6 pt-1" : "flex min-w-0 flex-col pt-1"}
          style={dock ? { gridTemplateColumns: `minmax(0,1fr) ${dockWidth}px` } : undefined}
        >
          <main className="min-w-0" data-testid="factory-page-body">
            {children}
          </main>
          {dock && (
            // The page scroller's padding already clears the header, so the dock
            // sticks 1rem under it and ends 1rem above the bottom.
            <aside
              aria-label="Room and decisions"
              data-testid="room-dock-column"
              className="sticky flex flex-col"
              ref={dockRef}
              style={{
                top: `calc(1rem - ${PAGE_EXTRA_TOP})`,
                // Down to 1rem above the viewport's bottom from wherever it starts,
                // so the composer shows before the page scrolls the dock up.
                height:
                  dockTop == null
                    ? DOCK_STUCK_HEIGHT
                    : `calc(100dvh - var(--omnigent-inset-bottom) - 1rem - ${dockTop}px)`,
                maxHeight: DOCK_STUCK_HEIGHT,
              }}
            >
              <DockResizeHandle width={dockWidth} onWidth={setDockWidth} />
              <RoomDock
                snapshot={snapshot}
                location={location}
                highlightDecision={params.get("decision")}
                clockOffsetS={clockOffsetS}
              />
            </aside>
          )}
        </div>
      </div>
      {sheet && (
        <RoomSheetButton
          snapshot={snapshot}
          location={location}
          highlightDecision={params.get("decision")}
          clockOffsetS={clockOffsetS}
        />
      )}
      {phone && (
        <PhoneTabBar
          factoryId={snapshot.factory.id}
          location={location}
          needsYou={split.mine.length + split.role.length}
          roomUnread={roomUnread}
          onShare={() => setSharing(true)}
          onPalette={() => setPalette(true)}
          onTryCockpit={onTryCockpit}
        />
      )}
      <ShareDialog
        factoryId={snapshot.factory.id}
        factoryName={snapshot.factory.name}
        open={sharing}
        onOpenChange={setSharing}
        nowS={nowS}
      />
      <FactoryCommandPalette
        snapshot={snapshot}
        open={palette}
        onOpenChange={setPalette}
        onShare={() => setSharing(true)}
        onAddProject={() => setAdding({ title: null })}
      />
      <AddProjectDialog
        // A fresh form per opening, so `/project new <title>` prefills it.
        key={adding ? `add:${adding.title ?? ""}` : "add"}
        factoryId={snapshot.factory.id}
        open={adding != null}
        initialTitle={adding?.title ?? null}
        onOpenChange={(open) => !open && setAdding(null)}
      />
    </FactoryShellContext.Provider>
  );
}

/**
 * The factory shell; `children` is the routed page. It renders nothing of the
 * page until the snapshot is in, and only a redirect for an old link.
 */
export function FactoryLayout({ children }: { children: ReactNode }) {
  const { factoryId = "", "*": rest = "" } = useParams<{ factoryId: string; "*": string }>();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const phone = useIsMobileViewport();
  const wide = useMediaQuery(WIDE_BOARD_QUERY);
  const { data, error, isLoading, updatedAt, clockOffsetS, refetch } = useFactory(factoryId, {
    minRefreshMs: COCKPIT_MIN_REFRESH_MS,
  });
  const legacy = legacyFactoryRedirect(factoryId, rest, params, data?.prs);
  const cockpit = useCockpitV2(factoryId);
  const here = useLocation();
  // The factory page the viewer was on before a Room link (the drawer opens over it).
  const from = useRef<string | null>(null);
  const mode = cockpit.on ? "v2" : "classic";
  const other = legacy ? null : cockpitRedirect(factoryId, rest, params, mode, from.current);
  // Follow another cockpit's address only once it is known which cockpit this person sees.
  const switched = legacy ? null : cockpit.loading || (other && !cockpit.settled) ? "wait" : other;
  const target = legacy && legacy !== "wait" ? legacy.to : switched !== "wait" ? switched : null;
  useEffect(() => {
    if (target) navigate(target, { replace: true });
  }, [navigate, target]);
  useEffect(() => {
    if (factoryLocation(rest).page !== "room") from.current = `${here.pathname}${here.search}`;
  }, [rest, here.pathname, here.search]);
  const redirecting = Boolean(legacy) || switched != null;
  // A 404 means no factory or no access; one after data (a removed person's
  // refetch) drops the stale page too.
  const missing = error instanceof ApiError && error.status === 404;
  useFactoryPresence(factoryId, presenceLocation(factoryLocation(rest)), {
    enabled: !redirecting && Boolean(data) && !missing,
  });
  const v2 = cockpit.on;
  return (
    <PageScroll
      data-testid="factory-page"
      maxWidthClassName="max-w-[100rem]"
      contentClassName="px-4 md:px-6"
      extraTop={PAGE_EXTRA_TOP}
      // Room for the phone's bottom tabs, or the Room button below 1280 px.
      extraBottom={pageExtraBottom(phone, wide && !v2)}
    >
      {/* The new cockpit's switcher lists every factory instead. */}
      {!v2 && (
        <Link
          to="/factory"
          componentId="factory.page.back"
          className="mb-2 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeftIcon aria-hidden className="size-3.5" />
          Factory
        </Link>
      )}
      {error && !missing && (
        <FactoryLoadError
          error={error}
          updatedAt={data ? updatedAt : 0}
          onRetry={refetch}
          what="this factory"
        />
      )}
      {(isLoading || redirecting) && !missing && <ShellSkeleton />}
      {missing && (
        <EmptyState
          testId="factory-missing"
          icon={<LockIcon aria-hidden className="size-6 text-muted-foreground/50" />}
          title="No access to this factory"
          body="This factory doesn’t exist, or you no longer have access to it. Ask one of its owners to share it with you again."
          action={
            <Button asChild size="sm" variant="outline" className="mt-1">
              <Link to="/factory" componentId="factory.missing.back">
                <ArrowLeftIcon />
                Back to factories
              </Link>
            </Button>
          }
        />
      )}
      {data && !redirecting && !missing && (
        <FactoryControlsProvider key={data.factory.id} snapshot={data}>
          {v2 ? (
            <CockpitShell
              snapshot={data}
              clockOffsetS={clockOffsetS}
              updatedAt={updatedAt}
              rest={rest}
              onClassic={() => cockpit.choose("v1")}
              variant={cockpit.v3 ? "v3" : "v2"}
            >
              {children}
            </CockpitShell>
          ) : (
            <Shell
              snapshot={data}
              clockOffsetS={clockOffsetS}
              updatedAt={updatedAt}
              rest={rest}
              onTryCockpit={cockpit.available ? () => cockpit.choose("v2") : undefined}
            >
              {children}
            </Shell>
          )}
        </FactoryControlsProvider>
      )}
    </PageScroll>
  );
}
