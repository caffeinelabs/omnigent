// The Activity list's model: kind filter groups and the server `kinds` query
// they become, and the rows a page of events renders as (message receipts
// folded into their message, runs of heartbeats folded into one ♥ row).

import type { EventView } from "@/lib/factoryApi";

export const ACTIVITY_KIND_GROUPS = [
  { id: "messages", label: "Messages", patterns: ["message.*", "notice.*", "rate_limited"] },
  { id: "dispatch", label: "Dispatch", patterns: ["dispatch", "task.*"] },
  { id: "gates", label: "Gates", patterns: ["gate.*", "pr.*", "stage.*"] },
  { id: "previews", label: "Previews & demos", patterns: ["preview.*", "demo.*", "env.*"] },
  { id: "decisions", label: "Decisions", patterns: ["decision.*"] },
  { id: "alerts", label: "Alerts", patterns: ["alert.*", "needs_human", "needs_human.*"] },
  { id: "controls", label: "Controls", patterns: ["control.*", "factory.*", "project.*"] },
  { id: "heartbeats", label: "Heartbeats", patterns: ["heartbeat.*"] },
] as const;

export type KindGroupId = (typeof ACTIVITY_KIND_GROUPS)[number]["id"];

/** Everything but heartbeats, which stay folded until asked for. */
export const DEFAULT_KIND_GROUPS: ReadonlySet<KindGroupId> = new Set(
  ACTIVITY_KIND_GROUPS.map((g) => g.id).filter((id) => id !== "heartbeats"),
);

function patternMatches(kind: string, pattern: string): boolean {
  return pattern.endsWith(".*") ? kind.startsWith(pattern.slice(0, -1)) : kind === pattern;
}

/** The filter group an event kind belongs to, or `null` for kinds no group names. */
export function kindGroup(kind: string): KindGroupId | null {
  for (const group of ACTIVITY_KIND_GROUPS) {
    if (group.patterns.some((p) => patternMatches(kind, p))) return group.id;
  }
  return null;
}

/**
 * The `kinds` query for a selection. With every non-heartbeat group on the
 * server sends everything (kinds no group names included) and heartbeats are
 * folded or shown on the client; a narrower selection asks for its patterns.
 */
export function kindsQuery(selected: ReadonlySet<KindGroupId>): string[] | undefined {
  const allButHeartbeats = ACTIVITY_KIND_GROUPS.every(
    (g) => g.id === "heartbeats" || selected.has(g.id),
  );
  if (allButHeartbeats) return undefined;
  return ACTIVITY_KIND_GROUPS.filter((g) => selected.has(g.id)).flatMap((g) => [...g.patterns]);
}

export type Delivery = "queued" | "delivered" | "bounced";

export type ActivityRow =
  | { type: "event"; key: string; event: EventView; delivery: Delivery | null }
  | {
      type: "heartbeats";
      key: string;
      events: EventView[];
      /** Beats in the run (a `heartbeat.skipped` event tallies several). */
      beats: number;
      delivered: number;
      skipped: number;
      failed: number;
    };

const isHeartbeat = (event: EventView) => event.kind.startsWith("heartbeat.");

function skippedCount(event: EventView): number {
  const counts = event.data?.counts;
  if (counts && typeof counts === "object") {
    const total = Object.values(counts as Record<string, unknown>).reduce<number>(
      (sum, n) => sum + (typeof n === "number" ? n : 0),
      0,
    );
    if (total > 0) return total;
  }
  return 1;
}

/**
 * Rows for events (newest first). A message's delivered/bounced receipt
 * becomes its row's delivery state when the message itself is loaded; with
 * `foldHeartbeats`, each run of consecutive heartbeat events is one row.
 */
export function buildActivityRows(
  events: EventView[],
  options: { foldHeartbeats: boolean },
): ActivityRow[] {
  const queuedSubjects = new Set(
    events.filter((e) => e.kind === "message.queued" && e.subject).map((e) => e.subject),
  );
  const receipts = new Map<string, Delivery>();
  for (const event of events) {
    if (!event.subject) continue;
    if (event.kind === "message.delivered") receipts.set(event.subject, "delivered");
    else if (event.kind === "message.bounced" && !receipts.has(event.subject)) {
      receipts.set(event.subject, "bounced");
    }
  }
  const rows: ActivityRow[] = [];
  let run: EventView[] = [];
  const flush = () => {
    if (run.length === 0) return;
    rows.push({
      type: "heartbeats",
      key: `hb-${run[0]!.id}`,
      events: run,
      beats: run.reduce(
        (sum, e) => sum + (e.kind === "heartbeat.skipped" ? skippedCount(e) : 1),
        0,
      ),
      delivered: run.filter((e) => e.kind === "heartbeat.delivered").length,
      skipped: run
        .filter((e) => e.kind === "heartbeat.skipped")
        .reduce((sum, e) => sum + skippedCount(e), 0),
      failed: run.filter((e) => e.kind === "heartbeat.failed").length,
    });
    run = [];
  };
  for (const event of events) {
    if (options.foldHeartbeats && isHeartbeat(event)) {
      run.push(event);
      continue;
    }
    flush();
    const receipt =
      (event.kind === "message.delivered" || event.kind === "message.bounced") &&
      event.subject != null &&
      queuedSubjects.has(event.subject);
    if (receipt) continue;
    const delivery: Delivery | null =
      event.kind === "message.queued"
        ? ((event.subject ? receipts.get(event.subject) : undefined) ?? "queued")
        : event.kind === "message.delivered"
          ? "delivered"
          : event.kind === "message.bounced"
            ? "bounced"
            : null;
    rows.push({ type: "event", key: event.id, event, delivery });
  }
  flush();
  return rows;
}

/** The short kind pill: a message's own kind (`request`), else the event kind. */
export function kindPill(event: EventView): string {
  if (event.kind === "message.queued") {
    const kind = event.data?.kind;
    return typeof kind === "string" ? kind : "message";
  }
  if (event.kind === "message.delivered" || event.kind === "message.bounced") return "message";
  return event.kind.replace(/\./g, " ");
}

function actor(data: EventView["data"], key: "from" | "to"): string | null {
  const value = data?.[key];
  return typeof value === "string" ? value : null;
}

/** Who acted: the session title, else the server-side actor (`server`, `GitHub`). */
export function fromLabel(event: EventView): string {
  return (
    event.from_title ??
    actor(event.data, "from") ??
    (kindGroup(event.kind) === "gates" ? "GitHub" : "Factory")
  );
}

/** Who it was for: the session title, the server-side target, or the PR from the subject. */
export function toLabel(event: EventView): string | null {
  if (event.to_title) return event.to_title;
  const to = actor(event.data, "to");
  if (to) return to;
  const pr = event.subject?.match(/#(\d+)$/);
  return pr ? `#${pr[1]}` : null;
}
