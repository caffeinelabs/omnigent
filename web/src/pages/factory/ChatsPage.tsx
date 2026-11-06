/**
 * `/chats`: every conversation in the factory, the ones you wrote in first,
 * then the newest, each with its unread count and the last line said there.
 * `/chats/<thread>` opens one.
 */

import { useMemo } from "react";
import { MessagesSquareIcon } from "lucide-react";
import { type ChatRow, chatRows, useNodeNames } from "@/components/factory/cockpit/chats";
import { chatsHref } from "@/components/factory/cockpit/cockpitRoutes";
import { ChatThreadPage } from "@/components/factory/cockpit/ChatThreadPage";
import { useFactoryNow } from "@/components/factory/factoryClock";
import { useRoomThreads } from "@/components/factory/room/roomQueries";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { FOCUS_RING } from "@/components/factory/ui/statusTokens";
import { Link, useParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

function when(at: number, now: Date): string {
  if (!at) return "";
  const date = new Date(at * 1000);
  const min = Math.round((now.getTime() - date.getTime()) / 60_000);
  if (min < 1) return "now";
  if (min < 60) return `${min}m`;
  if (min < 24 * 60) return `${Math.round(min / 60)}h`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function Initial({ name }: { name: string }) {
  return (
    <span
      aria-hidden
      className="grid size-9 shrink-0 place-items-center rounded-full bg-muted text-[13px] font-semibold text-muted-foreground"
    >
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}

function Row({ fid, row, now }: { fid: string; row: ChatRow; now: Date }) {
  return (
    <li className="border-t border-border first:border-t-0">
      <Link
        to={chatsHref(fid, row.key)}
        componentId="factory.cockpit.chats.row"
        data-testid="cockpit-chat-row"
        data-thread={row.key}
        data-mine={row.mine ? "true" : "false"}
        className={cn("flex min-w-0 items-center gap-3 px-3 py-2.5 hover:bg-muted/50", FOCUS_RING)}
      >
        <Initial name={row.name} />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex min-w-0 items-baseline gap-2">
            <span
              className={cn(
                "min-w-0 truncate text-[14px]",
                row.unread ? "font-semibold text-foreground" : "font-medium",
              )}
            >
              {row.name}
            </span>
            <span className="shrink-0 text-[11.5px] text-foreground-tertiary">{row.kind}</span>
            <span className="flex-1" />
            <span className="shrink-0 text-[12px] text-foreground-tertiary tabular-nums">
              {when(row.at, now)}
            </span>
          </span>
          <span className="flex min-w-0 items-center gap-2">
            <span
              className={cn(
                "min-w-0 flex-1 truncate text-[13px]",
                row.unread ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {row.last ? (
                <>
                  <span className="text-foreground-tertiary">{row.last.who}: </span>
                  {row.last.text}
                </>
              ) : (
                <span className="text-foreground-tertiary">
                  {row.messages ? "Only updates so far" : "No messages yet"}
                  {row.with ? ` · ${row.with} answers here` : ""}
                </span>
              )}
            </span>
            {row.unread > 0 && (
              <span
                data-testid="cockpit-chat-unread"
                className="inline-flex h-[18px] min-w-[18px] shrink-0 items-center justify-center rounded-full bg-status-blue px-1.5 text-[11px] font-semibold text-white tabular-nums"
              >
                {row.unread}
              </span>
            )}
          </span>
        </span>
      </Link>
    </li>
  );
}

function ChatList() {
  const { factoryId: fid, snapshot } = useFactoryShell();
  const threads = useRoomThreads(fid, { nodes: true });
  const names = useNodeNames(fid);
  const now = useFactoryNow();
  const rows = useMemo(
    () => chatRows(threads.data?.data ?? [], snapshot, names),
    [threads.data, snapshot, names],
  );
  const talked = rows.filter((r) => r.at > 0);
  const quiet = rows.filter((r) => r.at === 0);
  return (
    <div
      data-testid="cockpit-chats-page"
      className="mx-auto flex max-w-[48rem] min-w-0 flex-col gap-4"
    >
      <h1 className="text-xl font-semibold tracking-tight">Chats</h1>
      {threads.isPending ? (
        <div aria-busy="true" className="overflow-hidden rounded-xl ring-1 ring-border">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="flex items-center gap-3 border-t border-border px-3 py-3 first:border-t-0"
            >
              <div className="size-9 animate-pulse rounded-full bg-muted" />
              <div className="flex flex-1 flex-col gap-1.5">
                <div className="h-4 w-1/3 animate-pulse rounded bg-muted" />
                <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
              </div>
            </div>
          ))}
        </div>
      ) : threads.isError ? (
        <p role="alert" className="text-sm text-status-red-fg">
          The chats could not be read.{" "}
          <button type="button" onClick={() => void threads.refetch()} className="underline">
            Try again
          </button>
        </p>
      ) : (
        <>
          {talked.length > 0 ? (
            <ul
              data-testid="cockpit-chats"
              className="overflow-hidden rounded-xl bg-card-solid ring-1 ring-border"
            >
              {talked.map((r) => (
                <Row key={r.key} fid={fid} row={r} now={now} />
              ))}
            </ul>
          ) : (
            <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
              <MessagesSquareIcon aria-hidden className="size-6 opacity-60" />
              No conversations yet.
            </div>
          )}
          {quiet.length > 0 && (
            <details className="group">
              <summary className="cursor-pointer px-0.5 text-[12.5px] text-muted-foreground hover:text-foreground">
                {quiet.length} quiet chat{quiet.length === 1 ? "" : "s"} (no messages yet)
              </summary>
              <ul className="mt-2 overflow-hidden rounded-xl bg-card-solid ring-1 ring-border">
                {quiet.map((r) => (
                  <Row key={r.key} fid={fid} row={r} now={now} />
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
}

export function ChatsPage() {
  const { thread } = useParams<{ thread?: string }>();
  return thread ? <ChatThreadPage thread={thread} /> : <ChatList />;
}
