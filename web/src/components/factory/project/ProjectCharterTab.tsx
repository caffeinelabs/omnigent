/**
 * The project's Charter tab (new cockpit): what users get, done when, scope,
 * constraints, previews and demo focus, builders at once, dependencies, and
 * the merge policy and flag convention from the product profile. Editing
 * opens the charter form (change, review the diff, send); every change is a
 * version: the toast offers Undo for 10 minutes, and each version lists what
 * it changed (before → after) with Undo while open and Revert after.
 */

import { HistoryIcon, PencilIcon, Undo2Icon } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { factoryQueryKey } from "@/hooks/useFactory";
import type { CharterPatchResponse, FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import {
  type CharterFieldChange,
  type CharterVersion,
  revertCharterVersion,
} from "@/lib/factoryProjectsApi";
import { undoProposal } from "@/lib/factoryRoomApi";
import { cn } from "@/lib/utils";
import { controlErrorMessage } from "../controls/controlModel";
import { EditCharterDialog } from "../controls/EditCharterDialog";
import { formatElapsed } from "../factoryFormat";
import { useViewerCan } from "../shell/viewerCan";
import { useFactoryToast } from "../ui/Toast";
import { EmptyState, SectionCard } from "./projectParts";
import { useCharterVersions, useRefreshCharterVersions } from "./projectQueries";

const FIELD_LABEL: Record<string, string> = {
  outcome: "Outcome",
  goal: "Goal",
  dod: "Done when",
  scope_paths: "Scope",
  constraints: "Constraints",
  preview: "Previews",
  preview_slots: "Preview slots",
  demo: "Demo focus",
  demo_focus: "Demo focus",
  budget_ics: "Builders at once",
  budgets: "Budgets",
  depends_on: "Depends on",
  depends_mode: "Counts once",
  human_owner: "Owner",
  priority: "Priority",
};

/** A charter value in words: lists one per line, a preview as "on, 2 slots". */
export function charterValue(value: unknown): string {
  if (value == null || value === "") return "none";
  if (Array.isArray(value)) return value.length ? value.join("\n") : "none";
  if (typeof value === "object") {
    const v = value as Record<string, unknown>;
    if ("enabled" in v) {
      const slots = typeof v.slots === "number" ? `, ${v.slots} at once` : "";
      return `${v.enabled ? "on" : "off"}${slots}`;
    }
    return Object.entries(v)
      .map(([k, x]) => `${k.replace(/_/g, " ")} ${String(x)}`)
      .join(", ");
  }
  return String(value);
}

function Row({
  label,
  children,
  mono = false,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="contents">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn("min-w-0 break-words whitespace-pre-line", mono && "font-mono text-xs")}>
        {children}
      </dd>
    </div>
  );
}

function Change({ change }: { change: CharterFieldChange }) {
  return (
    <li className="text-sm">
      <span className="font-medium">{FIELD_LABEL[change.field] ?? change.field}: </span>
      <s className="text-muted-foreground whitespace-pre-line">{charterValue(change.before)}</s>
      {" → "}
      <span className="whitespace-pre-line text-status-green-fg">{charterValue(change.after)}</span>
    </li>
  );
}

function VersionRow({
  version,
  nowS,
  canChange,
  busy,
  onUndo,
  onRevert,
}: {
  version: CharterVersion;
  nowS: number;
  canChange: boolean;
  busy: boolean;
  onUndo: (version: CharterVersion) => void;
  onRevert: (version: CharterVersion) => void;
}) {
  const left = version.undo_until != null ? version.undo_until - nowS : null;
  return (
    <li
      data-testid={`charter-version-${version.ref}`}
      data-status={version.status}
      className="flex min-w-0 items-start gap-3 py-2.5"
    >
      <span className="w-12 shrink-0 pt-0.5 font-mono text-xs text-muted-foreground">
        {version.ref}
      </span>
      <div className="min-w-0 grow">
        {version.changes.length > 0 ? (
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
            {version.changes.map((c) => (
              <Change key={c.field} change={c} />
            ))}
          </ul>
        ) : (
          <p className="text-sm">
            {version.op === "create" ? "Started the project" : version.title}
          </p>
        )}
        <p className="mt-0.5 text-xs text-muted-foreground">
          {version.by}
          {` · ${formatElapsed(nowS - version.at) ?? "now"} ago`}
          {version.revert_of ? ` · reverts ${version.revert_of}` : ""}
          {version.status === "undone" ? " · undone" : ""}
        </p>
      </div>
      {canChange && left != null && left > 0 ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => onUndo(version)}
          data-testid={`charter-undo-${version.ref}`}
          componentId="factory.project.charter_undo"
        >
          <Undo2Icon aria-hidden className="size-3.5" />
          Undo <span className="text-muted-foreground">({formatElapsed(left)} left)</span>
        </Button>
      ) : canChange && version.revertible ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => onRevert(version)}
          data-testid={`charter-revert-${version.ref}`}
          componentId="factory.project.charter_revert"
        >
          Revert
        </Button>
      ) : null}
    </li>
  );
}

export function ProjectCharterTab({
  snapshot,
  project,
  nowS,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
}) {
  const fid = snapshot.factory.id;
  const { can } = useViewerCan();
  const toast = useFactoryToast();
  const client = useQueryClient();
  const versions = useCharterVersions(fid, project);
  const refresh = useRefreshCharterVersions(fid, project.slug);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const charter = project.charter;
  const delivery = snapshot.factory.product?.delivery ?? null;
  const flags = delivery?.flags;
  const flagLine = [flags?.convention, flags?.provider ? `provider ${flags.provider}` : null]
    .filter(Boolean)
    .join(" · ");
  const canChange = can.apply_projects;
  const after = () => {
    refresh();
    void client.invalidateQueries({ queryKey: factoryQueryKey(fid) });
  };
  const undo = async (ref: string) => {
    setBusy(true);
    try {
      await undoProposal(fid, ref);
      toast.show({ message: `Undone. The charter is back as before ${ref}.` });
    } catch (error) {
      toast.show({ message: `Could not undo ${ref}: ${controlErrorMessage(error)}` });
    } finally {
      setBusy(false);
      after();
    }
  };
  const saved = (res: CharterPatchResponse) => {
    after();
    if (!res.changed && !res.proposal) {
      toast.show({ message: "Nothing changed in the charter." });
      return;
    }
    const lead = project.pl
      ? "Its lead gets one CHARTER UPDATE."
      : "Its lead gets it when it starts.";
    const ref = res.proposal ?? null;
    toast.show({
      message: ref ? `Saved as ${ref}. ${lead}` : `Saved. ${lead}`,
      undo: ref ? () => void undo(ref) : undefined,
    });
  };
  const revert = async (version: CharterVersion) => {
    setBusy(true);
    try {
      const res = await revertCharterVersion(fid, project.slug, { proposal: version.ref });
      const ref = res.proposal?.ref ?? null;
      toast.show({
        message: ref ? `Reverted ${version.ref} as ${ref}.` : `Reverted ${version.ref}.`,
        undo: ref ? () => void undo(ref) : undefined,
      });
    } catch (error) {
      toast.show({ message: `Could not revert ${version.ref}: ${controlErrorMessage(error)}` });
    } finally {
      setBusy(false);
      after();
    }
  };
  const list = versions.data?.versions ?? [];
  return (
    <div
      data-testid="project-charter"
      className="grid min-w-0 grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]"
    >
      <SectionCard
        title="Charter"
        testId="project-charter-card"
        action={
          canChange ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setEditing(true)}
              data-testid="project-charter-edit"
              componentId="factory.project.charter_edit"
            >
              <PencilIcon aria-hidden className="size-3.5" />
              Edit
            </Button>
          ) : null
        }
      >
        {!canChange && (
          <p className="text-xs text-muted-foreground">Ask an operator to change this.</p>
        )}
        {charter ? (
          <dl className="grid grid-cols-[8.5rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm max-sm:grid-cols-1">
            <Row label="Outcome">{charter.outcome || project.title}</Row>
            <Row label="Goal">{charter.goal || "none"}</Row>
            <Row label="Done when">
              {charter.dod.length ? charter.dod.join("\n") : "the PR is ready for a person's merge"}
            </Row>
            <Row label="Scope" mono={charter.scope_paths.length > 0}>
              {charter.scope_paths.length ? charter.scope_paths.join("\n") : "the whole repo"}
            </Row>
            <Row label="Constraints">{charter.constraints || "none"}</Row>
            <Row label="Previews">
              {charter.preview.enabled
                ? `on, up to ${charter.preview.slots} at once`
                : charter.env_needed
                  ? "off"
                  : "no env needed"}
              {charter.demo_focus ? ` · demo focus: ${charter.demo_focus}` : ""}
            </Row>
            <Row label="Builders at once">{charter.budget_ics}</Row>
            {project.depends_on?.length ? (
              <Row label="Depends on">{project.depends_on.join(", ")}</Row>
            ) : null}
            <Row label="Merge policy">
              {delivery?.merge_policy ?? "A person merges every pull request."}{" "}
              <span className="text-muted-foreground">(product profile)</span>
            </Row>
            <Row label="Flags">
              {flagLine || "no convention set"}{" "}
              <span className="text-muted-foreground">(product profile)</span>
            </Row>
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">No charter recorded for this project.</p>
        )}
      </SectionCard>
      <SectionCard
        title="Versions"
        meta="every change, newest first"
        testId="project-charter-versions"
      >
        {versions.isPending ? (
          <div aria-busy="true" className="h-24 animate-pulse rounded-lg bg-muted" />
        ) : versions.error ? (
          <p className="text-sm text-muted-foreground">
            This server keeps no charter history yet. Edits still reach the project lead.
          </p>
        ) : list.length === 0 ? (
          <EmptyState
            testId="charter-versions-empty"
            icon={<HistoryIcon aria-hidden className="size-6 text-muted-foreground/50" />}
            title="No changes yet"
            body="Each change becomes a version you can undo for 10 minutes and revert after."
          />
        ) : (
          <ol className="m-0 flex list-none flex-col divide-y divide-border p-0">
            {list.map((v) => (
              <VersionRow
                key={v.ref}
                version={v}
                nowS={nowS}
                canChange={canChange}
                busy={busy}
                onUndo={(x) => void undo(x.ref)}
                onRevert={(x) => void revert(x)}
              />
            ))}
          </ol>
        )}
        <p className="rounded-lg bg-muted/60 px-3 py-2 text-xs text-foreground/80">
          Reverting puts back what one version changed, as a new version you can undo. Work the lead
          already did on the old charter is not rolled back.
        </p>
      </SectionCard>
      {canChange && (
        <EditCharterDialog
          factoryId={fid}
          project={project}
          open={editing}
          onOpenChange={setEditing}
          onSaved={saved}
        />
      )}
    </div>
  );
}
