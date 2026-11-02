/**
 * A member's identity in one glyph: the role drawn inside a ring whose colour
 * and stroke say its state, and a vendor badge letter. The full sentence
 * ("Dot, Demo, claude sonnet, Stuck") is the accessible name, so the ring
 * never carries meaning on colour alone.
 */

import {
  BotIcon,
  BoxIcon,
  CheckIcon,
  CodeIcon,
  CompassIcon,
  EyeIcon,
  HexagonIcon,
  type LucideIcon,
  PlayIcon,
  RadarIcon,
  ShieldIcon,
} from "lucide-react";
import type { FactoryRole } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import {
  type AgentState,
  FOCUS_RING,
  type LeadSeat,
  MEANING,
  type RoleGlyph,
  STATE_MEANING,
  STATE_RING,
  STATE_WORD,
  VENDOR_BADGE,
  type Vendor,
  roleLook,
} from "./statusTokens";

const GLYPH: Record<RoleGlyph, LucideIcon> = {
  compass: CompassIcon,
  shield: ShieldIcon,
  radar: RadarIcon,
  hexagon: HexagonIcon,
  code: CodeIcon,
  eye: EyeIcon,
  play: PlayIcon,
  box: BoxIcon,
  agent: BotIcon,
};

export interface MemberIdentity {
  /** Handle or display name ("Dot"). */
  name?: string | null;
  role: FactoryRole | null;
  /** A lead's seat, which picks its glyph and label. */
  seat?: LeadSeat | null;
  vendor?: Vendor | null;
  model?: string | null;
  state: AgentState;
}

/** "Dot, Demo, claude sonnet, Stuck": name, role, vendor and model, state. */
export function memberAvatarLabel(member: MemberIdentity): string {
  const { label } = roleLook(member.role, member.seat);
  const runsOn = [member.vendor, member.model].filter(Boolean).join(" ");
  return [member.name, label, runsOn, STATE_WORD[member.state]].filter(Boolean).join(", ");
}

function dashFor(state: AgentState, size: number): string | undefined {
  switch (STATE_RING[state].stroke) {
    case "dashed":
      return state === "staged" ? "3 2.5" : `${(size / 7).toFixed(1)} ${(size / 12).toFixed(1)}`;
    case "dotted":
      return "1.5 3";
    default:
      return undefined;
  }
}

export interface MemberAvatarProps extends MemberIdentity {
  /** Diameter in px (30 by default); the vendor badge scales with it. */
  size?: number;
  /** Replaces the generated accessible name. */
  label?: string;
  /** Makes the avatar a button (opens the agent's panel). */
  onClick?: () => void;
  className?: string;
}

export function MemberAvatar({
  size = 30,
  label,
  onClick,
  className,
  ...member
}: MemberAvatarProps) {
  const { state, vendor } = member;
  const name = label ?? memberAvatarLabel(member);
  const ring = STATE_RING[state];
  const Glyph = GLYPH[roleLook(member.role, member.seat).glyph];
  const c = size / 2;
  const large = size >= 40;
  const badge = large ? 18 : size >= 24 ? 14 : 11;
  const body = (
    <span
      aria-hidden
      className="relative inline-grid shrink-0 place-items-center rounded-full"
      style={{ width: size, height: size }}
    >
      <svg className="absolute inset-0" width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle
          cx={c}
          cy={c}
          r={c - 1.6}
          fill="none"
          stroke={ring.color}
          strokeWidth={ring.width}
          strokeDasharray={dashFor(state, size)}
          opacity={state === "done" ? 0.55 : 1}
          className={cn(ring.pulse && "animate-pulse motion-reduce:animate-none")}
        />
      </svg>
      <Glyph
        className={cn("text-foreground/80", state === "dead" && "opacity-35")}
        style={{ width: Math.round(size * 0.5), height: Math.round(size * 0.5) }}
        strokeWidth={1.8}
      />
      {ring.mark === "x" && (
        <svg
          className="absolute inset-0"
          width={size}
          height={size}
          viewBox="0 0 24 24"
          style={{ color: MEANING[STATE_MEANING.dead].hue }}
        >
          <path
            d="M7 7l10 10M17 7 7 17"
            stroke="currentColor"
            strokeWidth={2.2}
            strokeLinecap="round"
          />
        </svg>
      )}
      {ring.mark === "check" && (
        <span className="absolute -top-[3px] -right-[3px] grid size-[13px] place-items-center rounded-full border border-border bg-card-solid text-muted-foreground">
          <CheckIcon className="size-[9px]" strokeWidth={3} />
        </span>
      )}
      {vendor && (
        <span
          className="absolute -right-[3px] -bottom-[3px] grid place-items-center rounded-full border border-border bg-card-solid leading-none font-extrabold text-foreground/80"
          style={{ width: badge, height: badge, fontSize: large ? 10 : size >= 24 ? 8.5 : 7 }}
        >
          {VENDOR_BADGE[vendor]}
        </span>
      )}
    </span>
  );
  if (onClick) {
    return (
      <button
        type="button"
        aria-label={name}
        title={name}
        onClick={onClick}
        data-state={state}
        className={cn("inline-grid shrink-0 cursor-pointer rounded-full", FOCUS_RING, className)}
      >
        {body}
      </button>
    );
  }
  return (
    <span
      role="img"
      aria-label={name}
      title={name}
      data-state={state}
      className={cn("inline-grid shrink-0", className)}
    >
      {body}
    </span>
  );
}

/** A state as a small shape plus its word ("● Working", "◌ Stuck"). */
export function StateLabel({ state, className }: { state: AgentState; className?: string }) {
  const ring = STATE_RING[state];
  const filled = state === "working" || state === "waiting" || state === "dead";
  return (
    <span
      data-state={state}
      className={cn(
        "inline-flex items-center gap-1.5 text-sm whitespace-nowrap text-foreground/85",
        className,
      )}
    >
      <span
        aria-hidden
        className="inline-block size-2 shrink-0 rounded-full"
        style={
          filled
            ? { background: ring.color }
            : {
                border: `1.5px ${ring.stroke === "solid" ? "solid" : ring.stroke} ${ring.color}`,
              }
        }
      />
      {STATE_WORD[state]}
    </span>
  );
}
