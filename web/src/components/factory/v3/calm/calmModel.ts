// The calm rules ("Calm rules" in the v3 spec) as pure functions: short names,
// one status phrase per child, and the viewer's "Your move" (the server's when
// it sends one, else derived from the status line and Needs you).

import { levelName } from "../nodeWire";
import type {
  Crumb,
  DecisionAction,
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

/** Words a cut name never ends on: a dangling article, preposition or conjunction. */
const DANGLING = new Set(
  "a an the of to for in on at by with and or as from into same this that".split(" "),
);
/** A unit after a number ("300 s"): a cut name drops both. */
const UNIT = /^(s|ms|sec|secs|min|mins|h|hrs?|%|gb|mb|kb)$/i;

function cut(text: string, n: number): string {
  const kept = text.trim().split(/\s+/).slice(0, Math.max(1, n));
  const bare = (w: string) => w.replace(/[\s.,;:!?/–—-]+$/, "").toLowerCase();
  while (kept.length > 1) {
    const last = bare(kept.at(-1)!);
    if (UNIT.test(last) && kept.length > 2 && /^\d/.test(bare(kept.at(-2)!))) {
      kept.splice(-2, 2);
    } else if (DANGLING.has(last)) {
      kept.pop();
    } else {
      break;
    }
  }
  return `${kept.join(" ").replace(/[\s.,;:!?/–—-]+$/, "")}…`;
}

/** A machine name: lowercase words joined by hyphens. */
const SLUG = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$/;

/**
 * A machine name inside a title ("build-tier-drafts-3, part 1"): three or more
 * words joined by hyphens, or hyphenated words ending in a number. Two plain
 * words ("live-search") may be English, so the gate does not count them.
 */
const SLUG_STRICT =
  /(?<![\w-])[a-z][a-z0-9]*(?:(?:-[a-z0-9]+){2,}|(?:-[a-z][a-z0-9]*)*-\d+)(?![\w-])/;

/** Any lowercase hyphenated name ("assistant-core", "live-search"): read as words in a title. */
const SLUG_IN = /(?<![\w-])[a-z][a-z0-9]+(?:-(?:[a-z0-9]{2,}|\d+))+(?![\w-])/g;

/** A title that holds a machine name (a slug), which is never shown as a title. */
export function holdsSlug(text: string): boolean {
  return SLUG_STRICT.test(text);
}

function capital(text: string): string {
  return text ? `${text[0]!.toUpperCase()}${text.slice(1)}` : text;
}

/** Slugs inside a text read as words ("build-tier-drafts-3, part 1" → "build tier drafts 3, part 1"). */
export function slugWords(text: string): string {
  return text.replace(SLUG_IN, (slug) => slug.replace(/-/g, " "));
}

/**
 * A short name for a long or machine title, used until the server names the
 * node: "From C-11: create a · create b" reads "Create a +1", a slug reads as
 * words (also inside a title), a long ask keeps its first clause, else its
 * first words (never ending on a dangling "the" or "300 s"). Any other title
 * of six words or fewer stays.
 */
export function shortTitle(title: string, max: number = TITLE_WORDS_MAX): string {
  const raw = title.trim().replace(/\s+/g, " ");
  // A slug ("assistant-core") reads as words ("Assistant core").
  if (SLUG.test(raw) && raw.split("-").length <= max) return capital(raw.replace(/-/g, " "));
  const clean = slugWords(raw);
  const machine = clean !== raw;
  if (words(clean).length <= max && !/^From [A-Z]{1,3}-\d+:/.test(clean)) {
    return machine ? capital(clean) : clean;
  }
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

/** What a node is called on every page: the name the server gave it, else its title shortened. */
export function nodeName(node: { title: string; named?: string | null }): string {
  if (node.named === "person" || node.named === "factory") return node.title;
  return shortTitle(node.title);
}

/** The full text a node's name shortens, when it does (Details and tooltips show it). */
export function fullTitle(node: {
  title: string;
  full_title?: string | null;
  named?: string | null;
}): string | null {
  const full = node.full_title ?? node.title;
  return full !== nodeName(node) ? full : null;
}

/**
 * A child's name under its parent: a name that starts with the parent's
 * ("Build tier drafts 3, part 1" under "Build tier drafts 3") keeps only what
 * tells it apart ("Part 1").
 */
export function nameUnder(
  node: { title: string; named?: string | null },
  parent: string | null | undefined,
): string {
  const name = nodeName(node);
  const head = parent?.trim().toLowerCase();
  if (!head || !name.toLowerCase().startsWith(head)) return name;
  const rest = name.slice(head.length);
  if (!/^[\s,:·–—-]/.test(rest)) return name;
  const tail = rest.replace(/^[\s,:·–—-]+/, "").trim();
  return words(tail).length ? capital(tail) : name;
}

/** A 32-hex machine id (a factory's, a session's) is never a name. */
const HEX_ID = /^[0-9a-f]{32}$/i;

/** Who asked, in words: a factory id (the factory asked by itself) reads "the factory". */
export function askerName(asker: string | null | undefined, factoryId?: string): string | null {
  const who = asker?.trim();
  if (!who) return null;
  if (who === factoryId || HEX_ID.test(who) || /^[0-9a-f-]{36}$/i.test(who)) return "the factory";
  return who;
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
  /** What the ask is about in a few words ("update assistant core"), when its text names only a code. */
  name?: string | null;
  /** "Agent assistant", "this task"; null when it names nothing it frees (it may still hold work). */
  unblocks: string | null;
  /** It blocks nothing (a grey dot, "blocks nothing"): only when the server or Needs you says so. */
  quiet: boolean;
  /** The node the ask is on (the row's words link there). */
  at?: string;
  /** The row's button: an answer, a link out, or none (the row opens the ask). */
  primary: { label: string; action?: DecisionAction; url?: string } | null;
}

/** The first answer of an ask as the row's button (a merge's is a link out). */
function primaryOf(decision: DecisionRef): MoveRow["primary"] {
  const first = decision.actions?.[0];
  if (decision.external_url) {
    return { label: first?.label ?? decision.verb, url: decision.external_url };
  }
  return first ? { label: first.label, action: first } : null;
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

const CODE = /^(?:[A-Z]{1,3}-\d+|#\d+)$/;

/**
 * What an ask is about in a few words (five at most), from its title, when the row's
 * text names only a code ("Apply C-16"): "human proposes C-16: update
 * assistant-core (constraints, dod)" → "update assistant core". None when the
 * title repeats the row (a merge names its PRs) or says nothing more.
 */
export function askName(decision: DecisionRef, text: string, max = 5): string | null {
  const tail = text.split(/\s+/).slice(1);
  if (!tail.length || !tail.every((w) => CODE.test(w))) return null;
  let t = (decision.title ?? "").trim();
  t = t
    .replace(/^.*?\b(?:propos\w*|asks?|wants?)\s+(?:[A-Z]{1,3}-\d+|#\d+)\s*:\s*/i, "")
    .replace(/^(?:[A-Z]{1,3}-\d+|#\d+)\s*[:·–—-]\s*/, "")
    .replace(/\s*\([^)]*\)/g, "")
    .replace(/[?.!:]+$/, "")
    .trim();
  if (!t || tail.some((code) => t.includes(code))) return null;
  if (t.toLowerCase() === text.toLowerCase()) return null;
  const name = shortTitle(t, max);
  // A continuation reads in lower case ("Apply C-16 · update assistant core"), an acronym stays.
  return /^[A-Z][a-z]/.test(name) ? `${name[0]!.toLowerCase()}${name.slice(1)}` : name;
}

/** At most this many nodes are named after "unblocks"; the rest are "+n". */
const NAMED_MAX = 2;

/** "a", "a and b", "a, b +2" (`extra`: nodes past the names, unnamed ones too). */
function joinNames(names: readonly string[], extra = 0): string | null {
  if (!names.length) return null;
  const past = Math.max(0, names.length - NAMED_MAX) + extra;
  const shown = names.slice(0, NAMED_MAX);
  const head = past ? shown.join(", ") : shown.join(" and ");
  return past ? `${head} +${past}` : head;
}

/** "this task": the page's own node, by its level. */
function thisNode(page: NodePage): string {
  const node = page.node;
  return `this ${levelName(node.level, node.level_name).toLowerCase()}`;
}

/**
 * The names a page knows its nodes by (each the short name its rows show):
 * its ancestors, its children and the nodes Needs you's breadcrumbs pass.
 */
export function knownNames(page: NodePage, needs?: NeedsYouList | null): Map<string, string> {
  const out = new Map<string, string>();
  const put = (n: { ref: string; title: string; named?: string | null; tag?: string }) => {
    const name = nodeName(n);
    // A title that is only a code ("T-16") is no name.
    if (CODE.test(name)) return;
    if (!out.has(n.ref)) out.set(n.ref, name);
    if (n.tag && !out.has(n.tag)) out.set(n.tag, name);
  };
  // Children first, by the names their rows show (a part of this page reads "Part 1").
  const kids = page.children;
  const here = page.node ? nodeName(page.node) : null;
  for (const k of [...(kids?.items ?? []), ...(kids?.later ?? []), ...(kids?.ghosts ?? [])]) {
    const name = nameUnder(k, here);
    if (!out.has(k.ref)) out.set(k.ref, name);
    if (k.tag && !out.has(k.tag)) out.set(k.tag, name);
  }
  for (const c of page.breadcrumb ?? []) put(c);
  // The nodes the status lines pass ("waits on Assistant core › Debit each turn").
  const lines = [
    page.status,
    ...[...(kids?.items ?? []), ...(kids?.later ?? [])].map((k) => k.status_compact),
  ];
  for (const line of lines) {
    for (const sg of line?.segments ?? []) {
      if ("ref" in sg && sg.ref && sg.title) put({ ref: sg.ref, title: sg.title });
    }
    for (const link of line?.also_holds ?? []) put(link);
  }
  for (const item of needs?.items ?? []) for (const c of item.breadcrumb ?? []) put(c);
  for (const q of needs?.blocks_nothing ?? []) for (const c of q.breadcrumb ?? []) put(c);
  return out;
}

/**
 * The nodes an ask frees, by the names the page shows them by: the page's own
 * node is "this task"; a node the page does not list reads its slug as words;
 * one it knows only by id ("T-16") is counted ("+1"), never shown. `complete`:
 * every node has a name. Null when it names none of them.
 */
export function unblocksNames(
  refs: readonly string[],
  page: NodePage,
  names: Map<string, string>,
): { text: string; complete: boolean } | null {
  const own = page.node ? [page.node.ref, page.node.tag].filter(Boolean) : [];
  const said: string[] = [];
  let unnamed = 0;
  for (const ref of refs) {
    const name = own.includes(ref)
      ? thisNode(page)
      : (names.get(ref) ?? (SLUG.test(ref) ? shortTitle(ref) : null));
    if (!name) unnamed += 1;
    else if (!said.includes(name)) said.push(name);
  }
  // The page's own node goes first ("this task and Assistant panel ux").
  said.sort((a, b) => Number(b.startsWith("this ")) - Number(a.startsWith("this ")));
  const text = joinNames(said, unnamed);
  return text ? { text, complete: unnamed === 0 } : null;
}

/** A bare id ("T-16", "PR-9856", "#9856"): never what "unblocks" says. */
const BARE_REF = /(?:^|[\s,(])(?:[A-Z]{1,3}-\d+|#\d+)(?=$|[\s,).])/;

function rowOf(
  decision: DecisionRef,
  unblocks: string | null,
  at: string | undefined,
  quiet: boolean,
): MoveRow {
  const primary = primaryOf(decision);
  const text = decision.verb;
  return {
    id: decision.id,
    decision,
    text,
    name: askName(decision, text),
    unblocks,
    quiet,
    at,
    primary,
  };
}

/**
 * What answering it unblocks, by name: the node at the level this page lists
 * (folded crumbs give way to what they fold into), else a child of this page
 * it holds, else the node it is on, else this page's node ("this project").
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
  const held = kids.filter((k) => holds.includes(k.ref) || (k.tag && holds.includes(k.tag)));
  // Every child it holds is this page's whole list: it frees this node.
  if (held.length > 1 && held.length === kids.length) return thisNode(page);
  if (held.length) return joinNames(held.map((k) => nameUnder(k, nodeName(page.node))))!;
  const last = below.filter((c) => !c.folded).at(-1);
  return last ? nodeName(last) : thisNode(page);
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
  /** The ask this node's status line ends at goes first (false: Needs you's order only). */
  ownFirst = true,
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
  if (own && !own.blocks_nothing && ownFirst) {
    const listed = ranked.find((i) => i.decision.id === own.id);
    // Held by an ask outside its subtree (a dependency's): answering it unblocks this node.
    const unblocks = listed
      ? unblocksOf(listed.breadcrumb, listed.holds, shown, page)
      : page.breadcrumb.length
        ? thisNode(page)
        : null;
    add(rowOf(listed?.decision ?? own, unblocks, lastRef(listed?.breadcrumb), false));
  }
  for (const item of ranked) {
    add(
      rowOf(
        item.decision,
        unblocksOf(item.breadcrumb, item.holds, shown, page),
        lastRef(item.breadcrumb),
        false,
      ),
    );
  }
  const quiet: QuietAsk[] = (needs?.blocks_nothing ?? []).filter((q) => inSubtree(q, page));
  for (const ask of quiet) add(rowOf(ask, null, ask.node?.ref, true));
  if (own?.blocks_nothing) add(rowOf(own, null, undefined, true));
  return out;
}

function lastRef(crumbs: readonly Crumb[] | undefined): string | undefined {
  return crumbs?.[crumbs.length - 1]?.ref;
}

function itemRow(item: YourMoveItem, page: NodePage, names: Map<string, string>): MoveRow {
  const object = item.object && item.object !== item.id ? item.object : null;
  // A question reads by its topic, not its number ("Answer: live search follow-up").
  const text = item.about
    ? `${item.verb}: ${item.about}`
    : object
      ? `${item.verb} ${object}`
      : item.decision.verb || item.verb;
  const p = item.primary;
  const action = p?.option ? item.decision.actions.find((a) => a.id === p.option) : undefined;
  const primary: MoveRow["primary"] = p?.url
    ? { label: p.label, url: p.url }
    : p?.option
      ? { label: p.label, action: action ?? { id: p.option, label: p.label } }
      : p
        ? null
        : primaryOf(item.decision);
  const quiet = item.blocks_nothing === true || item.decision.blocks_nothing === true;
  const refs = item.unblocks_refs ?? (item.unblocks_ref ? [item.unblocks_ref] : []);
  // Named as the page names them (the server's words are the fallback); a slug never shows.
  const named = !quiet && refs.length ? unblocksNames(refs, page, names) : null;
  // The server's words when they hold no id and no slug (a newer server names the nodes).
  const given =
    item.unblocks && !BARE_REF.test(item.unblocks) && slugWords(item.unblocks) === item.unblocks
      ? item.unblocks
      : null;
  const unblocks = quiet
    ? null
    : named?.complete
      ? named.text
      : (given ?? named?.text ?? (page.breadcrumb?.length && page.node ? thisNode(page) : null));
  return {
    id: item.id,
    decision: item.decision,
    text,
    name: item.object_name ?? (item.about ? null : askName(item.decision, text)),
    unblocks,
    quiet,
    at: item.ref,
    primary,
  };
}

/**
 * The page's Your move: the server's rows when it sends them, else the
 * derived ones. `needs` (Needs you's list) names the nodes the page does not.
 */
export function moveView(
  page: NodePage,
  derived: MoveRow[],
  max: number = MOVE_ROWS,
  needs?: NeedsYouList | null,
): MoveView {
  const given: YourMove | undefined = page.your_move;
  if (given) {
    const names = knownNames(page, needs);
    const row = (item: YourMoveItem) => itemRow(item, page, names);
    const rows = given.items.slice(0, max).map(row);
    const keys = new Set(rows.flatMap((r) => askKeys(r.decision)));
    const rest = [
      ...given.items.slice(max).map(row),
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

/**
 * The viewer's asks under each level below a page (the strip's "n you"): an
 * ask counts under every level its breadcrumb passes below the page, so the
 * strip counts the same asks Your move ranks ("Features 3 · 5 you").
 */
export function youByLevel(
  page: NodePage,
  needs: NeedsYouList | null | undefined,
): Map<string, number> | null {
  if (!needs) return null;
  const out = new Map<string, number>();
  const trails: Crumb[][] = [
    ...needs.items.map((i) => i.breadcrumb ?? []),
    ...needs.blocks_nothing.map((q) => q.breadcrumb ?? []),
  ];
  for (const crumbs of trails) {
    const at = crumbs.findIndex((c) => c.ref === page.node.ref);
    if (page.breadcrumb.length && at < 0) continue;
    for (const level of new Set(crumbs.slice(at + 1).map((c) => c.level))) {
      out.set(level, (out.get(level) ?? 0) + 1);
    }
  }
  return out;
}
