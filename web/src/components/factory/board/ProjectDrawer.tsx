/**
 * The project drawer (`?project=<slug>`): one project in full. Its charter,
 * every PR, the dev env with reported and probed truth, previews by slot,
 * the team, its decisions, the PL's plan file and the project's activity.
 */

import { type ReactNode, useState } from "react";
import { ExternalLinkIcon, FileTextIcon, MessageSquareIcon, PencilIcon } from "lucide-react";
import { PullRequestStatus } from "@/components/github/PrStateBadge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { FactorySnapshot, PrView, ProjectView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { ActivityList } from "../activity/ActivityList";
import { EditCharterDialog } from "../controls/EditCharterDialog";
import { ProjectControlsMenu } from "../controls/ProjectControlsMenu";
import { ReadyForMergeBanner } from "../review/ReadyForMergeBanner";
import { TONE_DOT, formatUsd, plural, stageText, stageTone } from "../factoryFormat";
import { ageText, projectPrList } from "./boardModel";
import { DevEnvDetails, devEnvStatus } from "./DevEnvPill";
import { PreviewPill } from "./PreviewPill";
import { HarnessGlyph, MemberStatusDot, ROLE_SHORT } from "./TeamStrip";

function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="grid grid-cols-1 gap-x-4 gap-y-1.5 border-b border-border py-3 first:pt-0 last:border-b-0 md:grid-cols-[6.5rem_minmax(0,1fr)]">
      <div className="flex items-start justify-between gap-2 md:flex-col md:justify-start">
        <h3 className="text-sm font-medium text-muted-foreground">{title}</h3>
        {action}
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  );
}

function Charter({ project, onEdit }: { project: ProjectView; onEdit: () => void }) {
  const charter = project.charter;
  return (
    <Section
      title="Charter"
      action={
        <Button
          variant="ghost"
          size="xs"
          onClick={onEdit}
          data-testid="charter-edit"
          componentId="factory.project.edit_charter"
          className="text-muted-foreground"
        >
          <PencilIcon />
          Edit
        </Button>
      }
    >
      {charter ? (
        <div data-testid="project-charter" className="flex flex-col gap-2 text-sm">
          <p className="text-ui">{charter.goal}</p>
          {charter.dod.length > 0 && (
            <div>
              <p className="text-muted-foreground">Done when</p>
              <ul className="list-disc pl-4">
                {charter.dod.map((item) => (
                  <li key={item} className="font-mono break-words">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {charter.scope_paths.length > 0 && (
            <p className="flex flex-wrap gap-1">
              <span className="text-muted-foreground">Scope</span>
              {charter.scope_paths.map((path) => (
                <span key={path} className="rounded-sm bg-muted px-1 font-mono">
                  {path}
                </span>
              ))}
            </p>
          )}
          <p className="text-muted-foreground">
            {charter.env_needed ? "Needs an env" : "No env"} ·{" "}
            {charter.preview.enabled
              ? `previews on · ${plural(charter.preview.slots, "slot")}`
              : "previews off"}{" "}
            · up to {plural(charter.budget_ics, "IC")}
          </p>
          {(charter.demo_focus ?? charter.demo) && (
            <p>
              <span className="text-muted-foreground">Demo: </span>
              {charter.demo_focus ?? charter.demo}
            </p>
          )}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No charter stored for this project.</p>
      )}
    </Section>
  );
}

function PrLine({ pr, nowS }: { pr: PrView; nowS: number }) {
  const state = pr.state === "open" && pr.is_draft ? "DRAFT" : pr.state;
  return (
    <li className="flex min-w-0 flex-col gap-1">
      <div className="flex min-w-0 items-center gap-2 text-sm">
        <PullRequestStatus state={state} />
        <a
          href={pr.url}
          target="_blank"
          rel="noreferrer"
          className="shrink-0 font-mono tabular-nums hover:underline"
        >
          #{pr.number}
        </a>
        <span className="min-w-0 truncate" title={pr.title}>
          {pr.title}
        </span>
        <span className="ml-auto shrink-0 text-muted-foreground">
          {pr.merged
            ? `${pr.merged.by ? `@${pr.merged.by} · ` : ""}${ageText(nowS - pr.merged.at)}`
            : pr.ready
              ? "ready"
              : null}
        </span>
      </div>
      <ReadyForMergeBanner pr={pr} />
    </li>
  );
}

function Previews({ project, prs, nowS }: { project: ProjectView; prs: PrView[]; nowS: number }) {
  const slots = project.charter?.preview.slots ?? project.charter?.preview_slots ?? 0;
  const live = prs.filter((pr) => pr.state === "open" && pr.preview);
  const free = Math.max(0, slots - live.length);
  if (!project.charter?.preview.enabled && live.length === 0) {
    return <p className="text-sm text-muted-foreground">Previews are off for this project.</p>;
  }
  return (
    <ul data-testid="project-previews" className="flex flex-col gap-1.5 text-sm">
      {live.map((pr, i) => (
        <li key={pr.id} className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="w-12 shrink-0 text-muted-foreground">
            slot {pr.preview?.slot ?? i + 1}
          </span>
          <span className="shrink-0 font-mono tabular-nums">#{pr.number}</span>
          <PreviewPill pr={pr} project={project} nowS={nowS} />
        </li>
      ))}
      {Array.from({ length: free }, (_, i) => (
        <li key={`free-${i}`} className="flex items-center gap-2 text-muted-foreground">
          <span className="w-12 shrink-0">slot {live.length + i + 1}</span>
          free
        </li>
      ))}
    </ul>
  );
}

export function ProjectDrawer({
  snapshot,
  slug,
  nowS,
  onClose,
}: {
  snapshot: FactorySnapshot;
  slug: string;
  nowS: number;
  onClose: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const project = snapshot.projects.find((p) => p.slug === slug);
  const prs = project ? projectPrList(snapshot, project) : [];
  const decisions = snapshot.decisions.filter(
    (d) =>
      d.project_slug === slug || (project?.pl && d.asked_by_session_id === project.pl.session_id),
  );
  const team = project ? [...(project.pl ? [project.pl] : []), ...project.team] : [];
  const tone = project ? stageTone(project.stage, project.overlay) : "muted";
  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent size="lg" data-testid={`project-drawer-${slug}`}>
        <SheetHeader>
          <div className="flex min-w-0 items-center gap-2 pr-8">
            <SheetTitle className="truncate">{project?.title ?? slug}</SheetTitle>
            {project && (
              <span className="shrink-0 rounded-sm border border-border px-1 font-mono text-[10px] leading-4 text-muted-foreground">
                P{project.priority}
              </span>
            )}
          </div>
          {project ? (
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
              <SheetDescription className="inline-flex items-center gap-1.5">
                <span aria-hidden className={cn("size-1.5 rounded-full", TONE_DOT[tone])} />
                {stageText(project.stage, project.overlay)}
                {project.repo && (
                  <span className="font-mono">
                    · {project.repo}
                    {project.base_branch ? ` · ${project.base_branch}` : ""}
                  </span>
                )}
              </SheetDescription>
              <span className="ml-auto flex items-center gap-1">
                {project.pl && (
                  <Button asChild variant="outline" size="xs">
                    <Link to={project.pl.url} componentId="factory.project.open_pl_chat">
                      <MessageSquareIcon />
                      Open PL chat
                    </Link>
                  </Button>
                )}
                <ProjectControlsMenu
                  factoryId={snapshot.factory.id}
                  project={project}
                  prs={prs}
                  members={snapshot.members}
                />
              </span>
            </div>
          ) : (
            <SheetDescription>This project isn’t on the board.</SheetDescription>
          )}
        </SheetHeader>
        {project && (
          <SheetBody>
            <Charter project={project} onEdit={() => setEditing(true)} />
            <Section title="PRs">
              {prs.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No PRs yet{project.stage === "planning" ? " · the PL is planning" : ""}.
                </p>
              ) : (
                <ul data-testid="project-prs" className="flex flex-col gap-2">
                  {prs.map((pr) => (
                    <PrLine key={pr.id} pr={pr} nowS={nowS} />
                  ))}
                </ul>
              )}
            </Section>
            <Section title="Dev env">
              {project.env ? (
                <div data-testid="project-dev-env" className="flex flex-col gap-1.5">
                  <p className="inline-flex items-center gap-1.5 text-sm">
                    <span
                      aria-hidden
                      className={cn(
                        "size-1.5 rounded-full",
                        TONE_DOT[devEnvStatus(project.env).tone],
                      )}
                    />
                    {devEnvStatus(project.env).word}
                  </p>
                  <DevEnvDetails env={project.env} nowS={nowS} />
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {project.charter?.env_needed === false
                    ? "This project needs no env."
                    : "Not up yet."}
                </p>
              )}
            </Section>
            <Section title="Previews">
              <Previews project={project} prs={prs} nowS={nowS} />
            </Section>
            <Section title="Team">
              {team.length === 0 ? (
                <p className="text-sm text-muted-foreground">No agents yet.</p>
              ) : (
                <ul data-testid="project-team" className="flex flex-col gap-1.5 text-sm">
                  {team.map((member) => (
                    <li key={member.session_id} className="flex min-w-0 items-center gap-2">
                      <HarnessGlyph harness={member.harness} className="size-3.5" />
                      <span className="w-9 shrink-0 text-muted-foreground">
                        {member.role ? ROLE_SHORT[member.role] : "agent"}
                      </span>
                      <MemberStatusDot member={member} />
                      <Link
                        to={member.url}
                        componentId="factory.project.member"
                        className="min-w-0 shrink-0 truncate hover:underline"
                      >
                        {member.title}
                      </Link>
                      <span
                        className="min-w-0 flex-1 truncate text-muted-foreground"
                        title={member.summary ?? undefined}
                      >
                        {member.summary}
                      </span>
                      <span className="shrink-0 font-mono text-muted-foreground tabular-nums">
                        {formatUsd(member.cost_usd)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            <Section title="Decisions">
              {decisions.length === 0 ? (
                <p className="text-sm text-muted-foreground">None asked.</p>
              ) : (
                <ul data-testid="project-decisions" className="flex flex-col gap-1 text-sm">
                  {decisions.map((d) => (
                    <li key={d.id} className="min-w-0">
                      <span className="font-mono">{d.ref}</span>{" "}
                      {d.status === "open" ? (
                        <span className={d.blocking ? "text-warning" : "text-muted-foreground"}>
                          {d.blocking ? "blocking" : "open"} ·{" "}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">{d.status} · </span>
                      )}
                      {d.status === "answered"
                        ? `“${d.answer_text ?? d.options.find((o) => o.id === d.answer_option_id)?.label ?? ""}”`
                        : d.question}
                      {d.delivered_at != null && (
                        <span className="text-success"> · delivered ✓</span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            {project.pl && (
              <Section title="Plan">
                <Link
                  to={`${project.pl.url}?file=${encodeURIComponent(`~/factory/${project.slug}/plan.md`)}`}
                  componentId="factory.project.plan"
                  data-testid="project-plan-link"
                  className="inline-flex items-center gap-1 font-mono text-sm hover:underline"
                >
                  <FileTextIcon aria-hidden className="size-3.5" />
                  ~/factory/{project.slug}/plan.md
                  <ExternalLinkIcon aria-hidden className="size-3" />
                </Link>
              </Section>
            )}
            <Section title="Activity">
              <ActivityList
                factoryId={snapshot.factory.id}
                projects={snapshot.projects}
                members={snapshot.members}
                project={project.slug}
                heightClassName="max-h-80"
              />
            </Section>
          </SheetBody>
        )}
        {project && (
          <EditCharterDialog
            factoryId={snapshot.factory.id}
            project={project}
            open={editing}
            onOpenChange={setEditing}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}
