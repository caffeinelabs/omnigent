/**
 * The project page's team (new cockpit): a small org chart with a header that
 * says the template, members, builders at once, the definition version that
 * last changed it, spend against the budget and $/h while working, History
 * (`?peek=team-history`) and Edit team (`/projects/:slug/team`, also the `e`
 * key). A tile opens the agent panel (`?peek=agent:<handle>`). While a team
 * change can still be undone (10 minutes), the header offers Undo; while
 * changes are staged, a bar says so and goes back to the editor.
 */

import { ExternalLinkIcon, HistoryIcon, PencilIcon, Undo2Icon, UsersIcon } from "lucide-react";
import { useCallback, useEffect, useMemo } from "react";
import { Button } from "@/components/ui/button";
import type { FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { Link, useNavigate, useSearchParams } from "@/lib/routing";
import { AgentPanel } from "../agents/AgentPanel";
import { money } from "../agents/TrackRecord";
import { agentPageHref } from "../factoryRoutes";
import { formatElapsed, plural } from "../factoryFormat";
import { usePinDefinition } from "../insights/learnQueries";
import { EmptyState } from "../project/projectParts";
import { SECTION_SCROLL_MARGIN } from "../projects/ProjectSections";
import { Peek } from "../ui/Peek";
import { StagedBar } from "../ui/StagedBar";
import { DATA_HUE } from "../ui/statusTokens";
import { useFactoryToast } from "../ui/Toast";
import { TeamChart } from "./TeamChart";
import { TeamHistory } from "./TeamHistory";
import { ViewTile } from "./TeamTile";
import { type ChartMember, type StagedTeamChange, teamRate, undoLeft } from "./teamModel";
import { stageTeamChanges, teamEditorHref, useTeamStaging } from "./teamStaging";
import { useProjectTeam } from "./useProjectTeam";

export const TEAM_HISTORY_PEEK = "team-history";
const AGENT_PEEK = "agent:";

/** True when a key press belongs to whatever has focus (a field, a menu), not to a page key. */
export function keyBelongsToTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return true;
  return target.closest("[role=menu],[role=listbox],[role=dialog],[role=combobox]") !== null;
}

/** `e` opens `onEdit` (unless a field or a dialog has the key). */
export function useEditKey(onEdit: (() => void) | null): void {
  useEffect(() => {
    if (!onEdit) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "e" || event.repeat || event.defaultPrevented) return;
      if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      if (keyBelongsToTarget(event.target)) return;
      event.preventDefault();
      onEdit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onEdit]);
}

function SpendBar({ spent, budget }: { spent: number; budget: number }) {
  const share = budget > 0 ? Math.min(1, spent / budget) : 0;
  return (
    <span aria-hidden className="relative block h-1.5 w-20 overflow-hidden rounded-full bg-muted">
      <span
        className="absolute inset-y-0 left-0 rounded-full"
        style={{ width: `${share * 100}%`, background: DATA_HUE }}
      />
    </span>
  );
}

/**
 * @param sectionId - The page section id it stands for (`team`), so the
 *   section nav and `#team` land on it.
 */
export function TeamSection({
  snapshot,
  project,
  nowS,
  sectionId,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
  sectionId?: string;
}) {
  const fid = snapshot.factory.id;
  const slug = project.slug;
  const data = useProjectTeam(snapshot, project, { history: true, catalog: true });
  const { spec, members, names } = data;
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const toast = useFactoryToast();
  const pin = usePinDefinition(fid);
  const [staging] = useTeamStaging(fid, slug);
  const viewer = snapshot.viewer ?? null;
  const mayEdit = spec != null && viewer?.role !== "viewer";
  const owner = viewer == null || viewer.role === "owner" || viewer.role === "creator";
  const peek = params.get("peek");
  const setPeek = useCallback(
    (value: string | null) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set("peek", value);
          else next.delete("peek");
          return next;
        },
        { replace: value == null },
      ),
    [setParams],
  );
  const edit = useCallback(() => navigate(teamEditorHref(fid, slug)), [navigate, fid, slug]);
  useEditKey(mayEdit ? edit : null);

  const latest = data.history[0] ?? null;
  const left = undoLeft(latest, nowS, { userId: viewer?.user_id, owner });
  const undo = () => {
    if (!latest?.parent) return;
    void pin.mutateAsync(latest.parent).then(
      () =>
        toast.show({
          message: `Undone. The team is back as in v${latest.parent}; v${latest.version} stays in history.`,
        }),
      (error: Error) => toast.show({ message: `Could not undo: ${error.message}` }),
    );
  };
  const rate = useMemo(
    () =>
      spec
        ? teamRate(
            data.catalog.data,
            spec.members.map((m) => ({ role: m.role, vendor: m.vendor, model: m.model })),
            spec.builders_at_once,
          )
        : null,
    [spec, data.catalog.data],
  );
  const restore = (change: Extract<StagedTeamChange, { kind: "restore" }>) => {
    stageTeamChanges(fid, slug, [change]);
    setPeek(null);
    edit();
  };
  const peekRef = peek?.startsWith(AGENT_PEEK) ? peek.slice(AGENT_PEEK.length).toLowerCase() : null;
  const peeked = peekRef
    ? (data.live.find((m) => m.row.handle === peekRef || m.row.session_id === peekRef) ?? null)
    : null;
  const open = (member: ChartMember) =>
    member.live
      ? setPeek(`${AGENT_PEEK}${member.live.row.handle ?? member.live.row.session_id}`)
      : undefined;
  const spent = project.cost_usd ?? 0;
  const templateLabel = spec?.template
    ? (data.catalog.data?.templates.find((t) => t.id === spec.template)?.label ?? spec.template)
    : spec
      ? "Bundle"
      : null;
  // Not `latest`: after an Undo (or an older pin) the newest row is not the team in force.
  const changedIn = data.changedIn;
  return (
    <section
      id={sectionId}
      aria-labelledby="team-heading"
      data-testid="team-section"
      className="flex min-w-0 flex-col gap-4 rounded-xl border border-border bg-card p-4"
      style={sectionId ? { scrollMarginTop: SECTION_SCROLL_MARGIN } : undefined}
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 id="team-heading" className="text-base font-semibold">
          Team
        </h2>
        {templateLabel && (
          <span className="rounded-md border border-border px-1.5 py-0.5 text-xs font-medium">
            {templateLabel}
          </span>
        )}
        {spec && (
          <span className="text-xs text-muted-foreground" data-testid="team-summary">
            {plural(spec.members.length, "member")} · {plural(spec.builders_at_once, "builder")} at
            once
            {changedIn != null ? ` · changed in definition v${changedIn}` : ""}
          </span>
        )}
        <span className="grow" />
        {spec && (
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>
              {money(spent)} of {money(spec.budget_usd)}
              {rate != null ? ` · ${money(rate)}/h while working` : ""}
            </span>
            <SpendBar spent={spent} budget={spec.budget_usd} />
          </span>
        )}
        {left != null && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            componentId="factory.team.undo"
            data-testid="team-undo"
            disabled={pin.isPending}
            onClick={undo}
          >
            <Undo2Icon aria-hidden className="size-3.5" />
            Undo v{latest?.version}{" "}
            <span className="text-muted-foreground">({formatElapsed(left)} left)</span>
          </Button>
        )}
        {spec && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            componentId="factory.team.history"
            onClick={() => setPeek(TEAM_HISTORY_PEEK)}
          >
            <HistoryIcon aria-hidden className="size-3.5" />
            History
          </Button>
        )}
        {mayEdit && (
          <Button asChild variant="outline" size="sm">
            <Link
              to={teamEditorHref(fid, slug)}
              componentId="factory.team.edit"
              data-testid="team-edit-link"
              aria-keyshortcuts="e"
            >
              <PencilIcon aria-hidden className="size-3.5" />
              Edit team
            </Link>
          </Button>
        )}
      </header>
      {spec?.summary && <p className="-mt-2 text-xs text-muted-foreground">{spec.summary}</p>}
      {members.length === 0 ? (
        <EmptyState
          testId="team-empty"
          icon={<UsersIcon aria-hidden className="size-6 text-muted-foreground/50" />}
          title="No team yet"
          body="The project lead starts its team when the project starts."
        />
      ) : (
        <TeamChart
          members={members}
          tileKey={(m) => m.spec?.id ?? m.live?.row.session_id ?? ""}
          renderTile={(m) => {
            const id = m.spec?.id ?? m.live?.row.session_id ?? "";
            return (
              <ViewTile
                member={m}
                name={names.get(id) ?? id}
                runsOn={
                  m.spec
                    ? `${m.spec.vendor} · ${m.spec.model_label ?? m.spec.model ?? "default"}`
                    : (m.live?.vendorModel ?? null)
                }
                onOpen={m.live ? () => open(m) : undefined}
              />
            );
          }}
        />
      )}
      {spec?.drifted && (
        <ul
          className="flex flex-col gap-0.5 text-xs text-muted-foreground"
          data-testid="team-drift"
          aria-label="Differs from the team"
        >
          {spec.drift.map((d) => (
            <li key={`${d.kind}-${d.session_id ?? ""}-${d.text}`}>{d.text}</li>
          ))}
        </ul>
      )}
      {staging.changes.length > 0 && (
        <StagedBar
          count={staging.changes.length}
          noun="team change"
          context="not applied yet"
          placement="inline"
          className="self-start shadow-none"
          onReview={edit}
        />
      )}
      <Peek
        open={peek === TEAM_HISTORY_PEEK}
        onOpenChange={(o) => !o && setPeek(null)}
        title="Team history"
        description="Each change is a definition version."
      >
        {peek === TEAM_HISTORY_PEEK && (
          <TeamHistory
            rows={data.history}
            current={data.current}
            nowS={nowS}
            loading={data.historyLoading}
            names={names}
            onRestore={mayEdit ? restore : undefined}
          />
        )}
      </Peek>
      <Peek
        open={peeked != null}
        onOpenChange={(o) => !o && setPeek(null)}
        title={<span className="text-sm font-normal text-muted-foreground">{project.title}</span>}
        headerExtra={
          peeked ? (
            <Button asChild variant="ghost" size="sm">
              <Link
                to={agentPageHref(fid, peeked.row.handle ?? peeked.row.session_id)}
                componentId="factory.team.peek_full_page"
              >
                Full page
                <ExternalLinkIcon aria-hidden className="size-3.5" />
              </Link>
            </Button>
          ) : undefined
        }
      >
        {peeked && (
          <AgentPanel
            factoryId={fid}
            member={peeked}
            memberView={
              snapshot.members.find((m) => m.session_id === peeked.row.session_id) ?? null
            }
            project={project}
            nowS={nowS}
            defVersion={snapshot.def_version ?? null}
          />
        )}
      </Peek>
    </section>
  );
}
