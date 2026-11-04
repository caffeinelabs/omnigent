// The Demos sheet (`?peek=demos&at=<ref>`, `&demo=<id>` to play one): every
// recorded demo of a node's subtree, newest first, one line each (verdict,
// title, length, PR), each playing in place. "n more demos" on a page and the
// "▶" chip on Home's feature rows open it.

import { useState } from "react";
import { PlayIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { DemoStatusTag } from "../../board/DemoThumb";
import { useFactoryNow } from "../../factoryClock";
import { Peek } from "../../ui/Peek";
import { nodeName } from "../calm/calmModel";
import { ago } from "../envWords";
import { useNodeActions } from "../NodeContext";
import { useNode } from "../nodeQueries";
import type { DemoChip, DemoRef, NodeSummary } from "../nodeTypes";
import { DemoWords, NOTE_TONE, ProofPlayer, ProofThumb } from "./ProofRow";
import { chipLabel, notPassed, playLabel, useNodeDemos } from "./proofModel";

/** One demo: verdict, title, length and PR on one line; a click plays it in place. */
export function DemoLine({ demo, open: first = false }: { demo: DemoRef; open?: boolean }) {
  const [open, setOpen] = useState(first);
  const now = useFactoryNow();
  const toggle = () => setOpen((on) => !on);
  const when = ago(demo.at, now);
  return (
    <li
      data-testid="v3-demo-line"
      data-demo={demo.id}
      data-verdict={demo.verdict}
      className="border-t border-border px-3 py-2 first:border-t-0"
    >
      <div className="flex min-w-0 items-center gap-3">
        <ProofThumb demo={demo} onPlay={toggle} className="w-20" />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex min-w-0 items-center gap-2 text-[13.5px]">
            <DemoStatusTag status={demo.verdict} />{" "}
            <button
              type="button"
              aria-expanded={open}
              aria-label={open ? `Close ${demo.title}` : `${playLabel(demo)}: ${demo.title}`}
              onClick={toggle}
              title={when ? `Recorded ${when}` : undefined}
              className="min-w-0 truncate text-left outline-none hover:text-status-blue-fg focus-visible:underline"
            >
              {demo.title}
            </button>{" "}
            {demo.duration && (
              <span className="shrink-0 font-mono text-[12.5px] text-muted-foreground tabular-nums">
                {demo.duration}
              </span>
            )}
          </div>{" "}
          {(demo.pr_tag || demo.pr_number || demo.stale) && (
            <p className="truncate text-[12px] text-muted-foreground">
              <DemoWords demo={demo} title={false} />
            </p>
          )}
          {notPassed(demo.verdict) && demo.partial_note && (
            <p
              data-testid="v3-demo-note"
              className={cn("text-[12.8px] leading-snug", NOTE_TONE[demo.verdict])}
            >
              {demo.partial_note}
            </p>
          )}
        </div>
      </div>
      {open && (
        <div className="mt-2">
          <ProofPlayer demo={demo} />
        </div>
      )}
    </li>
  );
}

export function DemosSheet({
  factoryId,
  open,
  at,
  play,
  onClose,
}: {
  factoryId: string;
  open: boolean;
  at: string;
  /** The demo to play as the sheet opens (a feature row's "▶"). */
  play?: string | null;
  onClose: () => void;
}) {
  const page = useNode(open ? at : "", factoryId);
  const list = useNodeDemos(at, open, factoryId);
  const name = page.data ? nodeName(page.data.node) : null;
  const total = list.total;
  return (
    <Peek
      open={open}
      onOpenChange={(next) => !next && onClose()}
      wide
      title={name ? `Demos of ${name}` : "Demos"}
      description={total ? `${total} recorded, newest first` : undefined}
    >
      {list.error ? (
        <p role="alert" className="text-sm text-status-red-fg">
          The demos could not be read:{" "}
          {list.error instanceof Error ? list.error.message : String(list.error)}
        </p>
      ) : list.pending ? (
        <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-muted" />
      ) : list.missing ? (
        <p className="text-sm text-muted-foreground">
          No node has the ref <span className="font-mono">{at}</span> here.
        </p>
      ) : !list.items.length ? (
        <p className="text-[13.5px] text-muted-foreground">No demo is recorded here yet.</p>
      ) : (
        <div className="flex flex-col gap-2" data-testid="v3-demos-sheet">
          <ul role="list" className="overflow-hidden rounded-xl bg-card-solid ring-1 ring-border">
            {list.items.map((demo) => (
              <DemoLine key={demo.id} demo={demo} open={demo.id === play} />
            ))}
          </ul>
          {list.more && (
            <button
              type="button"
              data-testid="v3-demos-more"
              disabled={list.loading}
              onClick={list.more}
              className="self-start rounded-[7px] border border-border-strong bg-card-solid px-2 text-xs font-semibold disabled:opacity-60"
            >
              {list.loading ? "Reading…" : "Older demos"}
            </button>
          )}
        </div>
      )}
    </Peek>
  );
}

/** In a narrow row (a phone, or a column beside the chat) the chip says its verdict by colour alone. */
const NARROW_TONE: Record<DemoChip["verdict"], string> = {
  PASS: "",
  PARTIAL: "@max-md:border-status-yellow/60 @max-md:text-status-yellow-fg",
  FAIL: "@max-md:border-status-red/60 @max-md:text-status-red-fg",
  ERROR: "@max-md:border-status-red/60 @max-md:text-status-red-fg",
};

/**
 * A feature row's "▶" (Home links each feature's newest demo): opens the Demos
 * sheet playing it. "▶ PARTIAL 0:42"; in a narrow row "▶ 0:42" in the
 * verdict's colour, so the feature's name beside it stays whole.
 */
export function DemoChipButton({ node, name }: { node: NodeSummary; name: string }) {
  const { onSheet } = useNodeActions();
  const chip: DemoChip | undefined = node.demo;
  if (!chip || !onSheet) return null;
  const failed = notPassed(chip.verdict);
  return (
    <button
      type="button"
      data-testid="v3-demo-chip"
      data-verdict={chip.verdict}
      aria-label={chipLabel(chip, name)}
      title={chipLabel(chip, name)}
      onClick={() => onSheet("demos", node.ref, chip.id)}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border border-border-strong bg-card-solid px-1.5 py-px text-[11.5px] text-muted-foreground outline-none hover:border-status-blue hover:text-foreground focus-visible:ring-2 focus-visible:ring-status-blue",
        NARROW_TONE[chip.verdict],
      )}
    >
      <PlayIcon aria-hidden className="size-2.5 fill-current" />
      {failed && (
        <span
          data-testid="v3-demo-chip-verdict"
          className={cn("font-semibold @max-md:sr-only", NOTE_TONE[chip.verdict])}
        >
          {chip.verdict}{" "}
        </span>
      )}
      {chip.duration && <span className="font-mono tabular-nums">{chip.duration}</span>}
    </button>
  );
}
