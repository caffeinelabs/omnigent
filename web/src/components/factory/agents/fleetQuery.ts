// The fleet's free-text query: `state:running tool:Bash cost>2 project:db-*`.
// Terms AND together; a `-` negates one; values may be quoted ("pnpm test")
// and use `*` globs. Bare words match the agent's title, summary and what it
// runs now. Saved views are `is:` terms. Pure; tests pin the grammar.

export type FleetField =
  | "state"
  | "role"
  | "project"
  | "harness"
  | "tool"
  | "flag"
  | "task"
  | "loop"
  | "model"
  | "is"
  | "text";

export type FleetNumber = "cost" | "rate" | "turn" | "idle" | "mail";
export type Comparator = ">" | ">=" | "<" | "<=" | "=";

export type FleetTerm =
  | { kind: "match"; field: FleetField; value: string; negate: boolean }
  | { kind: "compare"; field: FleetNumber; op: Comparator; value: number; negate: boolean };

export interface FleetQuery {
  terms: FleetTerm[];
  /** Tokens that didn't parse, in the words the search box shows. */
  errors: string[];
}

const FIELDS: Record<string, FleetField> = {
  state: "state",
  status: "state",
  role: "role",
  project: "project",
  harness: "harness",
  vendor: "harness",
  tool: "tool",
  flag: "flag",
  flags: "flag",
  task: "task",
  loop: "loop",
  model: "model",
  is: "is",
};

const NUMBERS: Record<string, FleetNumber> = {
  cost: "cost",
  $: "cost",
  usd: "cost",
  rate: "rate",
  "$/h": "rate",
  usdh: "rate",
  turn: "turn",
  turns: "turn",
  idle: "idle",
  mail: "mail",
};

/** The saved views, as the `is:` value each one sets. */
export const SAVED_VIEWS = [
  { key: "looping", label: "Looping", hint: "repeating a failing step, or a loop out of budget" },
  { key: "waiting", label: "Waiting on me", hint: "blocked on a human decision or reply" },
  { key: "expensive", label: "Expensive", hint: "over $1/h now, or over $3 so far" },
  { key: "quiet", label: "Quiet", hint: "running but silent for 10 min" },
] as const;
export type SavedView = (typeof SAVED_VIEWS)[number]["key"];

/** Split on spaces outside quotes; quotes are dropped. */
export function tokenize(input: string): string[] {
  const out: string[] = [];
  let current = "";
  let quoted = false;
  let started = false;
  for (const ch of input) {
    if (ch === '"') {
      quoted = !quoted;
      started = true;
      continue;
    }
    if (!quoted && /\s/.test(ch)) {
      if (started) out.push(current);
      current = "";
      started = false;
      continue;
    }
    current += ch;
    started = true;
  }
  if (started) out.push(current);
  return out;
}

const COMPARE_RE = /^([a-z$/]+)(>=|<=|>|<|=|:)(-?\d+(?:\.\d+)?)([mhd]?)$/i;

function minutes(value: number, unit: string): number {
  if (unit === "h") return value * 60;
  if (unit === "d") return value * 1440;
  return value;
}

/**
 * Parse the search box.
 *
 * @example parseFleetQuery("state:running tool:Bash cost>2 project:db-*")
 */
export function parseFleetQuery(input: string): FleetQuery {
  const terms: FleetTerm[] = [];
  const errors: string[] = [];
  for (const raw of tokenize(input)) {
    if (!raw) continue;
    const negate = raw.startsWith("-") && raw.length > 1;
    const token = negate ? raw.slice(1) : raw;
    const numeric = COMPARE_RE.exec(token);
    const numberField = numeric ? NUMBERS[numeric[1]!.toLowerCase()] : undefined;
    if (numeric && numberField) {
      const op = (numeric[2] === ":" ? "=" : numeric[2]) as Comparator;
      const value = Number(numeric[3]);
      const unit = numeric[4]!.toLowerCase();
      if (unit && numberField !== "idle") {
        errors.push(raw);
        continue;
      }
      terms.push({
        kind: "compare",
        field: numberField,
        op,
        value: numberField === "idle" ? minutes(value, unit) : value,
        negate,
      });
      continue;
    }
    const colon = token.indexOf(":");
    if (colon > 0) {
      const key = token.slice(0, colon).toLowerCase();
      const value = token.slice(colon + 1);
      const field = FIELDS[key];
      if (!field || !value) {
        errors.push(raw);
        continue;
      }
      if (field === "is" && !SAVED_VIEWS.some((v) => v.key === value.toLowerCase())) {
        errors.push(raw);
        continue;
      }
      terms.push({ kind: "match", field, value: value.toLowerCase(), negate });
      continue;
    }
    if (/^[<>=]/.test(token) || NUMBERS[token.toLowerCase()]) {
      errors.push(raw);
      continue;
    }
    terms.push({ kind: "match", field: "text", value: token.toLowerCase(), negate });
  }
  return { terms, errors };
}

/** `db-*` matches `db-version`; without `*`, a value matches as a substring. */
export function globMatch(pattern: string, value: string | null | undefined): boolean {
  if (value == null) return false;
  const subject = value.toLowerCase();
  if (!pattern.includes("*")) return subject.includes(pattern);
  const re = new RegExp(
    `^${pattern
      .split("*")
      .map((part) => part.replace(/[.+?^${}()|[\]\\]/g, "\\$&"))
      .join(".*")}$`,
  );
  return re.test(subject);
}

/** Exact (case-insensitive) unless the pattern has a `*`: `role:pl` is not `planner`. */
export function exactMatch(pattern: string, value: string | null | undefined): boolean {
  if (value == null) return false;
  return pattern.includes("*") ? globMatch(pattern, value) : value.toLowerCase() === pattern;
}

function compare(actual: number | null, op: Comparator, value: number): boolean {
  if (actual == null) return false;
  switch (op) {
    case ">":
      return actual > value;
    case ">=":
      return actual >= value;
    case "<":
      return actual < value;
    case "<=":
      return actual <= value;
    case "=":
      return actual === value;
  }
}

/** What the query reads of one agent; the fleet model builds it from a row. */
export interface FleetFacts {
  state: string;
  role: string | null;
  roleShort: string | null;
  project: string | null;
  harness: string | null;
  model: string | null;
  tool: string | null;
  toolArg: string | null;
  flags: readonly string[];
  task: string | null;
  loop: string | null;
  loopState: string | null;
  title: string;
  summary: string | null;
  cost: number | null;
  rate: number | null;
  turn: number | null;
  idleMin: number | null;
  mail: number;
  decisions: number;
  views: ReadonlySet<SavedView>;
}

function matchTerm(facts: FleetFacts, term: Extract<FleetTerm, { kind: "match" }>): boolean {
  const v = term.value;
  switch (term.field) {
    case "state":
      return exactMatch(v, facts.state);
    case "role":
      return exactMatch(v, facts.role) || exactMatch(v, facts.roleShort);
    case "project":
      return v === "-" || v === "none" ? facts.project == null : globMatch(v, facts.project);
    case "harness":
      return globMatch(v, facts.harness);
    case "model":
      return globMatch(v, facts.model);
    case "tool":
      return globMatch(v, facts.tool) || globMatch(v, facts.toolArg);
    case "flag":
      return facts.flags.some((flag) => globMatch(v, flag));
    case "task":
      return globMatch(v, facts.task);
    case "loop":
      return globMatch(v, facts.loop) || globMatch(v, facts.loopState);
    case "is":
      return facts.views.has(v as SavedView);
    case "text":
      return (
        globMatch(v, facts.title) ||
        globMatch(v, facts.summary) ||
        globMatch(v, facts.toolArg) ||
        globMatch(v, facts.task)
      );
  }
}

/** Whether an agent passes every term of `query`. */
export function matchesFleetQuery(facts: FleetFacts, query: FleetQuery): boolean {
  return query.terms.every((term) => {
    const hit =
      term.kind === "match"
        ? matchTerm(facts, term)
        : compare(
            term.field === "cost"
              ? facts.cost
              : term.field === "rate"
                ? facts.rate
                : term.field === "turn"
                  ? facts.turn
                  : term.field === "idle"
                    ? facts.idleMin
                    : facts.mail,
            term.op,
            term.value,
          );
    return term.negate ? !hit : hit;
  });
}

/** The query with `term` (e.g. `is:looping`) added, or removed when present. */
export function toggleQueryTerm(input: string, term: string): string {
  const tokens = tokenize(input);
  const lower = term.toLowerCase();
  const kept = tokens.filter((t) => t.toLowerCase() !== lower);
  const next = kept.length === tokens.length ? [...kept, term] : kept;
  return next.map((t) => (/\s/.test(t) ? `"${t}"` : t)).join(" ");
}

/** Whether `input` already holds `term`. */
export function queryHasTerm(input: string, term: string): boolean {
  return tokenize(input).some((t) => t.toLowerCase() === term.toLowerCase());
}

/** The value of the first `field:` term (filters' dropdowns read and write it). */
export function queryFieldValue(input: string, field: string): string | null {
  const prefix = `${field.toLowerCase()}:`;
  const token = tokenize(input).find((t) => t.toLowerCase().startsWith(prefix));
  return token ? token.slice(prefix.length) : null;
}

/** `input` with its `field:` term set to `value` (or removed when null). */
export function setQueryField(input: string, field: string, value: string | null): string {
  const prefix = `${field.toLowerCase()}:`;
  const kept = tokenize(input).filter((t) => !t.toLowerCase().startsWith(prefix));
  if (value) kept.push(`${field}:${value}`);
  return kept.map((t) => (/\s/.test(t) ? `"${t}"` : t)).join(" ");
}
