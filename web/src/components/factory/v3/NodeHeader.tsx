// A node's header: at most five lines (what it is, why it exists, its status,
// its roll-up, its links), then the verbs that steer it. A line with nothing
// to say is left out; nothing renders empty.

import { type ReactNode, useState } from "react";
import { ChipList } from "./LinkChip";
import { useNodeActions } from "./NodeContext";
import { Avatar, Glyph, Kbd, NodeLink, Ring, money } from "./nodeParts";
import type { NodePage, Rollup, VerbAvail } from "./nodeTypes";
import { RichText } from "./RichText";
import { StatusBox } from "./StatusLine";
import { WhyCard } from "./WhyCard";

const DOT = <span className="text-foreground-tertiary">·</span>;

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
        {n && team.idle ? " · " : ""}
        {team.idle ? `${team.idle} idle` : ""}
      </span>
    </span>
  );
}

/** Line 4: progress, cost, team, base env, what changed since the last look. */
export function RollupLine({ page }: { page: NodePage }) {
  const { node, rollup } = page;
  const parts: { id: string; node: ReactNode }[] = [];
  const add = (id: string, part: ReactNode) => {
    parts.push({ id, node: part });
  };
  const p = rollup.progress;
  if (rollup.counts) {
    const c = rollup.counts;
    const words: { id: string; node: ReactNode }[] = [];
    if (c.features) {
      words.push({
        id: "features",
        node: (
          <>
            <b className="font-semibold text-foreground">{c.features}</b> feature
            {c.features > 1 ? "s" : ""}
          </>
        ),
      });
    }
    if (c.studies) {
      words.push({ id: "studies", node: `${c.studies} stud${c.studies > 1 ? "ies" : "y"}` });
    }
    if (c.projects) {
      words.push({
        id: "projects",
        node: (
          <>
            <b className="font-semibold text-foreground">{c.projects}</b> project
            {c.projects > 1 ? "s" : ""}
            {c.later ? <span className="text-foreground-tertiary"> (+{c.later} later)</span> : null}
          </>
        ),
      });
    }
    if (words.length) {
      add(
        "counts",
        <>
          {words.map((w) => (
            <span key={w.id}>
              {w.id !== words[0]!.id && " · "}
              {w.node}
            </span>
          ))}
        </>,
      );
    }
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
        : `nothing in the ${node.level_name.toLowerCase()} is moving`,
    );
  }
  if (rollup.in_base_env) {
    add(
      "env",
      <span className="rounded-full border border-status-green/70 bg-status-green/12 px-2 py-px text-xs text-status-green-fg">
        in base env <span className="font-mono">@{rollup.in_base_env.sha}</span>
      </span>,
    );
  }
  if (rollup.since?.items.length) {
    add(
      "since",
      <>
        <span className="text-status-blue-fg">since {rollup.since.at}:</span>{" "}
        {rollup.since.items.join(", ")}
        {rollup.since.more ? ` +${rollup.since.more}` : ""}
      </>,
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
      {p && node.level !== "root" && <Ring progress={p} size={28} />}
      {parts.map((part) => (
        <span key={part.id} className="inline-flex max-w-full items-center gap-2">
          {part.id !== parts[0]!.id && DOT}
          <span className="min-w-0">{part.node}</span>
        </span>
      ))}
    </div>
  );
}

function VerbBar({ verbs }: { verbs: VerbAvail[] }) {
  const { onVerb } = useNodeActions();
  if (!verbs.length) return null;
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
          onClick={() => onVerb?.(v)}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-[7px] border border-border bg-card px-2.5 py-0.5 text-[12.5px] whitespace-nowrap text-muted-foreground hover:border-border-strong hover:text-foreground disabled:opacity-50"
        >
          {v.label}
          {v.key && (
            <span className="max-md:hidden">
              <Kbd>{v.key}</Kbd>
            </span>
          )}
        </button>
      ))}
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
  const glyph = node.level !== "root" && (!node.progress || node.implicit) && node.glyph;
  const kind = node.kind_name && node.kind_name !== node.level_name ? ` · ${node.kind_name}` : "";
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
            {node.level_name}
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
      <VerbBar verbs={page.verbs} />
    </section>
  );
}
