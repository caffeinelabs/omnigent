// The Team verb: the project's team editor in a panel. Every member with a
// vendor and a model picker, nothing changed until a person picks (or words
// in the chat named a swap: "swap luz to codex"), then what changes, from
// when, the team's $/h before and after, and who approves; Apply with Undo
// for 10 minutes. On a live factory it is the team editor's own path (the
// project's team spec and catalog, the definition's preview, a definition
// version, Undo by re-pinning the version before, offered only while the
// version it made is in force); a sample source previews and applies through
// its own verbs.

import { useMemo, useState } from "react";
import { useOptionalFactoryShell } from "../../shell/FactoryShellContext";
import { useDefinitionEdit } from "../../insights/learnQueries";
import { teamEditorHref } from "../../team/teamStaging";
import {
  EMPTY_STAGING,
  type ModelOption,
  TEAM_ROLE_LABEL,
  type TeamRole,
  type TeamStaging,
  definitionChanges,
  modelOptions,
  stageChange,
  stagedTeam,
  teamBase,
  teamRate,
} from "../../team/teamModel";
import { useTeamPreview } from "../../team/teamQueries";
import { useProjectTeam } from "../../team/useProjectTeam";
import { useFactoryToast } from "../../ui/Toast";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { useNodeActions } from "../NodeContext";
import { Avatar } from "../nodeParts";
import { nodeQueryKeys } from "../nodeQueries";
import { useNodeSource } from "../nodeSource";
import {
  type NodeUndo,
  refusal,
  rememberUndo,
  useApplyVerb,
  useRefreshNodes,
  useVerbPreview,
  useVersionUndo,
} from "../nodeSteer";
import type {
  NodePage,
  PreviewLine,
  TeamSheet,
  VerbArgs,
  VerbAvail,
  VerbRequest,
} from "../nodeTypes";
import { PreviewLines, PreviewPanel } from "./PreviewPanel";

/** One member as the panel lists it. */
export interface SeatRow {
  key: string;
  name: string;
  role: string;
  vendor: string;
  model: string | null;
  /** What it could run: vendors, and each vendor's models (`null`: the bundle's default). */
  options: {
    vendor: string;
    disabled?: boolean;
    models: { model: string | null; label: string }[];
  }[];
  state?: string;
  cost?: number;
  /** It runs something else than today (staged here). */
  before?: string;
}

const SELECT =
  "h-7 min-w-0 rounded-md border border-border-strong bg-background px-1.5 text-[12.5px] outline-none focus-visible:ring-2 focus-visible:ring-status-blue disabled:opacity-60";

function money(x: number): string {
  return x >= 100 ? `$${Math.round(x)}` : `$${x.toFixed(2)}`;
}

/** The members with their pickers (shared by both paths). */
export function TeamRows({
  rows,
  frozen,
  onPick,
}: {
  rows: SeatRow[];
  frozen?: boolean;
  onPick: (key: string, vendor: string, model: string | null) => void;
}) {
  return (
    <ul
      data-testid="v3-team-rows"
      className="@container flex flex-col overflow-hidden rounded-[10px] ring-1 ring-border"
    >
      {rows.map((r) => {
        const vendor = r.options.find((o) => o.vendor === r.vendor);
        const models = vendor?.models ?? [{ model: r.model, label: r.model ?? "default" }];
        return (
          <li
            key={r.key}
            data-testid="v3-team-row"
            data-member={r.name}
            data-changed={r.before ? "" : undefined}
            className={cn(
              "grid grid-cols-[20px_minmax(0,8rem)_minmax(0,7rem)_minmax(0,1fr)_minmax(0,9rem)_4rem] items-center gap-2.5 border-t border-border px-3 py-2 text-[13px] first:border-t-0",
              "@max-[38rem]:grid-cols-[20px_minmax(0,1fr)_auto] @max-[38rem]:gap-y-1",
              r.before && "bg-status-blue/8",
            )}
          >
            <Avatar actor={{ name: r.name }} />
            <span className="min-w-0 truncate font-semibold">{r.name}</span>
            <span className="min-w-0 truncate text-muted-foreground @max-[38rem]:hidden">
              {r.role}
            </span>
            <span className="flex min-w-0 gap-1.5 @max-[38rem]:col-span-3 @max-[38rem]:col-start-1 @max-[38rem]:row-start-2">
              <select
                aria-label={`${r.name} vendor`}
                value={r.vendor}
                disabled={frozen}
                onChange={(e) => {
                  const next = r.options.find((o) => o.vendor === e.target.value);
                  onPick(r.key, e.target.value, next?.models[0]?.model ?? null);
                }}
                className={cn(SELECT, "w-[42%]")}
              >
                {r.options.map((o) => (
                  <option key={o.vendor} value={o.vendor} disabled={o.disabled}>
                    {o.vendor}
                  </option>
                ))}
              </select>
              <select
                aria-label={`${r.name} model`}
                value={r.model ?? ""}
                disabled={frozen}
                onChange={(e) => onPick(r.key, r.vendor, e.target.value || null)}
                className={cn(SELECT, "flex-1")}
              >
                {models.map((m) => (
                  <option key={m.model ?? ""} value={m.model ?? ""}>
                    {m.label}
                  </option>
                ))}
              </select>
            </span>
            <span className="min-w-0 truncate text-xs text-foreground-tertiary @max-[38rem]:hidden">
              {r.before ? `was ${r.before}` : (r.state ?? "")}
            </span>
            <span className="text-right font-mono text-xs text-muted-foreground">
              {r.cost ? money(r.cost) : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

interface PanelProps {
  page: NodePage;
  verb: VerbAvail;
  args?: VerbArgs;
  onClose: () => void;
}

/** "swap luz to codex": the member a chat named, and the vendor (or model family) to run. */
function namedSwap(
  args: VerbArgs | undefined,
  rows: { key: string; name: string; options: SeatRow["options"] }[],
) {
  if (!args?.member || !args.to) return null;
  const who = args.member.toLowerCase();
  const row = rows.find((r) => r.name.toLowerCase() === who || r.key.toLowerCase() === who);
  if (!row) return null;
  const to = args.to.toLowerCase();
  const vendor =
    row.options.find((o) => o.vendor.toLowerCase() === to && !o.disabled) ??
    row.options.find((o) => o.models.some((m) => (m.model ?? "").toLowerCase().includes(to)));
  if (!vendor) return null;
  const model =
    vendor.models.find((m) => (m.model ?? "").toLowerCase().includes(to))?.model ??
    vendor.models.at(-1)?.model ??
    null;
  return { key: row.key, vendor: vendor.vendor, model };
}

// ── A source with its own team (the sample) ────────────────────

function SourceTeamPanel({ page, verb, args, onClose }: PanelProps) {
  const source = useNodeSource();
  const fid = useNodeActions().factoryId;
  const team = useQuery({
    queryKey: [...nodeQueryKeys.node(fid, source?.kind ?? "pending", page.node.ref), "team"],
    queryFn: ({ signal }) => source!.team!(fid, page.node.ref, signal),
    enabled: Boolean(source?.team),
  });
  const sheet: TeamSheet | null = team.data ?? null;
  const rows: SeatRow[] = useMemo(
    () =>
      (sheet?.members ?? []).map((m) => ({
        key: m.id,
        name: m.name,
        role: m.role,
        vendor: m.vendor,
        model: m.model,
        state: m.state,
        ...(m.cost ? { cost: m.cost } : {}),
        options: Object.entries(sheet?.vendors ?? {}).map(([vendor, models]) => ({
          vendor,
          models: models.map((model) => ({ model, label: model })),
        })),
      })),
    [sheet],
  );
  const [picked, setPicked] = useState<{
    key: string;
    vendor: string;
    model: string | null;
  } | null>(null);
  const named = useMemo(() => namedSwap(args, rows), [args, rows]);
  const swap = picked ?? named;
  const was = swap ? rows.find((r) => r.key === swap.key) : undefined;
  const changed = Boolean(swap && was && (was.vendor !== swap.vendor || was.model !== swap.model));
  const shown = rows.map((r) =>
    changed && swap && r.key === swap.key
      ? {
          ...r,
          vendor: swap.vendor,
          model: swap.model,
          before: `${r.vendor} · ${r.model ?? "default"}`,
        }
      : r,
  );
  const request: VerbRequest["args"] =
    changed && swap ? { member: swap.key, vendor: swap.vendor, model: swap.model } : {};
  // Nothing picked: nothing to preview (Apply says why).
  const preview = useVerbPreview(page, verb.verb, request, { enabled: changed });
  const apply = useApplyVerb(page);
  const working = rows.filter((r) => /work|plan/.test(r.state ?? "")).length;
  return (
    <PreviewPanel
      label="Team editor"
      title={`Team of ${page.node.title}`}
      preview={preview.data}
      loading={changed && (!preview.settled || preview.isFetching)}
      error={changed ? preview.error : null}
      applyLabel="Apply team change"
      applying={apply.isPending}
      blocked={changed ? null : "Pick a vendor and model for a member to preview the change."}
      onCancel={onClose}
      onApply={() =>
        preview.data &&
        apply.mutate(
          { verb: verb.verb, label: verb.label, args: request, preview: preview.data },
          { onSuccess: onClose },
        )
      }
      note={
        changed
          ? "Undo for 10 min after applying."
          : "Idle members are listed here only; pages never draw them."
      }
    >
      <p className="-mt-1 text-[12.5px] text-foreground-tertiary">
        {rows.length} members · {working ? `${working} working` : "all idle"}
        {sheet?.rate ? ` · $${sheet.rate}/h while working` : ""}
      </p>
      {team.isPending ? (
        <div aria-hidden className="h-24 animate-pulse rounded-md bg-muted" />
      ) : (
        <TeamRows rows={shown} onPick={(key, vendor, model) => setPicked({ key, vendor, model })} />
      )}
    </PreviewPanel>
  );
}

// ── A live factory: the team editor's own path ─────────────────

function catalogOptions(options: ModelOption[]): SeatRow["options"] {
  const out: SeatRow["options"] = [];
  for (const o of options) {
    let vendor = out.find((v) => v.vendor === o.vendor);
    if (!vendor) {
      vendor = { vendor: o.vendor, disabled: o.disabled, models: [] };
      out.push(vendor);
    }
    if (!o.disabled) {
      vendor.disabled = false;
      vendor.models.push({ model: o.model, label: o.label.replace(`${o.vendor} · `, "") });
    }
  }
  return out;
}

function LiveTeamPanel({ page, verb, args, onClose }: PanelProps) {
  const shell = useOptionalFactoryShell();
  const project = shell?.snapshot.projects.find((p) => p.slug === page.node.ref) ?? null;
  if (!shell || !project?.team_spec) {
    return (
      <PreviewPanel
        bare
        label="Team editor"
        title={`Team of ${page.node.title}`}
        applyLabel="Apply team change"
        blocked="This server keeps no team spec for this project yet."
        onApply={() => undefined}
        onCancel={onClose}
        note="Teams become editable when the server keeps a team spec per project."
      />
    );
  }
  return <LiveTeamEditor page={page} verb={verb} args={args} onClose={onClose} />;
}

function LiveTeamEditor({ page, verb, args, onClose }: PanelProps) {
  const shell = useOptionalFactoryShell()!;
  const snapshot = shell.snapshot;
  const fid = shell.factoryId;
  const project = snapshot.projects.find((p) => p.slug === page.node.ref)!;
  const slug = project.slug;
  const data = useProjectTeam(snapshot, project, { editing: true });
  const catalog = data.catalog.data;
  const base = useMemo(
    () => (data.spec ? teamBase(slug, data.spec, data.briefs) : null),
    [data.spec, slug, data.briefs],
  );
  const [own, setOwn] = useState<TeamStaging | null>(null);
  const toast = useFactoryToast();
  const edit = useDefinitionEdit(fid);
  const refresh = useRefreshNodes(fid);
  const rows: SeatRow[] = useMemo(
    () =>
      (base?.members ?? []).map((m) => ({
        key: m.id,
        name: data.names.get(m.id) ?? m.handle ?? m.id,
        role: TEAM_ROLE_LABEL[m.role as TeamRole] ?? m.role,
        vendor: m.vendor,
        model: m.model,
        options: catalogOptions(modelOptions(catalog, data.bench.data, m.role)),
      })),
    [base, catalog, data.bench.data, data.names],
  );
  // Words in the chat ("swap luz to codex") stage that one change; else nothing is.
  const named = useMemo(() => namedSwap(args, rows), [args, rows]);
  const staging: TeamStaging =
    own ??
    (named && base
      ? stageChange(
          EMPTY_STAGING,
          { kind: "model", member: named.key, vendor: named.vendor, model: named.model },
          base,
          catalog,
        )
      : EMPTY_STAGING);
  const team = useMemo(
    () => (base ? stagedTeam(base, staging, catalog) : null),
    [base, staging, catalog],
  );
  const changes = useMemo(
    () => (team ? definitionChanges(slug, staging, team, catalog) : []),
    [slug, staging, team, catalog],
  );
  const preview = useTeamPreview(fid, changes, staging.when, data.spec?.def_version ?? null);
  const shown: SeatRow[] = rows.map((r) => {
    const tile = team?.tiles.find((t) => t.key === r.key);
    if (!tile?.before) return r;
    return {
      ...r,
      vendor: tile.vendor,
      model: tile.model,
      before: `${tile.before.vendor} · ${tile.before.model ?? "default"}`,
    };
  });
  const builders = team?.builders ?? base?.builders ?? 1;
  const rateBefore = base ? teamRate(catalog, base.members, base.builders) : null;
  const rateAfter = team
    ? teamRate(
        catalog,
        team.tiles.filter((t) => t.tag !== "remove"),
        builders,
      )
    : null;
  const res = changes.length ? preview.data : undefined;
  const lines: PreviewLine[] = [
    ...(res?.lines ?? []).map((l) => ({ label: "Change", html: escapeHtml(l) })),
    {
      label: "When",
      html:
        staging.when === "now" ? "Now: running work restarts." : "From each member’s next task.",
    },
    ...(rateBefore != null && rateAfter != null && changes.length
      ? [
          {
            label: "Cost",
            html: `$${rateBefore}/h → about <b>$${rateAfter}/h</b> while working (estimate)`,
          },
        ]
      : []),
    ...(res ? [{ label: "Who approves", html: escapeHtml(res.who) }] : []),
  ];
  const blocked = !changes.length
    ? "Pick a vendor and model for a member to preview the change."
    : res && !res.you_can_apply
      ? `${res.who} You can look, not apply.`
      : res?.blocked.length
        ? res.blocked.map((b) => b.message).join(" ")
        : null;
  const ref = page.node.ref;
  const versionUndo = useVersionUndo(ref, fid);
  const undoOf = (version: number, parent: number): NodeUndo =>
    versionUndo(
      verb.label,
      version,
      parent,
      `Undone. The team is back as in v${parent}; v${version} stays in history.`,
    );
  const applyNow = async () => {
    const baseVersion = res?.def_version ?? data.spec?.def_version ?? undefined;
    try {
      const out = await edit.mutateAsync({
        changes,
        when: staging.when,
        ...(baseVersion != null ? { base_version: baseVersion } : {}),
      });
      const version = out.def_version;
      void refresh();
      onClose();
      const undo = version != null && baseVersion != null ? undoOf(version, baseVersion) : null;
      if (undo) rememberUndo(fid, ref, undo);
      toast.show({
        message: `Applied as definition v${version ?? "?"}: the team of ${page.node.title} changes from the next task.`,
        ...(undo ? { undo: () => void undo.run() } : {}),
      });
    } catch (error) {
      // Refused (409: the definition moved on): the edit's own settle reads the
      // team spec and the definition again; the node pages read again too.
      void refresh();
      toast.show({ message: refusal(error) });
    }
  };
  return (
    <PreviewPanel
      bare
      label="Team editor"
      title={`Team of ${page.node.title}`}
      loading={changes.length > 0 && (!preview.settled || preview.isFetching)}
      error={changes.length ? preview.error : null}
      applyLabel="Apply team change"
      blocked={blocked}
      applying={edit.isPending}
      onApply={() => void applyNow()}
      onCancel={onClose}
      note={
        changes.length
          ? "Undo for 10 min after applying."
          : "Nothing is preselected: pick to see the change."
      }
    >
      <p className="-mt-1 flex flex-wrap gap-x-2 text-[12.5px] text-foreground-tertiary">
        <span>
          {rows.length} members{rateBefore != null ? ` · $${rateBefore}/h while working` : ""}
        </span>
        <Link
          to={teamEditorHref(fid, slug)}
          componentId="factory.v3.team.editor"
          className="text-status-blue-fg hover:underline"
        >
          Open the full team editor
        </Link>
      </p>
      <TeamRows
        rows={shown}
        onPick={(key, vendor, model) =>
          base &&
          setOwn(stageChange(staging, { kind: "model", member: key, vendor, model }, base, catalog))
        }
      />
      {changes.length > 0 && <PreviewLines lines={lines} />}
    </PreviewPanel>
  );
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!,
  );
}

/** Team: the sample's own panel, else the live team editor's path. */
export function TeamPanel(props: PanelProps) {
  const source = useNodeSource();
  return source?.team ? <SourceTeamPanel {...props} /> : <LiveTeamPanel {...props} />;
}
