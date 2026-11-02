/**
 * An agent's avatar: its role glyph inside a state ring, with a vendor badge.
 * Shape carries the state as well as colour (spec §11): working is a solid
 * green ring (the only one that moves), waiting on a person solid amber,
 * stuck a dashed orange ring, stopped red with a ×, idle a hollow grey ring,
 * done hollow grey with a small check. The label says it all in words.
 */

import type { ComponentType, SVGProps } from "react";
import {
  BoxIcon,
  CheckIcon,
  CircleIcon,
  CodeXmlIcon,
  CompassIcon,
  EyeIcon,
  HexagonIcon,
  PlayIcon,
  RadarIcon,
  ShieldIcon,
  XIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  type RoleGlyph,
  type TeamMember,
  type TeamState,
  roleGlyph,
  stateWord,
} from "./teamsModel";

const GLYPH: Record<RoleGlyph, ComponentType<SVGProps<SVGSVGElement>>> = {
  compass: CompassIcon,
  shield: ShieldIcon,
  radar: RadarIcon,
  hexagon: HexagonIcon,
  code: CodeXmlIcon,
  eye: EyeIcon,
  play: PlayIcon,
  box: BoxIcon,
  dot: CircleIcon,
};

/** Stuck's orange (dashed rings and hatched strip pieces); not a status token yet. */
export const STUCK_ORANGE = "#ef7d3c";

export const STATE_COLOR: Record<TeamState, string> = {
  working: "var(--status-green)",
  waiting: "var(--status-yellow)",
  stuck: STUCK_ORANGE,
  stopped: "var(--status-red)",
  idle: "var(--status-gray)",
  done: "var(--status-gray)",
};

/** `Dot, Demo, claude sonnet, Stuck`. */
export function ringLabel(member: TeamMember): string {
  return [member.name, member.role, member.vendorModel?.replace(" · ", " "), stateWord(member)]
    .filter(Boolean)
    .join(", ");
}

export function MemberRing({
  member,
  size = 28,
  className,
}: {
  member: TeamMember;
  size?: number;
  className?: string;
}) {
  const state = member.state;
  const Glyph = GLYPH[roleGlyph(member)];
  const color = STATE_COLOR[state];
  const hollow = state === "idle" || state === "done";
  const vendor = member.vendorModel?.split(" · ")[0] ?? null;
  const badge = Math.max(11, Math.round(size * 0.4));
  return (
    <span
      role="img"
      aria-label={ringLabel(member)}
      data-state={state}
      className={cn("relative inline-flex shrink-0 items-center justify-center", className)}
      style={{ width: size, height: size }}
    >
      <svg
        viewBox="0 0 40 40"
        aria-hidden
        className={cn("absolute inset-0", state === "working" && "motion-safe:animate-pulse")}
      >
        <circle
          cx="20"
          cy="20"
          r="18"
          fill="none"
          stroke={color}
          strokeWidth={hollow ? 1.6 : 3}
          strokeDasharray={state === "stuck" ? "5 4" : undefined}
        />
      </svg>
      <Glyph
        aria-hidden
        className="text-foreground"
        style={{ width: size * 0.45, height: size * 0.45 }}
      />
      {(state === "stopped" || state === "done") && (
        <span
          aria-hidden
          className="absolute -top-0.5 -right-0.5 inline-flex items-center justify-center rounded-full bg-background"
          style={{ width: badge, height: badge }}
        >
          {state === "stopped" ? (
            <XIcon style={{ color, width: badge - 2, height: badge - 2 }} strokeWidth={3} />
          ) : (
            <CheckIcon
              className="text-muted-foreground"
              style={{ width: badge - 3, height: badge - 3 }}
              strokeWidth={3}
            />
          )}
        </span>
      )}
      {vendor && (
        <span
          aria-hidden
          className="absolute -right-1 -bottom-1 inline-flex items-center justify-center rounded-full border border-border bg-background font-mono leading-none font-semibold text-foreground"
          style={{ width: badge, height: badge, fontSize: Math.max(8, badge - 5) }}
        >
          {vendor.charAt(0).toUpperCase()}
        </span>
      )}
    </span>
  );
}

/** The state word with a small shape that matches the ring (dot, dashed dot, hollow). */
export function StateWord({
  member,
  className,
}: {
  member: Pick<TeamMember, "state" | "onYou">;
  className?: string;
}) {
  const state = member.state;
  const hollow = state === "idle" || state === "done";
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs text-foreground", className)}>
      <span
        aria-hidden
        className="inline-block size-2 shrink-0 rounded-full"
        style={{
          background: hollow || state === "stuck" ? "transparent" : STATE_COLOR[state],
          border: `1.5px ${state === "stuck" ? "dashed" : "solid"} ${STATE_COLOR[state]}`,
        }}
      />
      {stateWord(member)}
    </span>
  );
}
