/**
 * Undo of the version in force for its whole window (10 minutes), not only
 * while the Apply toast shows: it re-pins the version it was made from. Owners
 * undo any version; an editor only one they made (the server's rule).
 */

import { Undo2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDefinitionVersions, usePinDefinition } from "../insights/learnQueries";
import { useFactoryToast } from "../ui/Toast";
import { useSteer } from "./SteerContext";
import { type VersionRow, undoLabel, undoLeft, versionRows } from "./steerModel";

export interface UndoLatest {
  /** The version in force, when it can still be undone. */
  row: VersionRow | null;
  /** Seconds left to undo it. */
  left: number | null;
  undo: () => void;
  pending: boolean;
}

/** The Undo of the version in force, while the viewer may still undo it. */
export function useUndoLatest(): UndoLatest {
  const steer = useSteer();
  const toast = useFactoryToast();
  const versions = useDefinitionVersions(steer.factoryId);
  const pin = usePinDefinition(steer.factoryId);
  const row = versionRows(versions.data?.data ?? []).find((r) => r.pinned) ?? null;
  const left = steer.canEdit
    ? undoLeft(row, steer.nowS, { userId: steer.snapshot.viewer?.user_id, owner: steer.owner })
    : null;
  const undo = () => {
    if (!row || row.parent == null) return;
    const to = row.parent;
    pin.mutate(to, {
      onSuccess: () =>
        toast.show({
          message: `Undone. v${to} is in force again; v${row.version} stays in history.`,
        }),
      onError: (error) => toast.show({ message: `Could not undo: ${error.message}` }),
    });
  };
  return { row: left != null ? row : null, left, undo, pending: pin.isPending };
}

/** "Undo (8 min left)" for the version in force; nothing once the window closed. */
export function UndoLatestButton({
  latest,
  testId = "steer-undo-latest",
}: {
  latest: UndoLatest;
  testId?: string;
}) {
  if (latest.left == null || !latest.row) return null;
  return (
    <Button
      size="xs"
      variant="outline"
      onClick={latest.undo}
      loading={latest.pending}
      data-testid={testId}
      componentId="factory.steer.undo_latest"
    >
      <Undo2Icon />
      {undoLabel(latest.left)}
    </Button>
  );
}
