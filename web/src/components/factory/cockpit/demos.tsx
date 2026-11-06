// Demos, one of the cockpit's main places: what the factory recorded of what
// it built, newest first. A demo is a card (poster, title, verdict, what did
// not pass, where it sits, length) that plays in place with one click; the
// player scrolls into view above any sticky bar.

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRightIcon, PlayIcon, XIcon } from "lucide-react";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { DemoStatusTag } from "../board/DemoThumb";
import { useFactoryNow } from "../factoryClock";
import { ago } from "../v3/envWords";
import { NODE_STALE_MS, nodeQueryKeys } from "../v3/nodeQueries";
import { PRODUCT_REF, nodeHref } from "../v3/nodeRoutes";
import { useNodeSource } from "../v3/nodeSource";
import type { DemoRef } from "../v3/nodeTypes";
import { NOTE_TONE, ProofPlayer, ProofThumb } from "../v3/proof/ProofRow";
import { notPassed, playLabel } from "../v3/proof/proofModel";

/** How many demos the Demos page reads (the server's page maximum). */
export const DEMOS_READ = 100;

export const factoryDemosKey = (fid: string, kind: string) =>
  [...nodeQueryKeys.node(fid, kind as "live", PRODUCT_REF), "demos", "all"] as const;

/** Every demo of the product, newest first (one read of up to 100). */
export function useFactoryDemos(fid: string) {
  const source = useNodeSource();
  return useQuery({
    queryKey: factoryDemosKey(fid, source?.kind ?? "pending"),
    queryFn: async ({ signal }) =>
      (await source!.demos!(fid, PRODUCT_REF, signal, undefined, DEMOS_READ)) ?? {
        ref: PRODUCT_REF,
        items: [],
        total: 0,
      },
    enabled: Boolean(fid && source?.demos),
    staleTime: NODE_STALE_MS * 6,
  });
}

/** "Agent assistant › Assistant core" (the project alone when it stands for its feature). */
export function DemoPlace({ demo, fid }: { demo: DemoRef; fid: string }) {
  const { feature, project } = demo;
  const parts = [feature, project && project.ref !== feature?.ref ? project : null].filter(
    (p): p is { ref: string; title: string } => Boolean(p),
  );
  if (!parts.length) return null;
  return (
    <span className="inline-flex min-w-0 items-center gap-1">
      {parts.map((p, i) => (
        <span key={p.ref} className="inline-flex min-w-0 items-center gap-1">
          {i > 0 && <ChevronRightIcon aria-hidden className="size-3 shrink-0 opacity-60" />}
          <Link
            to={nodeHref(fid, p.ref)}
            componentId="factory.cockpit.demo.place"
            onClick={(e) => e.stopPropagation()}
            className="truncate hover:text-foreground hover:underline"
          >
            {p.title}
          </Link>
        </span>
      ))}
    </span>
  );
}

/**
 * One demo as a card: the poster, its title and verdict, what did not pass,
 * where it sits and how long it runs. One click (poster or title) plays it
 * here; a second closes it.
 */
export function DemoCard({
  demo,
  fid,
  open: first = false,
  compact = false,
}: {
  demo: DemoRef;
  fid: string;
  open?: boolean;
  /** Home's shorter row: a smaller poster, no place line. */
  compact?: boolean;
}) {
  const [open, setOpen] = useState(first);
  const now = useFactoryNow();
  const when = ago(demo.at, now);
  const toggle = () => setOpen((on) => !on);
  return (
    <li
      data-testid="cockpit-demo"
      data-demo={demo.id}
      data-verdict={demo.verdict}
      className={cn("border-t border-border first:border-t-0", open && "bg-muted/30")}
    >
      <div className={cn("flex min-w-0 items-start gap-3", compact ? "px-3 py-2" : "px-3 py-3")}>
        <ProofThumb demo={demo} onPlay={toggle} className={compact ? "w-20" : "w-28 sm:w-40"} />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <button
            type="button"
            onClick={toggle}
            aria-expanded={open}
            aria-label={open ? `Close ${demo.title}` : `${playLabel(demo)}: ${demo.title}`}
            data-testid="cockpit-demo-play"
            title={demo.full_title ?? demo.title}
            className="flex min-w-0 items-center gap-2 self-start text-left outline-none hover:text-status-blue-fg focus-visible:underline"
          >
            <DemoStatusTag status={demo.verdict} />
            <span
              className={cn(
                "min-w-0 truncate font-medium",
                compact ? "text-[13.5px]" : "text-[14.5px]",
              )}
            >
              {demo.title}
            </span>
            {open ? (
              <XIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
            ) : (
              <PlayIcon
                aria-hidden
                className="size-3 shrink-0 fill-current text-muted-foreground"
              />
            )}
          </button>
          {notPassed(demo.verdict) && demo.partial_note && (
            <p
              data-testid="cockpit-demo-note"
              className={cn("text-[13px] leading-snug", NOTE_TONE[demo.verdict])}
            >
              {demo.partial_note}
            </p>
          )}
          <p className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-[12.5px] text-muted-foreground">
            {!compact && <DemoPlace demo={demo} fid={fid} />}
            {!compact && (demo.feature || demo.project) && (
              <span aria-hidden className="text-foreground-tertiary">
                ·
              </span>
            )}
            {demo.duration && <span className="font-mono tabular-nums">{demo.duration}</span>}
            {when && (
              <>
                <span aria-hidden className="text-foreground-tertiary">
                  ·
                </span>
                <span>{when}</span>
              </>
            )}
            {demo.stale && (
              <>
                <span aria-hidden className="text-foreground-tertiary">
                  ·
                </span>
                <span className="text-foreground-tertiary">an earlier version</span>
              </>
            )}
          </p>
        </div>
      </div>
      {open && (
        <div className="px-3 pb-3">
          <ProofPlayer demo={demo} />
        </div>
      )}
    </li>
  );
}

export function DemoListSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <ul
      aria-busy="true"
      aria-label="Loading demos"
      className="overflow-hidden rounded-xl ring-1 ring-border"
    >
      {Array.from({ length: rows }, (_, i) => (
        <li key={i} className="flex gap-3 border-t border-border px-3 py-3 first:border-t-0">
          <div className="aspect-video w-28 shrink-0 animate-pulse rounded-md bg-muted sm:w-40" />
          <div className="flex flex-1 flex-col gap-2 pt-1">
            <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
            <div className="h-3 w-1/3 animate-pulse rounded bg-muted" />
          </div>
        </li>
      ))}
    </ul>
  );
}
