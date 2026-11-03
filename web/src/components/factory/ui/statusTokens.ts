// The factory's one visual vocabulary: what each colour, ring, glyph and word
// means. Colour has one meaning each (amber needs you, red broken, green
// working, orange stuck, grey idle, blue selection and data, violet staged);
// shape repeats it so everything reads in greyscale. Every primitive in
// `components/factory/ui/` and the older tone maps read from this module.

import type { FactoryMarkerTone } from "@/lib/factoryMessages";
import type { FactoryRole, FactoryStage, MemberState } from "@/lib/factoryApi";

// ── Meanings ───────────────────────────────────────────────────

/** What a colour says. Never use a hue for anything but its meaning. */
export type StatusMeaning =
  "needsYou" | "broken" | "working" | "stuck" | "idle" | "selected" | "staged" | "brand";

export interface MeaningTokens {
  /** CSS colour for graphics (rings, bars, dots, stripes). */
  hue: string;
  /** Text-safe colour class (>= 4.5:1 on cards in both themes). */
  text: string;
  /** Tinted surface class for chips and panels. */
  surface: string;
  /** Border class for outlined chips and staged controls. */
  border: string;
}

export const MEANING: Record<StatusMeaning, MeaningTokens> = {
  needsYou: {
    hue: "var(--status-yellow)",
    text: "text-status-yellow-fg",
    surface: "bg-status-yellow/15",
    border: "border-status-yellow/55",
  },
  broken: {
    hue: "var(--status-red)",
    text: "text-status-red-fg",
    surface: "bg-status-red/15",
    border: "border-status-red/55",
  },
  working: {
    hue: "var(--status-green)",
    text: "text-status-green-fg",
    surface: "bg-status-green/15",
    border: "border-status-green/55",
  },
  stuck: {
    hue: "var(--status-orange)",
    text: "text-status-orange-fg",
    surface: "bg-status-orange/15",
    border: "border-status-orange/55",
  },
  idle: {
    hue: "var(--status-gray)",
    text: "text-muted-foreground",
    surface: "bg-muted",
    border: "border-border",
  },
  selected: {
    hue: "var(--status-blue)",
    text: "text-status-blue-fg",
    surface: "bg-status-blue/15",
    border: "border-status-blue",
  },
  staged: {
    hue: "var(--status-violet)",
    text: "text-status-violet-fg",
    surface: "bg-status-violet/15",
    border: "border-status-violet border-dashed",
  },
  brand: {
    hue: "var(--brand-accent)",
    text: "text-brand-accent",
    surface: "bg-brand-accent/10",
    border: "border-brand-accent/40",
  },
};

/** The one data hue for charts; categories differ by shade, label or hatching. */
export const DATA_HUE = "var(--chart-data)";
/** Faint text (the raised tertiary token): ids, axis ticks, zeros. */
export const FAINT_TEXT = "text-foreground-tertiary";
/** Blue focus ring, shown for keyboard focus only. */
export const FOCUS_RING =
  "outline-none focus-visible:ring-2 focus-visible:ring-status-blue focus-visible:ring-offset-1 focus-visible:ring-offset-background";
/** Hatching for "missing link", "stuck" and "do evidence needed". */
export function hatch(color: string, gap = 3): string {
  return `repeating-linear-gradient(135deg, ${color} 0 ${gap}px, transparent ${gap}px ${gap * 2}px)`;
}

// ── Agent states (rings, state words) ──────────────────────────

/** The state a member's ring shows. */
export type AgentState =
  "working" | "idle" | "done" | "waiting" | "stuck" | "dead" | "starting" | "staged";

export const AGENT_STATES: readonly AgentState[] = [
  "working",
  "waiting",
  "stuck",
  "dead",
  "starting",
  "idle",
  "done",
  "staged",
];

export const STATE_WORD: Record<AgentState, string> = {
  working: "Working",
  idle: "Idle",
  done: "Done",
  waiting: "Waiting on you",
  stuck: "Stuck",
  dead: "Stopped",
  starting: "Starting",
  staged: "Staged",
};

/** The meaning each state's colour carries. */
export const STATE_MEANING: Record<AgentState, StatusMeaning> = {
  working: "working",
  idle: "idle",
  done: "idle",
  waiting: "needsYou",
  stuck: "stuck",
  dead: "broken",
  starting: "selected",
  staged: "staged",
};

export type RingStroke = "solid" | "dashed" | "dotted";

export interface RingStyle {
  color: string;
  stroke: RingStroke;
  /** Stroke width in px at the default 30 px size. */
  width: number;
  /** Hollow and dimmed (done), or glyph dimmed (dead). */
  faded: boolean;
  /** A small mark over the ring: × for dead, a check for done. */
  mark: "none" | "x" | "check";
  /** Only working rings move (and stop under reduced motion). */
  pulse: boolean;
}

export const STATE_RING: Record<AgentState, RingStyle> = {
  working: {
    color: MEANING.working.hue,
    stroke: "solid",
    width: 2,
    faded: false,
    mark: "none",
    pulse: true,
  },
  waiting: {
    color: MEANING.needsYou.hue,
    stroke: "solid",
    width: 2,
    faded: false,
    mark: "none",
    pulse: false,
  },
  stuck: {
    color: MEANING.stuck.hue,
    stroke: "dashed",
    width: 2,
    faded: false,
    mark: "none",
    pulse: false,
  },
  dead: {
    color: MEANING.broken.hue,
    stroke: "solid",
    width: 2,
    faded: true,
    mark: "x",
    pulse: false,
  },
  starting: {
    color: MEANING.selected.hue,
    stroke: "dotted",
    width: 2,
    faded: false,
    mark: "none",
    pulse: false,
  },
  idle: {
    color: MEANING.idle.hue,
    stroke: "solid",
    width: 1.3,
    faded: false,
    mark: "none",
    pulse: false,
  },
  done: {
    color: MEANING.idle.hue,
    stroke: "solid",
    width: 1.3,
    faded: true,
    mark: "check",
    pulse: false,
  },
  staged: {
    color: MEANING.staged.hue,
    stroke: "dashed",
    width: 2,
    faded: false,
    mark: "none",
    pulse: false,
  },
};

/** The member fields a state is read from (today's snapshot, plus `stuck` once members carry it). */
export interface AgentStateInput {
  running: boolean;
  status: string;
  health: string | null;
  state?: MemberState | null;
  stuck?: boolean | null;
}

/**
 * A member's ring state from the snapshot: dead and stuck outrank working,
 * waiting means blocked on a person, a finished member is done.
 */
export function agentStateOf(member: AgentStateInput): AgentState {
  if (member.health === "dead" || member.status === "failed") return "dead";
  if (member.stuck) return "stuck";
  if (member.running) return "working";
  if (member.status === "waiting") return "waiting";
  if (member.status === "starting" || member.status === "launching") return "starting";
  if (member.state === "done") return "done";
  return "idle";
}

// ── Roles, seats, vendors ──────────────────────────────────────

/** A lead seat; all three are role `lead` today, told apart by the seat they hold. */
export type LeadSeat = "portfolio" | "reliability" | "discovery";

export type RoleGlyph =
  "compass" | "shield" | "radar" | "hexagon" | "code" | "eye" | "play" | "box" | "agent";

/** Where a role sits on a team chart. */
export type RoleGroup = "lead" | "build" | "check" | "run";

export interface RoleInfo {
  /** The v2 label: avatars, tiles, team lines ("Builder", "Demo"). */
  label: string;
  /** The older long name (hover cards, the agent header). */
  name: string;
  /** Compact chip text on the board ("PL", "rev"). */
  short: string;
  /** One word for tool-call summaries ("Reviewer", "IC"). */
  word: string;
  glyph: RoleGlyph;
  group: RoleGroup;
  /** Classes layered over the transcript's neutral role chip. */
  chip: string;
}

export const ROLE_INFO: Record<FactoryRole, RoleInfo> = {
  lead: {
    label: "Lead",
    name: "Lead",
    short: "lead",
    word: "Lead",
    glyph: "compass",
    group: "lead",
    chip: "border-brand-accent/40 text-brand-accent",
  },
  project_lead: {
    label: "Project lead",
    name: "Project lead",
    short: "PL",
    word: "PL",
    glyph: "hexagon",
    group: "lead",
    chip: "border-info/40 text-info",
  },
  ic: {
    label: "Builder",
    name: "Implementer",
    short: "ic",
    word: "IC",
    glyph: "code",
    group: "build",
    chip: "",
  },
  env_keeper: {
    label: "Env keeper",
    name: "Env keeper",
    short: "env",
    word: "Env",
    glyph: "box",
    group: "run",
    chip: "border-success/40 text-success",
  },
  reviewer: {
    label: "Reviewer",
    name: "Reviewer",
    short: "rev",
    word: "Reviewer",
    glyph: "eye",
    group: "check",
    chip: "",
  },
  demo: {
    label: "Demo",
    name: "Demo recorder",
    short: "demo",
    word: "Demo",
    glyph: "play",
    group: "check",
    chip: "border-success/40 text-success",
  },
  planner: {
    label: "Planner",
    name: "Planner",
    short: "plan",
    word: "planner",
    glyph: "agent",
    group: "lead",
    chip: "",
  },
  scout: {
    label: "Discovery lead",
    name: "Discovery lead (Scout)",
    short: "scout",
    word: "Scout",
    glyph: "radar",
    group: "lead",
    chip: "border-brand-accent/40 text-brand-accent",
  },
};

export const SEAT_INFO: Record<LeadSeat, { label: string; glyph: RoleGlyph }> = {
  portfolio: { label: "Portfolio lead", glyph: "compass" },
  reliability: { label: "Reliability lead", glyph: "shield" },
  discovery: { label: "Discovery lead", glyph: "radar" },
};

function roleMap<K extends keyof RoleInfo>(key: K): Record<FactoryRole, RoleInfo[K]> {
  return Object.fromEntries(
    Object.entries(ROLE_INFO).map(([role, info]) => [role, info[key]]),
  ) as Record<FactoryRole, RoleInfo[K]>;
}

/** Board chip text per role. */
export const ROLE_SHORT: Record<FactoryRole, string> = roleMap("short");
/** Long role names (hover cards, the agent header, the palette). */
export const ROLE_NAME: Record<FactoryRole, string> = roleMap("name");
/** One-word role names for tool-call summaries. */
export const ROLE_WORD: Record<FactoryRole, string> = roleMap("word");

/** A role's label and glyph, with a lead's seat winning over its role. */
export function roleLook(
  role: FactoryRole | null | undefined,
  seat?: LeadSeat | null,
): { label: string; glyph: RoleGlyph } {
  if (seat) return SEAT_INFO[seat];
  const info = roleInfo(role);
  // A role this build does not know yet still draws, as a plain agent.
  if (!info) return { label: role || "Agent", glyph: "agent" };
  return { label: info.label, glyph: info.glyph };
}

/** A role's entry, for known roles only (never `Object.prototype` keys). */
function roleInfo(role: string | null | undefined): RoleInfo | undefined {
  return role && Object.hasOwn(ROLE_INFO, role) ? ROLE_INFO[role as FactoryRole] : undefined;
}

/** The coding-agent vendors a member can run on. */
export type Vendor = "claude" | "codex";

/** Badge letters: shape, not colour, tells vendors apart. */
export const VENDOR_BADGE: Record<Vendor, string> = { claude: "C", codex: "X" };

/** The vendor a harness name runs on, when it is one we draw. */
export function vendorOf(harness: string | null | undefined): Vendor | null {
  if (!harness) return null;
  const h = harness.toLowerCase();
  if (h.includes("claude")) return "claude";
  if (h.includes("codex")) return "codex";
  return null;
}

// ── Stages ─────────────────────────────────────────────────────

/** The six segments of a project's stage bar. */
export type StageStep = "plan" | "build" | "review" | "verify" | "ready" | "shipped";

export const STAGE_STEPS: readonly StageStep[] = [
  "plan",
  "build",
  "review",
  "verify",
  "ready",
  "shipped",
];

export const STAGE_STEP_LABEL: Record<StageStep, string> = {
  plan: "Plan",
  build: "Build",
  review: "Review",
  verify: "Verify",
  ready: "Ready for merge",
  shipped: "Shipped",
};

/** The bar segment a project's derived stage falls in. */
export function stageStepOf(stage: FactoryStage): StageStep {
  switch (stage) {
    case "queued":
    case "planning":
      return "plan";
    case "building":
      return "build";
    case "review":
      return "review";
    case "verify":
      return "verify";
    case "ready":
      return "ready";
    case "merged":
    case "done":
      return "shipped";
  }
}

// ── Discovery ──────────────────────────────────────────────────

/** Evidence strength; a hunch is a person's conviction, not user evidence. */
export type EvidenceLevel = "hunch" | "weak" | "moderate" | "strong" | "proven";

export const EVIDENCE_LEVELS: readonly EvidenceLevel[] = [
  "hunch",
  "weak",
  "moderate",
  "strong",
  "proven",
];

/** Filled squares (of five) per level; a hunch draws one hollow square. */
export function evidenceSquares(level: EvidenceLevel): number {
  return EVIDENCE_LEVELS.indexOf(level) + 1;
}

/** A readout's verdict, as a shape and a word. */
export type ReadoutVerdict = "won" | "flat" | "lost" | "inconclusive";

export const READOUT_MARK: Record<ReadoutVerdict, string> = {
  won: "▲",
  flat: "▬",
  lost: "▼",
  inconclusive: "?",
};

export const READOUT_WORD: Record<ReadoutVerdict, string> = {
  won: "won",
  flat: "flat",
  lost: "lost",
  inconclusive: "inconclusive",
};

/** The five stages of the loop the factory runs. */
export type LoopStageKey = "sense" | "make_sense" | "decide" | "build" | "measure";

export const LOOP_STAGES: readonly LoopStageKey[] = [
  "sense",
  "make_sense",
  "decide",
  "build",
  "measure",
];

export const LOOP_STAGE_LABEL: Record<LoopStageKey, string> = {
  sense: "Sense",
  make_sense: "Make sense",
  decide: "Decide",
  build: "Build",
  measure: "Measure",
};

/** Stages that exist only while discovery is on. */
export const DISCOVERY_STAGES: ReadonlySet<LoopStageKey> = new Set([
  "sense",
  "make_sense",
  "decide",
]);

/** The links of a why-chain, from evidence to impact. */
export type WhyLinkKind = "evidence" | "opportunity" | "bet" | "test" | "project" | "readout";

export const WHY_LINK_LABEL: Record<WhyLinkKind, string> = {
  evidence: "Evidence",
  opportunity: "Opportunity",
  bet: "Bet",
  test: "Test",
  project: "Project",
  readout: "Readout",
};

/** A segment of an agent's state history. */
export type StripState = "working" | "waiting_ci" | "waiting" | "stuck" | "idle" | "dead";

export const STRIP_STATES: readonly StripState[] = [
  "working",
  "waiting_ci",
  "waiting",
  "stuck",
  "idle",
  "dead",
];

export const STRIP_WORD: Record<StripState, string> = {
  working: "working",
  waiting_ci: "waiting on checks or preview",
  waiting: "waiting on you",
  stuck: "stuck",
  idle: "idle",
  dead: "stopped",
};

/** Fill per strip state (stuck is hatched, so it reads without colour). */
export const STRIP_FILL: Record<StripState, string> = {
  working: MEANING.working.hue,
  waiting_ci: MEANING.selected.hue,
  waiting: MEANING.needsYou.hue,
  stuck: hatch(MEANING.stuck.hue),
  idle: "color-mix(in srgb, var(--status-gray) 22%, transparent)",
  dead: MEANING.broken.hue,
};

// ── Older tone maps, one table ─────────────────────────────────

/**
 * A semantic status tone of the older cockpit. Each maps onto an existing
 * color token: `pass` success, `pending`/`warning` warning, `fail`
 * destructive, `merged` the brand accent, `active` the session-active blue,
 * `muted` grey.
 */
export type FactoryTone = "pass" | "pending" | "warning" | "fail" | "merged" | "active" | "muted";

type ToneKey = "success" | "warning" | "danger" | "accent" | "active" | "info" | "muted";

/** Every class recipe the older surfaces use, per tone. */
const TONE_RECIPE: Record<ToneKey, { dot: string; text: string; chip: string; pill: string }> = {
  success: {
    dot: "bg-success",
    text: "text-success",
    chip: "bg-success/10 text-success",
    pill: "border-success/25 bg-success/10 text-success",
  },
  warning: {
    dot: "bg-warning",
    text: "text-warning",
    chip: "bg-warning/15 text-warning",
    pill: "border-warning/30 bg-warning/10 text-warning",
  },
  danger: {
    dot: "bg-destructive",
    text: "text-destructive",
    chip: "bg-destructive/10 text-destructive",
    pill: "border-destructive/25 bg-destructive/10 text-destructive",
  },
  accent: {
    dot: "bg-brand-accent",
    text: "text-brand-accent",
    chip: "bg-brand-accent/10 text-brand-accent",
    pill: "border-brand-accent/25 bg-brand-accent/10 text-brand-accent",
  },
  active: {
    dot: "bg-session-active",
    text: "text-session-active",
    chip: "bg-session-active/10 text-session-active",
    pill: "border-session-active/25 bg-session-active/10 text-session-active",
  },
  info: {
    dot: "bg-info",
    text: "text-info",
    chip: "bg-info/10 text-info",
    pill: "border-info/25 bg-info/10 text-info",
  },
  muted: {
    dot: "bg-muted-foreground/40",
    text: "text-muted-foreground",
    chip: "bg-muted text-muted-foreground",
    pill: "border-border bg-muted text-muted-foreground",
  },
};

const FACTORY_TONE_KEY: Record<FactoryTone, ToneKey> = {
  pass: "success",
  pending: "warning",
  warning: "warning",
  fail: "danger",
  merged: "accent",
  active: "active",
  muted: "muted",
};

const MARKER_TONE_KEY: Record<FactoryMarkerTone, ToneKey> = {
  muted: "muted",
  info: "info",
  success: "success",
  accent: "accent",
  warning: "warning",
  danger: "danger",
};

function toneMap<T extends string>(
  keys: Record<T, ToneKey>,
  part: "dot" | "text" | "chip" | "pill",
): Record<T, string> {
  return Object.fromEntries(
    Object.entries<ToneKey>(keys).map(([tone, key]) => [tone, TONE_RECIPE[key][part]]),
  ) as Record<T, string>;
}

/** Small round dot fill per tone. */
export const TONE_DOT: Record<FactoryTone, string> = toneMap(FACTORY_TONE_KEY, "dot");
/** Text color per tone. */
export const TONE_TEXT: Record<FactoryTone, string> = toneMap(FACTORY_TONE_KEY, "text");
/** Tinted chip surface per tone (the "Needs response" badge recipe). */
export const TONE_CHIP: Record<FactoryTone, string> = toneMap(FACTORY_TONE_KEY, "chip");
/** Transcript pill classes per marker tone (border + tinted fill + text). */
export const TONE_PILL: Record<FactoryMarkerTone, string> = toneMap(MARKER_TONE_KEY, "pill");
/** Transcript icon colour per marker tone. */
export const TONE_ICON: Record<FactoryMarkerTone, string> = toneMap(MARKER_TONE_KEY, "text");

/** Sender role → classes layered over the transcript's neutral role chip. */
export function roleChipTone(role: string | null | undefined): string {
  return roleInfo(role)?.chip ?? "";
}
