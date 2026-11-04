// A node's header: at most five lines (what it is, why it exists, its status,
// its roll-up, its links), then the verbs that steer it. A line with nothing
// to say is left out; nothing renders empty.

import { type ReactNode, useState } from "react";
import { Undo2Icon } from "lucide-react";
import { cn } from "@/lib/utils";
import { undoLabel } from "../steer/steerModel";
import { ChipList } from "./LinkChip";
import { useNodeActions } from "./NodeContext";
import { Avatar, Glyph, Kbd, NodeLink, Ring, money, sinceWhen } from "./nodeParts";
import { useNodeUndo } from "./nodeSteer";
import type { NodePage, Rollup, RollupCount, VerbAvail } from "./nodeTypes";
import { levelName } from "./nodeWire";
import { RichText } from "./RichText";
import { StatusBox } from "./StatusLine";
import { WhyCard } from "./WhyCard";

const DOT = <span className="text-foreground-tertiary">·</span>;
/** A separator that stays at the end of its line ("4 working · 2 idle"). */
const SEP = "\u00a0· ";

function TeamStrip({ team }: { team: NonNullable<Rollup["team"]> }) {
  const n = team.working.length;
  if (!n && !team.idle) return null;
  return (
    <span className="inline-flex items-center gap-1">
      {team.working.map((a) => (
        <Avatar key={a.name} actor={a} />
      ))}
      <span>
        {n ? `${n} working` : ""}
        {n && team.idle ? SEP : ""}
        {team.idle ? `${team.idle} idle` : ""}
      </span>
    </span>
  );
}

/** "3 features", "9 projects (+4 later)", "4 projects later": a root's count, named from the definition. */
function CountWords({ c }: { c: RollupCount }) {
  const later = c.later ?? 0;
  const n = c.count || later;
  const noun = (n === 1 ? c.name : c.plural).toLowerCase();
  return (
    <>
      <b className="font-semibold text-foreground">{n}</b> {noun}
      {c.count === 0 ? " later" : null}
      {c.count > 0 && later > 0 ? (
        <span className="text-foreground-tertiary"> (+{later} later)</span>
      ) : null}
    </>
  );
}

/** Line 4: progress, cost, team, base env, what changed since the last look. */
export function RollupLine({ page }: { page: NodePage }) {
  const { node, rollup } = page;
  const { onSheet } = useNodeActions();
  const isRoot = page.breadcrumb.length === 0;
  const parts: { id: string; node: ReactNode; shrink?: boolean }[] = [];
  const add = (id: string, part: ReactNode, shrink = false) => {
    parts.push({ id, node: part, shrink });
  };
  const p = rollup.progress;
  const counts = (rollup.counts ?? []).filter((c) => c.count > 0 || c.later);
  if (counts.length) {
    add(
      "counts",
      <>
        {counts.map((c, i) => (
          <span key={c.key}>
            <CountWords c={c} />
            {i < counts.length - 1 && SEP}
          </span>
        ))}
      </>,
    );
  }
  if (p?.what === "acceptance") {
    add(
      "progress",
      <>
        <b className="font-semibold text-foreground">{p.label}</b> acceptance steps pass on base env
        {p.secondary != null && (
          <>
            {" "}
            · build{" "}
            <b className="font-semibold text-foreground">{Math.round(p.secondary * 100)}%</b>
          </>
        )}
      </>,
    );
  } else if (p?.what === "tasks" || p?.what === "projects") {
    add(
      "progress",
      <>
        <b className="font-semibold text-foreground">{p.label}</b> {p.what} done
      </>,
    );
  } else if (p?.what === "not planned" && node.state !== "done") {
    add("progress", "no tasks planned yet");
  }
  if (rollup.cost > 0) {
    const tail = rollup.budget
      ? ` of ${money(rollup.budget)}`
      : rollup.counts
        ? " so far"
        : " spent";
    add(
      "cost",
      <>
        <b className="font-semibold text-foreground">{money(rollup.cost)}</b>
        {tail}
        {rollup.burn ? ` · $${rollup.burn}/h` : ""}
      </>,
    );
  } else if (rollup.budget) {
    add("budget", `budget ${money(rollup.budget)}`);
  }
  if (rollup.eta) add("eta", `ETA ${rollup.eta}`);
  if (rollup.team) {
    const strip = <TeamStrip team={rollup.team} />;
    if (rollup.team.working.length || rollup.team.idle) add("team", strip);
  }
  if (rollup.counts) {
    add(
      "moving",
      rollup.moving_anywhere
        ? "work moving"
        : `nothing in the ${levelName(node.level, node.level_name).toLowerCase()} is moving`,
    );
  }
  if (rollup.in_base_env) {
    const chip =
      "rounded-full border border-status-green/70 bg-status-green/12 px-2 py-px text-xs text-status-green-fg";
    const env = (
      <>
        in base env <span className="font-mono">@{rollup.in_base_env.sha}</span>
      </>
    );
    add(
      "env",
      rollup.in_base_env.try_it && onSheet ? (
        <button
          type="button"
          data-testid="v3-rollup-try"
          onClick={() => onSheet("try", node.ref)}
          className={cn(chip, "hover:bg-status-green/20")}
        >
          {env} · Try it ↗
        </button>
      ) : (
        <span className={chip}>{env}</span>
      ),
    );
  }
  if (rollup.since?.items.length) {
    add(
      "since",
      <>
        <span className="text-status-blue-fg">since {sinceWhen(rollup.since.at)}:</span>{" "}
        {rollup.since.items.join(", ")}
        {rollup.since.more ? ` +${rollup.since.more}` : ""}
      </>,
      true,
    );
  }
  if (rollup.alarms_closed?.count) {
    add(
      "alarms",
      <NodeLink nodeRef={rollup.alarms_closed.ref} className="text-status-blue-fg hover:underline">
        {rollup.alarms_closed.count} alarm{rollup.alarms_closed.count > 1 ? "s" : ""} closed as
        noise
      </NodeLink>,
    );
  }
  if (!parts.length) return null;
  return (
    <div
      data-testid="v3-rollup"
      className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-muted-foreground"
    >
      {p && !isRoot && <Ring progress={p} size={28} />}
      {parts.map((part, i) => (
        // The separator ends its part, so a wrapped line never starts with "·".
        <span key={part.id} className="inline-flex max-w-full min-w-0 items-center gap-2">
          <span className={cn("min-w-0", part.shrink && "max-md:truncate")}>{part.node}</span>
          {i < parts.length - 1 && DOT}
        </span>
      ))}
    </div>
  );
}

/** "Undo (8 min left)": the last verb applied here, for its whole window. */
function UndoLatest({ nodeRef }: { nodeRef: string }) {
  const latest = useNodeUndo(nodeRef);
  const [busy, setBusy] = useState(false);
  if (!latest) return null;
  return (
    <button
      type="button"
      data-testid="v3-undo-latest"
      disabled={busy}
      onClick={() => {
        setBusy(true);
        void latest.undo.run().finally(() => setBusy(false));
      }}
      title={`Undo ${latest.undo.label}`}
      className="inline-flex shrink-0 items-center gap-1.5 rounded-[7px] border border-dashed border-border-strong px-2.5 py-0.5 text-[12.5px] whitespace-nowrap text-muted-foreground hover:border-status-blue hover:text-foreground disabled:opacity-50"
    >
      <Undo2Icon aria-hidden className="size-3" />
      {latest.undo.label}: {undoLabel(latest.left)}
    </button>
  );
}

function VerbBar({ verbs, nodeRef }: { verbs: VerbAvail[]; nodeRef: string }) {
  const { onVerb, openVerb } = useNodeActions();
  const undo = useNodeUndo(nodeRef);
  if (!verbs.length && !undo) return null;
  return (
    <div
      role="toolbar"
      aria-label="Steer this node"
      data-testid="v3-verbs"
      className="mt-0.5 flex gap-1.5 max-md:-mx-4 max-md:overflow-x-auto max-md:px-4 md:flex-wrap [&::-webkit-scrollbar]:hidden"
    >
      {verbs.map((v) => (
        <button
          key={v.verb}
          type="button"
          disabled={!v.enabled}
          title={v.enabled ? undefined : v.reason}
          aria-pressed={v.verb === "talk" ? undefined : openVerb === v.verb}
          onClick={() => onVerb?.(v)}
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-[7px] border border-border bg-card-solid px-2.5 py-0.5 text-[12.5px] whitespace-nowrap text-muted-foreground hover:border-border-strong hover:text-foreground disabled:opacity-50",
            openVerb === v.verb && "border-status-blue bg-status-blue/12 text-status-blue-fg",
          )}
        >
          {v.label}
          {v.key && (
            <span className="max-md:hidden">
              <Kbd>{v.key}</Kbd>
            </span>
          )}
        </button>
      ))}
      <UndoLatest nodeRef={nodeRef} />
    </div>
  );
}

export function NodeHeader({
  page,
  whyOpen: whyOpenProp,
  onWhyChange,
}: {
  page: NodePage;
  /** Controlled "why? ▾" (the page binds `w`); uncontrolled when absent. */
  whyOpen?: boolean;
  onWhyChange?: (open: boolean) => void;
}) {
  const { node, status } = page;
  const [ownWhy, setOwnWhy] = useState(false);
  const whyOpen = whyOpenProp ?? ownWhy;
  const setWhy = onWhyChange ?? setOwnWhy;
  const level = levelName(node.level, node.level_name);
  const glyph = page.breadcrumb.length > 0 && (!node.progress || node.implicit) && node.glyph;
  const kind = node.kind_name && node.kind_name !== level ? ` · ${node.kind_name}` : "";
  const start = page.verbs.find((v) => v.verb === "start") ?? null;
  return (
    <section data-testid="v3-header" className="mb-3 flex flex-col gap-[7px]">
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        {glyph && <Glyph glyph={glyph} dashed={node.kind === "study" || node.implicit} />}
        <h1 className="min-w-0 text-[21px] font-bold tracking-[-0.01em] [overflow-wrap:anywhere] max-md:text-[19px]">
          {node.title}
        </h1>
        <span className="flex flex-wrap items-center gap-2 text-[12.5px] text-foreground-tertiary">
          {node.tag && <span className="font-mono text-xs">{node.tag}</span>}
          <span>
            {level}
            {kind}
            {node.implicit ? " · implicit" : ""}
          </span>
          {node.owner && (
            <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-muted-foreground">
              <Avatar actor={node.owner} />
              {node.owner.name}
            </span>
          )}
        </span>
      </div>
      {node.why_html && (
        <p className="max-w-[80ch] text-muted-foreground">
          <RichText html={node.why_html} />
        </p>
      )}
      <StatusBox
        line={status}
        nodeRef={node.ref}
        whyOpen={whyOpen}
        onWhy={() => setWhy(!whyOpen)}
      />
      {whyOpen && <WhyCard line={status} startNow={start} />}
      <RollupLine page={page} />
      <ChipList chips={page.chips} more={page.more_chips} moreGroups={page.chips_more} />
      <VerbBar verbs={page.verbs} nodeRef={node.ref} />
    </section>
  );
}
