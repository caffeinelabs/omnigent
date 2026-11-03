// A node's children in one lens: tiles under First / Then / Later columns
// (order among siblings is a column, never an arrow), rows past 12, rows
// grouped by state past 60 (60 to a page), done work folded into one line,
// held work in one dashed box with its reasons. Proposals and held work show
// at every size.

import { useState } from "react";
import { cn } from "@/lib/utils";
import { StateDot } from "./nodeParts";
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

/** "✓ 4 done  title · title · in base env  show": done siblings in one line; a click shows them. */
export function DoneLine({
  done,
  label,
  compactTiles = true,
  overlays,
  rows,
}: {
  done: NodeSummary[];
  /** Overrides "n done" (a folded group of groups: "6 projects all done"). */
  label?: string;
  compactTiles?: boolean;
  overlays: Overlays;
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
              <NodeRow key={n.ref} node={n} overlays={overlays} />
            ))}
          </ul>
        ) : (
          <ul
            role="list"
            className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,290px),1fr))] gap-3"
          >
            {done.map((n) => (
              <NodeTile key={n.ref} node={n} compact={compactTiles} overlays={overlays} />
            ))}
          </ul>
        )}
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="self-start rounded-[7px] border border-dashed border-border-strong px-2.5 text-[12.5px] text-muted-foreground hover:text-foreground"
        >
          Fold done
        </button>
      </div>
    );
  }
  const single = done.length === 1 ? done[0]! : null;
  const names = done.map((d) =>
    single && d.status_compact.detail ? `${d.title}: ${d.status_compact.detail}` : d.title,
  );
  const joined = names.join(" ");
  const allIn = done.every((d) => d.in_base_env) && !joined.includes("in base env");
  return (
    <button
      type="button"
      data-testid="v3-done-line"
      onClick={() => setOpen(true)}
      className="flex w-full flex-wrap items-center gap-2 rounded-[10px] border border-dashed border-border-strong px-3 py-2 text-left text-[13px] text-muted-foreground hover:border-foreground-tertiary hover:text-foreground"
    >
      <span aria-hidden className="font-bold text-status-green-fg">
        ✓
      </span>
      <b className="font-semibold text-foreground">{label ?? `${done.length} done`}</b>
      <span className="min-w-0">
        {names.slice(0, 4).join(" · ")}
        {names.length > 4 ? ` +${names.length - 4}` : ""}
      </span>
      {allIn && <span className="text-foreground-tertiary">· in base env</span>}
      <span className="flex-1" />
      <span className="text-foreground-tertiary">show</span>
    </button>
  );
}

/** Held work: one dashed box, each item with what it waits for. Not counted in progress or cost. */
export function LaterBox({ later, note }: { later: NodeSummary[]; note?: string }) {
  if (!later.length) return null;
  return (
    <div
      data-testid="v3-later"
      className="flex flex-col gap-[5px] rounded-[10px] border border-dashed border-border-strong px-3 py-2"
    >
      {later.map((k) => (
        <div
          key={k.ref}
          className="flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground"
          title={k.held_for ? `Held for ${k.held_for}` : undefined}
        >
          <StateDot state="later" />
          <span>{k.title}</span>
          {k.held_for && (
            <span className="-mt-1 basis-full pl-4 text-[11.5px] text-foreground-tertiary">
              {k.held_for}
            </span>
          )}
        </div>
      ))}
      {note && <div className="text-[11.8px] text-foreground-tertiary">{note}</div>}
    </div>
  );
}

function colOf(n: NodeSummary): number {
  return typeof n.col === "number" ? n.col : 0;
}

/** Siblings in their order columns: First / Then / … and the held column. At most four to a row. */
export function OrderColumns({ page, overlays }: { page: ChildPage; overlays: Overlays }) {
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
      {columns.map((c, i) => {
        const items = page.items.filter((k) => colOf(k) === c.col);
        const live = items.filter((k) => k.state !== "done").sort(byState);
        const done = items.filter((k) => k.state === "done");
        const ghosts = page.ghosts.filter((g) => colOf(g) === c.col);
        return (
          <li key={c.col} className="flex min-w-0 flex-col gap-2" data-testid="v3-column">
            <div className={COLH}>
              {c.title}
              {i > 0 && (
                <span className="text-[11.5px] tracking-normal normal-case">
                  after {columns[i - 1]!.title.toLowerCase()}
                </span>
              )}
            </div>
            <ul role="list" className="flex flex-col gap-2">
              {live.map((k) => (
                <NodeTile key={k.ref} node={k} compact={compact} overlays={overlays} />
              ))}
              {ghosts.map((k) => (
                <NodeTile key={k.ref} node={k} compact overlays={overlays} />
              ))}
              {done.length === 1 && <NodeTile node={done[0]!} compact overlays={overlays} />}
            </ul>
            {done.length > 1 && <DoneLine done={done} overlays={overlays} />}
          </li>
        );
      })}
      {hasLater && (
        <li className="flex min-w-0 flex-col gap-2">
          <div className={COLH}>{page.later_title ?? "Later"}</div>
          <LaterBox later={page.later} note={page.later_note} />
        </li>
      )}
    </ul>
  );
}

/** Rows, 60 to a page. */
export function PagedRows({
  items,
  overlays,
  owner,
}: {
  items: NodeSummary[];
  overlays: Overlays;
  owner?: boolean;
}) {
  const [pages, setPages] = useState(1);
  const shown = items.slice(0, pages * PAGE_ROWS);
  return (
    <ul role="list" className={ROWS}>
      {shown.map((n) => (
        <NodeRow key={n.ref} node={n} overlays={overlays} owner={owner} />
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
  overlays,
  owner,
}: {
  items: NodeSummary[];
  overlays: Overlays;
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
            <PagedRows items={g} overlays={overlays} owner={owner} />
          </div>
        );
      })}
      <DoneLine done={done} overlays={overlays} rows />
    </div>
  );
}

/** Rows; under First / Then headings when siblings have an order. Held work in its box below. */
export function ColumnRows({ page, overlays }: { page: ChildPage; overlays: Overlays }) {
  const active = [...page.items, ...page.ghosts];
  const columns = page.columns && page.columns.length > 1 ? page.columns : null;
  const owner = !drawsTiles(page);
  return (
    <div className="flex flex-col gap-2.5">
      {columns ? (
        columns.map((c, i) => {
          const rows = active.filter((k) => colOf(k) === c.col).sort(byState);
          if (!rows.length) return null;
          return (
            <div key={c.col} className="flex flex-col gap-1.5">
              <div className={cn(COLH, "mx-0.5")}>
                {c.title}
                {i > 0 && (
                  <span className="text-[11.5px] tracking-normal normal-case">
                    after {columns[i - 1]!.title.toLowerCase()}
                  </span>
                )}
              </div>
              <PagedRows items={rows} overlays={overlays} owner={owner} />
            </div>
          );
        })
      ) : (
        <>
          {active.some((k) => k.state !== "done") && (
            <PagedRows
              items={active.filter((k) => k.state !== "done").sort(byState)}
              overlays={overlays}
              owner={owner}
            />
          )}
          {active.filter((k) => k.state === "done").length > 1 ? (
            <DoneLine done={active.filter((k) => k.state === "done")} overlays={overlays} rows />
          ) : (
            active.some((k) => k.state === "done") && (
              <PagedRows
                items={active.filter((k) => k.state === "done")}
                overlays={overlays}
                owner={owner}
              />
            )
          )}
        </>
      )}
      <LaterBox later={page.later} note={page.later_note} />
    </div>
  );
}

/** The children of a node in the chosen lens, with the server's next 60 when there are more. */
export function ChildrenView({
  page,
  lens,
  overlays,
  more,
}: {
  page: ChildPage;
  lens: "map" | "list";
  overlays: Overlays;
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
        <StateGroups items={[...page.items, ...page.ghosts]} overlays={overlays} owner={!tiles} />
        <LaterBox later={page.later} note={page.later_note} />
        {footer}
      </div>
    );
  }
  const autoRows = lens === "map" && tiles && active > TILES_MAX;
  if (lens === "list" || !tiles || autoRows) {
    return (
      <div className="flex flex-col gap-2">
        {autoRows && (
          <p className="-mt-0.5 text-xs text-foreground-tertiary">
            {active} children: shown as rows (tiles are for {TILES_MAX} or fewer).
          </p>
        )}
        <ColumnRows page={page} overlays={overlays} />
        {footer}
      </div>
    );
  }
  if (page.columns) return <OrderColumns page={page} overlays={overlays} />;
  const live = page.items.filter((k) => k.state !== "done").sort(byState);
  const done = page.items.filter((k) => k.state === "done");
  return (
    <div className="flex flex-col gap-3">
      {(live.length > 0 || page.ghosts.length > 0) && (
        <ul
          role="list"
          className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,290px),1fr))] gap-3"
        >
          {live.map((k) => (
            <NodeTile key={k.ref} node={k} overlays={overlays} />
          ))}
          {page.ghosts.map((k) => (
            <NodeTile key={k.ref} node={k} compact overlays={overlays} />
          ))}
        </ul>
      )}
      <DoneLine done={done} overlays={overlays} />
    </div>
  );
}
