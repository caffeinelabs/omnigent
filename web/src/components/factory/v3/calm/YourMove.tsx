// "Your move" (calm rules 1, 8 and 9): what this viewer must do here, at most
// three rows ranked by what each unblocks, the rest behind "+n more", or one
// quiet line when nothing needs them. A row says in one sentence who wants
// what and what a yes does, with its answers named by their outcome ("Yes,
// allow it", "No"; one filled, on the first row only). A page lists only its
// own node's asks and its children's; deeper ones are one line per child, and
// Home (the inbox) names where each ask lives. A click on the sentence peeks
// at the ask: its original wording, its id, who asked and its other answers.
// Enter never answers. Under the rows, one quiet line per initiative of
// several waves says where its waves are.

import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { swallowEnter } from "../DecisionButtons";
import { useNodeActions } from "../NodeContext";
import { NodeLink } from "../nodeParts";
import { AMBER } from "../nodeTokens";
import type { MoveBelow, NodeLink as Link, NodePage, WaveLine } from "../nodeTypes";
import { inProductTree } from "../lenses/lensModel";
import { useNeedsYou } from "../nodeSteer";
import {
  MOVE_ROWS,
  type MoveRow,
  type MoveView,
  type RowAnswer,
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

/** One answer: a button that answers with its option, or a link out (a merge). */
function AnswerButton({
  row,
  answer,
  filled,
  small = false,
}: {
  row: MoveRow;
  answer: RowAnswer;
  /** The one filled answer of the list (the first row's primary). */
  filled: boolean;
  small?: boolean;
}) {
  const { onAnswer } = useNodeActions();
  const look = cn(BTN, filled ? AMBER.button : QUIET, small && "px-2 text-xs font-medium");
  const tag = {
    // The primary is the row's one answer the calm checks count; the others are options.
    "data-testid": answer.primary ? "v3-answer" : "v3-move-option",
    "data-decision": row.id,
    "data-option": answer.action?.id,
  };
  if (answer.url) {
    return (
      <a {...tag} href={answer.url} target="_blank" rel="noopener noreferrer" className={look}>
        {answer.label} ↗
      </a>
    );
  }
  const action = answer.action;
  return (
    <button
      {...tag}
      type="button"
      onKeyDown={swallowEnter}
      onClick={() => action && onAnswer?.(row.decision, action)}
      className={look}
    >
      {answer.label}
    </button>
  );
}

/** The row's answers (the rest wait in its peek), or "Answer" to open the ask when it has none. */
function Answers({ row, primary, onOpen }: { row: MoveRow; primary: boolean; onOpen: () => void }) {
  const shown = row.answers.filter((a) => !a.more);
  if (!shown.length) {
    return (
      <button
        type="button"
        data-testid="v3-answer"
        data-decision={row.id}
        onKeyDown={swallowEnter}
        onClick={onOpen}
        className={cn(BTN, primary ? AMBER.button : QUIET)}
      >
        Answer
      </button>
    );
  }
  return (
    <div data-testid="v3-move-answers" className="flex shrink-0 flex-wrap items-center gap-1.5">
      {shown.map((answer) => (
        <AnswerButton
          key={`${answer.action?.id ?? answer.url ?? answer.label}`}
          row={row}
          answer={answer}
          filled={primary && Boolean(answer.primary)}
        />
      ))}
    </div>
  );
}

/** The ask in full, under its row: its original words, its id, who asked, its other answers. */
function Peek({ row }: { row: MoveRow }) {
  const { onAnswer, onOpenDecision, factoryId } = useNodeActions();
  const d = row.decision;
  const text = [d.title, d.text].filter(Boolean).join(" · ");
  const asker = askerName(d.asker, factoryId);
  const code = [d.id.startsWith("merge:") ? null : d.id, d.subject].filter(Boolean).join(" · ");
  const asked = [
    code,
    asker && `asked by ${asker}`,
    d.age && `${d.age} ago`,
    d.expires && `expires in ${d.expires}`,
  ]
    .filter(Boolean)
    .join(" · ");
  // A newer server's answers that wait here; an older one's other answers by their own words.
  const others: RowAnswer[] = row.plain
    ? row.answers.filter((a) => a.more)
    : d.external_url
      ? []
      : d.actions
          .filter((a) => a.id !== row.primary?.action?.id)
          .map((a) => ({ label: a.label, action: a }));
  return (
    <div
      data-testid="v3-move-peek"
      className="flex flex-col gap-1.5 pt-1 pb-0.5 pl-[18px] text-[12.8px] text-muted-foreground"
    >
      {text && (
        <p className="max-w-[72ch]" data-testid="v3-move-original">
          {row.plain ? <span className="text-foreground-tertiary">Asked as: </span> : null}
          {text}
        </p>
      )}
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
          {others.map((answer) =>
            row.plain ? (
              <AnswerButton
                key={answer.action?.id ?? answer.label}
                row={row}
                answer={{ ...answer, primary: false }}
                filled={false}
                small
              />
            ) : (
              <button
                key={answer.action?.id ?? answer.label}
                type="button"
                data-testid="v3-move-option"
                onKeyDown={swallowEnter}
                onClick={() => answer.action && onAnswer?.(d, answer.action)}
                className={cn(BTN, QUIET, "px-2 text-xs font-medium")}
              >
                {answer.label}
              </button>
            ),
          )}
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

/** Whether *text* names *name* (case aside). */
function says(text: string, name: string): boolean {
  return text.toLowerCase().includes(name.toLowerCase());
}

/** "unblocks Agent assistant", "blocks nothing" (an older server's quiet ask), or nothing. */
function unblocksWords(row: MoveRow): string | null {
  if (row.quiet) return row.plain ? null : "blocks nothing";
  return row.unblocks ? `unblocks ${row.unblocks}` : null;
}

/** One ask: its sentence (a click peeks at it in full), its answers, where it lives and what it unblocks. */
export function MoveRowItem({
  row,
  primary,
  testId = "v3-move-row",
  inbox = false,
}: {
  row: MoveRow;
  /** The one filled answer of its list (the first row). */
  primary: boolean;
  testId?: string;
  /** On Home (and the Needs you sheet): the row names where the ask lives. */
  inbox?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const quiet = row.quiet;
  const frees = unblocksWords(row);
  // Home names where each ask lives, unless its sentence says it already.
  const where = inbox && row.where && !says(row.text, row.where.title) ? row.where : null;
  // What the sentence names is no news ("Merge Build tier drafts 3 · unblocks Build tier drafts 3").
  const freesShown = row.unblocks && says(row.text, row.unblocks) ? null : frees;
  const meta = row.plain && (where || freesShown);
  return (
    <li
      data-testid={testId}
      data-decision={row.id}
      data-ask={row.text}
      className="border-t border-border px-3 py-2 first:border-t-0"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1.5">
        <div className="flex min-w-0 flex-[1_1_18rem] items-start gap-2.5">
          <span
            aria-hidden
            className={cn(
              "mt-[7px] size-2 shrink-0 rounded-full",
              quiet ? "bg-foreground-tertiary/50" : "bg-status-yellow",
            )}
          />
          <div className="min-w-0 flex-1">
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setOpen((o) => !o)}
              className="text-left text-[13.5px] leading-snug hover:text-status-blue-fg"
              title={open ? "Hide the ask" : "Show the ask in full"}
            >
              <span
                data-testid="v3-move-text"
                className={cn("text-foreground", row.plain ? "font-medium" : "font-semibold")}
              >
                {row.text}
              </span>
              {row.name && (
                <span data-testid="v3-move-name" className="text-foreground">
                  {" "}
                  · {row.name}
                </span>
              )}
              {!row.plain && frees && (
                <span data-testid="v3-move-unblocks" className="text-muted-foreground">
                  {" "}
                  · {frees}
                </span>
              )}
            </button>
            {meta && (
              <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
                {where && (
                  <span data-testid="v3-move-where">
                    in{" "}
                    <NodeLink
                      nodeRef={where.ref}
                      className="hover:text-status-blue-fg hover:underline"
                    >
                      {where.title}
                    </NodeLink>
                  </span>
                )}
                {where && freesShown && <span aria-hidden>·</span>}
                {freesShown && <span data-testid="v3-move-unblocks">{freesShown}</span>}
              </p>
            )}
          </div>
        </div>
        <div className="ml-auto flex shrink-0 pl-[18px]">
          <Answers row={row} primary={primary} onOpen={() => setOpen(true)} />
        </div>
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

/** "Assistant core · 2 need you →": a child with asks deeper down, one line. */
function BelowLine({ below }: { below: MoveBelow }) {
  return (
    <li data-testid="v3-move-below" data-ref={below.ref}>
      <NodeLink
        nodeRef={below.ref}
        className="group flex items-center gap-2.5 px-3 py-1.5 text-[13px] hover:text-status-blue-fg"
      >
        <span aria-hidden className="size-2 shrink-0 rounded-full bg-status-yellow/60" />
        <span className="min-w-0 truncate font-medium text-foreground group-hover:text-status-blue-fg">
          {below.title}
        </span>
        <span className="shrink-0 text-muted-foreground">
          {" "}
          · {below.count} need{below.count === 1 ? "s" : ""} you →
        </span>
      </NodeLink>
    </li>
  );
}

/** "Held for an ask in Assistant core →": what holds this node lives on another page. */
function HeldBy({ link, className }: { link: Link; className?: string }) {
  return (
    <p data-testid="v3-move-held" className={cn("text-[13px] text-foreground-tertiary", className)}>
      Held for an ask in{" "}
      <NodeLink nodeRef={link.ref} className="text-status-blue-fg hover:underline">
        {link.title} →
      </NodeLink>
    </p>
  );
}

export function YourMove({ view, inbox = false }: { view: MoveView; inbox?: boolean }) {
  const { onSheet } = useNodeActions();
  const [more, setMore] = useState(false);
  if (!view.rows.length && !view.below.length) {
    const waves = view.waves?.length ? view.waves : null;
    const none = view.heldBy ? (
      <HeldBy link={view.heldBy} className={cn(!waves && "mb-4")} />
    ) : (
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
          <MoveRowItem key={row.id} row={row} primary={i === 0} inbox={inbox} />
        ))}
        {view.below.length > 0 && (
          <li className={cn("py-0.5", rows.length > 0 && "border-t border-border")}>
            <ul role="list" aria-label="Asks further down">
              {view.below.map((b) => (
                <BelowLine key={b.ref} below={b} />
              ))}
            </ul>
          </li>
        )}
      </ul>
      {(unseen > 0 || view.belowMore > 0) && (
        <button
          type="button"
          data-testid="v3-move-more"
          onClick={() =>
            unseen > 0 && !more && view.rest.length ? setMore(true) : onSheet?.("needs")
          }
          className="mt-1.5 px-0.5 text-[12.5px] text-status-blue-fg hover:underline"
        >
          +{unseen > 0 ? unseen : view.belowMore} more
        </button>
      )}
      <WaveLines waves={view.waves} />
    </section>
  );
}
