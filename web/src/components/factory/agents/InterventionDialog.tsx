/**
 * The confirmation for an intervention on one or more agents: what it does,
 * whom it reaches (and who is skipped), the vendor for a reassign, and the
 * message or note, posted as the viewer with the PL notified. `useIntervene`
 * sends the requests; every confirm button is `intervention-confirm`.
 */

import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { showToast } from "@/components/ui/toast";
import { factoryQueryKey } from "@/hooks/useFactory";
import { type FactoryControlRequest, sendFactoryControl } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { controlErrorMessage } from "../controls/controlModel";
import { plural } from "../factoryFormat";
import { useViewer } from "../shell/viewerCan";
import {
  INTERVENTION_LABEL,
  type InterventionKind,
  type InterventionTarget,
  VENDORS,
  type Vendor,
  canConfirm,
  consequences,
  otherVendor,
  planIntervention,
} from "./interventionModel";

export interface InterventionResult {
  sent: number;
  failed: { label: string; error: string }[];
}

/**
 * Send planned control requests one by one (each is its own human-origin
 * call and PL notice), then refresh the factory and say what happened.
 */
export function useIntervene(factoryId: string) {
  const queryClient = useQueryClient();
  return useCallback(
    async (
      kind: InterventionKind,
      requests: FactoryControlRequest[],
      labels: string[],
    ): Promise<InterventionResult> => {
      const result: InterventionResult = { sent: 0, failed: [] };
      /* oxlint-disable no-await-in-loop -- one notice per target, in order */
      for (const [i, request] of requests.entries()) {
        try {
          await sendFactoryControl(factoryId, request);
          result.sent += 1;
        } catch (error) {
          result.failed.push({
            label: labels[i] ?? request.target,
            error: controlErrorMessage(error),
          });
        }
      }
      /* oxlint-enable no-await-in-loop */
      void queryClient.invalidateQueries({ queryKey: factoryQueryKey(factoryId) });
      void queryClient.invalidateQueries({ queryKey: ["factory-events", factoryId] });
      const verb = INTERVENTION_LABEL[kind];
      if (result.failed.length === 0) {
        showToast(
          `${verb}: sent to ${requests.length === 1 ? labels[0] : plural(requests.length, "target")} · the PL is notified`,
        );
      } else {
        showToast(
          `${verb}: ${result.sent} sent, ${result.failed.length} refused (${result.failed[0]!.label}: ${result.failed[0]!.error})`,
        );
      }
      return result;
    },
    [factoryId, queryClient],
  );
}

/** `Mia · operator`: who the message is posted as. */
export function postingAs(viewer: ReturnType<typeof useViewer>): string {
  if (!viewer?.user_id) return "you";
  const name = viewer.user_id.split("@")[0] ?? viewer.user_id;
  const label = name.charAt(0).toUpperCase() + name.slice(1);
  return `${label} · ${viewer.role === "creator" ? "owner" : viewer.role}`;
}

interface DialogProps {
  factoryId: string;
  kind: InterventionKind | null;
  targets: InterventionTarget[];
  onOpenChange: (open: boolean) => void;
  /** After the requests went out (successfully or not). */
  onDone?: (result: InterventionResult) => void;
}

function Body({
  factoryId,
  kind,
  targets,
  onOpenChange,
  onDone,
}: DialogProps & { kind: InterventionKind }) {
  const viewer = useViewer();
  const intervene = useIntervene(factoryId);
  const [note, setNote] = useState("");
  const [vendor, setVendor] = useState<Vendor>(() => otherVendor(targets[0]?.harness));
  const [sending, setSending] = useState(false);
  const plan = planIntervention(kind, targets, { note, vendor });
  const ok = canConfirm(kind, plan, note);
  const one = plan.labels.length === 1;
  const subject = one
    ? plan.labels[0]
    : plural(plan.labels.length, kind === "message" || kind === "interrupt" ? "agent" : "task");
  const title =
    kind === "message"
      ? `Message ${subject}`
      : kind === "reassign"
        ? `Reassign ${subject}?`
        : `${INTERVENTION_LABEL[kind]} ${subject}?`;
  const confirm = async () => {
    setSending(true);
    const result = await intervene(kind, plan.requests, plan.labels);
    setSending(false);
    onDone?.(result);
    if (result.failed.length === 0) onOpenChange(false);
  };
  return (
    <>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>
          Every intervention is posted as you and notifies the PL.
        </DialogDescription>
      </DialogHeader>
      <ul
        data-testid="intervention-consequences"
        className="flex list-disc flex-col gap-1 pl-5 text-sm"
      >
        {consequences(kind, vendor).map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <div className="flex min-w-0 flex-col gap-1.5">
        <span className="text-sm text-muted-foreground">
          {kind === "message" || kind === "interrupt" ? "Agents" : "Tasks"}
        </span>
        <div
          data-testid="intervention-targets"
          className="flex max-h-28 min-w-0 flex-wrap gap-1 overflow-y-auto"
        >
          {plan.labels.map((label) => (
            <span
              key={label}
              className="inline-flex h-6 max-w-full items-center truncate rounded-md bg-muted px-2 font-mono text-xs"
            >
              {label}
            </span>
          ))}
          {plan.skipped.map((skip) => (
            <span
              key={skip.title}
              title={skip.reason}
              data-testid="intervention-skipped"
              className="inline-flex h-6 max-w-full items-center truncate rounded-md border border-dashed border-border px-2 font-mono text-xs text-muted-foreground line-through"
            >
              {skip.title}
            </span>
          ))}
        </div>
        {plan.skipped.length > 0 && (
          <span className="text-xs text-muted-foreground">
            {plural(plan.skipped.length, "agent")} skipped: no task to {kind}.
          </span>
        )}
      </div>
      {kind === "reassign" && (
        <fieldset className="flex flex-col gap-1.5">
          <legend className="mb-1.5 text-sm text-muted-foreground">Switch to</legend>
          <div role="radiogroup" className="inline-flex w-fit rounded-lg bg-muted p-[3px]">
            {VENDORS.map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={vendor === option.value}
                data-testid={`intervention-vendor-${option.value}`}
                onClick={() => setVendor(option.value)}
                className={cn(
                  "h-7 cursor-pointer rounded-md px-2.5 text-sm font-medium focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
                  vendor === option.value
                    ? "bg-background text-foreground shadow-sm dark:bg-input/30"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
        </fieldset>
      )}
      <label className="flex flex-col gap-1.5 text-sm">
        <span className="text-muted-foreground">
          {kind === "message" ? "Message" : "Note for the PL (optional)"}
          <span className="ml-1 text-xs">· posting as {postingAs(viewer)}</span>
        </span>
        <Textarea
          value={note}
          autoFocus={kind === "message"}
          placeholder={
            kind === "message"
              ? "What should they do next? Delivered after the current tool."
              : "Why, or what to try instead"
          }
          aria-label={kind === "message" ? "Message" : "Note"}
          data-testid="intervention-note"
          onChange={(event) => setNote(event.target.value)}
          className="min-h-16 text-sm"
        />
      </label>
      <DialogFooter>
        <Button
          variant="outline"
          onClick={() => onOpenChange(false)}
          componentId="factory.intervene.cancel"
        >
          Cancel
        </Button>
        <Button
          data-testid="intervention-confirm"
          componentId={`factory.intervene.${kind}`}
          variant={kind === "interrupt" ? "destructive" : "default"}
          disabled={!ok}
          loading={sending}
          onClick={() => void confirm()}
        >
          {kind === "message" ? "Send" : INTERVENTION_LABEL[kind]}
          {plan.requests.length > 1 ? ` ${plan.requests.length}` : ""}
        </Button>
      </DialogFooter>
    </>
  );
}

/** The confirmation of `kind` on `targets`; closed while `kind` is null. */
export function InterventionDialog(props: DialogProps) {
  const { kind, onOpenChange } = props;
  return (
    <Dialog open={kind != null} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="intervention-dialog"
        data-kind={kind ?? undefined}
        className="sm:max-w-md"
      >
        {kind && <Body {...props} kind={kind} />}
      </DialogContent>
    </Dialog>
  );
}
