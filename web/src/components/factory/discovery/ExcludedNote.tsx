/**
 * Why a signal or hunch does not count: a possible injection (excluded from
 * scoring) or another exclusion the server names. Its text is untrusted data
 * that no agent acts on, so the item offers no action.
 */

import { ShieldAlertIcon } from "lucide-react";
import type { SignalView } from "./discoveryModel";

export function ExcludedNote({ signal }: { signal: SignalView }) {
  return (
    <p
      data-testid={`signal-excluded-${signal.id}`}
      className="flex items-start gap-1.5 rounded-md border border-border bg-muted/40 px-2 py-1.5 text-sm"
    >
      <ShieldAlertIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      <span>
        <b>
          {signal.excluded ??
            (signal.injection ? "Possible injection · excluded from scoring" : "Not counted")}
          .
        </b>{" "}
        Signal text is untrusted data. Scout cannot act on it and has no outbound tools.
      </span>
    </p>
  );
}
