/**
 * One member on a team chart. In view: avatar (role glyph in a state ring,
 * vendor badge), name, role, vendor · model, what it is doing now, its state
 * and spend; it opens the agent panel. In the editor: a vendor · model select
 * with bench rates, the bench line, what the role cannot run, Instructions…,
 * and Remove (locked up front when a gate needs the member, with the gate
 * named). Staged members are dashed violet with a STAGED, NEW or REMOVE tag.
 */

import { FileTextIcon, LockIcon, PlusIcon, XIcon } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import type { CatalogResponse, FactoryRole } from "@/lib/factoryApi";
import type { BenchResponse } from "@/lib/factoryTeamsApi";
import { cn } from "@/lib/utils";
import { AVATAR_STATE, StateWord } from "../agents/MemberRing";
import { money } from "../agents/TrackRecord";
import { MemberAvatar } from "../ui/MemberAvatar";
import { type AgentState, FOCUS_RING, MEANING, type Vendor } from "../ui/statusTokens";
import {
  type ChartMember,
  type EditTile,
  TEAM_ROLE_LABEL,
  type TeamRole,
  benchFor,
  benchLine,
  defaultModel,
  modelOptions,
  parseOption,
  selectedOption,
  vendorLocks,
} from "./teamModel";

function badgeVendor(vendor: string | null | undefined): Vendor | null {
  return vendor === "claude" || vendor === "codex" ? vendor : null;
}

function roleLabel(role: string): string {
  return TEAM_ROLE_LABEL[role as TeamRole] ?? role;
}

const TILE =
  "relative flex min-w-0 flex-col gap-1.5 rounded-xl border border-border bg-card p-3 text-left";

/** A small capitalised tag on a tile's top edge (STAGED, NEW, REMOVE). */
function Tag({ children, tone = "staged" }: { children: ReactNode; tone?: "staged" | "remove" }) {
  return (
    <span
      className={cn(
        "absolute -top-2.5 right-3 rounded-md px-1.5 py-px text-[10px] leading-4 font-bold tracking-wider uppercase",
        tone === "staged"
          ? "bg-status-violet text-[#160f2b]"
          : "border border-status-red/55 bg-card-solid text-status-red-fg",
      )}
    >
      {children}
    </span>
  );
}

// ── View ───────────────────────────────────────────────────────

export interface ViewTileProps {
  member: ChartMember;
  /** The member's name on the chart (its handle, else role and number). */
  name: string;
  /** `claude · sonnet`. */
  runsOn: string | null;
  /** Opens the agent panel of the session doing its work, when one exists. */
  onOpen?: () => void;
}

export function ViewTile({ member, name, runsOn, onOpen }: ViewTileProps) {
  const live = member.live;
  const state = live ? AVATAR_STATE[live.state] : "idle";
  const vendor = member.spec?.vendor ?? live?.row.vendor ?? null;
  const now = live
    ? live.state === "stuck" && live.row.stuck_reason
      ? `Stuck: ${live.row.stuck_reason}`
      : (live.row.summary ??
        (live.row.now?.tool
          ? `${live.row.now.tool}${live.row.now.tool_arg ? ` ${live.row.now.tool_arg}` : ""}`
          : "No recent activity"))
    : "Waits for its first task.";
  const body = (
    <>
      <span className="flex min-w-0 items-center gap-2.5">
        <MemberAvatar
          name={name}
          role={(member.role as FactoryRole) ?? null}
          vendor={badgeVendor(vendor)}
          model={runsOn?.split(" · ")[1] ?? null}
          state={state}
          size={34}
        />
        <span className="min-w-0">
          <span className="block truncate text-sm font-semibold">{name}</span>
          <span className="block truncate text-xs text-muted-foreground">
            {roleLabel(member.role)}
          </span>
        </span>
      </span>
      <span className="block truncate font-mono text-xs text-foreground/85">
        {runsOn ?? "model not reported yet"}
      </span>
      <span className="line-clamp-2 min-h-[2lh] text-xs text-muted-foreground">{now}</span>
      <span className="mt-auto flex items-center justify-between gap-2">
        {live ? (
          <StateWord member={live} />
        ) : (
          <span className="text-xs text-muted-foreground">Not started</span>
        )}
        <span className="font-mono text-xs text-muted-foreground tabular-nums">
          {member.costUsd != null ? money(member.costUsd) : ""}
        </span>
      </span>
    </>
  );
  const testId = `team-tile-${member.spec?.id ?? live?.row.handle ?? live?.row.session_id}`;
  if (onOpen) {
    return (
      <button
        type="button"
        data-testid={testId}
        onClick={onOpen}
        className={cn(TILE, "cursor-pointer hover:border-border-strong", FOCUS_RING)}
      >
        {body}
      </button>
    );
  }
  return (
    <div data-testid={testId} className={TILE}>
      {body}
    </div>
  );
}

// ── Edit ───────────────────────────────────────────────────────

export interface EditTileProps {
  tile: EditTile;
  name: string;
  catalog: CatalogResponse | null | undefined;
  bench: BenchResponse | null | undefined;
  /** Why it cannot be removed (a gate needs it), else null. */
  lock: string | null;
  /** Nothing on the tile changes (a restore is staged, or the editor is read-only). */
  frozen: boolean;
  /** The ring of the session doing its work now (idle without one). */
  state?: AgentState;
  onModel: (vendor: string, model: string | null) => void;
  onRemove: () => void;
  /** Keep undoes its own removal (a template switch or a restore removes it as a whole). */
  keepable?: boolean;
  onKeep: () => void;
  onBrief: () => void;
}

export function EditTileCard({
  tile,
  name,
  catalog,
  bench,
  lock,
  frozen,
  state = "idle",
  onModel,
  onRemove,
  keepable = true,
  onKeep,
  onBrief,
}: EditTileProps) {
  const removing = tile.tag === "remove";
  const fresh = tile.tag === "new";
  const staged = tile.tag === "staged" || fresh;
  const options = modelOptions(catalog, bench, tile.role);
  const value = selectedOption(catalog, tile.role, tile.vendor, tile.model);
  const known = options.some((o) => o.value === value);
  const record = benchLine(
    benchFor(
      bench,
      tile.role,
      tile.vendor,
      tile.model ?? defaultModel(catalog, tile.role, tile.vendor),
    ),
  );
  const locks = vendorLocks(catalog, tile.role);
  const selectId = `team-model-${tile.key}`;
  const lead = tile.role === "project_lead";
  return (
    <div
      data-testid={`team-edit-tile-${tile.key}`}
      data-tag={tile.tag ?? undefined}
      className={cn(
        TILE,
        staged && cn("border-dashed", MEANING.staged.border, "bg-status-violet/5"),
        removing && "border-dashed border-status-red/55",
      )}
    >
      {tile.tag === "new" && <Tag>New</Tag>}
      {tile.tag === "staged" && <Tag>Staged</Tag>}
      {removing && <Tag tone="remove">Remove</Tag>}
      <div className="flex min-w-0 items-center gap-2.5">
        <MemberAvatar
          name={name}
          role={(tile.role as FactoryRole) ?? null}
          vendor={badgeVendor(tile.vendor)}
          model={tile.model}
          state={fresh ? "staged" : state}
          size={34}
        />
        <div className="min-w-0 grow">
          <div className={cn("truncate text-sm font-semibold", removing && "line-through")}>
            {name}
          </div>
          <div className="truncate text-xs text-muted-foreground">{roleLabel(tile.role)}</div>
        </div>
        {lead ? null : removing ? (
          <button
            type="button"
            onClick={onKeep}
            disabled={frozen || !keepable}
            title={keepable ? "Keep on the team" : "Drop the template switch to keep it"}
            className={cn(
              "h-7 shrink-0 cursor-pointer rounded-md px-2 text-xs font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50",
              FOCUS_RING,
            )}
          >
            Keep
          </button>
        ) : (
          <button
            type="button"
            onClick={onRemove}
            disabled={frozen || lock != null || tile.locked != null}
            aria-label={
              fresh
                ? `Drop the new ${roleLabel(tile.role).toLowerCase()}`
                : `Remove ${name} from the team${lock ? ` (locked: ${lock})` : ""}`
            }
            title={lock ?? (fresh ? "Drop this new member" : "Remove from the team")}
            className={cn(
              "grid size-7 shrink-0 cursor-pointer place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40",
              FOCUS_RING,
            )}
          >
            <XIcon aria-hidden className="size-3.5" />
          </button>
        )}
      </div>
      {tile.before && (
        <div className="truncate font-mono text-xs text-muted-foreground line-through">
          {tile.before.vendor} · {tile.before.model ?? "default"}
        </div>
      )}
      <label htmlFor={selectId} className="sr-only">
        Vendor and model for {name}
      </label>
      <select
        id={selectId}
        data-testid={`team-model-${tile.key}`}
        value={value}
        disabled={frozen || removing || tile.locked != null}
        onChange={(event) => {
          const next = parseOption(event.target.value);
          onModel(next.vendor, next.model);
        }}
        className={cn(
          "h-8 w-full min-w-0 cursor-pointer rounded-md border border-input bg-background px-2 text-sm disabled:cursor-not-allowed disabled:opacity-60",
          tile.tag === "staged" && "border-dashed border-status-violet",
          FOCUS_RING,
        )}
      >
        {!known && (
          <option value={value}>
            {tile.vendor} · {tile.model ?? "default"}
          </option>
        )}
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={o.disabled}>
            {o.label}
          </option>
        ))}
      </select>
      {record && <p className="text-xs text-muted-foreground">{record}</p>}
      {locks.map((text) => (
        <p key={text} className="flex items-start gap-1.5 text-xs text-muted-foreground">
          <LockIcon aria-hidden className="mt-0.5 size-3 shrink-0" />
          <span>{text}</span>
        </p>
      ))}
      {tile.locked === "template" && (
        <p className="text-xs text-muted-foreground">
          Joins with the template. Change its model after Apply.
        </p>
      )}
      {tile.locked === "restore" && (
        <p className="text-xs text-muted-foreground">Comes back with the restore.</p>
      )}
      {!fresh && !removing && (
        <div>
          <button
            type="button"
            onClick={onBrief}
            disabled={frozen}
            className={cn(
              "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md border border-border px-2 text-xs font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50",
              tile.briefStaged && "border-dashed border-status-violet",
              FOCUS_RING,
            )}
          >
            <FileTextIcon aria-hidden className="size-3" />
            Instructions…
          </button>
        </div>
      )}
      {lock && !lead && !removing && (
        <p
          className="flex items-start gap-1.5 text-xs text-muted-foreground"
          data-testid={`team-lock-${tile.key}`}
        >
          <LockIcon aria-hidden className="mt-0.5 size-3 shrink-0" />
          <span>{lock}</span>
        </p>
      )}
    </div>
  );
}

/** The dashed "Add a builder" tile at the foot of a group; opens the role catalog. */
export function GhostTile({
  label,
  disabled,
  ...rest
}: { label: string; disabled?: boolean } & ComponentProps<"button">) {
  return (
    <button
      type="button"
      disabled={disabled}
      {...rest}
      className={cn(
        "flex min-h-24 w-full cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-border-strong p-3 text-sm text-muted-foreground hover:border-status-violet hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50",
        FOCUS_RING,
        rest.className,
      )}
    >
      <PlusIcon aria-hidden className="size-4" />
      <span>{label}</span>
    </button>
  );
}
