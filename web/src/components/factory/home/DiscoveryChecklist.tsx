/**
 * "Getting discovery going": the cold-start steps while discovery is on and
 * one is still open (turned on, starter sources approved, the product profile
 * reviewed, a first goal, the first weekly synthesis), the current one bold.
 */

import { CheckIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { MEANING } from "../ui/statusTokens";
import type { ChecklistStep } from "./homeModel";

export function DiscoveryChecklist({
  steps,
  className,
}: {
  steps: readonly ChecklistStep[];
  className?: string;
}) {
  const done = steps.filter((s) => s.done).length;
  const current = steps.findIndex((s) => !s.done);
  return (
    <section
      aria-labelledby="home-checklist-h"
      data-testid="home-checklist"
      className={cn("rounded-xl border border-border bg-card px-4 py-3", className)}
    >
      <header className="flex items-center gap-2">
        <h2 id="home-checklist-h" className="grow text-base font-semibold">
          Getting discovery going
        </h2>
        <span className="text-xs text-muted-foreground">
          {done} of {steps.length}
        </span>
      </header>
      <ol className="mt-2 flex list-none flex-col gap-1.5 p-0">
        {steps.map((step, i) => (
          <li
            key={step.label}
            className={cn("flex items-center gap-2 text-sm", i === current && "font-semibold")}
          >
            <span
              aria-hidden
              className={cn(
                "grid size-4 shrink-0 place-items-center rounded border",
                step.done
                  ? cn(MEANING.working.surface, MEANING.working.text, "border-transparent")
                  : "border-border-strong",
              )}
            >
              {step.done && <CheckIcon className="size-3" />}
            </span>
            {step.label}
            <span className="sr-only">{step.done ? " (done)" : " (not done)"}</span>
          </li>
        ))}
      </ol>
      <p className="mt-2 text-xs text-muted-foreground">
        The discovery lead drafts the profile and a first goal; you correct them.
      </p>
    </section>
  );
}
