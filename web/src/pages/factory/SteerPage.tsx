/**
 * Steer (`/steer/<section>`): how the factory works, and changing it. Every
 * change here is one definition version with Undo and restore. This first
 * Steer holds what the cockpit can change today, each section as it was:
 * Versions and trials (every change, what is on trial, what agents proposed,
 * the findings), Budgets and autonomy, Alerts and rules, and the cadence,
 * previews and models. One change (`?peek=C-n`) opens in a side panel.
 */

import { Navigate } from "react-router-dom";
import {
  BellIcon,
  HistoryIcon,
  type LucideIcon,
  SlidersHorizontalIcon,
  TimerIcon,
  WalletIcon,
} from "lucide-react";
import {
  STEER_SECTIONS,
  type SteerSection,
  steerPageHref,
} from "@/components/factory/factoryRoutes";
import { AUTONOMY_GROUPS, BUDGET_GROUPS } from "@/components/factory/settings/settingsModel";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { Peek } from "@/components/factory/ui/Peek";
import { FOCUS_RING } from "@/components/factory/ui/statusTokens";
import { Link, useParams, useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { ImprovementsPage } from "./ImprovementsPage";
import { ProposalPage } from "./ProposalPage";
import { DefinitionGroups, DefinitionSections } from "./SettingsPage";

export const STEER_SECTION: Record<
  SteerSection,
  { label: string; hint: string; icon: LucideIcon }
> = {
  versions: {
    label: "Versions and trials",
    hint: "Every change, trials and agents' proposals",
    icon: HistoryIcon,
  },
  budgets: {
    label: "Budgets and autonomy",
    hint: "Money, attempts, who decides",
    icon: WalletIcon,
  },
  alerts: { label: "Alerts and rules", hint: "Thresholds and rules per role", icon: BellIcon },
  definition: {
    label: "Cadence, previews and models",
    hint: "Heartbeats, supervisor, mail, models",
    icon: TimerIcon,
  },
};

function Outline({ factoryId, active }: { factoryId: string; active: SteerSection }) {
  return (
    <nav
      aria-label="Steer sections"
      data-testid="steer-outline"
      className="flex min-w-0 gap-1 md:sticky md:top-2 md:w-60 md:shrink-0 md:flex-col max-md:overflow-x-auto max-md:[scrollbar-width:none]"
    >
      {STEER_SECTIONS.map((key) => {
        const { label, hint, icon: Icon } = STEER_SECTION[key];
        const on = key === active;
        return (
          <Link
            key={key}
            to={steerPageHref(factoryId, key)}
            componentId={`factory.steer.nav.${key}`}
            data-testid={`steer-nav-${key}`}
            aria-current={on ? "page" : undefined}
            className={cn(
              "flex shrink-0 items-start gap-2 rounded-md px-2 py-1.5 text-sm",
              FOCUS_RING,
              on
                ? "bg-muted font-medium text-foreground"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
          >
            <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
            <span className="flex min-w-0 flex-col">
              <span className="whitespace-nowrap">{label}</span>
              <span className="text-xs font-normal text-muted-foreground max-md:hidden">
                {hint}
              </span>
            </span>
          </Link>
        );
      })}
    </nav>
  );
}

function Section({ section }: { section: SteerSection }) {
  switch (section) {
    case "versions":
      return <ImprovementsPage />;
    case "budgets":
      return (
        <DefinitionGroups groups={[...BUDGET_GROUPS, ...AUTONOMY_GROUPS]} testId="steer-budgets" />
      );
    case "alerts":
      return (
        <div className="flex min-w-0 flex-col gap-4">
          <DefinitionSections section="alerts" slack={false} />
          <DefinitionSections section="rules" />
        </div>
      );
    case "definition":
      return <DefinitionSections section="definition" />;
  }
}

export function SteerPage() {
  const { section = "versions" } = useParams<{ section: string }>();
  const { snapshot } = useFactoryShell();
  const fid = snapshot.factory.id;
  const [params, setParams] = useSearchParams();
  if (!(STEER_SECTIONS as readonly string[]).includes(section)) {
    return <Navigate to={steerPageHref(fid)} replace />;
  }
  const active = section as SteerSection;
  const peek = params.get("peek");
  const change = peek && /^[CF]-\d+$/i.test(peek) ? peek.toUpperCase() : null;
  const closePeek = () =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("peek");
        return next;
      },
      { replace: true },
    );
  return (
    <div
      data-testid="factory-steer-page"
      data-section={active}
      className="flex min-w-0 flex-col gap-3"
    >
      <div className="flex min-w-0 flex-col gap-0.5">
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <SlidersHorizontalIcon aria-hidden className="size-5 text-muted-foreground" />
          Steer
        </h1>
        <p className="text-sm text-muted-foreground">
          How this factory works, and changing it. Every change is one definition version you can
          undo or restore.
        </p>
      </div>
      <div className="flex min-w-0 gap-4 max-md:flex-col">
        <Outline factoryId={fid} active={active} />
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <Section section={active} />
        </div>
      </div>
      <Peek
        open={change != null}
        onOpenChange={(open) => !open && closePeek()}
        title={change ?? ""}
        wide
      >
        {change && <ProposalPage proposalRef={change} />}
      </Peek>
    </div>
  );
}
