/**
 * The team editor (`/projects/:slug/team`): the project's org chart turned into
 * a form. A violet edit bar says nothing applies until Apply and holds the
 * template, builders at once and budget; every tile gets a vendor · model
 * select, Instructions… and Remove (locked when a gate needs it); ghost tiles
 * add a role from the catalog; the staged panel (sticky on the right, after
 * the chart on a phone with a bar above the tab bar) says what the changes
 * cost and do, from the server's preview. Apply creates one definition
 * version, with Undo for 10 minutes (the toast, `u`, and the team header).
 *
 * The editor is read-only (stage and preview, no Apply) until the server
 * enforces team specs (`OMNIGENT_FACTORY_TEAM_SPEC=enforce`).
 */

import { ChevronRightIcon, MinusIcon, PencilIcon, PlusIcon, UsersIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import type { FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { Link, useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { AVATAR_STATE } from "../agents/MemberRing";
import { projectPageHref, projectsPageHref } from "../factoryRoutes";
import { useDefinitionEdit, usePinDefinition, useProposeChange } from "../insights/learnQueries";
import { EmptyState } from "../project/projectParts";
import { StagedBar } from "../ui/StagedBar";
import { FOCUS_RING } from "../ui/statusTokens";
import { useFactoryToast } from "../ui/Toast";
import { RoleCatalogMenu } from "./RoleCatalogMenu";
import { StagedTeamPanel } from "./StagedTeamPanel";
import { TeamChart } from "./TeamChart";
import { EditTileCard, GhostTile } from "./TeamTile";
import {
  BRIEF_MAX,
  BUDGET_MAX,
  BUDGET_MIN,
  BUILDERS_MAX,
  BUILDERS_MIN,
  type EditTile,
  type StagedTeamChange,
  TEAM_ROLE_LABEL,
  type TeamRole,
  clearedStaging,
  definitionChanges,
  dropChange,
  hasRestore,
  nextAddKey,
  normalizeStaging,
  removeLock,
  stageChange,
  stagedTeam,
  teamBase,
  teamFacts,
} from "./teamModel";
import { useTeamPreview } from "./teamQueries";
import { useTeamStaging } from "./teamStaging";
import { useProjectTeam } from "./useProjectTeam";

/** Why Apply is off while the server only records dispatches outside a team. */
export const NOT_ENFORCED =
  "Preview only: applying team changes stays off until this server enforces teams (OMNIGENT_FACTORY_TEAM_SPEC=enforce). You can stage changes and see what they would do.";
const WATCHING = "You can look, not change: operators and owners edit teams.";
/** The staged panel's "Who applies" while the editor is read-only (the edit bar says why). */
const NOBODY_YET = "No one yet: this server previews team changes but does not apply them.";
const NOT_YOU = "Operators and owners. You can look, not change.";

function Breadcrumb({ factoryId, project }: { factoryId: string; project: ProjectView }) {
  return (
    <nav aria-label="Breadcrumb" data-testid="team-editor-breadcrumb">
      <ol className="flex min-w-0 items-center gap-1 text-sm text-muted-foreground">
        <li className="shrink-0">
          <Link
            to={projectsPageHref(factoryId)}
            componentId="factory.team.crumb_projects"
            className="hover:text-foreground"
          >
            Projects
          </Link>
        </li>
        <li className="flex min-w-0 items-center gap-1">
          <ChevronRightIcon aria-hidden className="size-3.5 shrink-0 opacity-60" />
          <Link
            to={projectPageHref(factoryId, project.slug)}
            componentId="factory.team.crumb_project"
            className="min-w-0 truncate hover:text-foreground"
          >
            {project.slug}
          </Link>
        </li>
        <li className="flex shrink-0 items-center gap-1" aria-current="page">
          <ChevronRightIcon aria-hidden className="size-3.5 shrink-0 opacity-60" />
          <span className="text-foreground">Edit team</span>
        </li>
      </ol>
    </nav>
  );
}

function BriefDialog({
  open,
  name,
  role,
  initial,
  onStage,
  onOpenChange,
}: {
  open: boolean;
  name: string;
  role: string;
  initial: string;
  onStage: (text: string) => void;
  onOpenChange: (open: boolean) => void;
}) {
  const [text, setText] = useState(initial);
  useEffect(() => {
    if (open) setText(initial);
  }, [open, initial]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" data-testid="team-brief-dialog">
        <DialogHeader>
          <DialogTitle>Instructions for {name} on this project</DialogTitle>
          <DialogDescription>
            Added to the {role.toLowerCase()} brief for this project only. It is staged with the
            other team changes and cannot replace guards or protocol.
          </DialogDescription>
        </DialogHeader>
        <label htmlFor="team-brief-text" className="sr-only">
          Instructions
        </label>
        <Textarea
          id="team-brief-text"
          value={text}
          maxLength={BRIEF_MAX}
          onChange={(e) => setText(e.target.value)}
          placeholder="For example: run the full demo after every push."
          className="min-h-28"
        />
        <p className="text-xs text-muted-foreground">
          {text.length} / {BRIEF_MAX}. Leave it empty to clear the instructions.
        </p>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            componentId="factory.team.brief_stage"
            onClick={() => {
              onStage(text);
              onOpenChange(false);
            }}
          >
            Stage
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function TeamEditor({
  snapshot,
  project,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
}) {
  const fid = snapshot.factory.id;
  const slug = project.slug;
  const data = useProjectTeam(snapshot, project, { editing: true });
  const { spec, names } = data;
  const catalog = data.catalog.data;
  const bench = data.bench.data;
  const [staging, setStaging] = useTeamStaging(fid, slug);
  const navigate = useNavigate();
  const toast = useFactoryToast();
  const phone = useIsMobileViewport();
  const edit = useDefinitionEdit(fid);
  const pin = usePinDefinition(fid);
  const propose = useProposeChange(fid);
  const panelRef = useRef<HTMLDivElement>(null);
  const [brief, setBrief] = useState<EditTile | null>(null);
  const viewer = snapshot.viewer ?? null;
  const watcher = viewer?.role === "viewer";

  const base = useMemo(
    () => (spec ? teamBase(slug, spec, data.briefs) : null),
    [spec, slug, data.briefs],
  );
  // Changes staged from elsewhere (the palette appends them raw, also while the
  // editor is open) merge as if staged here, once the team and catalog are known.
  const normalized = useRef<string | null>(null);
  useEffect(() => {
    if (!base || !catalog) return;
    const raw = JSON.stringify(staging);
    if (raw === normalized.current) return;
    const next = normalizeStaging(staging, base, catalog);
    normalized.current = JSON.stringify(next);
    if (normalized.current !== raw) setStaging(next);
  }, [base, catalog, staging, setStaging]);

  const team = useMemo(
    () => (base ? stagedTeam(base, staging, catalog) : null),
    [base, staging, catalog],
  );
  const changes = useMemo(
    () => (team ? definitionChanges(slug, staging, team, catalog) : []),
    [slug, staging, team, catalog],
  );
  const preview = useTeamPreview(fid, changes, staging.when, spec?.def_version ?? null);
  const facts = teamFacts(catalog);
  const restoring = hasRestore(staging);
  const enforced = (catalog?.mode ?? spec?.mode) === "enforce";
  const readOnly = watcher ? WATCHING : !enforced ? NOT_ENFORCED : null;
  const readOnlyWho = watcher ? NOT_YOU : !enforced ? NOBODY_YET : null;
  const frozen = watcher || restoring;

  const stage = useCallback(
    (change: StagedTeamChange) => {
      if (!base) return;
      setStaging(stageChange(staging, change, base, catalog));
    },
    [base, staging, setStaging, catalog],
  );
  const rings = useMemo(
    () =>
      new Map(
        data.members
          .filter((m) => m.spec && m.live)
          .map((m) => [m.spec!.id, AVATAR_STATE[m.live!.state]] as const),
      ),
    [data.members],
  );
  const leadName = useMemo(() => {
    const lead = spec?.members.find((m) => m.role === "project_lead");
    return lead ? (names.get(lead.id) ?? "The project lead") : "The project lead";
  }, [spec, names]);

  const back = () => navigate(projectPageHref(fid, slug));
  const briefText = (id: string) => {
    const staged = staging.changes.find((c) => c.kind === "brief" && c.member === id);
    return staged?.kind === "brief" ? staged.text : (data.briefs[id] ?? "");
  };
  const undo = (version: number, parent: number) =>
    pin.mutateAsync(parent).then(
      () =>
        toast.show({
          message: `Undone. The team is back as in v${parent}; v${version} stays in history.`,
        }),
      (error: Error) => toast.show({ message: `Could not undo: ${error.message}` }),
    );
  const apply = async () => {
    const baseVersion = preview.data?.def_version ?? spec?.def_version ?? undefined;
    try {
      const res = await edit.mutateAsync({
        changes,
        when: staging.when,
        ...(baseVersion != null ? { base_version: baseVersion } : {}),
      });
      setStaging(clearedStaging());
      const version = res.def_version;
      toast.show({
        message: `Applied as definition v${version ?? "?"}. ${leadName} gets one TEAM UPDATE.`,
        undo:
          version != null && baseVersion != null
            ? () => void undo(version, baseVersion)
            : undefined,
      });
      back();
    } catch (error) {
      toast.show({ message: `Not applied: ${(error as Error).message}` });
      void preview.refetch();
    }
  };
  const proposeToOwners = async (rationale: string) => {
    try {
      const res = await propose.mutateAsync({ kind: "tuning", changes, rationale });
      setStaging(clearedStaging());
      toast.show({
        message: `Proposed as ${res.proposal.ref}. It waits for an owner (${res.decision}).`,
      });
      back();
    } catch (error) {
      toast.show({ message: `Not proposed: ${(error as Error).message}` });
    }
  };

  if (!spec || !base || !team) {
    return (
      <div className="flex min-w-0 flex-col gap-4" data-testid="team-editor">
        <Breadcrumb factoryId={fid} project={project} />
        <EmptyState
          testId="team-editor-unavailable"
          icon={<UsersIcon aria-hidden className="size-6 text-muted-foreground/50" />}
          title="This server has no team specs yet"
          body="Teams become editable when the server keeps a team spec per project. Until then the project lead runs the team its bundle brings."
          action={
            <Button asChild variant="outline" size="sm">
              <Link to={projectPageHref(fid, slug)} componentId="factory.team.back">
                Back to the project
              </Link>
            </Button>
          }
        />
      </div>
    );
  }

  const templates = catalog?.templates ?? [];
  const tiles = team.tiles;
  const tileName = (tile: EditTile) =>
    tile.id
      ? (names.get(tile.id) ?? tile.id)
      : `New ${(TEAM_ROLE_LABEL[tile.role as TeamRole] ?? tile.role).toLowerCase()}`;
  const budgetStaged = team.budget !== base.budget;
  const buildersStaged = team.builders !== base.builders;
  const templateStaged = team.template !== base.template;
  return (
    <div className="flex min-w-0 flex-col gap-4 pb-20 md:pb-0" data-testid="team-editor">
      <Breadcrumb factoryId={fid} project={project} />
      <header className="flex min-w-0 flex-col gap-0.5">
        <h1 className="text-2xl font-semibold break-words">{project.title}</h1>
        <p className="text-sm text-muted-foreground">
          <span className="font-mono">{slug}</span> · its team, as of definition v
          {spec.def_version ?? "?"}
        </p>
      </header>
      <div className="grid min-w-0 grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1fr)_23rem]">
        <div className="flex min-w-0 flex-col gap-3">
          <div
            role="region"
            aria-label="Team editor"
            data-testid="team-edit-bar"
            className="flex flex-wrap items-center gap-x-4 gap-y-2.5 rounded-xl border border-dashed border-status-violet bg-status-violet/5 px-4 py-3 text-sm"
          >
            <span className="flex items-center gap-2">
              <PencilIcon aria-hidden className="size-3.5" />
              <b className="font-semibold">Editing.</b>
              <span>Nothing applies until Apply.</span>
            </span>
            <span className="grow max-sm:hidden" />
            <label className="flex items-center gap-2">
              Template
              <select
                data-testid="team-template"
                value={team.template ?? ""}
                disabled={frozen}
                onChange={(e) =>
                  e.target.value && stage({ kind: "template", value: e.target.value })
                }
                className={cn(
                  "h-8 cursor-pointer rounded-md border border-input bg-background px-2 text-sm disabled:cursor-not-allowed disabled:opacity-60",
                  templateStaged && "border-dashed border-status-violet",
                  FOCUS_RING,
                )}
              >
                {team.template == null && (
                  <option value="" disabled>
                    Today&apos;s bundle
                  </option>
                )}
                {templates.map((t) => (
                  <option
                    key={t.id}
                    value={t.id}
                    disabled={t.locked != null}
                    title={t.locked ?? t.summary}
                  >
                    {t.label || t.id}
                    {t.locked ? " (locked)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <span className="flex items-center gap-2">
              Builders at once
              <button
                type="button"
                aria-label="Fewer builders at once"
                disabled={frozen || team.builders <= BUILDERS_MIN}
                onClick={() => stage({ kind: "builders", value: team.builders - 1 })}
                className={cn(
                  "grid size-8 cursor-pointer place-items-center rounded-md border border-border hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50",
                  FOCUS_RING,
                )}
              >
                <MinusIcon aria-hidden className="size-3.5" />
              </button>
              <b
                className={cn(
                  "w-5 text-center font-mono tabular-nums",
                  buildersStaged && "text-status-violet-fg",
                )}
                data-testid="team-builders"
                aria-live="polite"
              >
                {team.builders}
              </b>
              <button
                type="button"
                aria-label="More builders at once"
                disabled={frozen || team.builders >= BUILDERS_MAX}
                onClick={() => stage({ kind: "builders", value: team.builders + 1 })}
                className={cn(
                  "grid size-8 cursor-pointer place-items-center rounded-md border border-border hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50",
                  FOCUS_RING,
                )}
              >
                <PlusIcon aria-hidden className="size-3.5" />
              </button>
            </span>
            <label className="flex items-center gap-2">
              Budget $
              <input
                type="number"
                inputMode="decimal"
                data-testid="team-budget"
                aria-label="Project budget in dollars"
                min={BUDGET_MIN}
                max={BUDGET_MAX}
                step={10}
                disabled={frozen}
                defaultValue={team.budget}
                key={`budget-${team.budget}-${staging.changes.length === 0}`}
                onBlur={(e) => {
                  const value = Number(e.target.value);
                  if (!Number.isFinite(value) || e.target.value === "") return;
                  stage({
                    kind: "budget",
                    value: Math.min(BUDGET_MAX, Math.max(BUDGET_MIN, Math.round(value))),
                  });
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                }}
                className={cn(
                  "h-8 w-20 rounded-md border border-input bg-background px-2 font-mono text-sm disabled:opacity-60",
                  budgetStaged && "border-dashed border-status-violet",
                  FOCUS_RING,
                )}
              />
            </label>
            <Button asChild variant="outline" size="sm">
              <Link to={projectPageHref(fid, slug)} componentId="factory.team.done">
                Done
              </Link>
            </Button>
            {restoring && (
              <p className="basis-full text-xs">
                A restore is staged: it replaces the whole team. Drop it to edit members one by one.
              </p>
            )}
            {readOnly && (
              <p
                className="basis-full text-xs text-muted-foreground"
                data-testid="team-editor-note"
              >
                {readOnly}
              </p>
            )}
          </div>
          <section
            aria-labelledby="team-members-heading"
            className="min-w-0 rounded-xl border border-border bg-card p-4"
          >
            <h2 id="team-members-heading" className="sr-only">
              Team members
            </h2>
            <TeamChart
              members={tiles}
              editing
              tileKey={(t) => t.key}
              renderTile={(tile) => (
                <EditTileCard
                  tile={tile}
                  name={tileName(tile)}
                  catalog={catalog}
                  bench={bench}
                  lock={removeLock(tile, team, facts, catalog)}
                  frozen={frozen}
                  state={tile.id ? rings.get(tile.id) : undefined}
                  onModel={(vendor, model) =>
                    stage({ kind: "model", member: tile.key, vendor, model })
                  }
                  onRemove={() => stage({ kind: "remove", member: tile.key })}
                  keepable={
                    tile.changeIndex != null && staging.changes[tile.changeIndex]?.kind === "remove"
                  }
                  onKeep={() =>
                    tile.changeIndex != null && setStaging(dropChange(staging, tile.changeIndex))
                  }
                  onBrief={() => setBrief(tile)}
                />
              )}
              renderGhost={(group) => (
                <RoleCatalogMenu
                  key={`ghost-${group.key}`}
                  catalog={catalog}
                  bench={bench}
                  group={group}
                  onPick={(role, vendor) =>
                    stage({ kind: "add", key: nextAddKey(staging), role, vendor, model: null })
                  }
                >
                  <GhostTile
                    label={group.add}
                    disabled={frozen || !catalog}
                    data-testid={`team-ghost-${group.key}`}
                  />
                </RoleCatalogMenu>
              )}
            />
          </section>
        </div>
        <div
          ref={panelRef}
          id="team-staged"
          className="min-w-0 lg:sticky lg:top-4 lg:max-h-[calc(100dvh-6rem)] lg:overflow-y-auto lg:overscroll-contain"
        >
          <StagedTeamPanel
            slug={slug}
            base={base}
            staging={staging}
            team={team}
            catalog={catalog}
            bench={bench}
            names={names}
            preview={staging.changes.length ? preview.data : undefined}
            // A background refetch of the same body is not stale; a new body is.
            previewStale={!preview.settled || preview.isPlaceholderData}
            previewError={preview.error}
            onRetryPreview={() => void preview.refetch()}
            readOnly={readOnlyWho}
            busy={edit.isPending || propose.isPending}
            onDrop={(i) => setStaging(dropChange(staging, i))}
            onDiscard={() => setStaging(clearedStaging())}
            onWhen={(when) => setStaging({ ...staging, when })}
            onScope={(scope) => setStaging({ ...staging, scope })}
            onApply={() => void apply()}
            onPropose={(why) => void proposeToOwners(why)}
          />
        </div>
      </div>
      {phone && (
        <StagedBar
          count={staging.changes.length}
          onReview={() => panelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
        />
      )}
      <BriefDialog
        open={brief != null}
        name={brief ? tileName(brief) : ""}
        role={brief ? (TEAM_ROLE_LABEL[brief.role as TeamRole] ?? brief.role) : ""}
        initial={brief?.id ? briefText(brief.id) : ""}
        onOpenChange={(o) => !o && setBrief(null)}
        onStage={(text) => brief?.id && stage({ kind: "brief", member: brief.id, text })}
      />
    </div>
  );
}
