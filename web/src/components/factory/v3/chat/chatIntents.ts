// Plain words in a node's chat, read on the client: "why", "apply c-16",
// "start the panel now", "also plan google ads", "swap luz to codex",
// "auto-merge this", "research …", "pause", "is this done?". The words are always posted
// first (a bare "why" stays here); then a short, command-shaped message also
// opens something beside them: the why card, the ask's card (its answer
// stays a click), or a verb's preview. Nothing here applies a change, and a
// sentence ("please don't auto-merge this, …") is only a message.

import type { DecisionRef, NodePage, NodeSummary, VerbArgs, VerbAvail } from "../nodeTypes";

export type ChatIntent =
  /** `bare`: the words were only "why" (they stay here; nothing is posted). */
  | { kind: "why"; bare: boolean }
  /** Show the ask's card; answering stays a click on it. */
  | { kind: "answer"; decision: DecisionRef }
  /** Open a verb's preview on `at` (this node or a child it names). */
  | { kind: "verb"; verb: string; at: string; args?: VerbArgs };

/** Only "why" ("Why?", "/why"): the why card, nothing posted. */
const BARE_WHY = /^\/?why[?!.]*$/;
/** "why is it not moving", "why hasn't it started", "what is it waiting on": the whole message. */
const WHY =
  /^(?:why (?:is|isn'?t|has|hasn'?t|did|does|doesn'?t|are|aren'?t) (?:it|this|that)(?: not)? (?:moving|started|starting|running|stuck|waiting|blocked|done|finished|merged|going)|what(?:'?s| is) (?:it|this) waiting (?:on|for))[?!.]*$/;
/** "is this done?", "is it really done", "are we done yet": a feature's done review. */
const DONE_ASK =
  /^(?:is (?:it|this|that)(?: (?:really|truly|actually))? (?:done|finished)|are we (?:done|finished))(?: yet)?[?!.]*$/;
/** A command is short: at most this many words. */
const COMMAND_WORDS = 6;
/** Words that turn a verb around ("don't auto-merge this"): never a command. */
const NEGATION = /\b(?:don'?t|do not|not|never|no|won'?t|can'?t|cannot|shouldn'?t|stop|without)\b/;
const ANSWER = /^(apply|approve|accept|reject|merge) (?:#|!)?([a-z]{1,3}-?\d+|\d+)$/;
/** Fillers a short command may carry ("start it now please"). */
const FILL = "(?: (?:it|this|that|now|please|today|again|for now))*";
const START = /^(?:please )?start\b/;
const ADD = /^(?:also )?(?:plan|add) (?:a |an |the )?(.+?)(?: (?:now|too|as well))?$/i;
const MOVE = /^move (?:this|it)\b/;
const SWAP = /^(?:swap|switch|change) @?([\w.-]+) (?:to|with|for) ([\w.-]+)$/;
const AUTO = new RegExp(`^(?:please )?(?:auto-?merge|merge (?:it |this )?(?:by )?itself)${FILL}$`);
const RESEARCH = /^(?:research|study|look into)\b/;
const PAUSE = new RegExp(`^(?:please )?(?:pause|hold)${FILL}$`);
const RESUME = new RegExp(`^(?:please )?(?:resume|unpause)${FILL}$`);
const NAME = /^(?:name|file) (?:it|this)\b/;
/** What a member can be swapped to: a coding-agent vendor or a model family. */
const KNOWN_MODEL =
  /^(?:claude|codex|opus|sonnet|haiku|gpt[\w.-]*|o[1-9][\w.-]*|gemini[\w.-]*|cursor|copilot|antigravity|pi)$/;

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

/** Only "why" ("Why?", "/why"): it stays in this viewer's chat, nothing is posted. */
export function isBareWhy(text: string): boolean {
  return BARE_WHY.test(text.trim().toLowerCase());
}

/**
 * What plain words in the chat open on this node, or null when they are only
 * a message for the responder. Only short, command-shaped words open
 * anything: from their first word, at most six words, no question (but
 * "why"), no negation.
 */
export function parseIntent(text: string, page: NodePage): ChatIntent | null {
  const t = text
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[.!]+$/, "");
  if (!t) return null;
  if (BARE_WHY.test(t)) return { kind: "why", bare: true };
  if (WHY.test(t)) return { kind: "why", bare: false };
  const verbs = page.verbs;
  const here = page.node.ref;
  if (DONE_ASK.test(t))
    return has(verbs, "review") ? { kind: "verb", verb: "review", at: here } : null;
  if (t.includes("?") || NEGATION.test(t) || t.split(" ").length > COMMAND_WORDS) return null;
  const answer = t.match(ANSWER);
  if (answer) {
    const decision = findDecision(page, answer[2]!);
    return decision ? { kind: "answer", decision } : null;
  }
  if (START.test(t)) {
    const kids = [...page.children.items, ...page.children.later];
    const kid = namedChild(t.replace(START, ""), kids);
    if (kid && kid.ref !== here) return { kind: "verb", verb: "start", at: kid.ref };
    if (has(verbs, "start")) return { kind: "verb", verb: "start", at: here };
    return null;
  }
  if (MOVE.test(t) && has(verbs, "move")) return { kind: "verb", verb: "move", at: here };
  const swap = t.match(SWAP);
  if (swap && KNOWN_MODEL.test(swap[2]!) && has(verbs, "team")) {
    return { kind: "verb", verb: "team", at: here, args: { member: swap[1]!, to: swap[2]! } };
  }
  if (AUTO.test(t) && has(verbs, "auto")) return { kind: "verb", verb: "auto", at: here };
  const add = text
    .trim()
    .replace(/\s+/g, " ")
    .replace(/[.!]+$/, "")
    .match(ADD);
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
