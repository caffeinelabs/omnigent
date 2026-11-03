// One line per node that says whether it moves and, if not, the path to the
// one person or thing that holds it. The header shows the full path with its
// answer and "why?"; tiles and rows show only its end, and say "the ask above"
// instead of repeating the header's decision (no second set of buttons).

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { DecisionButtons } from "./DecisionButtons";
import { useNodeActions } from "./NodeContext";
import { NodeLink } from "./nodeParts";
import { AMBER, labelClass } from "./nodeTokens";
import type { StatusLine as Line } from "./nodeTypes";
import { type StatusPart, endsAbove, hasWhy, statusParts, statusText } from "./statusModel";

const PATH_LINK =
  "text-foreground decoration-foreground-tertiary decoration-dotted underline-offset-[3px] underline hover:text-status-blue-fg hover:decoration-status-blue";

function Part({ part }: { part: StatusPart }): ReactNode {
  switch (part.t) {
    case "sep":
      return (
        <span aria-hidden className="mx-1.5 font-semibold text-foreground-tertiary">
          {part.text}
        </span>
      );
    case "link":
      return (
        <NodeLink nodeRef={part.ref} className={PATH_LINK} title={part.reason}>
          {part.text}
        </NodeLink>
      );
    case "person":
      return <span className={cn("font-semibold", AMBER.text)}>{part.text}</span>;
    case "muted":
      return <span className="text-foreground-tertiary">{part.text}</span>;
    default:
      return part.text;
  }
}

/** The line's words: a coloured label, then the path (links to nodes, the person in amber). */
export function StatusWords({
  line,
  nodeRef,
  compact,
  className,
}: {
  line: Line;
  nodeRef: string;
  compact?: boolean;
  className?: string;
}) {
  const { aboveId } = useNodeActions();
  const parts = statusParts(line, { compact, nodeRef, aboveId });
  return (
    <span className={className} data-testid="v3-status-words">
      <span className={cn("font-semibold whitespace-nowrap", labelClass(line.kind))}>
        {line.label}
      </span>{" "}
      {parts.map((part) => (
        <Part key={part.key} part={part} />
      ))}
    </span>
  );
}

/** The header's status line: full path, its answer, and "why? ▾" when there is more to say. */
export function StatusBox({
  line,
  nodeRef,
  whyOpen,
  onWhy,
}: {
  line: Line;
  nodeRef: string;
  whyOpen: boolean;
  onWhy: () => void;
}) {
  const needs = line.kind === "needs";
  return (
    <div
      role="status"
      aria-label={statusText(line, { nodeRef })}
      data-testid="v3-status"
      data-kind={line.kind}
      className={cn(
        "flex flex-wrap items-center gap-2 rounded-[9px] py-[7px] pr-2 pl-3 leading-[1.55]",
        needs ? cn(AMBER.surface, AMBER.ring) : "bg-card-solid ring-1 ring-border",
      )}
    >
      <span className="min-w-0 flex-1 max-md:basis-full">
        <StatusWords line={line} nodeRef={nodeRef} />
        {hasWhy(line) && (
          <button
            type="button"
            onClick={onWhy}
            aria-expanded={whyOpen}
            data-testid="v3-why-toggle"
            className="px-1 text-[12.5px] whitespace-nowrap text-foreground-tertiary hover:text-status-blue-fg"
          >
            {whyOpen ? "hide why ▴" : "why? ▾"}
          </button>
        )}
      </span>
      {line.decision && line.ends === "person" && (
        <DecisionButtons decision={line.decision} variant="header" />
      )}
    </div>
  );
}

/**
 * A tile's or row's line: the end of the path. The answer shows only on a
 * tile whose node itself needs a person, and never for the header's ask.
 */
export function CompactStatus({
  line,
  nodeRef,
  answer,
  className,
}: {
  line: Line;
  nodeRef: string;
  /** Show the answer button here (tiles of nodes that need you). */
  answer?: boolean;
  className?: string;
}) {
  const { aboveId } = useNodeActions();
  const d = line.decision;
  const showAnswer = answer && d && !d.blocks_nothing && !endsAbove(line, aboveId);
  return (
    <div className={cn("text-[12.8px] leading-[1.45] text-muted-foreground", className)}>
      <StatusWords line={line} nodeRef={nodeRef} compact />
      {showAnswer && (
        <span className="ml-1.5 inline-flex align-middle">
          <DecisionButtons decision={d} variant="tile" />
        </span>
      )}
    </div>
  );
}
