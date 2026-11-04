// Rename levels, from the factory's Definition: each level of the product tree
// with its name and plural, nothing changed until a person types. The preview
// says what changes (every page, the level strip, previews and agent prompts;
// keys, refs, URLs and data stay) and who approves; Apply saves a definition
// version, with Undo for 10 minutes. On a live factory it is the definition's
// own path (its preview, `PUT …/definition` on the version read, Undo by
// re-pinning the version before); a sample source previews and applies it as
// a verb.

import { useId, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  type DefinitionChange,
  type DefinitionPreviewResponse,
  previewFactoryDefinition,
} from "@/lib/factoryApi";
import { useDefinitionEdit } from "../../insights/learnQueries";
import { PREVIEW_DEBOUNCE_MS, useDebounced } from "../../team/teamQueries";
import { useFactoryToast } from "../../ui/Toast";
import { useNodeActions } from "../NodeContext";
import { useNodeSource } from "../nodeSource";
import {
  refusal,
  rememberUndo,
  useApplyVerb,
  useRefreshNodes,
  useVerbPreview,
  useVersionUndo,
} from "../nodeSteer";
import type { LevelInfo, NodePage, PreviewLine, VerbArgs, VerbAvail } from "../nodeTypes";
import { useLevelsInfo } from "../lenses/lensQueries";
import { FIELD, PreviewLines, PreviewPanel } from "./PreviewPanel";

interface PanelProps {
  page: NodePage;
  verb: VerbAvail;
  args?: VerbArgs;
  onClose: () => void;
}

/** A level's new words, only where a person typed something else. */
export type RenameDraft = Record<string, { name?: string; plural?: string }>;

const WHAT_CHANGES =
  "Every page, the level strip, previews and agent prompts use the new names. Keys, refs, URLs and data stay the same.";

/** The renames as `<level>.name` / `<level>.plural` arguments (the sample's verb reads these). */
export function renameArgs(draft: RenameDraft, levels: LevelInfo[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const level of levels) {
    const d = draft[level.key];
    if (!d) continue;
    if (d.name != null && d.name.trim() !== level.name) out[`${level.key}.name`] = d.name.trim();
    if (d.plural != null && d.plural.trim() !== level.plural) {
      out[`${level.key}.plural`] = d.plural.trim();
    }
  }
  return out;
}

/** The same renames as definition changes (`structure.levels.<level>.name`). */
export function renameChanges(args: Record<string, string>): DefinitionChange[] {
  return Object.entries(args).map(([path, value]) => ({
    op: "replace",
    path: `structure.levels.${path}`,
    value,
  }));
}

function RenameFields({
  levels,
  draft,
  onDraft,
}: {
  levels: LevelInfo[];
  draft: RenameDraft;
  onDraft: (next: RenameDraft) => void;
}) {
  const id = useId();
  const set = (level: LevelInfo, field: "name" | "plural", value: string) => {
    const was = draft[level.key] ?? {};
    const next = { ...was, [field]: value };
    // A new name brings its plural along until the plural is typed itself.
    if (field === "name" && was.plural == null) next.plural = undefined;
    onDraft({ ...draft, [level.key]: next });
  };
  return (
    <div
      role="group"
      aria-label="Level names"
      className="grid grid-cols-[minmax(0,110px)_minmax(0,1fr)_minmax(0,1fr)] items-center gap-x-2.5 gap-y-1.5 text-[13px] max-md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
    >
      <span className="text-xs text-foreground-tertiary max-md:hidden">Level</span>
      <span className="text-xs text-foreground-tertiary">Name</span>
      <span className="text-xs text-foreground-tertiary">Plural</span>
      {levels.map((level) => {
        const d = draft[level.key] ?? {};
        const name = d.name ?? level.name;
        const plural = d.plural ?? (d.name != null ? `${d.name.trim()}s` : level.plural);
        return (
          <div key={level.key} className="contents">
            <span className="font-mono text-xs text-foreground-tertiary max-md:col-span-2 max-md:mt-1">
              {level.key}
            </span>
            <input
              id={`${id}-${level.key}-name`}
              aria-label={`${level.name} level: name`}
              type="text"
              value={name}
              onChange={(e) => set(level, "name", e.target.value)}
              className={FIELD}
            />
            <input
              id={`${id}-${level.key}-plural`}
              aria-label={`${level.name} level: plural`}
              type="text"
              value={plural}
              onChange={(e) => set(level, "plural", e.target.value)}
              className={FIELD}
            />
          </div>
        );
      })}
    </div>
  );
}

/** The draft with plurals that follow a typed name filled in (what is sent). */
function withPlurals(draft: RenameDraft): RenameDraft {
  const out: RenameDraft = {};
  for (const [key, d] of Object.entries(draft)) {
    out[key] = {
      ...d,
      ...(d.name != null && d.plural == null ? { plural: `${d.name.trim()}s` } : {}),
    };
  }
  return out;
}

function productLevels(levels: { trees: Record<string, { levels: LevelInfo[] }> } | undefined) {
  return levels?.trees.product?.levels ?? [];
}

/** The sample's (or any source's) verb flow: preview and apply through the source. */
function SourceRename({ page, verb, onClose }: PanelProps) {
  const info = useLevelsInfo();
  const levels = productLevels(info.data);
  const [draft, setDraft] = useState<RenameDraft>({});
  const args = useMemo(() => renameArgs(withPlurals(draft), levels), [draft, levels]);
  const changed = Object.keys(args).length > 0;
  const preview = useVerbPreview(page, verb.verb, args, { enabled: changed });
  const apply = useApplyVerb(page);
  const data = changed ? preview.data : undefined;
  return (
    <PreviewPanel
      // Nothing typed yet: nothing to read, so no placeholder either.
      bare={!changed}
      label={verb.label}
      title="Rename levels"
      preview={data}
      loading={changed && (!preview.settled || preview.isPlaceholderData || preview.isFetching)}
      error={changed ? preview.error : null}
      applyLabel="Save as a definition version"
      blocked={changed ? null : "Type a new name for a level to preview it."}
      applying={apply.isPending}
      onCancel={onClose}
      onApply={() => {
        if (!data) return;
        apply.mutate(
          { verb: verb.verb, label: verb.label, args, preview: data },
          { onSuccess: onClose },
        );
      }}
    >
      {info.data ? (
        <RenameFields levels={levels} draft={draft} onDraft={setDraft} />
      ) : (
        <div aria-busy="true" className="h-24 animate-pulse rounded-md bg-muted" />
      )}
    </PreviewPanel>
  );
}

/** The definition's own path on a live factory. */
function LiveRename({ page, verb, onClose }: PanelProps) {
  const { factoryId: fid } = useNodeActions();
  const info = useLevelsInfo();
  const levels = productLevels(info.data);
  const version = info.data?.version ?? null;
  const [draft, setDraft] = useState<RenameDraft>({});
  const args = useMemo(() => renameArgs(withPlurals(draft), levels), [draft, levels]);
  const changes = useMemo(() => renameChanges(args), [args]);
  const body = useDebounced(
    JSON.stringify({ changes, base_version: version ?? undefined }),
    PREVIEW_DEBOUNCE_MS,
  );
  const settled = body === JSON.stringify({ changes, base_version: version ?? undefined });
  // Asked only once the typing paused on something to rename (never with no change).
  const asks = (JSON.parse(body) as { changes: DefinitionChange[] }).changes.length > 0;
  const preview = useQuery<DefinitionPreviewResponse>({
    queryKey: ["factory-node", fid, "rename-preview", body],
    queryFn: () => previewFactoryDefinition(fid, JSON.parse(body)),
    enabled: Boolean(fid) && asks && changes.length > 0 && version != null,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: false,
  });
  const edit = useDefinitionEdit(fid);
  const refresh = useRefreshNodes(fid);
  const toast = useFactoryToast();
  const versionUndo = useVersionUndo(page.node.ref, fid);
  const res = changes.length ? preview.data : undefined;
  const lines: PreviewLine[] = res
    ? [
        ...res.lines.map((l) => ({ label: "Names", html: escapeHtml(l) })),
        { label: "What changes", html: escapeHtml(WHAT_CHANGES) },
        { label: "Who approves", html: escapeHtml(res.who) },
      ]
    : [];
  const blocked = !changes.length
    ? "Type a new name for a level to preview it."
    : res && !res.you_can_apply
      ? `${res.who} You can look, not apply.`
      : res?.blocked.length
        ? res.blocked.map((b) => b.message).join(" ")
        : null;
  const applyNow = async () => {
    const base = res?.def_version ?? version ?? undefined;
    try {
      const out = await edit.mutateAsync({
        changes,
        note: "Rename levels",
        ...(base != null ? { base_version: base } : {}),
      });
      void refresh();
      onClose();
      const made = out.def_version;
      const undo =
        made != null && base != null
          ? versionUndo(
              verb.label,
              made,
              base,
              `Undone. The levels are named as in v${base} again; v${made} stays in history.`,
            )
          : null;
      if (undo) rememberUndo(fid, page.node.ref, undo);
      toast.show({
        message: `Levels renamed as definition v${made ?? "?"}: every page uses the new names.`,
        ...(undo ? { undo: () => void undo.run() } : {}),
      });
    } catch (error) {
      void refresh();
      toast.show({ message: refusal(error) });
    }
  };
  return (
    <PreviewPanel
      bare
      label={verb.label}
      title="Rename levels"
      loading={changes.length > 0 && (!settled || preview.isFetching)}
      error={changes.length ? preview.error : null}
      applyLabel="Save as a definition version"
      blocked={blocked}
      applying={edit.isPending}
      onApply={() => void applyNow()}
      onCancel={onClose}
      note={changes.length ? "Undo for 10 min after applying." : "Nothing changes until you type."}
    >
      {info.data ? (
        <RenameFields levels={levels} draft={draft} onDraft={setDraft} />
      ) : info.isError ? (
        <p role="alert" className="text-[13px] text-status-red-fg">
          The levels could not be read: {String(info.error)}
        </p>
      ) : (
        <div aria-busy="true" className="h-24 animate-pulse rounded-md bg-muted" />
      )}
      {lines.length > 0 && <PreviewLines lines={lines} />}
    </PreviewPanel>
  );
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!,
  );
}

/** Rename levels: the live definition's path, else the source's verb flow (the sample). */
export function RenamePanel(props: PanelProps) {
  const source = useNodeSource();
  return source?.kind === "live" ? <LiveRename {...props} /> : <SourceRename {...props} />;
}
