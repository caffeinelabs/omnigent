// Slack DM notifications in the cockpit: the event groups a person picks,
// the "Slack · carol 10:04 · reminder 14:04" receipts on decisions and
// incidents, deadline chips ("expires in 6 h"), link-status words, and the
// Slack app manifest owners paste at api.slack.com/apps.

import type {
  NotifiedView,
  NotifyGroup,
  NotifySettings,
  PersonNotifySummary,
  PersonView,
  SlackLinkStatus,
} from "@/lib/factoryApi";
import { formatElapsed } from "../factoryFormat";

export interface NotifyGroupInfo {
  id: NotifyGroup;
  label: string;
  hint: string;
}

/** The event groups, in the order the settings list them. */
export const NOTIFY_GROUPS: readonly NotifyGroupInfo[] = [
  { id: "decisions", label: "Decisions", hint: "A decision is waiting on you or your role" },
  {
    id: "reminders",
    label: "Reminders",
    hint: "Still open after 4 h and 24 h; last call before a default",
  },
  { id: "prs", label: "PRs to merge", hint: "A PR is ready for human merge" },
  { id: "incidents", label: "Incidents", hint: "An incident for you or your role" },
  { id: "factory", label: "Pause and stop", hint: "Someone else paused, stopped or resumed work" },
  {
    id: "budget",
    label: "Budgets and idle spend",
    hint: "80 % and 100 % budgets; money spent with no progress",
  },
  { id: "mentions", label: "@mentions", hint: "Someone mentioned you in the Room" },
  {
    id: "retros",
    label: "Sentinel retros",
    hint: "Each retro as it is filed (the digest has the headline)",
  },
];

const STAGE_WORD: Record<string, string> = {
  new: "",
  r4h: "reminder",
  r24h: "reminder",
  owners: "owners",
  deadline: "last call",
  opened: "",
};

/** `14:04` in the browser's zone, 24-hour. */
export function hhmm(epochS: number): string {
  return new Date(epochS * 1000).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** `carol` from `carol@caffeine.ai`. */
export function shortName(userId: string): string {
  return userId.split("@")[0] || userId;
}

export interface NotifiedChip {
  key: string;
  text: string;
  ok: boolean;
  title: string;
}

/**
 * One chip per DM, oldest first: `Carol 10:04`, `reminder 14:04`; a failed
 * one reads `carol ⚠ messages_tab_disabled`.
 */
export function notifiedChips(notified: readonly NotifiedView[] | undefined): NotifiedChip[] {
  return (notified ?? []).map((n, index) => {
    const word = STAGE_WORD[n.stage] ?? n.stage;
    const who = shortName(n.user_id);
    const ok = n.ok !== false;
    const when = hhmm(n.at);
    const text = ok
      ? `${who}${word ? ` ${word}` : ""} ${when}`
      : `${who} ⚠ ${n.error ?? "not sent"}`;
    const title = `Slack DM to ${n.user_id}${word ? ` (${word})` : ""} at ${new Date(
      n.at * 1000,
    ).toLocaleString()}${ok ? "" : `: ${n.error ?? "not sent"}`}`;
    return { key: `${n.user_id}-${n.stage}-${n.at}-${index}`, text, ok, title };
  });
}

/** `expires`, `auto-applies` or `reverts` from a decision's deadline effect. */
export function deadlineVerb(effect: string | null | undefined): string {
  const text = (effect ?? "").toLowerCase();
  if (text.includes("auto-applies")) return "auto-applies";
  if (text.includes("revert")) return "reverts";
  if (text.includes("expire")) return "expires";
  return "defaults";
}

/** `expires in 6h`, `reverts in 1d 2h`, `auto-applies now`; null without a deadline. */
export function deadlineText(
  deadlineAt: number | null | undefined,
  effect: string | null | undefined,
  nowS: number,
): string | null {
  if (deadlineAt == null) return null;
  const left = deadlineAt - nowS;
  const verb = deadlineVerb(effect);
  return left <= 0 ? `${verb} now` : `${verb} in ${formatElapsed(left)}`;
}

/** Under 6 h left: the chip turns red. */
export function deadlineUrgent(deadlineAt: number | null | undefined, nowS: number): boolean {
  return deadlineAt != null && deadlineAt - nowS < 6 * 3600;
}

export const LINK_STATUS_TEXT: Record<SlackLinkStatus, string> = {
  unlinked: "not linked",
  pending: "code sent",
  verified: "linked",
  error: "broken",
};

/** The Slack link status of a roster row, from its `notify` summary. */
export function personSlackStatus(person: PersonView): SlackLinkStatus {
  const summary = (person.notify ?? {}) as PersonNotifySummary;
  const status = summary.slack;
  return status === "pending" || status === "verified" || status === "error" ? status : "unlinked";
}

/** What the person typed: a member ID (`U0123ABCD`) or a Slack email; null otherwise. */
export function parseSlackTarget(input: string): { member_id: string } | { email: string } | null {
  const text = input.trim();
  if (/^[UW][A-Z0-9]{2,20}$/i.test(text)) return { member_id: text.toUpperCase() };
  if (/^[^\s@<>|]+@[^\s@<>|]+\.[^\s@<>|]{2,}$/.test(text)) return { email: text };
  return null;
}

/** The browser's IANA zone, e.g. `Europe/Zagreb`. */
export function browserTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

const FALLBACK_ZONES = [
  "UTC",
  "Europe/London",
  "Europe/Berlin",
  "Europe/Zagreb",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Asia/Kolkata",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
];

/** Every IANA zone the browser knows (a short list when it cannot say). */
export function timeZones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  try {
    const zones = intl.supportedValuesOf?.("timeZone");
    if (zones && zones.length > 0) return zones.includes("UTC") ? zones : ["UTC", ...zones];
  } catch {
    // An older engine: the short list below.
  }
  return FALLBACK_ZONES;
}

/** `HH:00` for each hour. */
export const HOURS: readonly number[] = Array.from({ length: 24 }, (_, hour) => hour);

export function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

/** The fields of `a` that differ from `b` (the PUT body of a settings save). */
export function settingsChanges(
  draft: NotifySettings,
  saved: NotifySettings,
): Partial<NotifySettings> {
  const out: Partial<NotifySettings> = {};
  for (const key of Object.keys(draft) as (keyof NotifySettings)[]) {
    if (JSON.stringify(draft[key]) !== JSON.stringify(saved[key])) {
      (out as Record<string, unknown>)[key] = draft[key];
    }
  }
  return out;
}

/** `HH:MM` 00:00–23:59. */
export function validClock(value: string): boolean {
  return /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
}

/** The Slack app manifest (v1: send-only DMs). Paste it at api.slack.com/apps. */
export const SLACK_APP_MANIFEST = `# Omnigent Factory: direct-message notifications (v1, send-only).
# api.slack.com/apps -> Create New App -> From a manifest -> pick the workspace -> paste.
# Then: Install to Workspace -> copy the "Bot User OAuth Token" (xoxb-...) ->
# cockpit -> Settings -> Alerts -> Slack app -> paste -> Save.
# v1 only sends DMs: no event subscriptions, no interactivity, no slash commands.
display_information:
  name: Omnigent Factory
  description: DMs from your Omnigent software factory - decisions waiting on you, PRs to merge, incidents and a daily digest.
  background_color: "#1f2933"
features:
  app_home:
    home_tab_enabled: false
    messages_tab_enabled: true
    messages_tab_read_only_enabled: true
  bot_user:
    display_name: omnigent-factory
    always_online: false
oauth_config:
  scopes:
    bot:
      - chat:write
      - im:write
      - users:read
      - users:read.email
settings:
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
`;

/** A bot token as Slack issues it: `xoxb-…`. */
export function validBotToken(value: string): boolean {
  return /^xoxb-[A-Za-z0-9-]{20,250}$/.test(value.trim());
}
