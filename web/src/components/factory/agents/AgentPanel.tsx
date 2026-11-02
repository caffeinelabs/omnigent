/**
 * The agent panel (J3): who it is, one fact-built sentence about what it is
 * doing and whether you need to act, its levers, what it runs now, its last
 * hours as a state strip with sparklines, its track record against the same
 * role on every vendor and model (with n), its seat when it is a lead, its
 * instructions, and the debug views one click away. The same content is the
 * full page at `/agents/:handle`.
 */

import { type ReactNode, useState } from "react";
import { MessageSquareIcon, PauseIcon, PlayIcon, RotateCcwIcon, ZapIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { showToast } from "@/components/ui/toast";
import type { MemberView, ProjectView } from "@/lib/factoryApi";
import { TIMELINE_WINDOWS, type TimelineWindow } from "@/lib/factoryTeamsApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { useFactoryControls } from "../controls/useFactoryControls";
import { agentPageHref, projectPageHref, roomPageHref } from "../factoryRoutes";
import { ReasonGated, useViewerCan } from "../shell/viewerCan";
import { AgentStateSentence } from "./AgentStateSentence";
import { AgentStateStrip, Sparkline, seriesSummary } from "./AgentStateStrip";
import { agentSentence, shortSpan } from "./agentSentence";
import { MemberRing, StateWord } from "./MemberRing";
import { SEAT_OWNS, SEAT_WORD, type TeamMember, modelLabel, vendorOf } from "./teamsModel";
import { useAgentTimeline, useFactoryBench } from "./teamsQueries";
import { TrackRecord, money } from "./TrackRecord";

export interface AgentPanelProps {
  factoryId: string;
  member: TeamMember;
  /** The snapshot's member row (check-in cadence, pause state), when known. */
  memberView?: MemberView | null;
  project: ProjectView | null;
  /** Server clock, epoch seconds. */
  nowS: number;
  defVersion?: number | null;
  /** The full page (wider sparklines, no "Full page" link). */
  full?: boolean;
  initialWindow?: TimelineWindow;
}

/** Every classic Agent page tab, one click away. */
const DEBUG_TABS = [
  ["live", "Live"],
  ["transcript", "Transcript"],
  ["tools", "Tool calls"],
  ["mail", "Mail"],
  ["terminal", "Terminal"],
  ["diff", "Diff"],
  ["task", "Task & loop"],
  ["cost", "Cost"],
] as const;

function Section({
  title,
  children,
  aside,
}: {
  title: string;
  children: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2 border-t border-border pt-3" aria-label={title}>
      <div className="flex items-center gap-2">
        <h3 className="grow text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {title}
        </h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function roomThread(member: TeamMember): string {
  if (member.row.project) return `project:${member.row.project}`;
  if (member.seat === "reliability") return "improvements";
  return "factory";
}

function controlTarget(member: TeamMember): `project:${string}` | `member:${string}` {
  return member.row.role === "project_lead" && member.row.project
    ? `project:${member.row.project}`
    : `member:${member.row.session_id}`;
}

function Levers({ factoryId, member }: { factoryId: string; member: TeamMember }) {
  const { can } = useViewerCan();
  const controls = useFactoryControls(factoryId);
  const top = member.row.is_top_level;
  const paused = member.row.state === "paused";
  const pauseOrResume = async (action: "pause" | "resume") => {
    const res = await controls.send(
      { action, target: controlTarget(member) },
      { label: member.name },
    );
    if (!res || action !== "pause") return;
    showToast(
      <span className="inline-flex items-center gap-2">
        {member.name} is pausing.
        <button
          type="button"
          className="font-medium underline underline-offset-2"
          onClick={() => void controls.send({ action: "resume", target: controlTarget(member) })}
        >
          Undo
        </button>
      </span>,
    );
  };
  // A stopped member restarts or relaunches; pausing it would change nothing.
  const mayControl = top && can.control && member.state !== "stopped";
  const mayRestart = top && can.restart;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap gap-2" data-testid="agent-levers">
        {can.post && (
          <Button asChild variant="outline" size="sm">
            <Link
              to={roomPageHref(factoryId, roomThread(member))}
              componentId="factory.agent_panel.message"
            >
              <MessageSquareIcon aria-hidden className="size-3.5" />
              Message
            </Link>
          </Button>
        )}
        {mayControl && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            componentId={paused ? "factory.agent_panel.resume" : "factory.agent_panel.pause"}
            onClick={() => void pauseOrResume(paused ? "resume" : "pause")}
          >
            {paused ? (
              <PlayIcon aria-hidden className="size-3.5" />
            ) : (
              <PauseIcon aria-hidden className="size-3.5" />
            )}
            {paused ? "Resume" : "Pause"}
          </Button>
        )}
        {mayRestart && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            componentId="factory.agent_panel.restart"
            onClick={() =>
              void controls
                .send({ action: "restart", target: controlTarget(member) }, { label: member.name })
                .then((res) => {
                  if (res) showToast(`${member.name} restarts after its current step.`);
                })
            }
          >
            <RotateCcwIcon aria-hidden className="size-3.5" />
            Restart
          </Button>
        )}
        {can.settings && (
          <ReasonGated reason="Changing a member's model comes with team specs.">
            <Button type="button" variant="outline" size="sm" disabled>
              <ZapIcon aria-hidden className="size-3.5" />
              Change model…
            </Button>
          </ReasonGated>
        )}
      </div>
      {!can.control && !can.post && (
        <p className="text-xs text-muted-foreground">Ask an operator to change this.</p>
      )}
      {!top && (can.control || can.post) && (
        <p className="text-xs text-muted-foreground">
          Its project lead starts, stops and reassigns it; message the project to steer it.
        </p>
      )}
    </div>
  );
}

function NowSection({ member, nowS }: { member: TeamMember; nowS: number }) {
  const now = member.row.now;
  const task = member.row.task;
  const loop = member.row.loop;
  if (member.state !== "working" && member.state !== "stuck") return null;
  if (!now?.tool && !task) return null;
  return (
    <Section title="Now">
      {task && (
        <p className="text-sm">
          <span className="font-mono text-xs text-muted-foreground">{task.ref}</span>{" "}
          <span className="font-medium">{task.title}</span>
          {loop ? (
            <span className="text-muted-foreground">
              {" "}
              · {loop.kind} {loop.iteration}
              {loop.limit ? `/${loop.limit}` : ""}
            </span>
          ) : null}
        </p>
      )}
      {now?.tool && (
        <pre className="overflow-x-auto rounded-md bg-muted px-2 py-1.5 font-mono text-xs whitespace-pre-wrap">
          $ {now.tool}
          {now.tool_arg ? ` ${now.tool_arg}` : ""}
          {now.tool_since != null ? `  (${shortSpan(nowS - now.tool_since)})` : ""}
        </pre>
      )}
      {now?.usd_per_h != null && (
        <p className="text-xs text-muted-foreground">{money(now.usd_per_h)}/h while working</p>
      )}
    </Section>
  );
}

function StripSection({
  factoryId,
  member,
  initialWindow,
  full,
}: {
  factoryId: string;
  member: TeamMember;
  initialWindow: TimelineWindow;
  full: boolean;
}) {
  const [window, setWindow] = useState<TimelineWindow>(initialWindow);
  const ref = member.row.handle ?? member.row.session_id;
  const timeline = useAgentTimeline(factoryId, ref, window);
  const data = timeline.data;
  const label = window === "7d" ? "7 days" : window === "6h" ? "6 hours" : "24 hours";
  const totals = data ? seriesSummary(data.series) : null;
  return (
    <Section
      title={`Last ${label}`}
      aside={
        <div
          role="group"
          aria-label="Window"
          className="inline-flex rounded-md border border-border"
        >
          {TIMELINE_WINDOWS.map((w) => (
            <button
              key={w}
              type="button"
              aria-pressed={w === window}
              onClick={() => setWindow(w)}
              className={cn(
                "px-2 py-0.5 text-xs first:rounded-l-md last:rounded-r-md",
                w === window ? "bg-muted font-semibold text-foreground" : "text-muted-foreground",
              )}
            >
              {w}
            </button>
          ))}
        </div>
      }
    >
      {timeline.isLoading && !data ? (
        <div aria-busy="true" className="h-14 animate-pulse rounded bg-muted" />
      ) : timeline.error && !data ? (
        <p role="alert" className="text-sm text-muted-foreground">
          Could not load its last hours. Everything else here still works.
        </p>
      ) : data ? (
        <>
          <AgentStateStrip
            pieces={data.segments}
            sinceS={data.since}
            untilS={data.until}
            windowLabel={window}
            totals={data.totals}
          />
          {data.partial && (
            <p className="text-xs text-muted-foreground" data-testid="strip-partial">
              Partial:{" "}
              {data.recorded_since
                ? `recorded since ${new Date(data.recorded_since * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}; earlier time shows as idle.`
                : "nothing recorded yet for this agent."}
            </p>
          )}
          <div className={cn("grid gap-3", full ? "sm:grid-cols-2" : "grid-cols-2")}>
            <div>
              <p className="text-xs text-muted-foreground">
                Tool calls per{" "}
                {data.bucket_s >= 3600 ? `${data.bucket_s / 3600} h` : `${data.bucket_s / 60} min`}
                {totals ? ` · ${totals.calls} in all` : ""}
              </p>
              <Sparkline
                values={data.series.map((b) => b.tool_calls)}
                label={`${totals?.calls ?? 0} tool calls in the last ${label}`}
              />
            </div>
            <div>
              <p className="text-xs text-muted-foreground">
                Spend per{" "}
                {data.bucket_s >= 3600 ? `${data.bucket_s / 3600} h` : `${data.bucket_s / 60} min`}
                {totals ? ` · ${money(totals.usd)}` : ""}
              </p>
              <Sparkline
                values={data.series.map((b) => b.cost_usd)}
                label={`${money(totals?.usd ?? 0)} spent in the last ${label}`}
              />
            </div>
          </div>
        </>
      ) : null}
    </Section>
  );
}

function SeatSection({
  member,
  memberView,
}: {
  member: TeamMember;
  memberView: MemberView | null;
}) {
  if (!member.seat) return null;
  const beat = memberView?.heartbeat_s;
  return (
    <Section title={SEAT_WORD[member.seat]}>
      <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1.5 text-sm">
        <dt className="text-muted-foreground">Owns</dt>
        <dd>{SEAT_OWNS[member.seat]}</dd>
        <dt className="text-muted-foreground">Model</dt>
        <dd className="font-mono text-xs">{member.vendorModel ?? "not reported yet"}</dd>
        {beat ? (
          <>
            <dt className="text-muted-foreground">Check-ins</dt>
            <dd>every {shortSpan(beat)} while idle</dd>
          </>
        ) : null}
        <dt className="text-muted-foreground">Brief</dt>
        <dd>None yet; seat briefs are set in Steer.</dd>
      </dl>
    </Section>
  );
}

export function AgentPanel({
  factoryId,
  member,
  memberView = null,
  project,
  nowS,
  defVersion = null,
  full = false,
  initialWindow = "24h",
}: AgentPanelProps) {
  const row = member.row;
  const sentence = agentSentence({ member, project, nowS });
  const lead = member.seat != null;
  const bench = useFactoryBench(factoryId, "30d", { enabled: !lead });
  const share =
    project?.cost_usd && row.cost_usd != null && project.cost_usd > 0
      ? Math.round((row.cost_usd / project.cost_usd) * 100)
      : null;
  return (
    <div
      className="flex min-w-0 flex-col gap-3"
      data-testid="agent-panel"
      data-handle={row.handle ?? undefined}
    >
      <div className="flex items-start gap-3">
        <MemberRing member={member} size={48} />
        <div className="min-w-0 grow">
          <h2 className="text-lg leading-tight font-semibold">
            {member.name}{" "}
            <span className="text-sm font-medium text-muted-foreground">
              · {member.role}
              {project ? (
                <>
                  {" on "}
                  <Link
                    to={projectPageHref(factoryId, project.slug)}
                    componentId="factory.agent_panel.project"
                    className="underline-offset-2 hover:underline"
                  >
                    {project.title}
                  </Link>
                </>
              ) : null}
            </span>
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
            <StateWord member={member} />
            {member.vendorModel && <span className="font-mono text-xs">{member.vendorModel}</span>}
            {row.cost_usd != null && (
              <span className="text-xs text-muted-foreground">
                {money(row.cost_usd)} spent{share != null ? ` · ${share} % of the project` : ""}
              </span>
            )}
            {row.handle && (
              <span className="font-mono text-xs text-muted-foreground">@{row.handle}</span>
            )}
          </div>
        </div>
      </div>
      <AgentStateSentence sentence={sentence} />
      <Levers factoryId={factoryId} member={member} />
      <NowSection member={member} nowS={nowS} />
      <StripSection
        factoryId={factoryId}
        member={member}
        initialWindow={initialWindow}
        full={full}
      />
      {lead ? (
        <SeatSection member={member} memberView={memberView} />
      ) : (
        <Section title="Track record · 30 days">
          <TrackRecord
            role={row.role ?? ""}
            vendor={vendorOf(row)}
            model={row.model_label ?? modelLabel(row.model)}
            bench={bench.data}
            loading={bench.isLoading}
          />
        </Section>
      )}
      {!lead && (
        <Section title="Instructions">
          <p className="text-sm">
            Role brief: none yet
            {defVersion != null ? (
              <span className="text-muted-foreground"> (definition v{defVersion})</span>
            ) : null}
          </p>
          <p className="text-sm text-muted-foreground">
            Built-in prompt and skills: platform code, read-only.
          </p>
        </Section>
      )}
      <Section title="Debug">
        <div className="flex flex-wrap gap-1.5">
          {DEBUG_TABS.map(([tab, label]) => (
            <Button key={tab} asChild variant="ghost" size="sm">
              <Link
                to={agentPageHref(factoryId, row.session_id, { tab })}
                componentId={`factory.agent_panel.debug_${tab}`}
              >
                {label}
              </Link>
            </Button>
          ))}
        </div>
      </Section>
    </div>
  );
}
