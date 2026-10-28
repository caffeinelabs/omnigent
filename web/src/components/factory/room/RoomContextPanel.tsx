/**
 * The Room page's right column: what the thread is about. A project's facts
 * (priority, stage, owner, its PL, tasks and critical path, loops, PRs, env)
 * or a PR's (state, head, gates, preview, demo); the factory or Sentinel for
 * the fixed threads. Then who is here, and "Open session ↗" for the agent
 * that answers.
 */

import type { ReactNode } from "react";
import { ArrowRightIcon, ExternalLinkIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { FactorySnapshot, MemberRef, MemberView, ThreadView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { DemoStatusTag } from "../board/DemoThumb";
import { GateChip, shownGates } from "../board/GateChip";
import { PreviewPill } from "../board/PreviewPill";
import { prStateView } from "../cards/PrCard";
import { TONE_CHIP, formatElapsed, formatUsd, plural } from "../factoryFormat";
import { prPageHref, projectPageHref } from "../factoryRoutes";
import { StageDots } from "../projects/StageDots";
import { PresenceFace } from "../shell/FactoryPresence";
import { peopleHere, threadPr, threadProject } from "./roomModel";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid min-w-0 grid-cols-[5.5rem_minmax(0,1fr)] items-baseline gap-x-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

function SessionLink({ member, label }: { member: MemberRef | MemberView | null; label: string }) {
  if (!member) return null;
  return (
    <Button asChild size="sm" variant="outline" className="justify-start">
      <Link to={member.url} componentId="factory.room.open_session" data-testid="room-open-session">
        <ExternalLinkIcon />
        {label}
      </Link>
    </Button>
  );
}

export function RoomContextPanel({
  snapshot,
  thread,
  threadView,
  nowS,
  className,
}: {
  snapshot: FactorySnapshot;
  thread: string;
  threadView: ThreadView | null;
  nowS: number;
  className?: string;
}) {
  const fid = snapshot.factory.id;
  const project = threadProject(snapshot, thread);
  const pr = threadPr(snapshot, thread);
  const here = peopleHere(snapshot.presence, snapshot, thread);
  const responder =
    snapshot.members.find((m) => m.session_id === threadView?.responder_session_id) ?? null;
  const atlas =
    snapshot.members.find((m) => m.session_id === snapshot.factory.root_session_id) ?? null;

  let facts: ReactNode;
  let actions: ReactNode;
  if (pr) {
    const state = prStateView(pr);
    facts = (
      <>
        <p className="text-ui font-medium break-words">
          <span className="font-mono">!{pr.number}</span> {pr.title}
        </p>
        <dl className="flex flex-col gap-1">
          <Row label="State">
            <span className={cn("rounded-full px-1.5 text-xs leading-5", TONE_CHIP[state.tone])}>
              {state.label}
            </span>
          </Row>
          <Row label="Head">
            <span className="font-mono">{pr.head_sha.slice(0, 7)}</span>
            {pr.base_ref && <span className="text-muted-foreground"> → {pr.base_ref}</span>}
          </Row>
          {project && <Row label="Project">#{project.slug}</Row>}
          <Row label="Gates">
            <span className="flex flex-wrap gap-1">
              {shownGates(pr).map((gate) => (
                <GateChip key={gate} pr={pr} gate={gate} nowS={nowS} />
              ))}
            </span>
          </Row>
          <Row label="Preview">
            <PreviewPill pr={pr} project={project} nowS={nowS} />
          </Row>
          {pr.demo && (
            <Row label="Demo">
              <DemoStatusTag status={pr.demo.status} />
            </Row>
          )}
        </dl>
      </>
    );
    actions = (
      <Button asChild size="sm" variant="outline" className="justify-start">
        <Link
          to={prPageHref(fid, pr.id)}
          componentId="factory.room.open_pr"
          data-testid="room-open-pr"
        >
          <ArrowRightIcon />
          Open PR page
        </Link>
      </Button>
    );
  } else if (project) {
    const tasks = project.tasks_summary;
    const loops = project.loops_summary;
    const prs = snapshot.prs.filter((p) => p.project_slug === project.slug && p.state === "open");
    facts = (
      <>
        <p className="text-ui font-medium break-words">{project.title}</p>
        <dl className="flex flex-col gap-1">
          <Row label="Stage">
            <StageDots project={project} nowS={nowS} />
          </Row>
          <Row label="Priority">P{project.priority}</Row>
          <Row label="Owner">{project.human_owner ?? "operators"}</Row>
          <Row label="PL">
            {project.pl
              ? `${project.pl.title} · ${project.pl.running ? "working" : project.pl.status}`
              : "not up yet"}
          </Row>
          {tasks && (
            <Row label="Tasks">
              {tasks.done}/{tasks.total}
              {tasks.critical_path_min
                ? ` · CP ${formatElapsed(tasks.critical_path_min * 60)}`
                : ""}
              {tasks.blocked ? ` · ${tasks.blocked} blocked` : ""}
            </Row>
          )}
          {loops && (loops.exhausted > 0 || loops.stalled > 0 || loops.running > 0) && (
            <Row label="Loops">
              {loops.running} running
              {loops.exhausted ? ` · ${loops.exhausted} exhausted` : ""}
              {loops.stalled ? ` · ${loops.stalled} stalled` : ""}
            </Row>
          )}
          <Row label="PRs">
            {prs.length === 0
              ? "none open"
              : prs.map((p) => (
                  <Link
                    key={p.id}
                    to={prPageHref(fid, p.id)}
                    componentId="factory.room.context_pr"
                    className="mr-2 font-mono hover:underline"
                  >
                    !{p.number}
                  </Link>
                ))}
          </Row>
          {project.env?.reported && (
            <Row label="Env">
              {project.env.reported.state}
              {project.env.reported.deployed.ref ? ` · ${project.env.reported.deployed.ref}` : ""}
            </Row>
          )}
          {project.cost_usd != null && <Row label="Spent">{formatUsd(project.cost_usd)}</Row>}
          {(project.depends_on?.length ?? 0) > 0 && (
            <Row label="Waits on">{project.depends_on!.map((d) => `#${d}`).join(", ")}</Row>
          )}
        </dl>
      </>
    );
    actions = (
      <Button asChild size="sm" variant="outline" className="justify-start">
        <Link
          to={projectPageHref(fid, project.slug)}
          componentId="factory.room.open_project"
          data-testid="room-open-project"
        >
          <ArrowRightIcon />
          Open project page
        </Link>
      </Button>
    );
  } else {
    const active = snapshot.projects.filter((p) => (p.status ?? "active") === "active");
    facts = (
      <>
        <p className="text-ui font-medium">{snapshot.factory.name}</p>
        <dl className="flex flex-col gap-1">
          <Row label="State">{snapshot.factory.state}</Row>
          <Row label="Projects">{plural(active.length, "active project")}</Row>
          {snapshot.factory.base && (
            <Row label="Base">
              <span className="font-mono">
                {snapshot.factory.base.repo.split("/").pop()}#{snapshot.factory.base.branch}
              </span>
            </Row>
          )}
          <Row label="Decisions">{snapshot.totals.decisions_open} open</Row>
          {snapshot.counts && thread === "improvements" && (
            <Row label="Proposals">
              {snapshot.counts.proposals_pending} pending · {snapshot.counts.on_trial} on trial
            </Row>
          )}
          {snapshot.totals.cost_usd != null && (
            <Row label="Spent">{formatUsd(snapshot.totals.cost_usd)}</Row>
          )}
        </dl>
      </>
    );
    actions = null;
  }

  return (
    <aside
      aria-label="Thread context"
      data-testid="room-context"
      className={cn(
        "flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-3 text-sm",
        className,
      )}
    >
      <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Context</h2>
      {facts}
      <div className="flex flex-col gap-1.5">
        {actions}
        <SessionLink
          member={responder ?? atlas}
          label={`Open ${(responder ?? atlas)?.title ?? "agent"} session ↗`}
        />
      </div>
      <div className="flex flex-col gap-1.5 border-t border-border pt-3">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          People here
        </p>
        {here.length === 0 ? (
          <p className="text-muted-foreground">Only you</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {here.map((p) => (
              <li key={p.user_id} className="flex min-w-0 items-center gap-2">
                <PresenceFace userId={p.user_id} idle={p.idle} />
                <span className="min-w-0 truncate">{p.name ?? p.user_id}</span>
                {p.idle && <span className="ml-auto text-xs text-muted-foreground">idle</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
