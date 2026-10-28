// What a typed Room token stands for right now: its label, where it opens,
// and a tone from the live state of the object (a ready PR, an open
// decision, a pending proposal). Pure; the chips render it.

import type { FactorySnapshot, PersonView, ProposalView } from "@/lib/factoryApi";
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

/** A token's chip: label, link and tone from the snapshot (and proposals, people). */
export function refView(token: RoomToken, sources: RefSources): RefView {
  const { snapshot } = sources;
  const fid = snapshot.factory.id;
  switch (token.kind) {
    case "project": {
      const project = snapshot.projects.find((p) => p.slug === token.value);
      return {
        label: token.text,
        href: project ? projectPageHref(fid, project.slug) : null,
        tone: project ? stageTone(project.stage, project.overlay) : "muted",
        title: project ? `${project.title} · ${project.stage}` : `${token.text} (no such project)`,
      };
    }
    case "thread":
      return {
        label: token.text,
        href: roomPageHref(fid, token.value),
        tone: "muted",
        title: `The ${token.text} thread`,
      };
    case "pr": {
      const pr = snapshot.prs.find((p) => String(p.number) === token.value);
      const state = pr ? (pr.state === "open" ? (pr.ready ? "ready" : "open") : pr.state) : null;
      return {
        label: token.text,
        href: pr ? prPageHref(fid, pr.id) : null,
        tone:
          state === "ready"
            ? "pass"
            : state === "merged"
              ? "merged"
              : state === "closed"
                ? "muted"
                : pr
                  ? "active"
                  : "muted",
        title: pr ? `${pr.title} · ${state}` : `${token.text} (not tracked here)`,
      };
    }
    case "decision": {
      const decision = snapshot.decisions.find((d) => d.ref === token.text);
      return {
        label: token.text,
        href: decisionsPageHref(fid, { decision: token.text }),
        tone: decision?.status === "open" ? "warning" : "muted",
        title: decision ? `${decision.question} · ${decision.status}` : token.text,
      };
    }
    case "proposal": {
      const proposal = sources.proposals?.find((p) => p.ref === token.text);
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
      return {
        label: token.text,
        href: incidentPageHref(fid, token.value),
        tone: "warning",
        title: `Incident ${token.text}`,
      };
    case "finding":
      return {
        label: token.text,
        href: improvementPageHref(fid, token.text),
        tone: "muted",
        title: `Finding ${token.text}`,
      };
    case "agent": {
      const found = resolveAgent(snapshot, token.text);
      if ("member" in found) {
        return {
          label: `@${found.member.title}`,
          href: found.member.url,
          tone: found.member.running ? "active" : "muted",
          title: `${found.member.title} · ${found.member.running ? "working" : found.member.status}`,
        };
      }
      return { label: token.text, href: null, tone: "muted", title: found.error };
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
