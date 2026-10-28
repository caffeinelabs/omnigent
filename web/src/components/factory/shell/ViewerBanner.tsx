/**
 * The read-only banner a viewer sees on every factory page, with Request
 * access: it opens an `access_request` decision for the owners, who answer
 * it with Grant operator or Decline.
 */

import { useState } from "react";
import { EyeIcon, SendIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { type DecisionView, requestFactoryAccess } from "@/lib/factoryApi";

type RequestState =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "sent"; decision: DecisionView; deduped: boolean }
  | { kind: "failed"; message: string };

export function ViewerBanner({ factoryId }: { factoryId: string }) {
  const [state, setState] = useState<RequestState>({ kind: "idle" });
  const request = async () => {
    setState({ kind: "sending" });
    try {
      const res = await requestFactoryAccess(factoryId, { role: "operator" });
      setState({ kind: "sent", decision: res.decision, deduped: res.deduped });
    } catch (error) {
      setState({
        kind: "failed",
        message: error instanceof Error ? error.message : "The request could not be sent",
      });
    }
  };
  return (
    <div
      role="status"
      data-testid="factory-viewer-banner"
      className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm"
    >
      <EyeIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
      {/* At least 14rem of text: on a phone the action wraps under it. */}
      <p className="min-w-0 flex-[1_1_14rem] text-muted-foreground">
        <span className="font-medium text-foreground">You can watch everything.</span> Ask an owner
        for operator access to post or act.
      </p>
      {state.kind === "sent" ? (
        <span data-testid="factory-access-requested" className="text-muted-foreground">
          {state.deduped ? "Already asked" : "Asked the owners"} ·{" "}
          <span className="font-mono text-foreground">{state.decision.ref}</span>
        </span>
      ) : (
        <Button
          size="xs"
          variant="outline"
          onClick={() => void request()}
          disabled={state.kind === "sending"}
          data-testid="factory-request-access"
          componentId="factory.viewer.request_access"
        >
          <SendIcon />
          {state.kind === "sending" ? "Asking…" : "Request access"}
        </Button>
      )}
      {state.kind === "failed" && (
        <p role="alert" className="w-full text-destructive">
          {state.message}
        </p>
      )}
    </div>
  );
}
