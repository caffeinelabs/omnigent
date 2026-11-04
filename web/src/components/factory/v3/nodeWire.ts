// The node API as it arrives, mapped onto the shapes the page renders. Most of
// api.md is read as is; three shapes may arrive in api.md's plainer form and
// are widened here: `also_holds` and `moving` name refs only, `why` items are
// bare sentences. Fields the page adds to api.md (marked "(client)" in
// `nodeTypes.ts`) are optional, and `levelName` is their fallback.

import type {
  ChildPage,
  Crumb,
  DecisionRef,
  FindHit,
  LevelView,
  LineageRow,
  LineageView,
  MoneyView,
  NeedsYouItem,
  NeedsYouList,
  NodeLink,
  NodePage,
  NodeSummary,
  PreviewLine,
  QuietAsk,
  StatusLine,
  TimelineView,
  VerbPreview,
  WhyItem,
} from "./nodeTypes";

/** A level's name: the server's, else its key in words ("feature" → "Feature"). */
export function levelName(key: string, name?: string | null): string {
  if (name) return name;
  const words = key.replace(/[_-]+/g, " ").trim();
  return words ? `${words[0]!.toUpperCase()}${words.slice(1)}` : "";
}

/** Children are tiles unless the server says the level is rows only. */
export function drawsTiles(page: { tiles?: boolean }): boolean {
  return page.tiles !== false;
}

function link(value: NodeLink | string): NodeLink {
  return typeof value === "string" ? { ref: value, title: value } : value;
}

function why(value: WhyItem | string): WhyItem {
  return typeof value === "string" ? { html: value } : value;
}

type WireLine = Omit<StatusLine, "also_holds" | "why"> & {
  also_holds?: (NodeLink | string)[];
  why?: (WhyItem | string)[];
};

/** An ask's text as words: a person's proposal arrives as Markdown ("**Why:** …"). */
export function askWords<T extends DecisionRef>(decision: T): T {
  const text = decision.text;
  if (!text || !/[*_`]/.test(text)) return decision;
  const plain = text
    .replace(/(\*\*|__)(.+?)\1/g, "$2")
    .replace(/`([^`]+)`/g, "$1")
    .trim();
  return { ...decision, text: plain };
}

export function normalizeStatus(line: StatusLine): StatusLine {
  const wire = line as WireLine;
  const segments = line.segments ?? [];
  const asks = line.decision?.text || segments.some((s) => "decision" in s);
  if (!wire.also_holds && !wire.why && !asks) return line;
  return {
    ...line,
    ...(line.decision ? { decision: askWords(line.decision) } : {}),
    segments: segments.map((s) =>
      "decision" in s && s.decision ? { ...s, decision: askWords(s.decision) } : s,
    ),
    ...(wire.also_holds ? { also_holds: wire.also_holds.map(link) } : {}),
    ...(wire.why ? { why: wire.why.map(why) } : {}),
  };
}

function summary<T extends NodeSummary>(node: T): T {
  return { ...node, status_compact: normalizeStatus(node.status_compact) };
}

function children(page: ChildPage): ChildPage {
  return {
    ...page,
    items: (page.items ?? []).map(summary),
    later: (page.later ?? []).map(summary),
    ghosts: (page.ghosts ?? []).map(summary),
  };
}

/** A node page from the server, in the shapes the page renders. */
export function normalizeNodePage(page: NodePage): NodePage {
  return {
    ...page,
    node: summary(page.node),
    breadcrumb: page.breadcrumb ?? [],
    status: normalizeStatus(page.status),
    chips: page.chips ?? [],
    more_chips: page.more_chips ?? 0,
    verbs: page.verbs ?? [],
    levels_below: page.levels_below ?? [],
    children: children(page.children),
  };
}

/** A level view from the server, in the shapes the page renders. */
export function normalizeLevelView(view: LevelView): LevelView {
  return {
    ...view,
    groups: (view.groups ?? []).map((g) => ({ ...g, items: (g.items ?? []).map(summary) })),
  };
}

function lineageRow(row: LineageRow): LineageRow {
  return { ...row, line: normalizeStatus(row.line) };
}

/** A lineage from the server: each row's line as the page reads it. */
export function normalizeLineage(view: LineageView): LineageView {
  return {
    ...view,
    ancestors: (view.ancestors ?? []).map(lineageRow),
    here: lineageRow(view.here),
    below: view.below ?? [],
  };
}

/** A timeline from the server: lanes with no bars are left out (nothing empty is drawn). */
export function normalizeTimeline(view: TimelineView): TimelineView {
  return { ...view, lanes: (view.lanes ?? []).filter((l) => l.bars?.length) };
}

/** Money from the server, as is (rows most first). */
export function normalizeMoney(view: MoneyView): MoneyView {
  return { ...view, rows: view.rows ?? [] };
}

/** Text as the limited markup preview lines carry (`RichText` reads it back as text). */
export const escText = (s: string) =>
  s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

type WirePreviewLine = PreviewLine | string | { label?: string; text?: string; html?: string };
interface WireDelta {
  before?: unknown;
  after?: unknown;
  text?: string;
  label?: string;
}

/** "Later 4 → 3": a field's before and after, or its own words. */
function deltaLine(label: string, value: unknown): PreviewLine | null {
  if (value == null) return null;
  if (typeof value !== "object") return { label, html: escText(String(value)) };
  const d = value as WireDelta;
  if (d.text) return { label: d.label ?? label, html: escText(d.text) };
  if (d.before == null && d.after == null) return null;
  // The arrow between before and after is drawn here; a value's own leading "→ " would double it.
  const words = (v: unknown) => escText(String(v).replace(/^→\s*/, ""));
  const before = d.before == null ? "" : `${words(d.before)} → `;
  return {
    label: d.label ?? label,
    html: `${before}<b>${d.after == null ? "" : words(d.after)}</b>`,
  };
}

/** "Wait: waits on …": a sentence that starts with a one- or two-word label ("Depends on"). */
const LABELLED = /^([A-Z][a-z]+(?: [a-z]+)?): (.+)$/s;

function lineOf(value: WirePreviewLine): PreviewLine {
  if (typeof value === "string") {
    const labelled = LABELLED.exec(value);
    return labelled
      ? { label: labelled[1]!, html: escText(labelled[2]!) }
      : { label: "", html: escText(value) };
  }
  const wire = value as { label?: string; text?: string; html?: string };
  if (wire.html != null) return { label: wire.label ?? "", html: wire.html };
  return { label: wire.label ?? "", html: escText(wire.text ?? "") };
}

/** A field name in words ("burn_per_h" → "Burn per h"). */
function fieldWords(key: string): string {
  return levelName(key);
}

/**
 * A preview from the server, in the shapes the panel renders: `effects` may
 * arrive as sentences, `rollup_delta` as api.md's open object
 * (`{progress: {before, after}, cost: "…"}`) or as lines.
 */
/**
 * The preview fields the web reads beyond api.md §4, each optional with a
 * fallback (docs/FACTORY.md lists them for the S12 server; a test keeps the
 * two lists the same).
 */
export const CLIENT_PREVIEW_FIELDS = [
  "title",
  "moved_from",
  "approver_note",
  "pulls_forward",
  "parents",
  "apply_label",
] as const satisfies readonly (keyof VerbPreview)[];

/** The S12 server's roll-up: each node's changed items, and one sentence for them all. */
interface WireNodeDelta {
  nodes: { ref?: string; title?: string; items?: ({ label?: string } & WireDelta)[] }[];
  text?: string;
}

function isNodeDelta(value: unknown): value is WireNodeDelta {
  return (
    value != null && typeof value === "object" && Array.isArray((value as WireNodeDelta).nodes)
  );
}

/** "Agent assistant · Projects: 2 → 3" per changed item, then the server's sentence. */
function nodeDeltaLines(delta: WireNodeDelta): PreviewLine[] {
  const lines = delta.nodes.flatMap((node) =>
    (node.items ?? [])
      .map((item) =>
        deltaLine([node.title, item.label].filter(Boolean).join(" · "), {
          before: item.before,
          after: item.after,
        }),
      )
      .filter((line): line is PreviewLine => line != null),
  );
  return delta.text ? [...lines, { label: "", html: escText(delta.text) }] : lines;
}

/**
 * The parent's columns after the change, as the mini columns draw them. The
 * server lists the changed node (moved, or new) under `ghosts` with the column
 * it lands in, keeping a moved node's own state; it is drawn dashed in that
 * column (`state: "ghost"`). Ghosts with no column are the page's own
 * proposals: they are not this change and stay apart.
 */
function previewColumns(page: ChildPage): ChildPage {
  const read = children(page);
  const landed = read.ghosts.filter((g) => g.col != null);
  if (!landed.length) return read;
  const ghost = (g: NodeSummary): NodeSummary => ({ ...g, state: "ghost" });
  const placed = new Set(landed.map((g) => g.ref));
  const keep = (k: NodeSummary) => !placed.has(k.ref);
  return {
    ...read,
    items: [...read.items.filter(keep), ...landed.filter((g) => g.col !== "later").map(ghost)],
    later: [...landed.filter((g) => g.col === "later").map(ghost), ...read.later.filter(keep)],
    ghosts: read.ghosts.filter((g) => g.col == null),
  };
}

/** "depends_on_done: x waits on done y" → "x waits on done y": a code prefix is not words. */
function warningWords(warning: string): string {
  return warning.replace(/^[a-z][a-z0-9]*(?:_[a-z0-9]+)+: /, "");
}

export function normalizePreview(preview: VerbPreview): VerbPreview {
  const wire = preview as unknown as Omit<VerbPreview, "effects" | "rollup_delta"> & {
    effects?: WirePreviewLine[];
    rollup_delta?: WirePreviewLine[] | Record<string, unknown> | WireNodeDelta | null;
  };
  const delta = wire.rollup_delta;
  const rollup: PreviewLine[] = Array.isArray(delta)
    ? delta.map(lineOf)
    : isNodeDelta(delta)
      ? nodeDeltaLines(delta)
      : Object.entries(delta ?? {})
          .map(([key, value]) => deltaLine(fieldWords(key), value))
          .filter((line): line is PreviewLine => line != null);
  return {
    ...preview,
    rollup_delta: rollup,
    effects: (wire.effects ?? []).map(lineOf),
    warnings: [...new Set((wire.warnings ?? []).map(warningWords))],
    ...(wire.columns_after ? { columns_after: previewColumns(wire.columns_after) } : {}),
  };
}

/** Needs you from the server, with its lists present. */
/** A breadcrumb without its tree's root (it comes root first): the sheet names the tree once. */
function belowRoot<T>(crumbs: T[] | undefined): T[] {
  const list = crumbs ?? [];
  return list.length > 1 ? list.slice(1) : list;
}

type WireQuiet = QuietAsk | NeedsYouItem;

/**
 * Needs you from the server, its lists present and its breadcrumbs below the
 * root. An ask that blocks nothing may come as an item (`{decision,
 * breadcrumb}`); it is read as the ask with the node it sits on.
 */
export function normalizeNeedsYou(list: NeedsYouList): NeedsYouList {
  const quiet = (list.blocks_nothing ?? []) as WireQuiet[];
  return {
    items: (list.items ?? []).map((i) => ({
      ...i,
      decision: askWords(i.decision),
      breadcrumb: belowRoot(i.breadcrumb),
      holds: i.holds ?? [],
    })),
    blocks_nothing: quiet.map((q) => {
      if (!("decision" in q)) return askWords(q);
      const crumbs = belowRoot(q.breadcrumb);
      const at = crumbs.at(-1);
      return {
        ...askWords(q.decision),
        breadcrumb: crumbs,
        ...(at ? { node: { ref: at.ref, title: at.title } } : {}),
      };
    }),
    closed_alarms: list.closed_alarms ?? [],
    other_tree: list.other_tree ?? 0,
  };
}

type WireHit = Omit<FindHit, "breadcrumb"> & { breadcrumb?: (Crumb | string)[] };

/**
 * Find's hits, their breadcrumbs as titles below the root (api.md sends crumbs
 * or titles; the server's answer is `{q, items}`).
 */
export function normalizeFind(
  hits: WireHit[] | { hits?: WireHit[]; items?: WireHit[] },
): FindHit[] {
  const list = Array.isArray(hits) ? hits : (hits.items ?? hits.hits ?? []);
  return list.map((h) => ({
    ...h,
    breadcrumb: belowRoot(h.breadcrumb).map((c) => (typeof c === "string" ? c : c.title)),
  }));
}
