// The answer to an ask, where its path ends. An answer is always a click:
// Enter steps into nodes and never answers (it is swallowed on these buttons).
// An answer made elsewhere (a person merges on GitHub) is a link out.

import type { KeyboardEvent } from "react";
import { cn } from "@/lib/utils";
import { useNodeActions } from "./NodeContext";
import { AMBER } from "./nodeTokens";
import type { DecisionRef } from "./nodeTypes";

const BTN =
  "inline-flex items-center gap-1.5 whitespace-nowrap rounded-[7px] border font-semibold leading-[1.6] outline-none focus-visible:ring-2 focus-visible:ring-status-blue focus-visible:ring-offset-1 focus-visible:ring-offset-background";

/** Enter never answers a decision. */
function swallowEnter(event: KeyboardEvent) {
  if (event.key === "Enter") {
    event.preventDefault();
    event.stopPropagation();
  }
}

export function DecisionButtons({
  decision,
  variant,
}: {
  decision: DecisionRef;
  /** `header`: the answer and "Open …"; `tile`: the answer, small; `ghost`: every option, small. */
  variant: "header" | "tile" | "ghost";
}) {
  const { onAnswer, onOpenDecision } = useNodeActions();
  const small = variant !== "header";
  const size = small ? "px-2 text-xs leading-[1.5]" : "px-2.5 py-0.5 text-[12.5px]";
  const first = decision.actions[0];
  if (decision.external_url) {
    return (
      <a
        href={decision.external_url}
        target="_blank"
        rel="noopener noreferrer"
        data-testid="v3-answer"
        data-decision={decision.id}
        className={cn(BTN, size, AMBER.button)}
      >
        {first?.label ?? decision.verb} ↗
      </a>
    );
  }
  if (!first) return null;
  if (variant === "ghost") {
    return (
      <span className="flex flex-wrap items-center gap-1.5">
        {decision.actions.map((action, i) => (
          <button
            key={action.id}
            type="button"
            data-testid="v3-answer"
            data-decision={decision.id}
            onKeyDown={swallowEnter}
            onClick={() => onAnswer?.(decision, action)}
            className={cn(
              BTN,
              size,
              i === 0
                ? "border-foreground bg-foreground text-background hover:opacity-90"
                : "border-border-strong bg-card-solid text-foreground hover:border-status-blue",
            )}
          >
            {action.label}
          </button>
        ))}
        <span className="text-[11.5px] text-foreground-tertiary">{decision.id}</span>
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <button
        type="button"
        data-testid="v3-answer"
        data-decision={decision.id}
        onKeyDown={swallowEnter}
        onClick={() => onAnswer?.(decision, first)}
        className={cn(BTN, size, AMBER.button)}
      >
        {first.label}
      </button>
      {variant === "header" && decision.actions.length > 1 && (
        <button
          type="button"
          data-testid="v3-open-decision"
          onClick={() => onOpenDecision?.(decision)}
          className={cn(
            BTN,
            size,
            "border-border-strong bg-card-solid text-foreground hover:border-status-blue hover:text-status-blue-fg",
          )}
        >
          Open {decision.subject ?? decision.id}
        </button>
      )}
    </span>
  );
}
