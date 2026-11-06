/**
 * The shell around every factory page (`/factory/:factoryId/*`): it reads the
 * live snapshot once, sends older addresses to the place that shows them now
 * (`cockpitTarget`, then the board's old parameters), beats presence, and
 * frames the page with the cockpit (`Cockpit`: the sidebar, or a phone's tab
 * bar).
 */

import { type ReactNode, useEffect } from "react";
import { ArrowLeftIcon, LockIcon } from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import { type CockpitTarget, cockpitTarget } from "@/components/factory/cockpit/cockpitRoutes";
import { FactoryControlsProvider } from "@/components/factory/controls/FactoryControlsContext";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { EmptyState } from "@/components/factory/project/projectParts";
import { PAGE_EXTRA_TOP, pageExtraBottom } from "@/components/factory/shell/scroll";
import {
  cockpitRedirect,
  factoryLocation,
  legacyFactoryRedirect,
  presenceLocation,
} from "@/components/factory/shell/shellRoutes";
import { Button } from "@/components/ui/button";
import { COCKPIT_MIN_REFRESH_MS, useFactory } from "@/hooks/useFactory";
import { useFactoryPresence } from "@/hooks/useFactoryPresence";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { Link, useNavigate, useParams, useSearchParams } from "@/lib/routing";
import { ApiError } from "@/lib/sessionsApi";
import { Cockpit } from "./Cockpit";

/** The cockpit's outline while the snapshot loads: the sidebar and a page. */
function ShellSkeleton() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading factory"
      data-testid="factory-loading"
      className="grid gap-7 md:grid-cols-[13.5rem_minmax(0,1fr)]"
    >
      <div className="flex flex-col gap-2 max-md:hidden">
        <div className="mb-2 h-7 w-40 animate-pulse rounded bg-muted" />
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="h-7 animate-pulse rounded bg-muted/70" />
        ))}
      </div>
      <div className="flex flex-col gap-4">
        <div className="h-8 w-64 animate-pulse rounded bg-muted" />
        <div className="h-28 animate-pulse rounded-xl bg-muted" />
        <div className="h-64 animate-pulse rounded-xl bg-muted" />
      </div>
    </div>
  );
}

/**
 * The factory shell; `children` is the routed page. It renders nothing of the
 * page until the snapshot is in, and only a redirect for an older address.
 */
export function FactoryLayout({ children }: { children: ReactNode }) {
  const { factoryId = "", "*": rest = "" } = useParams<{ factoryId: string; "*": string }>();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const phone = useIsMobileViewport();
  const { data, error, isLoading, updatedAt, clockOffsetS, refetch } = useFactory(factoryId, {
    minRefreshMs: COCKPIT_MIN_REFRESH_MS,
  });
  const target = factoryTarget(factoryId, rest, params, data?.prs);
  const to = target && target !== "wait" ? target.to : null;
  useEffect(() => {
    if (to) navigate(to, { replace: true });
  }, [navigate, to]);
  const redirecting = target != null;
  // A 404 means no factory or no access; one after data (a removed person's
  // refetch) drops the stale page too.
  const missing = error instanceof ApiError && error.status === 404;
  useFactoryPresence(factoryId, presenceLocation(factoryLocation(rest)), {
    enabled: !redirecting && Boolean(data) && !missing,
  });
  return (
    <PageScroll
      data-testid="factory-page"
      maxWidthClassName="max-w-[100rem]"
      contentClassName="px-4 md:px-6"
      // The cockpit never scrolls sideways (a phone's page is its width).
      className="overflow-x-hidden"
      extraTop={PAGE_EXTRA_TOP}
      // Room for the phone's bottom tabs.
      extraBottom={pageExtraBottom(phone, true)}
    >
      {error && !missing && (
        <FactoryLoadError
          error={error}
          updatedAt={data ? updatedAt : 0}
          onRetry={refetch}
          what="this factory"
        />
      )}
      {(isLoading || redirecting) && !missing && <ShellSkeleton />}
      {missing && (
        <EmptyState
          testId="factory-missing"
          icon={<LockIcon aria-hidden className="size-6 text-muted-foreground/50" />}
          title="No access to this factory"
          body="This factory doesn’t exist, or you no longer have access to it. Ask one of its owners to share it with you again."
          action={
            <Button asChild size="sm" variant="outline" className="mt-1">
              <Link to="/factory" componentId="factory.missing.back">
                <ArrowLeftIcon />
                Back to factories
              </Link>
            </Button>
          }
        />
      )}
      {data && !redirecting && !missing && (
        <FactoryControlsProvider key={data.factory.id} snapshot={data}>
          <Cockpit snapshot={data} clockOffsetS={clockOffsetS} updatedAt={updatedAt} rest={rest}>
            {children}
          </Cockpit>
        </FactoryControlsProvider>
      )}
    </PageScroll>
  );
}

/**
 * Where an address goes before its page renders: an earlier cockpit's page
 * to the place that shows it now, an old link's parameters to their page.
 */
export function factoryTarget(
  factoryId: string,
  rest: string,
  params: URLSearchParams,
  prs: FactorySnapshot["prs"] | undefined,
): CockpitTarget {
  const cockpit = cockpitTarget(factoryId, rest, params, prs);
  if (cockpit) return cockpit;
  const legacy = legacyFactoryRedirect(factoryId, rest, params, prs);
  if (legacy) return legacy;
  const page = factoryLocation(rest).page;
  if (page === "decisions" || page === "room") return null;
  const moved = cockpitRedirect(factoryId, rest, params, "v2", null);
  return moved ? { to: moved } : null;
}
