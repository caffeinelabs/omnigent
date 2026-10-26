import type { MouseEvent } from "react";
import { FactoryIcon } from "lucide-react";
import { RunningDot } from "@/components/RunningDot";
import { useFactories } from "@/hooks/useFactory";
import type { FactorySummary } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { PrimaryNavLink } from "@/shell/PrimaryNavLink";

/** What the sidebar entry summarises across every visible factory. */
export function factoryNavState(factories: FactorySummary[] | undefined): {
  running: boolean;
  needsYou: number;
} {
  const list = factories ?? [];
  return {
    running: list.some((f) => f.members_running > 0),
    needsYou: list.reduce((n, f) => n + f.decisions_open + f.prs_ready, 0),
  };
}

/** Running dot plus the amber "needs you" pill (open decisions + PRs ready for review). */
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

/** The sidebar's Factory entry, between Inbox and Canvas. */
export function FactoryNavLink({
  active,
  onClick,
}: {
  active: boolean;
  onClick?: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const { data } = useFactories();
  return (
    <PrimaryNavLink
      to="/factory"
      label="Factory"
      icon={FactoryIcon}
      active={active}
      onClick={onClick}
      componentId="sidebar.factory"
      testId="factory-nav"
      trailing={<FactoryNavTrailing {...factoryNavState(data?.data)} />}
    />
  );
}
