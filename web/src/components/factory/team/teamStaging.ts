// What is staged on each project's team, per factory and project, until Apply
// or Discard: it outlives leaving the editor (the project page says "3 team
// changes staged · Review") and a reload in the same tab (session storage,
// when the browser allows it). Other parts of the cockpit stage a change and
// open the editor through `useOpenTeamEditor` (the ⌘K palette's "swap X to …"
// and "add a reviewer to …", the agent panel's "Change model…").

import { useCallback, useSyncExternalStore } from "react";
import { projectPageHref } from "@/components/factory/factoryRoutes";
import { useOptionalFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { useNavigate } from "@/lib/routing";
import {
  EMPTY_STAGING,
  type StagedTeamChange,
  type TeamBase,
  type TeamStaging,
  stageChange,
} from "./teamModel";

const STORAGE_PREFIX = "omnigent.factory.team-staged";

const memory = new Map<string, TeamStaging>();
const listeners = new Set<() => void>();

function storeKey(factoryId: string, slug: string): string {
  return `${factoryId}/${slug}`;
}

function isStaging(value: unknown): value is TeamStaging {
  const v = value as TeamStaging | null;
  return (
    v != null &&
    Array.isArray(v.changes) &&
    (v.when === "after_current" || v.when === "now") &&
    (v.scope === "project" || v.scope === "template")
  );
}

function load(key: string): TeamStaging {
  const held = memory.get(key);
  if (held) return held;
  let found: TeamStaging = EMPTY_STAGING;
  try {
    const raw = window.sessionStorage.getItem(`${STORAGE_PREFIX}.${key}`);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (isStaging(parsed)) found = parsed;
  } catch {
    // Storage unavailable or unreadable: start empty.
  }
  memory.set(key, found);
  return found;
}

/** What is staged on project `slug` of factory `factoryId`. */
export function readTeamStaging(factoryId: string, slug: string): TeamStaging {
  return load(storeKey(factoryId, slug));
}

/** Replace what is staged on a project (an empty list clears it). */
export function writeTeamStaging(factoryId: string, slug: string, staging: TeamStaging): void {
  const key = storeKey(factoryId, slug);
  memory.set(key, staging);
  try {
    const item = `${STORAGE_PREFIX}.${key}`;
    if (staging.changes.length) window.sessionStorage.setItem(item, JSON.stringify(staging));
    else window.sessionStorage.removeItem(item);
  } catch {
    // Kept in memory for this page.
  }
  for (const listener of listeners) listener();
}

/**
 * Stage `changes` on a project's team. With `base` each change merges like a
 * change made in the editor (back to today's value drops it); without one they
 * are appended and the editor merges them when it opens.
 */
export function stageTeamChanges(
  factoryId: string,
  slug: string,
  changes: readonly StagedTeamChange[],
  base?: TeamBase,
): void {
  let staging = readTeamStaging(factoryId, slug);
  for (const change of changes) {
    staging = base
      ? stageChange(staging, change, base)
      : { ...staging, changes: [...staging.changes, change] };
  }
  writeTeamStaging(factoryId, slug, staging);
}

/** Forget everything staged (tests). */
export function resetTeamStaging(): void {
  memory.clear();
  try {
    for (let i = window.sessionStorage.length - 1; i >= 0; i -= 1) {
      const key = window.sessionStorage.key(i);
      if (key?.startsWith(STORAGE_PREFIX)) window.sessionStorage.removeItem(key);
    }
  } catch {
    // Nothing stored.
  }
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** What is staged on a project, and a setter; re-renders on every change from anywhere. */
export function useTeamStaging(
  factoryId: string,
  slug: string,
): [TeamStaging, (next: TeamStaging) => void] {
  const staging = useSyncExternalStore(
    subscribe,
    () => readTeamStaging(factoryId, slug),
    () => EMPTY_STAGING,
  );
  const set = useCallback(
    (next: TeamStaging) => writeTeamStaging(factoryId, slug, next),
    [factoryId, slug],
  );
  return [staging, set];
}

/** `/factory/<fid>/projects/<slug>/team`: the team editor. */
export function teamEditorHref(factoryId: string, slug: string): string {
  return `${projectPageHref(factoryId, slug)}/team`;
}

/**
 * Open a project's team editor, optionally with changes staged first (for the
 * palette's "swap Rex to claude" or "add a reviewer to …").
 *
 * @example
 *   const openTeam = useOpenTeamEditor();
 *   openTeam("csv-export", [{ kind: "model", member: "M-3", vendor: "claude", model: null }]);
 */
export function useOpenTeamEditor(
  factoryId?: string,
): (slug: string, changes?: readonly StagedTeamChange[]) => void {
  const shell = useOptionalFactoryShell();
  const fid = factoryId ?? shell?.factoryId ?? "";
  const navigate = useNavigate();
  return useCallback(
    (slug: string, changes: readonly StagedTeamChange[] = []) => {
      if (!fid) return;
      if (changes.length) stageTeamChanges(fid, slug, changes);
      navigate(teamEditorHref(fid, slug));
    },
    [fid, navigate],
  );
}
