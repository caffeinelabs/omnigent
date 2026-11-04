// The node's chat, a main column of every node page (never a drawer), calm:
// the conversation only (people, agents, and live cards that need this
// viewer); system events fold into one "Activity" line that opens them as a
// list, and an ask the page's Your move shows is never repeated here. The
// `/` menu holds the node's verbs, and plain words open the same previews.
// From 1024 px it sits beside the page (drag its edge); narrower, its box is
// pinned at the bottom and opens the thread full screen.

import {
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { usePreflight } from "@/hooks/useFactory";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import type { ChatMessageView } from "@/lib/factoryApi";
import { useLocation, useNavigate, useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { useMediaQuery } from "../../board/boardHooks";
import {
  type NodeVerbCommand,
  matchingNodeVerbs,
  nodeVerbCommands,
} from "../../cmdk/slashCommands";
import { CHORD_MS, keyOwnedByTarget } from "../../cmdk/useFactoryPaletteHotkey";
import { DockResizeHandle } from "../../room/RoomDock";
import { ThreadNewsLines } from "../../room/RoomFolds";
import { threadTitle } from "../../room/roomModel";
import type { FoldedItem } from "../../room/roomFold";
import {
  flattenPages,
  useProposals,
  useRoomMessages,
  useRoomThreads,
} from "../../room/roomQueries";
import { RoomThreadView } from "../../room/RoomThreadView";
import { useStickyTop } from "../../room/useViewportFill";
import { useFactoryShell } from "../../shell/FactoryShellContext";
import { PHONE_TAB_BAR_REM } from "../../shell/PhoneTabBar";
import { PAGE_EXTRA_TOP } from "../../shell/scroll";
import { swallowEnter } from "../DecisionButtons";
import { useNodeActions } from "../NodeContext";
import { nodeName } from "../calm/calmModel";
import { Avatar } from "../nodeParts";
import { useNode } from "../nodeQueries";
import { undoViewer, useUndoToken } from "../nodeSteer";
import { nodeHref } from "../nodeRoutes";
import { AMBER, STATE_WORDS } from "../nodeTokens";
import type { DecisionRef, NodePage, StatusLine, VerbArgs } from "../nodeTypes";
import { levelName } from "../nodeWire";
import { statusText } from "../statusModel";
import { WhyCard } from "../WhyCard";
import { ActivityLine } from "./ActivityLine";
import { AppliedCard, AskCard, ClosedAlarmCard, OfferCard } from "./ChatCards";
import { type LaneContext, itemLane, repeatsAsk, undoneTokens } from "./chatLanes";
import { CHAT_BESIDE_QUERY, CHAT_PARAM, useChatWidth, useNodeChatSlot } from "./chatLayout";
import { parseIntent } from "./chatIntents";
import {
  CHILD_NEWS_SHOWN,
  FACTORY_THREAD,
  childNews,
  factoryLeadTitle,
  namedBody,
  nodeThread,
  threadOwnerRef,
} from "./nodeThread";

/** The node chat reads its thread with each row's lane (the server's conversation / activity split). */
const NODE_FEED = "all" as const;

/** The Room's cards that answer an ask: a key never answers them (Enter, or 1-5 for an option). */
const ANSWER_CARDS =
  '[data-testid^="decision-card-"],[data-testid^="proposal-card-"],[data-testid^="loop-card-"]';

function swallowAnswerKeys(event: ReactKeyboardEvent<HTMLElement>) {
  const target = event.target as HTMLElement;
  if (!target.closest(ANSWER_CARDS)) return;
  const typing = target.closest("textarea,input,select,[contenteditable=true]");
  if (!typing && /^[1-5]$/.test(event.key) && !event.metaKey && !event.ctrlKey && !event.altKey) {
    event.preventDefault();
    event.stopPropagation();
    return;
  }
  if (target.tagName === "BUTTON") swallowEnter(event);
}

/** A card only this viewer sees, after the thread's messages. */
type LocalBody =
  { kind: "why" } | { kind: "ask"; decision: DecisionRef } | { kind: "note"; text: string };
type LocalCard = LocalBody & { id: number };

/** "Moving now: …" when there is no numbered why to show. */
function StatusCard({ title, line }: { title: string; line: StatusLine }) {
  return (
    <section
      data-testid="v3-chat-status"
      className="rounded-[10px] border border-border bg-muted/30 px-3.5 py-2.5 text-[13px] text-muted-foreground"
    >
      <b className="font-semibold text-foreground">{title}:</b> {statusText(line)}
    </section>
  );
}

/** The why card for a node: the chat's own node, or another one a message names. */
function WhyOf({
  nodeRef,
  chat,
  here,
  fallback = null,
}: {
  nodeRef: string;
  chat: NodePage;
  here: NodePage;
  /** What shows while the node loads, or when no node has that ref (the message's own line). */
  fallback?: ReactNode;
}) {
  const other = useNode(nodeRef === chat.node.ref ? "" : nodeRef);
  const page = nodeRef === chat.node.ref ? chat : other.data;
  if (!page) return fallback;
  if (!page.status.why?.length) return <StatusCard title={page.node.title} line={page.status} />;
  const start =
    page.node.ref === here.node.ref ? (page.verbs.find((v) => v.verb === "start") ?? null) : null;
  return <WhyCard line={page.status} startNow={start} inChat />;
}

/** An offer on a node: it opens the verb's preview, and says "started" once the node moves. */
function OfferOf({
  message,
  attached,
  nodeRef,
  chat,
  onVerb,
  fallback,
}: {
  message: ChatMessageView;
  attached: boolean;
  nodeRef: string;
  chat: NodePage;
  onVerb: (verb: string, at: string) => void;
  fallback: ReactNode;
}) {
  const other = useNode(nodeRef === chat.node.ref ? "" : nodeRef);
  const page = nodeRef === chat.node.ref ? chat : other.data;
  if (!page) return fallback;
  const meta = message.card?.meta?.kind === "offer" ? message.card.meta : null;
  const verbKey = meta?.verb ?? "start";
  const verb = page.verbs.find((v) => v.verb === verbKey && v.enabled) ?? null;
  const state = page.node.state;
  const started =
    state === "moving" || state === "done"
      ? (page.status.moving?.activity ?? `${page.node.title} is ${STATE_WORDS[state]}.`)
      : null;
  return (
    <OfferCard
      who={message.author_title ?? "The factory"}
      title={meta?.title ?? verb?.label ?? "An offer"}
      text={attached ? null : message.body}
      action={verb ? `Preview ${verb.label.toLowerCase()}` : null}
      started={started}
      onPreview={() => onVerb(verbKey, nodeRef)}
    />
  );
}

/** What a verb's card needs to offer its Undo: the tokens already undone, who looks, and how. */
interface AppliedUndo {
  undone: ReadonlySet<string>;
  /** The server lets the person who applied it, or an owner, undo it. */
  viewer: { userId: string | null | undefined; owner: boolean };
  clockOffsetS: number;
  run: ((token: string, label: string) => Promise<void>) | null;
}

/** "Now" on the server's clock, read every 15 s while `until` (epoch s) is ahead; no tick without it. */
function useNowUntil(clockOffsetS: number, until: number | null): number {
  const [nowS, setNowS] = useState(() => Math.floor(Date.now() / 1000 + clockOffsetS));
  useEffect(() => {
    if (until == null) return;
    const read = () => Math.floor(Date.now() / 1000 + clockOffsetS);
    const first = read();
    setNowS(first);
    if (first >= until) return;
    const timer = setInterval(() => {
      const at = read();
      setNowS(at);
      if (at >= until) clearInterval(timer);
    }, 15_000);
    return () => clearInterval(timer);
  }, [clockOffsetS, until]);
  return nowS;
}

/** A verb's card: what it did, and its Undo while the token is open, unused and this viewer's. */
function AppliedOf({
  message,
  applied,
  fallback,
}: {
  message: ChatMessageView;
  applied: AppliedUndo;
  fallback: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const meta = message.card?.meta?.kind === "preview_applied" ? message.card.meta : null;
  const token = meta && !meta.undone ? (meta.undo_token ?? null) : null;
  const { viewer } = applied;
  const theirs = viewer.owner || Boolean(viewer.userId && meta?.by_user === viewer.userId);
  const open = Boolean(token && theirs && !applied.undone.has(token) && applied.run);
  const until = open ? (meta?.undo_until ?? null) : null;
  // Only an open Undo ticks, so the rest of the thread never re-renders for the clock.
  const nowS = useNowUntil(applied.clockOffsetS, until);
  if (!meta) return fallback;
  const left = until != null ? until - nowS : null;
  return (
    <AppliedCard
      text={message.body}
      leftS={left}
      busy={busy}
      onUndo={
        token && until != null
          ? () => {
              setBusy(true);
              void applied.run!(token, meta.label ?? meta.verb).finally(() => setBusy(false));
            }
          : undefined
      }
    />
  );
}

/**
 * The cards the Room leaves to the node chat: why, closed alarm, offer, a
 * verb's card. One it cannot draw (its node loading or gone, an alarm it knows
 * nothing of) shows `fallback`, the message's own line, so it never renders empty.
 */
function NodeMessageCard({
  message,
  attached,
  chat,
  here,
  onVerb,
  applied,
  fallback,
}: {
  message: ChatMessageView;
  attached: boolean;
  chat: NodePage;
  here: NodePage;
  onVerb: (verb: string, at: string) => void;
  applied: AppliedUndo;
  fallback: ReactNode;
}) {
  const { snapshot } = useFactoryShell();
  const card = message.card;
  if (!card) return fallback;
  const nodeRef = card.ref ?? chat.node.ref;
  switch (card.type) {
    case "preview_applied":
      return <AppliedOf message={message} applied={applied} fallback={fallback} />;
    case "why":
      return <WhyOf nodeRef={nodeRef} chat={chat} here={here} fallback={fallback} />;
    case "offer":
      return (
        <OfferOf
          message={message}
          attached={attached}
          nodeRef={nodeRef}
          chat={chat}
          onVerb={onVerb}
          fallback={fallback}
        />
      );
    case "closed_alarm": {
      const meta = card.meta?.kind === "closed_alarm" ? card.meta : null;
      const asked = snapshot.decisions.find((d) => d.ref === card.ref || d.id === card.id);
      const id = card.ref ?? asked?.ref;
      const was = meta?.was ?? asked?.question;
      if (!id || !was) return fallback;
      return (
        <ClosedAlarmCard
          alarm={{
            id,
            was,
            cause: meta?.cause ?? asked?.answer_text ?? message.body,
            realAsk: meta?.real_ask ?? null,
          }}
        />
      );
    }
    default:
      return fallback;
  }
}

interface QuickVerb {
  id: string;
  label: string;
  /** The status line's answer (first on a phone): amber. */
  answer?: boolean;
  run: () => void;
}

function QuickVerbs({ verbs }: { verbs: QuickVerb[] }) {
  if (!verbs.length) return null;
  return (
    <div
      role="toolbar"
      aria-label="Quick verbs"
      data-testid="v3-chat-quick"
      className="flex flex-wrap gap-1.5 pb-1.5"
    >
      {verbs.map((v) => (
        <button
          key={v.id}
          type="button"
          data-testid={v.answer ? "v3-chat-quick-answer" : `v3-chat-quick-${v.id}`}
          onKeyDown={v.answer ? swallowEnter : undefined}
          onClick={v.run}
          className={cn(
            "rounded-full border px-2.5 py-0.5 text-[12.5px] whitespace-nowrap",
            v.answer
              ? cn("font-semibold", AMBER.button)
              : "border-border bg-card-solid text-muted-foreground hover:border-border-strong hover:text-foreground",
          )}
        >
          {v.label}
        </button>
      ))}
    </div>
  );
}

/**
 * What was new in the thread when the page opened, so the Activity line can
 * say "n since you looked": the server's count of activity after this
 * viewer's read mark when it sends one, else the newest seq they had read
 * (the thread's unread count says how many of the newest were not). Looking
 * at the list makes nothing new. Null until the thread is read.
 */
function useSince(fid: string, key: string, unread: number | undefined) {
  const messages = useRoomMessages(fid, key, NODE_FEED);
  const [mark, setMark] = useState<{
    key: string;
    seq: number | null;
    fresh: number | null;
  } | null>(null);
  if ((!mark || mark.key !== key) && messages.data) {
    const told = messages.data.pages[0]?.activity_since;
    if (typeof told === "number") setMark({ key, seq: null, fresh: told });
    else if (unread != null) {
      const sorted = [...flattenPages(messages.data)].sort((a, b) => a.seq - b.seq);
      const read = sorted[sorted.length - unread - 1];
      setMark({ key, seq: read ? read.seq : 0, fresh: null });
    }
  }
  const seen = useCallback((seq: number) => setMark({ key, seq, fresh: null }), [key]);
  const here = mark?.key === key ? mark : null;
  return { seq: here?.seq ?? null, fresh: here?.fresh ?? null, seen };
}

export interface NodeChatProps {
  /** The node page the chat sits beside. */
  page: NodePage;
  /** The page shows sample data: the chat is this factory's own root thread. */
  sample?: boolean;
}

let localSeq = 0;

export function NodeChat({ page: here, sample = false }: NodeChatProps) {
  const { factoryId: fid, snapshot, clockOffsetS } = useFactoryShell();
  const actions = useNodeActions();
  const slot = useNodeChatSlot();
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const beside = useMediaQuery(CHAT_BESIDE_QUERY, true);
  const phone = useIsMobileViewport();
  const full = !beside && params.get(CHAT_PARAM) === "1";
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  // A run shows its task's thread: the chat reads that node's page.
  const ownerRef = threadOwnerRef(here);
  const owner = useNode(ownerRef === here.node.ref ? "" : ownerRef, fid);
  const chat = ownerRef === here.node.ref ? here : (owner.data ?? here);
  const perNode = usePreflight().data?.node_threads === true;
  // With the per-node threads (`?nodes=1`): a child's own thread can have news.
  const threads = useRoomThreads(fid, { nodes: perNode }).data?.data ?? null;
  const thread = nodeThread(chat, { snapshot, threads, perNode, sample });
  const room = threads?.find((t) => t.key === thread.key) ?? null;
  // Who answers the thread the chat actually posts to: the node's own
  // responder only when that thread is the node's own (else a borrowed
  // thread's, e.g. #factory's Atlas for a sample node or a need without #discovery).
  const own = perNode || thread.key === chat.thread.key;
  const responder =
    (own ? chat.thread.responder : null) ??
    room?.responder_title ??
    (thread.key === FACTORY_THREAD ? factoryLeadTitle(snapshot) : null);
  const roomTitle = threadTitle(snapshot, thread.key);
  const node = chat.node;
  const relay =
    thread.about && thread.key.startsWith("project:") && node.owner && node.owner.name !== responder
      ? node.owner.name
      : null;

  // ── Opening and focusing ─────────────────────────────────────
  const openFull = useCallback(() => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set(CHAT_PARAM, "1");
        return next;
      },
      { state: { v3chat: true } },
    );
  }, [setParams]);
  const closeFull = useCallback(() => {
    if ((location.state as { v3chat?: boolean } | null)?.v3chat) {
      navigate(-1);
      return;
    }
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete(CHAT_PARAM);
        return next;
      },
      { replace: true },
    );
  }, [location.state, navigate, setParams]);
  const focusBox = useCallback(() => {
    if (!beside && !full) {
      openFull();
      return;
    }
    inputRef.current?.focus();
  }, [beside, full, openFull]);
  const latestFocus = useRef(focusBox);
  latestFocus.current = focusBox;
  useEffect(() => slot?.register(() => latestFocus.current()), [slot]);
  // `?chat=1` beside the page: the box takes focus, and the address forgets it.
  const asked = beside && params.get(CHAT_PARAM) === "1";
  useEffect(() => {
    if (!asked) return;
    inputRef.current?.focus();
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete(CHAT_PARAM);
        return next;
      },
      { replace: true },
    );
  }, [asked, setParams]);
  // `c` (and `/` outside the shell, which routes `/` itself) focuses the box; Esc closes the full screen.
  const latestClose = useRef(closeFull);
  latestClose.current = closeFull;
  useEffect(() => {
    let chordAt = 0;
    const handler = (event: KeyboardEvent) => {
      if (event.repeat || event.defaultPrevented) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (full && event.key === "Escape") {
        event.preventDefault();
        latestClose.current();
        return;
      }
      if (keyOwnedByTarget(event.target)) return;
      if (event.key === "g") {
        chordAt = Date.now();
        return;
      }
      if (Date.now() - chordAt < CHORD_MS) return;
      if (event.key === "c" || (event.key === "/" && !slot)) {
        event.preventDefault();
        latestFocus.current();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [full, slot]);

  // ── Cards only this viewer sees ─────────────────────────────
  const [cards, setCards] = useState<LocalCard[]>([]);
  const addCard = useCallback((card: LocalBody) => {
    localSeq += 1;
    const id = localSeq;
    setCards((prev) => [...prev, { ...card, id }]);
  }, []);

  /** Open a verb's preview on this page, or on the node it names (`?verb=` there). */
  const runVerb = useCallback(
    (verb: string, at: string, args?: VerbArgs) => {
      if (verb === "talk") {
        latestFocus.current();
        return;
      }
      const v = at === here.node.ref ? here.verbs.find((x) => x.verb === verb && x.enabled) : null;
      if (v) {
        // The preview opens on the page: a full-screen chat (a phone) steps
        // back to it first, or the preview would open out of sight.
        if (full) latestClose.current();
        // Always with an object: the preview then leaves focus in this box.
        actions.onVerb?.(v, args ?? {});
        return;
      }
      const search = new URLSearchParams({ verb });
      for (const [k, value] of Object.entries(args ?? {})) if (value) search.set(k, value);
      navigate(`${nodeHref(fid, at)}?${search.toString()}`);
    },
    [actions, fid, full, here.node.ref, here.verbs, navigate],
  );

  const decision = chat.status.ends === "person" ? (chat.status.decision ?? null) : null;
  const hasWhy = Boolean(chat.status.why?.length);
  const menu = useMemo(
    () => nodeVerbCommands({ verbs: chat.verbs, why: hasWhy, decision }),
    [chat.verbs, hasWhy, decision],
  );
  const verbsFor = useCallback((query: string) => matchingNodeVerbs(query, menu), [menu]);
  const onMenuVerb = useCallback(
    (picked: NodeVerbCommand) => {
      if (picked.id === "why") addCard({ kind: "why" });
      else if (picked.id.startsWith("answer:") && decision) addCard({ kind: "ask", decision });
      else if (picked.id.startsWith("verb:")) runVerb(picked.id.slice(5), node.ref);
    },
    [addCard, decision, node.ref, runVerb],
  );

  /**
   * Plain words the chat reads too. They are posted first (a bare "why" stays
   * here); short command-shaped words then open the why card, the ask's card
   * or a verb's preview beside them.
   */
  const interpret = useCallback(
    (body: string) => {
      const intent = parseIntent(body, chat);
      if (!intent) return null;
      const run = () => {
        if (intent.kind === "why") addCard({ kind: "why" });
        else if (intent.kind === "answer") addCard({ kind: "ask", decision: intent.decision });
        else {
          const target =
            intent.at === node.ref
              ? node.title
              : ([...chat.children.items, ...chat.children.later].find((k) => k.ref === intent.at)
                  ?.title ?? intent.at);
          const label = chat.verbs.find((v) => v.verb === intent.verb)?.label ?? intent.verb;
          addCard({
            kind: "note",
            text:
              intent.verb === "review"
                ? `“${body}”: an independent done review of ${target} starts now; it only reads.`
                : `“${body}”: ${label} on ${target} opens a preview first; nothing changes until you confirm it.`,
          });
          runVerb(intent.verb, intent.at, intent.args);
        }
      };
      return { local: intent.kind === "why" && intent.bare, run };
    },
    [addCard, chat, node.ref, node.title, runVerb],
  );

  const quick: QuickVerb[] = [];
  if (phone && decision && !decision.external_url && !decision.blocks_nothing) {
    const first = decision.actions[0];
    if (first) {
      quick.push({
        id: "answer",
        label: decision.verb,
        answer: true,
        run: () => actions.onAnswer?.(decision, first),
      });
    }
  }
  if (hasWhy) quick.push({ id: "why", label: "Why?", run: () => addCard({ kind: "why" }) });
  for (const v of chat.verbs.filter((x) => x.enabled && x.verb !== "talk").slice(0, 4)) {
    quick.push({ id: v.verb, label: v.label, run: () => runVerb(v.verb, node.ref) });
  }

  const news = childNews(chat, threads, thread.key, snapshot, sample);
  const level = levelName(here.node.level, here.node.level_name).toLowerCase();
  // Who the thread is with; where it lives and what the agent sees are the title's tooltip.
  const subtitle = responder ? `with ${responder}${relay ? ` (relays to ${relay})` : ""}` : null;
  const about = [
    sample ? `this factory’s ${roomTitle}: the sample’s nodes have no threads` : roomTitle,
    perNode && responder
      ? `${responder} sees this node’s breadcrumb, status, roll-up and open asks`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const title = (
    <div className="flex min-w-0 flex-1 flex-col" data-testid="v3-chat-title" title={about}>
      <h2 className="flex min-w-0 items-center gap-2 text-[14px] font-semibold">
        {responder && <Avatar actor={{ name: responder }} size={20} />}
        <span className="truncate">Chat · {nodeName(node)}</span>
      </h2>
      {subtitle && <p className="truncate text-xs text-foreground-tertiary">{subtitle}</p>}
    </div>
  );

  // ── Conversation and Activity ───────────────────────────────
  const above = actions.aboveId;
  const proposals = useProposals(fid).data?.data ?? null;
  const threadMessages = useRoomMessages(fid, thread.key, NODE_FEED).data;
  const laneCtx = useMemo<LaneContext>(
    () => ({
      asked: above instanceof Set ? above : new Set(typeof above === "string" ? [above] : []),
      decisions: snapshot.decisions,
      proposals,
      nowS: Math.floor(Date.now() / 1000 + clockOffsetS),
      undone: undoneTokens(flattenPages(threadMessages)),
    }),
    [above, clockOffsetS, proposals, snapshot.decisions, threadMessages],
  );
  const lane = useCallback((item: FoldedItem) => itemLane(item, laneCtx), [laneCtx]);
  const hideCard = useCallback(
    (message: ChatMessageView) => repeatsAsk(message, laneCtx),
    [laneCtx],
  );
  const since = useSince(fid, thread.key, room?.unread);
  const activity = useCallback(
    (items: FoldedItem[], render: (item: FoldedItem) => ReactNode) => (
      <ActivityLine
        items={items}
        render={render}
        since={since}
        onSeen={since.seen}
        title={nodeName(node)}
      />
    ),
    [node, since],
  );

  const lead =
    chat !== here ? (
      <p
        data-testid="v3-chat-via"
        className="mb-2 shrink-0 rounded-lg bg-muted/40 px-2.5 py-1.5 text-xs text-muted-foreground"
      >
        This {level} has no thread of its own: this is {nodeName(node)}’s thread.
      </p>
    ) : null;

  const top: ReactNode = (
    <ThreadNewsLines
      shown={CHILD_NEWS_SHOWN}
      lines={news.map((n) => ({
        key: n.key,
        title: n.title,
        unread: n.unread,
        text: n.text,
        href: nodeHref(fid, n.ref, n.level),
      }))}
    />
  );

  const bottom = cards.map((c) => (
    <li key={c.id} data-testid="v3-chat-local" data-kind={c.kind} className="my-1.5 flex flex-col">
      <span className="px-1 pb-0.5 text-[11px] text-foreground-tertiary">only you</span>
      {c.kind === "why" && <WhyOf nodeRef={node.ref} chat={chat} here={here} />}
      {c.kind === "ask" && <AskCard decision={c.decision} />}
      {c.kind === "note" && (
        <p className="rounded-lg border border-dashed border-border px-3 py-2 text-[13px] text-muted-foreground">
          {c.text}
        </p>
      )}
    </li>
  ));

  // A verb's card offers its Undo until it ran: the server's "undone" card names its token.
  const messages = useRoomMessages(fid, thread.key, NODE_FEED);
  const undoToken = useUndoToken(fid);
  const viewerId = snapshot.viewer?.user_id;
  const viewerOwner = undoViewer({ snapshot }).owner;
  const applied = useMemo<AppliedUndo>(() => {
    const undone = new Set<string>();
    for (const m of flattenPages(messages.data)) {
      const meta = m.card?.type === "preview_applied" ? m.card.meta : null;
      if (meta?.kind === "preview_applied" && meta.undone && meta.undo_token) {
        undone.add(meta.undo_token);
      }
    }
    return {
      undone,
      viewer: { userId: viewerId, owner: viewerOwner },
      clockOffsetS,
      run: undoToken,
    };
  }, [clockOffsetS, messages.data, undoToken, viewerId, viewerOwner]);

  const renderCard = useCallback(
    (message: ChatMessageView, attached: boolean, fallback: ReactNode) => (
      <NodeMessageCard
        message={message}
        attached={attached}
        chat={chat}
        here={here}
        onVerb={(verb, at) => runVerb(verb, at)}
        applied={applied}
        fallback={fallback}
      />
    ),
    [applied, chat, here, runVerb],
  );

  const threadView = (autoFocus: boolean, className: string) => (
    <div
      className={cn("flex min-h-0 min-w-0 flex-col", className)}
      onKeyDownCapture={swallowAnswerKeys}
    >
      <RoomThreadView
        snapshot={snapshot}
        clockOffsetS={clockOffsetS}
        thread={thread.key}
        variant="dock"
        visible={beside || full}
        className="min-h-0 flex-1"
        node={{
          title,
          lead,
          top,
          bottom,
          bottomCount: cards.length,
          card: renderCard,
          lane,
          activity,
          hideCard,
          feed: NODE_FEED,
          empty: (
            <p
              data-testid="v3-chat-empty"
              className="px-2 py-6 text-center text-[13px] text-muted-foreground"
            >
              {responder
                ? `Ask ${responder} anything about ${nodeName(node)}: why it is not moving, what to change, or a verb such as “start it now”.`
                : `Nothing in ${roomTitle} yet.`}
            </p>
          ),
          composer: {
            to: responder,
            about: thread.about?.label ?? null,
            placeholder: `Message about ${nodeName(node)}, or / for verbs`,
            prepare: (body) => namedBody(body, thread.about),
            interpret,
            verbs: verbsFor,
            onVerb: onMenuVerb,
            // Beside the page its verbs are the ⋯ menu and `/`; full screen keeps them at hand.
            above: beside ? null : <QuickVerbs verbs={quick} />,
            hint: null,
            inputRef,
            autoFocus,
          },
        }}
      />
    </div>
  );

  if (beside) return <ChatColumn here={here}>{threadView(false, "flex-1")}</ChatColumn>;
  return (
    <>
      <PinnedComposer
        fid={fid}
        threadKey={thread.key}
        responder={responder}
        nodeTitle={nodeName(node)}
        phone={phone}
        onOpen={openFull}
      />
      {full && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Chat · ${node.title}`}
          data-testid="v3-chat-full"
          className="fixed inset-0 z-50 flex flex-col bg-background"
          style={{
            paddingTop: "var(--omnigent-inset-top, 0px)",
            paddingBottom: "var(--omnigent-inset-bottom, 0px)",
          }}
        >
          <div className="flex min-w-0 shrink-0 items-center gap-2 border-b border-border px-4 py-2.5">
            <button
              type="button"
              data-testid="v3-chat-back"
              onClick={closeFull}
              className="shrink-0 text-[15px] text-status-blue-fg"
            >
              ‹ Back
            </button>
            <span className="min-w-0 truncate text-[12.5px] text-muted-foreground">
              {nodeName(here.node)}
            </span>
          </div>
          {threadView(true, "flex-1 px-4 pt-2 pb-3")}
        </div>
      )}
    </>
  );
}

/** "T-16" for a code ref, else the title. */
/** Beside the page: sticky under the strip, as tall as the window, its left edge dragged to resize. */
function ChatColumn({ here, children }: { here: NodePage; children: ReactNode }) {
  const ref = useRef<HTMLElement>(null);
  const top = useStickyTop(ref);
  // Clamped to the window as it is now (60 % at most), also after a resize.
  const [width, setWidth] = useChatWidth();
  return (
    <aside
      ref={ref}
      aria-label={`Chat for ${here.node.title}`}
      data-testid="v3-chat"
      className="sticky flex min-w-0 flex-col self-start border-l border-border pl-4"
      style={{
        width,
        top: `calc(1rem - ${PAGE_EXTRA_TOP})`,
        height:
          top == null
            ? STUCK_HEIGHT
            : `calc(100dvh - var(--omnigent-inset-bottom, 0px) - 1rem - ${top}px)`,
        maxHeight: STUCK_HEIGHT,
      }}
    >
      <DockResizeHandle width={width} onWidth={setWidth} label="Resize the chat" />
      {children}
    </aside>
  );
}

const STUCK_HEIGHT =
  "calc(100dvh - var(--omnigent-header-height, 0px) - var(--omnigent-inset-top, 0px) - var(--omnigent-inset-bottom, 0px) - 2rem)";

/** Narrow windows: the conversation's newest line over a box that opens the thread (the node's ask is Your move). */
function PinnedComposer({
  fid,
  threadKey,
  responder,
  nodeTitle,
  phone,
  onOpen,
}: {
  fid: string;
  threadKey: string;
  responder: string | null;
  nodeTitle: string;
  phone: boolean;
  onOpen: () => void;
}) {
  const messages = useRoomMessages(fid, threadKey, NODE_FEED);
  const last = useMemo(
    () =>
      [...flattenPages(messages.data)]
        .reverse()
        .find((m) => m.author_kind !== "system" && m.lane !== "activity" && m.body.trim()),
    [messages.data],
  );
  return (
    <>
      <div aria-hidden className="h-24" />
      <div
        data-testid="v3-chat-pinned"
        className="fixed inset-x-0 z-20 flex flex-col gap-1.5 border-t border-border bg-background/95 px-4 pt-2 pb-2 backdrop-blur-md"
        style={{
          bottom: phone
            ? `calc(${PHONE_TAB_BAR_REM}rem + var(--omnigent-inset-bottom, 0px))`
            : "var(--omnigent-inset-bottom, 0px)",
        }}
      >
        {last && (
          <button
            type="button"
            onClick={onOpen}
            data-testid="v3-chat-last"
            className="line-clamp-2 text-left text-[13px] text-muted-foreground"
          >
            <b className="font-semibold text-foreground">{last.author_title ?? last.author}:</b>{" "}
            {last.body}
          </button>
        )}
        <button
          type="button"
          onClick={onOpen}
          data-testid="v3-chat-open"
          aria-label={`Message ${responder ?? ""} about ${nodeTitle}`.replace(/\s+/g, " ")}
          className="flex min-w-0 items-center gap-2 rounded-2xl border border-border bg-card-solid py-1.5 pr-1.5 pl-3 text-left text-[14px] text-muted-foreground"
        >
          <span className="min-w-0 flex-1 truncate">
            Message {responder ? `${responder} ` : ""}about {nodeTitle}
          </span>
          <span className="rounded-lg bg-foreground px-3 py-1 text-[13px] font-semibold text-background">
            Send
          </span>
        </button>
      </div>
    </>
  );
}
