// The Now card's words: the server's headline, the waits split by whom they
// wait on (with the viewer's Slack DMs about them), and short lines for work,
// spend and health. Pure; the server picks what to say (status_summary.py,
// which the Slack digest is built from too) and this ages it against the
// cockpit's clock. Tests pin it.

import type { FactoryStage } from "@/lib/factoryApi";
import type {
  StatusCheckIn,
  StatusHealthItem,
  StatusLeadNote,
  StatusSlackReceipt,
  StatusNextItem,
  StatusSpend,
  StatusSummary,
  StatusTextPart,
  StatusWaitingItem,
  StatusWorkingItem,
} from "@/lib/factoryStatusApi";
import { STAGE_LABEL, formatElapsed, formatUsd, plural } from "../factoryFormat";
import { whenText } from "../notify/notifyModel";

/** Waits listed for the viewer before "+n more". */
export const WAITING_SHOWN = 5;
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

/**
 * The card's first line: the server's headline, word for word the one the
 * Slack digest opens with (`4 PRs wait on you · 1 project in progress`,
 * `Idle · 2 things wait on you`).
 */
export function nowHeadline(summary: StatusSummary): string {
  return summary.headline.replace(/\.$/, "");
}

/** `No active projects since 09:12`: Working on, when the factory is idle. */
export function idleText(spend: StatusSpend, nowS: number): string {
  return spend.idle_since
    ? `No active projects since ${clockText(spend.idle_since, nowS)}`
    : "No active projects";
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

/** The waits the card lists as lines: the viewer's first few, else a few of others'. */
export function shownWaiting(split: WaitingSplit): StatusWaitingItem[] {
  return split.mine.length > 0
    ? split.mine.slice(0, WAITING_SHOWN)
    : split.others.slice(0, OTHERS_SHOWN);
}

/**
 * The Needs-you panel's keys of the waits the card lists, so the panel below
 * leaves them out (an incident is an `issue:` there).
 */
export function nowShownKeys(summary: StatusSummary): Set<string> {
  return new Set(
    shownWaiting(splitWaiting(summary)).map((item) =>
      item.kind === "incident" ? item.key.replace(/^incident:/, "issue:") : item.key,
    ),
  );
}

/** The detail after a wait's text: a PR or incident names its project first. */
export function waitingDetail(item: StatusWaitingItem): string | null {
  const project = item.project && item.kind !== "decision" ? item.project : null;
  if (project && item.detail) return `${project}: ${item.detail}`;
  return project ?? item.detail ?? null;
}

const FIRST_DM = new Set(["new", "ready", "opened", "owners"]);

function receiptWord(stage: string): string {
  if (FIRST_DM.has(stage)) return "DM'd";
  if (stage === "deadline") return "last call";
  return "reminded";
}

/**
 * `DM'd 10:04 · reminded 14:04`: the viewer's first Slack DM about a wait and
 * the newest after it (a clock time today, else how long ago); null without one.
 */
export function receiptText(
  slack: readonly StatusSlackReceipt[] | null | undefined,
  nowS: number,
): string | null {
  const sent = [...(slack ?? [])].sort((a, b) => a.at - b.at);
  const first = sent[0];
  if (!first) return null;
  const parts = [`${receiptWord(first.stage)} ${whenText(first.at, nowS)}`];
  const last = sent.at(-1)!;
  if (last !== first) parts.push(`${receiptWord(last.stage)} ${whenText(last.at, nowS)}`);
  return parts.join(" · ");
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

/**
 * Why a queued project waits, with how long its start step has taken, e.g.
 * `starting up: pulling its image · 3m`.
 */
export function nextReason(item: StatusNextItem, nowS: number): string {
  const took = item.step_since != null ? formatElapsed(nowS - item.step_since) : null;
  return took ? `${item.reason} · ${took}` : item.reason;
}

/** Whom someone else's wait is for: `for mia`, `for owners`. */
export function waitsOn(item: StatusWaitingItem): string {
  return `for ${item.who}`;
}

/** `Building`: a working project's stage (its status line names the PR). */
export function workingStage(item: StatusWorkingItem): string {
  return STAGE_LABEL[item.stage as FactoryStage] ?? item.stage;
}

/**
 * The plain line after a working project's stage: its problem, else its status
 * from the stage, task and PR. The lead's raw label never shows (an older
 * server's summary has no status: the line says nothing more).
 */
export function workingNote(item: StatusWorkingItem): string | null {
  return item.problem ?? item.status ?? null;
}

/** The status is only the lead's own (trimmed) label: the card quotes it. */
export function workingNoteQuoted(item: StatusWorkingItem): boolean {
  return !item.problem && item.status_source === "lead";
}

/** The current $/h: the header's (the live snapshot's), else the summary's. */
export function spendRate(spend: StatusSpend, usdPerH?: number | null): number | null {
  return usdPerH !== undefined ? usdPerH : (spend.usd_per_h ?? null);
}

/**
 * `$61.44 in the last 24 h · $3.10/h now`, as the Slack digest says it; the
 * rate is left out when the idle call-out already says it (`idleSpendText`).
 *
 * @param usdPerH - The header's rate, so the two never differ (undefined: the summary's).
 */
export function spendText(spend: StatusSpend, usdPerH?: number | null): string {
  const day =
    spend.last_24h_usd != null
      ? `${formatUsd(spend.last_24h_usd)} in the last 24 h`
      : "No spend recorded in the last 24 h";
  const rate = formatUsd(spendRate(spend, usdPerH));
  return rate && !idleSpendText(spend, usdPerH) ? `${day} · ${rate}/h now` : day;
}

/** `Idle, but still spending $2.71/h`, or null when idle costs nothing. */
export function idleSpendText(spend: StatusSpend, usdPerH?: number | null): string | null {
  const rate = formatUsd(spendRate(spend, usdPerH));
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
 * or Next) set apart: they fold into one line. A project that only waits on a
 * wait the card lists (`holds`) is said there, so its line is dropped.
 */
export function splitHealth(
  items: StatusHealthItem[],
  listed: readonly { project: string }[],
  waits: readonly StatusWaitingItem[] = [],
): HealthSplit {
  const shown = new Set(listed.map((item) => item.project));
  const held = new Set(waits.filter((w) => w.holds && w.project).map((w) => w.project));
  const kept = items.filter((item) => item.project == null || !held.has(item.project));
  const folds = (item: StatusHealthItem) => item.project != null && shown.has(item.project);
  return { attention: kept.filter(folds), listed: kept.filter((item) => !folds(item)) };
}

/** `2 projects need attention: cache-warm, db-version`. */
export function attentionText(items: readonly StatusHealthItem[]): string {
  const names = items.map((item) => item.project).filter(Boolean);
  const verb = names.length === 1 ? "needs" : "need";
  return `${plural(names.length, "project")} ${verb} attention: ${names.join(", ")}`;
}

/** `Atlas said 52m ago`: who said the lead's one-liner, and how fresh it is. */
export function leadWho(lead: StatusLeadNote, nowS: number): string {
  const age = lead.since ? formatElapsed(nowS - lead.since) : null;
  return age ? `${lead.who} said ${age} ago` : `${lead.who} said`;
}

/** The lead's note as runs of text (`at`: where each starts), linked where the server resolved an id. */
export function leadParts(lead: StatusLeadNote): (StatusTextPart & { at: number })[] {
  const parts = lead.parts?.length ? lead.parts : [{ text: lead.text, href: null }];
  let at = 0;
  return parts.map((part) => {
    const run = { ...part, at };
    at += part.text.length;
    return run;
  });
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
