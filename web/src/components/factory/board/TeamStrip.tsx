/**
 * A project's agents as compact chips: role, harness glyph and a live status
 * dot (the Agents panel's palette). Hover shows what the agent is doing, when
 * it last acted and what it cost; a launch-mode drift adds an amber chip.
 */

import { BotIcon, TriangleAlertIcon } from "lucide-react";
import { ClaudeIcon } from "@/components/icons/ClaudeIcon";
import { CodexIcon } from "@/components/icons/CodexIcon";
import { RunningDot } from "@/components/RunningDot";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import type { FactoryRole, LaunchDriftView, MemberRef, MemberView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { activityDotClassName } from "@/shell/subagentStatus";
import { formatUsd } from "../factoryFormat";
import { ageText } from "./boardModel";

export const ROLE_SHORT: Record<FactoryRole, string> = {
  lead: "lead",
  project_lead: "PL",
  ic: "ic",
  env_keeper: "env",
  reviewer: "rev",
  demo: "demo",
};

const ROLE_NAME: Record<FactoryRole, string> = {
  lead: "Lead",
  project_lead: "Project lead",
  ic: "Implementer",
  env_keeper: "Env keeper",
  reviewer: "Reviewer",
  demo: "Demo recorder",
};

export function HarnessGlyph({
  harness,
  className,
}: {
  harness: string | null;
  className?: string;
}) {
  const Icon = harness?.includes("claude")
    ? ClaudeIcon
    : harness?.includes("codex")
      ? CodexIcon
      : BotIcon;
  return <Icon aria-hidden className={cn("size-3 shrink-0 text-muted-foreground", className)} />;
}

function dead(member: MemberRef): boolean {
  return member.health === "dead" || member.status === "failed";
}

/** The status dot: a spinner while running, else the Agents panel's dot colors. */
export function MemberStatusDot({ member }: { member: MemberRef }) {
  if (member.running) return <RunningDot className="size-2.5" />;
  const tone = dead(member)
    ? activityDotClassName("failed")
    : member.status === "waiting"
      ? "bg-warning"
      : activityDotClassName("idle");
  return <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", tone)} />;
}

function statusWord(member: MemberRef): string {
  if (member.running) return "working";
  if (dead(member)) return "down";
  if (member.status === "waiting") return "needs response";
  return "idle";
}

/** `permission mode auto → bypassPermissions`. */
export function driftText(drift: LaunchDriftView): string {
  return drift.fields
    .map(
      (field) =>
        `${field.replaceAll("_", " ")} ${drift.stored[field] ?? "—"} → ${drift.spec[field] ?? "—"}`,
    )
    .join(", ");
}

function MemberChip({
  member,
  drift,
  nowS,
}: {
  member: MemberRef;
  drift: LaunchDriftView | null;
  nowS: number;
}) {
  const role = member.role ? ROLE_SHORT[member.role] : "agent";
  const cost = formatUsd(member.cost_usd);
  return (
    <span className="inline-flex items-center gap-1">
      <HoverCard openDelay={200} closeDelay={100}>
        <HoverCardTrigger asChild>
          <Link
            to={member.url}
            componentId="factory.board.member"
            data-testid={`team-member-${member.session_id}`}
            data-status={statusWord(member)}
            aria-label={`${member.title}: ${statusWord(member)}`}
            className={cn(
              "inline-flex h-6 items-center gap-1 rounded-md border border-border px-1.5 text-sm text-foreground transition-colors hover:bg-muted",
              dead(member) && "border-destructive/40",
            )}
          >
            <HarnessGlyph harness={member.harness} />
            <span>{role}</span>
            <MemberStatusDot member={member} />
          </Link>
        </HoverCardTrigger>
        <HoverCardContent
          side="top"
          align="start"
          className="flex w-72 flex-col gap-1 bg-card-solid"
        >
          <p className="flex items-center gap-1.5 font-medium">
            <HarnessGlyph harness={member.harness} className="size-3.5" />
            <span className="truncate">{member.title}</span>
          </p>
          <p className="text-sm text-muted-foreground">
            {member.role ? ROLE_NAME[member.role] : "Agent"} · {statusWord(member)}
            {member.last_activity_at != null &&
              ` · active ${ageText(nowS - member.last_activity_at)}`}
            {cost && ` · ${cost}`}
          </p>
          {member.summary && <p className="text-sm">{member.summary}</p>}
          {drift && (
            <p className="text-sm text-warning">
              Launch mode differs from spec: {driftText(drift)}
            </p>
          )}
        </HoverCardContent>
      </HoverCard>
      {member.has_launch_drift && (
        <span
          data-testid={`launch-drift-chip-${member.session_id}`}
          title={
            drift
              ? `Launch mode differs from spec: ${driftText(drift)}`
              : "Launch mode differs from spec"
          }
          className="inline-flex h-5 items-center gap-1 rounded-md bg-warning/15 px-1.5 text-sm text-warning"
        >
          <TriangleAlertIcon aria-hidden className="size-3" />
          old launch mode
        </span>
      )}
    </span>
  );
}

/** The PL then its team, each a chip linking to the agent's chat. */
export function TeamStrip({
  pl,
  team,
  members,
  nowS,
  className,
}: {
  pl: MemberRef | null;
  team: MemberRef[];
  /** Snapshot members, for launch-drift details. */
  members: MemberView[];
  nowS: number;
  className?: string;
}) {
  const all = pl ? [pl, ...team] : team;
  if (all.length === 0) return null;
  const driftOf = new Map(members.map((m) => [m.session_id, m.launch_drift]));
  return (
    <div
      data-testid="team-strip"
      className={cn("flex min-w-0 flex-wrap items-center gap-1", className)}
    >
      <span className="mr-0.5 text-sm text-muted-foreground">Team</span>
      {all.map((member) => (
        <MemberChip
          key={member.session_id}
          member={member}
          drift={driftOf.get(member.session_id) ?? null}
          nowS={nowS}
        />
      ))}
    </div>
  );
}
