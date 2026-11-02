// What a project's stage reads as in one line: "Starting PL · 2m" while its PL
// comes up ("Restarting PL" when a PL that already worked is woken),
// "Queued · 3 projects ahead" while nothing starts it, "Waiting" while held on
// its dependencies, else the stage. Pure; StageDots, the project header and
// the board card render it.

import type { ProjectView } from "@/lib/factoryApi";
import {
  type FactoryTone,
  STAGE_LABEL,
  STAGE_SHORT_LABEL,
  plural,
  stageTone,
} from "../factoryFormat";

export interface StageHeadline {
  /** The short label, e.g. `Starting PL`, `Queued`, `Waiting`, `Verify`. */
  label: string;
  /**
   * The label where there is room (project header, board card): the start
   * label while queued with a reason, else the full stage name (`Building`).
   * An overlay such as `waiting` has its own tag there.
   */
  long: string;
  /** Short muted words after the label in a dense row: the start phase or the queue spot. */
  detail: string | null;
  /** The whole reason where there is room: why it is queued, or the stage note. */
  explain: string | null;
  /** Seconds the label has held, or `null` when unknown. */
  ageS: number | null;
  /** The hover text. */
  title: string;
  tone: FactoryTone;
}

export function stageHeadline(project: ProjectView, nowS: number): StageHeadline {
  const stageAge = nowS - project.stage_since;
  const tone = stageTone(project.stage, project.overlay);
  const long = STAGE_LABEL[project.stage];
  if (project.overlay === "waiting") {
    const deps = project.depends_on ?? [];
    const why = `Waiting on ${deps.length ? deps.join(", ") : "its dependencies"}`;
    return {
      label: "Waiting",
      long,
      detail: null,
      explain: why,
      ageS: stageAge,
      title: why,
      tone,
    };
  }
  const start = project.stage === "queued" && !project.overlay ? project.start : null;
  if (start?.state === "starting") {
    const phase = start.label === "Starting" ? null : start.label;
    const label = start.restart ? "Restarting PL" : "Starting PL";
    const verb = start.restart ? "Restarting" : "Starting";
    return {
      label,
      long: label,
      detail: phase,
      explain: phase,
      ageS: start.since != null ? nowS - start.since : stageAge,
      title: `${verb} the project lead${phase ? `: ${phase}` : ""}`,
      tone: "active",
    };
  }
  if (start?.state === "queued") {
    const ahead = start.ahead ?? 0;
    const pod = start.phase === "pod_cap";
    const spot = ahead > 0 ? `${plural(ahead, "project")} ahead` : "next for a free pod";
    const explain = pod ? `${start.label} · ${spot}` : start.label;
    return {
      label: "Queued",
      long: "Queued",
      detail: pod ? spot : start.label,
      explain,
      ageS: start.since != null ? nowS - start.since : stageAge,
      title: `Queued · ${explain}`,
      tone,
    };
  }
  const stage = STAGE_SHORT_LABEL[project.stage];
  const note = project.stage_note ?? null;
  return {
    label: stage,
    long,
    detail: null,
    explain: note,
    ageS: stageAge,
    title: [stage, project.overlay, note].filter(Boolean).join(" · "),
    tone,
  };
}
