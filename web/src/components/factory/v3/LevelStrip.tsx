// The big picture in one row: the node's ancestors (a state dot and a title;
// implicit ones folded into their only child in italics), the node itself,
// then each level below it with a count, "+n later", "n you" and a state bar,
// and the results at the end. Levels with no node are left out, and no cell
// ever says 0. On a phone: "‹ parent" and a row of level cells that scrolls
// sideways inside itself. Below 1024 px the ancestors between the root and
// the parent fold into "…" so every cell keeps a readable title.

import { cn } from "@/lib/utils";
import { NodeLink, StateBar, StateDot } from "./nodeParts";
import type { Crumb, LevelCount, NodePage, Results } from "./nodeTypes";
import { levelName } from "./nodeWire";

const CELL =
  "relative flex min-w-0 flex-[1_1_0] flex-col gap-[3px] px-2.5 pt-2 pb-[9px] text-left outline-none hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-status-blue focus-visible:ring-inset";
const LV =
  "block min-w-0 overflow-hidden text-[10.5px] tracking-[0.06em] text-ellipsis whitespace-nowrap uppercase text-foreground-tertiary";
const CV =
  "flex min-w-0 items-center gap-1.5 overflow-hidden text-[13px] text-ellipsis whitespace-nowrap";
const UNDER =
  "after:absolute after:right-2 after:bottom-[-1px] after:left-2 after:h-0.5 after:rounded-sm";

function Chev() {
  return (
    <span aria-hidden className="flex items-center px-px text-xs text-foreground-tertiary">
      ›
    </span>
  );
}

/** Levels that hold something: an empty level is left out, never shown as 0. */
export function heldLevels(below: LevelCount[]): LevelCount[] {
  return below.filter((c) => c.count + c.later > 0);
}

/** "4 +4 later 2 you": what a level holds, never a bare zero. */
export function LevelCountWords({ c, phone }: { c: LevelCount; phone?: boolean }) {
  if (c.count + c.later <= 0) return null;
  return (
    <>
      {c.count > 0 ? (
        <b className="font-semibold">{c.count}</b>
      ) : (
        <span className="text-foreground-tertiary">{c.later} later</span>
      )}
      {c.count > 0 && c.later > 0 && (
        <span className="font-normal text-foreground-tertiary">
          +{c.later}
          {phone ? "" : " later"}
        </span>
      )}
      {c.needs > 0 && (
        <span className="text-[11.5px] font-semibold text-status-yellow-fg">{c.needs} you</span>
      )}
    </>
  );
}

/** "7 merged · all in base env", or "nothing merged yet". */
export function resultsWords(r: Results): string {
  if (!r.merged) return "nothing merged yet";
  const inBase =
    r.in_base_env === r.merged
      ? "all in base env"
      : r.in_base_env
        ? `${r.in_base_env} in base env`
        : "not yet in base env";
  return `${r.merged} merged · ${inBase}`;
}

function readoutWords(r: Results): string {
  if (r.readouts === "not measured") return "readouts: not measured";
  const parts = [
    r.readouts.day7 && `day 7 ${r.readouts.day7}`,
    r.readouts.day28 && `day 28 ${r.readouts.day28}`,
  ];
  return `readouts: ${parts.filter(Boolean).join(" · ")}`;
}

function crumbTitle(c: Crumb): string {
  return c.tag ? `${c.tag} · ${c.title}` : c.title;
}

/** A code that names the node ("T-16", "#9876", a sha), not a priority like "P0". */
function codeTag(tag: string | undefined): string | undefined {
  return tag && /^([A-Z]{1,3}-\d+|#\d+|[0-9a-f]{7,40})$/.test(tag) ? tag : undefined;
}

/** The desktop strip. `shown` is the level the page lists (underlined). */
export function LevelStrip({ page, shown }: { page: NodePage; shown?: string | null }) {
  const { node, breadcrumb, results } = page;
  const below = heldLevels(page.levels_below);
  const here: Crumb = {
    ref: node.ref,
    level: node.level,
    level_name: node.level_name,
    title: node.title,
    state: node.state,
    tag: codeTag(node.tag),
  };
  const path = [...breadcrumb, here];
  // Below 1024 px: the root, "…", the parent and the node itself.
  const folds = (i: number) => i > 0 && i < path.length - 2;
  return (
    <nav
      aria-label="Levels"
      data-testid="v3-level-strip"
      className="flex min-w-0 items-stretch border-b border-border bg-muted/30 px-2"
    >
      {path.map((c, i) => {
        const isHere = i === path.length - 1;
        return (
          <span
            key={c.ref}
            className={cn("flex min-w-0 flex-[1_1_0]", folds(i) && "max-lg:hidden")}
            style={i === 0 ? { flex: "0.8 1 0" } : undefined}
          >
            {i > 0 && <Chev />}
            {i > 0 && folds(i - 1) && !folds(i) && (
              <span
                aria-hidden
                className="flex items-center gap-1 px-1 text-foreground-tertiary lg:hidden"
              >
                …
                <Chev />
              </span>
            )}
            <NodeLink
              nodeRef={c.ref}
              title={c.folded ? `${c.title} (implicit, folded into its only child)` : c.title}
              className={cn(
                CELL,
                isHere && cn("bg-card-solid after:bg-status-blue", UNDER),
                c.folded && "[&_[data-cv]]:italic [&_[data-cv]]:text-foreground-tertiary",
              )}
            >
              <span className={LV}>{levelName(c.level, c.level_name)}</span>
              <span
                data-cv
                className={cn(
                  CV,
                  isHere ? "font-semibold text-foreground" : "text-muted-foreground",
                )}
              >
                {!c.folded && <StateDot state={c.state} />}
                <span className="overflow-hidden text-ellipsis">{crumbTitle(c)}</span>
              </span>
            </NodeLink>
          </span>
        );
      })}
      {below.map((c) => (
        <span key={c.level} className="flex min-w-0 flex-[1_1_0]">
          <Chev />
          <NodeLink
            nodeRef={node.ref}
            level={c.level}
            title={`All ${c.name.toLowerCase()} under ${node.title}, grouped under their parents`}
            className={cn(CELL, shown === c.level && cn("after:bg-foreground-tertiary", UNDER))}
          >
            <span className={cn(LV, shown === c.level && "text-muted-foreground")}>
              {c.name}
              {c.debug ? " · debug" : ""}
            </span>
            <span data-testid={`v3-level-${c.level}`} className={CV}>
              <LevelCountWords c={c} />
            </span>
            <StateBar byState={c.by_state} />
          </NodeLink>
        </span>
      ))}
      {!below.length && page.children.level && (
        <span className="flex min-w-0 flex-[1_1_0]">
          <Chev />
          <span
            className={cn(CELL, "cursor-default text-foreground-tertiary hover:bg-transparent")}
          >
            <span className={LV}>Below</span>
            <span className={CV}>nothing yet</span>
          </span>
        </span>
      )}
      {results && (
        <span
          data-testid="v3-results"
          className={cn(
            CELL,
            "ml-1.5 flex-[1.3_1_0] cursor-default border-l border-dashed border-border-strong hover:bg-transparent",
          )}
        >
          <span className={LV}>Results</span>
          <span className={CV}>{resultsWords(results)}</span>
          <span className={cn(CV, "text-[11.5px] text-foreground-tertiary")}>
            {readoutWords(results)}
          </span>
        </span>
      )}
    </nav>
  );
}

/** The phone's row: back to the parent, then the levels below as cells scrolling inside the row. */
export function PhoneLevelRow({ page, shown }: { page: NodePage; shown?: string | null }) {
  const { node, breadcrumb, results } = page;
  const below = heldLevels(page.levels_below);
  const parent = [...breadcrumb].reverse().find((c) => !c.folded);
  return (
    <div data-testid="v3-phone-levels" className="flex min-w-0 flex-col gap-2">
      <div className="flex items-center gap-2">
        {parent ? (
          <NodeLink
            nodeRef={parent.ref}
            className="min-w-0 flex-1 overflow-hidden py-1 text-[13.5px] text-ellipsis whitespace-nowrap text-status-blue-fg"
          >
            ‹ {parent.title}
          </NodeLink>
        ) : (
          <span className="flex-1 py-1 text-[13.5px] text-muted-foreground">
            {levelName(node.level, node.level_name)}
          </span>
        )}
      </div>
      {(below.length > 0 || (results && results.merged > 0)) && (
        <nav
          aria-label="Levels below"
          className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {below.map((c) => (
            <NodeLink
              key={c.level}
              nodeRef={node.ref}
              level={c.level}
              className={cn(
                "flex min-w-[74px] shrink-0 flex-col gap-[3px] rounded-[9px] border bg-card-solid px-2.5 py-1.5 text-left",
                shown === c.level ? "border-foreground-tertiary" : "border-border",
              )}
            >
              <span className="text-[10px] tracking-[0.06em] uppercase text-foreground-tertiary">
                {c.name}
              </span>
              <span
                data-testid={`v3-phone-level-${c.level}`}
                className="flex items-center gap-1 text-[12.5px] font-semibold whitespace-nowrap"
              >
                <LevelCountWords c={c} phone />
              </span>
              <StateBar byState={c.by_state} className="max-w-none" />
            </NodeLink>
          ))}
          {results && results.merged > 0 && (
            <span className="flex min-w-[74px] shrink-0 flex-col gap-[3px] rounded-[9px] border border-border bg-card-solid px-2.5 py-1.5">
              <span className="text-[10px] tracking-[0.06em] uppercase text-foreground-tertiary">
                Results
              </span>
              <span className="text-[12.5px] font-semibold whitespace-nowrap">
                {results.merged} merged
              </span>
              <span className="text-[11px] whitespace-nowrap text-foreground-tertiary">
                {resultsWords(results).split(" · ")[1]}
              </span>
            </span>
          )}
        </nav>
      )}
    </div>
  );
}
