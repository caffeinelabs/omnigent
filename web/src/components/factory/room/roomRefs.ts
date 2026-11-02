// What a typed Room token stands for right now: its label, where it opens,
// and a tone from the live state of the object (a ready PR, an open
// decision, a pending proposal). Pure; the chips render it.
//
// A token that names nothing the cockpit knows ("shortlist #2", "!12" of a
// PR nobody tracks, "@pl:x" without a PL) is no ref: `refView` returns
// `null` and the text stays plain. A GitHub-style "#9774" that names no
// project but a tracked PR opens that PR. Kinds the cockpit cannot check from what
// it holds (tasks, an incident while the issues are not loaded) keep their
// chip, since their pages say what they are.

import type { FactorySnapshot, IssueView, PersonView, ProposalView } from "@/lib/factoryApi";
import type { RoomToken } from "@/lib/composerMentions";
import {
  decisionsPageHref,
  graphPageHref,
  improvementPageHref,
  incidentPageHref,
  prPageHref,
  projectPageHref,
  roomPageHref,
} from "../factoryRoutes";
import { type FactoryTone, stageTone } from "../factoryFormat";
import { resolveAgent } from "@/lib/roomCommands";

export interface RefView {
  label: string;
  href: string | null;
  tone: FactoryTone;
  /** The hover text: what the object is now. */
  title: string;
  /** The token names the viewer (`@me`). */
  self?: boolean;
}

export interface RefSources {
  snapshot: FactorySnapshot;
  proposals?: readonly ProposalView[] | null;
  people?: readonly PersonView[] | null;
  /** Incidents and findings, when the thread loaded them. */
  issues?: readonly IssueView[] | null;
  viewerId?: string | null;
}

const PROPOSAL_TONE: Partial<Record<ProposalView["status"], FactoryTone>> = {
  pending: "warning",
  awaiting_cosign: "warning",
  applied: "pass",
  trial: "active",
  kept: "pass",
  refused: "fail",
};

/**
 * Whether number `n` exists in a list that holds only the newest rows:
 * numbers count up per factory, so one at or below the highest known
 * number is an older row.
 */
function numberExists(n: number, known: readonly { number: number }[]): boolean {
  return known.some((row) => row.number >= n);
}

/** The chip of tracked PR number `n` as `label` (`!9774`, or `#9774` GitHub-style). */
function prRef(snapshot: FactorySnapshot, n: string, label: string): RefView | null {
  const pr = snapshot.prs.find((p) => String(p.number) === n);
  if (!pr) return null;
  const state = pr.state === "open" ? (pr.ready ? "ready" : "open") : pr.state;
  return {
    label,
    href: prPageHref(snapshot.factory.id, pr.id),
    tone:
      state === "ready"
        ? "pass"
        : state === "merged"
          ? "merged"
          : state === "closed"
            ? "muted"
            : "active",
    title: `${pr.title} · ${state}`,
  };
}

/**
 * A token's chip: label, link and tone from the snapshot (and proposals, people,
 * issues), or `null` when the token names nothing here and stays plain text.
 */
export function refView(token: RoomToken, sources: RefSources): RefView | null {
  const { snapshot } = sources;
  const fid = snapshot.factory.id;
  switch (token.kind) {
    case "project": {
      const project = snapshot.projects.find((p) => p.slug === token.value);
      if (!project) {
        // "#9774" as agents write a GitHub PR: the tracked PR, else plain text.
        return /^\d+$/.test(token.value) ? prRef(snapshot, token.value, token.text) : null;
      }
      return {
        label: token.text,
        href: projectPageHref(fid, project.slug),
        tone: stageTone(project.stage, project.overlay),
        title: `${project.title} · ${project.stage}`,
      };
    }
    case "thread":
      return {
        label: token.text,
        href: roomPageHref(fid, token.value),
        tone: "muted",
        title: `The ${token.text} thread`,
      };
    case "pr":
      return prRef(snapshot, token.value, token.text);
    case "decision": {
      const decision = snapshot.decisions.find((d) => d.ref === token.text);
      if (!decision && !numberExists(Number(token.value), snapshot.decisions)) return null;
      return {
        label: token.text,
        href: decisionsPageHref(fid, { decision: token.text }),
        tone: decision?.status === "open" ? "warning" : "muted",
        title: decision ? `${decision.question} · ${decision.status}` : token.text,
      };
    }
    case "proposal": {
      const proposal = sources.proposals?.find((p) => p.ref === token.text);
      if (
        !proposal &&
        sources.proposals != null &&
        !numberExists(Number(token.value), sources.proposals)
      ) {
        return null;
      }
      return {
        label: token.text,
        href: improvementPageHref(fid, token.text),
        tone: proposal ? (PROPOSAL_TONE[proposal.status] ?? "muted") : "muted",
        title: proposal ? `${proposal.title} · ${proposal.status}` : token.text,
      };
    }
    case "task":
      return {
        label: token.text,
        href: graphPageHref(fid, { task: token.text }),
        tone: "muted",
        title: `Task ${token.text} on the work graph`,
      };
    case "incident":
    case "finding": {
      const same = sources.issues?.filter((i) => i.class === token.kind);
      const issue = same?.find((i) => i.ref === token.text);
      if (!issue && same != null && !numberExists(Number(token.value), same)) return null;
      const now = issue ? `${issue.summary} · ${issue.status}` : null;
      return token.kind === "incident"
        ? {
            label: token.text,
            href: incidentPageHref(fid, token.value),
            tone: "warning",
            title: now ?? `Incident ${token.text}`,
          }
        : {
            label: token.text,
            href: improvementPageHref(fid, token.text),
            tone: "muted",
            title: now ?? `Finding ${token.text}`,
          };
    }
    case "agent": {
      const found = resolveAgent(snapshot, token.text);
      if (!("member" in found)) return null;
      return {
        label: `@${found.member.title}`,
        href: found.member.url,
        tone: found.member.running ? "active" : "muted",
        title: `${found.member.title} · ${found.member.running ? "working" : found.member.status}`,
      };
    }
    case "person": {
      const person = sources.people?.find(
        (p) => p.user_id.toLowerCase() === token.value.toLowerCase(),
      );
      const self =
        sources.viewerId != null && token.value.toLowerCase() === sources.viewerId.toLowerCase();
      return {
        label: `@${person?.name ?? token.value.split("@")[0]}`,
        href: null,
        tone: self ? "warning" : "muted",
        title: person ? `${person.user_id} · ${person.role}` : token.value,
        self,
      };
    }
  }
}
