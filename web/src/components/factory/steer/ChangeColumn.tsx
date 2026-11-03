/**
 * Steer's always-on right column. First the staged change for the next
 * definition version: each edit with its path, before → after and effect
 * (× drops it), or the restore with its diff; the effect on the ranking for
 * strategy and rubric changes; what else it does; who can change it; whether
 * it may run as a trial and why not; then Discard and Apply (Apply as a trial
 * where a trial makes sense; Propose to the owners when only they apply it).
 * Apply sends the version it was staged on, so it never overwrites a version
 * applied since (the column says so and offers Rebase). Apply creates one
 * version, says so in the toast, and offers Undo (it pins the version before
 * for 10 minutes, here and in Versions). Under it: agents' proposals and the
 * trials running now.
 */

import { AlertTriangleIcon, FlaskConicalIcon, XIcon } from "lucide-react";
import { forwardRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  type DefinitionPreviewEntry,
  type DefinitionPreviewResponse,
  FactoryApiError,
  type ProposalView,
} from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { controlErrorMessage } from "../controls/controlModel";
import {
  useAnswerProposal,
  useDefinitionEdit,
  usePinDefinition,
  useProposeChange,
} from "../insights/learnQueries";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import { useFactoryToast } from "../ui/Toast";
import { AgentProposals } from "./AgentProposals";
import { RerankTable } from "./RerankTable";
import { RunningTrials } from "./RunningTrials";
import { useSteer } from "./SteerContext";
import {
  EMPTY_STEER,
  type StagedEdit,
  type TrialRule,
  dotted,
  movesRank,
  proposalIntact,
  rerankRows,
  segmentNames,
  showValue,
  stagedChanges,
  trialRule,
} from "./steerModel";
import { useRestoreDefinition } from "./steerQueries";
import { UndoLatestButton, useUndoLatest } from "./UndoLatest";
import { CompareDialog } from "./Versions";
import { WhoCanChange } from "./WhoCanChange";

interface Preview {
  data: DefinitionPreviewResponse | undefined;
  error: Error | null;
  isFetching: boolean;
  /** The last staging's answer, shown while this one's loads. */
  isPlaceholderData: boolean;
  settled: boolean;
  refetch: () => unknown;
}

/** The value in force the preview's diff states for an edit (it may be newer than at staging). */
export function inForceBefore(
  diff: readonly DefinitionPreviewEntry[] | undefined,
  edit: StagedEdit,
): unknown {
  if (!diff || edit.change.op === "add") return edit.before;
  const path = dotted(edit.change.path);
  const entry = diff.find((d) => d.path === path);
  return entry ? (entry.before ?? null) : edit.before;
}

/** The trial rule the column states: the server's once it answers, else the client's. */
export function columnTrialRule(
  preview: DefinitionPreviewResponse | undefined,
  fallback: TrialRule,
): TrialRule {
  const verdict = preview?.trialable;
  if (!verdict) return fallback;
  if (verdict.ok) return { ...fallback, code: "ok", ok: true };
  if (verdict.code === "trial_overlap") {
    return {
      code: "trial_overlap",
      ok: false,
      head: `No second trial on ${verdict.block ?? "this block"}.`,
      text:
        verdict.reason ??
        `${verdict.trial ?? "A trial"} holds this block. Apply directly, or wait for it to end.`,
      trial: verdict.trial,
    };
  }
  if (verdict.code === "not_trialable" && fallback.code === "not_trialable") return fallback;
  if (verdict.code === "not_trialable") {
    return {
      code: "not_trialable",
      ok: false,
      head: "No trial for this change.",
      text: verdict.reason ?? "It applies as a new version; restore the old one any time.",
    };
  }
  return { ...fallback, ok: false, code: verdict.code ?? fallback.code };
}

function EditRow({
  edit,
  before,
  names,
}: {
  edit: StagedEdit;
  before: unknown;
  names: ReadonlyMap<string, string>;
}) {
  const steer = useSteer();
  const op = edit.change.op;
  return (
    <li
      data-testid={`steer-staged-${edit.key}`}
      className={cn(
        "relative flex min-w-0 flex-col gap-0.5 rounded-lg border p-2.5 pr-8",
        MEANING.staged.border,
        MEANING.staged.surface,
      )}
    >
      {steer.canEdit && (
        <button
          type="button"
          aria-label={`Drop the change to ${edit.label}`}
          onClick={() => steer.drop(edit.key)}
          className={cn(
            "absolute top-2 right-2 cursor-pointer rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground",
            FOCUS_RING,
          )}
        >
          <XIcon aria-hidden className="size-3.5" />
        </button>
      )}
      <span className="text-sm font-medium">{edit.label}</span>
      <span className="font-mono text-xs break-all text-foreground/75">
        {op === "add"
          ? `+ ${edit.change.path}`
          : op === "remove"
            ? `− ${edit.change.path}`
            : edit.change.path}
      </span>
      <span className="font-mono text-xs break-words">
        {op === "add" ? (
          <ins className="no-underline">{showValue(edit.after, names)}</ins>
        ) : op === "remove" ? (
          <s className="text-foreground/75">{showValue(before, names)}</s>
        ) : (
          <>
            <s className="text-foreground/75">{showValue(before, names)}</s> →{" "}
            <ins className="font-semibold no-underline">{showValue(edit.after, names)}</ins>
          </>
        )}
      </span>
      {edit.effect && <span className="text-xs text-foreground/80">{edit.effect}</span>}
    </li>
  );
}

function StagedCard({ preview }: { preview: Preview }) {
  const steer = useSteer();
  const toast = useFactoryToast();
  const edit = useDefinitionEdit(steer.factoryId);
  const pin = usePinDefinition(steer.factoryId);
  const restore = useRestoreDefinition(steer.factoryId);
  const answer = useAnswerProposal(steer.factoryId);
  const propose = useProposeChange(steer.factoryId);
  const latest = useUndoLatest();
  const [rationale, setRationale] = useState("");
  const [proposing, setProposing] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const { staging, definition, stale } = steer;
  const version = definition.def_version;
  // The version this was staged on: Apply sends it, so a version applied since is never overwritten.
  const base = staging.base ?? version;
  const next = preview.data?.version_next ?? version + 1;
  // The answer for what is staged now (not the last staging's, kept on screen while this one loads).
  const fresh = preview.settled && !preview.isPlaceholderData ? preview.data : undefined;
  const changes = stagedChanges(staging);
  const paths = fresh?.diff.map((d) => d.path) ?? changes.map((c) => c.path);
  const fallback = trialRule(paths, definition, { restore: staging.restore, nowS: steer.nowS });
  const rule = columnTrialRule(fresh, fallback);
  const names = segmentNames(steer.effective.product?.segments);
  const intact = proposalIntact(staging);
  const busy = edit.isPending || restore.isPending || answer.isPending || propose.isPending;
  // The stale line below says it in words; the server's row would repeat it.
  const blocked = (fresh?.blocked ?? []).filter((b) => !(stale && b.code === "stale_definition"));
  const showRerank = staging.restore == null && movesRank(paths);
  // Until the preview answers nobody knows the class; only a failed preview falls back to the
  // role, and never for a scoring change, which applies only with its re-rank shown.
  const waiting = !fresh && !preview.error;
  const canApply =
    !stale &&
    (fresh
      ? fresh.you_can_apply && fresh.blocked.length === 0
      : steer.owner && !waiting && !showRerank);
  const canPropose =
    !steer.owner && !waiting && !stale && (fresh?.you_can_propose ?? steer.canEdit);
  // An agent's proposal staged as it stands already waits for an owner: no second ask.
  const proposalWaits = intact && staging.proposal != null && !waiting && !canApply;
  const effects = (fresh?.effects ?? []).filter((e) => e.kind !== "restore");

  const clear = () => steer.setStaging(EMPTY_STEER);
  const undo = (to: number, from: number | null) =>
    pin.mutate(to, {
      onSuccess: () =>
        toast.show({
          message: `Undone. v${to} is in force again${from != null ? `; v${from} stays in history` : ""}.`,
        }),
      onError: (error) => toast.show({ message: `Could not undo: ${error.message}` }),
    });
  const failed = (error: unknown) => {
    const message =
      error instanceof FactoryApiError && error.code === "stale_definition"
        ? `${error.message}. Your staged change is kept: check it and apply again.`
        : controlErrorMessage(error);
    setFailure(message);
    void preview.refetch();
  };

  const apply = async (trial: boolean) => {
    setFailure(null);
    try {
      if (staging.restore != null) {
        const res = await restore.mutateAsync({ version: staging.restore, base_version: base });
        clear();
        toast.show({
          message: `Restored v${res.restored} as definition v${res.def_version}.`,
          undo: () => undo(version, res.def_version),
        });
        return;
      }
      if (intact && staging.proposal) {
        const res = await answer.mutateAsync({ ref: staging.proposal.ref, verb: "apply" });
        const to = res.proposal.def_version_to ?? null;
        clear();
        toast.show({
          message: `Applied ${staging.proposal.ref} as definition v${to ?? next}.`,
          undo: () => undo(version, to),
        });
        return;
      }
      const res = await edit.mutateAsync({
        changes,
        base_version: base,
        ...(staging.proposal
          ? { note: `Based on ${staging.proposal.ref} (${staging.proposal.by})` }
          : {}),
        ...(trial ? { trial: true } : {}),
      });
      clear();
      toast.show({
        message: `Applied as definition v${res.def_version ?? next}.${trial ? " It runs as a trial." : ""}`,
        undo: res.def_version != null ? () => undo(version, res.def_version) : undefined,
      });
    } catch (error) {
      failed(error);
    }
  };

  const sendProposal = async () => {
    setFailure(null);
    try {
      const res = await propose.mutateAsync({
        kind: changes.every((c) => c.path.startsWith("rules")) ? "rules" : "tuning",
        changes,
        rationale: rationale.trim(),
      });
      clear();
      setRationale("");
      setProposing(false);
      toast.show({ message: `Proposed as ${res.proposal.ref}. It waits for an owner.` });
    } catch (error) {
      failed(error);
    }
  };

  if (staging.edits.length === 0 && staging.restore == null) {
    return (
      <section
        data-testid="steer-staged-empty"
        aria-labelledby="steer-staged-h"
        className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4 max-md:p-3"
      >
        <h2 id="steer-staged-h" className="text-ui font-semibold">
          Staged change
        </h2>
        <p className="text-sm text-muted-foreground">
          {steer.canEdit
            ? "Nothing staged. Edit anything in the middle column: it turns violet here first, with its effect, who must approve and, for scoring changes, how the opportunities re-rank."
            : "You can look, not change. Ask an operator or an owner to change this."}
        </p>
        {latest.row && (
          <p
            data-testid="steer-undo-line"
            className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm"
          >
            <span className="min-w-0 flex-1">
              v{latest.row.version} is in force; undo it to put v{latest.row.parent} back.
            </span>
            <UndoLatestButton latest={latest} testId="steer-undo-column" />
          </p>
        )}
      </section>
    );
  }

  return (
    <section
      data-testid="steer-staged"
      aria-labelledby="steer-staged-h"
      className={cn(
        "flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-4 max-md:p-3",
        "border-status-violet",
      )}
    >
      <div className="flex items-center gap-2">
        <h2 id="steer-staged-h" className="flex-1 text-ui font-semibold">
          Staged for definition v{next}
        </h2>
        {steer.canEdit && (
          <Button
            size="xs"
            variant="ghost"
            onClick={clear}
            disabled={busy}
            data-testid="steer-discard-top"
            componentId="factory.steer.discard"
          >
            Discard
          </Button>
        )}
      </div>
      {staging.proposal && (
        <p data-testid="steer-staged-proposal" className="text-sm text-muted-foreground">
          {intact
            ? `From ${staging.proposal.ref} (${staging.proposal.by}). Apply answers it.`
            : `Based on ${staging.proposal.ref} (${staging.proposal.by}) and changed since: it applies as your change, and ${staging.proposal.ref} stays open until someone rejects it or it expires.`}
        </p>
      )}
      {staging.restore != null ? (
        <div data-testid="steer-staged-restore" className="flex min-w-0 flex-col gap-1.5">
          <div
            className={cn(
              "relative flex min-w-0 flex-col gap-1 rounded-lg border p-2.5 pr-8",
              MEANING.staged.border,
              MEANING.staged.surface,
            )}
          >
            {steer.canEdit && (
              <button
                type="button"
                aria-label={`Drop the restore of v${staging.restore}`}
                onClick={clear}
                className={cn(
                  "absolute top-2 right-2 cursor-pointer rounded p-0.5 text-muted-foreground hover:bg-muted",
                  FOCUS_RING,
                )}
              >
                <XIcon aria-hidden className="size-3.5" />
              </button>
            )}
            <span className="text-sm font-medium">Restore v{staging.restore}</span>
            <span className="text-xs text-foreground/80">
              Stores v{staging.restore}'s settings as v{next}; v{version} stays in the history and
              can be restored.
            </span>
            {preview.data?.lines.length ? (
              <ul className="font-mono text-xs break-words">
                {preview.data.lines.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            ) : preview.isFetching ? (
              <span aria-busy="true" className="h-8 animate-pulse rounded bg-muted/70" />
            ) : null}
          </div>
        </div>
      ) : (
        <ul className="flex min-w-0 flex-col gap-1.5">
          {staging.edits.map((e) => (
            <EditRow key={e.key} edit={e} before={inForceBefore(fresh?.diff, e)} names={names} />
          ))}
        </ul>
      )}
      {stale && (
        <div
          data-testid="steer-stale"
          role="status"
          className={cn(
            "flex min-w-0 flex-col gap-2 rounded-lg border p-2.5 text-sm",
            MEANING.needsYou.border,
            MEANING.needsYou.surface,
          )}
        >
          <p className="flex items-start gap-1.5">
            <AlertTriangleIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            <span className="min-w-0">
              v{version} came into force since you staged this on v{base}. Rebase to stage it on v
              {version}: the values before are read again, and edits v{version} already made drop.
            </span>
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="xs"
              variant="ghost"
              onClick={() => setReviewing(true)}
              data-testid="steer-stale-review"
              componentId="factory.steer.stale_review"
            >
              Review
            </Button>
            {steer.canEdit && (
              <Button
                size="xs"
                variant="outline"
                onClick={() => {
                  setFailure(null);
                  steer.rebase();
                }}
                data-testid="steer-rebase"
                componentId="factory.steer.rebase"
              >
                Rebase on v{version}
              </Button>
            )}
          </div>
          <CompareDialog
            from={reviewing ? base : null}
            to={version}
            onClose={() => setReviewing(false)}
            title={`What changed since v${base}`}
            description={`What v${version}, in force now, changed from v${base}, the version you staged on.`}
            testId="steer-stale-compare"
          />
        </div>
      )}
      {blocked.length > 0 && (
        <ul
          data-testid="steer-blocked"
          role="alert"
          className="flex flex-col gap-1 rounded-lg border border-status-red/55 bg-status-red/10 p-2 text-sm"
        >
          {blocked.map((b) => (
            <li key={`${b.path}:${b.code}`} className="flex items-start gap-1.5">
              <AlertTriangleIcon
                aria-hidden
                className="mt-0.5 size-3.5 shrink-0 text-status-red-fg"
              />
              <span className="min-w-0 break-words">{b.message}</span>
            </li>
          ))}
        </ul>
      )}
      {showRerank && (
        <div className="flex min-w-0 flex-col gap-1">
          <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            Effect on the ranking
          </h3>
          <RerankTable
            factoryId={steer.factoryId}
            rows={rerankRows(preview.data?.rerank)}
            discoveryOn={steer.discoveryOn}
            loading={preview.isFetching || !preview.settled}
          />
        </div>
      )}
      {effects.length > 0 && (
        <ul
          data-testid="steer-effects"
          className="flex flex-col gap-0.5 text-sm text-foreground/80"
        >
          {effects.map((e) => (
            <li key={`${e.kind}:${e.text}`}>{e.text}</li>
          ))}
        </ul>
      )}
      <WhoCanChange preview={fresh} owner={steer.owner} version={version} />
      {rule.code !== "no_change" && (
        <p
          data-testid="steer-trial-rule"
          data-code={rule.code}
          className="flex items-start gap-2 rounded-lg bg-muted/60 px-2.5 py-2 text-sm"
        >
          <FlaskConicalIcon
            aria-hidden
            className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
          />
          <span>
            <b className="font-semibold">{rule.head}</b> {rule.text}
          </span>
        </p>
      )}
      {preview.error && (
        <p data-testid="steer-preview-error" className="text-sm text-muted-foreground">
          The preview did not answer ({preview.error.message}).
          {showRerank && !fresh && " A scoring change applies only with its re-rank shown."}{" "}
          <button
            type="button"
            onClick={() => void preview.refetch()}
            className={cn("cursor-pointer underline", FOCUS_RING)}
          >
            Retry
          </button>
        </p>
      )}
      {failure && (
        <p role="alert" data-testid="steer-apply-error" className="text-sm text-destructive">
          Not applied: {failure}
        </p>
      )}
      {steer.canEdit &&
        (proposalWaits && !steer.owner ? (
          <div className="flex flex-col gap-2">
            <p data-testid="steer-proposal-waits" className="text-sm">
              {staging.proposal!.ref} already waits for an owner; an owner applies or rejects it.
              Change what is staged to propose your own version.
            </p>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={clear}
                disabled={busy}
                data-testid="steer-discard"
                componentId="factory.steer.discard"
              >
                Discard
              </Button>
            </div>
          </div>
        ) : canPropose && !canApply ? (
          proposing ? (
            <div className="flex flex-col gap-2">
              <label htmlFor="steer-rationale" className="text-sm">
                Why (the owners read this)
              </label>
              <Textarea
                id="steer-rationale"
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                className="min-h-16 text-sm"
                data-testid="steer-rationale"
              />
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setProposing(false)}
                  componentId="factory.steer.propose_cancel"
                >
                  Cancel
                </Button>
                <span className="flex-1" />
                <Button
                  size="sm"
                  onClick={() => void sendProposal()}
                  loading={propose.isPending}
                  disabled={!rationale.trim() || staging.restore != null}
                  data-testid="steer-propose-send"
                  componentId="factory.steer.propose_send"
                >
                  Propose to owners
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={clear}
                disabled={busy}
                componentId="factory.steer.discard"
              >
                Discard
              </Button>
              <span className="flex-1" />
              <Button
                size="sm"
                onClick={() => setProposing(true)}
                disabled={staging.restore != null}
                data-testid="steer-propose"
                componentId="factory.steer.propose"
              >
                Propose to owners
              </Button>
            </div>
          )
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={clear}
              disabled={busy}
              data-testid="steer-discard"
              componentId="factory.steer.discard"
            >
              Discard
            </Button>
            <span className="flex-1" />
            {fresh && rule.ok && !intact && staging.restore == null && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => void apply(true)}
                disabled={!canApply || busy}
                data-testid="steer-apply-trial"
                componentId="factory.steer.apply_trial"
              >
                Apply as a trial
              </Button>
            )}
            <Button
              size="sm"
              onClick={() => void apply(false)}
              disabled={!canApply || busy}
              loading={edit.isPending || restore.isPending || answer.isPending}
              data-testid="steer-apply"
              componentId="factory.steer.apply"
              className="bg-status-violet font-semibold text-[#160f2b] hover:bg-status-violet/85"
            >
              {intact && staging.proposal
                ? `Apply ${staging.proposal.ref} as v${next}`
                : `Apply as v${next}`}
            </Button>
          </div>
        ))}
    </section>
  );
}

export const ChangeColumn = forwardRef<
  HTMLElement,
  {
    preview: Preview;
    proposals: readonly ProposalView[] | undefined;
    proposalsLoading: boolean;
    className?: string;
  }
>(function ChangeColumn({ preview, proposals, proposalsLoading, className }, ref) {
  return (
    <aside
      ref={ref}
      aria-label="Staged change"
      data-testid="steer-change-column"
      className={cn("flex min-w-0 flex-col gap-3", className)}
    >
      <StagedCard preview={preview} />
      <AgentProposals proposals={proposals} loading={proposalsLoading} />
      <RunningTrials />
    </aside>
  );
});
