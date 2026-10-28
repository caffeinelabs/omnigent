/**
 * Projects as a dense table, one row per project in flight: stage, tasks,
 * the main PR with its gates, loops, agents, spend and flags (open
 * decisions, claims the gates don't back). Rows open the project page.
 * Below the work: the live env at the base (Environments) and the closed
 * projects, folded. On a phone each row stacks into two lines.
 */

import { type ReactNode, useState } from "react";
import {
  ChevronRightIcon,
  EyeOffIcon,
  FolderKanbanIcon,
  Rows3Icon,
  Rows4Icon,
  ServerIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import type { FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { Link, useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { ageText } from "../board/boardModel";
import { devEnvStatus } from "../board/DevEnvPill";
import { STATE_ICON_TONE } from "../board/GateChip";
import { TONE_CHIP, TONE_DOT, TONE_TEXT, formatUsd, plural, shortSha } from "../factoryFormat";
import { prPageHref, projectPageHref } from "../factoryRoutes";
import { SectionCard } from "../project/projectParts";
import {
  PROJECT_FILTER_LABEL,
  type ProjectFilter,
  type ProjectRowFacts,
  matchesFilter,
  projectRowFacts,
  projectSections,
  sectionsSummary,
} from "./projectsModel";
import { AddProjectInline } from "./AddProjectInline";
import { StageDots } from "./StageDots";

export type TableVariant = "overview" | "full";
export type Density = "comfortable" | "compact";

const DENSITY_KEY = "factory.projects.density";

function readDensity(): Density {
  try {
    return window.localStorage.getItem(DENSITY_KEY) === "compact" ? "compact" : "comfortable";
  } catch {
    return "comfortable";
  }
}

function useDensity(): [Density, (d: Density) => void] {
  const [density, setDensity] = useState<Density>(readDensity);
  return [
    density,
    (next) => {
      setDensity(next);
      try {
        window.localStorage.setItem(DENSITY_KEY, next);
      } catch {
        // Storage off: the choice lasts this page.
      }
    },
  ];
}

function GateDots({ facts }: { facts: ProjectRowFacts }) {
  if (facts.gates.length === 0) return null;
  return (
    <span
      aria-label={facts.gates.map((g) => g.label).join(", ")}
      title={facts.gates.map((g) => g.label).join(" · ")}
      className="inline-flex items-center gap-[2px]"
    >
      {facts.gates.map((gate) => (
        <span
          key={gate.key}
          aria-hidden
          className={cn(
            "size-[7px] rounded-[2px]",
            gate.state === "pass" && "bg-success",
            gate.state === "pending" && "bg-warning",
            gate.state === "fail" && "bg-destructive",
            gate.state === "skip" && "bg-muted-foreground/30",
            gate.state === "unknown" && "border border-dashed border-muted-foreground/60",
          )}
        />
      ))}
    </span>
  );
}

function PrCell({ facts, factoryId }: { facts: ProjectRowFacts; factoryId: string }) {
  const pr = facts.pr;
  if (!pr) return <span className="text-muted-foreground">—</span>;
  const failing = facts.gates.find((g) => g.state === "fail");
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <Link
        to={prPageHref(factoryId, pr.id)}
        componentId="factory.projects.pr"
        onClick={(e) => e.stopPropagation()}
        className={cn(
          "shrink-0 font-mono tabular-nums hover:underline",
          pr.ready ? "text-success" : "text-foreground",
        )}
        title={pr.title}
      >
        !{pr.number}
      </Link>
      {pr.state === "open" ? (
        <GateDots facts={facts} />
      ) : (
        <span className="text-xs text-muted-foreground">{pr.state}</span>
      )}
      {failing && (
        <span className={cn("truncate text-xs", STATE_ICON_TONE.fail)}>{failing.label}</span>
      )}
      {facts.prs.length > 1 && (
        <span className="text-xs text-muted-foreground">+{facts.prs.length - 1}</span>
      )}
    </span>
  );
}

/** Flags shown in a row; the rest fold into `+n` (all of them on hover). */
const FLAGS_SHOWN = 2;

function Flags({ facts }: { facts: ProjectRowFacts }) {
  if (facts.flags.length === 0) return null;
  const more = facts.flags.length - FLAGS_SHOWN;
  return (
    <span
      className="inline-flex items-center gap-1 whitespace-nowrap"
      title={facts.flags.map((f) => `${f.label}: ${f.title}`).join("\n")}
    >
      {more > 0 && <span className="order-last text-xs text-muted-foreground">+{more}</span>}
      {facts.flags.slice(0, FLAGS_SHOWN).map((flag) => (
        <span
          key={flag.key}
          title={flag.title}
          className={cn(
            "inline-flex h-5 items-center rounded-sm px-1.5 font-mono text-xs whitespace-nowrap",
            TONE_CHIP[flag.tone],
          )}
        >
          {flag.label}
        </span>
      ))}
    </span>
  );
}

function Agents({ facts }: { facts: ProjectRowFacts }) {
  return (
    <span
      className="font-mono tabular-nums"
      title={`${facts.agents} agents, ${facts.running} working`}
    >
      {facts.agents}
      {facts.running > 0 && <span className="text-session-active">·{facts.running}▶</span>}
    </span>
  );
}

function TitleCell({
  project,
  factoryId,
  slugClassName,
}: {
  project: ProjectView;
  factoryId: string;
  /** Extra classes for the slug (the full table drops it below 2xl). */
  slugClassName?: string;
}) {
  // One clipped line: a slug that doesn't fit after the title wraps out of
  // sight instead of shrinking to `a…` next to a truncated title.
  return (
    <span className="flex h-[1lh] min-w-0 flex-wrap items-baseline gap-x-2 overflow-hidden">
      <Link
        to={projectPageHref(factoryId, project.slug)}
        componentId="factory.projects.open"
        onClick={(e) => e.stopPropagation()}
        data-testid={`projects-row-link-${project.slug}`}
        className="max-w-full min-w-0 truncate font-medium text-foreground hover:underline"
        title={project.title}
      >
        {project.title}
      </Link>
      <span
        className={cn(
          "max-w-full shrink-0 truncate font-mono text-xs text-muted-foreground",
          slugClassName,
        )}
      >
        {project.slug}
      </span>
    </span>
  );
}

const TH = "px-2 py-1.5 text-left text-xs font-medium whitespace-nowrap text-muted-foreground";

// Below 1440 px the Room dock takes 21 rem, so priority, $/h and owner give
// way to the project's name (they stay on the project page).
const NARROW_HIDDEN = "max-[90rem]:hidden";

function DesktopTable({
  snapshot,
  projects,
  nowS,
  variant,
  density,
}: {
  snapshot: FactorySnapshot;
  projects: ProjectView[];
  nowS: number;
  variant: TableVariant;
  density: Density;
}) {
  const navigate = useNavigate();
  const fid = snapshot.factory.id;
  const full = variant === "full";
  const cell = cn("px-2 align-middle", density === "compact" ? "py-1" : "py-2");
  return (
    <div className="-mx-2 min-w-0 overflow-x-auto">
      <table data-testid="projects-table" data-density={density} className="w-full min-w-0 text-sm">
        <thead>
          <tr className="border-b border-border">
            <th className={cn(TH, "w-[28%] min-w-48")}>Project</th>
            {full && <th className={cn(TH, NARROW_HIDDEN)}>P</th>}
            {full && <th className={cn(TH, "max-2xl:hidden")}>Repo</th>}
            <th className={TH}>Stage</th>
            <th className={cn(TH, "text-right")}>Tasks</th>
            <th className={TH}>PR · gates</th>
            <th className={TH}>Loops</th>
            <th className={cn(TH, "text-right")}>Agents</th>
            <th className={cn(TH, "text-right")}>$</th>
            {full && <th className={cn(TH, "text-right", NARROW_HIDDEN)}>$/h</th>}
            {full && <th className={cn(TH, NARROW_HIDDEN)}>Owner</th>}
            {full && <th className={cn(TH, "max-2xl:hidden")}>Depends on</th>}
            <th className={TH}>Flags</th>
          </tr>
        </thead>
        <tbody>
          {projects.map((project) => {
            const facts = projectRowFacts(snapshot, project);
            return (
              <tr
                key={project.slug}
                data-testid={`projects-row-${project.slug}`}
                onClick={() => navigate(projectPageHref(fid, project.slug))}
                className={cn(
                  "cursor-pointer border-b border-border/60 last:border-0 hover:bg-muted/50",
                  project.overlay === "paused" && "opacity-70",
                )}
              >
                <td className={cn(cell, "max-w-0")}>
                  <TitleCell
                    project={project}
                    factoryId={fid}
                    slugClassName={full ? "max-2xl:hidden" : undefined}
                  />
                </td>
                {full && (
                  <td
                    className={cn(cell, "font-mono text-xs text-muted-foreground", NARROW_HIDDEN)}
                  >
                    P{project.priority}
                  </td>
                )}
                {full && (
                  <td
                    className={cn(
                      cell,
                      "font-mono text-xs whitespace-nowrap text-muted-foreground max-2xl:hidden",
                    )}
                  >
                    {project.repo
                      ? `${project.repo.split("/").pop()}#${project.base_branch ?? "main"}`
                      : "—"}
                  </td>
                )}
                <td className={cell}>
                  <StageDots project={project} nowS={nowS} />
                </td>
                <td className={cn(cell, "text-right font-mono tabular-nums")}>
                  {facts.tasks ?? <span className="text-muted-foreground">—</span>}
                </td>
                <td className={cn(cell, "max-w-48")}>
                  <PrCell facts={facts} factoryId={fid} />
                </td>
                <td
                  className={cn(
                    cell,
                    "font-mono text-xs whitespace-nowrap",
                    TONE_TEXT[facts.loopsTone],
                  )}
                >
                  {facts.loops ?? <span className="text-muted-foreground">—</span>}
                </td>
                <td className={cn(cell, "text-right")}>
                  <Agents facts={facts} />
                </td>
                <td className={cn(cell, "text-right font-mono tabular-nums")}>
                  {formatUsd(project.cost_usd) ?? <span className="text-muted-foreground">—</span>}
                </td>
                {full && (
                  <td
                    className={cn(
                      cell,
                      "text-right font-mono text-muted-foreground tabular-nums",
                      NARROW_HIDDEN,
                    )}
                  >
                    {facts.usdPerH != null ? facts.usdPerH.toFixed(1) : "—"}
                  </td>
                )}
                {full && (
                  <td
                    className={cn(cell, "max-w-28 truncate text-muted-foreground", NARROW_HIDDEN)}
                  >
                    <span title={facts.owner ?? undefined}>
                      {facts.owner ? (facts.owner.split("@")[0] ?? facts.owner) : "—"}
                    </span>
                  </td>
                )}
                {full && (
                  <td
                    className={cn(cell, "font-mono text-xs text-muted-foreground max-2xl:hidden")}
                  >
                    {facts.dependsOn.length > 0 ? facts.dependsOn.join(", ") : "—"}
                  </td>
                )}
                <td className={cell}>
                  <Flags facts={facts} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PhoneList({
  snapshot,
  projects,
  nowS,
}: {
  snapshot: FactorySnapshot;
  projects: ProjectView[];
  nowS: number;
}) {
  const fid = snapshot.factory.id;
  return (
    <ul
      data-testid="projects-table"
      data-layout="stacked"
      className="flex flex-col divide-y divide-border"
    >
      {projects.map((project) => {
        const facts = projectRowFacts(snapshot, project);
        return (
          <li
            key={project.slug}
            data-testid={`projects-row-${project.slug}`}
            className="flex min-w-0 flex-col gap-1 py-2 first:pt-0 last:pb-0"
          >
            {/* The name gets the whole line; the slug only where there is room. */}
            <TitleCell project={project} factoryId={fid} slugClassName="max-sm:hidden" />
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <StageDots project={project} nowS={nowS} />
              <PrCell facts={facts} factoryId={fid} />
              <Flags facts={facts} />
              <span className="ml-auto inline-flex items-center gap-3 text-muted-foreground">
                <Agents facts={facts} />
                <span className="font-mono tabular-nums">{formatUsd(project.cost_usd) ?? "—"}</span>
              </span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** The live env at the base (the integration project), one line each. */
export function EnvironmentRows({
  snapshot,
  envs,
  nowS,
}: {
  snapshot: FactorySnapshot;
  envs: ProjectView[];
  nowS: number;
}) {
  if (envs.length === 0) return null;
  const base = snapshot.factory.base;
  return (
    <div data-testid="projects-envs" className="flex flex-col gap-1 border-t border-border pt-2.5">
      {envs.map((project) => {
        const status = project.env ? devEnvStatus(project.env) : null;
        const deployed = project.env?.reported?.deployed;
        const urls = project.env?.reported?.urls;
        const atHead =
          base?.head_sha && deployed?.sha
            ? deployed.sha.startsWith(base.head_sha.slice(0, 7))
            : null;
        return (
          <div
            key={project.slug}
            data-testid={`projects-env-${project.slug}`}
            className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 text-sm"
          >
            <span className="inline-flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              <ServerIcon aria-hidden className="size-3.5" />
              Environments
            </span>
            <Link
              to={projectPageHref(snapshot.factory.id, project.slug)}
              componentId="factory.projects.env"
              className="font-mono hover:underline"
            >
              {project.slug}
            </Link>
            <span className="text-muted-foreground">(integration)</span>
            {status && (
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden className={cn("size-1.5 rounded-full", TONE_DOT[status.tone])} />
                {deployed?.sha ? (
                  <span className="font-mono">
                    {deployed.ref ?? project.base_branch}@{shortSha(deployed.sha)}
                  </span>
                ) : (
                  status.word
                )}
                {atHead === true && <span className="text-success">= head</span>}
                {atHead === false && <span className="text-warning">behind head</span>}
              </span>
            )}
            {!status && (
              <span className="text-muted-foreground">
                {project.provisioning?.label ?? "not up yet"}
              </span>
            )}
            {urls?.app && (
              <a
                href={urls.app}
                target="_blank"
                rel="noreferrer"
                className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
              >
                app ↗
              </a>
            )}
            {urls?.api && (
              <a
                href={urls.api}
                target="_blank"
                rel="noreferrer"
                className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
              >
                api ↗
              </a>
            )}
            {base?.moved_at != null && (
              <span className="text-muted-foreground">
                · base moved {ageText(nowS - base.moved_at)}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Closed projects folded into one line, opening to a list. */
export function ClosedRows({
  snapshot,
  closed,
}: {
  snapshot: FactorySnapshot;
  closed: ProjectView[];
}) {
  if (closed.length === 0) return null;
  return (
    <Collapsible data-testid="projects-closed" className="border-t border-border pt-2">
      <CollapsibleTrigger className="group inline-flex cursor-pointer items-center gap-1 rounded-sm text-sm text-muted-foreground hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none">
        <ChevronRightIcon
          aria-hidden
          className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
        />
        Closed <span className="tabular-nums">({closed.length})</span>
        <span className="ml-1 hidden truncate md:inline">
          {closed
            .slice(0, 3)
            .map((p) => p.slug)
            .join(" · ")}
          {closed.length > 3 ? " · …" : ""}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="mt-1.5 flex flex-col gap-1">
          {closed.map((project) => (
            <li key={project.slug} className="flex min-w-0 items-center gap-2 text-sm">
              <Link
                to={projectPageHref(snapshot.factory.id, project.slug)}
                componentId="factory.projects.closed"
                className="min-w-0 truncate hover:underline"
              >
                {project.title}
              </Link>
              <span className="font-mono text-xs text-muted-foreground">{project.slug}</span>
              <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                {project.status && project.status !== "active" ? project.status : project.stage}
                {project.parent_slug ? ` · from ${project.parent_slug}` : ""}
              </span>
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function ProjectsTable({
  snapshot,
  nowS,
  variant = "overview",
  title = "Projects",
  actions,
  initialFilter = "all",
}: {
  snapshot: FactorySnapshot;
  nowS: number;
  variant?: TableVariant;
  title?: string;
  /** Extra controls on the right of the toolbar (view switch, + Project). */
  actions?: ReactNode;
  initialFilter?: ProjectFilter;
}) {
  const phone = useIsMobileViewport();
  const [filter, setFilter] = useState<ProjectFilter>(initialFilter);
  const [density, setDensity] = useDensity();
  const sections = projectSections(snapshot.projects);
  const shown = sections.work.filter((p) => matchesFilter(snapshot, p, filter));
  return (
    <SectionCard
      title={title}
      meta={sectionsSummary(sections)}
      testId="projects-section"
      className="gap-2.5"
      action={
        <>
          {sections.work.length > 0 && (
            <Select
              value={filter}
              onValueChange={(v) => setFilter(v as ProjectFilter)}
              componentId="factory.projects.filter"
              valueHasNoPii
            >
              <SelectTrigger
                size="sm"
                aria-label="Filter projects"
                data-testid="projects-filter"
                className="h-7 w-40 max-sm:w-32"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(PROJECT_FILTER_LABEL) as ProjectFilter[]).map((key) => (
                  <SelectItem key={key} value={key}>
                    {PROJECT_FILTER_LABEL[key]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {!phone && sections.work.length > 0 && (
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => setDensity(density === "compact" ? "comfortable" : "compact")}
              aria-label={density === "compact" ? "Comfortable rows" : "Compact rows"}
              title={density === "compact" ? "Comfortable rows" : "Compact rows"}
              data-testid="projects-density"
              componentId="factory.projects.density"
              className="text-muted-foreground"
            >
              {density === "compact" ? <Rows3Icon /> : <Rows4Icon />}
            </Button>
          )}
          {actions}
        </>
      }
    >
      {sections.work.length === 0 ? (
        <div
          data-testid="projects-empty"
          className="flex flex-wrap items-center gap-2 py-4 text-sm text-muted-foreground"
        >
          <FolderKanbanIcon aria-hidden className="size-4" />
          No projects in flight. Ask Atlas, or
          <AddProjectInline />
        </div>
      ) : shown.length === 0 ? (
        <p data-testid="projects-filtered-empty" className="py-3 text-sm text-muted-foreground">
          No project is {PROJECT_FILTER_LABEL[filter].toLowerCase()}.{" "}
          <button
            type="button"
            onClick={() => setFilter("all")}
            className="cursor-pointer text-foreground underline underline-offset-2"
          >
            Show all
          </button>
        </p>
      ) : phone ? (
        <PhoneList snapshot={snapshot} projects={shown} nowS={nowS} />
      ) : (
        <DesktopTable
          snapshot={snapshot}
          projects={shown}
          nowS={nowS}
          variant={variant}
          density={density}
        />
      )}
      <EnvironmentRows snapshot={snapshot} envs={sections.envs} nowS={nowS} />
      <ClosedRows snapshot={snapshot} closed={sections.closed} />
      {snapshot.hidden_projects > 0 && (
        <p className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
          <EyeOffIcon aria-hidden className="size-3.5" />
          {plural(snapshot.hidden_projects, "project")} hidden: you can’t open their project lead.
        </p>
      )}
    </SectionCard>
  );
}
