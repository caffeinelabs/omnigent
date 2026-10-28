// Qualitative signals people give the factory (`factory_feedback`): the
// seeded taxonomies (Tier 0, mirrored from the server), the reason tags a PR
// rating offers, and the one-line receipts the widgets show once sent.

/** Why a PR was closed without merge (server `CLOSURE_REASONS`). */
export const CLOSURE_REASONS = [
  "wrong_scope",
  "wrong_approach",
  "quality",
  "missing_tests",
  "ux",
  "superseded",
  "not_needed",
  "test_run",
  "infra",
] as const;

/** Root causes for incidents and retro labels (server `ROOT_CAUSES`). */
export const ROOT_CAUSE_TAGS = [
  "charter_unclear",
  "task_too_big",
  "env_flaky",
  "ci_flaky",
  "tool_error",
  "prompt_gap",
  "reviewer_miss",
  "waiting_on_human",
  "coordination",
  "platform_bug",
] as const;

/** Reason tags offered with a PR rating: what was good, what wasn't. */
export const PR_RATING_TAGS = {
  good: ["scope_right", "clean_code", "good_tests", "clear_demo"],
  bad: ["wrong_scope", "wrong_approach", "quality", "missing_tests", "ux"],
} as const;

/** Reason tags offered with a project's wrap-up rating. */
export const WRAP_UP_TAGS = {
  good: ["on_plan", "few_questions", "fast", "good_demos"],
  bad: ["too_many_questions", "slow", "rework", "costly"],
} as const;

/** Tags shown for `stars`: the good ones from 4★, the bad ones up to 3★. */
export function tagsFor(
  stars: number | null,
  sets: { good: readonly string[]; bad: readonly string[] },
): readonly string[] {
  if (stars == null) return [];
  return stars >= 4 ? sets.good : sets.bad;
}

/** `"wrong_scope"` → `"wrong scope"`. */
export function tagLabel(tag: string): string {
  return tag.replaceAll("_", " ");
}

/** `"Rated 4★ · clear demo, good tests"`. */
export function ratingReceipt(stars: number, tags: readonly string[]): string {
  return `Rated ${stars}★${tags.length ? ` · ${tags.map(tagLabel).join(", ")}` : ""}`;
}
