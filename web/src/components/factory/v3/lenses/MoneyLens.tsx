// The Money lens: each child's cost so far against its budget, most first, as
// boxes in one column (a bar per child, never a chart joining them), the
// total, and what the node spent that no child holds.

import { NodeLink, money } from "../nodeParts";
import type { MoneyRow, MoneyView } from "../nodeTypes";

function rowWords(row: MoneyRow): string {
  const parts = [row.budget ? `${money(row.cost)} of ${money(row.budget)}` : money(row.cost)];
  if (row.budget && row.cost > row.budget) parts.push("over budget");
  if (row.burn) parts.push(`$${row.burn.toFixed(2)}/h`);
  return parts.join(" · ");
}

export function MoneyLens({ view }: { view: MoneyView }) {
  if (!view.rows.length) return null;
  const max = Math.max(...view.rows.map((r) => Math.max(r.budget ?? 0, r.cost)), 0.01);
  return (
    <div
      data-testid="v3-money"
      className="flex flex-col rounded-xl bg-card-solid px-3.5 py-2 ring-1 ring-border"
    >
      <ul aria-label="Cost by child" className="flex flex-col">
        {view.rows.map((row) => {
          const track = row.budget ? row.budget / max : 1;
          const fill = Math.min(1, row.cost / (row.budget || max));
          return (
            <li
              key={row.ref}
              data-testid="v3-money-row"
              data-ref={row.ref}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 border-t border-border py-2 first:border-t-0 sm:grid-cols-[minmax(0,200px)_minmax(0,1fr)_auto]"
            >
              <NodeLink
                nodeRef={row.ref}
                roving
                className="min-w-0 overflow-hidden text-[13px] font-medium text-ellipsis whitespace-nowrap text-foreground hover:text-status-blue-fg"
              >
                {row.tag && row.tag !== row.title ? `${row.tag} · ${row.title}` : row.title}
              </NodeLink>
              <span
                aria-hidden
                className="relative col-span-2 h-2 min-w-0 overflow-hidden rounded-full bg-muted max-sm:order-last sm:col-span-1"
                style={{ width: `${(track * 100).toFixed(1)}%` }}
                title={row.budget ? `budget ${money(row.budget)}` : undefined}
              >
                <i
                  className="absolute inset-y-0 left-0 block rounded-full bg-status-blue"
                  style={{ width: `${(fill * 100).toFixed(1)}%` }}
                />
              </span>
              <span className="text-right text-[12.5px] whitespace-nowrap text-muted-foreground tabular-nums">
                {rowWords(row)}
              </span>
            </li>
          );
        })}
        <li
          data-testid="v3-money-total"
          className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 border-t border-border-strong py-2 sm:grid-cols-[minmax(0,200px)_minmax(0,1fr)_auto]"
        >
          <span className="text-[13px] font-semibold">Total</span>
          <span className="max-sm:hidden" />
          <span className="text-right text-[12.5px] font-semibold whitespace-nowrap tabular-nums">
            {view.budget ? `${money(view.total)} of ${money(view.budget)}` : money(view.total)}
          </span>
        </li>
      </ul>
      {view.rest ? (
        <p className="pb-1 text-xs text-foreground-tertiary">
          {money(view.rest)} more was spent here by leads and agents that no child holds.
        </p>
      ) : null}
    </div>
  );
}
