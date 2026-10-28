import type { MouseEvent } from "react";
import { FactoryIcon } from "lucide-react";
import { RunningDot } from "@/components/RunningDot";
import { useFactories, useFactoryDecisionCount } from "@/hooks/useFactory";
import type { FactorySummary } from "@/lib/factoryApi";
import { Link, useLocation } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { PrimaryNavLink } from "@/shell/PrimaryNavLink";
import { factoryPageHref } from "./factoryRoutes";

/** Shared factories listed under the entry before the rest fold into the index. */
export const SHARED_FACTORIES_SHOWN = 5;

/** What the sidebar entry summarises across every visible factory. */
export function factoryNavState(
  factories: FactorySummary[] | undefined,
  mine?: number,
): {
  running: boolean;
  needsYou: number;
} {
  const list = factories ?? [];
  return {
    running: list.some((f) => f.members_running > 0),
    // Decisions assigned to the viewer or their role; without the count, every open item.
    needsYou: mine ?? list.reduce((n, f) => n + f.decisions_open + f.prs_ready, 0),
  };
}

/** Running dot plus the amber "needs you" pill (decisions assigned to the viewer or their role). */
export function FactoryNavTrailing({ running, needsYou }: { running: boolean; needsYou: number }) {
  if (!running && needsYou === 0) return null;
  return (
    <span className="ml-auto flex shrink-0 items-center gap-1.5">
      {running && <RunningDot />}
      {needsYou > 0 && (
        <span
          data-testid="factory-nav-badge"
          aria-label={
            needsYou === 1 ? "1 factory item needs you" : `${needsYou} factory items need you`
          }
          className={cn(
            "inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-10 font-medium tabular-nums",
            "bg-warning/15 text-warning",
          )}
        >
          {needsYou}
        </span>
      )}
    </span>
  );
}

/**
 * Factories other people shared with the viewer, under the Factory entry:
 * their members are left out of the Shared sessions list, so this is where
 * they are reached.
 */
export function SharedFactoryRows({
  factories,
  activeId,
  onClick,
}: {
  factories: FactorySummary[];
  activeId: string | null;
  onClick?: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const shared = factories.filter((f) => f.shared_by);
  if (shared.length === 0) return null;
  return (
    <ul data-testid="factory-nav-shared" className="ml-6 flex flex-col">
      {shared.slice(0, SHARED_FACTORIES_SHOWN).map((factory) => {
        const active = factory.id === activeId;
        return (
          <li key={factory.id}>
            <Link
              to={factoryPageHref(factory.id)}
              onClick={onClick}
              componentId="sidebar.factory.shared"
              data-testid={`factory-nav-shared-${factory.id}`}
              aria-current={active ? "page" : undefined}
              title={`Shared with you by ${factory.shared_by}${factory.viewer_role ? ` · ${factory.viewer_role}` : ""}`}
              className={cn(
                "flex h-7 min-w-0 items-center gap-1.5 rounded-md px-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground dark:hover:bg-muted/50",
                active && "text-foreground",
              )}
            >
              <span className="min-w-0 truncate">{factory.name}</span>
              {factory.members_running > 0 && <RunningDot className="size-2" />}
              {(factory.mine ?? 0) > 0 && (
                <span className="ml-auto rounded-full bg-warning/15 px-1 text-10 font-medium text-warning tabular-nums">
                  {factory.mine}
                </span>
              )}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

/** The sidebar's Factory entry, between Inbox and Canvas, with factories shared with you under it. */
export function FactoryNavLink({
  active,
  onClick,
}: {
  active: boolean;
  onClick?: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const { data } = useFactories();
  const mine = useFactoryDecisionCount();
  const { pathname } = useLocation();
  const activeId = /\/factory\/([^/]+)/.exec(pathname)?.[1] ?? null;
  return (
    <>
      <PrimaryNavLink
        to="/factory"
        label="Factory"
        icon={FactoryIcon}
        active={active}
        onClick={onClick}
        componentId="sidebar.factory"
        testId="factory-nav"
        trailing={<FactoryNavTrailing {...factoryNavState(data?.data, mine)} />}
      />
      <SharedFactoryRows factories={data?.data ?? []} activeId={activeId} onClick={onClick} />
    </>
  );
}
