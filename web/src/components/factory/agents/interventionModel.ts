// What an intervention sends: one `POST …/control` per agent (message,
// interrupt) or per task (redispatch, reassign), every one human-origin and
// noticed to the PL. Agents without a task are skipped for task actions, and
// several workers of one task send it once. Pure; tests pin the requests.

import type { FactoryControlRequest, FactoryRole } from "@/lib/factoryApi";

export type InterventionKind = "message" | "interrupt" | "redispatch" | "reassign";

export const VENDORS = [
  { value: "ic", label: "Claude (ic)" },
  { value: "ic_codex", label: "Codex (ic_codex)" },
] as const;
export type Vendor = (typeof VENDORS)[number]["value"];

/** An agent an intervention may act on. */
export interface InterventionTarget {
  sessionId: string;
  title: string;
  role: FactoryRole | null;
  project: string | null;
  /** Its task's ref (`T-4`), when it works on one. */
  taskRef: string | null;
  harness?: string | null;
}

export interface PlannedIntervention {
  requests: FactoryControlRequest[];
  /** Who each request acts on, in request order (`ic · route-r2`, `T-4 route`). */
  labels: string[];
  skipped: { title: string; reason: string }[];
}

export const INTERVENTION_LABEL: Record<InterventionKind, string> = {
  message: "Message",
  interrupt: "Interrupt",
  redispatch: "Redispatch task",
  reassign: "Reassign",
};

/** The vendor a reassign moves to: the other one from what the task runs now. */
export function otherVendor(harness: string | null | undefined): Vendor {
  return harness?.includes("codex") ? "ic" : "ic_codex";
}

/**
 * The control requests of `kind` on `targets`.
 *
 * @param options.note - The message text (required for `message`) or a note.
 * @param options.vendor - Where `reassign` moves the task.
 */
export function planIntervention(
  kind: InterventionKind,
  targets: readonly InterventionTarget[],
  options: { note?: string; vendor?: Vendor } = {},
): PlannedIntervention {
  const note = options.note?.trim() || undefined;
  const requests: FactoryControlRequest[] = [];
  const labels: string[] = [];
  const skipped: PlannedIntervention["skipped"] = [];
  if (kind === "message" || kind === "interrupt") {
    for (const target of targets) {
      requests.push({
        action: kind,
        target: `member:${target.sessionId}`,
        ...(note ? { note } : {}),
      });
      labels.push(target.title);
    }
    return { requests, labels, skipped };
  }
  const seen = new Set<string>();
  for (const target of targets) {
    if (!target.taskRef) {
      skipped.push({ title: target.title, reason: "works on no task" });
      continue;
    }
    if (seen.has(target.taskRef)) continue;
    seen.add(target.taskRef);
    requests.push({
      action: kind,
      target: `task:${target.taskRef}`,
      ...(note ? { note } : {}),
      ...(kind === "reassign" ? { options: { vendor: options.vendor ?? "ic_codex" } } : {}),
    });
    labels.push(target.taskRef);
  }
  return { requests, labels, skipped };
}

/** Whether the planned intervention can be confirmed (a message needs its text). */
export function canConfirm(
  kind: InterventionKind,
  plan: PlannedIntervention,
  note: string,
): boolean {
  if (plan.requests.length === 0) return false;
  return kind !== "message" || note.trim().length > 0;
}

/** What happens, in the confirmation's words. */
export function consequences(kind: InterventionKind, vendor: Vendor): string[] {
  switch (kind) {
    case "message":
      return [
        "Delivered after the current tool, marked as from you.",
        "The agent’s PL gets a notice with your words.",
      ];
    case "interrupt":
      return [
        "The current turn stops now; the agent waits for its PL or a message.",
        "The PL is told who interrupted and why.",
      ];
    case "redispatch":
      return [
        "The worker is interrupted.",
        "The PL dispatches a fresh worker from NOTES.md; the attempt counts toward the loop budget.",
      ];
    case "reassign":
      return [
        "The worker is interrupted.",
        `The PL switches the task to ${vendor === "ic_codex" ? "Codex (ic_codex)" : "Claude (ic)"}; it counts as an attempt.`,
      ];
  }
}
