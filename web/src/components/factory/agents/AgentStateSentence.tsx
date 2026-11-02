/**
 * The agent panel's first words: what it is doing, in one fact-built
 * sentence, then what happens next and whether you need to act.
 */

import { cn } from "@/lib/utils";
import type { AgentSentence } from "./agentSentence";
import { STATE_COLOR } from "./MemberRing";

export function AgentStateSentence({
  sentence,
  className,
}: {
  sentence: AgentSentence;
  className?: string;
}) {
  return (
    <div
      data-testid="agent-state-sentence"
      data-state={sentence.state}
      className={cn("rounded-lg border border-border bg-card px-3 py-2.5", className)}
      style={{
        borderLeft: `3px ${sentence.state === "stuck" ? "dashed" : "solid"} ${STATE_COLOR[sentence.state]}`,
      }}
    >
      <p className="text-ui font-semibold text-foreground">{sentence.headline}</p>
      {sentence.next && <p className="mt-1 text-xs text-muted-foreground">{sentence.next}</p>}
    </div>
  );
}
