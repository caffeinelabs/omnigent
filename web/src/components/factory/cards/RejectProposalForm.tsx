/**
 * The one way to reject (or dismiss) a proposal, on every card that offers
 * it: an optional reason the proposer reads, then "Reject C-n" or Cancel.
 * The note box keeps its height as it fills, so the buttons stay put.
 */

import { type KeyboardEvent, useState } from "react";
import { Loader2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { NoteTextarea } from "../formParts";

export const REJECT_NOTE_PLACEHOLDER = "Why not? The proposer reads this (optional)";
/** The server's cap on a reject note (`RejectBody.note`). */
export const REJECT_NOTE_MAX = 2000;

export interface RejectProposalFormProps {
  /** `C-4`. */
  proposalRef: string;
  /** Dismiss: a code change recorded while the Smith lane is off. */
  verb?: "Reject" | "Dismiss";
  busy: boolean;
  /** The trimmed note, or undefined when none was written. */
  onConfirm: (note: string | undefined) => void;
  onCancel: () => void;
}

export function RejectProposalForm({
  proposalRef,
  verb = "Reject",
  busy,
  onConfirm,
  onCancel,
}: RejectProposalFormProps) {
  const [note, setNote] = useState("");
  const confirm = () => {
    if (!busy) onConfirm(note.trim() || undefined);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      confirm();
    }
  };
  return (
    <div className="flex flex-col gap-1.5" data-testid={`proposal-reject-form-${proposalRef}`}>
      <NoteTextarea
        value={note}
        onChange={(event) => setNote(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={REJECT_NOTE_PLACEHOLDER}
        maxLength={REJECT_NOTE_MAX}
        aria-label={`Reason to ${verb.toLowerCase()} ${proposalRef}`}
        data-testid={`proposal-reject-note-${proposalRef}`}
        componentId="factory.proposal.reject_note"
        className="bg-background"
        autoFocus
      />
      <div className="flex items-center gap-1.5">
        <Button
          size="sm"
          variant="destructive"
          onClick={confirm}
          disabled={busy}
          data-testid={`proposal-reject-confirm-${proposalRef}`}
          componentId="factory.proposal.reject_confirm"
        >
          {busy && <Loader2Icon className="animate-spin" />}
          {verb} {proposalRef}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={onCancel}
          disabled={busy}
          data-testid={`proposal-reject-cancel-${proposalRef}`}
          componentId="factory.proposal.reject_cancel"
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
