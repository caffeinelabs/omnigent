import { CloudOffIcon, RefreshCwIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { retryServerInfoNow } from "@/lib/capabilities";
import { useServerInfoProbe } from "@/lib/CapabilitiesContext";

/**
 * Whole seconds until `at`, re-rendering every second while counting down.
 * Reads the clock on each render rather than from state, so a new `at` after
 * an attempt in flight (when no tick ran) never counts from a stale time.
 */
function useSecondsUntil(at: number | null): number | null {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (at === null) return;
    const timer = setInterval(() => setTick((tick) => tick + 1), 1_000);
    return () => clearInterval(timer);
  }, [at]);
  return at === null ? null : Math.max(1, Math.ceil((at - Date.now()) / 1_000));
}

/**
 * The server's capabilities couldn't be loaded and the probe is retrying.
 * Stands in for a page whose route depends on them, so a dropped boot request
 * never reads as "Page not found".
 *
 * @param retryAt - When the next attempt fires (epoch ms); `null` while one is in flight.
 */
export function ServerUnreachable({
  failures,
  retryAt,
  onRetry,
}: {
  failures: number;
  retryAt: number | null;
  onRetry: () => void;
}) {
  const seconds = useSecondsUntil(retryAt);
  return (
    <div className="flex flex-1 items-center justify-center px-4 py-10">
      <section
        aria-labelledby="server-unreachable-title"
        data-testid="server-unreachable"
        data-failures={failures}
        className="flex w-full max-w-sm flex-col items-center gap-3 text-center"
      >
        <span className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <CloudOffIcon className="size-5" aria-hidden />
        </span>
        <div className="flex flex-col gap-1">
          <h1 id="server-unreachable-title" className="font-medium text-foreground text-lg">
            Can’t reach the server
          </h1>
          <p className="text-muted-foreground text-ui">
            This page opens as soon as the server answers.
          </p>
        </div>
        <p
          className="text-muted-foreground text-sm tabular-nums"
          data-testid="server-unreachable-status"
        >
          {seconds === null ? "Retrying…" : `Retrying in ${seconds}s`}
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={onRetry}
          loading={retryAt === null}
          componentId="app.server_unreachable.retry"
          data-testid="server-unreachable-retry"
        >
          <RefreshCwIcon />
          Retry now
        </Button>
      </section>
    </div>
  );
}

/**
 * Stand-in for a route whose registration depends on `/v1/info` while that
 * probe has no answer yet: a spinner on the first attempt, the retrying state
 * once it has failed.
 */
export function ServerInfoPending() {
  const probe = useServerInfoProbe();
  if (probe.kind === "retrying") {
    return (
      <ServerUnreachable
        failures={probe.failures}
        retryAt={probe.retryAt}
        onRetry={retryServerInfoNow}
      />
    );
  }
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center">
      <Spinner className="size-5 text-muted-foreground" aria-label="Loading" />
    </div>
  );
}
