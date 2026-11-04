// A verb's preview, inline under the node's header: what it would change (the
// parent's columns with a dashed ghost, the roll-up change, the effects), who
// approves, then Apply or Cancel. Nothing changes until Apply, and every apply
// has Undo for 10 minutes. The columns are boxes in a grid: no line joins them.

import { type ReactNode, useEffect, useId, useRef } from "react";
import { cn } from "@/lib/utils";
import { Kbd, StateDot } from "../nodeParts";
import type { ChildPage, NodeSummary, PreviewLine, VerbPreview } from "../nodeTypes";
import { RichText } from "../RichText";

/** "you" → "You", "atlas" → "Atlas": who approves when the server sends no words. */
export function approverWords(approver: string): string {
  if (!approver) return "";
  return `${approver[0]!.toUpperCase()}${approver.slice(1)}.`;
}

const PANEL =
  "flex min-w-0 flex-col gap-2.5 rounded-xl border-[1.5px] border-status-blue/45 bg-card-solid px-4 py-3.5 max-md:px-3";

/** The parent's First / Then / … / Later columns after the change: the change dashed, its old place struck. */
export function MiniColumns({
  columns,
  movedFrom,
}: {
  columns: ChildPage;
  movedFrom?: VerbPreview["moved_from"];
}) {
  const ordered = columns.columns ?? [{ title: "", col: 0 }];
  const colOf = (k: NodeSummary) => (typeof k.col === "number" ? k.col : (ordered[0]?.col ?? 0));
  const ghost = [...columns.items, ...columns.later].find(
    (k) => k.state === "ghost" && k.ref === movedFrom?.ref,
  );
  const cells: { key: string; title: string; items: ReactNode[] }[] = ordered.map((c) => ({
    key: String(c.col),
    title: c.title,
    items: [],
  }));
  const cellOf = (col: number) =>
    cells.find((c) => c.key === String(col)) ?? cells[cells.length - 1]!;
  const item = (k: NodeSummary, gone = false) => (
    <li
      key={`${k.ref}${gone ? ":gone" : ""}`}
      data-testid={gone ? "v3-mini-gone" : k.state === "ghost" ? "v3-mini-ghost" : "v3-mini-item"}
      data-ref={k.ref}
      className={cn(
        "flex min-w-0 items-center gap-1.5 rounded-md border px-2 py-[3px] text-[12.5px]",
        k.state === "ghost"
          ? "border-dashed border-status-blue/70 text-status-blue-fg"
          : "border-border bg-card-solid text-foreground",
        gone && "border-border text-foreground-tertiary line-through opacity-60",
      )}
    >
      {k.state !== "ghost" && <StateDot state={gone ? "later" : k.state} />}
      <span className="min-w-0 truncate">{k.title}</span>
    </li>
  );
  for (const k of columns.items) cellOf(colOf(k)).items.push(item(k));
  const later = columns.later.map((k) => item(k));
  if (ghost && movedFrom) {
    if (movedFrom.col === "later") later.unshift(item(ghost, true));
    else cellOf(movedFrom.col).items.push(item(ghost, true));
  }
  if (later.length) {
    cells.push({ key: "later", title: columns.later_title ?? "Later", items: later });
  }
  return (
    <div
      data-testid="v3-mini-columns"
      className="grid gap-2.5 rounded-[10px] bg-muted/40 p-2 max-md:grid-cols-1!"
      style={{ gridTemplateColumns: `repeat(${cells.length}, minmax(0, 1fr))` }}
    >
      {cells.map((c) => (
        <div key={c.key} className="flex min-w-0 flex-col gap-1">
          {c.title && (
            <div className="px-0.5 text-[10.5px] tracking-[0.07em] text-foreground-tertiary uppercase">
              {c.title}
            </div>
          )}
          <ul className="flex flex-col gap-1">{c.items}</ul>
        </div>
      ))}
    </div>
  );
}

/** Label · value lines: the roll-up change, the effects, who approves. */
export function PreviewLines({ lines }: { lines: PreviewLine[] }) {
  const shown = lines.filter((l) => l.html);
  if (!shown.length) return null;
  return (
    <dl
      data-testid="v3-preview-lines"
      className="grid grid-cols-[120px_minmax(0,1fr)] gap-x-3 gap-y-1.5 border-t border-border pt-2.5 text-[13px] max-md:grid-cols-1 max-md:gap-y-0.5"
    >
      {shown.map((l) => (
        <div key={`${l.label}:${l.html}`} className="contents" data-testid="v3-preview-line">
          <dt className="text-muted-foreground max-md:mt-1.5 max-md:text-xs">{l.label}</dt>
          <dd className="min-w-0 [overflow-wrap:anywhere]">
            <RichText html={l.html} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

export interface PreviewPanelProps {
  /** The heading ("Start assistant-panel-ux now"). */
  title: string;
  /** The server's dry run (absent while the first one loads). */
  preview?: VerbPreview;
  /** Reading the preview (the first time, or again after the form changed). */
  loading?: boolean;
  error?: unknown;
  /** The verb's own fields (title, order, parent …). */
  children?: ReactNode;
  /** Above the columns: "Ads network integration is already here …". */
  found?: ReactNode;
  /** Apply's words; the server's `apply_label` wins. */
  applyLabel: string;
  /** Why Apply is off (no title yet, nothing staged …). */
  blocked?: string | null;
  applying?: boolean;
  onApply: () => void;
  onCancel: () => void;
  /** Under Apply: "Undo for 10 min after applying." by default. */
  note?: string;
  /** The panel's accessible name. */
  label?: string;
  /** No server preview: the panel's own fields say what changes (`children`). */
  bare?: boolean;
}

export function PreviewPanel({
  title,
  preview,
  loading = false,
  error,
  children,
  found,
  applyLabel,
  blocked,
  applying = false,
  onApply,
  onCancel,
  note = "Undo for 10 min after applying.",
  label = "Preview",
  bare = false,
}: PreviewPanelProps) {
  const heading = useId();
  const root = useRef<HTMLElement>(null);
  // Esc cancels, from anywhere but a dialog or menu (which close themselves first).
  const cancel = useRef(onCancel);
  cancel.current = onCancel;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest?.("[role=dialog],[role=menu],[role=listbox]")) return;
      // Esc in another field (the chat's box) is that field's, not the preview's.
      const field = target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
      if (field && !root.current?.contains(target)) return;
      event.preventDefault();
      cancel.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    root.current?.scrollIntoView?.({ block: "nearest" });
  }, []);
  const lines: PreviewLine[] = preview
    ? [
        ...preview.rollup_delta,
        ...preview.effects,
        {
          label: "Who approves",
          html: escapeText(preview.approver_note ?? approverWords(preview.approver)),
        },
      ]
    : [];
  const off = blocked ?? (preview || bare ? null : "Reading the preview…");
  return (
    <section
      ref={root}
      aria-labelledby={heading}
      aria-busy={loading || undefined}
      data-testid="v3-preview"
      data-label={label}
      className={PANEL}
    >
      <h3 id={heading} className="flex flex-wrap items-baseline gap-x-2 text-sm font-semibold">
        <span className="sr-only">{label}: </span>
        {preview?.title ?? title}
        <span className="text-[12.5px] font-normal text-foreground-tertiary">
          nothing changes until you confirm
        </span>
      </h3>
      {children}
      {found}
      {preview?.columns_after && (
        <MiniColumns columns={preview.columns_after} movedFrom={preview.moved_from} />
      )}
      {error ? (
        <p role="alert" className="text-[13px] text-status-red-fg">
          This preview could not be read: {error instanceof Error ? error.message : String(error)}
        </p>
      ) : preview ? (
        <PreviewLines lines={lines} />
      ) : bare ? null : (
        <div aria-hidden className="h-16 animate-pulse rounded-md bg-muted" />
      )}
      {preview?.warnings.map((w) => (
        <p key={w} className="text-[12.5px] text-status-yellow-fg">
          {w}
        </p>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="v3-preview-apply"
          disabled={Boolean(off) || applying || loading || Boolean(error)}
          title={off ?? undefined}
          onClick={onApply}
          className="rounded-[7px] bg-foreground px-3 py-1 text-[13px] font-semibold text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-45"
        >
          {applying ? "Applying…" : (preview?.apply_label ?? applyLabel)}
        </button>
        <button
          type="button"
          data-testid="v3-preview-cancel"
          onClick={onCancel}
          className="inline-flex items-center gap-1.5 rounded-[7px] border border-border-strong px-2.5 py-1 text-[13px] font-medium hover:border-status-blue"
        >
          Cancel <Kbd>esc</Kbd>
        </button>
        <span className="text-xs text-foreground-tertiary">{note}</span>
      </div>
    </section>
  );
}

function escapeText(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!,
  );
}

/** One field of a preview's form: a label and its control, side by side (stacked on a phone). */
export function PreviewField({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div className="grid grid-cols-[120px_minmax(0,1fr)] items-center gap-3 text-[13px] max-md:grid-cols-1 max-md:gap-1">
      {htmlFor ? (
        <label htmlFor={htmlFor} className="text-muted-foreground">
          {label}
        </label>
      ) : (
        <span className="text-muted-foreground">{label}</span>
      )}
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/** A row of options (one pressed), keyboardable. */
export function PreviewOptions<T extends string | number>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T | null;
  onChange: (value: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded-[7px] border px-2.5 py-0.5 text-[12.5px]",
            o.value === value
              ? "border-status-blue bg-status-blue/12 text-status-blue-fg"
              : "border-border-strong bg-muted/40 text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export const FIELD =
  "w-full min-w-0 rounded-[7px] border border-border-strong bg-muted/40 px-2.5 py-1 text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-status-blue";
