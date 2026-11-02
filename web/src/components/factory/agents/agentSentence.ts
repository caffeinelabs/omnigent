// The agent panel's first words: one sentence built from facts about what the
// agent is doing, then what happens next and whether a person must act.
// Idle always says why idle is fine. Pure; the panel and its tests read it.

import type { ProjectView } from "@/lib/factoryApi";
import type { TeamMember, TeamState } from "./teamsModel";

export interface AgentSentence {
  /** e.g. "Stuck: same failing Bash call 3 times". */
  headline: string;
  /** What happens next and whether you need to act; empty when the headline says it all. */
  next: string;
  state: TeamState;
}

export interface SentenceFacts {
  member: TeamMember;
  /** The agent's project, when it has one. */
  project?: ProjectView | null;
  /** Server clock, epoch seconds. */
  nowS: number;
}

/** `45s`, `14 min`, `3 h`, `2 d`. */
export function shortSpan(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86_400) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86_400)} d`;
}

function lowerFirst(text: string): string {
  return text ? text.charAt(0).toLowerCase() + text.slice(1) : text;
}

function clause(text: string | null | undefined, max = 140): string | null {
  const flat = (text ?? "").replace(/\s+/g, " ").trim().replace(/[.]$/, "");
  if (!flat) return null;
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** What it does now, in a few words: the tool and its argument, else its beacon. */
function doing(member: TeamMember): string | null {
  const now = member.row.now;
  if (now?.tool) return clause(now.tool_arg ? `${now.tool} ${now.tool_arg}` : now.tool, 90);
  return clause(member.row.summary) ?? clause(member.row.task?.title);
}

function waitsForMerge(project: ProjectView | null | undefined): boolean {
  return project?.stage === "ready" || project?.agent_stage === "ready";
}

function idleReason(facts: SentenceFacts): string {
  const { member, project } = facts;
  if (waitsForMerge(project))
    return "Idle, and that is fine: the project waits for a person to merge.";
  if (member.row.task?.state === "done") return "Idle, and that is fine: its task is finished.";
  if (member.row.is_top_level && member.row.state === "paused") {
    return "Idle, and that is fine: it is paused until someone resumes it.";
  }
  if (member.row.is_top_level)
    return "Idle, and that is fine: it looks again at its next check-in.";
  return "Idle, and that is fine: it starts when its project lead sends it work.";
}

/** The sentence for one agent (spec §7). */
export function agentSentence(facts: SentenceFacts): AgentSentence {
  const { member, nowS } = facts;
  const row = member.row;
  const state = member.state;
  const since = row.now?.last_event_at ?? row.last_activity_at;
  switch (state) {
    case "stopped": {
      const why = clause(row.last_error, 160);
      const headline = why
        ? `Stopped: ${lowerFirst(why)}`
        : row.health === "dead"
          ? "Stopped: it no longer answers its check-ins"
          : "Stopped: its last turn failed";
      const next = row.is_top_level
        ? "It needs a person: relaunch it or close its project."
        : "Its project lead retries or reassigns the task. Nothing needs you.";
      return { headline, next, state };
    }
    case "stuck": {
      const reason = clause(row.stuck_reason) ?? "no progress for a while";
      const issue = row.stuck_issue ?? "An incident";
      return {
        headline: `Stuck: ${reason}`,
        next: `${issue} is open and the factory is handling it. Nothing needs you; it would show in Needs you if it did.`,
        state,
      };
    }
    case "waiting":
      if (member.onYou) {
        return {
          headline: "Waiting on you: it asked a question only a person can answer",
          next: "Answer it here or on Home. Everything else on its team keeps moving.",
          state,
        };
      }
      return {
        headline: "Waiting on a person's answer",
        next: "Someone else answers it. Nothing needs you.",
        state,
      };
    case "working": {
      const what = doing(member);
      const start = row.now?.tool_since;
      const span = start != null ? ` for ${shortSpan(nowS - start)}` : "";
      return { headline: what ? `Working${span}: ${what}` : `Working${span}`, next: "", state };
    }
    case "done": {
      const what = clause(row.summary);
      return {
        headline: what ? `Done: ${what}` : "Done",
        next: "Idle, and that is fine: its work is finished.",
        state,
      };
    }
    default: {
      const what = clause(row.summary);
      const span = since != null ? ` for ${shortSpan(nowS - since)}` : "";
      return {
        headline: what ? `Idle${span}: ${what}` : `Idle${span}`,
        next: idleReason(facts),
        state,
      };
    }
  }
}
