/**
 * The cockpit's confirm dialogs: pause (with what it does and the "also tear
 * down previews" choice), type-to-confirm stops, a restart note and the
 * relaunch-with-spec diff. Every confirm button is `control-confirm`.
 */

import { type ReactNode, useState } from "react";
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
import { Textarea } from "@/components/ui/textarea";
import type { LaunchDriftView } from "@/lib/factoryApi";

/** Start a confirm dialog on its confirm button rather than its first option. */
function focusConfirm(event: Event): void {
  const content = event.currentTarget;
  const confirm =
    content instanceof HTMLElement
      ? content.querySelector<HTMLElement>('[data-testid="control-confirm"]')
      : null;
  if (!confirm) return;
  event.preventDefault();
  confirm.focus();
}

interface BaseProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function Consequences({ items }: { items: string[] }) {
  return (
    <ul data-testid="control-consequences" className="flex list-disc flex-col gap-1 pl-5 text-sm">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

interface PauseDialogProps extends BaseProps {
  scope: "project" | "factory";
  /** The project title or the factory name. */
  name: string;
  /** Previews live right now (kept up unless torn down). */
  livePreviews: number;
  onConfirm: (choice: { teardown: boolean; note: string }) => void;
}

// Dialog content unmounts on close, so each body starts from fresh inputs.
function PauseBody({ onOpenChange, scope, name, livePreviews, onConfirm }: PauseDialogProps) {
  const [teardown, setTeardown] = useState(false);
  const [note, setNote] = useState("");
  const previews =
    livePreviews === 0
      ? "No factory previews are up right now."
      : teardown
        ? `${livePreviews} factory ${livePreviews === 1 ? "preview is" : "previews are"} torn down; demos stay in the cockpit.`
        : `${livePreviews} factory ${livePreviews === 1 ? "preview stays" : "previews stay"} up for review; ${scope === "project" ? "the PL’s pod is" : "PL pods are"} kept alive for ${livePreviews === 1 ? "it" : "them"}.`;
  const items =
    scope === "project"
      ? [
          "The PL stops dispatching and cancels running ICs.",
          "The dev env goes down.",
          previews,
          "Open PRs stay open; resume picks up from the PL’s plan.",
        ]
      : [
          "Every PL pauses first, then Sentinel, then Atlas.",
          "Dev envs go down; no new work is dispatched.",
          previews,
          "Open PRs stay open; Resume all wakes them in reverse order.",
        ];
  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {scope === "project" ? `Pause ${name}?` : `Pause all of ${name}?`}
        </DialogTitle>
        <DialogDescription>
          Agents finish their current step, then wait for Resume.
        </DialogDescription>
      </DialogHeader>
      <Consequences items={items} />
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={teardown}
          data-testid="control-teardown"
          onChange={(event) => setTeardown(event.target.checked)}
          className="mt-1 size-3.5 shrink-0 cursor-pointer rounded-sm accent-primary outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        />
        <span>
          Also tear down previews
          <span className="block text-muted-foreground">
            Frees their namespaces; reviewers lose the live app until the next deploy.
          </span>
        </span>
      </label>
      <Textarea
        value={note}
        placeholder="Note for the agents (optional)"
        aria-label="Note"
        data-testid="control-note"
        onChange={(event) => setNote(event.target.value)}
        className="min-h-12 text-sm"
      />
      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button
          data-testid="control-confirm"
          onClick={() => {
            onConfirm({ teardown, note: note.trim() });
            onOpenChange(false);
          }}
        >
          {scope === "project" ? "Pause project" : "Pause all"}
        </Button>
      </DialogFooter>
    </>
  );
}

export function PauseDialog(props: PauseDialogProps) {
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent
        data-testid="control-pause-dialog"
        className="sm:max-w-md"
        onOpenAutoFocus={focusConfirm}
      >
        <PauseBody {...props} />
      </DialogContent>
    </Dialog>
  );
}

interface TypeToConfirmProps extends BaseProps {
  title: string;
  description: ReactNode;
  consequences: string[];
  /** What must be typed (a slug or the factory name). */
  expected: string;
  confirmLabel: string;
  onConfirm: () => void;
}

function TypeToConfirmBody({
  onOpenChange,
  title,
  description,
  consequences,
  expected,
  confirmLabel,
  onConfirm,
}: TypeToConfirmProps) {
  const [typed, setTyped] = useState("");
  const matches = typed.trim() === expected;
  const confirm = () => {
    onConfirm();
    onOpenChange(false);
  };
  return (
    <>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>
      <Consequences items={consequences} />
      <label className="flex flex-col gap-1 text-sm">
        <span>
          Type <span className="rounded bg-muted px-1 font-mono">{expected}</span> to confirm
        </span>
        <Input
          value={typed}
          autoComplete="off"
          spellCheck={false}
          data-testid="control-confirm-input"
          onChange={(event) => setTyped(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && matches) confirm();
          }}
          className="font-mono text-sm"
        />
      </label>
      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button
          variant="destructive"
          disabled={!matches}
          data-testid="control-confirm"
          onClick={confirm}
        >
          {confirmLabel}
        </Button>
      </DialogFooter>
    </>
  );
}

/** A destructive action the human confirms by typing `expected`. */
export function TypeToConfirmDialog(props: TypeToConfirmProps) {
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent data-testid="control-stop-dialog" className="sm:max-w-md">
        <TypeToConfirmBody {...props} />
      </DialogContent>
    </Dialog>
  );
}

interface RestartDialogProps extends BaseProps {
  who: string;
  onConfirm: (note: string) => void;
}

function RestartBody({ onOpenChange, who, onConfirm }: RestartDialogProps) {
  const [note, setNote] = useState("");
  return (
    <>
      <DialogHeader>
        <DialogTitle>Restart {who}?</DialogTitle>
        <DialogDescription>
          Its turn is stopped and it starts again on the same sandbox, re-reading its plan.
        </DialogDescription>
      </DialogHeader>
      <Textarea
        value={note}
        placeholder="Why, or what to do first (optional)"
        aria-label="Note"
        data-testid="control-note"
        onChange={(event) => setNote(event.target.value)}
        className="min-h-16 text-sm"
      />
      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button
          data-testid="control-confirm"
          onClick={() => {
            onConfirm(note.trim());
            onOpenChange(false);
          }}
        >
          Restart
        </Button>
      </DialogFooter>
    </>
  );
}

/** Restart a PL with an optional note it reads first. */
export function RestartDialog(props: RestartDialogProps) {
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent data-testid="control-restart-dialog" className="sm:max-w-md">
        <RestartBody {...props} />
      </DialogContent>
    </Dialog>
  );
}

/** The stored launch args beside the spec's, field by field. */
export function DriftTable({ drift }: { drift: LaunchDriftView }) {
  return (
    <table data-testid="control-drift-table" className="w-full text-sm">
      <thead>
        <tr className="text-left text-muted-foreground">
          <th className="py-1 pr-3 font-medium">Field</th>
          <th className="py-1 pr-3 font-medium">Launched with</th>
          <th className="py-1 font-medium">Spec says</th>
        </tr>
      </thead>
      <tbody>
        {drift.fields.map((field) => (
          <tr key={field} className="border-t border-border">
            <td className="py-1 pr-3">{field.replaceAll("_", " ")}</td>
            <td className="py-1 pr-3 font-mono text-warning">{drift.stored[field] ?? "—"}</td>
            <td className="py-1 font-mono">{drift.spec[field] ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Relaunch a lead or PL with its spec's launch args (fixes an old launch mode). */
export function RelaunchDialog({
  open,
  onOpenChange,
  who,
  drift,
  onConfirm,
}: BaseProps & { who: string; drift: LaunchDriftView | null; onConfirm: () => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="control-relaunch-dialog"
        className="sm:max-w-md"
        onOpenAutoFocus={focusConfirm}
      >
        <DialogHeader>
          <DialogTitle>Relaunch {who} with its spec’s launch mode?</DialogTitle>
          <DialogDescription>
            Its turn is stopped and it restarts on the same sandbox with the launch args its agent
            spec declares now.
          </DialogDescription>
        </DialogHeader>
        {drift ? (
          <DriftTable drift={drift} />
        ) : (
          <p className="text-sm text-muted-foreground">
            Its launch args already match the spec; relaunching only restarts it.
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            data-testid="control-confirm"
            onClick={() => {
              onConfirm();
              onOpenChange(false);
            }}
          >
            Relaunch
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
