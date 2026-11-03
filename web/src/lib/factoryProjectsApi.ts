// A project's charter versions and its close lever.
//
// Every charter edit is recorded as a projects proposal (`C-n`) that applied
// when it was filed, so the Charter tab lists the history, offers Undo for 10
// minutes (`POST …/proposals/<ref>/undo`) and Revert for any applied version
// (`POST …/projects/<slug>/charter/revert`). Shapes mirror
// `omnigent/server/factory/charter_versions.py`.

import { factoryErrorFromResponse } from "./factoryApi";
import type { ProposalView } from "./factoryApi";
import { authenticatedFetch } from "./identity";

const enc = encodeURIComponent;

function projectPath(factoryId: string, slug: string, tail = ""): string {
  return `/v1/factory/factories/${enc(factoryId)}/projects/${enc(slug)}${tail}`;
}

/** One field a version changed. Values are the charter's own (strings, lists, objects). */
export interface CharterFieldChange {
  field: string;
  before: unknown;
  after: unknown;
}

/** One applied (or undone) change of a project's charter, newest first. */
export interface CharterVersion {
  /** `C-14`. */
  ref: string;
  status: "applied" | "undone";
  /** How it touched the project: created it, changed its charter, split or merged it. */
  op: "create" | "update" | "split" | "merge";
  title: string;
  by: string;
  by_kind: "human" | "agent" | "system";
  /** Epoch seconds it applied. */
  at: number;
  /** While its 10-minute Undo is open. */
  undo_until: number | null;
  /** The version this one reverts. */
  revert_of: string | null;
  rationale: string | null;
  changes: CharterFieldChange[];
  revertible: boolean;
}

export interface CharterVersionsResponse {
  slug: string;
  versions: CharterVersion[];
  started: { at: number };
}

export interface CharterRevertResponse {
  project: Record<string, unknown>;
  proposal: ProposalView | null;
  changed: boolean;
  mail_id: string | null;
}

async function readJson<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw await factoryErrorFromResponse(resp);
  return (await resp.json()) as T;
}

/** `GET …/projects/<slug>/charter/versions`. */
export async function fetchCharterVersions(
  factoryId: string,
  slug: string,
  signal?: AbortSignal,
): Promise<CharterVersionsResponse> {
  return readJson(
    await authenticatedFetch(projectPath(factoryId, slug, "/charter/versions"), { signal }),
  );
}

/** `POST …/projects/<slug>/charter/revert`: the fields one version changed, put back. */
export async function revertCharterVersion(
  factoryId: string,
  slug: string,
  request: { proposal: string; note?: string },
): Promise<CharterRevertResponse> {
  return readJson(
    await authenticatedFetch(projectPath(factoryId, slug, "/charter/revert"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }),
  );
}

/** `POST …/projects/<slug>/cancel`: close a project (its PL stops; its PRs stay open). */
export async function closeFactoryProject(
  factoryId: string,
  slug: string,
  request: { reason?: string } = {},
): Promise<{ project: Record<string, unknown> }> {
  return readJson(
    await authenticatedFetch(projectPath(factoryId, slug, "/cancel"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }),
  );
}
