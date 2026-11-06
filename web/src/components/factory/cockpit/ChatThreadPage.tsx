// One conversation (`/chats/<thread>`), a messenger: who you talk to on
// top, newest at the bottom, the box at the bottom. It is the node chat of
// the node the thread is about (the same component as on node pages), shown
// as the page: system events stay behind its Activity line.

import { useMemo } from "react";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { useRoomThreads } from "../room/roomQueries";
import { useFactoryShell } from "../shell/FactoryShellContext";
import { FOCUS_RING } from "../ui/statusTokens";
import { NodeChat } from "../v3/chat/NodeChat";
import { chatNodeRef } from "../v3/chat/nodeThread";
import { type NodeActions, NodeContextProvider } from "../v3/NodeContext";
import { useNode, usePrefetchNode } from "../v3/nodeQueries";
import { PRODUCT_REF, nodeHref } from "../v3/nodeRoutes";
import { useAnswerAsk } from "../v3/nodeSteer";
import { useSheetActions } from "../v3/sheets/V3Sheets";
import { chatName, useNodeNames } from "./chats";
import { chatsHref } from "./cockpitRoutes";

function Loading() {
  return (
    <div aria-busy="true" aria-label="Loading the chat" className="flex flex-col gap-4 py-4">
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex gap-2">
          <div className="size-6 shrink-0 animate-pulse rounded-full bg-muted" />
          <div className="flex flex-1 flex-col gap-1.5">
            <div className="h-3.5 w-28 animate-pulse rounded bg-muted" />
            <div className="h-3.5 w-2/3 animate-pulse rounded bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function ChatThreadPage({ thread }: { thread: string }) {
  const { factoryId: fid, snapshot } = useFactoryShell();
  const threads = useRoomThreads(fid, { nodes: true }).data?.data;
  const view = threads?.find((t) => t.key === thread) ?? null;
  const ref = view?.node_ref || chatNodeRef(thread, { snapshot, sample: false });
  const node = useNode(ref || PRODUCT_REF, fid);
  const names = useNodeNames(fid);
  const name = view
    ? chatName(view, snapshot, names)
    : thread === "factory"
      ? "Factory chat"
      : null;
  const prefetch = usePrefetchNode(fid);
  const answer = useAnswerAsk(ref, { factoryId: fid });
  const sheets = useSheetActions();
  const actions = useMemo<NodeActions>(
    () => ({ ...sheets, factoryId: fid, prefetch, onAnswer: answer }),
    [answer, fid, prefetch, sheets],
  );
  const page = node.data;
  return (
    <div
      data-testid="cockpit-chat-thread"
      data-thread={thread}
      className="mx-auto flex max-w-[52rem] min-w-0 flex-col"
    >
      <header className="mb-2 flex min-w-0 items-center gap-3 border-b border-border pb-2">
        <Link
          to={chatsHref(fid)}
          componentId="factory.cockpit.chat.back"
          data-testid="cockpit-chat-back"
          className={cn("shrink-0 text-[13px] text-status-blue-fg hover:underline", FOCUS_RING)}
        >
          ‹ Chats
        </Link>
        <h1 className="min-w-0 flex-1 truncate text-[15px] font-semibold">
          {name ?? (
            <span className="inline-block h-4 w-40 animate-pulse rounded bg-muted align-middle" />
          )}
          {view?.responder_title && (
            <span className="ml-2 text-[12.5px] font-normal text-muted-foreground">
              with {view.responder_title}
            </span>
          )}
        </h1>
        {page && page.node.ref !== PRODUCT_REF && (
          <Link
            to={nodeHref(fid, page.node.ref)}
            componentId="factory.cockpit.chat.node"
            data-testid="cockpit-chat-node"
            onMouseEnter={() => prefetch(page.node.ref)}
            className={cn(
              "shrink-0 text-[12.5px] text-muted-foreground hover:text-foreground hover:underline",
              FOCUS_RING,
            )}
          >
            Open its page ›
          </Link>
        )}
      </header>
      {page ? (
        <NodeContextProvider value={actions}>
          <NodeChat page={page} layout="page" threadKey={thread} />
        </NodeContextProvider>
      ) : node.isError ? (
        <p role="alert" className="text-sm text-status-red-fg">
          This chat could not be opened.{" "}
          <button type="button" onClick={() => void node.refetch()} className="underline">
            Try again
          </button>
        </p>
      ) : (
        <Loading />
      )}
    </div>
  );
}
