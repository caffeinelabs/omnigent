/**
 * The closure-reason picker on a closed PR's decision: why it closed without
 * merge (wrong scope, superseded, test run, …), sent with the answer and
 * counted in Insights ▸ Quality.
 */

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { DecisionView } from "@/lib/factoryApi";
import { tagLabel } from "./feedbackModel";

/** The reasons a decision's options require (empty for every other decision). */
export function closureReasons(decision: DecisionView): string[] {
  if (decision.source !== "pr_closed") return [];
  const found = decision.options.find((o) => Array.isArray(o.reasons) && o.reasons.length > 0);
  return found?.reasons ?? [];
}

export function ClosureReasonPicker({
  decisionRef,
  reasons,
  value,
  onChange,
  disabled = false,
}: {
  decisionRef: string;
  reasons: readonly string[];
  value: string | null;
  onChange: (reason: string) => void;
  disabled?: boolean;
}) {
  return (
    <Select
      value={value ?? undefined}
      onValueChange={onChange}
      disabled={disabled}
      componentId="factory.decision.closure_reason"
      valueHasNoPii
    >
      <SelectTrigger
        size="sm"
        aria-label="Why it closed"
        data-testid={`decision-closure-${decisionRef}`}
        className="w-48 basis-full sm:basis-auto"
      >
        <SelectValue placeholder="Why it closed (required)" />
      </SelectTrigger>
      <SelectContent>
        {reasons.map((reason) => (
          <SelectItem key={reason} value={reason}>
            {tagLabel(reason)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
