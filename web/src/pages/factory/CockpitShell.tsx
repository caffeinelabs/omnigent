/**
 * The new cockpit's shell around every factory page: one top bar (switcher,
 * verdict, five places, search, Room, theme, you), the viewer banner, and
 * the page at full width. The Room is a drawer (`?room=<thread>`), the
 * Health panel and the interim Needs you list are side panels
 * (`?peek=health`, `?peek=needs`), so a link opens the exact view. On a
 * phone the five places sit in a bottom bar. ⌘K (or `/`), Share and Add
 * project open from anywhere; `g` chords jump between the five places, `r`
 * opens the Room and `?` lists the keys.
 *
 * Cockpit v3 has no Room drawer: every node page carries its chat, so `/`,
 * `r`, the Room button and a `?room=` link focus (or go to) the chat of the
 * node a thread is about. Its sheets open by the address too: Needs you
 * (`?peek=needs`, ranked, once the node source serves it), the base env,
 * Try it and the keys; ⌘K finds nodes.
 */

import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AddProjectDialog } from "@/components/factory/controls/AddProjectDialog";
import { CockpitPalette } from "@/components/factory/cmdk/CockpitPalette";
import { KeyboardHelp } from "@/components/factory/cmdk/KeyboardHelp";
import {
  PLACE_HREF,
  useCockpitKeys,
  useFactoryPaletteHotkey,
} from "@/components/factory/cmdk/useFactoryPaletteHotkey";
import { useFactoryServerNowS } from "@/components/factory/factoryClock";
import { shellNeedsCount } from "@/components/factory/home/homeModel";
import { useCachedFactoryAsks } from "@/components/factory/home/homeQueries";
import { useFactoryDefinition } from "@/components/factory/insights/learnQueries";
import { splitNeedsYou } from "@/components/factory/overview/needsYouModel";
import { useFactoryStatusSummary } from "@/components/factory/overview/statusSummaryQuery";
import { ShareDialog } from "@/components/factory/people/ShareDialog";
import { RoomDrawer, mentionCount } from "@/components/factory/room/RoomDock";
import { useRoomThreads } from "@/components/factory/room/roomQueries";
import { Peek } from "@/components/factory/ui/Peek";
import { FactoryToastProvider } from "@/components/factory/ui/Toast";
import { PAGE_PALETTE_ATTRIBUTE } from "@/hooks/useCommandPaletteHotkey";
import {
  mentionNotices,
  snapshotNotices,
  useFactoryNotifications,
} from "@/hooks/useFactoryNotifications";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { useNavigate, useSearchParams } from "@/lib/routing";
import {
  FactoryShellContext,
  type FactoryShellValue,
} from "@/components/factory/shell/FactoryShellContext";
import { FactoryTopBar, fallbackVerdict } from "@/components/factory/shell/FactoryTopBar";
import { HealthPeek } from "@/components/factory/shell/HealthPeek";
import { CockpitPhoneTabBar } from "@/components/factory/shell/PhoneTabBar";
import { factoryLocation } from "@/components/factory/shell/shellRoutes";
import { ViewerBanner } from "@/components/factory/shell/ViewerBanner";
import { pageThread } from "@/components/factory/room/roomModel";
import {
  CHAT_PARAM,
  NodeChatSlotContext,
  createNodeChatSlot,
} from "@/components/factory/v3/chat/chatLayout";
import { DISCOVERY_THREAD, chatNodeRef } from "@/components/factory/v3/chat/nodeThread";
import { NODE_STALE_MS, nodeQueryKeys, useNode } from "@/components/factory/v3/nodeQueries";
import { useNeedsYou } from "@/components/factory/v3/nodeSteer";
import { V3Sheets } from "@/components/factory/v3/sheets/V3Sheets";
import { PRODUCT_REF, nodeHref } from "@/components/factory/v3/nodeRoutes";
import { useNodeSource } from "@/components/factory/v3/nodeSource";
import { DecisionsPage } from "./DecisionsPage";

/** The id of the page's main region (the skip link's target). */
export const COCKPIT_MAIN_ID = "factory-main";

/** `?room=` and `?peek=`: the drawer and side panels the address opens. */
export const ROOM_PARAM = "room";
export const PEEK_PARAM = "peek";

const NONE_HELD: ReadonlySet<string> = new Set();

export function CockpitShell({
  snapshot,
  clockOffsetS,
  updatedAt,
  rest,
  onClassic,
  onV2,
  variant = "v2",
  children,
}: {
  snapshot: FactorySnapshot;
  clockOffsetS: number;
  updatedAt: number;
  rest: string;
  /** Switch this person back to the classic cockpit. */
  onClassic: () => void;
  /** Cockpit v3: switch back to v2. */
  onV2?: () => void;
  /** v3 puts the node pages in this shell (Home is the product's node page). */
  variant?: "v2" | "v3";
  children: ReactNode;
}) {
  const fid = snapshot.factory.id;
  const [params, setParams] = useSearchParams();
  const nowS = useFactoryServerNowS(clockOffsetS);
  const phone = useIsMobileViewport();
  const location = useMemo(() => factoryLocation(rest), [rest]);
  const [sharing, setSharing] = useState(false);
  const [palette, setPalette] = useState(false);
  const [keys, setKeys] = useState(false);
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
  const summaryQuery = useFactoryStatusSummary(fid);
  // The query keeps the previous factory's answer while a switched-to one loads.
  const summary = summaryQuery.data?.factory_id === fid ? summaryQuery.data : undefined;
  const productName = useFactoryDefinition(fid).data?.effective.product?.name ?? null;
  const split = useMemo(() => splitNeedsYou(snapshot), [snapshot]);
  // Answers Home holds for Undo already left its list; the count leaves them out too.
  const [held, setHeld] = useState<ReadonlySet<string>>(NONE_HELD);
  // Home's own read of the list, from the cache only: the shell never fetches it.
  const asks = useCachedFactoryAsks(fid);
  // One count: the rows of Needs you (older servers: what waits on the viewer).
  const shellNeeds = shellNeedsCount(
    {
      count:
        summary?.counts.needs_you ??
        summary?.counts.waiting_for_you ??
        split.mine.length + split.role.length,
      at: summary?.counts.needs_you != null ? summary.generated_at : null,
    },
    held,
    asks?.factory_id === fid ? asks : null,
  );
  // Until the server's verdict is in (or when the read fails, or an older
  // server has none) the snapshot's facts speak, and they never say green.
  const verdict =
    summary?.verdict ??
    fallbackVerdict(
      snapshot,
      summaryQuery.isPending || summaryQuery.isPlaceholderData ? "checking" : "unknown",
    );
  const room = params.get(ROOM_PARAM);
  const peek = params.get(PEEK_PARAM);
  const v3 = variant === "v3";
  const [chatSlot] = useState(createNodeChatSlot);
  const queryClient = useQueryClient();
  const nodeSource = useNodeSource(v3);
  // v3 with a ranked Needs you: its count is the sheet's (every ask, also those that block nothing).
  const ranked = useNeedsYou("product", v3 && Boolean(nodeSource?.needsYou), fid).data;
  const needsYou = ranked ? ranked.items.length + ranked.blocks_nothing.length : shellNeeds;
  /**
   * v3: the node page whose chat shows a thread, with its chat open (on
   * sample data, the product's: sample nodes have no threads). #discovery
   * reads the product's page for a need to open.
   */
  const nodeChatHref = useCallback(
    async (thread: string): Promise<string> => {
      const sample = nodeSource?.kind !== "live";
      const root =
        !sample && thread === DISCOVERY_THREAD && nodeSource
          ? await queryClient
              .fetchQuery({
                queryKey: nodeQueryKeys.node(fid, nodeSource.kind, PRODUCT_REF),
                queryFn: ({ signal }) => nodeSource.node(fid, PRODUCT_REF, signal),
                staleTime: NODE_STALE_MS,
              })
              .catch(() => null)
          : null;
      const ref = chatNodeRef(thread, { snapshot, sample, root });
      return `${nodeHref(fid, ref)}?${CHAT_PARAM}=1`;
    },
    [fid, nodeSource, queryClient, snapshot],
  );
  const openThreadChat = useCallback(
    (thread: string, replace = false) => {
      void nodeChatHref(thread).then((href) => navigate(href, { replace }));
    },
    [navigate, nodeChatHref],
  );
  /** v3: focus the chat on this page, else open the chat of the page's thread. */
  const openChat = useCallback(() => {
    if (chatSlot.focus()) return;
    openThreadChat(pageThread(snapshot, location, null));
  }, [chatSlot, location, openThreadChat, snapshot]);
  // An old `?room=` link under v3 opens that thread's node with its chat
  // (once the shell knows whether the nodes are this factory's or the sample's).
  const sourceKnown = nodeSource != null;
  useEffect(() => {
    if (v3 && room != null && sourceKnown) openThreadChat(room || "factory", true);
  }, [openThreadChat, room, sourceKnown, v3]);

  const setParam = useCallback(
    (key: string, value: string | null) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value == null) next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );
  // v3: the base env the top bar names (the product's node page, read once for every page).
  const root = useNode(v3 ? PRODUCT_REF : "", fid).data;
  const env = root?.node.env;
  useCockpitKeys({
    onPalette: () => {
      if (!(v3 && chatSlot.focus())) setPalette(true);
    },
    onHelp: () => (v3 ? setParam(PEEK_PARAM, "keys") : setKeys(true)),
    onRoom: v3 ? openChat : () => setParam(ROOM_PARAM, room ?? ""),
    onGo: (place) => navigate(PLACE_HREF[place](fid)),
  });
  const shell: FactoryShellValue = useMemo(
    () => ({
      factoryId: fid,
      snapshot,
      clockOffsetS,
      nowS,
      updatedAt,
      location,
      // No dock sits beside the page: pages show their own decisions.
      wide: false,
      cockpit: "v2",
      openShare: () => setSharing(true),
      openPalette: () => setPalette(true),
      openAddProject: () => setAdding({ title: null }),
      openAddProjectTitled: (title: string | null) => setAdding({ title }),
      holdAnswers: setHeld,
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
        data-cockpit={variant}
        data-page={location.page}
        className="flex min-w-0 flex-col gap-3"
      >
        <FactoryTopBar
          snapshot={snapshot}
          location={location}
          verdict={verdict}
          productName={productName}
          needsYou={needsYou}
          mentions={mentionCount(threads)}
          phone={phone}
          onHealth={() => setParam(PEEK_PARAM, "health")}
          onPalette={() => setPalette(true)}
          onRoom={v3 ? openChat : () => setParam(ROOM_PARAM, room ?? "")}
          onShare={() => setSharing(true)}
          onAddProject={() => setAdding({ title: null })}
          onClassic={onClassic}
          onV2={onV2}
          {...(v3
            ? {
                onNeedsYou: () => setParam(PEEK_PARAM, "needs"),
                baseEnv: env
                  ? {
                      label: `${env.name} ${env.branch}${env.sha ? ` @${env.sha}` : ""}`,
                      ok: env.smoke?.ok ?? null,
                      onOpen: () => setParam(PEEK_PARAM, "env"),
                    }
                  : null,
              }
            : {})}
        />
        {viewer?.role === "viewer" && <ViewerBanner factoryId={fid} />}
        <main
          id={COCKPIT_MAIN_ID}
          tabIndex={-1}
          data-testid="factory-page-body"
          className="min-w-0 pt-1 outline-none"
        >
          {/* The cockpit toast (Undo after Apply) outlives the page that showed it. */}
          <FactoryToastProvider>
            <NodeChatSlotContext.Provider value={v3 ? chatSlot : null}>
              {children}
            </NodeChatSlotContext.Provider>
            {/* Inside the toast's reach: a verb's Undo shows there. Both are portals. */}
            <CockpitPalette
              snapshot={snapshot}
              open={palette}
              onOpenChange={setPalette}
              onShare={() => setSharing(true)}
              onAddProject={() => setAdding({ title: null })}
              onRoom={(thread) => (v3 ? openThreadChat(thread) : setParam(ROOM_PARAM, thread))}
              nodes={v3}
            />
            {v3 ? (
              <V3Sheets factoryId={fid} keys />
            ) : (
              <KeyboardHelp open={keys} onOpenChange={setKeys} />
            )}
          </FactoryToastProvider>
        </main>
      </div>
      {!v3 && (
        <RoomDrawer
          snapshot={snapshot}
          location={location}
          clockOffsetS={clockOffsetS}
          open={room != null}
          thread={room || null}
          onOpenChange={(open) => !open && setParam(ROOM_PARAM, null)}
          onThread={(thread) => setParam(ROOM_PARAM, thread)}
        />
      )}
      <HealthPeek
        open={peek === "health"}
        onOpenChange={(open) => !open && setParam(PEEK_PARAM, null)}
        snapshot={snapshot}
        nowS={nowS}
        verdict={verdict}
        spend={summary?.spend ?? null}
      />
      <Peek
        open={peek === "needs" && !(v3 && nodeSource?.needsYou)}
        onOpenChange={(open) => !open && setParam(PEEK_PARAM, null)}
        title="Needs you"
        description="Everything the factory asked a person, answered and expired included."
        wide
      >
        {peek === "needs" && <DecisionsPage />}
      </Peek>
      {phone && <CockpitPhoneTabBar factoryId={fid} location={location} needsYou={needsYou} />}
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
