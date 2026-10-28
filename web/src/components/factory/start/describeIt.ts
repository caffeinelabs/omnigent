// New factory "Describe it": a chat-first start. A name, the factory's
// default repo#base (from preflight) with its live-env switch, a brief that
// becomes #factory message #1, and the factory's project autonomy and engine
// mode. Atlas plans the projects from the brief. Pure; tests pin validation.

import type { EngineMode, FactoryCadence, StartFactoryForm } from "@/lib/factoryApi";
import type { ProjectAutonomy } from "@/lib/factoryRoomApi";
import {
  type FactoryDefaultsDraft,
  NAME_MAX,
  emptyDefaults,
  liveEnvByDefault,
  toDefaultsInput,
} from "./projectForm";

/** The server's `BRIEF_MAX_CHARS`. */
export const BRIEF_MAX = 8000;
/** A brief shorter than this can't be planned from. */
export const BRIEF_MIN = 20;

const REPO_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const BRANCH_RE = /^(?!\/)(?!.*\.\.)(?!.*\/\/)[A-Za-z0-9._/-]{1,255}$/;

export interface DescribeDraft {
  name: string;
  cadence: FactoryCadence;
  defaults: FactoryDefaultsDraft;
  brief: string;
  autonomy: ProjectAutonomy;
  engineMode: EngineMode;
}

export interface DescribeErrors {
  name?: string;
  defaults?: string;
  brief?: string;
}

/** A fresh draft on `defaults`: requested autonomy, and `observe` for a first run. */
export function emptyDescribeDraft(
  defaults: FactoryDefaultsDraft = emptyDefaults(),
): DescribeDraft {
  return {
    name: "Atlas factory",
    cadence: "demo",
    defaults,
    brief: "",
    autonomy: "requested",
    engineMode: "observe",
  };
}

/** Change the default repo and base; the live env follows the base until set by hand. */
export function withDescribeDefaults(
  draft: DescribeDraft,
  repo: string,
  branch: string,
): DescribeDraft {
  const old = draft.defaults;
  return {
    ...draft,
    defaults: {
      ...old,
      repo,
      branch,
      liveEnv: old.liveEnvTouched ? old.liveEnv : liveEnvByDefault(branch),
    },
  };
}

/** Inline errors (empty when the draft can start). */
export function validateDescribe(draft: DescribeDraft): DescribeErrors {
  const errors: DescribeErrors = {};
  const name = draft.name.trim();
  if (!name) errors.name = "Name the factory";
  else if (name.length > NAME_MAX) errors.name = `At most ${NAME_MAX} characters`;
  else if (name.includes("\n")) errors.name = "One line";
  const repo = draft.defaults.repo.trim();
  const branch = draft.defaults.branch.trim();
  if (!repo) errors.defaults = "Pick the repo the factory works on";
  else if (!REPO_RE.test(repo)) errors.defaults = "Use owner/name";
  else if (branch && !BRANCH_RE.test(branch)) errors.defaults = "Not a valid branch name";
  const brief = draft.brief.trim();
  if (!brief) errors.brief = "Describe what the factory should build";
  else if (brief.length < BRIEF_MIN)
    errors.brief = "Say a bit more: Atlas plans projects from this";
  else if (brief.length > BRIEF_MAX) errors.brief = `At most ${BRIEF_MAX} characters`;
  return errors;
}

export function describeHasErrors(errors: DescribeErrors): boolean {
  return Object.keys(errors).length > 0;
}

/** What `startFactory` sends for a Describe-it start (no projects). */
export function describeToStartForm(draft: DescribeDraft): StartFactoryForm {
  return {
    name: draft.name.trim(),
    cadence: draft.cadence,
    defaults: toDefaultsInput(draft.defaults),
    projects: [],
    brief: draft.brief.trim(),
    autonomy: draft.autonomy,
    engineMode: draft.engineMode,
  };
}

export const AUTONOMY_HINT: Record<ProjectAutonomy, string> = {
  confirm: "Every project change waits for an operator's click.",
  requested:
    "When someone asks in the Room, Atlas's new projects apply at once with Undo; anything it starts on its own waits for a click.",
  grace:
    "Like requested, and what Atlas proposes on its own applies after 15 minutes without an objection.",
};

export const ENGINE_HINT: Record<EngineMode, string> = {
  observe:
    "Watch only: loops, binder and detectors record; nothing is enforced. Best for a first run.",
  assist: "Also nudges project leads with NEXT and READY hints and escalates stuck loops.",
  enforce: "Also refuses work past its budgets and pauses projects that overspend.",
};
