// A node's children in one lens, calm: one line each (List, the default) or
// tiles (Map), under First / Then / Later when siblings have an order (order
// is a heading, never an arrow), rows grouped by state past 60 (60 to a page),
// done work folded into one "✓ n done" line, held work under Later.

import { useState } from "react";
import { ChevronDownIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { MorePages } from "./nodeQueries";
import { NodeRow, NodeTile, type Overlays, byState } from "./NodeTile";
import type { ChildPage, NodeSummary } from "./nodeTypes";
import { drawsTiles } from "./nodeWire";

export const PAGE_ROWS = 60;
export const TILES_MAX = 12;

const COLH =
  "flex items-baseline gap-2 px-0.5 text-[11px] tracking-[0.07em] uppercase text-foreground-tertiary";
// `bg-card-solid`, not `bg-card`: dark mode's glass rule restyles every
// top-level `bg-card` and would drop these rings and state edges.
const ROWS = "overflow-hidden rounded-xl bg-card-solid ring-1 ring-border";
const NEXT_BTN =
  "rounded-[7px] border border-border-strong bg-card-solid px-2 text-xs font-semibold text-foreground disabled:opacity-60";
const TILE_GRID = "grid grid-cols-[repeat(auto-fill,minmax(min(100%,240px),1fr))] gap-3";

/** "✓ 4 done": done siblings in one line; a click shows them. */
export function DoneLine({
  done,
  label,
  compactTiles = true,
  rows,
}: {
  done: NodeSummary[];
  /** Overrides "n done" (a folded group of groups: "6 projects all done"). */
  label?: string;
  compactTiles?: boolean;
  overlays?: Overlays;
  /** Show them as rows when unfolded. */
  rows?: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (!done.length) return null;
  if (open) {
    return (
      <div className="flex flex-col gap-1.5">
        {rows ? (
          <ul role="list" className={ROWS}>
            {done.map((n) => (
              <NodeRow key={n.ref} node={n} />
            ))}
          </ul>
        ) : (
          <ul role="list" className={TILE_GRID}>
            {done.map((n) => (
              <NodeTile key={n.ref} node={n} compact={compactTiles} />
            ))}
          </ul>
        )}
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="self-start px-0.5 text-[12.5px] text-muted-foreground hover:text-foreground"
        >
          Fold done
        </button>
      </div>
    );
  }
  return (
    <button
      type="button"
      data-testid="v3-done-line"
      aria-expanded={false}
      title={done.map((d) => d.title).join(" · ")}
      onClick={() => setOpen(true)}
      className="flex items-center gap-2 self-start rounded-[8px] px-1 py-1 text-left text-[13px] text-muted-foreground hover:text-foreground"
    >
      <span aria-hidden className="font-bold text-status-green-fg">
        ✓
      </span>
      <span>{label ?? `${done.length} done`}</span>
      <ChevronDownIcon aria-hidden className="size-3" />
    </button>
  );
}

/** Held work, one row each; its reason is the row's phrase. Not counted in progress or cost. */
export function LaterBox({ later }: { later: NodeSummary[]; note?: string }) {
  if (!later.length) return null;
  return (
    <ul
      role="list"
      data-testid="v3-later"
      className="overflow-hidden rounded-xl border border-dashed border-border-strong"
    >
      {later.map((k) => (
        <NodeRow key={k.ref} node={{ ...k, state: "later" }} />
      ))}
    </ul>
  );
}

function colOf(n: NodeSummary): number {
  return typeof n.col === "number" ? n.col : 0;
}

/** Siblings in their order columns as tiles (the Map lens): First / Then / … and Later. */
export function OrderColumns({ page }: { page: ChildPage; overlays?: Overlays }) {
  const columns = page.columns ?? [{ title: "", col: 0 }];
  const hasLater = page.later.length > 0;
  const ncol = Math.min(4, columns.length + (hasLater ? 1 : 0));
  const compact = ncol > 2;
  return (
    <ul
      role="list"
      data-testid="v3-columns"
      className="grid items-start gap-3.5 max-md:!grid-cols-1"
      style={{ gridTemplateColumns: `repeat(${ncol}, minmax(0, 1fr))` }}
    >
      {columns.map((c) => {
        const items = page.items.filter((k) => colOf(k) === c.col);
        const live = items.filter((k) => k.state !== "done").sort(byState);
        const done = items.filter((k) => k.state === "done");
        const ghosts = page.ghosts.filter((g) => colOf(g) === c.col);
        return (
          <li key={c.col} className="flex min-w-0 flex-col gap-2" data-testid="v3-column">
            {c.title && <div className={COLH}>{c.title}</div>}
            <ul role="list" className="flex flex-col gap-2">
              {live.map((k) => (
                <NodeTile key={k.ref} node={k} compact={compact} />
              ))}
              {ghosts.map((k) => (
                <NodeTile key={k.ref} node={k} compact />
              ))}
              {done.length === 1 && <NodeTile node={done[0]!} compact />}
            </ul>
            {done.length > 1 && <DoneLine done={done} />}
          </li>
        );
      })}
      {hasLater && (
        <li className="flex min-w-0 flex-col gap-2">
          <div className={COLH}>{page.later_title ?? "Later"}</div>
          <LaterBox later={page.later} />
        </li>
      )}
    </ul>
  );
}

/** Rows, 60 to a page. */
export function PagedRows({
  items,
}: {
  items: NodeSummary[];
  overlays?: Overlays;
  owner?: boolean;
}) {
  const [pages, setPages] = useState(1);
  const shown = items.slice(0, pages * PAGE_ROWS);
  return (
    <ul role="list" className={ROWS}>
      {shown.map((n) => (
        <NodeRow key={n.ref} node={n} />
      ))}
      {items.length > shown.length && (
        <li className="flex items-center justify-center gap-2.5 border-t border-border p-2 text-[12.5px] text-foreground-tertiary">
          {shown.length} of {items.length}
          <button type="button" onClick={() => setPages((p) => p + 1)} className={NEXT_BTN}>
            Next {PAGE_ROWS}
          </button>
        </li>
      )}
    </ul>
  );
}

/** The server's next 60 (`children.next`, a group's `next`): "60 of 340 · Next 60". */
export function MoreFooter({
  more,
  shown,
  total,
}: {
  more: MorePages;
  shown: number;
  total?: number;
}) {
  if (!more.more) return null;
  return (
    <div
      data-testid="v3-more"
      className="flex items-center justify-center gap-2.5 p-2 text-[12.5px] text-foreground-tertiary"
    >
      {total && total > shown ? `${shown} of ${total}` : `${shown} shown`}
      <button type="button" onClick={more.more} disabled={more.loading} className={NEXT_BTN}>
        Next {PAGE_ROWS}
      </button>
    </div>
  );
}

/** Every state but done, which folds into one line. */
const STATE_GROUPS: [NodeSummary["state"][], string][] = [
  [["needs"], "Needs you"],
  [["broken"], "Broken"],
  [["moving"], "Moving"],
  [["waiting", "paused"], "Waiting"],
  [["notstarted", "offered"], "Not started"],
  [["ghost"], "Proposed"],
  [["later"], "Later"],
];

/** More than 60 children: rows grouped by state, done folded, 60 to a page. */
export function StateGroups({
  items,
}: {
  items: NodeSummary[];
  overlays?: Overlays;
  owner?: boolean;
}) {
  const done = items.filter((k) => k.state === "done");
  return (
    <div className="flex flex-col gap-3" data-testid="v3-state-groups">
      {STATE_GROUPS.map(([states, label]) => {
        const g = items.filter((k) => states.includes(k.state));
        if (!g.length) return null;
        return (
          <div key={label} className="flex flex-col gap-1.5">
            <div className={COLH}>
              {label} <span className="tracking-normal normal-case">{g.length}</span>
            </div>
            <PagedRows items={g} />
          </div>
        );
      })}
      <DoneLine done={done} rows />
    </div>
  );
}

/** Live rows, then done folded into one line (one done child stays a row). */
function LiveThenDone({ items }: { items: NodeSummary[] }) {
  const live = items.filter((k) => k.state !== "done").sort(byState);
  const done = items.filter((k) => k.state === "done");
  const rows = done.length === 1 ? [...live, ...done] : live;
  return (
    <>
      {rows.length > 0 && <PagedRows items={rows} />}
      {done.length > 1 && <DoneLine done={done} rows />}
    </>
  );
}

/** Rows (the List lens); under First / Then / Later when siblings have an order. */
export function ColumnRows({ page }: { page: ChildPage; overlays?: Overlays }) {
  const active = [...page.items, ...page.ghosts];
  const columns = page.columns && page.columns.length > 1 ? page.columns : null;
  const later = page.later.length > 0;
  const headed = Boolean(columns) || (later && active.length > 0);
  return (
    <div className="flex flex-col gap-2.5">
      {columns ? (
        columns.map((c) => {
          const rows = active.filter((k) => colOf(k) === c.col);
          if (!rows.length) return null;
          return (
            <div key={c.col} className="flex flex-col gap-1.5" data-testid="v3-column">
              <div className={cn(COLH, "mx-0.5")}>{c.title}</div>
              <LiveThenDone items={rows} />
            </div>
          );
        })
      ) : (
        <LiveThenDone items={active} />
      )}
      {later && (
        <div className="flex flex-col gap-1.5">
          {headed && <div className={cn(COLH, "mx-0.5")}>{page.later_title ?? "Later"}</div>}
          <LaterBox later={page.later} />
        </div>
      )}
    </div>
  );
}

/** The children of a node in the chosen lens, with the server's next 60 when there are more. */
export function ChildrenView({
  page,
  lens,
  more,
}: {
  page: ChildPage;
  lens: "map" | "list";
  overlays?: Overlays;
  more?: MorePages;
}) {
  const active = page.items.length;
  if (!active && !page.later.length && !page.ghosts.length) return null;
  const footer = more ? (
    <MoreFooter
      more={more}
      shown={active}
      total={page.total - page.later.length - page.ghosts.length}
    />
  ) : null;
  const tiles = drawsTiles(page);
  if (active > PAGE_ROWS || page.grouped_by_state) {
    return (
      <div className="flex flex-col gap-3">
        <StateGroups items={[...page.items, ...page.ghosts]} />
        <LaterBox later={page.later} />
        {footer}
      </div>
    );
  }
  if (lens === "list" || !tiles || active > TILES_MAX) {
    return (
      <div className="flex flex-col gap-2">
        <ColumnRows page={page} />
        {footer}
      </div>
    );
  }
  if (page.columns) return <OrderColumns page={page} />;
  const live = page.items.filter((k) => k.state !== "done").sort(byState);
  const done = page.items.filter((k) => k.state === "done");
  return (
    <div className="flex flex-col gap-3">
      {(live.length > 0 || page.ghosts.length > 0) && (
        <ul role="list" className={TILE_GRID}>
          {live.map((k) => (
            <NodeTile key={k.ref} node={k} />
          ))}
          {page.ghosts.map((k) => (
            <NodeTile key={k.ref} node={k} compact />
          ))}
        </ul>
      )}
      <DoneLine done={done} />
    </div>
  );
}
