/**
 * Steer › Sources: one card per evidence source (its kind, state, what it
 * reads, privacy, retention, key or not, calls today against its cap and
 * signals this week) with Pause / Resume (at once, with Undo), Test and the
 * access log; sources an agent proposed wait in amber for an owner. Add
 * source opens the four-step sheet, which stages a definition change. The
 * rules every source follows (Tier 0) close the section.
 */

import { CheckIcon, PlusIcon, XIcon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { ProposalView, SourceDef } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { formatElapsed } from "../factoryFormat";
import { usePinDefinition } from "../insights/learnQueries";
import { SectionCard } from "../project/projectParts";
import { Peek } from "../ui/Peek";
import { DATA_HUE, MEANING } from "../ui/statusTokens";
import { useFactoryToast } from "../ui/Toast";
import { AddSourceSheet } from "./AddSourceSheet";
import { useSteer } from "./SteerContext";
import { STAGED_CONTROL, StagedChip, TierZero } from "./steerFields";
import { agentProposals, proposerName, stageProposal, stagedList } from "./steerModel";
import { usePauseSource, useSourceCounts, useSourceLog, useTestSource } from "./steerQueries";

const SECRET_KINDS = new Set([
  "analytics",
  "observability",
  "tracker",
  "reviews",
  "feedback",
  "flags",
  "mcp",
]);

/** What a source reads, in words, from its kind and parameters. */
export function readsText(source: Pick<SourceDef, "kind" | "params">): string {
  const p = source.params ?? {};
  switch (source.kind) {
    case "repo": {
      const repos =
        Array.isArray(p.repos) && p.repos.length
          ? (p.repos as string[]).join(", ")
          : "the factory's own repos";
      const since = typeof p.since_days === "number" ? p.since_days : 90;
      return `Issues, discussions and releases of ${repos}, last ${since} days`;
    }
    case "web": {
      const domains =
        Array.isArray(p.domains) && p.domains.length
          ? `on ${(p.domains as string[]).join(", ")}`
          : "public pages";
      return `Web search and ${domains}${p.changelogs === false ? "" : ", plus competitors' changelogs"}`;
    }
    case "team":
      return "Hunches and requests from the factory's people. They count as hunches, not user evidence.";
    default:
      return `${source.kind} through read-only tools`;
  }
}

function stateChip(state: string, held: boolean) {
  if (held) return { text: "on hold", cls: cn(MEANING.broken.surface, MEANING.broken.text) };
  switch (state) {
    case "active":
      return { text: "on", cls: cn(MEANING.working.surface, MEANING.working.text) };
    case "trial":
      return { text: "on trial", cls: cn(MEANING.selected.surface, MEANING.selected.text) };
    default:
      return { text: "paused", cls: "bg-muted text-muted-foreground" };
  }
}

function SourceLogPeek({ sourceId, onClose }: { sourceId: string | null; onClose: () => void }) {
  const steer = useSteer();
  const log = useSourceLog(steer.factoryId, sourceId);
  return (
    <Peek
      open={sourceId != null}
      onOpenChange={(open) => !open && onClose()}
      title={`Access log · ${sourceId ?? ""}`}
    >
      {log.isLoading ? (
        <div aria-busy="true" className="h-32 animate-pulse rounded-lg bg-muted/70" />
      ) : log.error ? (
        <p className="text-sm text-muted-foreground">Could not read the log: {log.error.message}</p>
      ) : (
        <div data-testid="steer-source-log" className="flex flex-col gap-2 text-sm">
          <p className="text-muted-foreground">{log.data?.note}</p>
          {(log.data?.entries ?? []).length === 0 ? (
            <p className="text-muted-foreground">Nothing read yet.</p>
          ) : (
            <ul className="flex flex-col divide-y divide-border/60">
              {log.data!.entries.map((e) => (
                <li key={e.id} className="flex flex-col gap-0.5 py-1.5">
                  <span>{e.summary}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {e.kind} · {formatElapsed(steer.nowS - Math.floor(e.ts / 1000)) ?? "now"} ago
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Peek>
  );
}

function SourceCard({
  source,
  counts,
  onLog,
}: {
  source: SourceDef & { staged?: "added" | "changed" | "removed" };
  counts: {
    callsToday: number | null;
    callsPerDay: number | null;
    signalsWeek: number | null;
    health: string | null;
  } | null;
  onLog: (id: string) => void;
}) {
  const steer = useSteer();
  const toast = useFactoryToast();
  const pause = usePauseSource(steer.factoryId);
  const pin = usePinDefinition(steer.factoryId);
  const test = useTestSource(steer.factoryId);
  const [tested, setTested] = useState<{ ok: boolean; text: string } | null>(null);
  const held = counts?.health === "failing";
  const chip = stateChip(source.state, held);
  const cap = counts?.callsPerDay ?? source.budget?.calls_per_day ?? null;
  const used = counts?.callsToday ?? null;
  const paused = source.state === "paused";
  const live = !source.staged;
  const toggle = () =>
    pause.mutate(
      { source: source.id, paused: !paused },
      {
        onSuccess: (res) =>
          toast.show({
            message: paused
              ? `Resumed ${source.label}.`
              : `Paused ${source.label}. Its reads stopped.`,
            undo: res.undo?.pin != null ? () => pin.mutate(res.undo!.pin) : undefined,
          }),
        onError: (error) => toast.show({ message: `Not done: ${error.message}` }),
      },
    );
  const runTest = () =>
    test.mutate(source.id, {
      onSuccess: (res) =>
        setTested({
          ok: res.ok,
          text: res.checks.map((c) => `${c.ok ? "✓" : "✗"} ${c.name}: ${c.detail}`).join(" · "),
        }),
      onError: (error) => setTested({ ok: false, text: error.message }),
    });
  return (
    <section
      data-testid={`steer-source-${source.id}`}
      aria-label={source.label}
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-3",
        source.staged && STAGED_CONTROL,
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="rounded border border-border px-1.5 font-mono text-xs">{source.kind}</span>
        <b className="min-w-0 flex-1 truncate text-sm font-semibold">
          {source.staged === "removed" ? <s>{source.label}</s> : source.label}
        </b>
        {source.staged ? (
          <StagedChip />
        ) : (
          <span className={cn("rounded-md px-1.5 text-xs leading-5", chip.cls)}>{chip.text}</span>
        )}
        {source.staged === "added" && steer.canEdit && (
          <button
            type="button"
            aria-label={`Drop the new source ${source.label}`}
            onClick={() =>
              steer.removeItem("sources", "discovery.sources", source.id, source.label)
            }
            className="cursor-pointer rounded p-0.5 hover:bg-muted"
          >
            <XIcon aria-hidden className="size-3.5" />
          </button>
        )}
      </div>
      <p className="text-sm text-foreground/85">{readsText(source)}</p>
      <p className="text-xs text-muted-foreground">
        {source.kind} · privacy {source.privacy?.level ?? "—"} · kept{" "}
        {source.privacy?.keep_days ?? "—"} days ·{" "}
        {SECRET_KINDS.has(source.kind) ? "read-only key" : "no secret"}
      </p>
      {live && cap != null && (
        <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
          <span className="w-32 shrink-0">
            {used != null ? `${used} of ${cap} calls today` : `up to ${cap} calls a day`}
          </span>
          {used != null && cap > 0 && (
            <span
              role="img"
              aria-label={`${used} of ${cap} calls today`}
              className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-muted"
            >
              <i
                className="block h-full"
                style={{ width: `${Math.min(100, (used / cap) * 100)}%`, background: DATA_HUE }}
              />
            </span>
          )}
        </div>
      )}
      {live && counts?.signalsWeek != null && (
        <p className="text-xs text-muted-foreground">Signals this week: {counts.signalsWeek}</p>
      )}
      {tested && (
        <p
          role="status"
          className={cn("text-xs", tested.ok ? MEANING.working.text : "text-destructive")}
        >
          {tested.ok && <CheckIcon aria-hidden className="mr-1 inline size-3" />}
          {tested.text}
        </p>
      )}
      {live && (
        <div className="flex flex-wrap items-center gap-1.5">
          {steer.canEdit && (paused ? steer.owner : true) && (
            <Button
              size="xs"
              variant="outline"
              onClick={toggle}
              loading={pause.isPending}
              data-testid={`steer-source-pause-${source.id}`}
              componentId="factory.steer.source_pause"
            >
              {paused ? "Resume" : "Pause"}
            </Button>
          )}
          {steer.canEdit && !paused && (
            <Button
              size="xs"
              variant="ghost"
              onClick={runTest}
              loading={test.isPending}
              componentId="factory.steer.source_test"
            >
              Test
            </Button>
          )}
          <Button
            size="xs"
            variant="ghost"
            onClick={() => onLog(source.id)}
            componentId="factory.steer.source_log"
          >
            Access log
          </Button>
        </div>
      )}
    </section>
  );
}

function WaitingSource({ proposal }: { proposal: ProposalView }) {
  const steer = useSteer();
  const toast = useFactoryToast();
  const add = proposal.ops.find(
    (op) => op.op === "add" && String(op.path).replace("/", ".") === "discovery.sources",
  );
  const source = (add?.value ?? {}) as Partial<SourceDef>;
  const stage = () => {
    const before = steer.staging;
    steer.setStaging(stageProposal(proposal, steer.effective));
    toast.show({
      message: `Staged ${proposal.ref}. Review it, then apply it as an owner.`,
      undo: () => steer.setStaging(before),
    });
  };
  return (
    <section
      data-testid={`steer-source-waiting-${proposal.ref}`}
      aria-label={source.label ?? proposal.title}
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-xl border bg-card p-3",
        MEANING.needsYou.border,
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        {source.kind && (
          <span className="rounded border border-border px-1.5 font-mono text-xs">
            {source.kind}
          </span>
        )}
        <b className="min-w-0 flex-1 truncate text-sm font-semibold">
          {source.label ?? proposal.title}
        </b>
        <span
          className={cn(
            "rounded-md px-1.5 text-xs leading-5",
            MEANING.needsYou.surface,
            MEANING.needsYou.text,
          )}
        >
          waits for an owner
        </span>
      </div>
      {source.kind && (
        <p className="text-sm text-foreground/85">
          {readsText({ kind: source.kind, params: source.params ?? {} })}
        </p>
      )}
      {proposal.rationale && <p className="text-xs text-muted-foreground">{proposal.rationale}</p>}
      <div className="flex items-center gap-2">
        {steer.canEdit && (
          <Button size="xs" onClick={stage} componentId="factory.steer.source_review">
            Review · {steer.owner ? "approve as owner" : "stage it"}
          </Button>
        )}
        <span className="text-xs text-muted-foreground">
          proposed by {proposerName(proposal)} ({proposal.ref})
        </span>
      </div>
    </section>
  );
}

export function Sources({ proposals }: { proposals: readonly ProposalView[] | undefined }) {
  const steer = useSteer();
  const [adding, setAdding] = useState(false);
  const [logOf, setLogOf] = useState<string | null>(null);
  const counts = useSourceCounts(steer.factoryId, steer.discoveryOn);
  const sources = stagedList<SourceDef>(
    steer.staging,
    "discovery.sources",
    steer.effective.discovery?.sources ?? [],
  );
  const waiting = agentProposals(proposals ?? []).filter((p) =>
    p.ops.some(
      (op) => op.op === "add" && String(op.path).replace("/", ".") === "discovery.sources",
    ),
  );
  const countOf = (id: string) => counts.data?.find((c) => c.id === id) ?? null;
  return (
    <div data-testid="steer-sources" className="flex min-w-0 flex-col gap-4">
      <div className="flex min-w-0 flex-wrap items-start gap-3">
        <p className="min-w-0 flex-1 text-sm text-muted-foreground">
          Only the server reads sources. Every call is logged, and personal data is removed before
          anyone reads it. Pausing applies at once; resuming, adding or widening a source needs an
          owner, and a new source runs a 14-day trial.
        </p>
        {steer.canEdit && !steer.frozen && (
          <Button
            size="sm"
            onClick={() => setAdding(true)}
            data-testid="steer-add-source-open"
            componentId="factory.steer.add_source"
          >
            <PlusIcon />
            Add source
          </Button>
        )}
      </div>
      {sources.length === 0 && waiting.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
          No sources yet. Add one, or stage the discovery starter on the Overview.
        </p>
      ) : (
        <div className="grid min-w-0 grid-cols-1 gap-3 lg:grid-cols-2">
          {sources.map((s) => (
            <SourceCard key={s.id} source={s} counts={countOf(s.id)} onLog={setLogOf} />
          ))}
          {waiting.map((p) => (
            <WaitingSource key={p.id} proposal={p} />
          ))}
        </div>
      )}
      <SectionCard title="Rules for every source" meta="Tier 0" testId="steer-source-rules">
        <ul className="flex flex-col gap-1 text-sm">
          <li>
            Emails, phones, tokens, addresses and card-like numbers are removed before storing.
            Account ids are hashed per factory.
          </li>
          <li>
            Quotes never leave the cockpit unless the source is public. Builders and project leads
            never read signals.
          </li>
          <li>
            Segments under 5 accounts are hidden. Personal sources stay off until the server can
            detect names.
          </li>
          <li>
            Signal text is untrusted data: wrapped, scored for injection, never turned into a tool
            call.
          </li>
        </ul>
        <TierZero>No definition change, trial or agent proposal changes these.</TierZero>
      </SectionCard>
      <AddSourceSheet open={adding} onOpenChange={setAdding} />
      <SourceLogPeek sourceId={logOf} onClose={() => setLogOf(null)} />
    </div>
  );
}
