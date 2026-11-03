// Plain words in a node's chat, read on the client: "why", "apply c-16",
// "start the panel now", "also plan google ads", "swap luz to codex",
// "auto-merge this", "research …", "pause". Each one only opens something:
// the why card, the ask's card (its answer stays a click), or a verb's
// preview. Nothing here applies a change. Anything else is a message.

import type { DecisionRef, NodePage, NodeSummary, VerbArgs, VerbAvail } from "../nodeTypes";

export type ChatIntent =
  | { kind: "why" }
  /** Show the ask's card; answering stays a click on it. */
  | { kind: "answer"; decision: DecisionRef }
  /** Open a verb's preview on `at` (this node or a child it names). */
  | { kind: "verb"; verb: string; at: string; args?: VerbArgs };

const WHY =
  /^\/?why\b|\bwhy (is|isn'?t|not|has|hasn'?t|did|does|doesn'?t|are|aren'?t)\b|\bwhat('?s| is) (it |this )?waiting\b/;
const ANSWER = /\b(apply|approve|accept|reject|merge)\s+(?:#|!)?([a-z]{1,3}-?\d+|\d+)\b/;
const START = /\bstart\b/;
const ADD =
  /^(?:also\s+)?(?:plan|add)\s+(?:a\s+|an\s+|the\s+)?(.+?)(?:\s+(?:now|too|as well))?[.!]?$/i;
const MOVE = /^move\s+(?:this|it)\b/;
const SWAP = /\b(?:swap|switch|change)\s+@?([\w.-]+)\s+(?:to|with|for)\s+(.+?)[.!]?$/;
const AUTO = /\bauto-?merge\b|\bmerge (?:it |this )?(?:by )?itself\b/;
const RESEARCH = /\b(?:research|study|look into)\b/;
const PAUSE = /^(?:pause|hold)\b/;
const RESUME = /^(?:resume|unpause)\b/;
const NAME = /^(?:name|file)\s+(?:it|this)\b/;

/** Words that say what to do, not which node. */
const COMMON = new Set([
  "a",
  "an",
  "the",
  "it",
  "this",
  "that",
  "now",
  "please",
  "start",
  "starting",
  "today",
  "instead",
  "again",
  "and",
  "to",
  "of",
  "on",
]);

function normRef(raw: string): string {
  return raw.toUpperCase().replace(/^([A-Z]+)-?(\d+)$/, "$1-$2");
}

/** Every ask the page names: its status line's, its path's, its children's. */
export function pageDecisions(page: NodePage): DecisionRef[] {
  const out = new Map<string, DecisionRef>();
  const add = (d: DecisionRef | undefined) => {
    if (d && !out.has(d.id)) out.set(d.id, d);
  };
  add(page.status.decision);
  for (const sg of page.status.segments) if ("person" in sg) add(sg.decision);
  const kids = [...page.children.items, ...page.children.later, ...page.children.ghosts];
  for (const k of kids) {
    add(k.status_compact.decision);
    for (const sg of k.status_compact.segments) if ("person" in sg) add(sg.decision);
  }
  return [...out.values()];
}

function findDecision(page: NodePage, raw: string): DecisionRef | null {
  const ref = /^\d+$/.test(raw) ? `#${raw}` : normRef(raw);
  return (
    pageDecisions(page).find(
      (d) =>
        !d.external_url &&
        (d.id.toUpperCase() === ref ||
          d.subject?.toUpperCase() === ref ||
          new RegExp(`(^|[^A-Z0-9-])${ref.replace("#", "#?")}\\b`, "i").test(d.verb)),
    ) ?? null
  );
}

/** The child whose title or ref the text names ("the panel" → assistant-panel-ux). */
export function namedChild(text: string, kids: readonly NodeSummary[]): NodeSummary | null {
  const words = text
    .toLowerCase()
    .split(/[^a-z0-9-]+/)
    .filter((w) => w.length > 2 && !COMMON.has(w));
  if (!words.length) return null;
  let best: { kid: NodeSummary; score: number } | null = null;
  for (const kid of kids) {
    const hay = `${kid.ref} ${kid.title} ${kid.tag ?? ""}`.toLowerCase();
    const score = words.filter((w) => hay.includes(w)).length;
    if (score > 0 && (!best || score > best.score)) best = { kid, score };
  }
  return best?.kid ?? null;
}

function has(verbs: readonly VerbAvail[], verb: string): boolean {
  return verbs.some((v) => v.verb === verb && v.enabled);
}

/**
 * What plain words in the chat mean on this node, or null when they are a
 * message for the responder.
 */
export function parseIntent(text: string, page: NodePage): ChatIntent | null {
  const t = text.trim().toLowerCase().replace(/\s+/g, " ");
  if (!t) return null;
  if (WHY.test(t)) return { kind: "why" };
  const verbs = page.verbs;
  const here = page.node.ref;
  const answer = ANSWER.exec(t);
  if (answer) {
    const decision = findDecision(page, answer[2]!);
    if (decision) return { kind: "answer", decision };
  }
  if (START.test(t) && (/\bnow\b/.test(t) || /^start\b/.test(t))) {
    const kids = [...page.children.items, ...page.children.later];
    const kid = namedChild(t.replace(START, ""), kids);
    if (kid && kid.ref !== here) return { kind: "verb", verb: "start", at: kid.ref };
    if (has(verbs, "start")) return { kind: "verb", verb: "start", at: here };
  }
  if (MOVE.test(t) && has(verbs, "move")) return { kind: "verb", verb: "move", at: here };
  const swap = SWAP.exec(t);
  if (swap && has(verbs, "team")) {
    return { kind: "verb", verb: "team", at: here, args: { member: swap[1]!, to: swap[2]! } };
  }
  if (AUTO.test(t) && has(verbs, "auto")) return { kind: "verb", verb: "auto", at: here };
  const add = ADD.exec(text.trim());
  if (add && has(verbs, "add")) {
    return { kind: "verb", verb: "add", at: here, args: { title: add[1]!.trim() } };
  }
  if (RESEARCH.test(t) && has(verbs, "research")) {
    return { kind: "verb", verb: "research", at: here };
  }
  if (PAUSE.test(t) && has(verbs, "pause")) return { kind: "verb", verb: "pause", at: here };
  if (RESUME.test(t) && has(verbs, "resume")) return { kind: "verb", verb: "resume", at: here };
  if (NAME.test(t) && has(verbs, "name")) return { kind: "verb", verb: "name", at: here };
  return null;
}
