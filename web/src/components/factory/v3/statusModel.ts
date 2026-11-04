// How a status line reads, as parts the component draws (and tests read as
// text). Full lines are the header's: the whole path with separators. Compact
// lines are a tile's or a row's: only the end of the path, and "the ask above"
// in place of a decision the page's header already asks.

import type { DecisionRef, StatusLine } from "./nodeTypes";

export type StatusPart = { key: number } & (
  | { t: "text"; text: string }
  | { t: "muted"; text: string }
  | { t: "sep"; text: string }
  | { t: "link"; ref: string; text: string; reason?: string }
  | { t: "person"; text: string }
);

type Part = StatusPart extends infer P ? (P extends unknown ? Omit<P, "key"> : never) : never;

/** "needs you: Apply C-16 (D-45)"; a group of merges is named by its verb only. */
export function personText(d: DecisionRef): string {
  const id = d.grouped || d.verb.includes(d.id) ? "" : ` (${d.id})`;
  return `needs you: ${d.verb}${id}`;
}

/** Labels that already say a person must act, so the person reads as just the verb. */
function needsStyle(label: string): boolean {
  return /^(needs you|waiting)\b/i.test(label);
}

export interface StatusOptions {
  compact?: boolean;
  /** The node the line belongs to (a moving line names its worker only when it is another node). */
  nodeRef?: string;
  /** The asks the page's Your move holds (their keys): a line ending there points up instead of repeating it. */
  aboveId?: Above;
}

/** The asks a page already shows: one id, or every key of its Your move. */
export type Above = ReadonlySet<string> | string | null | undefined;

function isAbove(decision: DecisionRef | undefined, above: Above): boolean {
  if (!decision) return false;
  if (decision.in_your_move) return true;
  if (!above) return false;
  if (typeof above === "string") return decision.id === above;
  return [decision.id, decision.row_id, decision.subject].some((k) => k && above.has(k));
}

/** True when this line ends at an ask the page already shows. */
export function endsAbove(line: StatusLine, aboveId: Above): boolean {
  return isAbove(line.decision, aboveId);
}

export function statusParts(line: StatusLine, options: StatusOptions = {}): StatusPart[] {
  const parts: Part[] = [];
  const moving = line.moving;
  if (moving) {
    if (moving.ref !== options.nodeRef) {
      parts.push({ t: "link", ref: moving.ref, text: moving.title ?? moving.ref });
      parts.push({ t: "text", text: ` · ${moving.activity}` });
    } else {
      parts.push({ t: "text", text: moving.activity });
    }
  }
  if (options.compact) compactPath(line, options.aboveId ?? null, parts);
  else fullPath(line, parts, options.aboveId ?? null);
  // An ask's own words stay on its Your move row.
  if (line.detail && !(line.kind === "needs" && isAbove(line.decision, options.aboveId))) {
    const lead = line.segments.length || moving ? " · " : "";
    parts.push({ t: line.kind === "needs" ? "muted" : "text", text: `${lead}${line.detail}` });
  }
  return parts.map((part, i) => ({ ...part, key: i }) as StatusPart);
}

function fullPath(line: StatusLine, parts: Part[], above: Above) {
  line.segments.forEach((sg, i) => {
    if (i > 0) parts.push({ t: "sep", text: sg.sep });
    if ("person" in sg && isAbove(sg.decision, above)) {
      parts.push({ t: "muted", text: "your move, above" });
      return;
    }
    if ("person" in sg) {
      // "Needs you: Merge #9856 and #9857", not "Needs you: needs you: …".
      const alone = i === 0 && needsStyle(line.label);
      parts.push({ t: "person", text: alone ? sg.decision.verb : personText(sg.decision) });
      return;
    }
    const ref = "ref" in sg ? sg.ref : undefined;
    const label = "label" in sg ? sg.label : "";
    if (label) parts.push({ t: "text", text: ref ? `${label} ` : label });
    if (ref) {
      parts.push({
        t: "link",
        ref,
        text: sg.title ?? ref,
        ...("reason" in sg && sg.reason ? { reason: sg.reason } : {}),
      });
    }
  });
  if (line.also_holds?.length) {
    parts.push({ t: "muted", text: " · also holds " });
    line.also_holds.forEach((h, i) => {
      if (i > 0) parts.push({ t: "text", text: ", " });
      parts.push({ t: "link", ref: h.ref, text: h.title });
    });
  }
}

function compactPath(line: StatusLine, aboveId: Above, parts: Part[]) {
  const above = endsAbove(line, aboveId);
  const verbOnly = needsStyle(line.label);
  const via: string[] = [];
  let person: DecisionRef | null = null;
  let first = true;
  for (const sg of line.segments) {
    if ("person" in sg) {
      person = sg.decision;
      continue;
    }
    if (sg.sep === "›") {
      via.push(sg.title ?? sg.ref);
      continue;
    }
    if (!first) parts.push({ t: "text", text: " · " });
    first = false;
    if (sg.ref) {
      if (sg.label) parts.push({ t: "text", text: `${sg.label} ` });
      parts.push({ t: "link", ref: sg.ref, text: sg.title ?? sg.ref });
    } else {
      parts.push({ t: "text", text: sg.label });
    }
  }
  if (person) {
    if (!first) parts.push({ t: "text", text: " · " });
    if (verbOnly) parts.push({ t: "text", text: person.verb });
    else if (above) parts.push({ t: "muted", text: "the ask above" });
    else parts.push({ t: "person", text: personText(person) });
  }
  if (via.length) parts.push({ t: "muted", text: ` · via ${via.join(" › ")}` });
  if (above && verbOnly) parts.push({ t: "muted", text: " · the ask above" });
}

/** The line as one string ("Not moving: T-16 → held for C-16 → needs you: Apply C-16 (D-45)"). */
export function statusText(line: StatusLine, options: StatusOptions = {}): string {
  const body = statusParts(line, options)
    .map((p) => (p.t === "sep" ? ` ${p.text} ` : p.text))
    .join("");
  return `${line.label} ${body}`.replace(/\s+/g, " ").trim();
}

/** Whether the header offers "why? ▾": the explanation says more than the line. */
export function hasWhy(line: StatusLine): boolean {
  return Boolean(line.why && (line.why.length > 2 || line.note));
}
