// The node API as it arrives, mapped onto the shapes the page renders. Most of
// api.md is read as is; three shapes may arrive in api.md's plainer form and
// are widened here: `also_holds` and `moving` name refs only, `why` items are
// bare sentences. Fields the page adds to api.md (marked "(client)" in
// `nodeTypes.ts`) are optional, and `levelName` is their fallback.

import type {
  ChildPage,
  LevelView,
  NodeLink,
  NodePage,
  NodeSummary,
  StatusLine,
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
