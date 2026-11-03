/**
 * Steer › Roles and briefs: every role, and for the one selected (`?role=`)
 * its default vendor · model (the roles a project team is built from read it
 * for new projects; each project may override it in its team), this
 * factory's brief for the role (added to the built-in prompt, versioned, never
 * replacing guards or protocol), its rules, and the built-in prompt and skills
 * shown as platform code. Scout also shows its budget, cadence and what it may
 * and may not do. Every edit stages; models are owner changes.
 */

import { useMemo } from "react";
import type { CatalogResponse, FactoryRole } from "@/lib/factoryApi";
import { Link, useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { steerPageHref } from "../factoryRoutes";
import { roleLabel } from "../settings/RulesEditor";
import { SectionCard } from "../project/projectParts";
import { MemberAvatar } from "../ui/MemberAvatar";
import { FOCUS_RING, type LeadSeat } from "../ui/statusTokens";
import { useSteer } from "./SteerContext";
import { FieldRow, NumberField, STAGED_CONTROL, TextField, TierZero } from "./steerFields";
import { useSteerCatalog } from "./steerQueries";

export interface RoleEntry {
  key: string;
  label: string;
  /** What the role does, in a few words. */
  does: string;
  role: FactoryRole;
  seat?: LeadSeat;
  /** Its key in `briefs`. */
  brief: string;
  /** Its key in `role_defaults` (null: a lead seat on its bundle's model). */
  defaults: string | null;
  discoveryOnly?: boolean;
}

export const ROLE_ENTRIES: readonly RoleEntry[] = [
  {
    key: "scout",
    label: "Scout",
    does: "discovery lead",
    role: "lead",
    seat: "discovery",
    brief: "scout",
    defaults: null,
    discoveryOnly: true,
  },
  {
    key: "atlas",
    label: "Atlas",
    does: "portfolio lead",
    role: "lead",
    seat: "portfolio",
    brief: "atlas",
    defaults: null,
  },
  {
    key: "sentinel",
    label: "Sentinel",
    does: "reliability lead",
    role: "lead",
    seat: "reliability",
    brief: "sentinel",
    defaults: null,
  },
  {
    key: "project_lead",
    label: "Project lead",
    does: "runs one project's team",
    role: "project_lead",
    brief: "project_lead",
    defaults: "project_lead",
  },
  { key: "ic", label: "Builder", does: "writes the code", role: "ic", brief: "ic", defaults: "ic" },
  {
    key: "reviewer",
    label: "Reviewer",
    does: "reviews every pull request",
    role: "reviewer",
    brief: "reviewer",
    defaults: "reviewer",
  },
  {
    key: "env_keeper",
    label: "Env keeper",
    does: "runs changes in an environment",
    role: "env_keeper",
    brief: "env_keeper",
    defaults: "env_keeper",
  },
  {
    key: "demo",
    label: "Demo",
    does: "proves it works on a preview",
    role: "demo",
    brief: "demo",
    defaults: "demo",
  },
  {
    key: "planner",
    label: "Planner",
    does: "splits work into tasks",
    role: "planner",
    brief: "planner",
    defaults: "planner",
  },
];

/** Rule roles a role reads (`ic` also reads the Codex builder's). */
function ruleRoles(entry: RoleEntry): string[] {
  if (entry.role === "lead") return ["lead", "*"];
  if (entry.role === "ic") return ["ic", "ic_codex", "*"];
  return [entry.role, "*"];
}

/** The vendor · model choices of a role: the catalog's, else Claude's aliases. */
export function modelChoices(
  catalog: CatalogResponse | null | undefined,
  role: string,
): { vendor: string; model: string }[] {
  const found = catalog?.roles.find((r) => r.role === role);
  const claude = catalog?.roles
    .find((r) => r.role === "ic")
    ?.vendors.find((v) => v.vendor === "claude")?.models;
  const vendors = found?.vendors.filter((v) => v.available) ?? [
    { vendor: "claude", models: claude ?? ["opus", "sonnet", "haiku"] },
  ];
  return vendors.flatMap((v) => (v.models ?? []).map((model) => ({ vendor: v.vendor, model })));
}

function ModelSelect({
  entry,
  catalog,
}: {
  entry: RoleEntry;
  catalog: CatalogResponse | null | undefined;
}) {
  const steer = useSteer();
  const id = `steer-model-${entry.key}`;
  if (entry.key === "scout") {
    const path = "discovery.seat.model";
    const value = (steer.value(path) as string | null) ?? "";
    const staged = steer.isStaged(path);
    const options = modelChoices(catalog, "ic").filter((o) => o.vendor === "claude");
    return (
      <FieldRow
        id={id}
        label="Model"
        help="Scout runs on Claude Code; empty keeps its bundle's default."
        staged={staged}
        testId={`steer-field-${path}`}
      >
        {steer.canEdit && !steer.frozen ? (
          <select
            id={id}
            value={value}
            onChange={(e) =>
              steer.replace(
                "roles",
                path,
                "Scout's model",
                e.target.value || null,
                "Scout restarts on it at its next turn.",
              )
            }
            className={cn(
              "h-7 rounded-md border border-input bg-background px-2 text-sm max-sm:w-full",
              FOCUS_RING,
              staged && STAGED_CONTROL,
            )}
          >
            <option value="">bundle default</option>
            {options.map((o) => (
              <option key={o.model} value={o.model}>
                claude · {o.model}
              </option>
            ))}
          </select>
        ) : (
          <span className="text-sm">{value || "bundle default"}</span>
        )}
      </FieldRow>
    );
  }
  if (!entry.defaults) {
    return (
      <FieldRow
        id={id}
        label="Model"
        help="Lead seats run on their bundle's model; seats as data come later."
      >
        <span className="text-sm text-muted-foreground">bundle default</span>
      </FieldRow>
    );
  }
  const vendorPath = `role_defaults.${entry.defaults}.vendor`;
  const modelPath = `role_defaults.${entry.defaults}.model`;
  const vendor = (steer.value(vendorPath) as string | null) ?? null;
  const model = (steer.value(modelPath) as string | null) ?? null;
  const staged = steer.isStaged(vendorPath) || steer.isStaged(modelPath);
  const options = modelChoices(catalog, entry.role);
  const current = vendor || model ? `${vendor ?? "claude"}:${model ?? ""}` : "";
  const lock = steer.lockOf(`role_defaults.${entry.defaults}`);
  return (
    <FieldRow
      id={id}
      label="Default vendor and model"
      help={
        entry.role === "project_lead"
          ? "Project leads run on Claude Code today. Used for new projects; each project can override it in its team."
          : "Used for new projects. Each project can override it in its team."
      }
      staged={staged}
      testId={`steer-field-role_defaults.${entry.defaults}`}
    >
      {steer.canEdit && !steer.frozen && !lock ? (
        <select
          id={id}
          aria-label={`Default vendor and model for ${entry.label}`}
          value={current}
          onChange={(e) => {
            const [v, m] = e.target.value ? e.target.value.split(":") : [null, null];
            steer.replace("roles", vendorPath, `${entry.label}: vendor`, v || null);
            steer.replace(
              "roles",
              modelPath,
              `${entry.label}: model`,
              m || null,
              "New projects start their members on it.",
            );
          }}
          data-testid={`steer-model-${entry.key}`}
          className={cn(
            "h-7 rounded-md border border-input bg-background px-2 text-sm max-sm:w-full",
            FOCUS_RING,
            staged && STAGED_CONTROL,
          )}
        >
          <option value="">bundle default</option>
          {current && !options.some((o) => `${o.vendor}:${o.model}` === current) && (
            <option value={current}>{current.replace(":", " · ")}</option>
          )}
          {options.map((o) => (
            <option key={`${o.vendor}:${o.model}`} value={`${o.vendor}:${o.model}`}>
              {o.vendor} · {o.model}
            </option>
          ))}
        </select>
      ) : (
        <span className="text-sm">
          {current ? current.replace(":", " · ") : "bundle default"}
          {lock && <span className="ml-1.5 text-xs text-muted-foreground">on trial {lock}</span>}
        </span>
      )}
    </FieldRow>
  );
}

export function RolesAndBriefs() {
  const steer = useSteer();
  const [params] = useSearchParams();
  const catalog = useSteerCatalog(steer.factoryId);
  const entries = useMemo(
    () => ROLE_ENTRIES.filter((e) => steer.discoveryOn || !e.discoveryOnly),
    [steer.discoveryOn],
  );
  const selected = entries.find((e) => e.key === params.get("role")) ?? entries[0]!;
  const rules = (steer.effective.rules ?? []).filter((r) => ruleRoles(selected).includes(r.role));
  const scoutMay = steer.definition.agent_paths?.[selected.key] ?? [];
  return (
    <div
      data-testid="steer-roles"
      className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-[13rem_minmax(0,1fr)]"
    >
      <nav
        aria-label="Roles"
        className="flex min-w-0 gap-1 rounded-xl border border-border bg-card p-1.5 lg:flex-col max-lg:overflow-x-auto max-lg:[scrollbar-width:none]"
      >
        {entries.map((e) => {
          const on = e.key === selected.key;
          const staged = steer.staging.edits.some(
            (edit) =>
              edit.change.path === `briefs.${e.brief}` ||
              (e.defaults && edit.change.path.startsWith(`role_defaults.${e.defaults}.`)) ||
              (e.key === "scout" && /^discovery\.(seat|cadence)\./.test(edit.change.path)),
          );
          return (
            <Link
              key={e.key}
              to={steerPageHref(steer.factoryId, "roles", { role: e.key })}
              componentId="factory.steer.role"
              aria-current={on ? "page" : undefined}
              data-testid={`steer-role-${e.key}`}
              className={cn(
                "flex shrink-0 items-center gap-2 rounded-lg px-2 py-1.5 text-sm",
                FOCUS_RING,
                on
                  ? "bg-muted font-medium"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              <MemberAvatar size={24} role={e.role} seat={e.seat} state="idle" label={e.label} />
              <span className="flex min-w-0 flex-col">
                <span className="text-foreground">{e.label}</span>
                <span
                  className={cn(
                    "truncate text-xs font-normal max-lg:hidden",
                    on ? "text-foreground/80" : "text-muted-foreground",
                  )}
                >
                  {e.does}
                </span>
              </span>
              {staged && (
                <span
                  aria-label="staged"
                  className="ml-auto size-2 shrink-0 rounded-full bg-status-violet"
                />
              )}
            </Link>
          );
        })}
      </nav>
      <SectionCard
        title={selected.label}
        meta={selected.does}
        testId={`steer-role-pane-${selected.key}`}
      >
        <ModelSelect entry={selected} catalog={catalog.data} />
        {selected.key === "scout" && (
          <div className="flex min-w-0 flex-col divide-y divide-border/60">
            <NumberField
              section="budgets"
              path="discovery.budgets.usd_per_day"
              label="Daily budget"
              unit="$/day"
              bounds={steer.definition.bounds["discovery.budgets.usd_per_day"]}
              help="Scout stops at 100 %"
            />
            <NumberField
              section="budgets"
              path="discovery.budgets.per_ask"
              label="Per research ask"
              unit="$"
              bounds={steer.definition.bounds["discovery.budgets.per_ask"]}
            />
            <NumberField
              section="roles"
              path="discovery.cadence.sweep_h"
              label="Sweep every"
              unit="h"
              integer
              bounds={steer.definition.bounds["discovery.cadence.sweep_h"]}
              help="0 turns it off"
            />
            <NumberField
              section="roles"
              path="discovery.cadence.synthesis_h"
              label="Synthesis every"
              unit="h"
              integer
              bounds={steer.definition.bounds["discovery.cadence.synthesis_h"]}
              help="0 turns it off"
            />
          </div>
        )}
        {selected.key === "scout" && (
          <dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
            <dt className="text-muted-foreground">May</dt>
            <dd>
              read signals, query approved sources through the gateway, label and group signals,
              write bet notes, propose changes to{" "}
              {scoutMay.length ? scoutMay.join(", ") : "the profile, strategy and sources"} (two a
              day)
            </dd>
            <dt className="text-muted-foreground">May not</dt>
            <dd>
              write a signal's text, a count or a score; file projects; push; contact users; post
              outside the factory
            </dd>
          </dl>
        )}
        <TextField
          section="roles"
          path={`briefs.${selected.brief}`}
          label="This factory's brief for the role"
          help="Added to the built-in prompt; it cannot replace guards or protocol. Product knowledge goes here, as data."
          placeholder="Guidance for this role in this factory."
          multiline
          maxLength={4000}
          effect="Members in the role read it at their next turn."
        />
        <div className="flex min-w-0 flex-col gap-1">
          <span className="flex items-center gap-2 text-sm">
            Rules
            <Link
              to={steerPageHref(steer.factoryId, "alerts")}
              componentId="factory.steer.rules_link"
              className="ml-auto text-xs text-status-blue-fg hover:underline"
            >
              Edit in Alerts and rules
            </Link>
          </span>
          {rules.length === 0 ? (
            <p className="text-sm text-muted-foreground">None.</p>
          ) : (
            <ul className="flex flex-col gap-1 text-sm">
              {rules.map((r) => (
                <li key={r.id} className="flex gap-2">
                  <span className="shrink-0 font-mono text-xs leading-5 text-muted-foreground">
                    {r.id}
                  </span>
                  <span className="min-w-0 break-words">
                    {r.text}
                    <span className="ml-1.5 text-xs text-muted-foreground">
                      {roleLabel(r.role)}
                      {r.origin ? ` · from ${r.origin}` : ""}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <TierZero>
          The built-in prompt and skills are platform code. A better skill ships as a platform
          change, never as a brief.
        </TierZero>
      </SectionCard>
    </div>
  );
}
