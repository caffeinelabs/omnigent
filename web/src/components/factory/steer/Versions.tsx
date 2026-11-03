/**
 * Steer › Versions and trials: every definition version (who, the change
 * that made it, when, its class, whether it ran as a trial, whether it was
 * undone or restores an older one), newest first. The version in force offers
 * Undo for 10 minutes after it was made. Compare shows a version against the
 * one in force; Restore stages that version as the next one, with its diff in
 * the change column (applying creates a version you can undo). Then the agent
 * bundles members run, and "Ask Sentinel for a retro now".
 */

import { HistoryIcon, RotateCcwIcon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { DefinitionView, ProposalView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { formatElapsed } from "../factoryFormat";
import { steerPageHref } from "../factoryRoutes";
import { BundlesCard } from "../improvements/DefinitionPanel";
import { useDefinitionDiff, useRequestRetro } from "../insights/learnQueries";
import { SectionCard } from "../project/projectParts";
import { useFactoryToast } from "../ui/Toast";
import { useSteer } from "./SteerContext";
import { STAGED_CONTROL } from "./steerFields";
import { stageRestore, stagedCount, versionRows } from "./steerModel";
import { UndoLatestButton, useUndoLatest } from "./UndoLatest";

/** Two versions' differences, in the server's words. */
export function CompareDialog({
  from,
  to,
  onClose,
  title,
  description,
  testId = "steer-compare",
}: {
  from: number | null;
  to: number;
  onClose: () => void;
  title?: string;
  description?: string;
  testId?: string;
}) {
  const steer = useSteer();
  const diff = useDefinitionDiff(steer.factoryId, from, to);
  return (
    <Dialog open={from != null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-xl" data-testid={testId}>
        <DialogHeader>
          <DialogTitle>{title ?? `v${from} against v${to} (in force)`}</DialogTitle>
          <DialogDescription>
            {description ?? `What restoring v${from} would change in the version in force.`}
          </DialogDescription>
        </DialogHeader>
        {diff.isLoading ? (
          <div aria-busy="true" className="h-24 animate-pulse rounded-md bg-muted/70" />
        ) : diff.error ? (
          <p className="text-sm text-muted-foreground">Could not compare: {diff.error.message}</p>
        ) : diff.data?.lines.length ? (
          <ul className="flex max-h-80 flex-col gap-0.5 overflow-y-auto font-mono text-xs break-words">
            {diff.data.lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">They say the same.</p>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function Versions({
  versions,
  proposals,
  loading,
}: {
  versions: readonly DefinitionView[] | undefined;
  proposals: readonly ProposalView[] | undefined;
  loading: boolean;
}) {
  const steer = useSteer();
  const toast = useFactoryToast();
  const retro = useRequestRetro(steer.factoryId);
  const latest = useUndoLatest();
  const [compare, setCompare] = useState<number | null>(null);
  const rows = versionRows(versions ?? [], proposals ?? []);
  const pinned = rows.find((r) => r.pinned)?.version ?? steer.definition.def_version;
  const restore = (version: number) => {
    const before = steer.staging;
    steer.setStaging(stageRestore(version));
    toast.show({
      message:
        stagedCount(before) > 0
          ? `Staged a restore of v${version}; what was staged before is set aside.`
          : `Staged a restore of v${version}. Its diff is in the change column.`,
      undo: () => steer.setStaging(before),
    });
  };
  const askRetro = () =>
    retro.mutate(undefined, {
      onSuccess: (res) =>
        toast.show({
          message: res.sent ? "Sentinel got a retro request." : "A retro already ran this hour.",
        }),
      onError: (error) => toast.show({ message: `Not sent: ${error.message}` }),
    });
  return (
    <div data-testid="steer-versions" className="flex min-w-0 flex-col gap-4">
      <SectionCard
        title="Versions"
        meta="immutable; restoring creates a new version you can undo"
        testId="steer-version-list"
      >
        {loading && rows.length === 0 ? (
          <div aria-busy="true" className="h-40 animate-pulse rounded-md bg-muted/70" />
        ) : (
          <ul className="flex min-w-0 flex-col divide-y divide-border/60">
            {rows.map((v) => {
              const restoring = steer.staging.restore === v.version;
              return (
                <li
                  key={v.version}
                  data-testid={`steer-version-v${v.version}`}
                  data-pinned={v.pinned ? "true" : undefined}
                  className={cn(
                    "flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 py-2",
                    restoring && cn("rounded-md px-2", STAGED_CONTROL),
                  )}
                >
                  <span className="w-10 shrink-0 font-mono text-sm font-medium">v{v.version}</span>
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="text-sm">
                      {v.via === "restore" && v.restoredFrom != null
                        ? `restores v${v.restoredFrom}`
                        : v.via}{" "}
                      by {v.by}
                      {v.ref && (
                        <Link
                          to={steerPageHref(steer.factoryId, "versions", { peek: v.ref })}
                          componentId="factory.steer.version_ref"
                          className="ml-1.5 font-mono text-xs text-muted-foreground hover:text-foreground hover:underline"
                        >
                          {v.ref}
                        </Link>
                      )}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {[
                        `${formatElapsed(steer.nowS - v.at) ?? "now"} ago`,
                        v.klass ? `class ${v.klass}` : null,
                        v.trial,
                        v.undone ? `undone by ${v.undone}` : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </span>
                  {v.pinned ? (
                    <span className="flex shrink-0 items-center gap-1.5">
                      {latest.row?.version === v.version && <UndoLatestButton latest={latest} />}
                      <span className="rounded-md bg-muted px-1.5 text-xs leading-5">in force</span>
                    </span>
                  ) : (
                    <span className="flex shrink-0 items-center gap-1">
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() => setCompare(v.version)}
                        data-testid={`steer-compare-v${v.version}`}
                        componentId="factory.steer.compare"
                      >
                        Compare
                      </Button>
                      {steer.owner && (
                        <Button
                          size="xs"
                          variant="outline"
                          onClick={() => restore(v.version)}
                          disabled={restoring}
                          data-testid={`steer-restore-v${v.version}`}
                          componentId="factory.steer.restore"
                        >
                          <RotateCcwIcon />
                          {restoring ? "Staged" : "Restore"}
                        </Button>
                      )}
                    </span>
                  )}
                </li>
              );
            })}
            {rows.length === 0 && (
              <li className="py-2 text-sm text-muted-foreground">No versions yet.</li>
            )}
          </ul>
        )}
      </SectionCard>
      <BundlesCard factoryId={steer.factoryId} />
      {steer.owner && (
        <SectionCard
          title="Retro"
          meta="Sentinel reviews findings and proposes changes"
          testId="steer-retro"
        >
          <span>
            <Button
              size="sm"
              variant="outline"
              onClick={askRetro}
              loading={retro.isPending}
              componentId="factory.steer.retro"
            >
              <HistoryIcon />
              Ask Sentinel for a retro now
            </Button>
          </span>
        </SectionCard>
      )}
      <CompareDialog from={compare} to={pinned} onClose={() => setCompare(null)} />
    </div>
  );
}
