// Pure model of the factory Settings: which definition paths each section
// edits (with labels, units and kinds), reading a value from the doc, turning
// a form draft into `{op, path, value}` changes, and validating a draft against
// the server's bounds. Every save is one `PUT …/definition`: an applied,
// human-authored `C-n` that can be reverted.

import type { DefinitionChange, DefinitionDoc, DefinitionResponse } from "@/lib/factoryApi";

export type FieldKind = "number" | "select" | "bool" | "text";

export interface SettingField {
  path: string;
  label: string;
  hint?: string;
  /** Shown after the input: `s`, `h`, `min`, `$`, `$/day`, … */
  unit?: string;
  kind: FieldKind;
  options?: readonly { value: string; label: string; hint?: string }[];
  /** Whole numbers only. */
  integer?: boolean;
}

export interface FieldGroup {
  title: string;
  description?: string;
  fields: SettingField[];
}

const n = (
  path: string,
  label: string,
  unit?: string,
  hint?: string,
  integer = true,
): SettingField => ({ path, label, unit, hint, kind: "number", integer });

export const AUTONOMY_GROUPS: FieldGroup[] = [
  {
    title: "Projects",
    description: "When an agent's project proposal applies on its own.",
    fields: [
      {
        path: "autonomy.projects",
        label: "Project proposals",
        kind: "select",
        options: [
          { value: "confirm", label: "Confirm", hint: "every proposal waits for a click" },
          {
            value: "requested",
            label: "Requested",
            hint: "creates a human asked for apply with Undo; the rest wait",
          },
          { value: "grace", label: "Grace", hint: "applies after a grace window unless rejected" },
        ],
      },
    ],
  },
  {
    title: "Engine",
    description: "How far the loop engine acts. Roll back to observe if anything misbehaves.",
    fields: [
      {
        path: "engine.mode",
        label: "Engine mode",
        kind: "select",
        options: [
          { value: "observe", label: "Observe", hint: "measure only: loop state, findings" },
          { value: "assist", label: "Assist", hint: "NEXT hints, loop decisions, warnings" },
          { value: "enforce", label: "Enforce", hint: "refuse past budgets, auto-pause on spend" },
        ],
      },
      { path: "engine.next_hints", label: "NEXT hints to project leads", kind: "bool" },
      {
        path: "engine.spawn",
        label: "Who spawns project leads",
        kind: "select",
        options: [
          { value: "server", label: "Server", hint: "on apply, without an Atlas turn" },
          { value: "lead", label: "Lead", hint: "Atlas spawns them (fallback)" },
        ],
      },
    ],
  },
];

export const BUDGET_GROUPS: FieldGroup[] = [
  {
    title: "Money",
    description: "Notices in observe, decisions in assist, auto-pause in enforce.",
    fields: [
      n("budgets.project_usd", "Per project", "$", undefined, false),
      n("budgets.factory_usd_per_day", "Factory per day", "$/day", undefined, false),
      n("budgets.usd_per_hour_alert", "Spend alert", "$/h", undefined, false),
    ],
  },
  {
    title: "Implement loop",
    description: "One task's tries before it escalates.",
    fields: [
      n("loops.implement.attempts", "Attempts", "tries"),
      n("loops.implement.usd", "Budget per task", "$", undefined, false),
      n("loops.implement.wall_h", "Wall clock", "h", undefined, false),
      n("loops.implement.no_progress_s", "No progress for", "s"),
    ],
  },
  {
    title: "Review, verify, demo, CI",
    fields: [
      n("loops.review.rounds", "Review rounds", "rounds"),
      n("loops.verify.per_head", "Verify runs per head", "runs"),
      n("loops.verify.env_retries", "Env retries", "retries"),
      n("loops.demo.per_head", "Demo runs per head", "runs"),
      n("loops.demo.per_pr_heads", "Demo heads per PR", "heads"),
      n("loops.ci.flake_reruns", "CI flake reruns", "reruns"),
      n("loops.claim.mismatches", "Claim mismatches", "claims"),
    ],
  },
  {
    title: "Plan, intake, improve",
    fields: [
      n("loops.plan.stall_h", "Plan stalls after", "h", undefined, false),
      n("loops.intake.revisions", "Intake revisions", "revisions"),
      n("loops.improve.usd", "Retro budget", "$", undefined, false),
    ],
  },
];

export const ALERT_GROUPS: FieldGroup[] = [
  {
    title: "Detector thresholds",
    description: "When a detector opens an incident or a finding.",
    fields: [
      n("alerts.repeat_tool_n", "Same tool repeated", "×"),
      n("alerts.repeat_window_m", "… within", "min"),
      n("alerts.quiet_m", "Agent quiet for", "min"),
      n("alerts.ready_unreviewed_h", "Ready PR unreviewed for", "h", undefined, false),
      n("alerts.decision_waiting_m", "Decision waiting for", "min"),
      n("alerts.mail_stuck_m", "Mail stuck for", "min"),
      n("alerts.spend_window_m", "Spend window", "min"),
      n("alerts.pingpong_n", "Mail ping-pong", "×"),
      n("alerts.pingpong_window_m", "… within", "min"),
      n("alerts.orphan_h", "Orphan sub-agent after", "h", undefined, false),
      n("alerts.gate_flaps", "Gate flaps", "×"),
      n("alerts.gate_flap_window_h", "… within", "h", undefined, false),
      n("alerts.ask_unneeded_n", "Unneeded questions", "×"),
      n("alerts.ask_unneeded_s", "… answered within", "s"),
      n("alerts.heartbeat_waste_usd", "Heartbeat waste", "$/day", undefined, false),
      n("alerts.dead_ticks", "Dead after", "ticks"),
    ],
  },
  {
    title: "Decision escalation",
    description: "An assigned decision widens to operators, then owners.",
    fields: [
      n("decisions.escalate_after_m", "To operators after", "min"),
      n("decisions.escalate_owners_after_m", "To owners after", "min"),
    ],
  },
];

export const OTHER_GROUPS: FieldGroup[] = [
  {
    title: "Heartbeats",
    description: "Seconds between beats; 0 turns them off, otherwise 120–86400.",
    fields: [
      n("cadence.lead", "Leads", "s"),
      n("cadence.project_lead", "Project leads", "s"),
      n("cadence.demo_project_lead", "Project leads (demo cadence)", "s"),
    ],
  },
  {
    title: "Supervisor",
    fields: [
      n("supervisor.stuck_s", "Stuck after", "s"),
      n("supervisor.blocked_escalate_s", "Blocked escalates after", "s"),
      { path: "supervisor.quiet_beats", label: "Skip beats when nothing changed", kind: "bool" },
    ],
  },
  {
    title: "Mail and previews",
    fields: [
      n("mail.sender_rate", "Mail per sender", "/h"),
      n("mail.pair_rate", "Mail per pair", "/h"),
      n("previews.ttl_h", "Preview parks after", "h", "0 keeps previews up", false),
      n("previews.max", "Live previews", "max"),
      n("previews.env_probe_s", "Env probe every", "s"),
      n("previews.preview_probe_s", "Preview probe every", "s"),
      n("caps.max_projects", "Projects", "max"),
    ],
  },
  {
    title: "Models",
    description: "Aliases such as sonnet or opus, never model ids.",
    fields: [
      { path: "models.ic", label: "Implementers", kind: "text" },
      { path: "models.reviewer", label: "Reviewer", kind: "text" },
      { path: "models.planner", label: "Planner", kind: "text" },
    ],
  },
];

/** Every editable field by path (Propose change picks one). */
export const ALL_FIELDS: SettingField[] = [
  ...AUTONOMY_GROUPS,
  ...BUDGET_GROUPS,
  ...ALERT_GROUPS,
  ...OTHER_GROUPS,
].flatMap((g) => g.fields);

export function fieldFor(path: string): SettingField | undefined {
  return ALL_FIELDS.find((f) => f.path === path);
}

/** Roles a rule can target (`*` is everyone). */
export const RULE_ROLES = [
  "*",
  "lead",
  "planner",
  "project_lead",
  "ic",
  "ic_codex",
  "reviewer",
  "env_keeper",
  "demo",
] as const;
export const RULE_TEXT_MAX = 500;
export const RULES_PER_ROLE = 20;

/** The value at a dotted path of the doc, or undefined. */
export function getPath(doc: DefinitionDoc | null | undefined, path: string): unknown {
  let node: unknown = doc;
  for (const part of path.split(".")) {
    if (node == null || typeof node !== "object") return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  return node;
}

/** A doc value as the form's string. */
export function draftOf(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

/** A form string back to the value the server takes, or an error. */
export function parseDraft(
  field: SettingField,
  raw: string,
  bounds?: readonly [number, number],
): { value: unknown } | { error: string } {
  const text = raw.trim();
  switch (field.kind) {
    case "bool":
      return { value: text === "true" };
    case "select":
      return field.options?.some((o) => o.value === text)
        ? { value: text }
        : { error: "Pick one of the options" };
    case "text":
      if (!text) return { value: null };
      return /^[a-z][a-z0-9-]{0,31}$/.test(text)
        ? { value: text }
        : { error: "An alias like sonnet, never a model id" };
    default: {
      if (!text) return { error: "Required" };
      const value = Number(text);
      if (!Number.isFinite(value)) return { error: "Must be a number" };
      if (field.integer && !Number.isInteger(value)) return { error: "Must be a whole number" };
      if (field.path.startsWith("cadence.") && value !== 0 && (value < 120 || value > 86_400)) {
        return { error: "Must be 0 (off) or 120–86400" };
      }
      if (bounds && (value < bounds[0] || value > bounds[1])) {
        return { error: `Must be ${bounds[0]}–${bounds[1]}` };
      }
      return { value };
    }
  }
}

export interface DraftResult {
  changes: DefinitionChange[];
  errors: Record<string, string>;
}

/** The changes a draft makes against `effective`, and the fields that don't parse. */
export function draftChanges(
  fields: readonly SettingField[],
  draft: Record<string, string>,
  effective: DefinitionDoc,
  bounds: DefinitionResponse["bounds"],
): DraftResult {
  const changes: DefinitionChange[] = [];
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const raw = draft[field.path];
    if (raw === undefined) continue;
    const before = getPath(effective, field.path);
    if (raw === draftOf(before)) continue;
    const parsed = parseDraft(field, raw, bounds[field.path]);
    if ("error" in parsed) {
      errors[field.path] = parsed.error;
      continue;
    }
    if (parsed.value === before) continue;
    changes.push({ op: "replace", path: field.path, value: parsed.value });
  }
  return { changes, errors };
}

/** `"loops.implement.attempts: 3 → 4"`. */
export function changeLine(change: DefinitionChange, effective: DefinitionDoc): string {
  if (change.path === "rules") {
    const value = change.value as { role?: string; text?: string } | undefined;
    return `+ rule · ${value?.role ?? "?"} · “${value?.text ?? ""}”`;
  }
  if (change.path.startsWith("rules/") || change.path.startsWith("rules.")) {
    const id = change.path.replace(/^rules[/.]/, "");
    return change.op === "remove" ? `− ${id}` : `~ ${id}`;
  }
  return `${change.path}: ${draftOf(getPath(effective, change.path)) || "—"} → ${draftOf(change.value)}`;
}

/** Money, engine, autonomy and models changes are for owners (class B, owners). */
export function ownersOnly(path: string): boolean {
  return /^(engine|autonomy|budgets|models)\./.test(path);
}
