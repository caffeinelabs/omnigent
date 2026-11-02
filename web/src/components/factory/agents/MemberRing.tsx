/**
 * A team member's avatar and state word, drawn with the cockpit's shared
 * primitives (`ui/MemberAvatar`, `ui/statusTokens`): the role glyph inside a
 * state ring with a vendor badge, where shape carries the state as well as
 * colour (spec §11). The label says it all in words.
 */

import type { FactoryRole } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { MemberAvatar } from "../ui/MemberAvatar";
import { type AgentState, STATE_RING, vendorOf as badgeVendor } from "../ui/statusTokens";
import { type TeamMember, type TeamState, stateWord } from "./teamsModel";

/** The shared avatar state for each Teams state (a stopped agent draws as dead). */
export const AVATAR_STATE: Record<TeamState, AgentState> = {
  working: "working",
  waiting: "waiting",
  stuck: "stuck",
  stopped: "dead",
  idle: "idle",
  done: "done",
};

/** Each Teams state's hue, from the shared ring tokens. */
export const STATE_COLOR: Record<TeamState, string> = {
  working: STATE_RING.working.color,
  waiting: STATE_RING.waiting.color,
  stuck: STATE_RING.stuck.color,
  stopped: STATE_RING.dead.color,
  idle: STATE_RING.idle.color,
  done: STATE_RING.done.color,
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
  const [vendor, model] = member.vendorModel?.split(" · ") ?? [];
  return (
    <MemberAvatar
      name={member.name}
      role={(member.row.role as FactoryRole | null) ?? null}
      seat={member.seat}
      vendor={badgeVendor(vendor)}
      model={model ?? null}
      state={AVATAR_STATE[member.state]}
      size={size}
      label={ringLabel(member)}
      className={className}
    />
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
