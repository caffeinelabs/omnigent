// Which lenses a view offers, and which one it shows. A view is a node, a
// level and a lens; a lens is offered only when it has something to show:
// Map when the children are tiles, List when there are children, Lineage in
// the product tree, Timeline once the server drew at least one lane, Money
// when two or more children have a cost or a budget. The address keeps the
// lens (`?lens=timeline`), so a link opens that view.

import { useCallback, useMemo } from "react";
import { useSearchParams } from "@/lib/routing";
import { PRODUCT_REF } from "../nodeRoutes";
import type { ChildPage, NodePage, NodeSummary } from "../nodeTypes";
import { useLensSupport, useTimeline } from "./lensQueries";

export type Lens = "map" | "list" | "lineage" | "timeline" | "money";

/** The lenses in the order the bar lists them (and `v` steps through them). */
export const LENS_ORDER: readonly Lens[] = ["map", "list", "lineage", "timeline", "money"];

export const LENS_NAMES: Record<Lens, string> = {
  map: "Map",
  list: "List",
  lineage: "Lineage",
  timeline: "Timeline",
  money: "Money",
};

/** The address's lens (`?lens=`). */
export const LENS_PARAM = "lens";

export function isLens(value: string | null | undefined): value is Lens {
  return Boolean(value && (LENS_ORDER as readonly string[]).includes(value));
}

/** True on the product tree (the factory tree has no lineage or results to read). */
export function inProductTree(page: NodePage): boolean {
  return (page.breadcrumb[0]?.ref ?? page.node.ref) === PRODUCT_REF;
}

/** Children the money lens would list: a cost or a budget, never a proposal or a run. */
export function costedChildren(children: ChildPage): NodeSummary[] {
  return [...children.items, ...children.later].filter(
    (k) => k.level !== "run" && ((k.cost ?? 0) >= 0.005 || Boolean(k.budget)),
  );
}

/** Children that could have a lane (the timeline leaves out runs and held work). */
export function laneCandidates(children: ChildPage): NodeSummary[] {
  return [...children.items, ...children.ghosts].filter((k) => k.level !== "run");
}

export interface LensChoice {
  /** The lenses this view offers, in bar order. */
  lenses: Lens[];
  /**
   * The lens shown: the address's when offered, else Map or List. Null when
   * the view has no children (Lineage then folds into one line until asked).
   */
  lens: Lens | null;
  /** The address asks for a lens still being read (Timeline): show it loading. */
  pending: boolean;
  /** Show this lens (kept in the address); null goes back to the default. */
  choose: (lens: Lens | null) => void;
  /** `v`: the next lens. */
  next: () => void;
  timeline: ReturnType<typeof useTimeline>;
}

/**
 * The view's lenses. `grouped`: one level under the node grouped under
 * parents (only Map, List and Lineage read there); `items` and `tiles`: what
 * the children (or the level's groups) hold.
 */
export function useLensChoice(
  page: NodePage,
  {
    grouped,
    items,
    tiles,
  }: {
    grouped: boolean;
    items: number;
    tiles: boolean;
  },
): LensChoice {
  const support = useLensSupport();
  const [params, setParams] = useSearchParams();
  const asked = params.get(LENS_PARAM);
  const lanes = !grouped && support.timeline && laneCandidates(page.children).length > 0;
  const timeline = useTimeline(page.node.ref, lanes);
  const lenses = useMemo(() => {
    const out: Lens[] = [];
    if (items && tiles) out.push("map");
    if (items) out.push("list");
    if (support.lineage && inProductTree(page)) out.push("lineage");
    if (lanes && timeline.data?.lanes.length) out.push("timeline");
    if (!grouped && support.money && costedChildren(page.children).length >= 2) out.push("money");
    return out;
  }, [grouped, items, lanes, page, support.lineage, support.money, tiles, timeline.data]);
  const pending = asked === "timeline" && lanes && timeline.isLoading;
  // Calm: children one line each (List) unless the address asks for Map.
  const fallback = lenses.includes("list") ? "list" : lenses.includes("map") ? "map" : null;
  const lens: Lens | null = isLens(asked) && lenses.includes(asked) ? asked : fallback;
  const choose = useCallback(
    (next: Lens | null) =>
      setParams(
        (prev) => {
          const p = new URLSearchParams(prev);
          if (next) p.set(LENS_PARAM, next);
          else p.delete(LENS_PARAM);
          return p;
        },
        { replace: true },
      ),
    [setParams],
  );
  const next = useCallback(() => {
    if (!lenses.length || (lenses.length < 2 && lens)) return;
    const at = lens ? lenses.indexOf(lens) : -1;
    choose(lenses[(at + 1) % lenses.length]!);
  }, [choose, lens, lenses]);
  return { lenses, lens, pending, choose, next, timeline };
}
