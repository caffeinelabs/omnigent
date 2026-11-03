/**
 * Needs you's FYI lines: what the factory did on its own that a person can
 * still undo (a project change an autonomy level let through, or one that
 * carried out a person's answer). Each line says what changed and why it
 * applied at once, links its proposal and offers Undo while the window is
 * open. They need nobody, so they never count in Needs you.
 */

import { Loader2Icon, SparklesIcon, Undo2Icon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { Fyi } from "@/lib/factoryAsksApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { countdownText } from "../cards/proposalModel";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";

/** The FYI lines still open at *nowS*, newest first. */
export function openFyi(fyi: readonly Fyi[] | undefined, nowS: number): Fyi[] {
  return [...(fyi ?? [])]
    .filter((item) => item.undo_until > nowS)
    .sort((a, b) => b.since - a.since);
}

function FyiLine({
  item,
  nowS,
  onUndo,
}: {
  item: Fyi;
  nowS: number;
  onUndo?: (ref: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const left = item.undo_until - nowS;
  const undo = async () => {
    if (!onUndo || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onUndo(item.ref);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <li data-testid={`fyi-${item.ref}`} className="flex flex-wrap items-start gap-x-2 gap-y-1">
      <p className="min-w-0 grow text-sm">
        <Link
          to={item.href}
          componentId="factory.home.fyi.ref"
          className={cn(
            "font-mono text-xs text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground",
            FOCUS_RING,
          )}
        >
          {item.ref}
        </Link>{" "}
        <span className="text-foreground">{item.title}</span>
        <span className="text-muted-foreground"> · applied at once, {item.why}</span>
        {item.note && (
          <span data-testid={`fyi-${item.ref}-note`} className="block text-muted-foreground">
            {item.note}
          </span>
        )}
        {error && (
          <span data-testid={`fyi-${item.ref}-error`} className="block text-destructive">
            Couldn’t undo: {error}
          </span>
        )}
      </p>
      {item.can_undo && onUndo && (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => void undo()}
          disabled={busy}
          data-testid={`fyi-${item.ref}-undo`}
          componentId="factory.home.fyi.undo"
          className="shrink-0"
        >
          {busy ? <Loader2Icon className="animate-spin" /> : <Undo2Icon />}
          Undo
          <span className="font-mono text-xs text-muted-foreground tabular-nums">
            {countdownText(left)}
          </span>
        </Button>
      )}
    </li>
  );
}

export function FyiLines({
  fyi,
  nowS,
  onUndo,
}: {
  fyi: readonly Fyi[] | undefined;
  nowS: number;
  onUndo?: (ref: string) => Promise<void>;
}) {
  const open = openFyi(fyi, nowS);
  if (open.length === 0) return null;
  return (
    <div
      data-testid="home-fyi"
      className="flex gap-2 border-t border-border px-4 py-2.5 text-sm text-muted-foreground"
    >
      <SparklesIcon aria-hidden className={cn("mt-1 size-3.5 shrink-0", MEANING.working.text)} />
      <div className="min-w-0 grow">
        <p className="font-medium text-foreground">FYI: done on its own, yours to undo</p>
        <ul className="mt-1 flex list-none flex-col gap-1.5 p-0">
          {open.map((item) => (
            <FyiLine key={item.id} item={item} nowS={nowS} onUndo={onUndo} />
          ))}
        </ul>
      </div>
    </div>
  );
}
