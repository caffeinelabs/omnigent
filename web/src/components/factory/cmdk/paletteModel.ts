// What ⌘K can jump to, from the snapshot: pages, projects, PRs, tasks (the
// PRs' task refs), open decisions and agents. Pure; the palette renders and
// filters these.

import type { FactorySnapshot } from "@/lib/factoryApi";
import { ROLE_NAME } from "../board/TeamStrip";
import {
  activityPageHref,
  factoryPageHref,
  prPageHref,
  projectPageHref,
  projectsPageHref,
  settingsPageHref,
} from "../factoryRoutes";
import { STAGE_LABEL } from "../factoryFormat";

export type JumpKind = "page" | "project" | "pr" | "task" | "decision" | "agent";

export interface JumpItem {
  id: string;
  kind: JumpKind;
  /** e.g. `!9630`, `D-2`, `T-4`; null for pages and agents. */
  ref: string | null;
  label: string;
  detail: string | null;
  /** In-app path (agents open their chat). */
  href: string;
  /** Extra terms the filter matches. */
  keywords: string[];
}

export const JUMP_GROUP_LABEL: Record<JumpKind, string> = {
  page: "Pages",
  project: "Projects",
  pr: "PRs",
  task: "Tasks",
  decision: "Decisions",
  agent: "Agents",
};

/** Every jump target the snapshot names, grouped in palette order. */
export function jumpItems(snapshot: FactorySnapshot): JumpItem[] {
  const fid = snapshot.factory.id;
  const items: JumpItem[] = [
    {
      id: "page:overview",
      kind: "page",
      ref: null,
      label: "Overview",
      detail: null,
      href: factoryPageHref(fid),
      keywords: ["home", "needs you"],
    },
    {
      id: "page:projects",
      kind: "page",
      ref: null,
      label: "Projects",
      detail: null,
      href: projectsPageHref(fid),
      keywords: ["table", "cards", "board"],
    },
    {
      id: "page:activity",
      kind: "page",
      ref: null,
      label: "Activity",
      detail: null,
      href: activityPageHref(fid),
      keywords: ["events", "log"],
    },
    {
      id: "page:people",
      kind: "page",
      ref: null,
      label: "People",
      detail: "Settings",
      href: settingsPageHref(fid),
      keywords: ["share", "invite", "roles", "settings"],
    },
  ];
  for (const project of snapshot.projects) {
    items.push({
      id: `project:${project.slug}`,
      kind: "project",
      ref: `#${project.slug}`,
      label: project.title,
      detail: `${project.slug} · ${STAGE_LABEL[project.stage]}`,
      href: projectPageHref(fid, project.slug),
      keywords: [project.slug],
    });
  }
  for (const pr of [...snapshot.prs].sort((a, b) => b.number - a.number)) {
    items.push({
      id: `pr:${pr.id}`,
      kind: "pr",
      ref: `!${pr.number}`,
      label: pr.title,
      detail: [pr.project_slug, pr.state === "open" ? (pr.ready ? "ready" : "open") : pr.state]
        .filter(Boolean)
        .join(" · "),
      href: prPageHref(fid, pr.id),
      keywords: [String(pr.number), pr.head_ref],
    });
    const taskRef = pr.task_ref ?? null;
    if (taskRef) {
      items.push({
        id: `task:${taskRef}`,
        kind: "task",
        ref: taskRef,
        label: pr.task ?? pr.title,
        detail: `!${pr.number}`,
        href: prPageHref(fid, pr.id),
        keywords: [pr.task ?? ""],
      });
    }
  }
  for (const decision of snapshot.decisions.filter((d) => d.status === "open")) {
    items.push({
      id: `decision:${decision.id}`,
      kind: "decision",
      ref: decision.ref,
      label: decision.question,
      detail: decision.project_slug ?? decision.asked_by_title,
      href: decision.project_slug
        ? `${projectPageHref(fid, decision.project_slug, { params: { decision: decision.ref } })}`
        : `${factoryPageHref(fid)}?decision=${encodeURIComponent(decision.ref)}`,
      keywords: [decision.ref],
    });
  }
  for (const member of snapshot.members) {
    items.push({
      id: `agent:${member.session_id}`,
      kind: "agent",
      ref: null,
      label: member.title,
      detail:
        [member.role ? ROLE_NAME[member.role] : null, member.project].filter(Boolean).join(" · ") ||
        null,
      href: member.url,
      keywords: [member.role ?? "", member.project ?? "", member.harness ?? ""],
    });
  }
  return items;
}

/** Items matching `query` (every word in the label, ref, detail or keywords). */
export function filterJumpItems(items: readonly JumpItem[], query: string): JumpItem[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...items];
  return items.filter((item) => {
    const hay = [item.label, item.ref ?? "", item.detail ?? "", ...item.keywords]
      .join(" ")
      .toLowerCase();
    return words.every((word) => hay.includes(word));
  });
}
