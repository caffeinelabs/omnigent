/**
 * Teams (`/factory/:factoryId/teams/{teams,bench,templates,agents}`), part of
 * the new cockpit: every team side by side (the lead seats, then one card per
 * project with each member on one line: ring, name, role, vendor · model,
 * state), the bench (track record by role × vendor · model with n), a way
 * to the team templates (Steer lists and changes them), and every agent in a
 * table. A filter matches name, role, vendor,
 * model, state and project; the state counts are filters too. Any member
 * opens the agent panel (`?peek=agent:<handle>`).
 */

import { useCallback, useMemo } from "react";
import { ExternalLinkIcon, UsersIcon } from "lucide-react";
import { AgentPanel } from "@/components/factory/agents/AgentPanel";
import { BenchTable } from "@/components/factory/agents/BenchTable";
import { waitingOnViewer } from "@/components/factory/agents/fleetModel";
import { MemberRing, STATE_COLOR, StateWord } from "@/components/factory/agents/MemberRing";
import { isMissingRoute, useFactoryAgents } from "@/components/factory/agents/opsQueries";
import {
  FILTER_WORD,
  type OtherAgents,
  type ProjectTeam,
  SEAT_WORD,
  STATE_FILTERS,
  type StateFilter,
  type TeamMember,
  filterOf,
  groupTeams,
  matchesTeamFilter,
  stateCounts,
  teamLine,
  teamMember,
  teamRows,
} from "@/components/factory/agents/teamsModel";
import { useFactoryBench } from "@/components/factory/agents/teamsQueries";
import { money } from "@/components/factory/agents/TrackRecord";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import {
  TEAMS_TABS,
  type TeamsTab,
  agentPageHref,
  agentsPageHref,
  projectPageHref,
  steerPageHref,
  teamsPageHref,
} from "@/components/factory/factoryRoutes";
import { EmptyState } from "@/components/factory/project/projectParts";
import { teamEditorHref } from "@/components/factory/team/teamStaging";
import { useCockpitV2 } from "@/components/factory/shell/cockpitV2";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { Peek } from "@/components/factory/ui/Peek";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ProjectView } from "@/lib/factoryApi";
import { Link, useNavigate, useParams, useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

const TAB_LABEL: Record<TeamsTab, string> = {
  teams: "Teams",
  bench: "Bench",
  templates: "Templates",
  agents: "All agents",
};

const PEEK_PREFIX = "agent:";

function healthChip(
  project: ProjectView | null,
): { text: string; tone: "ok" | "you" | "bad" } | null {
  if (!project) return null;
  if (project.overlay === "failed" || project.status === "cancelled")
    return { text: "Stopped", tone: "bad" };
  if (project.overlay === "paused") return { text: "Paused", tone: "ok" };
  if (project.stage === "ready") return { text: "Needs a merge", tone: "you" };
  if (project.stage === "merged" || project.stage === "done")
    return { text: "Shipped", tone: "ok" };
  return { text: "On track", tone: "ok" };
}

function MemberLine({ member, onOpen }: { member: TeamMember; onOpen: (m: TeamMember) => void }) {
  return (
    <button
      type="button"
      data-testid={`team-member-${member.row.handle ?? member.row.session_id}`}
      onClick={() => onOpen(member)}
      className="flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <MemberRing member={member} size={28} />
      <span className="min-w-0 grow">
        <span className="block truncate text-sm">
          <span className="font-semibold">{member.name}</span>{" "}
          <span className="text-muted-foreground">{member.role}</span>
        </span>
        <span className="block font-mono text-xs text-muted-foreground">
          {member.vendorModel ?? "model not reported yet"}
        </span>
      </span>
      <StateWord member={member} className="shrink-0" />
    </button>
  );
}

function SeatCard({ member, onOpen }: { member: TeamMember; onOpen: (m: TeamMember) => void }) {
  return (
    <button
      type="button"
      data-testid={`team-seat-${member.seat ?? member.row.session_id}`}
      onClick={() => onOpen(member)}
      className="flex items-start gap-3 rounded-xl border border-border bg-card p-4 text-left hover:border-border-strong focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <MemberRing member={member} size={40} />
      <span className="min-w-0 grow">
        <span className="block text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {member.seat ? SEAT_WORD[member.seat] : "Lead"}
        </span>
        <span className="block text-sm">
          <span className="font-semibold">{member.name}</span>{" "}
          <span className="text-muted-foreground">{member.role}</span>
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-2">
          <span className="font-mono text-xs">
            {member.vendorModel ?? "model not reported yet"}
          </span>
          <StateWord member={member} />
        </span>
        {member.row.summary && (
          <span className="mt-1 line-clamp-2 block text-xs text-muted-foreground">
            {member.row.summary}
          </span>
        )}
      </span>
    </button>
  );
}

function TeamCard({
  factoryId,
  team,
  onOpen,
}: {
  factoryId: string;
  team: ProjectTeam;
  onOpen: (m: TeamMember) => void;
}) {
  const chip = healthChip(team.project);
  return (
    <section
      aria-label={`Team of ${team.title}`}
      data-testid={`team-card-${team.slug}`}
      className="flex flex-col rounded-xl border border-border bg-card"
    >
      <header className="flex items-start gap-2 px-4 pt-3 pb-2">
        <div className="min-w-0 grow">
          <Link
            to={projectPageHref(factoryId, team.slug)}
            componentId="factory.teams.project"
            className="text-sm font-semibold underline-offset-2 hover:underline"
          >
            {team.title}
          </Link>
          <p className="truncate text-xs text-muted-foreground">
            <span className="font-mono">{team.slug}</span>
            {team.costUsd != null ? ` · ${money(team.costUsd)} spent` : ""}
          </p>
        </div>
        {chip && (
          <span
            className={cn(
              "shrink-0 rounded-md px-1.5 py-0.5 text-xs font-medium",
              chip.tone === "you" && "bg-status-yellow/15 text-foreground",
              chip.tone === "bad" && "bg-status-red/15 text-foreground",
              chip.tone === "ok" && "bg-muted text-muted-foreground",
            )}
          >
            {chip.text}
          </span>
        )}
      </header>
      <p className="px-4 pb-1 text-xs text-muted-foreground" data-testid="team-line">
        {teamLine(team.members)}
      </p>
      <div className="flex flex-col px-2 pb-2">
        {team.members.map((m) => (
          <MemberLine key={m.row.session_id} member={m} onOpen={onOpen} />
        ))}
      </div>
      <footer className="mt-auto flex items-center gap-2 border-t border-border px-4 py-2 text-xs text-muted-foreground">
        <span className="grow">
          {team.project?.team_spec && team.project.status !== "cancelled" ? (
            <Link
              to={teamEditorHref(factoryId, team.slug)}
              componentId="factory.teams.edit_team"
              className="font-medium text-foreground underline-offset-2 hover:underline"
            >
              Edit team
            </Link>
          ) : (
            "Editing a team needs its team spec."
          )}
        </span>
        <span>
          {team.members.length} {team.members.length === 1 ? "member" : "members"}
          {team.earlier
            ? ` · ${team.earlier} earlier ${team.earlier === 1 ? "session" : "sessions"}`
            : ""}
        </span>
      </footer>
    </section>
  );
}

/** The leads' helpers and members of shared projects: every counted agent shows somewhere. */
function OtherAgentsCard({
  others,
  onOpen,
}: {
  others: OtherAgents;
  onOpen: (m: TeamMember) => void;
}) {
  return (
    <section
      aria-label="Other agents"
      data-testid="team-card-others"
      className="flex flex-col rounded-xl border border-border bg-card"
    >
      <header className="px-4 pt-3 pb-2">
        <h3 className="text-sm font-semibold">Other agents</h3>
        <p className="text-xs text-muted-foreground">
          Helpers of the leads, and agents on shared work outside a project team.
        </p>
      </header>
      <div className="flex flex-col px-2 pb-2">
        {others.members.map((m) => (
          <MemberLine key={m.row.session_id} member={m} onOpen={onOpen} />
        ))}
      </div>
      <footer className="mt-auto border-t border-border px-4 py-2 text-right text-xs text-muted-foreground">
        {others.members.length} {others.members.length === 1 ? "agent" : "agents"}
        {others.earlier
          ? ` · ${others.earlier} earlier ${others.earlier === 1 ? "session" : "sessions"}`
          : ""}
      </footer>
    </section>
  );
}

function AllAgentsTable({
  members,
  onOpen,
  factoryId,
}: {
  members: TeamMember[];
  onOpen: (m: TeamMember) => void;
  factoryId: string;
}) {
  return (
    <section className="rounded-xl border border-border bg-card" data-testid="all-agents">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground">
              <th scope="col" className="px-4 py-2 font-medium">
                Agent
              </th>
              <th scope="col" className="hidden px-2 py-2 font-medium sm:table-cell">
                Team
              </th>
              <th scope="col" className="px-2 py-2 font-medium">
                Vendor · model
              </th>
              <th scope="col" className="px-2 py-2 font-medium">
                State
              </th>
              <th scope="col" className="hidden px-2 py-2 font-medium md:table-cell">
                Now
              </th>
              <th scope="col" className="hidden px-4 py-2 text-right font-medium sm:table-cell">
                Spent
              </th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.row.session_id} className="border-t border-border">
                <td className="px-4 py-1.5">
                  <button
                    type="button"
                    onClick={() => onOpen(m)}
                    className="flex items-center gap-2 text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  >
                    <MemberRing member={m} size={22} />
                    <span>
                      <span className="font-semibold">{m.name}</span>{" "}
                      <span className="text-xs text-muted-foreground">{m.role}</span>
                    </span>
                  </button>
                </td>
                <td className="hidden px-2 py-1.5 font-mono text-xs sm:table-cell">
                  {m.seat ? "Leads" : (m.row.project ?? "—")}
                </td>
                <td className="px-2 py-1.5 font-mono text-xs">{m.vendorModel ?? "—"}</td>
                <td className="px-2 py-1.5">
                  <StateWord member={m} />
                </td>
                <td className="hidden max-w-xs truncate px-2 py-1.5 text-xs text-muted-foreground md:table-cell">
                  {m.row.now?.tool
                    ? `${m.row.now.tool}${m.row.now.tool_arg ? ` ${m.row.now.tool_arg}` : ""}`
                    : (m.row.summary ?? "")}
                </td>
                <td className="hidden px-4 py-1.5 text-right font-mono text-xs sm:table-cell">
                  {m.row.cost_usd != null ? money(m.row.cost_usd) : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <footer className="border-t border-border px-4 py-2 text-xs text-muted-foreground">
        The fleet query and bulk actions stay in the{" "}
        <Link
          to={agentsPageHref(factoryId)}
          componentId="factory.teams.fleet"
          className="underline underline-offset-2"
        >
          fleet table
        </Link>
        .
      </footer>
    </section>
  );
}

function TeamsSkeleton() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading teams"
      className="grid gap-3 md:grid-cols-2"
      data-testid="teams-loading"
    >
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="h-48 animate-pulse rounded-xl bg-muted" />
      ))}
    </div>
  );
}

export function TeamsPage() {
  const { snapshot, nowS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const root = snapshot.factory.root_session_id;
  const { tab: rawTab } = useParams<{ tab?: string }>();
  const tab: TeamsTab = TEAMS_TABS.find((t) => t === rawTab) ?? "teams";
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const cockpit = useCockpitV2();
  const query = params.get("q") ?? "";
  const stateParam = params.get("state");
  const stateFilter = STATE_FILTERS.find((s) => s === stateParam) ?? null;
  const peek = params.get("peek");
  const setParam = useCallback(
    (key: string, value: string | null) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set(key, value);
          else next.delete(key);
          return next;
        },
        { replace: key !== "peek" },
      );
    },
    [setParams],
  );
  const agents = useFactoryAgents(fid);
  const fallback = agents.error != null && !agents.data;
  const rows = useMemo(
    () => (agents.data || fallback ? teamRows(snapshot, agents.data?.data) : []),
    [agents.data, fallback, snapshot],
  );
  const waitingOnMe = useMemo(
    () => waitingOnViewer(snapshot.decisions, snapshot.viewer),
    [snapshot.decisions, snapshot.viewer],
  );
  const members = useMemo(
    () => rows.map((r) => teamMember(r, root, waitingOnMe)),
    [rows, root, waitingOnMe],
  );
  const projects = snapshot.projects;
  const titleOf = useMemo(() => new Map(projects.map((p) => [p.slug, p.title])), [projects]);
  const shown = useMemo(
    () =>
      members.filter(
        (m) =>
          (!stateFilter || filterOf(m.state) === stateFilter) &&
          matchesTeamFilter(m, query, m.row.project ? titleOf.get(m.row.project) : null),
      ),
    [members, stateFilter, query, titleOf],
  );
  const counts = useMemo(() => stateCounts(members), [members]);
  const shared = projects.filter((p) => p.kind === "integration");
  const grouped = useMemo(
    () =>
      groupTeams(
        shown,
        projects.filter((p) => p.kind !== "integration"),
        new Set(projects.filter((p) => p.kind === "integration").map((p) => p.slug)),
      ),
    [shown, projects],
  );
  const bench = useFactoryBench(fid, "30d", { enabled: tab === "bench" });
  const peekRef = peek?.startsWith(PEEK_PREFIX) ? peek.slice(PEEK_PREFIX.length) : null;
  const peeked = peekRef
    ? (members.find((m) => {
        const ref = peekRef.toLowerCase();
        return m.row.handle === ref || m.row.session_id === ref;
      }) ?? null)
    : null;
  // A link to an agent this factory does not have (or the viewer cannot see) says so.
  const peekMissing = peekRef != null && peeked == null && !(agents.isLoading && !agents.data);
  const open = (m: TeamMember) =>
    setParam("peek", `${PEEK_PREFIX}${m.row.handle ?? m.row.session_id}`);
  const memberView = peeked
    ? (snapshot.members.find((m) => m.session_id === peeked.row.session_id) ?? null)
    : null;
  const filtered = query.trim() !== "" || stateFilter != null;
  return (
    <div data-testid="factory-teams-page" className="flex min-w-0 flex-col gap-4">
      <header className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 grow">
          <h1 className="text-xl font-semibold">Teams</h1>
          <p className="text-sm text-muted-foreground">
            Every team, who is on it, which vendor and model each member runs, and how each is
            doing. {members.length} {members.length === 1 ? "agent" : "agents"}.
          </p>
        </div>
        {cockpit.available && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            componentId="factory.teams.classic"
            onClick={() => {
              cockpit.choose("v1");
              navigate(agentsPageHref(fid));
            }}
          >
            Back to the classic view
          </Button>
        )}
      </header>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {(tab === "teams" || tab === "agents") && (
          <>
            <label htmlFor="teams-filter" className="sr-only">
              Filter by name, role, vendor, model, state or project
            </label>
            <Input
              id="teams-filter"
              data-testid="teams-filter"
              value={query}
              autoComplete="off"
              placeholder="Filter: codex, stuck, reviewer, sonnet…"
              onChange={(e) => setParam("q", e.target.value || null)}
              className="h-8 w-full max-w-xs"
            />
          </>
        )}
        <div role="group" aria-label="States" className="flex flex-wrap gap-1">
          {STATE_FILTERS.map((s) => (
            <button
              key={s}
              type="button"
              aria-pressed={stateFilter === s}
              data-testid={`teams-state-${s}`}
              onClick={() => setParam("state", stateFilter === s ? null : s)}
              className={cn(
                "rounded-md px-2 py-1 text-xs",
                stateFilter === s
                  ? "bg-muted font-semibold text-foreground"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              <span
                aria-hidden
                className="mr-1.5 inline-block size-2 rounded-full align-middle"
                style={{
                  background: s === "idle" || s === "stuck" ? "transparent" : STATE_COLOR[s],
                  border: `1.5px ${s === "stuck" ? "dashed" : "solid"} ${STATE_COLOR[s]}`,
                }}
              />
              <span className="font-semibold text-foreground tabular-nums">{counts[s]}</span>{" "}
              {FILTER_WORD[s as StateFilter]}
            </button>
          ))}
        </div>
      </div>
      <nav role="tablist" aria-label="Teams views" className="flex gap-1 border-b border-border">
        {TEAMS_TABS.map((t) => (
          <Link
            key={t}
            role="tab"
            aria-selected={t === tab}
            to={teamsPageHref(fid, t, Object.fromEntries(params.entries()))}
            componentId={`factory.teams.tab_${t}`}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm",
              t === tab
                ? "border-foreground font-semibold"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {TAB_LABEL[t]}
          </Link>
        ))}
      </nav>
      {agents.error != null && !isMissingRoute(agents.error) && (
        <FactoryLoadError
          error={agents.error}
          updatedAt={agents.dataUpdatedAt}
          onRetry={() => void agents.refetch()}
          what="the agents"
          testId="teams-error"
          className="mb-0"
        />
      )}
      {tab === "bench" ? (
        bench.isLoading ? (
          <div aria-busy="true" className="h-64 animate-pulse rounded-xl bg-muted" />
        ) : bench.error ? (
          <FactoryLoadError
            error={bench.error}
            updatedAt={bench.dataUpdatedAt}
            onRetry={() => void bench.refetch()}
            what="the bench"
            testId="bench-error"
            className="mb-0"
          />
        ) : bench.data ? (
          <BenchTable bench={bench.data} />
        ) : null
      ) : tab === "templates" ? (
        <EmptyState
          testId="teams-templates"
          icon={<UsersIcon aria-hidden className="size-6 text-muted-foreground/50" />}
          title="Team templates live in Steer"
          body="Templates are the named team shapes new projects start from, such as a standard team or a pair. Steer › Team templates shows each one with its members, cost, use and locks, and is where they change. A project's own team changes on its team page."
          action={
            <Button asChild variant="outline" size="sm">
              <Link
                to={steerPageHref(fid, "templates")}
                componentId="factory.teams.templates_steer"
                data-testid="teams-templates-steer"
              >
                Open team templates
              </Link>
            </Button>
          }
        />
      ) : agents.isLoading && !agents.data ? (
        <TeamsSkeleton />
      ) : members.length === 0 ? (
        <EmptyState
          testId="teams-empty"
          icon={<UsersIcon aria-hidden className="size-6 text-muted-foreground/50" />}
          title="No agents yet"
          body="The portfolio lead starts a project lead per project; each brings its own team."
        />
      ) : filtered && shown.length === 0 ? (
        <EmptyState
          testId="teams-no-match"
          title="No member matches"
          body="The filter matches name, role, vendor, model, state and project."
          action={
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                setParams(
                  (prev) => {
                    const next = new URLSearchParams(prev);
                    next.delete("q");
                    next.delete("state");
                    return next;
                  },
                  { replace: true },
                )
              }
            >
              Clear
            </Button>
          }
        />
      ) : tab === "agents" ? (
        <AllAgentsTable members={shown} onOpen={open} factoryId={fid} />
      ) : (
        <>
          {grouped.leads.length > 0 && (
            <section aria-labelledby="teams-leads" className="flex flex-col gap-2">
              <h2
                id="teams-leads"
                className="text-xs font-semibold tracking-wide text-muted-foreground uppercase"
              >
                Leads
              </h2>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {grouped.leads.map((m) => (
                  <SeatCard key={m.row.session_id} member={m} onOpen={open} />
                ))}
              </div>
            </section>
          )}
          <section aria-labelledby="teams-projects" className="flex flex-col gap-2">
            <div className="flex flex-wrap items-baseline gap-x-3">
              <h2
                id="teams-projects"
                className="grow text-xs font-semibold tracking-wide text-muted-foreground uppercase"
              >
                Project teams
              </h2>
              {shared.map((p) => (
                <span
                  key={p.slug}
                  className="text-xs text-muted-foreground"
                  data-testid="teams-shared-env"
                >
                  Shared environment: {p.env?.reported?.state ?? "not reported"}
                  {p.env?.reported?.note ? `, ${p.env.reported.note}` : ""}
                </span>
              ))}
            </div>
            {grouped.teams.length === 0 && grouped.others.members.length === 0 ? (
              <p className="text-sm text-muted-foreground">No project team matches.</p>
            ) : (
              <div className="grid gap-3 md:grid-cols-2">
                {grouped.teams.map((team) => (
                  <TeamCard key={team.slug} factoryId={fid} team={team} onOpen={open} />
                ))}
                {grouped.others.members.length > 0 && (
                  <OtherAgentsCard others={grouped.others} onOpen={open} />
                )}
              </div>
            )}
          </section>
        </>
      )}
      <Peek
        open={peeked != null || peekMissing}
        onOpenChange={(o) => !o && setParam("peek", null)}
        title={
          <span className="text-sm font-normal text-muted-foreground">
            {peeked ? (peeked.seat ? "Leads" : (peeked.row.project ?? "Agent")) : "Agent"}
          </span>
        }
        headerExtra={
          peeked ? (
            <Button asChild variant="ghost" size="sm">
              <Link
                to={agentPageHref(fid, peeked.row.handle ?? peeked.row.session_id)}
                componentId="factory.teams.peek_full_page"
              >
                Full page
                <ExternalLinkIcon aria-hidden className="size-3.5" />
              </Link>
            </Button>
          ) : undefined
        }
      >
        {peekMissing && (
          <EmptyState
            testId="agent-peek-missing"
            icon={<UsersIcon aria-hidden className="size-6 text-muted-foreground/50" />}
            title="Not in this factory"
            body={`No agent here is called “${peekRef}”, or you cannot see it. Its session may have closed, or the link is from another factory.`}
          />
        )}
        {peeked && (
          <AgentPanel
            factoryId={fid}
            member={peeked}
            memberView={memberView}
            project={
              peeked.row.project
                ? (projects.find((p) => p.slug === peeked.row.project) ?? null)
                : null
            }
            nowS={nowS}
            defVersion={snapshot.def_version ?? null}
          />
        )}
      </Peek>
    </div>
  );
}
