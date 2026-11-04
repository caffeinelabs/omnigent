// The node API as it arrives, mapped onto the shapes the page renders. Most of
// api.md is read as is; three shapes may arrive in api.md's plainer form and
// are widened here: `also_holds` and `moving` name refs only, `why` items are
// bare sentences. Fields the page adds to api.md (marked "(client)" in
// `nodeTypes.ts`) are optional, and `levelName` is their fallback.

import type {
  ChildPage,
  Crumb,
  FindHit,
  LevelView,
  NeedsYouItem,
  NeedsYouList,
  NodeLink,
  NodePage,
  NodeSummary,
  PreviewLine,
  QuietAsk,
  StatusLine,
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

export function normalizeStatus(line: StatusLine): StatusLine {
  const wire = line as WireLine;
  if (!wire.also_holds && !wire.why) return line;
  return {
    ...line,
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

const escText = (s: string) =>
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
  const before = d.before == null ? "" : `${escText(String(d.before))} → `;
  return { label: d.label ?? label, html: `${before}<b>${escText(String(d.after ?? ""))}</b>` };
}

function lineOf(value: WirePreviewLine): PreviewLine {
  if (typeof value === "string") return { label: "", html: escText(value) };
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

export function normalizePreview(preview: VerbPreview): VerbPreview {
  const wire = preview as unknown as Omit<VerbPreview, "effects" | "rollup_delta"> & {
    effects?: WirePreviewLine[];
    rollup_delta?: WirePreviewLine[] | Record<string, unknown>;
  };
  const delta = wire.rollup_delta;
  const rollup: PreviewLine[] = Array.isArray(delta)
    ? delta.map(lineOf)
    : Object.entries(delta ?? {})
        .map(([key, value]) => deltaLine(fieldWords(key), value))
        .filter((line): line is PreviewLine => line != null);
  return {
    ...preview,
    rollup_delta: rollup,
    effects: (wire.effects ?? []).map(lineOf),
    warnings: wire.warnings ?? [],
    ...(wire.columns_after ? { columns_after: children(wire.columns_after) } : {}),
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
      breadcrumb: belowRoot(i.breadcrumb),
      holds: i.holds ?? [],
    })),
    blocks_nothing: quiet.map((q) => {
      if (!("decision" in q)) return q;
      const crumbs = belowRoot(q.breadcrumb);
      const at = crumbs.at(-1);
      return {
        ...q.decision,
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
