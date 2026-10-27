// What a cockpit control is waiting for. A control is pending from the click
// until every recipient replies (`control.ack`); the server's `control.*`
// events are the truth, so a reload still shows what is in flight, and a
// click shows at once before its event exists.

import type {
  EventView,
  FactoryCadence,
  FactoryControlAction,
  LaunchDriftView,
  MemberView,
} from "@/lib/factoryApi";
import { ApiError } from "@/lib/sessionsApi";
import { heartbeatsPerHour, sentence } from "../start/projectForm";

/**
 * Controls whose recipients acknowledge (cadence only relabels). A preview
 * control with `mode: "down"` reads as `preview_down`.
 */
export type AckedAction =
  Exclude<FactoryControlAction, "cadence"> | "preview_down" | "charter_update";

/** No reply after this long: the chip turns amber and offers a restart. */
export const ACK_LATE_MS = 15 * 60_000;
/** Controls older than this are history, acked or not. */
export const PENDING_MAX_AGE_MS = 6 * 3600_000;
/** How long a confirmed control keeps its "✓" chip. */
export const DONE_SHOW_MS = 60_000;
/** How long a failed click keeps its error chip. */
export const ERROR_SHOW_MS = 60_000;

/** Clock skew tolerated between a click's stamp and its server event. */
const SKEW_MS = 60_000;

export type PendingState = "sending" | "waiting" | "late" | "error" | "done";

/** A click not yet (or just) known to the server, kept in this tab. */
export interface LocalControl {
  localId: string;
  action: AckedAction;
  /** `factory`, `project:<slug>` or `member:<id>`. */
  target: string;
  project: string | null;
  /** Epoch ms on the cockpit clock (the event's own time once it exists). */
  at: number;
  /** The server took it (an id may still be unknown, e.g. a charter PATCH). */
  sent: boolean;
  /** The `control.<action>` event id the server returned. */
  eventId: string | null;
  recipients: string[] | null;
  error: string | null;
  /** E.g. `#9631` for a preview switch. */
  label: string | null;
}

/** One control as the cockpit shows it. */
export interface PendingControl {
  key: string;
  action: AckedAction;
  target: string;
  project: string | null;
  at: number;
  eventId: string | null;
  recipients: string[];
  acked: string[];
  state: PendingState;
  error: string | null;
  label: string | null;
  /** The local click this came from, when it was made in this tab. */
  localId: string | null;
}

const ACKED_ACTIONS: ReadonlySet<string> = new Set([
  "pause",
  "resume",
  "stop",
  "restart",
  "relaunch",
  "preview",
  "env_status",
  "charter_update",
]);

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function targetOf(event: EventView): string {
  const target = event.data?.target;
  if (typeof target === "string" && target) return target;
  if (event.subject?.startsWith("project:")) return event.subject;
  return event.project_slug ? `project:${event.project_slug}` : "factory";
}

function optionsOf(event: EventView): Record<string, unknown> {
  const options = event.data?.options;
  return options && typeof options === "object" ? (options as Record<string, unknown>) : {};
}

/** The acknowledged action a control request or event stands for. */
export function ackedAction(action: string, options?: Record<string, unknown> | null): string {
  return action === "preview" && options?.mode === "down" ? "preview_down" : action;
}

function labelOf(event: EventView): string | null {
  const pr = optionsOf(event).pr;
  return typeof pr === "number" ? `#${pr}` : null;
}

interface DerivedControl {
  eventId: string;
  action: AckedAction;
  target: string;
  project: string | null;
  at: number;
  recipients: string[];
  acked: string[];
  ackedAt: number | null;
  label: string | null;
}

/**
 * The newest acknowledged-kind control per target in `events` (newest first),
 * with who has acknowledged it so far.
 */
export function controlsFromEvents(events: readonly EventView[], nowMs: number): DerivedControl[] {
  const acks = new Map<string, { from: Set<string>; last: number }>();
  for (const event of events) {
    if (event.kind !== "control.ack") continue;
    const id = event.data?.control_event_id;
    if (typeof id !== "string") continue;
    const entry = acks.get(id) ?? { from: new Set<string>(), last: 0 };
    if (event.from_id) entry.from.add(event.from_id);
    entry.last = Math.max(entry.last, event.ts);
    acks.set(id, entry);
  }
  const newest = new Map<string, DerivedControl>();
  for (const event of events) {
    const kind = event.kind.startsWith("control.") ? event.kind.slice("control.".length) : "";
    if (!ACKED_ACTIONS.has(kind)) continue;
    const action = ackedAction(kind, optionsOf(event));
    if (nowMs - event.ts > PENDING_MAX_AGE_MS) continue;
    const target = targetOf(event);
    const seen = newest.get(target);
    if (seen && seen.at >= event.ts) continue;
    const ack = acks.get(event.id);
    newest.set(target, {
      eventId: event.id,
      action: action as AckedAction,
      target,
      project: event.project_slug,
      at: event.ts,
      recipients: strings(event.data?.recipients),
      acked: ack ? [...ack.from] : [],
      ackedAt: ack ? ack.last : null,
      label: labelOf(event),
    });
  }
  return [...newest.values()];
}

function settled(recipients: readonly string[], acked: readonly string[]): boolean {
  if (acked.length === 0) return false;
  return recipients.length === 0 || recipients.every((r) => acked.includes(r));
}

/**
 * Merge this tab's clicks with the server's control events into what each
 * target shows now: the newest control per target, or nothing once it was
 * confirmed a while ago.
 */
export function pendingControls(
  local: readonly LocalControl[],
  events: readonly EventView[],
  nowMs: number,
): PendingControl[] {
  const derived = controlsFromEvents(events, nowMs);
  const byEvent = new Map(derived.map((d) => [d.eventId, d]));
  const candidates: (PendingControl & { ackedAt: number | null })[] = [];
  for (const d of derived) {
    candidates.push({
      key: d.target,
      action: d.action,
      target: d.target,
      project: d.project,
      at: d.at,
      eventId: d.eventId,
      recipients: d.recipients,
      acked: d.acked,
      state: "waiting",
      error: null,
      label: d.label,
      localId: local.find((l) => l.eventId === d.eventId)?.localId ?? null,
      ackedAt: d.ackedAt,
    });
  }
  for (const l of local) {
    if (l.eventId && byEvent.has(l.eventId)) continue;
    // A sent click whose event id is unknown is the server's newest one of its kind.
    const twin = derived.find(
      (d) => d.target === l.target && d.action === l.action && d.at >= l.at - SKEW_MS,
    );
    if (l.sent && !l.eventId && twin) continue;
    candidates.push({
      key: l.target,
      action: l.action,
      target: l.target,
      project: l.project,
      at: l.at,
      eventId: l.eventId,
      recipients: l.recipients ?? [],
      acked: [],
      state: l.error ? "error" : l.sent ? "waiting" : "sending",
      error: l.error,
      label: l.label,
      localId: l.localId,
      ackedAt: null,
    });
  }
  const newest = new Map<string, (typeof candidates)[number]>();
  for (const c of candidates) {
    const seen = newest.get(c.key);
    // A click still in flight is newer than anything the server has.
    const rank = (x: typeof c) => (x.state === "sending" || x.state === "error" ? Infinity : x.at);
    if (!seen || rank(c) > rank(seen) || (rank(c) === rank(seen) && c.at > seen.at)) {
      newest.set(c.key, c);
    }
  }
  const out: PendingControl[] = [];
  for (const { ackedAt, ...c } of newest.values()) {
    if (c.state === "error") {
      if (nowMs - c.at <= ERROR_SHOW_MS) out.push(c);
      continue;
    }
    if (c.state === "waiting" && settled(c.recipients, c.acked)) {
      if (ackedAt != null && nowMs - ackedAt <= DONE_SHOW_MS) out.push({ ...c, state: "done" });
      continue;
    }
    if (c.state === "waiting" && nowMs - c.at > ACK_LATE_MS) {
      out.push({ ...c, state: "late" });
      continue;
    }
    out.push(c);
  }
  return out;
}

/** The control a project card shows: its own target, or a member of it. */
export function pendingForProject(
  pending: readonly PendingControl[],
  slug: string,
): PendingControl | null {
  const mine = pending.filter(
    (p) => p.target === `project:${slug}` || (p.target.startsWith("member:") && p.project === slug),
  );
  return mine.sort((a, b) => b.at - a.at)[0] ?? null;
}

const VERB: Record<AckedAction, string> = {
  pause: "Pausing",
  resume: "Resuming",
  stop: "Stopping",
  restart: "Restarting",
  relaunch: "Relaunching",
  preview: "Deploying preview",
  preview_down: "Taking down preview",
  env_status: "Asking for env status",
  charter_update: "Sending the charter",
};

const DONE: Record<AckedAction, string> = {
  pause: "paused",
  resume: "resumed",
  stop: "stopped",
  restart: "back after restart",
  relaunch: "relaunched",
  preview: "deploying the preview",
  preview_down: "taking the preview down",
  env_status: "reporting env status",
  charter_update: "has the new charter",
};

const NAME: Record<AckedAction, string> = {
  pause: "Pause",
  resume: "Resume",
  stop: "Stop",
  restart: "Restart",
  relaunch: "Relaunch",
  preview: "Preview switch",
  preview_down: "Preview take-down",
  env_status: "Env status",
  charter_update: "Charter update",
};

/**
 * The chip text, e.g. `Pausing… waiting for PL`.
 *
 * @param who - The recipient as the human knows it (`PL`, `Atlas`), or null
 *   for a factory-wide control (it counts replies instead).
 */
export function pendingText(control: PendingControl, who: string | null): string {
  const verb = `${VERB[control.action]}${control.label ? ` ${control.label}` : ""}`;
  const total = control.recipients.length;
  switch (control.state) {
    case "sending":
      return `${verb}…`;
    case "waiting":
      if (who) return `${verb}… waiting for ${who}`;
      return total > 1
        ? `${verb}… ${control.acked.length}/${total} replied`
        : `${verb}… waiting for a reply`;
    case "late":
      if (who) return `No reply from ${who} in 15 min`;
      return `${total - control.acked.length} of ${total} haven’t replied in 15 min`;
    case "error":
      return `${NAME[control.action]}: ${control.error ?? "unknown error"}`;
    case "done":
      return who ? `${who} ${DONE[control.action]}` : `All ${total} replied`;
  }
}

/** The chip on a narrow card: just the state, e.g. `Pausing…` (the full text is its title). */
export function pendingShortText(control: PendingControl): string {
  switch (control.state) {
    case "sending":
    case "waiting":
      return `${VERB[control.action]}…`;
    case "late":
      return "No reply · 15 min";
    case "error":
      return `${NAME[control.action]} failed`;
    case "done":
      return DONE[control.action];
  }
}

/** A refused control as the human should read it (403 → missing access). */
export function controlErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) return "You need edit/owner access";
  if (error instanceof Error && error.message) return sentence(error.message);
  return "Something went wrong";
}

/** A member's launch drift, when it can be relaunched from the cockpit (top-level only). */
export function relaunchableDrift(
  members: readonly MemberView[],
  sessionId: string | null | undefined,
): LaunchDriftView | null {
  if (!sessionId) return null;
  const member = members.find((m) => m.session_id === sessionId);
  return member?.is_top_level ? member.launch_drift : null;
}

/** Heartbeat turns per hour of each cadence for the factory's active leads and PLs. */
export function cadenceEstimates(members: readonly MemberView[]): Record<FactoryCadence, number> {
  const live = members.filter((m) => m.is_top_level && m.state !== "done");
  const leads = live.filter((m) => m.role === "lead").length;
  const pls = live.filter((m) => m.role === "project_lead").length;
  return {
    demo: heartbeatsPerHour("demo", leads, pls),
    normal: heartbeatsPerHour("normal", leads, pls),
  };
}

/** `≈42 vs ≈4 heartbeat turns/h`. */
export function cadenceEstimateText(members: readonly MemberView[]): string {
  const { demo, normal } = cadenceEstimates(members);
  return `≈${demo} vs ≈${normal} heartbeat turns/h`;
}
