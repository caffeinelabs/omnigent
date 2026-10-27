/**
 * "What will start": the New factory form's live summary. The org tree the
 * leads will build, how many sandboxes and previews it may use, what the
 * cadence costs in heartbeat turns, and the guardrails no form field changes.
 */

import {
  BoxesIcon,
  HeartPulseIcon,
  MonitorPlayIcon,
  ShieldCheckIcon,
  TimerIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import {
  FACTORY_MAX_PODS,
  FACTORY_PREVIEW_CAP,
  type FactoryDraft,
  previewsOn,
  startSummary,
} from "./projectForm";

function Stat({
  icon,
  children,
  testId,
}: {
  icon: ReactNode;
  children: ReactNode;
  testId: string;
}) {
  return (
    <li data-testid={testId} className="flex min-w-0 items-start gap-2 text-sm">
      <span aria-hidden className="mt-0.5 shrink-0 text-muted-foreground [&_svg]:size-3.5">
        {icon}
      </span>
      <span className="min-w-0">{children}</span>
    </li>
  );
}

function projectEnvText(project: FactoryDraft["projects"][number]): string {
  if (!project.envNeeded) return "no env";
  if (!previewsOn(project)) return "env";
  return `env · ${project.previewSlots} prev`;
}

export function StartSummaryCard({
  draft,
  className,
}: {
  draft: FactoryDraft;
  className?: string;
}) {
  const summary = startSummary(draft);
  const projects = draft.projects;

  return (
    <aside
      data-testid="factory-new-preview"
      aria-label="What will start"
      className={cn(
        "flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4",
        className,
      )}
    >
      <h2 className="text-sm font-medium text-muted-foreground">What will start</h2>
      <div data-testid="factory-new-preview-tree" className="flex min-w-0 flex-col gap-0.5 text-sm">
        <p className="font-medium">
          Atlas <span className="font-normal text-muted-foreground">⇄</span> Sentinel{" "}
          <span className="font-normal text-muted-foreground">(leads)</span>
        </p>
        {projects.map((project, i) => (
          <p
            key={project.key}
            data-testid={`factory-new-preview-pl-${i}`}
            className="flex min-w-0 items-baseline gap-1.5"
          >
            <span aria-hidden className="shrink-0 font-mono text-muted-foreground">
              {i === projects.length - 1 ? "└" : "├"}
            </span>
            <span className="min-w-0 truncate">
              PL <span className="font-mono">{project.slug || `project-${i + 1}`}</span>
            </span>
            <span className="ml-auto shrink-0 text-muted-foreground tabular-nums">
              {projectEnvText(project)}
            </span>
          </p>
        ))}
      </div>
      <ul className="flex flex-col gap-1.5 border-t border-border pt-3">
        <Stat icon={<BoxesIcon />} testId="factory-new-preview-sandboxes">
          <span className="tabular-nums">{summary.sandboxes}</span> sandboxes{" "}
          <span className="text-muted-foreground">
            (2 leads + {projects.length} {projects.length === 1 ? "PL" : "PLs"}; ICs share their
            PL’s)
          </span>
        </Stat>
        <Stat icon={<MonitorPlayIcon />} testId="factory-new-preview-previews">
          {summary.maxPreviews === 0 ? (
            "No factory previews"
          ) : (
            <>
              Up to <span className="tabular-nums">{summary.maxPreviews}</span>{" "}
              {summary.maxPreviews === 1 ? "preview" : "previews"} at once
              {summary.requestedPreviews > FACTORY_PREVIEW_CAP && (
                <span className="text-muted-foreground"> (factory cap {FACTORY_PREVIEW_CAP})</span>
              )}
            </>
          )}
        </Stat>
        <Stat icon={<HeartPulseIcon />} testId="factory-new-preview-heartbeats">
          ≈<span className="tabular-nums">{summary.heartbeats}</span> heartbeat turns/h max{" "}
          <span className="text-muted-foreground">
            ({draft.cadence === "demo" ? "demo" : "normal"} cadence; quiet beats are skipped)
          </span>
        </Stat>
        <Stat icon={<TimerIcon />} testId="factory-new-preview-first-pr">
          First PR typically in 30–45 min
        </Stat>
      </ul>
      <div className="flex flex-col gap-1.5 border-t border-border pt-3">
        <h3 className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground">
          <ShieldCheckIcon aria-hidden className="size-3.5" />
          Guardrails (fixed)
        </h3>
        <ul
          data-testid="factory-new-guardrails"
          className="flex list-disc flex-col gap-0.5 pl-5 text-sm"
        >
          <li>
            Humans merge every PR
            {summary.repos.length > 0 && (
              <>
                {" "}
                into <span className="font-mono break-words">{summary.repos.join(", ")}</span>
              </>
            )}
            ; the factory never merges
          </li>
          <li>Deny-only policy hooks on every agent</li>
          <li>At most {FACTORY_MAX_PODS} pods</li>
        </ul>
      </div>
    </aside>
  );
}
