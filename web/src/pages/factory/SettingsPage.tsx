/**
 * Settings (`/factory/:factoryId/settings/:section`): People, Autonomy and
 * engine mode, Budgets & loops, Alerts (thresholds and the set-only Slack
 * webhook), Rules and Definition (versions, diff, revert, bundles, other
 * settings, "Run retro now"). Every save is `PUT …/definition`: an applied,
 * human-authored `C-n` in History that one click reverts. Viewers and
 * operators see the values read-only.
 */

import { Navigate } from "react-router-dom";
import {
  BellIcon,
  BookTextIcon,
  GaugeIcon,
  HistoryIcon,
  type LucideIcon,
  UsersIcon,
  WalletIcon,
} from "lucide-react";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import {
  SETTINGS_SECTIONS,
  type SettingsSection,
  improvementsPageHref,
  settingsPageHref,
} from "@/components/factory/factoryRoutes";
import { DefinitionPanel } from "@/components/factory/improvements/DefinitionPanel";
import { useFactoryDefinition } from "@/components/factory/insights/learnQueries";
import { PeopleSettings } from "@/components/factory/people/PeopleSettings";
import { DefinitionForm } from "@/components/factory/settings/DefinitionForm";
import { RulesEditor } from "@/components/factory/settings/RulesEditor";
import {
  ALERT_GROUPS,
  AUTONOMY_GROUPS,
  BUDGET_GROUPS,
  OTHER_GROUPS,
} from "@/components/factory/settings/settingsModel";
import { SlackWebhookCard } from "@/components/factory/settings/SlackWebhookCard";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { Link, useParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

const SECTION: Record<SettingsSection, { label: string; icon: LucideIcon; hint: string }> = {
  people: { label: "People", icon: UsersIcon, hint: "Who can watch, operate and own" },
  autonomy: { label: "Autonomy", icon: GaugeIcon, hint: "Project proposals and engine mode" },
  budgets: { label: "Budgets & loops", icon: WalletIcon, hint: "Money, attempts and rounds" },
  alerts: { label: "Alerts", icon: BellIcon, hint: "Thresholds and Slack" },
  rules: { label: "Rules", icon: BookTextIcon, hint: "Advisory text per role" },
  definition: { label: "Definition", icon: HistoryIcon, hint: "Versions, bundles, cadence" },
};

function SectionNav({ factoryId, active }: { factoryId: string; active: SettingsSection }) {
  return (
    <nav
      aria-label="Settings sections"
      data-testid="settings-nav"
      className="flex min-w-0 gap-1 md:sticky md:top-2 md:w-52 md:shrink-0 md:flex-col max-md:flex-wrap"
    >
      {SETTINGS_SECTIONS.map((key) => {
        const { label, icon: Icon } = SECTION[key];
        const on = key === active;
        return (
          <Link
            key={key}
            to={settingsPageHref(factoryId, key)}
            componentId={`factory.settings.nav.${key}`}
            data-testid={`settings-nav-${key}`}
            aria-current={on ? "page" : undefined}
            className={cn(
              "inline-flex h-8 shrink-0 items-center gap-2 rounded-md px-2 text-sm whitespace-nowrap",
              on
                ? "bg-muted font-medium text-foreground"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
          >
            <Icon aria-hidden className="size-4" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

function DefinitionSections({ section }: { section: Exclude<SettingsSection, "people"> }) {
  const { snapshot, nowS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const definition = useFactoryDefinition(fid);
  if (definition.isLoading && !definition.data) {
    return (
      <div aria-busy="true" data-testid="settings-loading" className="flex flex-col gap-3">
        {[0, 1].map((i) => (
          <div key={i} className="h-40 animate-pulse rounded-xl bg-muted/70" />
        ))}
      </div>
    );
  }
  if (!definition.data) {
    return (
      <FactoryLoadError
        error={definition.error ?? new Error("unavailable")}
        updatedAt={0}
        onRetry={() => void definition.refetch()}
        what="the factory definition"
        testId="settings-error"
      />
    );
  }
  const data = definition.data;
  switch (section) {
    case "autonomy":
      return (
        <DefinitionForm
          factoryId={fid}
          groups={AUTONOMY_GROUPS}
          definition={data}
          testId="settings-autonomy"
        />
      );
    case "budgets":
      return (
        <DefinitionForm
          factoryId={fid}
          groups={BUDGET_GROUPS}
          definition={data}
          testId="settings-budgets"
        />
      );
    case "alerts":
      return (
        <div className="flex min-w-0 flex-col gap-4">
          <SlackWebhookCard factoryId={fid} />
          <DefinitionForm
            factoryId={fid}
            groups={ALERT_GROUPS}
            definition={data}
            testId="settings-alerts"
          />
        </div>
      );
    case "rules":
      return (
        <RulesEditor
          factoryId={fid}
          definition={data}
          projects={snapshot.projects.map((p) => p.slug)}
        />
      );
    case "definition":
      return (
        <div className="flex min-w-0 flex-col gap-4">
          <DefinitionPanel factoryId={fid} nowS={nowS} showRetro />
          <DefinitionForm
            factoryId={fid}
            groups={OTHER_GROUPS}
            definition={data}
            testId="settings-other"
          />
        </div>
      );
  }
}

export function SettingsPage() {
  const { section = "people" } = useParams<{ section: string }>();
  const { snapshot, nowS } = useFactoryShell();
  const fid = snapshot.factory.id;
  if (!SETTINGS_SECTIONS.includes(section as SettingsSection)) {
    return <Navigate to={settingsPageHref(fid)} replace />;
  }
  const active = section as SettingsSection;
  return (
    <div data-testid="factory-settings-page" className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
        <h1 className="text-base font-semibold">Settings</h1>
        <span className="text-sm text-muted-foreground">{SECTION[active].label}</span>
        {active !== "people" && (
          <Link
            to={improvementsPageHref(fid, "history")}
            componentId="factory.settings.history"
            className="ml-auto text-sm text-muted-foreground hover:text-foreground hover:underline"
          >
            History of changes →
          </Link>
        )}
      </div>
      <div className="flex min-w-0 gap-4 max-md:flex-col">
        <SectionNav factoryId={fid} active={active} />
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {active === "people" ? (
            <PeopleSettings factoryId={fid} nowS={nowS} />
          ) : (
            <DefinitionSections section={active} />
          )}
        </div>
      </div>
    </div>
  );
}
