// Answering in place with Undo. An answer reaches an agent at once and can
// never be recalled, so the card holds it for a few seconds first: the row
// leaves the list, the toast says when it goes out and offers Undo (also `u`),
// and only then is it sent. Leaving the page (or the tab) sends what is held
// at once, so no answer is lost. A failed send brings the row back and says why.

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import { type Ask, type AskOption, sendAskAnswer } from "@/lib/factoryAsksApi";
import { type DecisionConflictBody, isDecisionConflict } from "@/lib/factoryApi";
import { useFactoryToast } from "../ui/Toast";

/** How long an answer waits for Undo before it goes out. */
export const ANSWER_UNDO_MS = 8_000;

interface Held {
  ask: Ask;
  option: AskOption;
  timer: ReturnType<typeof setTimeout>;
}

export interface DeferredAnswers {
  /** Rows held or sent and not yet gone from the server's list. */
  hidden: ReadonlySet<string>;
  /** Hold *option* as the answer to *ask*, with Undo. */
  answer: (ask: Ask, option: AskOption) => void;
  /** Forget hidden rows the server no longer lists. */
  prune: (listed: ReadonlySet<string>) => void;
}

function who(error: unknown): string | null {
  if (!isDecisionConflict(error)) return null;
  const body = (error.body ?? {}) as DecisionConflictBody;
  const by = body.answered_by ?? body.decision?.answered_by ?? null;
  return by ? by.split("@")[0] || by : "someone";
}

export function useDeferredAnswers(factoryId: string): DeferredAnswers {
  const toast = useFactoryToast();
  const queryClient = useQueryClient();
  const held = useRef(new Map<string, Held>());
  const mounted = useRef(true);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set());

  const setShown = useCallback((id: string, shown: boolean) => {
    setHidden((prev) => {
      if (prev.has(id) === !shown) return prev;
      const next = new Set(prev);
      if (shown) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const send = useCallback(
    (entry: Held, keepalive = false) => {
      held.current.delete(entry.ask.id);
      const ref = entry.ask.ref ?? entry.ask.id;
      sendAskAnswer(entry.ask.decision_id ?? "", { option_id: entry.option.id }, { keepalive })
        .then(
          () => {
            if (mounted.current) toast.show({ message: `Answered ${ref}: ${entry.option.label}.` });
          },
          (error: unknown) => {
            if (!mounted.current) return;
            const by = who(error);
            if (by == null) setShown(entry.ask.id, true);
            const why = error instanceof Error ? error.message : String(error);
            toast.show({
              message: by
                ? `${ref} was already answered by ${by}.`
                : `Could not answer ${ref}: ${why}`,
            });
          },
        )
        .finally(() => {
          for (const key of factoryInvalidationKeys(factoryId)) {
            void queryClient.invalidateQueries({ queryKey: key });
          }
        });
    },
    [factoryId, queryClient, setShown, toast],
  );

  const answer = useCallback(
    (ask: Ask, option: AskOption) => {
      if (!ask.decision_id || held.current.has(ask.id)) return;
      const entry: Held = { ask, option, timer: setTimeout(() => send(entry), ANSWER_UNDO_MS) };
      held.current.set(ask.id, entry);
      setShown(ask.id, false);
      toast.show({
        message: `${option.label}: your answer to ${ask.ref ?? ask.id} goes out in a few seconds.`,
        durationMs: ANSWER_UNDO_MS,
        undo: () => {
          const still = held.current.get(ask.id);
          if (!still) return;
          clearTimeout(still.timer);
          held.current.delete(ask.id);
          setShown(ask.id, true);
        },
      });
    },
    [send, setShown, toast],
  );

  // Leaving the page (or closing the tab) sends what is held right away.
  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(() => {
    mounted.current = true;
    const flush = () => {
      for (const entry of [...held.current.values()]) {
        clearTimeout(entry.timer);
        sendRef.current(entry, true);
      }
    };
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      mounted.current = false;
      flush();
    };
  }, []);

  const prune = useCallback((listed: ReadonlySet<string>) => {
    setHidden((prev) => {
      const keep = [...prev].filter((id) => listed.has(id) || held.current.has(id));
      return keep.length === prev.size ? prev : new Set(keep);
    });
  }, []);

  return { hidden, answer, prune };
}
