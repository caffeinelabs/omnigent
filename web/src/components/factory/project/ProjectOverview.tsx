/**
 * The project page's Overview tab: the charter (goal, done-when, scope,
 * constraints, demo focus, env and preview settings) with Edit charter, the
 * team with what each agent is doing, the project's decisions (open ones
 * answered in one click, answered, withdrawn and expired ones folded), and
 * the PL's plan file.
 */

import { useState } from "react";
import { CircleCheckIcon, ExternalLinkIcon, FileTextIcon, PencilIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { CharterView, FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { ageText } from "../board/boardModel";
import { HarnessGlyph, MemberStatusDot, ROLE_NAME, statusWord } from "../board/TeamStrip";
import { EditCharterDialog } from "../controls/EditCharterDialog";
import { DecisionsRail } from "../decisions/DecisionsRail";
import { formatUsd, plural } from "../factoryFormat";
import { LeverGated } from "../shell/viewerCan";
import { projectDecisions, projectTeam } from "./projectModel";
import { SectionCard } from "./projectParts";

function Chip({ children, muted = false }: { children: React.ReactNode; muted?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center rounded-md border border-border px-2 text-sm whitespace-nowrap",
        muted ? "text-muted-foreground" : "bg-muted/50 text-foreground",
      )}
    >
      {children}
    </span>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid min-w-0 grid-cols-1 gap-1 md:grid-cols-[8rem_minmax(0,1fr)] md:gap-4">
      <dt className="text-sm text-muted-foreground md:pt-0.5">{label}</dt>
      <dd className="min-w-0 text-sm">{children}</dd>
    </div>
  );
}

function CharterSettings({ charter }: { charter: CharterView }) {
  const slots = charter.preview.slots || charter.preview_slots;
  return (
    <div data-testid="project-charter-settings" className="flex flex-wrap gap-1.5">
      <Chip muted={!charter.env_needed}>{charter.env_needed ? "Needs a dev env" : "No env"}</Chip>
      <Chip muted={!charter.preview.enabled}>
        {charter.preview.enabled ? `Previews on · ${plural(slots, "slot")}` : "Previews off"}
      </Chip>
      <Chip>Up to {plural(charter.budget_ics, "IC")}</Chip>
      {charter.preview.tooling_ref && (
        <Chip muted>
          Tooling <span className="ml-1 font-mono">{charter.preview.tooling_ref}</span>
        </Chip>
      )}
      {charter.version_path && (
        <Chip muted>
          Version <span className="ml-1 font-mono">{charter.version_path}</span>
        </Chip>
      )}
      {charter.health_path && (
        <Chip muted>
          Health <span className="ml-1 font-mono">{charter.health_path}</span>
        </Chip>
      )}
    </div>
  );
}

export function CharterCard({ project, onEdit }: { project: ProjectView; onEdit: () => void }) {
  const charter = project.charter;
  const demoFocus = charter?.demo_focus ?? charter?.demo;
  return (
    <SectionCard
      title="Charter"
      testId="project-charter"
      action={
        <LeverGated lever="apply_projects">
          <Button
            variant="outline"
            size="xs"
            onClick={onEdit}
            data-testid="charter-edit"
            componentId="factory.project.edit_charter"
          >
            <PencilIcon />
            Edit charter
          </Button>
        </LeverGated>
      }
    >
      {charter ? (
        <div className="flex flex-col gap-4">
          <p data-testid="project-goal" className="text-ui leading-relaxed">
            {charter.goal}
          </p>
          <dl className="flex flex-col gap-3">
            {charter.dod.length > 0 && (
              <Field label="Done when">
                <ul data-testid="project-dod" className="flex flex-col gap-1">
                  {charter.dod.map((item) => (
                    <li key={item} className="flex min-w-0 items-start gap-1.5">
                      <CircleCheckIcon
                        aria-hidden
                        className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
                      />
                      <span className="min-w-0 font-mono break-words">{item}</span>
                    </li>
                  ))}
                </ul>
              </Field>
            )}
            {charter.scope_paths.length > 0 && (
              <Field label="Scope">
                <span className="flex flex-wrap gap-1">
                  {charter.scope_paths.map((path) => (
                    <span key={path} className="rounded-sm bg-muted px-1.5 font-mono break-all">
                      {path}
                    </span>
                  ))}
                </span>
              </Field>
            )}
            {charter.constraints && (
              <Field label="Constraints">
                <p className="whitespace-pre-wrap">{charter.constraints}</p>
              </Field>
            )}
            {demoFocus && (
              <Field label="Demo focus">
                <p data-testid="project-demo-focus">{demoFocus}</p>
              </Field>
            )}
            <Field label="Env & previews">
              <CharterSettings charter={charter} />
            </Field>
          </dl>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No charter stored for this project.</p>
      )}
    </SectionCard>
  );
}

export function TeamCard({ project, nowS }: { project: ProjectView; nowS: number }) {
  const team = projectTeam(project);
  const working = team.filter((m) => m.running).length;
  return (
    <SectionCard
      title="Team"
      meta={team.length > 0 ? `${plural(team.length, "agent")} · ${working} working` : undefined}
      testId="project-team"
    >
      {team.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No agents yet · Atlas spawns the PL, the PL its team.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-border">
          {team.map((member) => (
            <li
              key={member.session_id}
              data-testid={`project-member-${member.session_id}`}
              data-status={statusWord(member)}
              className="flex min-w-0 items-center gap-2.5 py-2 text-sm first:pt-0 last:pb-0"
            >
              <span className="flex w-4 shrink-0 justify-center">
                <MemberStatusDot member={member} />
              </span>
              <div className="flex min-w-0 flex-1 flex-col">
                <span className="flex min-w-0 items-center gap-1.5">
                  <HarnessGlyph harness={member.harness} className="size-3.5" />
                  <Link
                    to={member.url}
                    componentId="factory.project.member"
                    className="min-w-0 truncate font-medium hover:underline"
                  >
                    {member.title}
                  </Link>
                  <span className="shrink-0 text-muted-foreground">
                    {member.role ? ROLE_NAME[member.role] : "Agent"}
                  </span>
                </span>
                <span
                  className="truncate text-muted-foreground"
                  title={member.summary ?? undefined}
                >
                  {statusWord(member)}
                  {member.summary ? ` · ${member.summary}` : ""}
                </span>
              </div>
              <span className="flex shrink-0 flex-col items-end text-muted-foreground">
                <span className="font-mono tabular-nums">{formatUsd(member.cost_usd) ?? "—"}</span>
                {member.last_activity_at != null && (
                  <span className="text-xs">{ageText(nowS - member.last_activity_at)}</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

export function PlanCard({ project }: { project: ProjectView }) {
  const path = `~/factory/${project.slug}/plan.md`;
  return (
    <SectionCard title="Plan" testId="project-plan">
      {project.pl ? (
        <Link
          to={`${project.pl.url}?file=${encodeURIComponent(path)}`}
          componentId="factory.project.plan"
          data-testid="project-plan-link"
          className="inline-flex min-w-0 items-center gap-1.5 font-mono text-sm hover:underline"
        >
          <FileTextIcon aria-hidden className="size-3.5 shrink-0" />
          <span className="truncate">{path}</span>
          <ExternalLinkIcon aria-hidden className="size-3 shrink-0" />
        </Link>
      ) : (
        <p className="text-sm text-muted-foreground">The PL writes its plan once it starts.</p>
      )}
    </SectionCard>
  );
}

export function ProjectOverview({
  snapshot,
  project,
  nowS,
  clockOffsetS,
  highlightDecision,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
  clockOffsetS: number;
  /** A decision id or ref to highlight (`?decision=`). */
  highlightDecision: string | null;
}) {
  const [editing, setEditing] = useState(false);
  const decisions = projectDecisions(snapshot, project);
  const asking = decisions.some((d) => d.status === "open");
  return (
    <div
      data-testid="project-overview"
      className="grid min-w-0 grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1fr)_22rem] lg:grid-rows-[auto_1fr] lg:gap-x-6"
    >
      <div className="flex min-w-0 flex-col gap-4 lg:col-start-1 lg:row-span-2 lg:row-start-1">
        <CharterCard project={project} onEdit={() => setEditing(true)} />
        <TeamCard project={project} nowS={nowS} />
      </div>
      {/* Open decisions come first on narrow screens: they are what waits on the human. */}
      <div className={cn("min-w-0 lg:col-start-2 lg:row-start-1", asking && "max-lg:order-first")}>
        <DecisionsRail
          factoryId={snapshot.factory.id}
          decisions={decisions}
          highlightId={highlightDecision}
          clockOffsetS={clockOffsetS}
          showClosed
        />
      </div>
      <div className="min-w-0 lg:col-start-2 lg:row-start-2">
        <PlanCard project={project} />
      </div>
      <EditCharterDialog
        factoryId={snapshot.factory.id}
        project={project}
        open={editing}
        onOpenChange={setEditing}
      />
    </div>
  );
}
