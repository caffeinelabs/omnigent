/**
 * The cockpit's shell around every factory page: a sidebar that is always in
 * view (Home, Demos, Chats, Decisions, Search, then the outline of the work)
 * beside the page; on a phone the places move to a bottom tab bar. ⌘K opens
 * Search from anywhere; Share and Add project open from the factory menu.
 * Sheets (`?peek=`: the base env, Try it, a node's demos, the keys) open by
 * the address, so a link opens the exact view.
 */

import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CockpitPalette } from "@/components/factory/cmdk/CockpitPalette";
import {
  PLACE_HREF,
  useCockpitKeys,
  useFactoryPaletteHotkey,
} from "@/components/factory/cmdk/useFactoryPaletteHotkey";
import { CockpitSidebar, CockpitTabBar } from "@/components/factory/cockpit/CockpitNav";
import { chatsHref, placeOf } from "@/components/factory/cockpit/cockpitRoutes";
import { AddProjectDialog } from "@/components/factory/controls/AddProjectDialog";
import { useFactoryServerNowS } from "@/components/factory/factoryClock";
import { useFactoryDefinition } from "@/components/factory/insights/learnQueries";
import { ShareDialog } from "@/components/factory/people/ShareDialog";
import { unreadTotal } from "@/components/factory/room/roomModel";
import { useRoomThreads } from "@/components/factory/room/roomQueries";
import {
  FactoryShellContext,
  type FactoryShellValue,
} from "@/components/factory/shell/FactoryShellContext";
import { FactorySwitcher } from "@/components/factory/shell/FactoryTopBar";
import { factoryLocation } from "@/components/factory/shell/shellRoutes";
import { PAGE_EXTRA_TOP } from "@/components/factory/shell/scroll";
import { ViewerBanner } from "@/components/factory/shell/ViewerBanner";
import { FactoryToastProvider } from "@/components/factory/ui/Toast";
import { NodeChatSlotContext, createNodeChatSlot } from "@/components/factory/v3/chat/chatLayout";
import { nodeQueryKeys } from "@/components/factory/v3/nodeQueries";
import { PRODUCT_REF } from "@/components/factory/v3/nodeRoutes";
import { useNodeSource } from "@/components/factory/v3/nodeSource";
import { useNeedsYou } from "@/components/factory/v3/nodeSteer";
import { V3Sheets } from "@/components/factory/v3/sheets/V3Sheets";
import { PAGE_PALETTE_ATTRIBUTE } from "@/hooks/useCommandPaletteHotkey";
import {
  mentionNotices,
  snapshotNotices,
  useFactoryNotifications,
} from "@/hooks/useFactoryNotifications";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { useNavigate, useSearchParams } from "@/lib/routing";

/** The id of the page's main region (the skip link's target). */
export const COCKPIT_MAIN_ID = "factory-main";

/** The sidebar's height once stuck 1rem under the app header, ending 1rem above the bottom. */
const SIDEBAR_HEIGHT =
  "calc(100dvh - var(--omnigent-header-height, 0px) - var(--omnigent-inset-top, 0px) - var(--omnigent-inset-bottom, 0px) - 2rem)";

export function Cockpit({
  snapshot,
  clockOffsetS,
  updatedAt,
  rest,
  children,
}: {
  snapshot: FactorySnapshot;
  clockOffsetS: number;
  updatedAt: number;
  rest: string;
  children: ReactNode;
}) {
  const fid = snapshot.factory.id;
  const nowS = useFactoryServerNowS(clockOffsetS);
  const phone = useIsMobileViewport();
  const location = useMemo(() => factoryLocation(rest), [rest]);
  const place = placeOf(rest);
  const nodeRef = location.page === "node" ? (location.id ?? null) : null;
  const [sharing, setSharing] = useState(false);
  const [palette, setPalette] = useState(false);
  const [adding, setAdding] = useState<{ title: string | null } | null>(null);
  const navigate = useNavigate();
  const togglePalette = useCallback(() => setPalette((open) => !open), []);
  useFactoryPaletteHotkey(togglePalette);
  const threads = useRoomThreads(fid).data?.data;
  useFactoryNotifications(
    useMemo(
      () => [...snapshotNotices(snapshot), ...mentionNotices(fid, snapshot.factory.name, threads)],
      [snapshot, fid, threads],
    ),
  );
  const productName = useFactoryDefinition(fid).data?.effective.product?.name ?? null;
  const [chatSlot] = useState(createNodeChatSlot);
  const queryClient = useQueryClient();
  const nodeSource = useNodeSource();
  // One count of asks everywhere: the product tree's ranked list (Decisions, Home).
  const ranked = useNeedsYou("product", Boolean(nodeSource?.needsYou), fid).data;
  const decisions = ranked ? ranked.items.length + ranked.blocks_nothing.length : 0;
  const counts = useMemo(() => ({ decisions, unread: unreadTotal(threads) }), [decisions, threads]);
  // The asks and the product page move with the factory: read them again when
  // the snapshot's open asks change (a push frame re-read it).
  const liveMark = useMemo(
    () =>
      snapshot.decisions
        .filter((d) => d.status === "open")
        .map((d) => d.id)
        .sort()
        .join(","),
    [snapshot.decisions],
  );
  const sourceKind = nodeSource?.kind;
  const markRead = useRef(liveMark);
  useEffect(() => {
    if (liveMark === markRead.current || !sourceKind) return;
    markRead.current = liveMark;
    void queryClient.invalidateQueries({
      queryKey: [...nodeQueryKeys.all(fid), sourceKind, "needs-you"],
    });
    void queryClient.invalidateQueries({
      queryKey: nodeQueryKeys.node(fid, sourceKind, PRODUCT_REF),
      exact: true,
    });
  }, [fid, liveMark, queryClient, sourceKind]);
  const [, setParams] = useSearchParams();
  useCockpitKeys({
    // `/`: the chat's box on a node page, else Search.
    onPalette: () => {
      if (!chatSlot.focus()) setPalette(true);
    },
    onHelp: () =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("peek", "keys");
          return next;
        },
        { replace: true },
      ),
    onRoom: () => {
      if (!chatSlot.focus()) navigate(chatsHref(fid));
    },
    onGo: (go) => navigate(PLACE_HREF[go](fid)),
  });
  const shell: FactoryShellValue = useMemo(
    () => ({
      factoryId: fid,
      snapshot,
      clockOffsetS,
      nowS,
      updatedAt,
      location,
      wide: false,
      cockpit: "v2",
      openShare: () => setSharing(true),
      openPalette: () => setPalette(true),
      openAddProject: () => setAdding({ title: null }),
      openAddProjectTitled: (title: string | null) => setAdding({ title }),
    }),
    [fid, snapshot, clockOffsetS, nowS, updatedAt, location],
  );
  const viewer = snapshot.viewer ?? null;
  return (
    <FactoryShellContext.Provider value={shell}>
      <a
        href={`#${COCKPIT_MAIN_ID}`}
        data-testid="cockpit-skip"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById(COCKPIT_MAIN_ID)?.focus();
        }}
        className="sr-only z-40 rounded-md bg-background px-3 py-1.5 text-sm focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:ring-2 focus:ring-status-blue"
      >
        Skip to main content
      </a>
      <div
        {...{ [PAGE_PALETTE_ATTRIBUTE]: "" }}
        data-testid="factory-shell"
        data-cockpit="v3"
        data-page={location.page}
        className={phone ? "flex min-w-0 flex-col gap-3" : "grid items-start gap-7"}
        style={phone ? undefined : { gridTemplateColumns: "13.5rem minmax(0,1fr)" }}
      >
        {phone ? (
          <div className="-mt-1 flex min-w-0 items-center">
            <FactorySwitcher
              snapshot={snapshot}
              productName={productName}
              compact
              shrink
              onShare={() => setSharing(true)}
              onAddProject={() => setAdding({ title: null })}
            />
          </div>
        ) : (
          <CockpitSidebar
            snapshot={snapshot}
            place={place}
            nodeRef={nodeRef}
            counts={counts}
            factoryAsks={ranked?.other_tree ?? 0}
            productName={productName}
            onSearch={() => setPalette(true)}
            onShare={() => setSharing(true)}
            onAddProject={() => setAdding({ title: null })}
            style={{ top: `calc(1rem - ${PAGE_EXTRA_TOP})`, height: SIDEBAR_HEIGHT }}
          />
        )}
        <main
          id={COCKPIT_MAIN_ID}
          tabIndex={-1}
          data-testid="factory-page-body"
          className="min-w-0 outline-none"
        >
          {viewer?.role === "viewer" && <ViewerBanner factoryId={fid} />}
          <FactoryToastProvider>
            <NodeChatSlotContext.Provider value={chatSlot}>{children}</NodeChatSlotContext.Provider>
            <CockpitPalette
              snapshot={snapshot}
              open={palette}
              onOpenChange={setPalette}
              onShare={() => setSharing(true)}
              onAddProject={() => setAdding({ title: null })}
              onRoom={(thread) => navigate(chatsHref(fid, thread))}
              nodes
            />
            <V3Sheets factoryId={fid} keys />
          </FactoryToastProvider>
        </main>
      </div>
      {phone && (
        <CockpitTabBar fid={fid} place={place} counts={counts} onSearch={() => setPalette(true)} />
      )}
      <ShareDialog
        factoryId={fid}
        factoryName={snapshot.factory.name}
        open={sharing}
        onOpenChange={setSharing}
        nowS={nowS}
      />
      <AddProjectDialog
        key={adding ? `add:${adding.title ?? ""}` : "add"}
        factoryId={fid}
        open={adding != null}
        initialTitle={adding?.title ?? null}
        onOpenChange={(open) => !open && setAdding(null)}
      />
    </FactoryShellContext.Provider>
  );
}
