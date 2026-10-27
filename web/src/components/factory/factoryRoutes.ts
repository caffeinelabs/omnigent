// Web paths of the factory pages: the cockpit and one project's page.

/** The project page's tabs (`?tab=`); Overview is the default and leaves the param out. */
export const PROJECT_TABS = ["overview", "prs", "demos", "previews", "activity"] as const;
export type ProjectTab = (typeof PROJECT_TABS)[number];

/** The tab a `?tab=` value names; anything else is Overview. */
export function projectTab(value: string | null | undefined): ProjectTab {
  return PROJECT_TABS.find((tab) => tab === value) ?? "overview";
}

/** `/factory/<fid>`, the cockpit. */
export function factoryPageHref(factoryId: string): string {
  return `/factory/${encodeURIComponent(factoryId)}`;
}

/**
 * `/factory/<fid>/projects/<slug>`, on `tab` (Overview when omitted) with any
 * extra query parameters (e.g. `review` to open a PR's review drawer).
 */
export function projectPageHref(
  factoryId: string,
  slug: string,
  options: { tab?: ProjectTab; params?: Record<string, string | number> } = {},
): string {
  const query = new URLSearchParams();
  if (options.tab && options.tab !== "overview") query.set("tab", options.tab);
  for (const [key, value] of Object.entries(options.params ?? {})) query.set(key, String(value));
  const qs = query.toString();
  return `${factoryPageHref(factoryId)}/projects/${encodeURIComponent(slug)}${qs ? `?${qs}` : ""}`;
}
