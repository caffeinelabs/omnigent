/**
 * Steer › Team templates: the named team shapes new projects start from, as
 * cards with the role rings, an hourly estimate from the catalog's rates, how
 * many projects use each, and the gates that lock some shapes. Edit stages a
 * new shape (members per role, builders at once, the default), which reaches
 * projects only when they switch to it. A project's own team is changed on
 * its team page.
 */

import { MinusIcon, PencilIcon, PlusIcon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { CatalogResponse, FactoryRole, TeamTemplateView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { SectionCard } from "../project/projectParts";
import { MemberAvatar } from "../ui/MemberAvatar";
import { FOCUS_RING } from "../ui/statusTokens";
import { useSteer } from "./SteerContext";
import { STAGED_CONTROL, StagedChip } from "./steerFields";
import { useSteerCatalog } from "./steerQueries";

type TemplateRole = TeamTemplateView["roles"][number];

const ROLE_ORDER = ["project_lead", "ic", "reviewer", "env_keeper", "demo"] as const;
const ROLE_WORD: Record<string, string> = {
  project_lead: "Project lead",
  ic: "Builders",
  reviewer: "Reviewers",
  env_keeper: "Env keepers",
  demo: "Demo agents",
};

/** $/h of a shape from the catalog's per-role rates (the vendor named, else the role's first). */
export function templateRate(
  roles: readonly TemplateRole[],
  catalog: CatalogResponse | null | undefined,
): number | null {
  if (!catalog) return null;
  let total = 0;
  for (const r of roles) {
    const role = catalog.roles.find((x) => x.role === r.role);
    const vendor = role?.vendors.find(
      (v) => v.vendor === (r.vendor ?? role.vendors.find((x) => x.available)?.vendor),
    );
    const rate = (r.model ? vendor?.model_rates?.[r.model] : undefined) ?? vendor?.usd_per_h;
    if (rate == null) return null;
    total += rate * (r.count || 1);
  }
  return total;
}

function countOf(roles: readonly TemplateRole[], role: string): number {
  return roles.filter((r) => r.role === role).reduce((n, r) => n + (r.count || 1), 0);
}

/** `roles` with `role`'s count set to `n` (its first entry carries the change). */
export function withCount(roles: readonly TemplateRole[], role: string, n: number): TemplateRole[] {
  const out: TemplateRole[] = [];
  let seen = false;
  for (const r of roles) {
    if (r.role !== role) {
      out.push(r);
    } else if (!seen) {
      seen = true;
      if (n > 0) out.push({ ...r, count: n });
    }
  }
  if (!seen && n > 0) out.push({ role, vendor: null, model: null, count: n });
  return out;
}

function TemplateCard({
  template,
  used,
  catalog,
}: {
  template: TeamTemplateView;
  used: number;
  catalog: CatalogResponse | null | undefined;
}) {
  const steer = useSteer();
  const [editing, setEditing] = useState(false);
  const rolesPath = `team.templates.${template.id}.roles`;
  const roles = (steer.value(rolesPath) as TemplateRole[] | null) ?? template.roles;
  const staged = steer.staging.edits.some((e) =>
    e.change.path.startsWith(`team.templates.${template.id}`),
  );
  const rate = templateRate(roles, catalog);
  const needs = catalog?.gates ?? [];
  const editable = steer.canEdit && !steer.frozen;
  const set = (role: string, n: number) =>
    steer.replace(
      "templates",
      rolesPath,
      `Template ${template.label || template.id}: members`,
      withCount(roles, role, n),
      "Reaches projects only when they switch to this template.",
    );
  return (
    <section
      data-testid={`steer-template-${template.id}`}
      aria-label={template.label || template.id}
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-3",
        staged && STAGED_CONTROL,
      )}
    >
      <div className="flex items-center gap-2">
        <b className="min-w-0 flex-1 truncate text-sm font-semibold">
          {template.label || template.id}
        </b>
        {template.default && (
          <span className="rounded-md bg-muted px-1.5 text-xs leading-5">default</span>
        )}
        {staged && <StagedChip />}
      </div>
      {template.summary && <p className="text-sm text-foreground/80">{template.summary}</p>}
      <div className="flex flex-wrap gap-1" aria-label="Members">
        {roles.flatMap((r) =>
          Array.from({ length: r.count || 1 }, (_, i) => (
            <MemberAvatar
              key={`${r.role}-${r.vendor}-${r.model}-${i}`}
              size={24}
              role={r.role as FactoryRole}
              vendor={(r.vendor as "claude" | "codex" | null) ?? null}
              model={r.model}
              state="idle"
            />
          )),
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {rate != null ? `≈ $${rate.toFixed(2)}/h while working · ` : ""}
        used by {used} {used === 1 ? "project" : "projects"}
        {template.builders_at_once != null
          ? ` · ${template.builders_at_once} ${template.builders_at_once === 1 ? "builder" : "builders"} at once`
          : ""}
      </p>
      {editing && editable && (
        <ul className="flex flex-col gap-1 rounded-lg border border-border p-2 text-sm">
          {ROLE_ORDER.map((role) => {
            const n = countOf(roles, role);
            const need = needs.find((g) => g.role === role);
            const min = role === "project_lead" ? 1 : need?.when === "always" ? 1 : 0;
            const max = role === "project_lead" ? 1 : 6;
            return (
              <li key={role} className="flex items-center gap-2">
                <span className="min-w-0 flex-1">
                  {ROLE_WORD[role]}
                  {need && n <= min && min > 0 && (
                    <span className="ml-1.5 text-xs text-muted-foreground">{need.text}</span>
                  )}
                </span>
                <button
                  type="button"
                  aria-label={`One fewer ${ROLE_WORD[role]}`}
                  disabled={n <= min}
                  onClick={() => set(role, n - 1)}
                  className={cn(
                    "cursor-pointer rounded border border-border p-0.5 disabled:cursor-default disabled:opacity-40",
                    FOCUS_RING,
                  )}
                >
                  <MinusIcon aria-hidden className="size-3" />
                </button>
                <span className="w-5 text-center tabular-nums">{n}</span>
                <button
                  type="button"
                  aria-label={`One more ${ROLE_WORD[role]}`}
                  disabled={n >= max}
                  onClick={() => set(role, n + 1)}
                  className={cn(
                    "cursor-pointer rounded border border-border p-0.5 disabled:cursor-default disabled:opacity-40",
                    FOCUS_RING,
                  )}
                >
                  <PlusIcon aria-hidden className="size-3" />
                </button>
              </li>
            );
          })}
          {!template.default && (
            <li>
              <button
                type="button"
                onClick={() => {
                  for (const other of catalog?.templates ?? []) {
                    if (other.default && other.id !== template.id) {
                      steer.replace(
                        "templates",
                        `team.templates.${other.id}.default`,
                        `Template ${other.label || other.id}: default`,
                        false,
                      );
                    }
                  }
                  steer.replace(
                    "templates",
                    `team.templates.${template.id}.default`,
                    `Template ${template.label || template.id}: default`,
                    true,
                    "New projects start from it; the old default stays as a template.",
                  );
                }}
                className={cn(
                  "cursor-pointer text-sm text-status-blue-fg hover:underline",
                  FOCUS_RING,
                )}
              >
                Make it the default
              </button>
            </li>
          )}
        </ul>
      )}
      {editable && (
        <span className="flex items-center gap-1.5">
          <Button
            size="xs"
            variant="outline"
            onClick={() => setEditing((v) => !v)}
            data-testid={`steer-template-edit-${template.id}`}
            componentId="factory.steer.template_edit"
          >
            <PencilIcon />
            {editing ? "Done" : "Edit"}
          </Button>
        </span>
      )}
    </section>
  );
}

export function Templates() {
  const steer = useSteer();
  const catalog = useSteerCatalog(steer.factoryId);
  const templates = catalog.data?.templates ?? [];
  const used = (id: string) =>
    steer.snapshot.projects.filter((p) => p.team_spec?.template === id).length;
  if (catalog.isLoading)
    return <div aria-busy="true" className="h-48 animate-pulse rounded-xl bg-muted/70" />;
  if (!catalog.data || templates.length === 0) {
    return (
      <SectionCard title="Team templates" testId="steer-templates-none">
        <p className="text-sm text-muted-foreground">
          This server has no team templates yet. They arrive with team specs; until then every
          project runs the team its project lead's bundle brings.
        </p>
      </SectionCard>
    );
  }
  return (
    <div data-testid="steer-templates" className="flex min-w-0 flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        A template is a named team shape. New projects start from the default; a change reaches a
        project only when it switches to the template, so a template change applies as a version.
        Some members cannot go: a gate needs them.
      </p>
      <div className="grid min-w-0 grid-cols-1 gap-3 lg:grid-cols-2">
        {templates.map((t) => (
          <TemplateCard key={t.id} template={t} used={used(t.id)} catalog={catalog.data} />
        ))}
      </div>
    </div>
  );
}
