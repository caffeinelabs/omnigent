/**
 * One Room thread, live: its header (who answers and whether they work, who
 * is here, the bus-view toggle), the messages (older pages load on scroll,
 * `before_seq`), cards for every structured object, "Atlas is working… · n
 * queued", notes from slash commands, and the composer. Reading the thread
 * marks it read here, in this browser's other tabs and on the server.
 *
 * Cockpit v3's node chat passes `node`: its own title (no bus toggle or Room
 * page link), lines above the messages (children with news), cards only the
 * viewer sees after them, the cards the Room does not draw, and the
 * composer's node options.
 */

import { type ReactNode, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDownIcon,
  CircleDotIcon,
  ExternalLinkIcon,
  Loader2Icon,
  MessagesSquareIcon,
  NetworkIcon,
  XIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useFactoryEvents, useFactoryPeople } from "@/hooks/useFactory";
import type { ActiveRoomToken } from "@/lib/composerMentions";
import type { ChatMessageView, FactorySnapshot, TaskView } from "@/lib/factoryApi";
import { type ChatFeed, fetchFactoryTasks, postRoomMessage } from "@/lib/factoryRoomApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { RoomCardContext, type RoomCardData } from "../cards/RoomCardContext";
import { AddProjectDialog } from "../controls/AddProjectDialog";
import { EditCharterDialog } from "../controls/EditCharterDialog";
import { FactoryLoadError } from "../FactoryLoadError";
import { roomPageHref } from "../factoryRoutes";
import { PresenceFace } from "../shell/FactoryPresence";
import { useOptionalFactoryShell } from "../shell/FactoryShellContext";
import { FoldableMarkdown } from "../transcript/FoldableMarkdown";
import { roomCandidateList } from "./roomCandidates";
import { RoomComposer, type RoomComposerNodeOptions } from "./RoomComposer";
import {
  type FoldedItem,
  foldRoomItems,
  namesRepos,
  supersededCards,
  supersededIssueCards,
} from "./roomFold";
import { BaseMovesLine, IssueRepeats } from "./RoomFolds";
import { BusRow, RoomMessage } from "./RoomMessage";
import {
  busFilterFor,
  busLines,
  continuesRun,
  interleave,
  lastSeq,
  peopleHere,
  responderLine,
  responderStatus,
  threadTitle,
} from "./roomModel";
import {
  flattenPages,
  messagesQueryKey,
  roomQueryKey,
  threadsQueryKey,
  useMarkThreadRead,
  useProposals,
  useReadMarkSync,
  useRoomIssues,
  useRoomLoops,
  useRoomMessages,
  useRoomThreads,
} from "./roomQueries";
import type { RefSources } from "./roomRefs";
import type { CommandNote } from "./useRoomCommandRunner";
import { useRoomCommandRunner } from "./useRoomCommandRunner";

const BUS_KEY = "omnigent.factory.room.busView";

function loadBus(): boolean {
  try {
    return window.localStorage.getItem(BUS_KEY) === "1";
  } catch {
    return false;
  }
}

function saveBus(on: boolean): void {
  try {
    window.localStorage.setItem(BUS_KEY, on ? "1" : "0");
  } catch {
    // The toggle lasts this page only.
  }
}

interface LocalNote extends CommandNote {
  id: number;
}

/** A command's outcome under the messages; only this viewer sees it. */
function NoteRow({ note, onDismiss }: { note: LocalNote; onDismiss: () => void }) {
  return (
    <li
      data-testid="room-note"
      data-ok={note.ok ? "true" : "false"}
      className={cn(
        "my-1 flex min-w-0 flex-col gap-1 rounded-lg border border-dashed px-3 py-2 text-sm",
        note.ok ? "border-border" : "border-destructive/40 text-destructive",
      )}
    >
      <p className="flex min-w-0 items-center gap-1.5">
        <CircleDotIcon aria-hidden className="size-3.5 shrink-0 opacity-70" />
        <span className="min-w-0 flex-1 break-words">{note.text}</span>
        <span className="shrink-0 text-xs text-muted-foreground">only you</span>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="shrink-0 cursor-pointer rounded-sm text-muted-foreground hover:text-foreground"
        >
          <XIcon className="size-3.5" />
        </button>
      </p>
      {note.markdown && (
        <FoldableMarkdown
          text={note.markdown}
          className="text-sm text-foreground"
          testId="room-status-digest"
        />
      )}
    </li>
  );
}

/** A message on its way: the viewer's words, marked as sending until the server answers. */
function pendingMessage(
  id: string,
  thread: string,
  body: string,
  author: string,
  after: number,
): ChatMessageView {
  return {
    id,
    factory_id: "",
    thread,
    seq: after + 0.5,
    author_kind: "human",
    author,
    author_title: null,
    author_role: null,
    body,
    mentions: [],
    refs: [],
    reply_to: null,
    card: null,
    routed: [],
    auto_reply: false,
    created_at: Math.floor(Date.now() / 1000),
    lane: "conversation",
    pending: true,
  };
}

function SkeletonRows() {
  return (
    <div aria-busy="true" aria-label="Loading messages" className="flex flex-col gap-4 p-3">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex gap-2">
          <div className="size-6 shrink-0 animate-pulse rounded-full bg-muted" />
          <div className="flex flex-1 flex-col gap-1.5">
            <div className="h-3.5 w-28 animate-pulse rounded bg-muted" />
            <div
              className={cn("h-3.5 animate-pulse rounded bg-muted", i % 2 ? "w-2/3" : "w-5/6")}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Cockpit v3's node chat around one Room thread. */
export interface RoomThreadNodeOptions {
  /** The header's title; the bus toggle, the responder chip and the Room page link are left out. */
  title: ReactNode;
  /** Under the header, outside the scroller (a run's "this is its task's thread"). */
  lead?: ReactNode;
  /** Inside the scroller, above the messages (children with news). */
  top?: ReactNode;
  /** List items after the messages: cards only this viewer sees. */
  bottom?: ReactNode;
  /** Grows with `bottom`, so the thread keeps to its newest line. */
  bottomCount?: number;
  /** What an empty thread says instead of "Nothing in #x yet". */
  empty?: ReactNode;
  /** Cards the Room does not draw (`why`, `closed_alarm`, `offer`, `preview_applied`); `fallback` when it cannot draw one. */
  card?: (message: ChatMessageView, attached: boolean, fallback: ReactNode) => ReactNode;
  composer?: RoomComposerNodeOptions;
  /** Where each item goes: inline (`conversation`), the Activity list, or nowhere. All inline when absent. */
  lane?: (item: FoldedItem) => "conversation" | "activity" | "hidden";
  /** The Activity line under the header, from the activity items (`render` draws one as the thread would). */
  activity?: (items: FoldedItem[], render: (item: FoldedItem) => ReactNode) => ReactNode;
  /** A card under a person's or agent's message to leave out (its ask shows elsewhere on the page). */
  hideCard?: (message: ChatMessageView) => boolean;
  /** Read the thread with each row's lane (a server without feeds ignores it). */
  feed?: ChatFeed;
}

export interface RoomThreadViewProps {
  snapshot: FactorySnapshot;
  clockOffsetS: number;
  /** Thread key: `factory`, `improvements`, `project:<slug>`, `pr:<id>`. */
  thread: string;
  /** `page`: the Room page's middle column; `dock`: the docked column or the phone sheet. */
  variant?: "page" | "dock";
  /** On screen now (dock tab open, sheet open, tab visible): reading marks it read. */
  visible?: boolean;
  /** Extra header content (the dock's thread switcher). */
  headerStart?: ReactNode;
  className?: string;
  style?: React.CSSProperties;
  /** Cockpit v3's node chat (absent elsewhere). */
  node?: RoomThreadNodeOptions;
}

export function RoomThreadView({
  snapshot,
  clockOffsetS,
  thread,
  variant = "page",
  visible = true,
  headerStart,
  className,
  style,
  node,
}: RoomThreadViewProps) {
  const fid = snapshot.factory.id;
  const queryClient = useQueryClient();
  const shell = useOptionalFactoryShell();
  const dock = variant === "dock" || node != null;
  const nodeCard = node?.card ?? null;
  const title = threadTitle(snapshot, thread);
  const threads = useRoomThreads(fid);
  const feed = node?.feed;
  const messagesQuery = useRoomMessages(fid, thread, feed);
  const messages = useMemo(() => flattenPages(messagesQuery.data), [messagesQuery.data]);
  const threadView = threads.data?.data.find((t) => t.key === thread) ?? null;
  const cardTypes = useMemo(() => new Set(messages.map((m) => m.card?.type)), [messages]);
  const [trigger, setTrigger] = useState<ActiveRoomToken["trigger"] | null>(null);
  const proposals = useProposals(fid);
  const loops = useRoomLoops(fid, cardTypes.has("loop"));
  const issues = useRoomIssues(
    fid,
    cardTypes.has("issue") || trigger === "INC-" || trigger === "F-",
  );
  const people = useFactoryPeople(fid);
  const tasks = useQuery<TaskView[]>({
    queryKey: [...roomQueryKey(fid), "tasks", "all"],
    queryFn: async ({ signal }) => (await fetchFactoryTasks(fid, null, signal)).tasks,
    enabled: trigger === "T-",
    retry: false,
  });
  const [busOn, setShowBus] = useState(loadBus);
  // The node chat has no bus toggle, so it never interleaves the bus.
  const showBus = busOn && !node;
  const busEvents = useFactoryEvents(showBus ? fid : null, {
    kinds: ["message.queued", "message.bounced"],
    limit: 200,
    ...busFilterFor(snapshot, thread),
  });
  const lines = useMemo(
    () => busLines(busEvents.data?.pages.flatMap((p) => p.data) ?? []),
    [busEvents.data],
  );
  const items = useMemo(() => {
    const all = interleave(messages, lines, showBus);
    return foldRoomItems(all, supersededIssueCards(messages));
  }, [messages, lines, showBus]);
  const laneOf = node?.lane;
  const { shown, activity } = useMemo(() => {
    if (!laneOf) return { shown: items, activity: [] as FoldedItem[] };
    const inline: FoldedItem[] = [];
    const folded: FoldedItem[] = [];
    for (const item of items) {
      const lane = laneOf(item);
      if (lane === "conversation") inline.push(item);
      else if (lane === "activity") folded.push(item);
    }
    return { shown: inline, activity: folded };
  }, [items, laneOf]);
  const byId = useMemo(() => new Map(messages.map((m) => [m.id, m])), [messages]);
  const nameRepos = namesRepos(snapshot, items);
  const status = responderStatus(snapshot, threadView, messages);
  const statusLine = responderLine(status);
  const here = peopleHere(snapshot.presence, snapshot, thread);
  const [notes, setNotes] = useState<LocalNote[]>([]);
  const noteSeq = useRef(0);
  const pendingSeq = useRef(0);
  const [adding, setAdding] = useState<{ title: string | null } | null>(null);
  const [charter, setCharter] = useState<string | null>(null);
  const channel = useReadMarkSync();
  const newest = lastSeq(messages);
  useMarkThreadRead(fid, thread, newest, visible && messagesQuery.isSuccess, channel);

  // A new thread starts without the previous one's notes.
  useEffect(() => setNotes([]), [thread]);

  const runCommand = useRoomCommandRunner(fid, {
    openAddProject: (name) => {
      if (shell) shell.openAddProjectTitled(name);
      else setAdding({ title: name });
    },
    openCharter: setCharter,
  });

  const sources: RefSources = {
    snapshot,
    proposals: proposals.data?.data ?? null,
    people: people.data?.data ?? null,
    issues: issues.data ?? null,
    viewerId: snapshot.viewer?.user_id ?? null,
  };
  // The node chat shows one live card per proposal: earlier copies are their line.
  const repeated = useMemo(
    () => (node ? supersededCards(messages, ["proposal"]) : null),
    [messages, node],
  );
  const cardData: RoomCardData = useMemo(
    () => ({
      factoryId: fid,
      snapshot,
      clockOffsetS,
      proposals: proposals.data?.data ?? null,
      loops: loops.data ?? null,
      issues: issues.data ?? null,
      people: people.data?.data ?? null,
      compact: dock,
      nodeCard,
      repeated,
      hidden: node?.hideCard ?? null,
    }),
    [
      fid,
      snapshot,
      clockOffsetS,
      proposals.data,
      loops.data,
      issues.data,
      people.data,
      dock,
      nodeCard,
      repeated,
      node?.hideCard,
    ],
  );
  const candidates = useMemo(
    () =>
      roomCandidateList({
        snapshot,
        people: people.data?.data,
        proposals: proposals.data?.data,
        tasks: tasks.data,
        issues: issues.data,
      }),
    [snapshot, people.data, proposals.data, tasks.data, issues.data],
  );

  // ── Scrolling: stick to the bottom; keep the place when older pages load ──
  const scrollerRef = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const restore = useRef<number | null>(null);
  const [showJump, setShowJump] = useState(false);
  const onScroll = () => {
    const el = scrollerRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    atBottom.current = near;
    setShowJump(!near);
    if (el.scrollTop < 80 && messagesQuery.hasNextPage && !messagesQuery.isFetchingNextPage) {
      restore.current = el.scrollHeight - el.scrollTop;
      void messagesQuery.fetchNextPage();
    }
  };
  useLayoutEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    if (restore.current != null) {
      el.scrollTop = el.scrollHeight - restore.current;
      restore.current = null;
    } else if (atBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
    // Messages too: an older page can fold into the first line without adding an item.
  }, [shown.length, messages.length, notes.length, node?.bottomCount]);
  useLayoutEffect(() => {
    atBottom.current = true;
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thread]);
  const jumpDown = () => {
    const el = scrollerRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };

  const addNote = (note: CommandNote | null) => {
    if (!note) return;
    noteSeq.current += 1;
    const id = noteSeq.current;
    atBottom.current = true;
    setNotes((prev) => [...prev, { ...note, id }]);
  };

  const send = async (body: string, mentions: string[]) => {
    // Shown at once as "sending", then swapped for the server's row (or dropped on a refusal).
    const pendingId = `pending-${(pendingSeq.current += 1)}`;
    const key = messagesQueryKey(fid, thread, feed);
    type Pages = typeof messagesQuery.data;
    const edit = (change: (rows: ChatMessageView[]) => ChatMessageView[]) =>
      queryClient.setQueryData<Pages>(key, (prev) => {
        if (!prev || prev.pages.length === 0) return prev;
        const [first, ...rest] = prev.pages;
        return { ...prev, pages: [{ ...first!, data: change(first!.data) }, ...rest] };
      });
    atBottom.current = true;
    edit((rows) => [
      ...rows,
      pendingMessage(pendingId, thread, body, snapshot.viewer?.user_id ?? "you", lastSeq(rows)),
    ]);
    let res: Awaited<ReturnType<typeof postRoomMessage>>;
    try {
      res = await postRoomMessage(fid, { thread, body, mentions });
    } catch (error) {
      edit((rows) => rows.filter((m) => m.id !== pendingId));
      throw error;
    }
    atBottom.current = true;
    edit((rows) => {
      const kept = rows.filter((m) => m.id !== pendingId);
      const fresh: ChatMessageView[] = [res.message, ...res.notes].filter(
        (m) => !kept.some((d) => d.id === m.id),
      );
      return [...kept, ...fresh];
    });
    void queryClient.invalidateQueries({ queryKey: threadsQueryKey(fid) });
    void queryClient.invalidateQueries({ queryKey: key });
  };

  const renderItem = (item: FoldedItem, prev?: FoldedItem): ReactNode => {
    if (item.type === "bus") return <BusRow key={item.key} line={item.line} />;
    if (item.type === "base" || item.type === "repeats") {
      return (
        <li
          key={item.key}
          data-testid={`room-message-${item.messages[0]!.seq}`}
          data-kind="system"
          className="py-1"
        >
          {item.type === "base" ? (
            <BaseMovesLine
              base={item.base}
              repo={nameRepos ? item.repo : null}
              moves={item.moves}
              messages={item.messages}
              sources={sources}
            />
          ) : (
            <IssueRepeats refText={item.ref} messages={item.messages} sources={sources} />
          )}
        </li>
      );
    }
    const prevMessage = prev?.type === "message" ? prev.message : undefined;
    return (
      <RoomMessage
        key={item.key}
        message={item.message}
        sources={sources}
        continued={continuesRun(prevMessage, item.message)}
        replyTo={item.message.reply_to ? (byId.get(item.message.reply_to) ?? null) : null}
      />
    );
  };
  const empty = laneOf ? shown.length === 0 : messages.length === 0;

  const charterProject = charter ? snapshot.projects.find((p) => p.slug === charter) : null;
  const responderBadge =
    status.state === "working"
      ? "bg-session-active animate-pulse motion-reduce:animate-none"
      : status.state === "idle"
        ? "bg-success"
        : status.state === "paused"
          ? "bg-muted-foreground/50"
          : "bg-destructive";

  return (
    <RoomCardContext.Provider value={cardData}>
      <section
        data-testid="room-thread"
        data-thread={thread}
        data-variant={variant}
        aria-label={`Room ${title}`}
        className={cn(
          "flex min-h-0 min-w-0 flex-col",
          !dock && "rounded-xl border border-border bg-card",
          className,
        )}
        style={style}
      >
        <header
          className={cn(
            "flex min-w-0 shrink-0 items-center gap-x-2 gap-y-1",
            // The dock keeps one line: the responder's name gives way first.
            dock ? "flex-nowrap pb-2" : "flex-wrap border-b border-border px-3 py-2",
          )}
        >
          {node?.title ?? headerStart ?? (
            <h2 className="flex min-w-0 items-center gap-1.5 text-ui font-semibold">
              <MessagesSquareIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
              <span className="truncate font-mono" data-testid="room-thread-title">
                {title}
              </span>
            </h2>
          )}
          {status.name && !node && (
            <span
              data-testid="room-responder"
              data-state={status.state}
              className={cn(
                "inline-flex min-w-0 items-center gap-1.5 text-sm text-muted-foreground",
                dock && "flex-1",
              )}
              title={`${status.name} answers here`}
            >
              <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", responderBadge)} />
              <span className="truncate">{status.name}</span>
            </span>
          )}
          <span className="ml-auto flex shrink-0 items-center gap-2">
            {here.length > 0 && (
              <span
                data-testid="room-people-here"
                className="flex items-center -space-x-1.5"
                title={`${here.map((p) => p.name ?? p.user_id).join(", ")} here`}
              >
                {here.slice(0, 3).map((p) => (
                  <PresenceFace key={p.user_id} userId={p.user_id} idle={p.idle} />
                ))}
              </span>
            )}
            {!node && (
              <label
                className="inline-flex cursor-pointer items-center gap-1.5 text-sm text-muted-foreground"
                title="Interleave the agents' messages (message.* events) as one-liners"
              >
                <NetworkIcon aria-hidden className="size-3.5" />
                <span className={cn(dock && "sr-only")}>Bus view</span>
                <Switch
                  size="sm"
                  checked={showBus}
                  onCheckedChange={(on) => {
                    setShowBus(on);
                    saveBus(on);
                  }}
                  data-testid="room-bus-toggle"
                  aria-label="Bus view"
                />
              </label>
            )}
            {dock && !node && (
              <Link
                to={roomPageHref(fid, thread)}
                componentId="factory.room.open_page"
                data-testid="room-open-page"
                aria-label="Open the Room page"
                title="Open the Room page"
                className="inline-flex size-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <ExternalLinkIcon className="size-3.5" />
              </Link>
            )}
          </span>
        </header>

        {node?.lead}
        {node?.activity?.(activity, (item) => renderItem(item))}
        <div className="relative flex min-h-0 flex-1 flex-col">
          <div
            ref={scrollerRef}
            onScroll={onScroll}
            data-testid="room-messages"
            className={cn(
              "min-h-0 flex-1 overflow-y-auto overscroll-contain",
              dock ? "pr-1" : "px-3",
            )}
          >
            {messagesQuery.error instanceof Error && !messagesQuery.data && (
              <div className="p-3">
                <FactoryLoadError
                  error={messagesQuery.error}
                  updatedAt={0}
                  onRetry={() => void messagesQuery.refetch()}
                  what={`${title}`}
                  testId="room-error"
                  className="mb-0"
                />
              </div>
            )}
            {node?.top}
            {messagesQuery.isLoading && <SkeletonRows />}
            {messagesQuery.hasNextPage && (
              <div className="flex justify-center py-2">
                <Button
                  size="xs"
                  variant="ghost"
                  className="text-muted-foreground"
                  disabled={messagesQuery.isFetchingNextPage}
                  onClick={() => {
                    const el = scrollerRef.current;
                    if (el) restore.current = el.scrollHeight - el.scrollTop;
                    void messagesQuery.fetchNextPage();
                  }}
                  data-testid="room-load-earlier"
                >
                  {messagesQuery.isFetchingNextPage && <Loader2Icon className="animate-spin" />}
                  Earlier messages
                </Button>
              </div>
            )}
            {messagesQuery.isSuccess && empty && node?.empty}
            {messagesQuery.isSuccess && empty && !node?.empty && (
              <div
                data-testid="room-empty"
                className="flex flex-col items-center gap-1.5 px-4 py-10 text-center text-sm text-muted-foreground"
              >
                <MessagesSquareIcon aria-hidden className="size-5 opacity-60" />
                <p className="font-medium text-foreground">Nothing in {title} yet</p>
                <p className="max-w-72">
                  {status.name ? `${status.name} answers here. ` : ""}@ to mention, # ! D- to link,
                  / for commands.
                </p>
              </div>
            )}
            <ol className="flex min-w-0 flex-col pb-2" data-testid="room-message-list">
              {shown.map((item, i) => renderItem(item, shown[i - 1]))}
              {notes.map((note) => (
                <NoteRow
                  key={note.id}
                  note={note}
                  onDismiss={() => setNotes((prev) => prev.filter((n) => n.id !== note.id))}
                />
              ))}
              {node?.bottom}
            </ol>
          </div>
          {showJump && (
            <Button
              size="icon-xs"
              variant="outline"
              onClick={jumpDown}
              aria-label="Jump to the newest message"
              className="absolute right-3 bottom-2 rounded-full bg-background shadow-sm"
            >
              <ArrowDownIcon />
            </Button>
          )}
        </div>

        <div
          className={cn(
            "flex shrink-0 flex-col gap-1",
            dock ? "pt-2" : "border-t border-border p-2",
          )}
        >
          {statusLine && (
            <p
              data-testid="room-status-line"
              data-state={status.state}
              className="flex min-w-0 items-center gap-1.5 px-1 text-xs text-muted-foreground"
            >
              {status.state === "working" && (
                <Loader2Icon
                  aria-hidden
                  className="size-3 shrink-0 animate-spin motion-reduce:animate-none"
                />
              )}
              <span className="truncate">{statusLine}</span>
            </p>
          )}
          <RoomComposer
            snapshot={snapshot}
            thread={thread}
            threadTitle={title}
            candidates={candidates}
            people={people.data?.data}
            onSend={send}
            onCommand={async (parse) => {
              const note = await runCommand(parse.command);
              if (note?.ok) addNote(note);
              return note;
            }}
            onTrigger={setTrigger}
            compact={dock}
            node={node?.composer}
          />
        </div>
      </section>
      {adding && (
        <AddProjectDialog
          factoryId={fid}
          open
          initialTitle={adding.title}
          onOpenChange={(open) => !open && setAdding(null)}
        />
      )}
      {charterProject && (
        <EditCharterDialog
          factoryId={fid}
          project={charterProject}
          open
          onOpenChange={(open) => !open && setCharter(null)}
        />
      )}
    </RoomCardContext.Provider>
  );
}
