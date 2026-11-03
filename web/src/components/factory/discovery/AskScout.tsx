/**
 * Asking Scout. On an opportunity, "Research this" is a structured ask: a
 * question, a budget (capped at the factory's per-ask budget unless an owner
 * raises it) and a due date; it applies at once with Undo. Anywhere else,
 * Ask Scout opens the Room drawer on Scout's thread (`#discovery`), or on
 * `#factory` while this server has no discovery thread yet.
 */

import { MessageSquareIcon, SendIcon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useSearchParams } from "@/lib/routing";
import { useRoomThreads } from "../room/roomQueries";

export const DISCOVERY_THREAD = "discovery";
const FALLBACK_THREAD = "factory";

/** The Room thread Ask Scout opens: Scout's when the server has it, else the factory's. */
export function useAskScoutThread(factoryId: string): string {
  const threads = useRoomThreads(factoryId).data?.data ?? [];
  return threads.some((t) => t.key === DISCOVERY_THREAD) ? DISCOVERY_THREAD : FALLBACK_THREAD;
}

export function AskScoutButton({
  factoryId,
  discuss = false,
  variant = "outline",
}: {
  factoryId: string;
  /** "Discuss in #<thread>" instead of "Ask Scout". */
  discuss?: boolean;
  variant?: "outline" | "ghost";
}) {
  const thread = useAskScoutThread(factoryId);
  const label = discuss ? `Discuss in #${thread}` : "Ask Scout";
  const [, setParams] = useSearchParams();
  return (
    <Button
      type="button"
      size="sm"
      variant={variant}
      data-testid={discuss ? "discovery-discuss" : "discovery-ask-scout"}
      title={
        thread === DISCOVERY_THREAD
          ? "Opens #discovery in the Room"
          : "Scout's thread opens once Scout runs; the portfolio lead answers in #factory meanwhile"
      }
      onClick={() =>
        setParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            next.set("room", thread);
            return next;
          },
          { replace: true },
        )
      }
    >
      <MessageSquareIcon aria-hidden />
      {label}
    </Button>
  );
}

/** "Research this": a question for Scout with a budget, on one opportunity. */
export function ResearchForm({
  opportunity,
  perAsk,
  onSend,
  onCancel,
}: {
  opportunity: string;
  /** The factory's per-ask cap (`discovery.budgets.per_ask`). */
  perAsk: number | null;
  onSend: (question: string) => Promise<boolean>;
  onCancel: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async () => {
    const q = question.trim();
    if (!q || busy) return;
    setBusy(true);
    const ok = await onSend(q);
    setBusy(false);
    if (ok) setQuestion("");
  };
  return (
    <form
      data-testid={`research-form-${opportunity}`}
      className="flex flex-col gap-2 rounded-lg border border-border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      <label htmlFor={`research-${opportunity}`} className="text-sm font-medium">
        What should Scout find out about {opportunity}?
      </label>
      <Textarea
        id={`research-${opportunity}`}
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        maxLength={500}
        rows={2}
        placeholder="Who has this problem most, and what do they do today?"
        data-testid={`research-question-${opportunity}`}
      />
      <p className="text-xs text-muted-foreground">
        {perAsk != null ? `Up to $${perAsk} per ask` : "Capped at the factory's per-ask budget"}.
        Scout answers in #discovery with new signals and an updated {opportunity}. You can undo for
        10 minutes.
      </p>
      <div className="flex gap-1.5">
        <Button
          type="submit"
          size="sm"
          disabled={busy || !question.trim()}
          data-testid={`research-send-${opportunity}`}
        >
          <SendIcon aria-hidden />
          Ask Scout
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
