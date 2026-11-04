// One preview per structural verb, each a form over the server's dry run:
// Add child (a held child with a matching title is pulled forward instead of
// copied), Start now, Autonomy & merge, Pause / Resume, Move (parents one
// level up only), Name it / file under, and any other verb as a plain
// preview. Team has its own panel (`TeamPanel`). Words in the chat open these
// same previews, with what they named filled in.

import { useId, useState } from "react";
import { cn } from "@/lib/utils";
import { useApplyVerb, useVerbPreview } from "../nodeSteer";
import type { NodePage, VerbArgs, VerbAvail, VerbRequest } from "../nodeTypes";
import { levelName } from "../nodeWire";
import {
  FIELD,
  PreviewField,
  PreviewOptions,
  PreviewPanel,
  type PreviewPanelProps,
} from "./PreviewPanel";
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

/** The preview of `verb` with `args`, and Apply (which closes the panel once it applied). */
function useFlow(page: NodePage, verb: VerbAvail, args: Args, onClose: () => void) {
  const preview = useVerbPreview(page, verb.verb, args);
  const apply = useApplyVerb(page);
  const data = preview.data;
  const stale = !preview.settled || preview.isPlaceholderData || preview.isFetching;
  const panel: Pick<
    PreviewPanelProps,
    "preview" | "loading" | "error" | "applying" | "onApply" | "onCancel"
  > = {
    preview: data,
    loading: stale,
    error: preview.error,
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
  const [col, setCol] = useState<number | "later">(columns[1]?.col ?? columns[0]?.col ?? 0);
  const [dep, setDep] = useState("");
  const [because, setBecause] = useState("");
  const [heldFor, setHeldFor] = useState("");
  const later = col === "later";
  const sibs = kids.items.filter((k) => k.state !== "ghost" && k.level === kids.level).slice(0, 5);
  const request: Args = {
    title: title.trim(),
    col,
    ...(dep && !later ? { depends_on: dep, because: because.trim() } : {}),
    ...(later && heldFor.trim() ? { held_for: heldFor.trim() } : {}),
  };
  const { panel, data } = useFlow(page, verb, request, onClose);
  const order = [
    ...columns.map((c) => ({ value: c.col as number | "later", label: c.title })),
    ...(laterTitle ? [{ value: "later" as const, label: laterTitle }] : []),
  ];
  const into = later ? laterTitle : columns.find((c) => c.col === col)?.title;
  const pulled = data?.pulls_forward;
  return (
    <PreviewPanel
      {...panel}
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

function AutonomyPreview({ page, verb, onClose }: VerbProps) {
  const [on, setOn] = useState(!page.node.auto_merge);
  const { panel } = useFlow(page, verb, { auto_merge: on }, onClose);
  return (
    <PreviewPanel
      {...panel}
      label="Autonomy and merge"
      title={`Autonomy for ${page.node.title} and everything under it`}
      applyLabel={on ? "Turn on for this subtree" : "Turn off for this subtree"}
    >
      <button
        type="button"
        role="switch"
        aria-checked={on}
        data-testid="v3-auto-merge-switch"
        onClick={() => setOn((v) => !v)}
        className={cn(
          "flex items-start gap-3 rounded-[10px] border px-3 py-2 text-left text-[13px]",
          on ? "border-status-blue bg-status-blue/12" : "border-border-strong bg-muted/40",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "relative mt-0.5 inline-block h-[18px] w-[30px] shrink-0 rounded-full transition-colors",
            on ? "bg-status-blue" : "bg-border-strong",
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
          <b className="font-semibold">Auto-merge this subtree</b> when gates, review and demo pass
          <span className="block text-xs text-muted-foreground">
            It covers {page.node.title} and everything under it, also what is added later.
          </span>
        </span>
      </button>
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

/** Move and Name it / file under: a parent one level up (only those are offered), and a name. */
function ParentPreview({ page, verb, onClose, naming }: VerbProps & { naming: boolean }) {
  const id = useId();
  const current = [...page.breadcrumb].reverse().find((c) => !c.folded)?.ref ?? "";
  const [to, setTo] = useState(current);
  const [name, setName] = useState("");
  const request: Args = {
    ...(to && to !== current ? { to } : {}),
    ...(naming && name.trim() ? { name: name.trim() } : {}),
  };
  const { panel, data } = useFlow(page, verb, request, onClose);
  const parents = data?.parents ?? [];
  const parentLevel = page.breadcrumb.find((c) => c.ref === current);
  const changed = to !== current || Boolean(naming && name.trim());
  return (
    <PreviewPanel
      {...panel}
      label={verb.label}
      title={naming ? `Name ${page.node.title}, or file it under` : `Move ${page.node.title}`}
      applyLabel={naming ? "Save" : "Move"}
      blocked={changed ? null : naming ? "Give it a name or a parent." : "Pick another parent."}
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
          label={`Parent${parentLevel ? ` (${levelName(parentLevel.level, parentLevel.level_name)})` : ""}`}
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
    default:
      return <PlainPreview {...props} />;
  }
}
