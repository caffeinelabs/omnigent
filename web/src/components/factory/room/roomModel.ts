// The Room's pure model: thread keys and titles, the thread list's groups,
// who answers a thread and whether they are working, the bus view's
// one-liners from `message.*` events, and the dock's width. Tests pin it.

import type {
  ChatMessageView,
  EventView,
  FactorySnapshot,
  MemberView,
  PresenceEntry,
  ProjectView,
  PrView,
  ThreadView,
} from "@/lib/factoryApi";
import type { FactoryLocation } from "../shell/shellRoutes";
import { findPr } from "../shell/shellRoutes";

export const THREAD_FACTORY = "factory";
export const THREAD_IMPROVEMENTS = "improvements";

/** `project:db-version` for a slug. */
export function projectThread(slug: string): string {
  return `project:${slug}`;
}

/** `pr:<id>` for a PR row id. */
export function prThread(prId: string): string {
  return `pr:${prId}`;
}

/**
 * The thread key a Room URL segment names: a key as is (`project:x`,
 * `pr:<id>`, `factory`), `#slug` or a bare slug → its project thread, `!n` → its PR.
 */
export function threadFromParam(snapshot: FactorySnapshot, param: string | null): string {
  const raw = (param ?? "").trim();
  if (!raw || raw === THREAD_FACTORY || raw === "#factory") return THREAD_FACTORY;
  if (raw === THREAD_IMPROVEMENTS || raw === "#improvements") return THREAD_IMPROVEMENTS;
  if (raw.startsWith("project:")) return raw;
  if (raw.startsWith("pr:")) {
    const pr = findPr(snapshot.prs, raw.slice(3));
    return prThread(pr?.id ?? raw.slice(3));
  }
  if (raw.startsWith("!")) {
    const pr = findPr(snapshot.prs, raw.slice(1));
    return pr ? prThread(pr.id) : THREAD_FACTORY;
  }
  return projectThread(raw.replace(/^#/, ""));
}

/** `#factory`, `#db-version`, `!9631`: how a thread reads. */
export function threadTitle(snapshot: FactorySnapshot, key: string): string {
  if (key.startsWith("project:")) return `#${key.slice(8)}`;
  if (key.startsWith("pr:")) {
    const pr = findPr(snapshot.prs, key.slice(3));
    return pr ? `!${pr.number}` : "!PR";
  }
  return `#${key}`;
}

/** The project a thread is about (its own, or its PR's). */
export function threadProject(snapshot: FactorySnapshot, key: string): ProjectView | null {
  if (key.startsWith("project:")) {
    return snapshot.projects.find((p) => p.slug === key.slice(8)) ?? null;
  }
  if (key.startsWith("pr:")) {
    const pr = findPr(snapshot.prs, key.slice(3));
    return snapshot.projects.find((p) => p.slug === pr?.project_slug) ?? null;
  }
  return null;
}

/** The PR a `pr:` thread is about. */
export function threadPr(snapshot: FactorySnapshot, key: string): PrView | null {
  return key.startsWith("pr:") ? findPr(snapshot.prs, key.slice(3)) : null;
}

/** The thread a page binds the dock to; other pages keep the last one used. */
export function pageThread(
  snapshot: FactorySnapshot,
  location: FactoryLocation,
  lastUsed: string | null,
): string {
  if (location.page === "project" && location.id) return projectThread(location.id);
  if (location.page === "pr" && location.id) {
    const pr = findPr(snapshot.prs, location.id);
    return prThread(pr?.id ?? location.id);
  }
  return lastUsed ?? THREAD_FACTORY;
}

/** The project a page is about: its decisions lead the dock's Decisions tab. */
export function pageProjectSlug(
  snapshot: FactorySnapshot,
  location: FactoryLocation,
): string | null {
  if (location.page === "project") return location.id ?? null;
  if (location.page === "pr" && location.id) {
    return findPr(snapshot.prs, location.id)?.project_slug ?? null;
  }
  return null;
}

export interface ThreadGroups {
  fixed: ThreadView[];
  projects: ThreadView[];
  prs: ThreadView[];
}

/**
 * The thread list's groups: `#factory` and `#improvements`, then projects
 * (active first, in the snapshot's order), then PR threads (newest first).
 * Closed projects' threads stay listed but sink.
 */
export function groupThreads(
  threads: readonly ThreadView[],
  snapshot: FactorySnapshot,
): ThreadGroups {
  const order = new Map(snapshot.projects.map((p, i) => [p.slug, i]));
  const closed = (slug: string | null) => {
    const status = snapshot.projects.find((p) => p.slug === slug)?.status;
    return status != null && status !== "active" ? 1 : 0;
  };
  const projects = threads
    .filter((t) => t.kind === "project")
    .sort(
      (a, b) =>
        closed(a.project_slug) - closed(b.project_slug) ||
        (order.get(a.project_slug ?? "") ?? 99) - (order.get(b.project_slug ?? "") ?? 99),
    );
  const prs = threads
    .filter((t) => t.kind === "pr")
    .sort((a, b) => (b.last_at ?? 0) - (a.last_at ?? 0));
  const fixed = threads
    .filter((t) => t.kind === "factory" || t.kind === "improvements")
    .sort((a, b) => (a.kind === "factory" ? -1 : b.kind === "factory" ? 1 : 0));
  return { fixed, projects, prs };
}

/** Every unread message across threads (the dock button's badge), capped like the server. */
export function unreadTotal(threads: readonly ThreadView[] | null | undefined): number {
  return Math.min(
    99,
    (threads ?? []).reduce((sum, t) => sum + t.unread, 0),
  );
}

export type ResponderState = "working" | "idle" | "paused" | "down" | "none";

export interface ResponderStatus {
  /** e.g. `Atlas`, `PL · db-version`. */
  name: string | null;
  state: ResponderState;
  /** Messages for it not delivered yet (they wait while it works). */
  queued: number;
  member: MemberView | null;
}

/**
 * Who answers the thread and what they are doing: working (running a turn),
 * idle, paused or down, plus how many messages wait for them.
 */
export function responderStatus(
  snapshot: FactorySnapshot,
  thread: ThreadView | null,
  messages: readonly ChatMessageView[],
): ResponderStatus {
  const id = thread?.responder_session_id ?? null;
  const member = id ? (snapshot.members.find((m) => m.session_id === id) ?? null) : null;
  const queued = id
    ? messages.filter(
        (m) =>
          m.author_kind === "human" &&
          m.routed.some((r) => r.session_id === id && r.delivered_at == null),
      ).length
    : 0;
  const name = thread?.responder_title ?? member?.title ?? null;
  if (!id || !member) return { name, state: id ? "down" : "none", queued, member };
  if (member.state === "paused") return { name, state: "paused", queued, member };
  if (member.health === "dead" || member.status === "failed") {
    return { name, state: "down", queued, member };
  }
  return { name, state: member.running ? "working" : "idle", queued, member };
}

/** `"Atlas is working… · 2 queued"`, `"PL · db-version is paused"`; null when idle with nothing queued. */
export function responderLine(status: ResponderStatus): string | null {
  const who = status.name ?? "The responder";
  const queued = status.queued > 0 ? ` · ${status.queued} queued` : "";
  switch (status.state) {
    case "working":
      return `${who} is working…${queued}`;
    case "paused":
      return `${who} is paused; Atlas answers meanwhile${queued}`;
    case "down":
      return `${who} is down; Atlas answers meanwhile${queued}`;
    case "none":
      return null;
    default:
      return status.queued > 0 ? `${status.queued} queued for ${who}` : null;
  }
}

/** People whose presence is on this thread (Room page or a page bound to it). */
export function peopleHere(
  presence: readonly PresenceEntry[] | null | undefined,
  snapshot: FactorySnapshot,
  thread: string,
): PresenceEntry[] {
  return (presence ?? []).filter((entry) => {
    const loc = entry.location ?? "";
    if (loc === `room:${thread}`) return true;
    if (thread === THREAD_FACTORY && (loc === "room" || loc === "overview")) return true;
    if (thread.startsWith("project:") && loc === `project:${thread.slice(8)}`) return true;
    if (thread.startsWith("pr:")) {
      const pr = findPr(snapshot.prs, thread.slice(3));
      return pr != null && (loc === `pr:${pr.id}` || loc === `pr:${pr.number}`);
    }
    return false;
  });
}

// ── Bus view ───────────────────────────────────────────────────

export interface BusLine {
  id: string;
  /** Epoch milliseconds. */
  ts: number;
  from: string;
  to: string;
  /** `request`, `reply`, `info`, `chat`, … */
  kind: string;
  text: string;
  bounced: boolean;
}

const BUS_KINDS = new Set(["message.queued", "message.bounced"]);

/** `"PL · db-version → Sentinel · request · verify !9631"` lines from `message.*` events. */
export function busLines(events: readonly EventView[]): BusLine[] {
  return events
    .filter((e) => BUS_KINDS.has(e.kind))
    .map((e) => {
      const data = e.data ?? {};
      const kind = typeof data.kind === "string" ? data.kind : "info";
      const excerpt = typeof data.excerpt === "string" && data.excerpt ? data.excerpt : e.summary;
      return {
        id: e.id,
        ts: e.ts,
        from: e.from_title ?? (e.from_id ? "agent" : "server"),
        to: e.to_title ?? (typeof data.target_title === "string" ? data.target_title : "agent"),
        kind,
        text: excerpt.replace(/^Undeliverable \([^)]*\): /, ""),
        bounced: e.kind === "message.bounced",
      };
    })
    .sort((a, b) => a.ts - b.ts);
}

export type RoomItem =
  | { type: "message"; key: string; at: number; message: ChatMessageView }
  | { type: "bus"; key: string; at: number; line: BusLine };

/** Messages with the bus lines that fall inside their time span, oldest first. */
export function interleave(
  messages: readonly ChatMessageView[],
  lines: readonly BusLine[],
  showBus: boolean,
): RoomItem[] {
  const items: RoomItem[] = messages.map((message) => ({
    type: "message",
    key: message.id,
    at: message.created_at * 1000,
    message,
  }));
  if (showBus) {
    const first = items[0]?.at ?? 0;
    for (const line of lines) {
      if (line.ts >= first) items.push({ type: "bus", key: `bus:${line.id}`, at: line.ts, line });
    }
  }
  return items.sort((a, b) => a.at - b.at);
}

/** Which `message.*` events a thread's bus view reads (`project` filter, or the leads'). */
export function busFilterFor(snapshot: FactorySnapshot, thread: string): { project?: string } {
  const project = threadProject(snapshot, thread);
  return project ? { project: project.slug } : {};
}

// ── Messages ───────────────────────────────────────────────────

/** The highest seq on screen (what reading the thread marks). */
export function lastSeq(messages: readonly ChatMessageView[]): number {
  return messages.reduce((max, m) => Math.max(max, m.seq), 0);
}

/** How a message's author reads: a person's name, an agent's title, or "system". */
export function authorName(message: ChatMessageView): string {
  if (message.author_kind === "system") return "system";
  if (message.author_title) return message.author_title;
  if (message.author_kind === "human") return message.author.split("@")[0] ?? message.author;
  return "agent";
}

/** Runs of one author within 5 minutes read as one block (only the first shows the name). */
export function continuesRun(prev: ChatMessageView | undefined, next: ChatMessageView): boolean {
  return (
    prev != null &&
    prev.author === next.author &&
    prev.author_kind === next.author_kind &&
    next.author_kind !== "system" &&
    next.card == null &&
    prev.card == null &&
    next.created_at - prev.created_at < 300
  );
}

/** `14:03`: a message's time, 24-hour in every locale. */
export function messageTime(epochS: number): string {
  return new Date(epochS * 1000).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

// ── The dock's width ───────────────────────────────────────────

export const DOCK_MIN_PX = 304;
export const DOCK_MAX_PX = 560;
/** 20 rem, as before the dock resized: the Overview's tables fit beside it at 1280 px. */
export const DOCK_DEFAULT_PX = 320;
export const DOCK_WIDTH_KEY = "omnigent.factory.roomDock.width";
export const DOCK_TAB_KEY = "omnigent.factory.roomDock.tab";
export const LAST_THREAD_KEY = "omnigent.factory.room.lastThread";

/** A dock width within its bounds (a non-number falls back to the default). */
export function clampDockWidth(px: number): number {
  if (!Number.isFinite(px)) return DOCK_DEFAULT_PX;
  return Math.round(Math.min(DOCK_MAX_PX, Math.max(DOCK_MIN_PX, px)));
}

/** The dock width stored in this browser, else the default. */
export function loadDockWidth(): number {
  try {
    const raw = window.localStorage.getItem(DOCK_WIDTH_KEY);
    return raw == null ? DOCK_DEFAULT_PX : clampDockWidth(Number(raw));
  } catch {
    return DOCK_DEFAULT_PX;
  }
}

export function saveDockWidth(px: number): void {
  try {
    window.localStorage.setItem(DOCK_WIDTH_KEY, String(clampDockWidth(px)));
  } catch {
    // Private mode: the width lasts this page only.
  }
}

/** The thread last used in this factory (the dock's default off project and PR pages). */
export function loadLastThread(factoryId: string): string | null {
  try {
    return window.localStorage.getItem(`${LAST_THREAD_KEY}.${factoryId}`);
  } catch {
    return null;
  }
}

export function saveLastThread(factoryId: string, thread: string): void {
  try {
    window.localStorage.setItem(`${LAST_THREAD_KEY}.${factoryId}`, thread);
  } catch {
    // Private mode: the dock follows #factory.
  }
}
