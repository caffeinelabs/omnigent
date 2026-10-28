// What the Room composer can complete: agents and people after `@`,
// projects and threads after `#`, PRs after `!`, and decisions, proposals,
// tasks, incidents and findings after their prefix. Built from the snapshot,
// the roster and whatever lists the thread already read.

import type { RoomCandidate } from "@/lib/composerMentions";
import type {
  FactorySnapshot,
  IssueView,
  PersonView,
  ProposalView,
  TaskView,
} from "@/lib/factoryApi";
import { STAGE_SHORT_LABEL } from "../factoryFormat";

export interface CandidateSources {
  snapshot: FactorySnapshot;
  people?: readonly PersonView[] | null;
  proposals?: readonly ProposalView[] | null;
  tasks?: readonly TaskView[] | null;
  issues?: readonly IssueView[] | null;
}

/** Every completion the composer offers, in a stable order. */
export function roomCandidateList(sources: CandidateSources): RoomCandidate[] {
  const { snapshot } = sources;
  const out: RoomCandidate[] = [];
  const rootId = snapshot.factory.root_session_id;
  out.push({ kind: "agent", insert: "@atlas", label: "Atlas", detail: "lead · #factory" });
  if (snapshot.members.some((m) => m.role === "lead" && m.session_id !== rootId)) {
    out.push({
      kind: "agent",
      insert: "@sentinel",
      label: "Sentinel",
      detail: "lead · #improvements",
    });
  }
  for (const project of snapshot.projects) {
    if (project.pl) {
      out.push({
        kind: "agent",
        insert: `@pl:${project.slug}`,
        label: `PL · ${project.slug}`,
        detail: project.pl.running ? "working" : project.pl.status,
        keywords: [project.slug, project.title],
      });
    }
  }
  for (const person of sources.people ?? []) {
    out.push({
      kind: "person",
      insert: `@${person.user_id}`,
      label: person.name ?? person.user_id,
      detail: person.role,
      keywords: [person.user_id],
    });
  }
  out.push({ kind: "thread", insert: "#factory", label: "factory", detail: "Atlas's thread" });
  out.push({
    kind: "thread",
    insert: "#improvements",
    label: "improvements",
    detail: "Sentinel's thread",
  });
  for (const project of snapshot.projects) {
    out.push({
      kind: "project",
      insert: `#${project.slug}`,
      label: project.title,
      detail: STAGE_SHORT_LABEL[project.stage],
      keywords: [project.slug],
    });
  }
  for (const pr of snapshot.prs) {
    out.push({
      kind: "pr",
      insert: `!${pr.number}`,
      label: pr.title,
      detail: pr.state === "open" ? (pr.ready ? "ready" : pr.project_slug) : pr.state,
      keywords: [pr.project_slug ?? "", String(pr.number)],
    });
  }
  const decisions = [...snapshot.decisions].sort(
    (a, b) => Number(b.status === "open") - Number(a.status === "open") || b.number - a.number,
  );
  for (const decision of decisions) {
    out.push({
      kind: "decision",
      insert: decision.ref,
      label: decision.question,
      detail: decision.status,
      keywords: [decision.project_slug ?? ""],
    });
  }
  for (const proposal of sources.proposals ?? []) {
    out.push({
      kind: "proposal",
      insert: proposal.ref,
      label: proposal.title,
      detail: proposal.status,
    });
  }
  for (const task of sources.tasks ?? []) {
    out.push({
      kind: "task",
      insert: task.ref,
      label: task.title,
      detail: `${task.project_slug} · ${task.state}`,
      keywords: [task.key, task.project_slug],
    });
  }
  for (const issue of sources.issues ?? []) {
    if (!issue.ref) continue;
    out.push({
      kind: issue.class === "incident" ? "incident" : "finding",
      insert: issue.ref,
      label: issue.summary,
      detail: issue.status,
    });
  }
  return out;
}
