/**
 * Home (`/factory/<fid>`): what needs this person (at most three, the rest on
 * Decisions), then the features one line each (progress and a ▶ for their
 * latest demo), then what shipped lately with its demo. Nothing else by
 * default: the rest lives a click away (the outline, a feature's page).
 */

import { type ReactNode, useMemo, useState } from "react";
import { CheckIcon, PlayIcon, XIcon } from "lucide-react";
import { useFactoryDemos } from "@/components/factory/cockpit/demos";
import { plainPrTitle, useNodeNames } from "@/components/factory/cockpit/chats";
import { decisionsHref, demosHref } from "@/components/factory/cockpit/cockpitRoutes";
import { useFactoryNow } from "@/components/factory/factoryClock";
import { useFactoryDefinition } from "@/components/factory/insights/learnQueries";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { FOCUS_RING } from "@/components/factory/ui/statusTokens";
import { nodeName } from "@/components/factory/v3/calm/calmModel";
import { YourMove, useYourMove } from "@/components/factory/v3/calm/YourMove";
import { NodeContextProvider, type NodeActions } from "@/components/factory/v3/NodeContext";
import { Ring, StateDot, hasRing } from "@/components/factory/v3/nodeParts";
import { useLevel, useNode, usePrefetchNode } from "@/components/factory/v3/nodeQueries";
import { PRODUCT_REF, nodeHref } from "@/components/factory/v3/nodeRoutes";
import { useAnswerAsk } from "@/components/factory/v3/nodeSteer";
import type { DemoRef, NodePage, NodeSummary } from "@/components/factory/v3/nodeTypes";
import { ProofPlayer } from "@/components/factory/v3/proof/ProofRow";
import { DemoStatusTag } from "@/components/factory/board/DemoThumb";
import type { PrView } from "@/lib/factoryApi";
import { Link, useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";

const SHIPPED_ROWS = 5;

function Section({
  title,
  action,
  children,
  testId,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
  testId: string;
}) {
  return (
    <section aria-label={title} data-testid={testId} className="flex min-w-0 flex-col gap-2">
      <div className="flex items-baseline gap-2 px-0.5">
        <h2 className="text-[11px] tracking-[0.07em] text-foreground-tertiary uppercase">
          {title}
        </h2>
        <span className="flex-1" />
        {action}
      </div>
      {children}
    </section>
  );
}

function RowsSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-busy="true" className="overflow-hidden rounded-xl ring-1 ring-border">
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="flex items-center gap-3 border-t border-border px-3 py-3 first:border-t-0"
        >
          <div className="size-5 animate-pulse rounded-full bg-muted" />
          <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

/** "▶ 0:42" with the verdict's colour; opens the demo under its row. */
function PlayChip({
  demo,
  open,
  onClick,
}: {
  demo: Pick<DemoRef, "verdict" | "duration">;
  open: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={open}
      aria-label={open ? "Close the demo" : `Play the latest demo (${demo.verdict})`}
      data-testid="cockpit-home-play"
      className={cn(
        "inline-flex h-6 shrink-0 cursor-pointer items-center gap-1.5 rounded-full border border-border px-2 text-[12px] text-muted-foreground hover:border-border-strong hover:text-foreground",
        open && "border-foreground/30 text-foreground",
        FOCUS_RING,
      )}
    >
      {open ? (
        <XIcon aria-hidden className="size-3" />
      ) : (
        <PlayIcon aria-hidden className="size-2.5 fill-current" />
      )}
      <DemoStatusTag status={demo.verdict} className="max-sm:hidden" />
      {demo.duration && <span className="font-mono tabular-nums">{demo.duration}</span>}
    </button>
  );
}

function progressWords(node: NodeSummary): string | null {
  const p = node.progress;
  if (!p || p.what === "not planned") return null;
  return `${p.label} ${p.what === "acceptance" ? "checks" : p.what}`;
}

function FeatureRow({ fid, node, demo }: { fid: string; node: NodeSummary; demo?: DemoRef }) {
  const [open, setOpen] = useState(false);
  const prefetch = usePrefetchNode(fid);
  const needs = node.needs?.open ?? 0;
  const progress = progressWords(node);
  return (
    <li
      data-testid="cockpit-home-feature"
      data-ref={node.ref}
      className="border-t border-border first:border-t-0"
    >
      <div className="flex min-w-0 items-center gap-3 px-3 py-2.5">
        <span className="grid size-6 shrink-0 place-items-center">
          {hasRing(node.progress) ? (
            <Ring progress={node.progress} size={22} bare />
          ) : (
            <StateDot state={node.state} />
          )}
        </span>
        <Link
          to={nodeHref(fid, node.ref)}
          componentId="factory.cockpit.home.feature"
          onMouseEnter={() => prefetch(node.ref)}
          onFocus={() => prefetch(node.ref)}
          title={node.full_title ?? node.title}
          className={cn(
            "min-w-0 flex-1 truncate text-[14px] font-medium hover:text-status-blue-fg",
            FOCUS_RING,
          )}
        >
          {nodeName(node)}
        </Link>
        {needs > 0 && (
          <span className="shrink-0 text-[12.5px] font-semibold text-status-yellow-fg">
            {needs} need{needs === 1 ? "s" : ""} you
          </span>
        )}
        {progress && (
          <span className="shrink-0 text-[12.5px] text-muted-foreground tabular-nums max-sm:hidden">
            {progress}
          </span>
        )}
        {node.demo && demo && (
          <PlayChip demo={node.demo} open={open} onClick={() => setOpen((o) => !o)} />
        )}
      </div>
      {open && demo && (
        <div className="px-3 pb-3">
          <ProofPlayer demo={demo} />
        </div>
      )}
    </li>
  );
}

function ShippedRow({
  fid,
  pr,
  demo,
  names,
}: {
  fid: string;
  pr: PrView;
  demo?: DemoRef;
  names: ReadonlyMap<string, string>;
}) {
  const [open, setOpen] = useState(false);
  const now = useFactoryNow();
  const at = pr.merged?.at;
  const when = at ? agoWords(now.getTime() / 1000 - at) : null;
  const where = demo?.project?.title ?? (pr.project_slug ? names.get(pr.project_slug) : null);
  const title = demo?.title ?? plainPrTitle(pr.title);
  return (
    <li data-testid="cockpit-home-shipped" className="border-t border-border first:border-t-0">
      <div className="flex min-w-0 items-center gap-3 px-3 py-2.5">
        <CheckIcon aria-hidden className="size-4 shrink-0 text-status-green-fg" />
        <Link
          to={nodeHref(fid, `PR-${pr.number}`)}
          componentId="factory.cockpit.home.shipped"
          title={pr.title}
          className={cn(
            "min-w-0 flex-1 truncate text-[13.5px] hover:text-status-blue-fg",
            FOCUS_RING,
          )}
        >
          {title}
          {where && where.toLowerCase() !== title.toLowerCase() && (
            <span className="text-muted-foreground"> · {where}</span>
          )}
        </Link>
        {when && (
          <span className="shrink-0 text-[12.5px] text-muted-foreground max-sm:hidden">{when}</span>
        )}
        {demo && <PlayChip demo={demo} open={open} onClick={() => setOpen((o) => !o)} />}
      </div>
      {open && demo && (
        <div className="px-3 pb-3">
          <ProofPlayer demo={demo} />
        </div>
      )}
    </li>
  );
}

function agoWords(seconds: number): string {
  const min = Math.max(0, Math.round(seconds / 60));
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

function NeedsYou({ fid, page }: { fid: string; page: NodePage }) {
  const navigate = useNavigate();
  const answer = useAnswerAsk(PRODUCT_REF, { factoryId: fid });
  const { view } = useYourMove(page, page.home ?? null, fid);
  const actions = useMemo<NodeActions>(
    () => ({
      factoryId: fid,
      onAnswer: answer,
      onOpenDecision: () => navigate(decisionsHref(fid)),
      onSheet: (sheet) => {
        if (sheet === "needs") navigate(decisionsHref(fid));
      },
    }),
    [answer, fid, navigate],
  );
  return (
    <NodeContextProvider value={actions}>
      <YourMove view={{ ...view, waves: [] }} heading={false} />
    </NodeContextProvider>
  );
}

export function CockpitHome() {
  const { factoryId: fid, snapshot } = useFactoryShell();
  const product = useNode(PRODUCT_REF, fid);
  const features = useLevel(PRODUCT_REF, "feature", fid);
  const demos = useFactoryDemos(fid);
  const names = useNodeNames(fid);
  const productName = useFactoryDefinition(fid).data?.effective.product?.name ?? null;
  const byId = useMemo(
    () => new Map((demos.data?.items ?? []).map((d) => [d.id, d])),
    [demos.data],
  );
  const byPr = useMemo(() => {
    const out = new Map<number, DemoRef>();
    // Newest first: the first one seen per PR is its latest.
    for (const d of demos.data?.items ?? []) {
      if (d.pr_number && !out.has(d.pr_number)) out.set(d.pr_number, d);
    }
    return out;
  }, [demos.data]);
  const rows = useMemo(() => {
    const all = (features.data?.groups ?? []).flatMap((g) => g.items);
    return [...all.filter((n) => n.state !== "done"), ...all.filter((n) => n.state === "done")];
  }, [features.data]);
  const shipped = useMemo(
    () =>
      snapshot.prs
        .filter((p) => p.state === "merged" && p.merged?.at)
        .sort((a, b) => (b.merged?.at ?? 0) - (a.merged?.at ?? 0))
        .slice(0, SHIPPED_ROWS),
    [snapshot.prs],
  );
  const newest = demos.data?.items[0];
  return (
    <div data-testid="cockpit-home" className="mx-auto flex max-w-[56rem] min-w-0 flex-col gap-7">
      <h1 className="truncate text-xl font-semibold tracking-tight">
        {productName ?? snapshot.factory.name}
      </h1>
      <Section title="Needs you" testId="cockpit-home-needs">
        {product.isPending ? (
          <RowsSkeleton rows={2} />
        ) : product.data ? (
          <NeedsYou fid={fid} page={product.data} />
        ) : null}
      </Section>
      <Section
        title="Features"
        testId="cockpit-home-features"
        action={
          newest && (
            <Link
              to={demosHref(fid)}
              componentId="factory.cockpit.home.demos"
              className="text-[12.5px] text-status-blue-fg hover:underline"
            >
              All demos ›
            </Link>
          )
        }
      >
        {features.isPending ? (
          <RowsSkeleton />
        ) : rows.length ? (
          <ul className="overflow-hidden rounded-xl bg-card-solid ring-1 ring-border">
            {rows.map((n) => (
              <FeatureRow
                key={n.ref}
                fid={fid}
                node={n}
                demo={n.demo ? byId.get(n.demo.id) : undefined}
              />
            ))}
          </ul>
        ) : (
          <p className="px-0.5 text-sm text-muted-foreground">
            No features yet. Ask for one in Chats, or add a project from the factory menu.
          </p>
        )}
      </Section>
      {shipped.length > 0 && (
        <Section title="Recently shipped" testId="cockpit-home-shipped-list">
          <ul className="overflow-hidden rounded-xl bg-card-solid ring-1 ring-border">
            {shipped.map((pr) => (
              <ShippedRow key={pr.id} fid={fid} pr={pr} demo={byPr.get(pr.number)} names={names} />
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
