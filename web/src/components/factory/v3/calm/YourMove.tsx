// "Your move" (calm rule 1): what this viewer must do here, at most three rows
// ranked by what each unblocks, the rest behind "+n more", or one quiet line
// when nothing needs them. A row is the ask in words, what it unblocks and one
// answer button (filled only on the first row). A click on its words peeks at
// the ask: its full text and its other answers. Enter never answers. Under the
// rows, one quiet line per initiative of several waves says where its waves are.

import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { swallowEnter } from "../DecisionButtons";
import { useNodeActions } from "../NodeContext";
import { NodeLink } from "../nodeParts";
import { AMBER } from "../nodeTokens";
import type { NodePage, WaveLine } from "../nodeTypes";
import { inProductTree } from "../lenses/lensModel";
import { useNeedsYou } from "../nodeSteer";
import {
  MOVE_ROWS,
  type MoveRow,
  type MoveView,
  askerName,
  deriveMoves,
  moveView,
  youByLevel,
} from "./calmModel";

/**
 * The page's Your move (the server's, else derived from its status line and
 * Needs you) and the same asks counted under each level below (the strip's
 * "n you"), so every count on the page is one set of asks.
 */
export function useYourMove(
  page: NodePage,
  shown: string | null,
  factoryId?: string,
): { view: MoveView; you: Map<string, number> | null } {
  const tree = inProductTree(page) ? "product" : "factory";
  const needs = useNeedsYou(tree, true, factoryId);
  return useMemo(
    () => ({
      view: moveView(page, deriveMoves(page, needs.data, shown), MOVE_ROWS, needs.data),
      you: youByLevel(page, needs.data),
    }),
    [needs.data, page, shown],
  );
}

const BTN =
  "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-[7px] border px-2.5 py-0.5 text-[12.5px] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-status-blue focus-visible:ring-offset-1 focus-visible:ring-offset-background";
const QUIET = "border-border-strong bg-card-solid text-foreground hover:border-status-blue";

/** The row's one button: its answer, a link out (a merge), or "Answer" to open the ask. */
function Answer({ row, primary, onOpen }: { row: MoveRow; primary: boolean; onOpen: () => void }) {
  const { onAnswer } = useNodeActions();
  const look = primary ? AMBER.button : QUIET;
  const p = row.primary;
  const tag = { "data-testid": "v3-answer", "data-decision": row.id };
  if (p?.url) {
    return (
      <a {...tag} href={p.url} target="_blank" rel="noopener noreferrer" className={cn(BTN, look)}>
        {p.label} ↗
      </a>
    );
  }
  const action = p?.action;
  return (
    <button
      {...tag}
      type="button"
      onKeyDown={swallowEnter}
      onClick={() => (action ? onAnswer?.(row.decision, action) : onOpen())}
      className={cn(BTN, look)}
    >
      {p?.label ?? "Answer"}
    </button>
  );
}

/** The ask in full, under its row: what was asked, by whom, and its other answers. */
function Peek({ row }: { row: MoveRow }) {
  const { onAnswer, onOpenDecision, factoryId } = useNodeActions();
  const d = row.decision;
  const text = [d.title, d.text].filter(Boolean).join(" · ");
  const asker = askerName(d.asker, factoryId);
  const asked = [
    asker && `asked by ${asker}`,
    d.age && `${d.age} ago`,
    d.expires && `expires in ${d.expires}`,
  ]
    .filter(Boolean)
    .join(" · ");
  const others = d.external_url ? [] : d.actions.filter((a) => a.id !== row.primary?.action?.id);
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

/** "· unblocks Agent assistant", "· blocks nothing", or nothing when it names nothing it frees. */
function unblocksWords(row: MoveRow): string | null {
  if (row.quiet) return "blocks nothing";
  return row.unblocks ? `unblocks ${row.unblocks}` : null;
}

/** One ask: its words (a click peeks at it in full), what it unblocks and its one answer. */
export function MoveRowItem({
  row,
  primary,
  testId = "v3-move-row",
}: {
  row: MoveRow;
  /** The one filled answer of its list (the first row). */
  primary: boolean;
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const quiet = row.quiet;
  const frees = unblocksWords(row);
  return (
    <li
      data-testid={testId}
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
          {row.name && (
            <span data-testid="v3-move-name" className="text-foreground">
              {" "}
              · {row.name}
            </span>
          )}
          {frees && (
            <span data-testid="v3-move-unblocks" className="text-muted-foreground">
              {" "}
              · {frees}
            </span>
          )}
        </button>
        <Answer row={row} primary={primary} onOpen={() => setOpen(true)} />
      </div>
      {open && <Peek row={row} />}
    </li>
  );
}

/** One quiet line per initiative of several waves: "Agent assistant: wave 1 done · wave 2 started". */
function WaveLines({ waves }: { waves?: WaveLine[] }) {
  if (!waves?.length) return null;
  return (
    <ul
      role="list"
      data-testid="v3-move-waves"
      className="mt-1.5 flex flex-col gap-0.5 px-0.5 text-[12.5px] text-muted-foreground"
    >
      {waves.map((wave) => (
        <li key={wave.ref} data-testid="v3-move-wave" data-ref={wave.ref}>
          <NodeLink nodeRef={wave.ref} className="hover:text-status-blue-fg hover:underline">
            {wave.line}
          </NodeLink>
        </li>
      ))}
    </ul>
  );
}

export function YourMove({ view }: { view: MoveView }) {
  const { onSheet } = useNodeActions();
  const [more, setMore] = useState(false);
  if (!view.rows.length) {
    const waves = view.waves?.length ? view.waves : null;
    const none = (
      <p
        data-testid="v3-move-none"
        className={cn(
          "flex items-center gap-2 text-[13px] text-foreground-tertiary",
          !waves && "mb-4",
        )}
      >
        <span aria-hidden className="text-status-green-fg">
          ✓
        </span>
        Nothing needs you
      </p>
    );
    if (!waves) return none;
    return (
      <div className="mb-4">
        {none}
        <WaveLines waves={waves} />
      </div>
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
          <MoveRowItem key={row.id} row={row} primary={i === 0} />
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
      <WaveLines waves={view.waves} />
    </section>
  );
}
