// The product profile's auto-merge targets as Steer edits them: repo and base
// pairs (`owner/name#branch`) whose ready, green factory PRs the server merges.
// Owners set them; agents never do. Names only: no wildcards.

import type { AutoMergeTarget } from "@/lib/factoryApi";

const REPO_RE = /^[A-Za-z0-9_.-]{1,100}\/[A-Za-z0-9_.-]{1,100}$/;
// The server's branch rule: no leading "-", "." or "/", no "..", "//", "/." or
// ".lock" component, no trailing "/" or ".".
const BRANCH_RE =
  /^(?![-./])(?!.*\.\.)(?!.*\/\/)(?!.*\/\.)(?!.*\.lock(?:\/|$))(?!.*[/.]$)[A-Za-z0-9._/-]{1,255}$/;

/** Branch names that are usually a repo's default when the code host does not say. */
const USUAL_DEFAULTS = new Set(["main", "master"]);

/** `owner/name#branch, …` as targets, or why it is not. */
export function parseAutoMerge(text: string): { value: AutoMergeTarget[] } | { error: string } {
  const out: AutoMergeTarget[] = [];
  const seen = new Set<string>();
  for (const raw of text.split(/[,\n]/)) {
    const entry = raw.trim();
    if (!entry) continue;
    const [repo = "", base = "", ...rest] = entry.split("#").map((p) => p.trim());
    if (rest.length > 0 || !REPO_RE.test(repo) || !BRANCH_RE.test(base)) {
      return { error: `${entry} is not owner/name#branch` };
    }
    const key = `${repo.toLowerCase()}#${base}`;
    if (seen.has(key)) return { error: `${repo}#${base} is listed twice` };
    seen.add(key);
    out.push({ repo, base });
  }
  return { value: out };
}

/** Targets as Steer shows them: `owner/name#branch, …`. */
export function formatAutoMerge(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return value
    .filter((t): t is AutoMergeTarget => !!t && typeof t === "object" && "repo" in t)
    .map((t) => `${t.repo}#${t.base}`)
    .join(", ");
}

/**
 * The plain warnings for targets that are a repo's default branch: the
 * server merges into it with nobody looking. *defaults* maps a lowercase
 * `owner/name` to its default branch where the code host said; elsewhere
 * `main` and `master` count as the default.
 */
export function autoMergeWarnings(
  targets: readonly AutoMergeTarget[],
  defaults: ReadonlyMap<string, string>,
): string[] {
  return targets.flatMap((t) => {
    const known = defaults.get(t.repo.toLowerCase());
    const isDefault = known ? known === t.base : USUAL_DEFAULTS.has(t.base);
    return isDefault
      ? [
          `${t.repo}#${t.base} is ${known ? "the repo's" : "likely the repo's"} default branch: ` +
            "the factory's server will merge into it without a person reviewing first.",
        ]
      : [];
  });
}
