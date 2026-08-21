// Persisted, app-global preferences for the managed-sandbox repo picker on the
// landing composer (NewChatDialog):
//
//   1. The fetched repo LIST, cached across reloads so reopening the composer
//      shows the last-known repos instantly instead of an empty picker while
//      the `github-repos` query refetches. react-query already caches in
//      memory for the tab's lifetime; this survives a full reload.
//   2. The last repo SELECTION, so the next new session pre-selects the repos
//      the user launched with instead of starting empty.
//
// Mirrors baseBranchPreferences / agentPreferences: tiny localStorage helpers
// that never throw — a server render (no `window`), inaccessible storage, or a
// malformed/stale entry all read back as "unset" so a bad value can't break the
// composer.

import type { GithubRepoList } from "@/lib/githubIntegration";

const REPO_LIST_KEY = "omnigent:sandbox-repo-list";
const REPO_LIST_TS_KEY = "omnigent:sandbox-repo-list-updated-at";
const LAST_SELECTION_KEY = "omnigent:sandbox-repos-last-selection";

/** The persisted shape of one selected sandbox repo — structurally identical
 *  to NewChatDialog's `SelectedSandboxRepo`, kept here so the persistence layer
 *  owns its own contract and callers pass their value in by structural typing. */
export interface PersistedSandboxRepo {
  fullName: string;
  cloneUrl: string;
  defaultBranch: string | null;
  branch: string;
}

/**
 * Read the cached repo list, or `null` when nothing is cached / storage is
 * unavailable / the entry is malformed. Validates the shape defensively (a
 * hand-edited or version-skewed entry must not reach the picker), returning
 * only a well-formed `{ connected, repos }`.
 */
export function loadCachedRepoList(): GithubRepoList | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(REPO_LIST_KEY);
    if (raw === null) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      typeof (parsed as GithubRepoList).connected !== "boolean" ||
      !Array.isArray((parsed as GithubRepoList).repos)
    ) {
      return null;
    }
    return parsed as GithubRepoList;
  } catch {
    return null;
  }
}

/**
 * Epoch-ms timestamp of the cached list, or `null` when absent/unparseable.
 * Fed to react-query's `initialDataUpdatedAt` so the seeded list is treated as
 * potentially stale and refetched in the background once `staleTime` elapses.
 */
export function loadCachedRepoListUpdatedAt(): number | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(REPO_LIST_TS_KEY);
    if (raw === null) return null;
    const ts = Number.parseInt(raw, 10);
    return Number.isFinite(ts) ? ts : null;
  } catch {
    return null;
  }
}

/**
 * Persist the fetched repo list plus a fetch timestamp. Only caches a
 * `connected: true` list — a `connected: false` result carries no repos and
 * would just blank the picker on the next reload. Swallows quota/access errors.
 */
export function saveCachedRepoList(list: GithubRepoList): void {
  if (typeof window === "undefined") return;
  if (!list.connected) return;
  try {
    window.localStorage.setItem(REPO_LIST_KEY, JSON.stringify(list));
    window.localStorage.setItem(REPO_LIST_TS_KEY, String(Date.now()));
  } catch {
    // localStorage quota or access errors shouldn't break the composer.
  }
}

/**
 * Read the last repo selection, or `null` when nothing is stored / storage is
 * unavailable / the entry is malformed. Every element is validated so a stale
 * or partial entry can't seed the picker with a broken repo.
 */
export function readLastSandboxRepos(): PersistedSandboxRepo[] | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(LAST_SELECTION_KEY);
    if (raw === null) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return null;
    const repos: PersistedSandboxRepo[] = [];
    for (const item of parsed) {
      if (
        typeof item !== "object" ||
        item === null ||
        typeof (item as PersistedSandboxRepo).fullName !== "string" ||
        typeof (item as PersistedSandboxRepo).cloneUrl !== "string" ||
        typeof (item as PersistedSandboxRepo).branch !== "string"
      ) {
        return null;
      }
      const r = item as PersistedSandboxRepo;
      repos.push({
        fullName: r.fullName,
        cloneUrl: r.cloneUrl,
        defaultBranch: typeof r.defaultBranch === "string" ? r.defaultBranch : null,
        branch: r.branch,
      });
    }
    return repos;
  } catch {
    return null;
  }
}

/**
 * Persist the repos the user just launched a session with, as the selection to
 * pre-fill next time. An empty array is a real choice ("start in an empty
 * workspace") and is stored as such. Swallows quota/access errors.
 */
export function writeLastSandboxRepos(repos: readonly PersistedSandboxRepo[]): void {
  if (typeof window === "undefined") return;
  try {
    const normalized: PersistedSandboxRepo[] = repos.map((r) => ({
      fullName: r.fullName,
      cloneUrl: r.cloneUrl,
      defaultBranch: r.defaultBranch,
      branch: r.branch,
    }));
    window.localStorage.setItem(LAST_SELECTION_KEY, JSON.stringify(normalized));
  } catch {
    // localStorage quota or access errors shouldn't break session creation.
  }
}
