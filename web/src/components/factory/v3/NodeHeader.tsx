// A node's header, calm: its short name (with its ring or state dot), at most
// one phrase when it is held by something other than the viewer, the ⋯ verbs
// menu and one "Details" disclosure. Everything else (the full title, ids,
// why it exists, the status path and why chain, the roll-up, links, acceptance
// and evidence) waits behind Details; nothing renders empty.

import { type ReactNode, useState } from "react";
import { ChevronDownIcon, Undo2Icon } from "lucide-react";
import { cn } from "@/lib/utils";
import { undoLabel } from "../steer/steerModel";
import { childPhrase, fullTitle, nodeName } from "./calm/calmModel";
import { VerbMenu } from "./calm/NodeMenu";
import { LineageFold } from "./lenses/LensBodies";
import { inProductTree } from "./lenses/lensModel";
import { useLensSupport } from "./lenses/lensQueries";
import { ChipList } from "./LinkChip";
import { useNodeActions } from "./NodeContext";
import { NodeContent } from "./NodeContent";
import { Avatar, Glyph, NodeLink, Ring, StateDot, hasRing, money, sinceWhen } from "./nodeParts";
import { useNodeUndo } from "./nodeSteer";
import type { NodePage, ReviewGap, Rollup, RollupCount } from "./nodeTypes";
import { levelName } from "./nodeWire";
import { RichText } from "./RichText";
import { StatusBox } from "./StatusLine";
import { endsAbove } from "./statusModel";
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
      {hasRing(p) && !isRoot && <Ring progress={p} size={28} />}
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

const GAP_WORDS: Record<string, string> = {
  started: "started",
  asked: "asks a person",
  waiting: "waits for approval",
  next_wave: "next wave",
  to_lead: "with the lead",
  finished: "finished",
  noted: "noted",
  dropped: "dropped",
};

/** One gap of the done review: its line, and where its follow-up is. */
function gapWords(gap: ReviewGap): string {
  const where = GAP_WORDS[gap.status] ?? gap.status;
  return gap.follow && gap.live ? `${where}: ${gap.follow}` : where;
}

/** The done review's gaps, one line each (Details: the header shows only its line). */
function ReviewGaps({ page }: { page: NodePage }) {
  const gaps = page.review?.gaps ?? [];
  if (!gaps.length) return null;
  return (
    <ol data-testid="v3-review-gaps" aria-label="Done review" className="flex flex-col gap-1">
      {gaps.map((gap) => (
        <li key={gap.n} className="flex flex-wrap gap-x-2 text-muted-foreground">
          <span className="text-foreground">{gap.line}</span>
          <span className="text-foreground-tertiary">· {gapWords(gap)}</span>
        </li>
      ))}
    </ol>
  );
}

/** What the node is, in words: its full title when the name shortens it, its code, level, kind and owner. */
function MetaLine({ page }: { page: NodePage }) {
  const { node } = page;
  const level = levelName(node.level, node.level_name);
  const kind = node.kind_name && node.kind_name !== level ? node.kind_name : null;
  const full = fullTitle(node);
  return (
    <div className="flex flex-col gap-1">
      {full && (
        <p data-testid="v3-full-title" className="text-foreground [overflow-wrap:anywhere]">
          {full}
        </p>
      )}
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-foreground-tertiary">
        {node.tag && <span className="font-mono text-xs">{node.tag}</span>}
        <span>
          {level}
          {kind ? ` · ${kind}` : ""}
          {node.implicit ? " · implicit" : ""}
        </span>
        {node.owner && (
          <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-muted-foreground">
            <Avatar actor={node.owner} />
            {node.owner.name}
          </span>
        )}
      </p>
    </div>
  );
}

/** Everything about the node that is not the viewer's move or its children, until asked. */
export function NodeDetails({
  page,
  whyOpen,
  onWhy,
}: {
  page: NodePage;
  whyOpen: boolean;
  onWhy: () => void;
}) {
  const { node, status } = page;
  const { aboveId } = useNodeActions();
  const start = page.verbs.find((v) => v.verb === "start") ?? null;
  const kids = page.children;
  const lineage =
    useLensSupport().lineage &&
    inProductTree(page) &&
    !kids.items.length &&
    !kids.later.length &&
    !kids.ghosts.length;
  return (
    <section
      id="v3-details"
      data-testid="v3-details"
      aria-label="Details"
      className="mt-1 mb-1 flex flex-col gap-2.5 rounded-xl bg-muted/30 px-3.5 py-3 text-[13px] ring-1 ring-border"
    >
      <MetaLine page={page} />
      {node.why_html && (
        <p className="max-w-[80ch] text-muted-foreground">
          <RichText html={node.why_html} />
        </p>
      )}
      <StatusBox
        line={status}
        nodeRef={node.ref}
        whyOpen={whyOpen}
        onWhy={onWhy}
        answer={!endsAbove(status, aboveId)}
      />
      {whyOpen && <WhyCard line={status} startNow={start} />}
      <RollupLine page={page} />
      <ChipList chips={page.chips} more={page.more_chips} moreGroups={page.chips_more} />
      <ReviewGaps page={page} />
      <NodeContent page={page} part="details" />
      {/* A node with nothing below has no lens menu: its lineage opens from here. */}
      {lineage && <LineageFold page={page} />}
    </section>
  );
}

/** The ring a node's progress draws, else its state dot (the root has neither). */
function Mark({ page }: { page: NodePage }) {
  const { node } = page;
  if (!page.breadcrumb.length) return null;
  if (hasRing(node.progress) && !node.implicit) return <Ring progress={node.progress} size={30} />;
  if (node.glyph && (node.implicit || node.kind === "study")) {
    return <Glyph glyph={node.glyph} dashed />;
  }
  return <StateDot state={node.state} className="size-2.5" />;
}

export function NodeHeader({
  page,
  whyOpen: whyOpenProp,
  onWhyChange,
  detailsOpen: detailsProp,
  onDetailsChange,
}: {
  page: NodePage;
  /** Controlled "why? ▾" (the page binds `w`); uncontrolled when absent. */
  whyOpen?: boolean;
  onWhyChange?: (open: boolean) => void;
  /** Controlled Details (`w` opens it with the why); uncontrolled when absent. */
  detailsOpen?: boolean;
  onDetailsChange?: (open: boolean) => void;
}) {
  const { node } = page;
  const [ownWhy, setOwnWhy] = useState(false);
  const [ownDetails, setOwnDetails] = useState(false);
  const whyOpen = whyOpenProp ?? ownWhy;
  const setWhy = onWhyChange ?? setOwnWhy;
  const details = detailsProp ?? ownDetails;
  const setDetails = onDetailsChange ?? setOwnDetails;
  const { aboveId } = useNodeActions();
  const name = nodeName(node);
  // The viewer's own asks are Your move; a phrase says only what holds it otherwise.
  const covered = page.status.ends === "person" && endsAbove(page.status, aboveId);
  // A feature's done review says where it is in one line ("Review: not done · …").
  const review = page.review?.line ?? null;
  const phrase = page.breadcrumb.length && !covered && !review ? childPhrase(node) : null;
  return (
    <section data-testid="v3-header" className="mb-4 flex flex-col gap-1.5">
      <div className="flex min-w-0 items-center gap-2.5">
        <Mark page={page} />
        <h1
          data-testid="v3-title"
          title={fullTitle(node) ?? undefined}
          className="min-w-0 text-[21px] font-bold tracking-[-0.01em] [overflow-wrap:anywhere] max-md:text-[19px]"
        >
          {name}
        </h1>
        <span className="flex-1" />
        <UndoLatest nodeRef={node.ref} />
        <button
          type="button"
          data-testid="v3-details-toggle"
          aria-expanded={details}
          aria-controls="v3-details"
          onClick={() => setDetails(!details)}
          className="inline-flex shrink-0 items-center gap-1 rounded-[7px] border border-transparent px-1.5 py-0.5 text-[12.5px] text-muted-foreground hover:border-border hover:text-foreground aria-expanded:border-border aria-expanded:text-foreground"
        >
          Details
          <ChevronDownIcon
            aria-hidden
            className={cn("size-3 transition-transform", details && "rotate-180")}
          />
        </button>
        <VerbMenu verbs={page.verbs} />
      </div>
      {phrase && phrase.tone !== "you" && (
        <p data-testid="v3-phrase-here" className="text-[13px] text-muted-foreground">
          {phrase.text}
        </p>
      )}
      {review && (
        <p data-testid="v3-review" className="text-[13px] text-muted-foreground">
          {review}
        </p>
      )}
      {details && <NodeDetails page={page} whyOpen={whyOpen} onWhy={() => setWhy(!whyOpen)} />}
    </section>
  );
}
