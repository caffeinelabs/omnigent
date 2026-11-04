// The calm rules ("Calm rules" in the v3 spec) as pure functions: short names,
// one status phrase per child, and the viewer's "Your move" (the server's when
// it sends one, else derived from the status line and Needs you).

import type {
  Crumb,
  DecisionRef,
  NeedsYouItem,
  NeedsYouList,
  NodePage,
  NodeSummary,
  QuietAsk,
  StatusLine,
  YourMove,
  YourMoveItem,
} from "../nodeTypes";

/** A title is at most this many words. */
export const TITLE_WORDS_MAX = 6;

/** Your move shows at most this many rows; the rest sit behind "+n more". */
export const MOVE_ROWS = 3;

/** The words of a text: tokens with a letter or a digit ("·" and "›" are not words). */
export function words(text: string): string[] {
  return text
    .trim()
    .split(/\s+/)
    .filter((w) => /[\p{L}\p{N}]/u.test(w));
}

function cut(text: string, n: number): string {
  const kept = text.trim().split(/\s+/).slice(0, Math.max(1, n)).join(" ");
  return `${kept.replace(/[\s.,;:!?/–—-]+$/, "")}…`;
}

/** A machine name: lowercase words joined by hyphens. */
const SLUG = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$/;

function capital(text: string): string {
  return text ? `${text[0]!.toUpperCase()}${text.slice(1)}` : text;
}

/**
 * A short name for a long or machine title, used until the server names the
 * node: "From C-11: create a · create b" reads "Create a +1", a slug reads as
 * words, a long ask keeps its first clause, else its first words. Any other
 * title of six words or fewer stays.
 */
export function shortTitle(title: string, max: number = TITLE_WORDS_MAX): string {
  const clean = title.trim().replace(/\s+/g, " ");
  // A slug ("assistant-core") reads as words ("Assistant core").
  if (SLUG.test(clean) && clean.split("-").length <= max) return capital(clean.replace(/-/g, " "));
  if (words(clean).length <= max && !/^From [A-Z]{1,3}-\d+:/.test(clean)) return clean;
  let text = clean.replace(/^From [A-Z]{1,3}-\d+:\s*/, "").replace(/^@\S+\s+/, "");
  const parts = text
    .split(" · ")
    .map((p) => p.trim())
    .filter(Boolean);
  const more = parts.length > 1 ? parts.length - 1 : 0;
  if (more) text = parts[0]!;
  const room = more ? max - 1 : max;
  if (words(text).length > room) {
    const clause = text.split(/(?<=[.!?;:,])\s+|\s+[–—/-]\s+/)[0]!.replace(/[.!?;:,]+$/, "");
    const n = words(clause).length;
    text = n >= 2 && n <= room ? clause : cut(text, room - 1);
  }
  text = capital(text || clean);
  return more ? `${text} +${more}` : text;
}

/** What a node is called on every page: its short name, else its title shortened. */
export function nodeName(node: { title: string; short_title?: string | null }): string {
  const given = node.short_title?.trim();
  return given || shortTitle(node.title);
}

/** One status phrase for a child row (calm rule 3), or none while it moves or is done. */
export interface Phrase {
  text: string;
  /** `you`: a person (the viewer) must act; amber. */
  tone?: "you";
}

function firstHop(line: StatusLine): string | null {
  for (const sg of line.segments) {
    if ("person" in sg) return null;
    if (sg.ref) return nodeName({ title: sg.title ?? sg.ref });
  }
  return null;
}

/** "Waits on assistant-core", "Held for C-16", "Needs you", or null (moving, done, nothing to say). */
export function childPhrase(node: NodeSummary): Phrase | null {
  const line = node.status_compact;
  switch (node.state) {
    case "moving":
    case "done":
      return null;
    case "needs":
      return { text: "Needs you", tone: "you" };
    case "paused":
      return { text: "Paused" };
    case "ghost":
      return { text: "Proposed" };
    case "offered":
      return { text: "Offered" };
    case "later":
      return { text: node.held_for ? shortTitle(`Held for ${node.held_for}`, 6) : "Later" };
    default:
      break;
  }
  if (line.kind === "moving" || line.kind === "done" || line.kind === "idle") return null;
  if (line.kind === "needs") return { text: "Needs you", tone: "you" };
  const label = line.label.replace(/:\s*$/, "").trim();
  const hop = firstHop(line);
  if (hop && /^(waits on|held for)$/i.test(label)) return { text: `${label} ${hop}` };
  if (/^(waiting|not moving|top blocker)$/i.test(label) && hop) return { text: `Waits on ${hop}` };
  if (!label) return node.state === "broken" ? { text: "Broken" } : null;
  return { text: shortTitle(label, 4) };
}

// ── Your move ──────────────────────────────────────────────────

/** A row of Your move as the page draws it. */
export interface MoveRow {
  id: string;
  decision: DecisionRef;
  /** "Apply C-16". */
  text: string;
  /** "Agent assistant", or null when it blocks nothing. */
  unblocks: string | null;
  /** The node the ask is on (the row's words link there). */
  at?: string;
}

/** The rows shown and the rest (behind "+n more"; `more` may exceed `rest` when the server only counts them). */
export interface MoveView {
  rows: MoveRow[];
  rest: MoveRow[];
  more: number;
}

/** Every id an ask goes by: its ref, its row id, its subject ("C-16"). */
export function askKeys(decision: DecisionRef): string[] {
  return [decision.id, decision.row_id, decision.subject].filter((k): k is string => Boolean(k));
}

/** The keys of every ask Your move holds: no other place on the page repeats them. */
export function movedAsks(view: MoveView): Set<string> {
  return new Set([...view.rows, ...view.rest].flatMap((r) => askKeys(r.decision)));
}

function rowOf(decision: DecisionRef, unblocks: string | null, at?: string): MoveRow {
  return { id: decision.id, decision, text: decision.verb, unblocks, at };
}

/**
 * What answering it unblocks, by name: the node at the level this page lists
 * (folded crumbs give way to what they fold into), else a child of this page
 * it holds, else the node it is on, else this page's node.
 */
function unblocksOf(
  crumbs: readonly Crumb[],
  holds: readonly string[],
  shown: string | null,
  page: NodePage,
): string {
  const below = crumbs.slice(crumbs.findIndex((c) => c.ref === page.node.ref) + 1);
  const at = shown ? below.findIndex((c) => c.level === shown) : -1;
  let pick: Crumb | undefined = at >= 0 ? below[at] : undefined;
  if (pick?.folded) pick = below.slice(at + 1).find((c) => !c.folded) ?? pick;
  if (pick) return nodeName(pick);
  const kids = [...page.children.items, ...page.children.later];
  const held = kids.find((k) => holds.includes(k.ref) || (k.tag && holds.includes(k.tag)));
  if (held) return nodeName(held);
  const last = below.filter((c) => !c.folded).at(-1);
  return nodeName(last ?? page.node);
}

function inSubtree(item: { breadcrumb?: Crumb[] }, page: NodePage): boolean {
  if (!page.breadcrumb.length) return true;
  return Boolean(item.breadcrumb?.some((c) => c.ref === page.node.ref));
}

/**
 * Your move derived from what an older server sends: the ask this node's own
 * status line ends at first, then the subtree's asks as Needs you ranks them
 * (by what each holds), then asks that block nothing.
 */
export function deriveMoves(
  page: NodePage,
  needs: NeedsYouList | null | undefined,
  shown: string | null,
): MoveRow[] {
  const out: MoveRow[] = [];
  const seen = new Set<string>();
  const add = (row: MoveRow) => {
    if (askKeys(row.decision).some((k) => seen.has(k))) return;
    askKeys(row.decision).forEach((k) => seen.add(k));
    out.push(row);
  };
  const status = page.status;
  const own = status.ends === "person" ? status.decision : undefined;
  const ranked: NeedsYouItem[] = (needs?.items ?? []).filter((i) => inSubtree(i, page));
  if (own && !own.blocks_nothing) {
    const listed = ranked.find((i) => i.decision.id === own.id);
    // Held by an ask outside its subtree (a dependency's): answering it unblocks this node.
    const unblocks = listed
      ? unblocksOf(listed.breadcrumb, listed.holds, shown, page)
      : nodeName(page.node);
    add(rowOf(listed?.decision ?? own, unblocks, lastRef(listed?.breadcrumb)));
  }
  for (const item of ranked) {
    add(
      rowOf(
        item.decision,
        unblocksOf(item.breadcrumb, item.holds, shown, page),
        lastRef(item.breadcrumb),
      ),
    );
  }
  const quiet: QuietAsk[] = (needs?.blocks_nothing ?? []).filter((q) => inSubtree(q, page));
  for (const ask of quiet) add(rowOf(ask, null, ask.node?.ref));
  if (own?.blocks_nothing) add(rowOf(own, null));
  return out;
}

function lastRef(crumbs: readonly Crumb[] | undefined): string | undefined {
  return crumbs?.[crumbs.length - 1]?.ref;
}

function itemRow(item: YourMoveItem): MoveRow {
  const text = item.object ? `${item.verb} ${item.object}` : item.verb;
  return {
    id: item.id,
    decision: item.decision,
    text,
    unblocks: item.unblocks ?? null,
    at: item.ref,
  };
}

/** The page's Your move: the server's rows when it sends them, else the derived ones. */
export function moveView(page: NodePage, derived: MoveRow[], max: number = MOVE_ROWS): MoveView {
  const given: YourMove | undefined = page.your_move;
  if (given) {
    const rows = given.items.slice(0, max).map(itemRow);
    const keys = new Set(rows.flatMap((r) => askKeys(r.decision)));
    const rest = [
      ...given.items.slice(max).map(itemRow),
      ...derived.filter((r) => !askKeys(r.decision).some((k) => keys.has(k))),
    ];
    const more = Math.max(0, given.more) + Math.max(0, given.items.length - max);
    return { rows, rest: rest.slice(0, more), more };
  }
  return {
    rows: derived.slice(0, max),
    rest: derived.slice(max),
    more: Math.max(0, derived.length - max),
  };
}
