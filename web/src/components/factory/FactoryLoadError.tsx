import { RefreshCwIcon, TriangleAlertIcon } from "lucide-react";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/sessionsApi";

function clockTime(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * A failed factory read. Stale data stays on screen below it, so the alert
 * says how old that data is; without any data it is the whole page body.
 *
 * @param updatedAt - When the data on screen was fetched (epoch ms), 0 for none.
 */
export function FactoryLoadError({
  error,
  updatedAt,
  onRetry,
  what = "factories",
  testId = "factory-load-error",
  className,
}: {
  error: Error;
  updatedAt: number;
  onRetry: () => void;
  what?: string;
  testId?: string;
  /** Replaces the bottom margin (inside a card, where the card spaces it). */
  className?: string;
}) {
  const status = error instanceof ApiError ? `HTTP ${error.status}` : error.message;
  return (
    <Alert variant="destructive" data-testid={testId} className={className ?? "mb-4"}>
      <TriangleAlertIcon />
      <AlertTitle>
        Couldn’t {updatedAt > 0 ? "refresh" : "load"} {what}
      </AlertTitle>
      <AlertDescription>
        {status}
        {updatedAt > 0 && ` · showing data from ${clockTime(updatedAt)}`}
      </AlertDescription>
      <AlertAction>
        <Button
          variant="outline"
          size="xs"
          onClick={onRetry}
          componentId="factory.retry"
          data-testid={`${testId}-retry`}
        >
          <RefreshCwIcon />
          Retry
        </Button>
      </AlertAction>
    </Alert>
  );
}
