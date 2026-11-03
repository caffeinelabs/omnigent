// Everything the team chart and editor read about one project's team: its
// team spec (from the snapshot), the live sessions behind each member (the
// fleet read, else the snapshot), the catalog and bench (editor and options),
// its brief texts (the definition) and its history (the definition versions).

import { useMemo } from "react";
import type { FactorySnapshot, ProjectView, TeamSpecView } from "@/lib/factoryApi";
import { waitingOnViewer } from "../agents/fleetModel";
import { useFactoryAgents } from "../agents/opsQueries";
import { type TeamMember, handleName, teamMember, teamRows } from "../agents/teamsModel";
import { useFactoryBench } from "../agents/teamsQueries";
import { useDefinitionVersions, useFactoryDefinition } from "../insights/learnQueries";
import { useTeamCatalog } from "./teamQueries";
import {
  type ChartMember,
  type RestoredTeam,
  TEAM_ROLE_LABEL,
  type TeamHistoryRow,
  type TeamRole,
  chartMembers,
  liveRole,
  teamHistory,
  teamInDoc,
} from "./teamModel";

const NO_BRIEFS: Record<string, string> = {};

export interface ProjectTeamData {
  spec: TeamSpecView | null;
  /** Every live session of the project (leads' seats left out). */
  live: TeamMember[];
  members: ChartMember[];
  /** Sessions no member of the team fits (dispatches outside it). */
  outside: TeamMember[];
  /** Member id → the name the chart shows. */
  names: Map<string, string>;
  catalog: ReturnType<typeof useTeamCatalog>;
  bench: ReturnType<typeof useFactoryBench>;
  briefs: Record<string, string>;
  history: TeamHistoryRow[];
  historyLoading: boolean;
  /** The project's own team in force (`null`: it follows today's bundle). */
  current: RestoredTeam | null;
}

/**
 * @param options.editing - Read the catalog and bench (the editor and its options).
 * @param options.history - Read the definition versions (History, "changed in vN", Undo).
 */
export function useProjectTeam(
  snapshot: FactorySnapshot,
  project: ProjectView,
  options: { editing?: boolean; history?: boolean; catalog?: boolean } = {},
): ProjectTeamData {
  const fid = snapshot.factory.id;
  const slug = project.slug;
  const spec = project.team_spec ?? null;
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
  const root = snapshot.factory.root_session_id;
  const live = useMemo(
    () =>
      rows
        .filter((r) => r.project === slug && r.role !== "lead")
        .map((r) => teamMember(r, root, waitingOnMe)),
    [rows, slug, root, waitingOnMe],
  );
  const { members, outside } = useMemo(() => chartMembers(spec, live), [spec, live]);
  const names = useMemo(() => {
    const out = new Map<string, string>();
    const perRole = new Map<string, number>();
    for (const m of spec?.members ?? []) perRole.set(m.role, (perRole.get(m.role) ?? 0) + 1);
    const seen = new Map<string, number>();
    for (const m of members) {
      const id = m.spec?.id ?? m.live?.row.session_id ?? "";
      const role = m.spec?.role ?? (m.live ? liveRole(m.live) : "agent");
      const n = (seen.get(role) ?? 0) + 1;
      seen.set(role, n);
      const label = TEAM_ROLE_LABEL[role as TeamRole] ?? role;
      const name =
        m.spec?.handle ??
        (m.live?.row.handle ? handleName(m.live.row.handle) : null) ??
        ((perRole.get(role) ?? 0) > 1 ? `${label} ${n}` : label);
      out.set(id, name);
    }
    return out;
  }, [members, spec]);
  const catalog = useTeamCatalog(fid, slug, Boolean(spec) && (options.editing || options.catalog));
  const bench = useFactoryBench(fid, "30d", { enabled: Boolean(options.editing) });
  const definition = useFactoryDefinition(fid);
  const effective = definition.data?.effective as Record<string, unknown> | undefined;
  const current = useMemo(() => teamInDoc(effective, slug), [effective, slug]);
  const briefs = current?.briefs ?? NO_BRIEFS;
  const versions = useDefinitionVersions(fid, Boolean(spec) && Boolean(options.history));
  const history = useMemo(
    () => teamHistory(versions.data?.data ?? [], slug, versions.data?.pinned),
    [versions.data, slug],
  );
  return {
    spec,
    live,
    members,
    outside,
    names,
    catalog,
    bench,
    briefs,
    history,
    historyLoading: versions.isLoading,
    current,
  };
}
