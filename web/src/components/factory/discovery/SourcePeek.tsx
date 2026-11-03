/**
 * A source in the side panel: in plain words what Scout can read, how much,
 * and how long text is kept; the three read-only layers; its usage and the
 * access log (every read, pause and resume, by whom). A source an agent
 * proposed waits for an owner: the panel shows what approving would let Scout
 * read and the proposal to approve or reject. Pausing applies at once with
 * Undo; resuming a paused source is for owners.
 */

import { LockIcon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { testSource } from "@/lib/factoryDiscoveryApi";
import { cn } from "@/lib/utils";
import { ProposalCard } from "../cards/ProposalCard";
import { useFactoryProposal } from "../insights/learnQueries";
import { Peek } from "../ui/Peek";
import { useFactoryToast } from "../ui/Toast";
import { MEANING } from "../ui/statusTokens";
import {
  HUNCH_WEIGHT_FALLBACK,
  type PendingSource,
  type SourceView,
  sourceStateWord,
  starterReads,
} from "./discoveryModel";
import { useSourceLog } from "./discoveryQueries";
import type { DiscoveryLevers } from "./useDiscoveryLevers";

const EYEBROW = "text-xs font-semibold tracking-wide text-muted-foreground uppercase";

function readsOf(kind: string, weight: number): string {
  return starterReads(kind, weight) ?? `what its ${kind} connection returns, read-only`;
}

function Layers() {
  const row = (title: string, text: string) => (
    <li className="flex items-start gap-2 text-sm">
      <LockIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
      <span>
        <b>{title}:</b> {text}
      </span>
    </li>
  );
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className={EYEBROW}>Read-only, in three layers</h3>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        {row(
          "Provider",
          "only read access; a source that needs a key keeps it write-only on the server, tested on save.",
        )}
        {row(
          "Gateway",
          "only listed read tools run; names like create, update, delete, post or send are refused.",
        )}
        {row(
          "Policy",
          "Scout cannot push, write issues or contact anyone; the server checks the caller's factory and role on every call.",
        )}
      </ul>
    </section>
  );
}

interface LogEntry {
  id: string;
  ts: number;
  summary: string;
}

function logEntries(raw: unknown): LogEntry[] {
  const entries = (raw as { entries?: unknown } | null)?.entries;
  if (!Array.isArray(entries)) return [];
  return entries
    .map((e) => {
      const row = e as Record<string, unknown>;
      const ts = typeof row.ts === "number" ? row.ts : null;
      const summary = typeof row.summary === "string" ? row.summary : null;
      return ts != null && summary ? { id: String(row.id ?? `${ts}`), ts, summary } : null;
    })
    .filter((e): e is LogEntry => e != null)
    .slice(0, 10);
}

function when(ms: number): string {
  // Event stamps are epoch milliseconds.
  const at = new Date(ms > 1e12 ? ms : ms * 1000);
  return at.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function Configured({
  factoryId,
  source,
  canAct,
  weight,
}: {
  factoryId: string;
  source: SourceView;
  canAct: boolean;
  weight: number;
}) {
  const log = useSourceLog(factoryId, source.id, true);
  const toast = useFactoryToast();
  const [testing, setTesting] = useState(false);
  const test = async () => {
    setTesting(true);
    try {
      await testSource(factoryId, source.id);
      toast.show({ message: `${source.label} answered. Nothing was stored.` });
    } catch (error) {
      toast.show({
        message: `${source.label} did not answer: ${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      setTesting(false);
    }
  };
  const entries = logEntries(log.data);
  const held = source.health === "failing" && source.state !== "paused";
  const calls =
    source.callsPerDay != null
      ? `up to ${source.callsPerDay} calls a day`
      : "no call budget of its own";
  return (
    <div data-testid={`source-peek-${source.id}`} className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        {source.kind} source · privacy {source.privacy ?? "public"} · needs no secret
      </p>
      <section className="flex flex-col gap-1.5">
        <h3 className={EYEBROW}>In plain words</h3>
        <p className="rounded-lg border border-dashed border-border px-3 py-2 text-sm">
          Scout can read {readsOf(source.kind, weight)}, {calls}. Emails, phone numbers and tokens
          are removed before anyone reads it.
          {source.keepDays != null ? ` Text is kept ${source.keepDays} days.` : ""}
          {source.state === "trial" ? " It runs a 14-day trial first, then asks “keep it?”." : ""}
        </p>
      </section>
      {held && (
        <p
          data-testid="source-held"
          className={cn("rounded-lg border px-3 py-2 text-sm", MEANING.broken.border)}
        >
          <b>On hold after repeated failures.</b> Scout reads nothing from it until someone lifts
          the hold.
          {source.lastError ? ` The last error: ${source.lastError}` : ""}
        </p>
      )}
      <Layers />
      <section className="flex flex-col gap-1.5">
        <h3 className={cn(EYEBROW, "flex items-center gap-2")}>
          Usage
          {canAct && source.state !== "paused" && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={testing}
              onClick={() => void test()}
              data-testid="source-test"
              className="ml-auto normal-case tracking-normal"
            >
              Test it
            </Button>
          )}
        </h3>
        <p className="text-sm">
          {[
            source.callsPerDay != null
              ? `${source.callsToday ?? 0} of ${source.callsPerDay} calls today`
              : null,
            source.signalsWeek != null ? `${source.signalsWeek} signals this week` : null,
            source.signalsTotal != null ? `${source.signalsTotal} in all` : null,
          ]
            .filter(Boolean)
            .join(" · ") || "Nothing read yet."}
        </p>
        {source.lastError && !held && (
          <p className={cn("text-sm", MEANING.broken.text)}>Last error: {source.lastError}</p>
        )}
      </section>
      <section className="flex flex-col gap-1.5">
        <h3 className={EYEBROW}>Access log</h3>
        {log.isLoading ? (
          <div aria-busy="true" className="h-16 animate-pulse rounded-lg bg-muted" />
        ) : entries.length === 0 ? (
          <p className="text-sm text-muted-foreground">No reads yet.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col p-0" data-testid="source-log">
            {entries.map((e) => (
              <li
                key={e.id}
                className="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-2 border-t border-border py-1.5 text-sm first:border-t-0"
              >
                <span className="font-mono text-xs text-muted-foreground">{when(e.ts)}</span>
                <span className="break-words">{e.summary}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Pending({
  factoryId,
  pending,
  snapshot,
  clockOffsetS,
  weight,
}: {
  factoryId: string;
  pending: PendingSource;
  snapshot: FactorySnapshot;
  clockOffsetS: number;
  weight: number;
}) {
  const proposal = useFactoryProposal(factoryId, pending.proposal).data?.proposal ?? null;
  const calls = pending.source.budget?.calls_per_day;
  return (
    <div data-testid={`source-peek-${pending.id}`} className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        {pending.source.kind ?? "source"} source · waits for an owner
        {pending.by ? ` · proposed by ${pending.by}` : ""}
      </p>
      <section className="flex flex-col gap-1.5">
        <h3 className={EYEBROW}>In plain words</h3>
        <p className="rounded-lg border border-dashed border-border px-3 py-2 text-sm">
          If an owner approves, Scout can read {readsOf(String(pending.source.kind ?? ""), weight)}
          {calls != null ? `, up to ${calls} times a day` : ""}. It starts with a 14-day trial, then
          asks “keep it?”. Nothing is read until then.
        </p>
      </section>
      <Layers />
      {proposal ? (
        <ProposalCard
          factoryId={factoryId}
          proposal={proposal}
          snapshot={snapshot}
          clockOffsetS={clockOffsetS}
        />
      ) : (
        <div aria-busy="true" className="h-24 animate-pulse rounded-lg bg-muted" />
      )}
    </div>
  );
}

export function SourcePeek({
  factoryId,
  sourceId,
  sources,
  pending,
  snapshot,
  clockOffsetS,
  canAct,
  isOwner,
  levers,
  hunchWeight = HUNCH_WEIGHT_FALLBACK,
  onClose,
}: {
  factoryId: string;
  /** The source's id; null keeps the panel closed. */
  sourceId: string | null;
  sources: readonly SourceView[];
  pending: readonly PendingSource[];
  snapshot: FactorySnapshot;
  clockOffsetS: number;
  canAct: boolean;
  isOwner: boolean;
  levers: DiscoveryLevers;
  /** The rubric's hunch weight, for the team source's plain words. */
  hunchWeight?: number;
  onClose: () => void;
}) {
  const source = sources.find((s) => s.id === sourceId) ?? null;
  const waiting = source ? null : (pending.find((p) => p.id === sourceId) ?? null);
  const paused = source?.state === "paused";
  // The server holds a source after repeated failures; any editor lifts the hold.
  const held = source != null && !paused && source.health === "failing";
  return (
    <Peek
      open={sourceId != null}
      onOpenChange={(open) => !open && onClose()}
      title={source?.label ?? waiting?.label ?? sourceId ?? ""}
      headerExtra={
        <span className="flex shrink-0 items-center gap-1.5">
          <span className="rounded border border-border px-1.5 font-mono text-xs">{sourceId}</span>
          <span
            className={cn(
              "rounded-md px-1.5 text-xs font-medium max-[760px]:hidden",
              waiting ? cn(MEANING.needsYou.surface, MEANING.needsYou.text) : "bg-muted",
            )}
          >
            {waiting ? "waits for you" : source ? sourceStateWord(source) : ""}
          </span>
        </span>
      }
      footer={
        source && canAct ? (
          held ? (
            <Button
              size="sm"
              data-testid="source-lift-hold"
              onClick={() => void levers.resumeSource(source.id, source.label, { hold: true })}
            >
              Lift the hold and retry
            </Button>
          ) : paused ? (
            isOwner ? (
              <Button size="sm" onClick={() => void levers.resumeSource(source.id, source.label)}>
                Resume
              </Button>
            ) : (
              <p className="text-sm text-muted-foreground">An owner resumes a paused source.</p>
            )
          ) : (
            <Button
              size="sm"
              variant="outline"
              data-testid="source-pause"
              onClick={() => void levers.pauseSource(source.id, source.label)}
            >
              Pause
            </Button>
          )
        ) : undefined
      }
    >
      {source ? (
        <Configured factoryId={factoryId} source={source} canAct={canAct} weight={hunchWeight} />
      ) : waiting ? (
        <Pending
          factoryId={factoryId}
          pending={waiting}
          snapshot={snapshot}
          clockOffsetS={clockOffsetS}
          weight={hunchWeight}
        />
      ) : (
        <p role="alert" className="text-sm">
          {sourceId} is not a source of this factory.
        </p>
      )}
    </Peek>
  );
}
