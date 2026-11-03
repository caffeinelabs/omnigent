// People's levers on discovery, each a reversible act that applies at once and
// confirms in the cockpit toast with Undo (votes and pins never change a
// score): vote, pin, park, kill with a reason, plan, research, attach a
// signal, add or withdraw a hunch, pause or resume a source. The server
// checks the human origin and the viewer's level; a refusal says why.

import { useCallback, useMemo } from "react";
import { pinFactoryDefinition } from "@/lib/factoryApi";
import {
  type DiscoveryActionHandle,
  type OpportunityLever,
  addHunch,
  linkSignal,
  patchOpportunity,
  pauseSource,
  planOpportunity,
  researchOpportunity,
  resumeSource,
  withdrawHunch,
} from "@/lib/factoryDiscoveryApi";
import { useFactoryToast } from "../ui/Toast";
import { KILL_REASON_WORD, type KillReason, type OpportunityRow } from "./discoveryModel";
import { useRefreshDiscovery } from "./discoveryQueries";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export interface DiscoveryLevers {
  vote: (row: Pick<OpportunityRow, "id" | "title" | "votes">) => Promise<void>;
  pin: (row: Pick<OpportunityRow, "id" | "title" | "pinned">) => Promise<void>;
  park: (row: Pick<OpportunityRow, "id" | "title">) => Promise<void>;
  unpark: (row: Pick<OpportunityRow, "id" | "title">) => Promise<void>;
  kill: (
    row: Pick<OpportunityRow, "id" | "title">,
    reason: KillReason,
    note?: string,
  ) => Promise<void>;
  revive: (row: Pick<OpportunityRow, "id" | "title">) => Promise<void>;
  plan: (row: Pick<OpportunityRow, "id" | "title">) => Promise<void>;
  research: (
    row: Pick<OpportunityRow, "id" | "title">,
    question: string,
    budgetUsd?: number,
  ) => Promise<boolean>;
  attach: (signal: string, opportunity: string, counter: boolean) => Promise<void>;
  hunch: (text: string, opportunity?: string) => Promise<boolean>;
  withdraw: (signal: string) => Promise<void>;
  pauseSource: (id: string, label: string) => Promise<void>;
  resumeSource: (id: string, label: string) => Promise<void>;
}

export function useDiscoveryLevers(factoryId: string): DiscoveryLevers {
  const toast = useFactoryToast();
  const refresh = useRefreshDiscovery(factoryId);

  const undoOf = useCallback(
    (ref: string, action: DiscoveryActionHandle | null | undefined, what: string) =>
      action
        ? () => {
            patchOpportunity(factoryId, ref, { op: "undo", action: action.id }).then(
              () => {
                refresh();
                toast.show({ message: `Undone: ${what}.` });
              },
              (error: unknown) => toast.show({ message: `Could not undo: ${message(error)}` }),
            );
          }
        : undefined,
    [factoryId, refresh, toast],
  );

  const lever = useCallback(
    async (ref: string, body: OpportunityLever, done: string): Promise<void> => {
      try {
        const result = await patchOpportunity(factoryId, ref, body);
        refresh();
        toast.show({ message: `${done}.`, undo: undoOf(ref, result.action, done.toLowerCase()) });
      } catch (error) {
        toast.show({ message: `Could not change ${ref}: ${message(error)}` });
      }
    },
    [factoryId, refresh, toast, undoOf],
  );

  return useMemo(
    () => ({
      vote: (row) =>
        lever(
          row.id,
          { op: "vote", value: row.votes.mine > 0 ? 0 : 1 },
          row.votes.mine > 0
            ? `Took back your vote on ${row.id}`
            : `Voted for ${row.id}. Votes never change the score`,
        ),
      pin: (row) =>
        lever(
          row.id,
          { op: "pin", on: !row.pinned },
          row.pinned ? `Unpinned ${row.id}` : `Pinned ${row.id}. Pins never change the score`,
        ),
      park: (row) => lever(row.id, { op: "park" }, `Parked ${row.id}`),
      unpark: (row) => lever(row.id, { op: "unpark" }, `Unparked ${row.id}`),
      kill: (row, reason, note) =>
        lever(
          row.id,
          { op: "kill", reason, ...(note?.trim() ? { note: note.trim() } : {}) },
          `Killed ${row.id}: ${KILL_REASON_WORD[reason].toLowerCase()}`,
        ),
      revive: (row) => lever(row.id, { op: "revive" }, `Revived ${row.id}`),
      plan: async (row) => {
        try {
          await planOpportunity(factoryId, row.id);
          refresh();
          toast.show({
            message: `Asked the portfolio lead to plan ${row.id}. Its proposal shows here and in Needs you.`,
          });
        } catch (error) {
          toast.show({ message: `Could not ask for a plan: ${message(error)}` });
        }
      },
      research: async (row, question, budgetUsd) => {
        try {
          const result = await researchOpportunity(factoryId, row.id, {
            question,
            ...(budgetUsd != null ? { budget_usd: budgetUsd } : {}),
          });
          refresh();
          toast.show({
            message: `Asked Scout to research ${row.id}.`,
            undo: undoOf(row.id, result.action, `the research ask on ${row.id}`),
          });
          return true;
        } catch (error) {
          toast.show({ message: `Could not ask Scout: ${message(error)}` });
          return false;
        }
      },
      attach: async (signal, opportunity, counter) => {
        try {
          const result = await linkSignal(factoryId, signal, { opportunity, counter }, true);
          refresh();
          const what = `${signal} attached to ${opportunity}${counter ? " as evidence against" : ""}`;
          toast.show({ message: `${what}.`, undo: undoOf(opportunity, result.action, what) });
        } catch (error) {
          toast.show({ message: `Could not attach ${signal}: ${message(error)}` });
        }
      },
      hunch: async (text, opportunity) => {
        try {
          await addHunch(factoryId, { text, ...(opportunity ? { opportunity } : {}) });
          refresh();
          toast.show({ message: "Hunch added. It counts as a hunch, never as user evidence." });
          return true;
        } catch (error) {
          toast.show({ message: `Could not add the hunch: ${message(error)}` });
          return false;
        }
      },
      withdraw: async (signal) => {
        try {
          await withdrawHunch(factoryId, signal);
          refresh();
          toast.show({ message: `Withdrew hunch ${signal}.` });
        } catch (error) {
          toast.show({ message: `Could not withdraw ${signal}: ${message(error)}` });
        }
      },
      pauseSource: async (id, label) => {
        try {
          const result = await pauseSource(factoryId, id);
          refresh();
          const pin = result.undo?.pin;
          toast.show({
            message: result.changed ? `Paused ${label}.` : `${label} was already paused.`,
            undo:
              result.changed && pin != null
                ? () => {
                    pinFactoryDefinition(factoryId, pin).then(
                      () => {
                        refresh();
                        toast.show({ message: `${label} runs again.` });
                      },
                      (error: unknown) =>
                        toast.show({ message: `Could not undo: ${message(error)}` }),
                    );
                  }
                : undefined,
          });
        } catch (error) {
          toast.show({ message: `Could not pause ${label}: ${message(error)}` });
        }
      },
      resumeSource: async (id, label) => {
        try {
          const result = await resumeSource(factoryId, id);
          refresh();
          toast.show({ message: result.changed ? `Resumed ${label}.` : `${label} already runs.` });
        } catch (error) {
          toast.show({ message: `Could not resume ${label}: ${message(error)}` });
        }
      },
    }),
    [factoryId, lever, refresh, toast, undoOf],
  );
}
