// "Your move" (calm rule 1): what this viewer must do here, at most three rows
// ranked by what each unblocks, the rest behind "+n more", or one quiet line
// when nothing needs them. A row is the ask in words, what it unblocks and one
// answer button (filled only on the first row). A click on its words peeks at
// the ask: its full text and its other answers. Enter never answers.

import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { swallowEnter } from "../DecisionButtons";
import { useNodeActions } from "../NodeContext";
import { NodeLink } from "../nodeParts";
import { AMBER } from "../nodeTokens";
import type { DecisionRef, NodePage } from "../nodeTypes";
import { inProductTree } from "../lenses/lensModel";
import { useNeedsYou } from "../nodeSteer";
import { type MoveRow, type MoveView, deriveMoves, moveView } from "./calmModel";

/** The page's Your move: the server's, else derived from its status line and Needs you. */
export function useYourMove(page: NodePage, shown: string | null, factoryId?: string): MoveView {
  const tree = inProductTree(page) ? "product" : "factory";
  const needs = useNeedsYou(tree, true, factoryId);
  return useMemo(
    () => moveView(page, deriveMoves(page, needs.data, shown)),
    [needs.data, page, shown],
  );
}

const BTN =
  "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-[7px] border px-2.5 py-0.5 text-[12.5px] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-status-blue focus-visible:ring-offset-1 focus-visible:ring-offset-background";
const QUIET = "border-border-strong bg-card-solid text-foreground hover:border-status-blue";

function Answer({ decision, primary }: { decision: DecisionRef; primary: boolean }) {
  const { onAnswer } = useNodeActions();
  const first = decision.actions[0];
  const look = primary ? AMBER.button : QUIET;
  if (decision.external_url) {
    return (
      <a
        href={decision.external_url}
        target="_blank"
        rel="noopener noreferrer"
        data-testid="v3-answer"
        data-decision={decision.id}
        className={cn(BTN, look)}
      >
        {first?.label ?? decision.verb} ↗
      </a>
    );
  }
  if (!first) return null;
  return (
    <button
      type="button"
      data-testid="v3-answer"
      data-decision={decision.id}
      onKeyDown={swallowEnter}
      onClick={() => onAnswer?.(decision, first)}
      className={cn(BTN, look)}
    >
      {first.label}
    </button>
  );
}

/** The ask in full, under its row: what was asked, by whom, and its other answers. */
function Peek({ row }: { row: MoveRow }) {
  const { onAnswer, onOpenDecision } = useNodeActions();
  const d = row.decision;
  const text = [d.title, d.text].filter(Boolean).join(" · ");
  const asked = [
    d.asker && `asked by ${d.asker}`,
    d.age && `${d.age} ago`,
    d.expires && `expires in ${d.expires}`,
  ]
    .filter(Boolean)
    .join(" · ");
  const others = d.external_url ? [] : d.actions.slice(1);
  return (
    <div
      data-testid="v3-move-peek"
      className="flex flex-col gap-1.5 pt-1 pb-0.5 pl-5 text-[12.8px] text-muted-foreground"
    >
      {text && <p className="max-w-[72ch]">{text}</p>}
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-foreground-tertiary">
        {asked && <span>{asked}</span>}
        {row.at && (
          <NodeLink nodeRef={row.at} className="text-status-blue-fg hover:underline">
            Open where it is asked
          </NodeLink>
        )}
      </p>
      {(others.length > 0 || onOpenDecision) && (
        <div className="flex flex-wrap gap-1.5">
          {others.map((action) => (
            <button
              key={action.id}
              type="button"
              data-testid="v3-move-option"
              onKeyDown={swallowEnter}
              onClick={() => onAnswer?.(d, action)}
              className={cn(BTN, QUIET, "px-2 text-xs font-medium")}
            >
              {action.label}
            </button>
          ))}
          {onOpenDecision && (
            <button
              type="button"
              onClick={() => onOpenDecision(d)}
              className={cn(
                BTN,
                "border-dashed border-border-strong px-2 text-xs font-medium text-muted-foreground",
              )}
            >
              All asks
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ row, primary }: { row: MoveRow; primary: boolean }) {
  const [open, setOpen] = useState(false);
  const quiet = row.unblocks == null;
  return (
    <li
      data-testid="v3-move-row"
      data-decision={row.id}
      data-ask={row.text}
      className="border-t border-border px-3 py-2 first:border-t-0"
    >
      <div className="flex min-w-0 items-center gap-2.5">
        <span
          aria-hidden
          className={cn(
            "size-2 shrink-0 rounded-full",
            quiet ? "bg-foreground-tertiary/50" : "bg-status-yellow",
          )}
        />
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="min-w-0 flex-1 text-left text-[13.5px] leading-snug hover:text-status-blue-fg"
          title={open ? "Hide the ask" : "Show the ask in full"}
        >
          <span className="font-semibold text-foreground">{row.text}</span>
          <span className="text-muted-foreground">
            {quiet ? " · blocks nothing" : ` · unblocks ${row.unblocks}`}
          </span>
        </button>
        <Answer decision={row.decision} primary={primary} />
      </div>
      {open && <Peek row={row} />}
    </li>
  );
}

export function YourMove({ view }: { view: MoveView }) {
  const { onSheet } = useNodeActions();
  const [more, setMore] = useState(false);
  if (!view.rows.length) {
    return (
      <p
        data-testid="v3-move-none"
        className="mb-4 flex items-center gap-2 text-[13px] text-foreground-tertiary"
      >
        <span aria-hidden className="text-status-green-fg">
          ✓
        </span>
        Nothing needs you
      </p>
    );
  }
  const rows = more ? [...view.rows, ...view.rest] : view.rows;
  const unseen = view.more - (more ? view.rest.length : 0);
  return (
    <section aria-label="Your move" data-testid="v3-your-move" className="mb-5">
      <h2 className="mb-1.5 px-0.5 text-[11px] tracking-[0.07em] text-foreground-tertiary uppercase">
        Your move
      </h2>
      <ul role="list" className="overflow-hidden rounded-xl bg-card-solid ring-1 ring-border">
        {rows.map((row, i) => (
          <Row key={row.id} row={row} primary={i === 0} />
        ))}
      </ul>
      {unseen > 0 && (
        <button
          type="button"
          data-testid="v3-move-more"
          onClick={() => (!more && view.rest.length ? setMore(true) : onSheet?.("needs"))}
          className="mt-1.5 px-0.5 text-[12.5px] text-status-blue-fg hover:underline"
        >
          +{unseen} more
        </button>
      )}
    </section>
  );
}
