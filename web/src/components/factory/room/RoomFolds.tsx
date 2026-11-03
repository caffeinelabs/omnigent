/**
 * Room noise, folded but never hidden: a base's moves as one line ("base
 * main moved 7 times since 14:12 → 0c8a4e1", each move a click away and all
 * of them in Activity), an incident's older cards as one quiet line above
 * its live card, and other threads with news as one line each (cockpit v3's
 * node chat: "assistant-core · 1 new · …"). Times carry their date when it
 * is not today.
 */

import { useId, useState } from "react";
import { ArrowRightIcon, GitBranchIcon, OctagonAlertIcon } from "lucide-react";
import { findRoomTokens } from "@/lib/composerMentions";
import type { BaseMoveView, ChatMessageView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { useRoomCardData } from "../cards/RoomCardContext";
import { SystemLine } from "../cards/SystemLine";
import { useFactoryNow } from "../factoryClock";
import { shortSha } from "../factoryFormat";
import { activityPageHref } from "../factoryRoutes";
import { clockText } from "../overview/nowCardModel";
import { repoName } from "./roomFold";
import { type RefSources, refView } from "./roomRefs";
import { RefChip, RoomText } from "./RoomText";

function Toggle({
  open,
  onToggle,
  controls,
  testId,
}: {
  open: boolean;
  onToggle: () => void;
  controls: string;
  testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      aria-controls={controls}
      data-testid={testId}
      className="cursor-pointer rounded-sm whitespace-nowrap text-foreground/80 underline-offset-2 hover:text-foreground hover:underline focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
    >
      {open ? "Hide" : "Show each"}
    </button>
  );
}

function moveText(move: BaseMoveView): string {
  const pr = move.pr ? ` (#${move.pr})` : "";
  return `${shortSha(move.from_sha) || "?"} → ${shortSha(move.to_sha)}${pr}`;
}

/** The viewer's "now" in epoch seconds, for "today or with its date". */
function useNowS(): number {
  return Math.floor(useFactoryNow().getTime() / 1000);
}

/**
 * One line for a base's moves: a batched line, or a run of one-move lines. A
 * single move reads as it always did.
 */
export function BaseMovesLine({
  base,
  repo = null,
  moves,
  messages,
  sources,
}: {
  base: string;
  /** `owner/name` to name, when the factory watches more than one repo. */
  repo?: string | null;
  moves: readonly BaseMoveView[];
  messages: readonly ChatMessageView[];
  sources: RefSources;
}) {
  const { factoryId } = useRoomCardData();
  const [open, setOpen] = useState(false);
  const listId = useId();
  const nowS = useNowS();
  const first = moves[0];
  const last = moves[moves.length - 1];
  if (!first || !last || (moves.length === 1 && messages.length === 1)) {
    return messages[0] ? <SystemLine message={messages[0]} sources={sources} /> : null;
  }
  return (
    <div
      data-testid={`room-base-moves-${base}`}
      data-moves={moves.length}
      className="flex min-w-0 flex-col py-0.5 text-sm text-muted-foreground"
    >
      <div className="flex min-w-0 items-start gap-2">
        <GitBranchIcon aria-hidden className="mt-[3px] size-3.5 shrink-0 opacity-70" />
        <p className="min-w-0 flex-1 break-words">
          base <span className="font-mono">{base}</span>
          {repo && (
            <span title={repo} data-testid="room-base-moves-repo">
              {" "}
              ({repoName(repo)})
            </span>
          )}{" "}
          moved {moves.length} times since {clockText(first.at, nowS)} →{" "}
          <span className="font-mono text-foreground/80">{shortSha(last.to_sha)}</span>{" "}
          <Toggle
            open={open}
            onToggle={() => setOpen((v) => !v)}
            controls={listId}
            testId={`room-base-moves-toggle-${base}`}
          />
        </p>
        <time
          className="shrink-0 font-mono text-xs tabular-nums opacity-80"
          title="The latest move"
        >
          {clockText(last.at, nowS)}
        </time>
      </div>
      {open && (
        <ol
          id={listId}
          data-testid={`room-base-moves-list-${base}`}
          className="mt-1 ml-[0.4375rem] flex min-w-0 flex-col gap-0.5 border-l border-border pl-3.5 text-xs"
        >
          {moves.map((move) => (
            <li
              key={`${move.at}-${move.from_sha}-${move.to_sha}`}
              className="flex min-w-0 items-baseline gap-2"
            >
              <time className="shrink-0 font-mono tabular-nums">{clockText(move.at, nowS)}</time>
              <RoomText
                body={moveText(move)}
                sources={sources}
                className="min-w-0 font-mono text-xs"
              />
            </li>
          ))}
          <li className="pt-0.5">
            <Link
              to={activityPageHref(factoryId)}
              componentId="factory.room.base_moves_activity"
              className="inline-flex items-center gap-1 rounded-sm text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            >
              Every move is in Activity
              <ArrowRightIcon aria-hidden className="size-3" />
            </Link>
          </li>
        </ol>
      )}
    </div>
  );
}

/** `INC-14` as a live chip when the viewer's lists know it, else as text. */
function RefLabel({ text, sources }: { text: string; sources: RefSources }) {
  const token = findRoomTokens(text)[0];
  const view = token ? refView(token, sources) : null;
  if (!token || !view) return <span className="font-mono">{text}</span>;
  return <RefChip token={token} view={view} />;
}

/** An incident's older cards (its live card is further down) as one quiet line. */
export function IssueRepeats({
  refText,
  messages,
  sources,
}: {
  refText: string;
  messages: readonly ChatMessageView[];
  sources: RefSources;
}) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const nowS = useNowS();
  const first = messages[0];
  const last = messages[messages.length - 1];
  if (!first || !last) return null;
  const row = (message: ChatMessageView, className?: string) => (
    <div className={cn("flex min-w-0 items-start gap-2", className)}>
      <RoomText body={message.body} sources={sources} className="min-w-0 flex-1 text-sm" />
      <time className="shrink-0 font-mono text-xs tabular-nums opacity-80">
        {clockText(message.created_at, nowS)}
      </time>
    </div>
  );
  if (messages.length === 1) {
    return (
      <div
        data-testid={`room-issue-repeat-${first.seq}`}
        className="flex min-w-0 items-start gap-2 py-0.5 text-sm text-muted-foreground"
      >
        <OctagonAlertIcon aria-hidden className="mt-[3px] size-3.5 shrink-0 opacity-70" />
        {row(first, "flex-1")}
      </div>
    );
  }
  return (
    <div
      data-testid={`room-issue-repeats-${refText}`}
      data-count={messages.length}
      className="flex min-w-0 flex-col py-0.5 text-sm text-muted-foreground"
    >
      <div className="flex min-w-0 items-start gap-2">
        <OctagonAlertIcon aria-hidden className="mt-[3px] size-3.5 shrink-0 opacity-70" />
        <p className="min-w-0 flex-1 break-words">
          <RefLabel text={refText} sources={sources} /> was posted {messages.length} times (
          {clockText(first.created_at, nowS)}–{clockText(last.created_at, nowS)}); its live card is
          below.{" "}
          <Toggle
            open={open}
            onToggle={() => setOpen((v) => !v)}
            controls={listId}
            testId={`room-issue-repeats-toggle-${refText}`}
          />
        </p>
      </div>
      {open && (
        <ol
          id={listId}
          className="mt-1 ml-[0.4375rem] flex min-w-0 flex-col gap-0.5 border-l border-border pl-3.5"
        >
          {messages.map((message) => (
            <li key={message.id}>{row(message)}</li>
          ))}
        </ol>
      )}
    </div>
  );
}

/** Another thread with unread messages, as one line that opens it. */
export interface ThreadNewsLine {
  key: string;
  title: string;
  unread: number;
  /** The newest unread message that mentions the viewer. */
  text?: string | null;
}

/**
 * Threads with news as one line each ("assistant-core · 1 new · …"), each a
 * link to `href`; past `shown` lines the rest are one "Show each" away.
 */
export function ThreadNewsLines({
  lines,
  shown = 3,
}: {
  lines: readonly (ThreadNewsLine & { href: string })[];
  shown?: number;
}) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (!lines.length) return null;
  const visible = open ? lines : lines.slice(0, shown);
  const hidden = lines.length - visible.length;
  return (
    <div data-testid="room-thread-news" className="flex min-w-0 flex-col gap-1 pb-2">
      <ul id={listId} className="flex min-w-0 flex-col gap-1">
        {visible.map((line) => (
          <li key={line.key} data-testid={`room-thread-news-${line.key}`} className="min-w-0">
            <Link
              to={line.href}
              componentId="factory.room.thread_news"
              className="flex min-w-0 items-baseline gap-2 rounded-lg border border-border bg-card-solid px-2.5 py-1.5 text-[12.5px] hover:border-border-strong focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
            >
              <b className="shrink-0 font-semibold">{line.title}</b>
              <span className="shrink-0 text-status-blue-fg">{line.unread} new</span>
              {line.text && (
                <span className="min-w-0 truncate text-muted-foreground">{line.text}</span>
              )}
            </Link>
          </li>
        ))}
      </ul>
      {(hidden > 0 || open) && lines.length > shown && (
        <p className="px-1 text-xs text-muted-foreground">
          {hidden > 0 ? `${hidden} more with news · ` : ""}
          <Toggle
            open={open}
            onToggle={() => setOpen((v) => !v)}
            controls={listId}
            testId="room-thread-news-toggle"
          />
        </p>
      )}
    </div>
  );
}
