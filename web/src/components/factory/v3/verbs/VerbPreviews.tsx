// One preview per structural verb, each a form over the server's dry run:
// Add child (a held child with a matching title is pulled forward instead of
// copied), Start now, Autonomy & merge, Pause / Resume, Move (parents one
// level up only), Name it / file under, Drop a follow-up (of a feature's done
// review), and any other verb as a plain preview. Team and Rename levels have their own panels (`TeamPanel`,
// `RenamePanel`): both are definition versions. Words in the chat open these
// same previews, with what they named filled in.

import { useId, useState } from "react";
import { FactoryApiError } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { useApplyVerb, useVerbPreview } from "../nodeSteer";
import type { NodePage, VerbArgs, VerbAvail, VerbRequest } from "../nodeTypes";
import { escText, levelName } from "../nodeWire";
import {
  FIELD,
  PreviewField,
  PreviewOptions,
  PreviewPanel,
  type PreviewPanelProps,
} from "./PreviewPanel";
import { RenamePanel } from "./RenamePanel";
import { TeamPanel } from "./TeamPanel";

/** A verb opened on this page, with what words in the chat named. */
export interface OpenVerb {
  verb: VerbAvail;
  args?: VerbArgs;
}

interface VerbProps {
  page: NodePage;
  verb: VerbAvail;
  args?: VerbArgs;
  onClose: () => void;
}

type Args = VerbRequest["args"];

/**
 * The preview of `verb` with `args`, and Apply (which closes the panel once it
 * applied). `enabled: false` while the form is blocked: nothing is asked.
 */
function useFlow(page: NodePage, verb: VerbAvail, args: Args, onClose: () => void, enabled = true) {
  const preview = useVerbPreview(page, verb.verb, args, { enabled });
  const apply = useApplyVerb(page);
  // A blocked form shows no earlier answer either: there is nothing to preview.
  const data = enabled ? preview.data : undefined;
  const stale = enabled && (!preview.settled || preview.isPlaceholderData || preview.isFetching);
  const panel: Pick<
    PreviewPanelProps,
    "preview" | "loading" | "error" | "applying" | "onApply" | "onCancel"
  > = {
    preview: data,
    loading: stale,
    error: enabled ? preview.error : null,
    applying: apply.isPending,
    onCancel: onClose,
    onApply: () => {
      if (!data) return;
      apply.mutate(
        { verb: verb.verb, label: verb.label, args, preview: data },
        { onSuccess: onClose },
      );
    },
  };
  return { panel, data };
}

function AddChildPreview({ page, verb, args, onClose }: VerbProps) {
  const id = useId();
  const kids = page.children;
  const noun = levelName(kids.level, null).toLowerCase() || "child";
  const columns = kids.columns ?? [];
  const laterTitle = kids.later_title ?? (kids.later.length ? "Later" : null);
  const [title, setTitle] = useState(args?.title ?? "");
  // A new child with nothing to wait on lands in the first column; it goes
  // later in the order by what it depends on (the server's rule).
  const [col, setCol] = useState<number | "later">(columns[0]?.col ?? 0);
  const [dep, setDep] = useState("");
  const [because, setBecause] = useState("");
  const [heldFor, setHeldFor] = useState("");
  const later = col === "later";
  const sibs = kids.items.filter((k) => k.state !== "ghost" && k.level === kids.level).slice(0, 5);
  const request: Args = {
    title: title.trim(),
    col,
    // What it waits on, as the projects op names it: a list of refs and a reason each.
    ...(dep && !later
      ? { depends_on: [dep], ...(because.trim() ? { depends_why: { [dep]: because.trim() } } : {}) }
      : {}),
    ...(later && heldFor.trim() ? { held_for: heldFor.trim() } : {}),
  };
  // No title yet: no preview (the server would refuse an empty one).
  const { panel, data } = useFlow(page, verb, request, onClose, Boolean(request.title));
  const order = [
    ...columns.map((c) => ({ value: c.col as number | "later", label: c.title })),
    ...(laterTitle ? [{ value: "later" as const, label: laterTitle }] : []),
  ];
  const pulled = data?.pulls_forward;
  // Where it lands, as the server placed it (its column follows what it depends on).
  const ghost = data?.columns_after?.items.find(
    (k) => k.state === "ghost" && k.ref === pulled?.ref,
  );
  const landed = typeof ghost?.col === "number" ? ghost.col : col;
  const into = later ? laterTitle : columns.find((c) => c.col === landed)?.title;
  // The banner says the pull-forward; the server's sentence saying it again is left out.
  const said = pulled ? `${escText(pulled.title)} is already here` : null;
  const preview =
    data && said
      ? { ...data, effects: data.effects.filter((e) => !e.html.startsWith(said)) }
      : data;
  return (
    <PreviewPanel
      {...panel}
      preview={preview}
      label="Add child"
      title={`Add a ${noun} under ${page.node.title}`}
      applyLabel={later ? "Add as held" : "Add and plan"}
      blocked={title.trim() ? null : "Give it a title first."}
      found={
        pulled && !later ? (
          <p
            data-testid="v3-preview-found"
            className="rounded-lg bg-status-blue/12 px-2.5 py-1.5 text-[12.8px] text-status-blue-fg"
          >
            {pulled.title} is already here
            {pulled.held_for ? `, held for ${pulled.held_for}` : ", held"}. Adding it now{" "}
            <b>pulls it forward</b>
            {into ? ` into ${into}` : ""} instead of making a second copy.
          </p>
        ) : null
      }
    >
      <PreviewField label="Title" htmlFor={`${id}-title`}>
        <input
          id={`${id}-title`}
          type="text"
          value={title}
          // A button or key opened it: type the title now (words in the chat keep their box).
          autoFocus={!args}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={`A new ${noun}`}
          className={FIELD}
        />
      </PreviewField>
      {order.length > 1 && (
        <PreviewField label="Order">
          <PreviewOptions label="Order" options={order} value={col} onChange={setCol} />
        </PreviewField>
      )}
      {!later && sibs.length > 0 && (
        <PreviewField label="Depends on">
          <PreviewOptions
            label="Depends on"
            value={dep}
            onChange={setDep}
            options={[
              ...sibs.map((s) => ({ value: s.ref, label: s.title })),
              { value: "", label: "nothing" },
            ]}
          />
        </PreviewField>
      )}
      {!later && dep && (
        <PreviewField label="Because" htmlFor={`${id}-why`}>
          <input
            id={`${id}-why`}
            type="text"
            value={because}
            onChange={(e) => setBecause(e.target.value)}
            placeholder="Why it waits on it (a link across features needs one)"
            className={FIELD}
          />
        </PreviewField>
      )}
      {later && (
        <PreviewField label="Held for" htmlFor={`${id}-held`}>
          <input
            id={`${id}-held`}
            type="text"
            value={heldFor}
            onChange={(e) => setHeldFor(e.target.value)}
            placeholder="What it waits for before it can start"
            className={FIELD}
          />
        </PreviewField>
      )}
    </PreviewPanel>
  );
}

function StartNowPreview({ page, verb, onClose }: VerbProps) {
  const { panel } = useFlow(page, verb, {}, onClose);
  return (
    <PreviewPanel
      {...panel}
      label="Start now"
      title={`Start ${page.node.title} now`}
      applyLabel="Start now"
    />
  );
}

/** The subtree's autonomy level: kept as it is unless picked ("As above" drops its own). */
const AUTONOMY_CHOICES = [
  { value: "", label: "Keep" },
  { value: "careful", label: "Careful" },
  { value: "standard", label: "Standard" },
  { value: "tryout", label: "Tryout" },
  { value: "inherit", label: "As above" },
];

/** The base the server would have taken when it asks the owner to name it (`args.base`). */
function askedBase(error: unknown): { base: string | null } | null {
  if (!(error instanceof FactoryApiError) || error.field !== "args.base") return null;
  const body = error.body;
  const base =
    body !== null && typeof body === "object" ? (body as Record<string, unknown>).base : null;
  return { base: typeof base === "string" && base ? base : null };
}

function AutonomyPreview({ page, verb, onClose }: VerbProps) {
  const id = useId();
  const covered = Boolean(page.node.auto_merge);
  // Auto-merge: `null` keeps it as it is (nothing sent). A node it does not
  // cover yet opens on turning it on, what the verb is mostly for; one it
  // covers keeps it, so picking only a level changes only the level.
  const [merge, setMerge] = useState<boolean | null>(covered ? null : true);
  const [level, setLevel] = useState("");
  // `null` until the owner names a base: the server's choice, never main.
  const [base, setBase] = useState<string | null>(null);
  const on = merge ?? covered;
  const named = merge === true ? base?.trim() : undefined;
  const args: Args = {
    ...(merge === null ? {} : { auto_merge: merge }),
    ...(named ? { base: named } : {}),
    ...(level ? { autonomy: level } : {}),
  };
  const nothing = merge === null && !level;
  const { panel } = useFlow(page, verb, args, onClose, !nothing);
  const asked = askedBase(panel.error);
  const showBase = merge === true && (base !== null || asked !== null);
  return (
    <PreviewPanel
      {...panel}
      label="Autonomy and merge"
      title={`Autonomy for ${page.node.title} and everything under it`}
      applyLabel={
        merge === true
          ? "Turn on for this subtree"
          : merge === false
            ? "Turn off for this subtree"
            : "Apply to this subtree"
      }
      blocked={nothing ? "Pick what changes: auto-merge or the autonomy level." : null}
    >
      <div className="flex flex-wrap items-start gap-2">
        <button
          type="button"
          role="switch"
          aria-checked={on}
          data-testid="v3-auto-merge-switch"
          data-kept={merge === null || undefined}
          onClick={() => setMerge(!on)}
          className={cn(
            "flex min-w-0 flex-1 items-start gap-3 rounded-[10px] border px-3 py-2 text-left text-[13px]",
            merge === null
              ? "border-border-strong bg-transparent"
              : on
                ? "border-status-blue bg-status-blue/12"
                : "border-border-strong bg-muted/40",
          )}
        >
          <span
            aria-hidden
            className={cn(
              "relative mt-0.5 inline-block h-[18px] w-[30px] shrink-0 rounded-full transition-colors",
              on ? "bg-status-blue" : "bg-border-strong",
              merge === null && "opacity-60",
            )}
          >
            <span
              className={cn(
                "absolute top-[2px] size-[14px] rounded-full bg-white transition-[left]",
                on ? "left-[14px]" : "left-[2px]",
              )}
            />
          </span>
          <span>
            <b className="font-semibold">Auto-merge this subtree</b> when gates, review and demo
            pass
            <span className="block text-xs text-muted-foreground">
              {merge === null
                ? `Kept as it is (${covered ? "on" : "off"}). Flip it to change it.`
                : `It covers ${page.node.title} and everything under it, also what is added later.`}
            </span>
          </span>
        </button>
        {merge !== null && (
          <button
            type="button"
            data-testid="v3-auto-merge-keep"
            onClick={() => {
              setMerge(null);
              setBase(null);
            }}
            className="rounded-[7px] border border-border-strong px-2.5 py-0.5 text-[12.5px] text-muted-foreground hover:text-foreground"
          >
            Keep as it is
          </button>
        )}
      </div>
      {showBase && (
        <PreviewField label="Into base" htmlFor={`${id}-base`}>
          <input
            id={`${id}-base`}
            type="text"
            value={base ?? ""}
            onChange={(e) => setBase(e.target.value)}
            placeholder={asked?.base ?? "the branch to merge into"}
            aria-describedby={`${id}-base-why`}
            className={FIELD}
          />
          <span id={`${id}-base-why`} className="mt-1 block text-xs text-muted-foreground">
            {asked?.base
              ? `${asked.base} is never chosen for you: name it to auto-merge into it.`
              : "The branch the server merges this subtree's ready PRs into."}
          </span>
        </PreviewField>
      )}
      <PreviewField label="Autonomy here">
        <PreviewOptions
          label="Autonomy here"
          options={AUTONOMY_CHOICES}
          value={level}
          onChange={setLevel}
        />
      </PreviewField>
    </PreviewPanel>
  );
}

function PausePreview({ page, verb, onClose }: VerbProps) {
  const { panel } = useFlow(page, verb, {}, onClose);
  const pause = verb.verb === "pause";
  return (
    <PreviewPanel
      {...panel}
      label={verb.label}
      title={`${pause ? "Pause" : "Resume"} ${page.node.title}`}
      applyLabel={pause ? "Pause" : "Resume"}
    />
  );
}

/**
 * Move and Name it / file under: a parent one level up (only those the server
 * lists are offered), and a name. With nothing picked yet the preview is read
 * only for its `parents` (docs/FACTORY.md, the S12 contract); a server that
 * answers it with an error offers no parent, and says so, instead of an error.
 */
function ParentPreview({ page, verb, onClose, naming }: VerbProps & { naming: boolean }) {
  const id = useId();
  // The node's own parent is the last crumb, folded or not (step-out skips a
  // folded one; the parent is still that implicit node).
  const parentCrumb = page.breadcrumb.at(-1);
  const current = parentCrumb?.ref ?? "";
  const [to, setTo] = useState(current);
  const [name, setName] = useState("");
  const request: Args = {
    ...(to && to !== current ? { to } : {}),
    // The name goes as `title`, as a new child's does (the S12 server reads it so).
    ...(naming && name.trim() ? { title: name.trim() } : {}),
  };
  const { panel, data } = useFlow(page, verb, request, onClose);
  const parents = data?.parents ?? [];
  const changed = to !== current || Boolean(naming && name.trim());
  const unlisted = !changed && Boolean(panel.error);
  return (
    <PreviewPanel
      {...panel}
      error={unlisted ? null : panel.error}
      loading={!unlisted && panel.loading}
      label={verb.label}
      title={naming ? `Name ${page.node.title}, or file it under` : `Move ${page.node.title}`}
      applyLabel={naming ? "Save" : "Move"}
      blocked={
        changed
          ? null
          : unlisted
            ? `This server lists no parent to ${naming ? "file it under" : "move it under"} yet.`
            : naming
              ? "Give it a name or a parent."
              : "Pick another parent."
      }
    >
      {naming && (
        <PreviewField label="Name" htmlFor={`${id}-name`}>
          <input
            id={`${id}-name`}
            type="text"
            value={name}
            autoFocus
            onChange={(e) => setName(e.target.value)}
            placeholder={page.node.title}
            className={FIELD}
          />
        </PreviewField>
      )}
      {parents.length > 0 && (
        <PreviewField
          label={
            naming
              ? // What it is filed under is the server's to say (its line names the level).
                "File under"
              : `Parent${parentCrumb ? ` (${levelName(parentCrumb.level, parentCrumb.level_name)})` : ""}`
          }
        >
          <PreviewOptions
            label="Parent"
            value={to}
            onChange={setTo}
            options={parents.map((p) => ({ value: p.ref, label: p.title }))}
          />
        </PreviewField>
      )}
    </PreviewPanel>
  );
}

/** Drop one follow-up of the feature's done review: pick it, see what is taken back. */
function DropPreview({ page, verb, args, onClose }: VerbProps) {
  const open = (page.review?.gaps ?? []).filter((g) => g.live);
  const [gap, setGap] = useState<number | null>(
    args?.gap ?? (open.length === 1 ? open[0]!.n : null),
  );
  const { panel } = useFlow(page, verb, gap == null ? {} : { gap }, onClose, gap != null);
  return (
    <PreviewPanel
      {...panel}
      label={verb.label}
      title={`Drop a follow-up of ${page.node.title}`}
      applyLabel="Drop it"
      blocked={gap == null ? "Pick the follow-up to drop." : null}
    >
      {open.length > 1 && (
        <PreviewField label="Follow-up">
          <PreviewOptions
            label="Follow-up"
            value={gap}
            onChange={setGap}
            options={open.map((g) => ({ value: g.n, label: g.line }))}
          />
        </PreviewField>
      )}
    </PreviewPanel>
  );
}

/** A verb with no fields of its own (Start research …): its preview and Apply. */
function PlainPreview({ page, verb, onClose }: VerbProps) {
  const { panel } = useFlow(page, verb, {}, onClose);
  return (
    <PreviewPanel
      {...panel}
      label={verb.label}
      title={`${verb.label}: ${page.node.title}`}
      applyLabel={verb.label}
    />
  );
}

/** The preview of the verb opened on this page. */
export function VerbPreview({
  page,
  open,
  onClose,
}: {
  page: NodePage;
  open: OpenVerb;
  onClose: () => void;
}) {
  const props: VerbProps = { page, verb: open.verb, args: open.args, onClose };
  switch (open.verb.verb) {
    case "add":
      return <AddChildPreview {...props} />;
    case "start":
      return <StartNowPreview {...props} />;
    case "team":
      return <TeamPanel {...props} />;
    case "auto":
      return <AutonomyPreview {...props} />;
    case "pause":
    case "resume":
      return <PausePreview {...props} />;
    case "move":
      return <ParentPreview {...props} naming={false} />;
    case "name":
      return <ParentPreview {...props} naming />;
    case "rename":
      return <RenamePanel {...props} />;
    case "drop":
      return <DropPreview {...props} />;
    default:
      return <PlainPreview {...props} />;
  }
}
