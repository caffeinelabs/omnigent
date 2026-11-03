/**
 * Kill an opportunity with a reason from the seeded list (and a note), so the
 * factory learns why. It applies at once with Undo; an opportunity with a
 * running bet can only be killed by an owner (the server says so).
 */

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { FOCUS_RING } from "../ui/statusTokens";
import { KILL_REASONS, KILL_REASON_WORD, type KillReason } from "./discoveryModel";

export function KillDialog({
  target,
  onKill,
  onClose,
}: {
  /** The opportunity to kill; null keeps the dialog closed. */
  target: { id: string; title: string } | null;
  onKill: (reason: KillReason, note: string) => void;
  onClose: () => void;
}) {
  const [reason, setReason] = useState<KillReason | null>(null);
  const [note, setNote] = useState("");
  const close = () => {
    setReason(null);
    setNote("");
    onClose();
  };
  return (
    <Dialog open={target != null} onOpenChange={(open) => !open && close()}>
      <DialogContent data-testid="discovery-kill-dialog" className="max-w-md">
        <DialogHeader>
          <DialogTitle>Kill {target?.id}</DialogTitle>
          <DialogDescription>
            {target?.title}. Pick why, so the factory learns from it. You can undo for 10 minutes,
            and revive it later.
          </DialogDescription>
        </DialogHeader>
        <fieldset className="flex flex-col gap-1">
          <legend className="sr-only">Why kill it</legend>
          {KILL_REASONS.map((r) => (
            <label
              key={r}
              className={cn(
                "flex cursor-pointer items-center gap-2 rounded-md border border-border px-2.5 py-1.5 text-sm hover:bg-muted",
                reason === r && "border-status-blue bg-status-blue/10",
              )}
            >
              <input
                type="radio"
                name="kill-reason"
                value={r}
                checked={reason === r}
                onChange={() => setReason(r)}
                className={FOCUS_RING}
                data-testid={`discovery-kill-reason-${r}`}
              />
              {KILL_REASON_WORD[r]}
            </label>
          ))}
        </fieldset>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted-foreground">Note (optional)</span>
          <Input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={300}
            data-testid="discovery-kill-note"
          />
        </label>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={reason == null}
            data-testid="discovery-kill-confirm"
            onClick={() => {
              if (!reason) return;
              onKill(reason, note);
              close();
            }}
          >
            Kill {target?.id}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
