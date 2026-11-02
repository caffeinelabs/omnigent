// The Now card's words: a headline for the factory's state, the waits split by
// whom they wait on, and short lines for work, spend and health. Pure; the
// server picks what to say (status_summary.py) and this ages it against the
// cockpit's clock. Tests pin it.

import type { FactoryStage } from "@/lib/factoryApi";
import type {
  StatusCheckIn,
  StatusHealthItem,
  StatusLeadNote,
  StatusSpend,
  StatusSummary,
  StatusWaitingItem,
  StatusWorkingItem,
} from "@/lib/factoryStatusApi";
import { STAGE_LABEL, formatElapsed, formatUsd, plural } from "../factoryFormat";

/** Waits listed for the viewer before "+n more". */
export const WAITING_SHOWN = 3;
/** Someone else's waits listed when none are the viewer's. */
export const OTHERS_SHOWN = 2;
/** Projects listed under Working on before "+n more". */
export const WORKING_SHOWN = 4;

/** `09:12` today, else `Oct 30, 09:12`, in the viewer's time zone (24-hour). */
export function clockText(epochS: number, nowS: number): string {
  const at = new Date(epochS * 1000);
  const time = at.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  if (at.toDateString() === new Date(nowS * 1000).toDateString()) return time;
  const day = at.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return `${day}, ${time}`;
}

/** The card's first sentence: paused, finished, idle since when, or how much is in flight. */
export function nowHeadline(summary: StatusSummary, nowS: number): string {
  switch (summary.state) {
    case "paused": {
      const pause = summary.health.items.find((item) => item.kind === "paused");
      return pause?.by ? `Paused by ${pause.by}` : "Paused";
    }
    case "done":
      return "Finished: every project is closed";
    case "idle":
      return idleText(summary.spend, nowS);
    case "working": {
      const { queued, working } = summary.counts;
      const stuck = summary.counts.cant_start ?? 0;
      if (working === 0 && stuck > 0) {
        const text = `Nothing in progress · ${plural(stuck, "project")} can't start`;
        return queued > stuck ? `${text} · ${queued - stuck} queued` : text;
      }
      if (working === 0) return `Nothing in progress yet · ${plural(queued, "project")} queued`;
      const text = `Working on ${plural(working, "project")}`;
      return queued > 0 ? `${text} · ${queued} queued` : text;
    }
  }
}

/** `Idle since 09:12 — no active projects`. */
export function idleText(spend: StatusSpend, nowS: number): string {
  return spend.idle_since
    ? `Idle since ${clockText(spend.idle_since, nowS)} — no active projects`
    : "Idle — no active projects";
}

export interface WaitingSplit {
  /** For the viewer or their role, in the server's order. */
  mine: StatusWaitingItem[];
  others: StatusWaitingItem[];
}

export function splitWaiting(summary: StatusSummary): WaitingSplit {
  return {
    mine: summary.waiting.filter((item) => item.for_you),
    others: summary.waiting.filter((item) => !item.for_you),
  };
}

/** `Nothing waiting on you`, with how many wait on others. */
export function waitingEmptyText(split: WaitingSplit): string {
  const others = split.others.length;
  return others > 0
    ? `Nothing waiting on you · ${plural(others, "thing")} waiting on others`
    : "Nothing waiting on you";
}

/** A wait this long is called out: nobody has looked at it for a day. */
export const LONG_WAIT_S = 86_400;

/** Whether a wait has gone on for a day or more. */
export function waitedLong(since: number, nowS: number): boolean {
  return since > 0 && nowS - since >= LONG_WAIT_S;
}

/** How long it has waited, e.g. `2d 18h`. */
export function waitedFor(since: number, nowS: number): string | null {
  return since > 0 ? formatElapsed(nowS - since) : null;
}

/** Whom someone else's wait is for: `for mia`, `for owners`. */
export function waitsOn(item: StatusWaitingItem): string {
  return `for ${item.who}`;
}

/** `Ready · PR #9630`: a working project's stage and PR. */
export function workingStage(item: StatusWorkingItem): string {
  const stage = STAGE_LABEL[item.stage as FactoryStage] ?? item.stage;
  return item.pr ? `${stage} · PR #${item.pr.number}` : stage;
}

/** The plain sentence after a working project's stage: its problem, else what its lead says. */
export function workingNote(item: StatusWorkingItem): string | null {
  return item.problem ?? item.doing ?? null;
}

/**
 * `$48.00 in the last 24 h · $2.71/h now`; the rate is left out when the idle
 * call-out already says it (`idleSpendText`).
 */
export function spendText(spend: StatusSpend): string {
  const day =
    spend.last_24h_usd != null
      ? `${formatUsd(spend.last_24h_usd)} in the last 24 h`
      : "No spend recorded in the last 24 h";
  const rate = formatUsd(spend.usd_per_h);
  return rate && !idleSpendText(spend) ? `${day} · ${rate}/h now` : day;
}

/** `Idle, but still spending $2.71/h`, or null when idle costs nothing. */
export function idleSpendText(spend: StatusSpend): string | null {
  const rate = formatUsd(spend.usd_per_h);
  return spend.idle_spend && rate ? `Idle, but still spending ${rate}/h` : null;
}

export interface HealthSplit {
  /** Problems of projects the card already lists under Working on. */
  attention: StatusHealthItem[];
  /** Everything else, one line each: the factory paused, GitHub, other projects. */
  listed: StatusHealthItem[];
}

/**
 * Health items, with the problems of projects the card lists (under Working on
 * or Next) set apart: they fold into one line.
 */
export function splitHealth(
  items: StatusHealthItem[],
  listed: readonly { project: string }[],
): HealthSplit {
  const shown = new Set(listed.map((item) => item.project));
  const folds = (item: StatusHealthItem) => item.project != null && shown.has(item.project);
  return { attention: items.filter(folds), listed: items.filter((item) => !folds(item)) };
}

/** `2 projects need attention: cache-warm, db-version`. */
export function attentionText(items: readonly StatusHealthItem[]): string {
  const names = items.map((item) => item.project).filter(Boolean);
  const verb = names.length === 1 ? "needs" : "need";
  return `${plural(names.length, "project")} ${verb} attention: ${names.join(", ")}`;
}

/** `Atlas, 4m ago`: who said the lead's one-liner, and how fresh it is. */
export function leadWho(lead: StatusLeadNote, nowS: number): string {
  const age = lead.since ? formatElapsed(nowS - lead.since) : null;
  return age ? `${lead.who}, ${age} ago` : lead.who;
}

/** `Atlas checks in again at 14:31`, or that the check-in is due. */
export function checkInText(checkIn: StatusCheckIn, nowS: number): string {
  return checkIn.at > nowS
    ? `${checkIn.who} checks in again at ${clockText(checkIn.at, nowS)}`
    : `${checkIn.who} is due to check in`;
}

/** `Showing 14:02 — couldn't refresh`: a card that keeps its last good read. */
export function staleText(updatedAtS: number, nowS: number): string {
  return `Showing ${clockText(updatedAtS, nowS)} — couldn’t refresh`;
}
