// Software-factory labels as the web UI reads them. A factory is a labelled
// tree of ordinary sessions: leads and project leads are top-level sessions
// linked to the session that spawned them by `omnigent.factory.parent`, and
// any member may carry a one-line beacon (`summary` + `health`). Mirrors the
// label keys in `omnigent/factory.py`.

export const FACTORY_ID_LABEL_KEY = "omnigent.factory.id";
export const FACTORY_NAME_LABEL_KEY = "omnigent.factory.name";
export const FACTORY_ROLE_LABEL_KEY = "omnigent.factory.role";
export const FACTORY_PARENT_LABEL_KEY = "omnigent.factory.parent";
export const FACTORY_PROJECT_LABEL_KEY = "omnigent.factory.project";
export const FACTORY_SUMMARY_LABEL_KEY = "omnigent.factory.summary";
export const FACTORY_HEALTH_LABEL_KEY = "omnigent.factory.health";

/** The factory guide (fork docs), linked from the cockpit's preflight checks. */
export const FACTORY_DOCS_URL =
  "https://github.com/caffeinelabs/omnigent/blob/staging/docs/FACTORY.md";

const LEGACY_ID = /^(?:[a-z]+_)?([0-9a-f]{32})$/;

/**
 * A factory id as the server keys it: bare lowercase hex, without dashes or a
 * legacy `conv_` prefix, so label ids match `factory_changed` frames.
 *
 * @returns The bare hex id, or the trimmed input when it is not a uuid.
 */
export function normalizeFactoryId(raw: string): string {
  const trimmed = raw.trim();
  const match = LEGACY_ID.exec(trimmed.toLowerCase().replaceAll("-", ""));
  return match?.[1] ?? trimmed;
}

/** Beacon health values that need a human's eye (rendered in the warning colour). */
const WARNING_HEALTH: ReadonlySet<string> = new Set(["degraded", "blocked", "dead"]);

/** Visible characters of a beacon summary before it is cut with an ellipsis. */
export const FACTORY_SUMMARY_DISPLAY_MAX = 60;

const ROLE_DISPLAY: Record<string, string> = {
  lead: "Lead",
  project_lead: "PL",
  ic: "IC",
  env_keeper: "Env",
  reviewer: "Reviewer",
  demo: "Demo",
};

/** The factory labels of one session, `null` per field when unset. */
export interface FactoryInfo {
  role: string | null;
  project: string | null;
  parent: string | null;
  summary: string | null;
  health: string | null;
}

function label(labels: Record<string, string>, key: string): string | null {
  const value = labels[key]?.trim();
  return value ? value : null;
}

/**
 * Read a session's factory labels.
 *
 * @param labels - The session's labels, e.g. `{"omnigent.factory.role": "lead"}`.
 * @returns The factory fields, or `null` when the session carries none of them.
 */
export function factoryInfo(labels: Record<string, string> | undefined): FactoryInfo | null {
  if (!labels) return null;
  const info: FactoryInfo = {
    role: label(labels, FACTORY_ROLE_LABEL_KEY),
    project: label(labels, FACTORY_PROJECT_LABEL_KEY),
    parent: label(labels, FACTORY_PARENT_LABEL_KEY),
    summary: label(labels, FACTORY_SUMMARY_LABEL_KEY),
    health: label(labels, FACTORY_HEALTH_LABEL_KEY),
  };
  return Object.values(info).some((v) => v !== null) ? info : null;
}

/** Whether the session is a top-level factory member (joined by label, not a sub-agent). */
export function isFactoryMember(info: FactoryInfo | null): boolean {
  return info !== null && (info.role !== null || info.parent !== null);
}

/** Whether the session publishes a beacon (summary or health). */
export function hasFactoryBeacon(info: FactoryInfo | null): info is FactoryInfo {
  return info !== null && (info.summary !== null || info.health !== null);
}

/** Whether a beacon health value should render in the warning colour. */
export function isFactoryWarningHealth(health: string | null): boolean {
  return health !== null && WARNING_HEALTH.has(health);
}

/**
 * Text of the role chip, e.g. `"PL · api-version"` or `"Lead"`.
 *
 * @param shownTitle - The row's visible title; the chip is dropped when it
 *   already reads the same (a PL titled `"PL · api-version"`).
 * @returns `null` when the session has neither a role nor a project.
 */
export function factoryRoleChipText(
  info: FactoryInfo | null,
  shownTitle?: string | null,
): string | null {
  if (!info) return null;
  const role = info.role ? (ROLE_DISPLAY[info.role] ?? info.role) : null;
  const parts = [role, info.project].filter((p): p is string => p !== null);
  if (parts.length === 0) return null;
  const text = parts.join(" · ");
  return shownTitle?.includes(text) ? null : text;
}

/**
 * Visible beacon text: the summary (cut to `max` characters), prefixed with
 * the health word when it is not `ok`, e.g. `"blocked · waiting on CI"`.
 */
export function factoryBeaconText(
  info: FactoryInfo,
  max: number = FACTORY_SUMMARY_DISPLAY_MAX,
): string {
  const summary =
    info.summary && info.summary.length > max ? `${info.summary.slice(0, max)}…` : info.summary;
  const health = info.health && info.health !== "ok" ? info.health : null;
  if (health && summary) return `${health} · ${summary}`;
  return summary ?? info.health ?? "";
}

/**
 * Title for a factory member's own row in the Agents panel (e.g. `"Atlas"`),
 * where other sessions show their harness name.
 *
 * @returns The trimmed title when the session carries a factory role, else `null`.
 */
export function factoryMemberTitle(
  labels: Record<string, string> | undefined,
  title: string | null | undefined,
): string | null {
  const shown = title?.trim();
  return labels?.[FACTORY_ROLE_LABEL_KEY]?.trim() && shown ? shown : null;
}

/**
 * Whether `pathname` is a factory page: `/factory`, `/factory/new`,
 * `/factory/<id>` and any page under it (`/factory/<id>/prs/<pr>`), behind an
 * embed prefix too. A session whose id happens to read "factory" is not.
 */
export function isFactoryPath(pathname: string): boolean {
  const segments = pathname.split("/").filter(Boolean);
  const at = segments.indexOf("factory");
  return at >= 0 && segments[at - 1] !== "c";
}

/** The factory a session belongs to (`omnigent.factory.id`), bare hex, or null. */
export function factoryIdOf(labels: Record<string, string> | undefined): string | null {
  const raw = labels?.[FACTORY_ID_LABEL_KEY]?.trim();
  return raw ? normalizeFactoryId(raw) : null;
}

/**
 * Whether a session is a member of a factory someone else shared with the
 * viewer: the sidebar lists those under its Factory entry, not in Shared.
 * Only factories the viewer can open (`readable`) count; a session shared on
 * its own, without its factory, stays in Shared.
 */
export function isSharedFactoryMember(
  labels: Record<string, string> | undefined,
  ownedByViewer: boolean,
  readable: ReadonlySet<string>,
): boolean {
  const id = factoryIdOf(labels);
  return !ownedByViewer && id !== null && readable.has(id);
}
